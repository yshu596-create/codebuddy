#!/usr/bin/env python3
"""Step 1 · CloudAudit 高危事件流扫描(2026-05-12 第 6 轮收编)。

用法:
    python3 scripts/inspect_audit_events.py
    AUDIT_WINDOW_DAYS=30 python3 scripts/inspect_audit_events.py   # 月度合规

输入:
    环境变量 TENCENTCLOUD_SECRET_ID / _KEY / INSPECTION_UIN
    可选环境变量 AUDIT_WINDOW_DAYS(默认 7,腾讯云上限单次 < 30)

输出:
    $OUT_DIR/inspect_audit_events.json
        ← {time_window, scan_status, events_by_category, summary_by_principal,
            service_account_set, total_events, total_categories_hit}
    stdout 只返 7 字段 summary JSON

为什么独立脚本而不放进 inspect_scan.py:
    - 时间窗参数化(7d/30d 两种巡检节奏),inspect_scan.py 是无参纯采集
    - 失败可独立重跑,不阻塞 12 域主扫
    - 43 个 EventName × 并发拉 ~ 5s/账户,串行进 inspect_scan.py 会拖慢主流程
    - 与 inspect_cam.py / inspect_cos.py 同位阶,作为 Step 1 的并行脚本
    - **本脚本不替代 inspect_scan.py 里的 cloudaudit 跟踪集块**(那是配置层),
      这里查的是事件流(行为层),两者互补:
        跟踪集块  → "审计日志有没有持久化到 CLS/COS"(合规摆设,🟡 中)
        事件流块  → "本周内有谁干了哪些高危操作"(运维核心信号,🔴/🟠)

历史教训(2026-05-12 第 5 轮已写,本轮再次印证):
    - 上一轮误把"跟踪集没配"当成 §10/§12 唯一信号,实际事件流默认采集
      (DescribeEvents 90 天窗口),跟踪集只是投递器,价值远小于事件流。
      用户一句质疑直接戳穿:"这个功能我理解为是要检测高危操作才对"——对。
    - 子账号身份调 DescribeEvents 默认有权(audit:DescribeEvents 在 ReadOnly
      策略里),不存在跟踪集查询那种 caller_subuser 三态歧义。

策略:43 个高危 EventName 白名单 × 6 路并发查
    - ActionType=Write 全拉行不通(30 天 STS GetFederationToken 噪声 1k+ 条,
      分页 2 分钟没出结果,2026-05-12 实测淘汰)
    - LookupAttributes 单 EventName 过滤 → 每类 < 50 条,精准且快
    - 失败 EventName(命名变更/服务停用)只是返回 events=[],不会报错,
      所以白名单可以放心列扩展项

时间窗设计:
    - 默认 7d(周巡检节奏):事件不会被反复告警(30d 窗口下一个事件被报 30 次)
    - 月度合规:env AUDIT_WINDOW_DAYS=30
    - 实时告警:env AUDIT_WINDOW_DAYS=1(配 cron)
    - 上限严格 < 30:腾讯云 DescribeEvents 单次跨度 < 30 天,29 天稳妥

服务账号自动识别(降噪关键):
    Probe 实测发现 bh-add-white-list / cam-add_user / liuyun-sg /
    CVM_QCSLinkedRoleInClawPro 等会大量调高危接口(如一中心 33 次 CreateUser
    全是 bh-add-white-list 干的),逐条列报告会淹没真高危事件。
    判定规则(满足任一):
      1. Username 含 'QcsRole' / 'QCSLinkedRole'(腾讯云服务关联角色,腾讯云内部)
      2. 该 Username 单类 EventName 出现 ≥ 5 次(自动化批处理特征)
      3. Username 含 '_role' / '-role' / '-sg' 后缀且事件数 ≥ 3
    标记后报告里聚合显示一行,不展开明细。

字段清洗(踩过的坑):
    - EventTime 是字符串 → int() 转换
    - Resources 是 dict 单值 {ResourceName, ResourceType},不是 list
    - SecretId 字段裁剪保留前 12 位 + 后 4 位(脱敏,接 SOUL §零流转)
    - 一中心账户偶发 NetworkError → retry=3 + sleep 0.5
"""
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
from sign import call_api  # noqa: E402

# ── 并发凭证隔离（2026-06-13，与 cloud-billing-pro/cloud-costopt-pro/cloud-inspector-pro 同方案）──
# 历史 bug：多账号并发时各 specialist 读同一裸名 TENCENTCLOUD_SECRET_ID，last-write-wins
# 互相覆盖 → 多份报告同账号数据。现方案：接 --cloud/--project，进程内经
# build_credential_env.resolve(cloud, project) 从 SSM 取该项目专属凭证（KMS 服务端解密），
# sid/sk 全程内存传递给 fetch_event/call_api，绝不读裸名 os.environ、也不依赖前缀变量。
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)


