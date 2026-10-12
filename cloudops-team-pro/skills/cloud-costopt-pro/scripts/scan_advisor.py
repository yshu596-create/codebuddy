#!/usr/bin/env python3
"""costopt Step 1.5 · advisor 成本组扫描(2026-05-21,吸收 cloudq 成本能力)。

用法:
    python3 scripts/scan_advisor.py

输入:
    TENCENTCLOUD_SECRET_ID / _KEY / INSPECTION_UIN 环境变量
    可选 COSTOPT_OUT_DIR(默认 /tmp/costopt_{uin})

输出:
    $OUT_DIR/advisor_cost.json   ← Step 2 / Step 3 输入
    stdout 一行 summary JSON

为什么独立脚本(不复用 inspector inspect_advisor.py):
    - inspector 关心 4 组(安全/可靠/限制/性能),costopt 关心 1 组(成本)
    - 输出 schema 不同:inspector 按 strategy 聚合 findings;
      costopt 按 instance_id 索引 hits(Step 2 跳过名单 + Step 3 去重需要)
    - 分两个独立 scanner 避免误用 — plan 明确条款

工程契约:
    1. r['Risks'] 是 JSON-string,必须 json.loads 二次解码
    2. call_api 已剥 Response 包,直接读平级字段
    3. advisor 元数据**禁止落盘**,每次现拉(SKILL.md HARD,合规 cache/ 禁用规则)
    4. Level 是 string,统一转 int 后做严重度判定
    5. 严重度判定**不写脚本**,LLM 在 Step 3 按 docs/15-advisor-cost-coverage.md 判定表
    6. 只过滤 GroupName="成本"(22 个 strategy),其他组的命中由 inspector 处理

性能预算:
    - DescribeStrategies 一次 1-2s
    - 22 strategy × DescribeTaskStrategyRisks(单个 ~0.5-1s) / 8 路并发 ≈ 10-15s
    - 与 inspect_advisor.py 同量级,因 strategy 数从 450 降到 22,预期更快
"""
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
from sign import call_api  # noqa: E402

ADV_HOST = 'advisor.tencentcloudapi.com'
ADV_VER = '2020-07-21'
ADV_REGION = 'ap-guangzhou'

COST_GROUP_NAME = '成本'  # 只过滤成本组,其他组 inspector 已收

MAX_WORKERS = 15
RETRY = 3
RETRY_SLEEP = 0.5


def resolve_out_dir():
    d = os.environ.get('COSTOPT_OUT_DIR', '').strip()
    if d:
        return d
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    if uin:
        return f'/tmp/costopt_{uin}'
    sys.stderr.write('[FATAL] INSPECTION_UIN 或 COSTOPT_OUT_DIR 必须至少设置其一\n')
    sys.exit(2)


def fetch_strategies(sid, sk):
    """拉全量 strategy 元数据,内存过滤 GroupName=成本。返回 (cost_strategies, error_or_None)。"""
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
                cost_only = [s for s in strategies if s.get('GroupName') == COST_GROUP_NAME]
                return cost_only, None
            last_err = (r.get('Error', {}) if isinstance(r, dict) else {'Message': str(r)})
        except Exception as e:
            last_err = {'Message': str(e)[:200]}
        time.sleep(RETRY_SLEEP)
    return [], last_err


