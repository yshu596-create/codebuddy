#!/usr/bin/env python3
"""Step 1b · 智能顾问 advisor 全量风险拉取(2026-05-20 advisor 进 inspector,替代 cloudq 7 类能力)。

用法:
    python3 scripts/inspect_advisor.py

输入:
    环境变量 TENCENTCLOUD_SECRET_ID / _KEY / INSPECTION_UIN

输出:
    $OUT_DIR/inspect_advisor.json           ← 主报告输入
    $OUT_DIR/inspect_advisor_strategies.json ← strategy 元数据缓存(LLM 反查)
    stdout 一行 summary JSON

为什么独立脚本:
    - 与 inspect_scan.py 同位阶,Step 1 拆 1a/1b 并行 exec,各自 timeout 300
    - advisor 是补充信号源,**不是依赖**(失败 scan_status=denied,LLM 跳过 advisor 段,
      本地硬扫数据完整即报告可用 → 见 references/14-advisor-coverage.md §0)
    - cloudq skill 替代:advisor 全 450 strategy 覆盖 cloudq 的 7 类能力(巡检/安全合规/
      处置方案/最佳实践/AI 智能巡检/主动预警/架构健康度),路由表见 references/14-advisor-coverage.md §1

工程契约(对齐 memory 4 条 + plan A 节):
    1. r['Risks'] 是 JSON-string,必须 json.loads 二次解码 — 否则 len() 看到字符数不是条数
    2. call_api 已剥 Response 包,直接读平级字段(不要再 .get('Response',{}))
    3. strategies.json 单文件无 uin 分目录(三账户元数据零差异 — memory 实测)
    4. 不做 caller_identity 三态(advisor 三账户子账号全通)
    5. 严重度判定**不写脚本**,LLM 在 Step 4 按 references/14-advisor-coverage.md §2 判定表
    6. 不按 GroupName 过滤,全 450 strategy 都拉一遍(GroupName 是产品维度,不是风险维度)

性能预算:
    - DescribeStrategies 一次 1-2s
    - 450 strategy × DescribeTaskStrategyRisks(单个 ~0.5-1s) / 15 路并发 ≈ 20-35s
    - API 限频: DescribeTaskStrategyRisks 20 QPS,15 路并发峰值 ~15 QPS,留 5 QPS 余量
"""
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
from sign import call_api  # noqa: E402

ADV_HOST = 'advisor.tencentcloudapi.com'
ADV_VER = '2020-07-21'
ADV_REGION = 'ap-guangzhou'

MAX_WORKERS = 15
RETRY = 3
RETRY_SLEEP = 0.5


def resolve_out_dir():
    d = os.environ.get('INSPECT_OUT_DIR', '').strip()
    if d:
        return d
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    if uin:
        return f'/tmp/inspect_{uin}'
    sys.stderr.write('[FATAL] INSPECTION_UIN 或 INSPECT_OUT_DIR 必须至少设置其一\n')
    sys.exit(2)


def fetch_strategies(sid, sk):
    """拉全量 strategy 元数据。返回 (strategies_list, error_or_None)。

    一次性接口,腾讯云不强制分页(实测 450 一次返完)。retry=3 防网络抖动。
    """
    payload = {}
    last_err = None
    for _ in range(RETRY):
        try:
            r = call_api(host=ADV_HOST, service='advisor',
                         action='DescribeStrategies', version=ADV_VER,
                         payload=payload, region=ADV_REGION,
                         secret_id=sid, secret_key=sk)
            if isinstance(r, dict) and not r.get('Error'):
                strategies = r.get('Strategies', []) or []
                return strategies, None
            last_err = (r.get('Error', {}) if isinstance(r, dict) else {'Message': str(r)})
        except Exception as e:
            last_err = {'Message': str(e)[:200]}
        time.sleep(RETRY_SLEEP)
    return [], last_err