def load_env_credentials(cloud='tencent', project=None):
    """进程内解析某 (cloud, project) 的裸凭证 dict（不污染 os.environ）。

    凭证唯一来源 = SSM（KMS 加密）。build_credential_env.resolve() 内部只走 SSM，
    没有 env 前缀路径、也没有裸名兜底。本函数因此**强制要求 project 非空**：
      - project 非空 → resolve(cloud, project) 从 SSM 取密钥（KMS 服务端解密）。
      - project 为空 → 直接返回空凭证（上层给出引导）。绝不读裸名 env。

    跨账号（STS AssumeRole）已收敛进 resolve()：若 SSM secret 带 role_arn，
    resolve() 已在进程内 assume 完毕，返回的 secret_id/secret_key 即临时凭证、并带回
    token（需作为 X-TC-Token 透传给 cloudaudit API）。本函数因此一并取出 token。
    """
    empty = {'secret_id': '', 'secret_key': '', 'token': ''}
    if not project:
        return empty
    try:
        import build_credential_env as _bce
    except Exception:
        return empty
    try:
        env = _bce.resolve(cloud, project)
    except SystemExit:
        return empty
    return {
        'secret_id': env.get('TENCENTCLOUD_SECRET_ID', '').strip(),
        'secret_key': env.get('TENCENTCLOUD_SECRET_KEY', '').strip(),
        # token：resolve() 做过 AssumeRole 时为临时凭证 Token；直连场景为空。
        'token': env.get('TENCENTCLOUD_TOKEN', '').strip(),
    }

CA_HOST = 'cloudaudit.tencentcloudapi.com'
CA_VER = '2019-03-19'
CA_REGION = 'ap-guangzhou'

# 9 大类高危 EventName 白名单(2026-05-12 probe 验证全部腾讯云有效命名)
# 命中类目 → 事件类别的映射在 main() 里
HIGH_RISK_CATEGORIES = {
    'ak_lifecycle': [
        'CreateAccessKey', 'DeleteAccessKey', 'UpdateAccessKey',
    ],
    'user_lifecycle': [
        'CreateUser', 'DeleteUser', 'UpdateUser',
        'CreateLoginProfile', 'DeleteLoginProfile', 'UpdateLoginProfile',
    ],
    'permission_grant': [
        'AttachUserPolicy', 'DetachUserPolicy',
        'AttachGroupPolicy', 'DetachGroupPolicy',
        'AttachRolePolicy', 'DetachRolePolicy',
        'CreatePolicy', 'UpdatePolicy', 'DeletePolicy',
    ],
    'mfa_change': [
        'SetMfaFlag', 'UnbindMFADevice',
    ],
    'resource_destroy': [
        'TerminateInstances', 'TerminateInstancesByApi',
        'DeleteBucket', 'PutBucketAcl',
        'IsolateDBInstance', 'DeleteDBInstance', 'OfflineIsolatedInstances',
    ],
    'kms_tamper': [
        'DisableKey', 'ScheduleKeyDeletion', 'CancelKeyDeletion',
    ],
    'sg_change': [
        'CreateSecurityGroupPolicies', 'ModifySecurityGroupPolicies',
        'DeleteSecurityGroup', 'ReplaceSecurityGroupPolicy',
    ],
    'audit_tamper': [
        # 攻击者反取证标志动作(出现即 🔴 严重)
        'DeleteAuditTrack', 'StopLogging', 'UpdateAuditTrack',
    ],
    'role_change': [
        'CreateRole', 'DeleteRole', 'UpdateRole', 'UpdateAssumeRolePolicy',
    ],
}

# 类别风险等级(供报告侧参考,不在脚本里判定)
CATEGORY_RISK = {
    'audit_tamper': 'critical',     # 反取证 → 🔴 严重
    'ak_lifecycle': 'high',         # 主账号 AK 操作 → 🔴
    'permission_grant': 'high',     # Admin 授予 → 🔴
    'resource_destroy': 'high',     # 批量销毁 → 🔴
    'kms_tamper': 'high',           # 主密钥操作 → 🔴
    'mfa_change': 'high',           # MFA 解绑 → 🟠
    'user_lifecycle': 'medium',     # 子用户 CRUD → 🟠
    'role_change': 'medium',        # 角色 CRUD → 🟠
    'sg_change': 'medium',          # SG 改动 → 🟠(看具体规则)
}

ALL_EVENT_NAMES = [n for names in HIGH_RISK_CATEGORIES.values() for n in names]
EVENT_TO_CATEGORY = {n: cat for cat, names in HIGH_RISK_CATEGORIES.items() for n in names}