def fetch_risks_one(sid, sk, strategy_id):
    """单 strategy 的 Risks 拉取。返回 (strategy_id, payload_dict 或 None, error_or_None)。

    关键:r['Risks'] 是 JSON-string,必须二次 json.loads。
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
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except (ValueError, TypeError):
            return default
    return default


def _extract_repair_url(repair_md):
    """从 strategy.Repair markdown 抽第一个腾讯云文档链接。"""
    if not repair_md or not isinstance(repair_md, str):
        return ''
    m = re.search(r'\]\((https://[^\s)]+)\)', repair_md)
    if m:
        return m.group(1)
    m = re.search(r'https://cloud\.tencent\.com/[^\s\)]+', repair_md)
    return m.group(0) if m else ''


def _saving_estimate(strategy, risk):
    """月省金额提取:
    1. risk 行字段 EstimatedSaving / MonthlySaving(advisor 部分 strategy 自带)
    2. strategy 元数据字段 EstimatedSaving(部分策略整体性预估)
    3. 都没有 → 0(LLM 在 Step 3 用 costopt 自算补)
    """
    for key in ('EstimatedSaving', 'MonthlySaving', 'SavingAmount'):
        v = risk.get(key) if isinstance(risk, dict) else None
        if v not in (None, '', 0, '0'):
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    for key in ('EstimatedSaving', 'MonthlySaving'):
        v = strategy.get(key) if isinstance(strategy, dict) else None
        if v not in (None, '', 0, '0'):
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0


def normalize_hit(strategy, risk, repair_url):
    """把 advisor 一行 risk 展平为按 instance_id 索引的 hit。

    Step 2 跳过名单 + Step 3 去重都基于 instance_id,因此这里直接按 instance 维度展开。
    """
    if not isinstance(risk, dict):
        return None
    instance_id = risk.get('InstanceId', '')
    if not instance_id:
        return None
    return {
        'strategy_id': strategy.get('StrategyId', ''),
        'strategy_name': strategy.get('Name', ''),
        'product': strategy.get('ProductDesc', '') or strategy.get('Product', ''),
        'product_key': strategy.get('Product', ''),
        'instance_id': instance_id,
        'instance_name': risk.get('InstanceName', ''),
        'region': risk.get('Region', ''),
        'zone': risk.get('Zone', ''),
        'level': _to_int(risk.get('Level'), 0),
        'risk_days': _to_int(risk.get('RiskDays'), 0),
        'tags': risk.get('Tags', []) or [],
        'monthly_saving_estimate': _saving_estimate(strategy, risk),
        'repair_url': repair_url,
        'pri_id': risk.get('PriId', ''),
        'status': risk.get('Status', ''),
    }


def build_by_strategy(hits):
    """strategy 聚合:每 strategy 命中数 / 总月省 / repair_url。LLM 渲染 §2.10 子表用。"""
    by = defaultdict(lambda: {
        'strategy_name': '',
        'product': '',
        'hit_count': 0,
        'total_saving': 0.0,
        'repair_url': '',
        'level_max': 0,
    })
    for h in hits:
        k = h['strategy_id']
        by[k]['strategy_name'] = h['strategy_name']
        by[k]['product'] = h['product']
        by[k]['hit_count'] += 1
        by[k]['total_saving'] += h.get('monthly_saving_estimate', 0.0)
        by[k]['repair_url'] = h.get('repair_url', '') or by[k]['repair_url']
        by[k]['level_max'] = max(by[k]['level_max'], h.get('level', 0))
    return {k: dict(v) for k, v in by.items()}


def main():
    sid = os.environ.get('TENCENTCLOUD_SECRET_ID', '').strip()
    sk = os.environ.get('TENCENTCLOUD_SECRET_KEY', '').strip()
    if not sid or not sk:
        sys.stderr.write('[FATAL] TENCENTCLOUD_SECRET_ID / _KEY 未设置\n')
        sys.exit(2)
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    out_dir = resolve_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    out = {
        'uin': uin,
        'scan_status': 'ok',
        'strategies_total': 0,
        'hits_total': 0,
        'hits': [],
        'by_strategy': {},
        'errors': [],
        'elapsed_s': 0.0,
    }

    t0 = time.time()

    # 1. 拉 cost-group strategies
    strategies, strat_err = fetch_strategies(sid, sk)
    if strat_err:
        out['scan_status'] = 'denied'
        out['errors'].append({'kind': 'fetch_strategies', 'error': strat_err})
        elapsed = time.time() - t0
        out['elapsed_s'] = round(elapsed, 1)
        json.dump(out, open(os.path.join(out_dir, 'advisor_cost.json'), 'w'),
                  ensure_ascii=False, indent=2, default=str)
        print(json.dumps({
            'scan_status': 'denied',
            'strategies_total': 0,
            'hits_total': 0,
            'elapsed_s': round(elapsed, 1),
            'out_dir': out_dir,
            'error': strat_err,
        }, ensure_ascii=False))
        return

    out['strategies_total'] = len(strategies)
    strategy_index = {s.get('StrategyId', ''): s for s in strategies}

    # 2. 8 路并发拉 risks
    all_hits = []
    failed = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_risks_one, sid, sk, s.get('StrategyId', '')): s.get('StrategyId', '')
                for s in strategies if s.get('StrategyId')}
        for f in as_completed(futs):
            sid_, risks_payload, err = f.result()
            strat = strategy_index.get(sid_, {'StrategyId': sid_})
            if risks_payload is None:
                failed[sid_] = err
                continue
            repair_url = _extract_repair_url(strat.get('Repair', ''))
            for r in risks_payload.get('risks', []):
                hit = normalize_hit(strat, r, repair_url)
                if hit:
                    all_hits.append(hit)

    # 3. scan_status 三态
    if failed:
        out['scan_status'] = 'denied' if len(failed) == out['strategies_total'] else 'partial'
        for sid_, err in failed.items():
            out['errors'].append({
                'kind': 'fetch_risks',
                'strategy_id': sid_,
                'strategy_name': strategy_index.get(sid_, {}).get('Name', ''),
                'error': err,
            })

    # 4. 排序稳定输出
    all_hits.sort(key=lambda h: (h.get('product', ''), h.get('strategy_id', ''), h.get('instance_id', '')))
    out['hits'] = all_hits
    out['hits_total'] = len(all_hits)
    out['by_strategy'] = build_by_strategy(all_hits)

    elapsed = time.time() - t0
    out['elapsed_s'] = round(elapsed, 1)

    # 5. 落盘
    json.dump(out, open(os.path.join(out_dir, 'advisor_cost.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)

    # 6. stdout summary
    print(json.dumps({
        'scan_status': out['scan_status'],
        'strategies_total': out['strategies_total'],
        'hits_total': out['hits_total'],
        'level_max': max((h.get('level', 0) for h in all_hits), default=0),
        'failed_strategies': len(failed),
        'total_saving_estimate': round(sum(h.get('monthly_saving_estimate', 0.0) for h in all_hits), 2),
        'elapsed_s': out['elapsed_s'],
        'out_dir': out_dir,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