def fetch_risks_one(sid, sk, strategy_id):
    """单 strategy 的 Risks 拉取。

    返回 (strategy_id, payload_dict 或 None, error_or_None)。
    payload_dict = {risks: [...], resource_count: int, risk_total_count: int}
    关键:r['Risks'] 是 JSON-string,必须二次 json.loads。
    ResourceCount/RiskTotalCount 是 DescribeTaskStrategyRisks 顶层字段,strategy 元数据里没有。
    """
    payload = {'StrategyId': strategy_id}
    last_err = None
    for _ in range(RETRY):
        try:
            r = call_api(host=ADV_HOST, service='advisor',
                         action='DescribeTaskStrategyRisks', version=ADV_VER,
                         payload=payload, region=ADV_REGION,
                         secret_id=sid, secret_key=sk)
            if isinstance(r, dict) and not r.get('Error'):
                risks_raw = r.get('Risks', '[]')
                # 关键:Risks 是 JSON-string,二次解码
                if isinstance(risks_raw, str):
                    try:
                        risks = json.loads(risks_raw)
                    except (ValueError, TypeError):
                        risks = []
                elif isinstance(risks_raw, list):
                    risks = risks_raw
                else:
                    risks = []
                return strategy_id, {
                    'risks': risks,
                    'resource_count': r.get('ResourceCount', 0) or 0,
                    'risk_total_count': r.get('RiskTotalCount', 0) or 0,
                }, None
            last_err = (r.get('Error', {}) if isinstance(r, dict) else {'Message': str(r)})
        except Exception as e:
            last_err = {'Message': str(e)[:200]}
        time.sleep(RETRY_SLEEP)
    return strategy_id, None, last_err


def _to_int(v, default=0):
    """advisor Risks 里的 Level 字段是 string '3' 不是 int 3,需要统一转 int。"""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except (ValueError, TypeError):
            return default
    return default


def _extract_repair_url(repair_md):
    """从 strategy.Repair markdown 文本中抽取第一个腾讯云文档链接作为 repair_url。

    strategy 元数据没有独立的 RepairUrl 字段,URL 嵌在 Repair markdown 里,形如:
        建议依据最小化权限原则，根据不同的业务需求[创建不同的账号](https://cloud.tencent.com/document/product/236/35794)
    抽第一个 https://cloud.tencent.com/... 的链接,这是 advisor 的主修复指引。
    """
    if not repair_md or not isinstance(repair_md, str):
        return ''
    # 优先抓 [文本](url) 格式
    m = re.search(r'\]\((https://[^\s)]+)\)', repair_md)
    if m:
        return m.group(1)
    # 兜底抓裸 URL
    m = re.search(r'https://cloud\.tencent\.com/[^\s\)]+', repair_md)
    return m.group(0) if m else ''


def normalize_risk(rr):
    """单条 Risk 的字段统一形态。

    输入:advisor Risks 元素(JSON-string 解码后)
    输出:固定 schema dict — 报告侧基于此读
    注:advisor 返回 Level 是 string '3',这里统一转 int(后续聚合 level_max 用)
    """
    if not isinstance(rr, dict):
        return None
    return {
        'instance_id': rr.get('InstanceId', ''),
        'instance_name': rr.get('InstanceName', ''),
        'region': rr.get('Region', ''),
        'zone': rr.get('Zone', ''),
        'level': _to_int(rr.get('Level'), 0),  # 1 低 / 2 中 / 3 高,string → int
        'risk_days': _to_int(rr.get('RiskDays'), 0),
        'tags': rr.get('Tags', []) or [],  # [{Key,Value}, ...] 原样
        'vip': rr.get('Vip', ''),
        'vport': rr.get('Vport', ''),
        'status': rr.get('Status', ''),
        'ignore_type': rr.get('IgnoreType', ''),
        'pri_id': rr.get('PriId', ''),
        'condition_id': rr.get('conditionID', ''),
    }


def assemble_finding(strategy, risks_payload):
    """合并 strategy 元数据 + 该 strategy 的 risks 为一个 finding 条目。

    risks_payload = {risks: [...], resource_count: int, risk_total_count: int}
    或 None(拉取失败)→ 空 finding。

    strategy 字段映射(实测 schema):
      StrategyId / Name(name)/ Product(key)/ ProductDesc(中文)/ GroupName / Repair(markdown)
      Conditions:[{ConditionId, Level, LevelDesc, Desc}]
    没有顶层 Type 字段,Conditions[].LevelDesc 接近"类型"语义,这里用 GroupName 即够。
    没有顶层 RepairUrl 字段,从 Repair markdown 抽第一个 [文档](url) 的链接。
    """
    if risks_payload is None:
        risks_list = []
        resource_total = 0
        risk_total_count = 0
    else:
        risks_list = risks_payload.get('risks', [])
        resource_total = risks_payload.get('resource_count', 0)
        risk_total_count = risks_payload.get('risk_total_count', 0)
    risks_norm = [normalize_risk(rr) for rr in risks_list if isinstance(rr, dict)]
    risks_norm = [r for r in risks_norm if r is not None]
    levels = [r['level'] for r in risks_norm if r['level'] > 0]
    level_max = max(levels) if levels else 0
    # 优先用 advisor 返的 RiskTotalCount,fallback 用 risks 数组长度
    risk_total = risk_total_count if risk_total_count else len(risks_norm)
    return {
        'strategy_id': strategy.get('StrategyId', ''),
        'strategy_name': strategy.get('Name', ''),
        'product': strategy.get('ProductDesc', '') or strategy.get('Product', ''),
        'group': strategy.get('GroupName', ''),
        'type': '',  # advisor 元数据无独立 Type 字段,LLM 用 GroupName 路由
        'level_max': level_max,
        'risk_total': risk_total,
        'resource_total': resource_total,
        'repair_url': _extract_repair_url(strategy.get('Repair', '')),
        'repair': strategy.get('Repair', '') or '',
        'risks': risks_norm,
    }