def resolve_out_dir():
    d = os.environ.get('INSPECT_OUT_DIR', '').strip()
    if d:
        return d
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    if uin:
        return f'/tmp/inspect_{uin}'
    sys.stderr.write('[FATAL] INSPECTION_UIN 或 INSPECT_OUT_DIR 必须至少设置其一\n')
    sys.exit(2)


def safe_int(x, default=0):
    try:
        return int(x)
    except (ValueError, TypeError):
        return default


def mask_secret_id(sid):
    """SecretId 脱敏:保留前 12 位 + 后 4 位,中间用 ... 替代。"""
    if not sid or len(sid) < 20:
        return sid or ''
    return f'{sid[:12]}...{sid[-4:]}'


def normalize_event(e):
    """字段清洗:把脏字段(字符串时间戳、嵌套 Resources、长 SecretId)统一形态。

    输出字段固定为:event_name, event_name_cn, event_time, event_source,
                username, source_ip, region, error_code, resource_name,
                resource_type, secret_id_masked, request_id
    """
    res = e.get('Resources') or {}
    if not isinstance(res, dict):
        res = {}
    return {
        'event_name': e.get('EventName', ''),
        'event_name_cn': e.get('EventNameCn', ''),
        'event_time': safe_int(e.get('EventTime')),
        'event_source': e.get('EventSource', ''),
        'username': e.get('Username', ''),
        'source_ip': e.get('SourceIPAddress', ''),
        'region': e.get('EventRegion', ''),
        'error_code': str(e.get('ErrorCode', '0')),
        'resource_name': res.get('ResourceName', ''),
        'resource_type': res.get('ResourceType', ''),
        'secret_id_masked': mask_secret_id(e.get('SecretId', '')),
        'request_id': e.get('RequestID', ''),
    }


def fetch_event(sid, sk, event_name, start_ts, end_ts):
    """单 EventName 拉一页(MaxResults=50),retry=3。

    返回 (event_name, events_list 或 None, error_or_none)。
    高危类事件月度量级远小于 50,单页足够;若某天异常爆量,留 next_token
    扩展空间(本轮按"够用就行"原则不做多页)。
    """
    payload = {
        'StartTime': start_ts,
        'EndTime': end_ts,
        'MaxResults': 50,
        'LookupAttributes': [{'AttributeKey': 'EventName',
                              'AttributeValue': event_name}],
    }
    last_err = None
    for attempt in range(3):
        try:
            r = call_api(host=CA_HOST, service='cloudaudit',
                         action='DescribeEvents', version=CA_VER,
                         payload=payload, region=CA_REGION,
                         secret_id=sid, secret_key=sk)
            if isinstance(r, dict) and not r.get('Error'):
                evs = r.get('Events', []) or []
                # 注:腾讯云未实现的 EventName 也只是返回 events=[],不报错,
                # 所以白名单里的"幽灵命名"会安静地变 0,不污染 errors
                return event_name, evs, None
            last_err = (r.get('Error', {}) if isinstance(r, dict) else {'Message': str(r)})
        except Exception as e:
            last_err = {'Message': str(e)[:200]}
        time.sleep(0.5)
    return event_name, None, last_err


def detect_service_accounts(events_flat):
    """识别服务账号(自动化/QcsRole),供报告侧聚合显示降噪。

    判定规则(满足任一):
      1. Username 含 'QcsRole' / 'QCSLinkedRole'(腾讯云服务关联角色)
      2. 同 Username 单类 EventName 出现 ≥ 5 次(自动化批处理特征)
      3. Username 含 '_role' / '-role' / '-sg' / '-add' 后缀且事件数 ≥ 3
    """
    user_event_counter = defaultdict(lambda: defaultdict(int))
    for e in events_flat:
        u = e.get('username', '')
        if u:
            user_event_counter[u][e.get('event_name', '')] += 1

    service_set = set()
    for u, en_cnt in user_event_counter.items():
        total = sum(en_cnt.values())
        # 规则 1:服务关联角色
        if 'QcsRole' in u or 'QCSLinkedRole' in u:
            service_set.add(u)
            continue
        # 规则 2:单 EventName 高频
        if any(c >= 5 for c in en_cnt.values()):
            service_set.add(u)
            continue
        # 规则 3:命名后缀 + 事件数
        suffix_hit = any(s in u for s in ('_role', '-role', '-sg', '-add'))
        if suffix_hit and total >= 3:
            service_set.add(u)
    return sorted(service_set)


def build_summary_by_principal(events_flat, service_set):
    """按 Username 聚合:total + 类别命中分布。"""
    summary = defaultdict(lambda: {'total': 0, 'events': defaultdict(int),
                                    'is_service': False})
    for e in events_flat:
        u = e.get('username', '?')
        en = e.get('event_name', '?')
        summary[u]['total'] += 1
        summary[u]['events'][en] += 1
    out = {}
    for u, d in summary.items():
        out[u] = {
            'total': d['total'],
            'events': dict(d['events']),
            'is_service': u in set(service_set),
        }
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description="CloudAudit 高危事件扫描")
    ap.add_argument("--cloud", default="tencent",
                    help="云厂商（默认 tencent），并发隔离用")
    ap.add_argument("--project", default="",
                    help="项目/账号代号（如 ADC/TSP/YX）；必传——进程内经 "
                         "build_credential_env.resolve() 从 SSM 取该账号专属凭证（KMS 服务端解密），"
                         "规避竞态。省略则返回空凭证、走环境未就绪引导（不读裸名 env）")
    args, _ = ap.parse_known_args()
    cred = load_env_credentials(cloud=args.cloud, project=args.project or None)
    sid = cred['secret_id']
    sk = cred['secret_key']
    if not sid or not sk:
        sys.stderr.write(
            "[FATAL] 无法从 SSM 取到该账号凭证。请确认运行端已配置 runner key "
            "TENCENTCLOUD_BOOTSTRAP_SECRET_ID/_KEY，且 SSM 中存在 "
            f"cloudops/{args.cloud}/{args.project or '<PROJECT>'} 密钥。"
            "（本脚本只从 SSM 取凭证，不读裸名 env。）\n"
        )
        sys.exit(2)
    # 跨账号：若 resolve() 做过 AssumeRole，会带回临时 Token；注入进程局部 holder，
    # 之后所有 call_api 自动带 X-TC-Token（不写共享 os.environ，并发隔离）。
    _tok = cred.get('token', '')
    if _tok:
        try:
            from sign import set_process_token
            set_process_token(_tok)
        except Exception:
            pass
    out_dir = resolve_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    # 时间窗:默认 7 天,可通过 AUDIT_WINDOW_DAYS 环境变量覆盖
    window_days = safe_int(os.environ.get('AUDIT_WINDOW_DAYS', '7'), default=7)
    if window_days < 1:
        window_days = 1
    if window_days > 29:  # 腾讯云上限严格 < 30 天,29 稳妥
        window_days = 29
    now = int(time.time())
    start = now - window_days * 86400

    out = {
        'time_window': {
            'start': start,
            'end': now,
            'days': window_days,
            'start_iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)),
            'end_iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
        },
        'scan_status': 'ok',
        'events_by_category': {cat: [] for cat in HIGH_RISK_CATEGORIES},
        'summary_by_principal': {},
        'service_account_set': [],
        'total_events': 0,
        'total_categories_hit': 0,
        'category_risk': CATEGORY_RISK,
        'errors': [],
    }

    # 43 个 EventName × 6 路并发拉
    t0 = time.time()
    all_events_flat = []
    failed_events = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_event, sid, sk, en, start, now): en
                for en in ALL_EVENT_NAMES}
        for f in as_completed(futs):
            en, evs, err = f.result()
            if evs is None:
                failed_events[en] = err
                continue
            cat = EVENT_TO_CATEGORY[en]
            for raw in evs:
                normalized = normalize_event(raw)
                out['events_by_category'][cat].append(normalized)
                all_events_flat.append(normalized)
    elapsed = time.time() - t0

    # 失败的 EventName 全列入 errors(failed_events 通常是网络抖动,非命名错误)
    for en, err in failed_events.items():
        out['errors'].append({
            'kind': 'audit_event_fetch',
            'event_name': en,
            'error': err,
        })

    # 检查权限错误(整个白名单全失败 → 子账号 audit:DescribeEvents 无权)
    if len(failed_events) == len(ALL_EVENT_NAMES):
        out['scan_status'] = 'denied'

    # 服务账号识别 + 主体聚合
    service_set = detect_service_accounts(all_events_flat)
    out['service_account_set'] = service_set
    out['summary_by_principal'] = build_summary_by_principal(
        all_events_flat, service_set
    )

    # 类别命中统计
    nonzero_cats = [c for c, evs in out['events_by_category'].items() if evs]
    out['total_events'] = len(all_events_flat)
    out['total_categories_hit'] = len(nonzero_cats)

    # 落盘
    json.dump(out, open(os.path.join(out_dir, 'inspect_audit_events.json'), 'w'),
              ensure_ascii=False, indent=2, default=str)

    # stdout summary(给 LLM/调用方)
    print(json.dumps({
        'scan_status': out['scan_status'],
        'window_days': window_days,
        'total_events': out['total_events'],
        'total_categories_hit': out['total_categories_hit'],
        'category_counts': {c: len(evs)
                             for c, evs in out['events_by_category'].items()
                             if evs},
        'service_accounts': len(service_set),
        'failed_event_names': len(failed_events),
        'elapsed_s': round(elapsed, 1),
        'out_dir': out_dir,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