def build_by_group(findings):
    """按 GroupName 聚合扫描覆盖统计。"""
    by_group = defaultdict(lambda: {'checked': 0, 'hit': 0, 'risks_total': 0})
    for f in findings:
        g = f.get('group', '?') or '?'
        by_group[g]['checked'] += 1
        if f.get('risk_total', 0) > 0:
            by_group[g]['hit'] += 1
        by_group[g]['risks_total'] += f.get('risk_total', 0)
    return {g: dict(v) for g, v in by_group.items()}


def main():
    sid = os.environ.get('TENCENTCLOUD_SECRET_ID', '').strip()
    sk = os.environ.get('TENCENTCLOUD_SECRET_KEY', '').strip()
    if not sid or not sk:
        sys.stderr.write('[FATAL] TENCENTCLOUD_SECRET_ID / _KEY 未设置\n')
        sys.exit(2)
    out_dir = resolve_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    out = {
        'scan_status': 'ok',  # ok / partial / denied
        'total_strategies': 0,
        'total_risks': 0,
        'total_resources': 0,
        'by_group': {},
        'findings': [],
        'errors': [],
        'level3_count': 0,
        'repair_url_coverage': 0.0,
    }

    t0 = time.time()

    # 1. 拉 strategy 元数据
    strategies, strat_err = fetch_strategies(sid, sk)
    if strat_err:
        # strategy 拉取失败 → 整体 denied(没法发起后续 risks 拉取)
        out['scan_status'] = 'denied'
        out['errors'].append({'kind': 'fetch_strategies', 'error': strat_err})
        # 落盘 + summary 后退出
        json.dump(out, open(os.path.join(out_dir, 'inspect_advisor.json'), 'w'),
                  ensure_ascii=False, indent=2, default=str)
        json.dump([], open(os.path.join(out_dir, 'inspect_advisor_strategies.json'), 'w'),
                  ensure_ascii=False, indent=2, default=str)
        elapsed = time.time() - t0
        print(json.dumps({
            'scan_status': 'denied',
            'total_strategies': 0,
            'total_risks': 0,
            'total_resources': 0,
            'by_group': {},
            'level_max': 0,
            'level3_count': 0,
            'repair_url_coverage': 0.0,
            'elapsed_s': round(elapsed, 1),
            'out_dir': out_dir,
            'error': strat_err,
        }, ensure_ascii=False))
        return

    # 落盘 strategies 元数据缓存(LLM 在 Step 4 反查 strategy_name/group/type/repair_url 用)
    json.dump(strategies, open(os.path.join(out_dir, 'inspect_advisor_strategies.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)

    out['total_strategies'] = len(strategies)
    strategy_index = {s.get('StrategyId', ''): s for s in strategies}

    # 2. 8 路并发拉每个 strategy 的 Risks
    findings_by_id = {}
    failed_strategies = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_risks_one, sid, sk, sid_): sid_
                for sid_ in [s.get('StrategyId', '') for s in strategies if s.get('StrategyId')]}
        for f in as_completed(futs):
            sid_, risks_payload, err = f.result()
            if risks_payload is None:
                failed_strategies[sid_] = err
                # 失败的 strategy 仍然组装一个空 finding,LLM 端按 errors 决定是否展示
                strat = strategy_index.get(sid_, {'StrategyId': sid_})
                findings_by_id[sid_] = assemble_finding(strat, None)
            else:
                strat = strategy_index.get(sid_, {'StrategyId': sid_})
                findings_by_id[sid_] = assemble_finding(strat, risks_payload)

    # 3. scan_status 三态判定
    if failed_strategies:
        if len(failed_strategies) == out['total_strategies']:
            out['scan_status'] = 'denied'
        else:
            out['scan_status'] = 'partial'
        for sid_, err in failed_strategies.items():
            out['errors'].append({
                'kind': 'fetch_risks',
                'strategy_id': sid_,
                'strategy_name': strategy_index.get(sid_, {}).get('StrategyName', ''),
                'error': err,
            })

    # 4. 组装 findings 列表(按 strategy_id 排序保持稳定)
    findings = [findings_by_id[k] for k in sorted(findings_by_id.keys())]
    out['findings'] = findings

    # 5. 统计
    total_risks = sum(f['risk_total'] for f in findings)
    total_resources = sum(f['resource_total'] for f in findings)
    level3_count = sum(1 for f in findings if f['level_max'] >= 3)
    findings_with_url = sum(1 for f in findings if f.get('repair_url'))
    findings_with_hits = [f for f in findings if f['risk_total'] > 0]
    # repair_url 覆盖率:在"有命中的 finding"里有几个带 url(无命中的 finding 不算分母)
    if findings_with_hits:
        url_cov = sum(1 for f in findings_with_hits if f.get('repair_url')) / len(findings_with_hits)
    else:
        # 全无命中 → 看全 strategy 元数据带 url 的覆盖率,作为信号
        url_cov = (findings_with_url / len(findings)) if findings else 0.0

    out['total_risks'] = total_risks
    out['total_resources'] = total_resources
    out['level3_count'] = level3_count
    out['repair_url_coverage'] = round(url_cov, 3)
    out['by_group'] = build_by_group(findings)

    # 6. 落盘 — 全量 advisor.json(报告级数据,文件大,LLM 不直接读)
    json.dump(out, open(os.path.join(out_dir, 'inspect_advisor.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)

    # 6b. 落盘 — digest.json(LLM 报告组装阶段唯一应读的 advisor 入口)
    # 设计目标:取代 LLM 在 Step 4 反复 import json 翻 690KB 全量数据的行为。
    # 只保留 hit 的 finding,且不含 risks 数组本身(只留聚合计数),全量 ≈ 5-10KB。
    # LLM 若需要某 finding 的 risks 详情,再按 strategy_id 反查全量 advisor.json。
    digest_findings = []
    for f in findings:
        if f.get('risk_total', 0) <= 0:
            continue  # 只保留命中的 strategy
        # 抽取代表性资源 ID(前 5 个),避免 LLM 看不到任何实例就乱编
        sample_ids = [r.get('instance_id', '') for r in f.get('risks', [])[:5]
                      if r.get('instance_id')]
        digest_findings.append({
            'strategy_id': f.get('strategy_id', ''),
            'strategy_name': f.get('strategy_name', ''),
            'product': f.get('product', ''),
            'group': f.get('group', ''),
            'level_max': f.get('level_max', 0),
            'risk_total': f.get('risk_total', 0),
            'resource_total': f.get('resource_total', 0),
            'repair_url': f.get('repair_url', ''),
            'sample_instance_ids': sample_ids,
        })
    # 按严重度 + 命中数排序,LLM 从头读即可优先看到高危
    digest_findings.sort(key=lambda x: (-x['level_max'], -x['risk_total']))

    digest = {
        'scan_status': out['scan_status'],
        'total_strategies': out['total_strategies'],
        'total_strategies_hit': len(digest_findings),
        'total_risks': total_risks,
        'total_resources': total_resources,
        'level_max': max((f['level_max'] for f in findings), default=0),
        'level3_count': level3_count,
        'by_group': out['by_group'],
        'failed_strategies': len(failed_strategies),
        'repair_url_coverage': out['repair_url_coverage'],
        'findings': digest_findings,  # 已按 level_max DESC, risk_total DESC 排序
        'note': '此文件为 advisor 摘要,Step 4 报告组装应只读此文件;'
                '需 risks 实例明细时按 strategy_id 反查 inspect_advisor.json。',
    }
    json.dump(digest, open(os.path.join(out_dir, 'inspect_advisor_digest.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)

    elapsed = time.time() - t0

    # 7. stdout summary
    level_max = max((f['level_max'] for f in findings), default=0)
    print(json.dumps({
        'scan_status': out['scan_status'],
        'total_strategies': out['total_strategies'],
        'total_risks': total_risks,
        'total_resources': total_resources,
        'by_group': out['by_group'],
        'level_max': level_max,
        'level3_count': level3_count,
        'repair_url_coverage': out['repair_url_coverage'],
        'failed_strategies': len(failed_strategies),
        'elapsed_s': round(elapsed, 1),
        'out_dir': out_dir,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
