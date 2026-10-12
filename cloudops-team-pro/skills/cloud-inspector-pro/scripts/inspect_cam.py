#!/usr/bin/env python3
"""Step 2:CAM 账号安全审计(子账号 × AK/MFA/登录时间/AK闲置/用户组/Admin 权限 并发)。

腾讯云的访问管理服务叫 CAM(cam.tencentcloudapi.com),不叫 IAM——脚本名跟 API 一致。
报告 §01 章节标题保留 "IAM/CAM" 是因为对齐 SOP 文档的双叫法,但底层数据全部来自 CAM。

用法:
    python3 scripts/inspect_cam.py

输出:
    $OUT_DIR/inspect_cam.json  ← { principals: [...], policies: [...], admin_grants: [...], ... }
    stdout 一行 summary

OUT_DIR 与 inspect_scan.py 同策略(解析 $INSPECT_OUT_DIR / $INSPECTION_UIN)。

为什么单独一步而不是塞进 Step 1:CAM API 是全局服务(region='',
不分地域),调用维度是"子账号 × N",和 Step 1 的"产品 × 地域"模型不一样,
放一起会让并发任务调度更复杂。分开后两步都干净。

为什么 probe_uin 必须并发:2026-05-11 session 实测,子账号串行 ListAccessKeys +
GetSafeAuthFlagColl 跑了 380s,占总耗时的 40%。

2026-05-11 改进:
  - 增加 DescribeSubAccounts 批量获取 LastLoginTime
  - 对"从未登录"和">90天未登录"的账号,通过 CloudAudit DescribeEvents
    交叉校验 ConsoleLogin 事件,降低误判率
  - 增加 GetSecurityLastUsed 获取每个 AK 的最后使用时间(闲置检测)
"""
import json, os, sys, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
from sign import call_api


def resolve_out_dir():
    d = os.environ.get('INSPECT_OUT_DIR', '').strip()
    if d:
        return d
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    if uin:
        return f'/tmp/inspect_{uin}'
    sys.stderr.write('[FATAL] INSPECTION_UIN 或 INSPECT_OUT_DIR 必须至少设置其一\n')
    sys.exit(2)


OUT_DIR = resolve_out_dir()
os.makedirs(OUT_DIR, exist_ok=True)

sid = os.environ['TENCENTCLOUD_SECRET_ID']
sk = os.environ['TENCENTCLOUD_SECRET_KEY']


def cam(action, payload, version='2019-01-16'):
    return call_api(host='cam.tencentcloudapi.com', service='cam', action=action,
                    version=version, payload=payload, region='',
                    secret_id=sid, secret_key=sk)


def cloudaudit(action, payload, region='ap-guangzhou'):
    return call_api(host='cloudaudit.tencentcloudapi.com', service='cloudaudit',
                    action=action, version='2019-03-19', payload=payload,
                    region=region, secret_id=sid, secret_key=sk)


# ── Phase 0: 调用者身份探测(决定 CAM 章节哪些判定可信) ────────────
# 2026-05-12 修复:不传 TargetUin 的 ListAccessKeys 返回的是"调用者自己"的 AK,
# 不是"主账号 AK"。若环境变量配的扫描凭证是子账号 AK,此前会被脚本误标
# 为 root_aks 写入报告 §01/§09(还会带"最近使用=今天",因为扫描器自己刚用过)。
# 同理子账号身份调 ListAccessKeys(TargetUin=他人) / GetSafeAuthFlagColl(他人) /
# GetUser(他人) / DescribeSubAccounts / ListGroupsForUser(他人) / CloudAudit
# DescribeEvents 全量都会被拒,产出空值会被下游误判为"无 AK / 未开 MFA / 从未登录"。
# 修法:先 GetUserAppId 取调用者 Uin/OwnerUin,is_root_caller=False 时:
#   - root_aks 直接置空 + 标 status=skipped_subuser_caller
#   - 子用户/协助者枚举照常发(子账号通常能 ListUsers/ListCollaborators),
#     但 per-principal 的 AK/MFA/登录时间/用户组字段全部标 *_scan_status,
#     LLM 在报告里看到 skipped 必须降级表达而非"无"。
try:
    _me = cam('GetUserAppId', {})
    _err = _me.get('Error') if isinstance(_me, dict) else None
    if _err:
        sys.stderr.write(f'[FATAL] GetUserAppId failed: {_err}\n')
        sys.exit(2)
    caller_uin = str(_me.get('Uin', ''))
    owner_uin = str(_me.get('OwnerUin', ''))
    is_root_caller = bool(caller_uin) and caller_uin == owner_uin
except Exception as e:
    sys.stderr.write(f'[FATAL] GetUserAppId exception: {e!r}\n')
    sys.exit(2)

caller_identity = {
    'uin': caller_uin,
    'owner_uin': owner_uin,
    'is_root_caller': is_root_caller,
    # CAM 章节降级原因码,LLM 看到此值非空时必须按"扫描凭证为子账号"的模板写
    'degraded_reason': '' if is_root_caller else 'subuser_caller',
}
print(f'[Phase0] caller_uin={caller_uin} owner_uin={owner_uin} '
      f'is_root_caller={is_root_caller}', file=sys.stderr)


def _subuser_skipped():
    """子账号身份下,需要主账号权限的 API 的统一占位返回。"""
    return {'_scan_status': 'skipped_subuser_caller'}


try:
    users = cam('ListUsers', {}).get('Data', [])
except Exception as e:
    sys.stderr.write(f'[WARN] ListUsers exception: {e!r}\n')
    users = []
try:
    _collab_r = cam('ListCollaborators', {})
    collabs = _collab_r.get('Data', [])
    # 子账号通常无权调 ListCollaborators;返回空时无法区分"真的没协助者"还是"无权"
    # 主账号身份下空 = 真没有;子账号身份下空 = 不可信,需在 audit_cross_check 里标
    collaborators_scan_status = 'ok' if is_root_caller else (
        'ok' if collabs else 'skipped_subuser_caller'
    )
except Exception as e:
    sys.stderr.write(f'[WARN] ListCollaborators exception: {e!r}\n')
    collabs = []
    collaborators_scan_status = 'skipped_subuser_caller' if not is_root_caller else 'error'


# ── Phase 1: per-UIN probe (AK + MFA, 8-way parallel) ──────────────
# 2026-05-12: 子账号身份调 ListAccessKeys(TargetUin=他人) / GetSafeAuthFlagColl(他人)
# 通常会被拒(AccessDenied / OperationDenied.SubUin)。call_api 把异常包成
# {'Error': {...}} 返回,这里识别并落 _scan_status。
def probe_uin(u):
    uin = int(u['Uin'])
    aks, flags = [], {}
    aks_status, mfa_status = 'ok', 'ok'
    # 子账号身份探测自己 OK,探测他人可能被拒(取决于是否有 cam-read 等策略)
    is_self = (str(uin) == caller_uin)
    # 2026-05-13: 不再无条件跳过。先尝试调接口,根据实际返回决定 scan_status
    try:
        r = cam('ListAccessKeys', {'TargetUin': uin})
        err = r.get('Error') if isinstance(r, dict) else None
        if err:
            aks_status = 'denied' if 'Denied' in (err.get('Code') or '') else 'error'
        else:
            aks = r.get('AccessKeys', [])
    except Exception:
        aks_status = 'error'
    try:
        r = cam('GetSafeAuthFlagColl', {'SubUin': uin})
        err = r.get('Error') if isinstance(r, dict) else None
        if err:
            mfa_status = 'denied' if 'Denied' in (err.get('Code') or '') else 'error'
        else:
            flags = r.get('LoginFlag', {})
    except Exception:
        mfa_status = 'error'
    return {'uin': uin, 'name': u.get('Name', ''),
            'type': u.get('_type', 'user'),
            'console_login': u.get('ConsoleLogin', 0),
            'aks': aks,
            'aks_scan_status': aks_status,
            'mfa_flags': {k: v for k, v in flags.items() if v},
            'mfa_scan_status': mfa_status}


all_principals = users + [
    {'Uin': c['Uin'], 'Name': c.get('Name', ''), '_type': 'collaborator'} for c in collabs
]
with ThreadPoolExecutor(max_workers=16) as ex:
    cam_data = list(ex.map(probe_uin, all_principals))


# ── Phase 2: GetUser → RecentlyLoginTime (权威数据源) ──────────────
# 2026-05-11 发现: DescribeSubAccounts 的 LastLoginTime 对 SSO/企微扫码
# 登录的账号返回空值,导致误判为"从未登录"。
# GetUser 的 RecentlyLoginTime 才是准确的登录时间数据源。
# 但 GetUser 只支持子用户(按 Name 查),不支持协作者。
# 策略: 子用户用 GetUser; 协作者仍用 DescribeSubAccounts 作为 fallback。
# 2026-05-12: GetUser(他人) / DescribeSubAccounts 在子账号身份下通常被拒
# (AuthFailure / OperationDenied),整批返空会被下游误判"从未登录"。
# 子账号身份直接短路,在每个 principal 上落 last_login_scan_status=skipped。
def fetch_login_times(cam_data):
    """获取每个账号的最近登录时间。
    子用户: GetUser(按 Name), 取 RecentlyLoginTime(准确, 含 SSO 登录)
    协作者: DescribeSubAccounts(批量, LastLoginTime 可能不含 SSO 登录)
    """
    if not is_root_caller:
        # 2026-05-13: 不再无条件跳过。先尝试调接口,根据实际返回决定是否降级。
        # 子账号如果有 cam-read 策略, GetUser/DescribeSubAccounts 可正常返回。
        # 先试探一个子用户, 如果成功则正常走采集流程
        test_user = next((p for p in cam_data if p.get('type', 'user') == 'user' and p.get('name')), None)
        can_fetch_login = False
        if test_user:
            try:
                r = cam('GetUser', {'Name': test_user['name']})
                err = r.get('Error') if isinstance(r, dict) else None
                if not err and r.get('Uin'):
                    can_fetch_login = True
            except Exception:
                pass
        if not can_fetch_login:
            for p in cam_data:
                p['last_login_time'] = ''
                p['last_login_ip'] = ''
                p['last_login_scan_status'] = 'skipped_subuser_caller'
            return
        # 探测成功, 继续正常采集流程(fall through 到下方的 Phase 2a/2b)

    # Phase 2a: 子用户用 GetUser (并发, 8路)
    sub_users = [p for p in cam_data if p.get('type', 'user') == 'user']
    collab_users = [p for p in cam_data if p.get('type') == 'collaborator']

    def get_user_login(p):
        name = p.get('name', '')
        if not name:
            return p['uin'], '', ''
        max_retries = 3
        for attempt in range(max_retries):
            try:
                r = cam('GetUser', {'Name': name})
                err = r.get('Error')
                if err:
                    if err.get('Code') == 'RequestLimitExceeded' and attempt < max_retries - 1:
                        time.sleep(1.0 + attempt * 0.5)  # backoff: 1s, 1.5s, 2s
                        continue
                    sys.stderr.write(f'[WARN] GetUser({name}) API error: {err}\n')
                    return p['uin'], '', ''
                return p['uin'], r.get('RecentlyLoginTime', ''), r.get('RecentlyLoginIP', '')
            except Exception as e:
                sys.stderr.write(f'[WARN] GetUser({name}) exception: {e}\n')
                return p['uin'], '', ''
        return p['uin'], '', ''

    login_map = {}  # uin → {last_login_time, last_login_ip}
    # max_workers=15: GetUser QPS=20, 15 workers ≈ 15 QPS (per-UIN 1 call),留 5 QPS 余量
    with ThreadPoolExecutor(max_workers=15) as ex:
        results = list(ex.map(get_user_login, sub_users))
    for uin, lt, lip in results:
        login_map[uin] = {'last_login_time': lt, 'last_login_ip': lip}

    # Phase 2b: 协作者用 DescribeSubAccounts (批量 fallback)
    if collab_users:
        collab_uins = [p['uin'] for p in collab_users]
        BATCH = 50
        for i in range(0, len(collab_uins), BATCH):
            batch = collab_uins[i:i+BATCH]
            try:
                r = cam('DescribeSubAccounts', {'FilterSubAccountUin': batch})
                for a in r.get('SubAccounts', []):
                    login_map[a['Uin']] = {
                        'last_login_time': a.get('LastLoginTime', ''),
                        'last_login_ip': a.get('LastLoginIp', ''),
                    }
            except Exception as e:
                sys.stderr.write(f'[WARN] DescribeSubAccounts batch {i} failed: {e}\n')

    # 写回 cam_data
    for p in cam_data:
        info = login_map.get(p['uin'], {})
        p['last_login_time'] = info.get('last_login_time', '')
        p['last_login_ip'] = info.get('last_login_ip', '')
        p['last_login_scan_status'] = 'ok'

fetch_login_times(cam_data)


# ── Phase 3: CloudAudit 交叉校验(仅对疑似未活跃账号) ─────────────
def identify_suspect_uins(cam_data):
    """找出"从未登录"或">90天未登录"的可登录控制台账号 UIN 列表。"""
    now = datetime.now()
    suspects = []
    for p in cam_data:
        if p.get('console_login') != 1 and p.get('type') != 'collaborator':
            continue
        lt = p.get('last_login_time', '')
        if not lt or lt == '0000-00-00 00:00:00':
            suspects.append(p['uin'])
        else:
            try:
                dt = datetime.strptime(lt, '%Y-%m-%d %H:%M:%S')
                if (now - dt).days > 90:
                    suspects.append(p['uin'])
            except (ValueError, TypeError):
                suspects.append(p['uin'])
    return suspects


def cloudaudit_verify_logins(suspect_uins):
    """通过 CloudAudit DescribeEvents 查询 90 天内的 ConsoleLogin 事件,
    返回 {uin: latest_login_time} 映射(只含有记录的)。"""
    if not suspect_uins:
        return {}
    audit_logins = {}  # uin → latest_event_time_str
    end_ts = int(time.time())
    start_ts = end_ts - 86400 * 89  # 89天, 保证在 CloudAudit 合法范围内
    next_token = None
    max_pages = 20  # 防无限循环
    page = 0
    suspect_set = set(str(u) for u in suspect_uins)

    while page < max_pages:
        payload = {
            'MaxResults': 50,
            'StartTime': start_ts,
            'EndTime': end_ts,
            'LookupAttributes': [
                {'AttributeKey': 'EventName', 'AttributeValue': 'ConsoleLogin'}
            ],
        }
        if next_token:
            payload['NextToken'] = next_token
        try:
            r = cloudaudit('DescribeEvents', payload)
            events = r.get('Events', [])
            for ev in events:
                try:
                    detail = json.loads(ev.get('CloudAuditEvent', '{}')) \
                        if isinstance(ev.get('CloudAuditEvent'), str) \
                        else ev.get('CloudAuditEvent', {})
                    uid = detail.get('userIdentity', {})
                    principal_id = str(uid.get('principalId', ''))
                    event_ts = detail.get('eventTime', 0)
                    if principal_id in suspect_set:
                        event_time_str = datetime.fromtimestamp(int(event_ts)).strftime('%Y-%m-%d %H:%M:%S') \
                            if event_ts else ''
                        # 保留最新的
                        if principal_id not in audit_logins or event_time_str > audit_logins.get(principal_id, ''):
                            audit_logins[principal_id] = event_time_str
                except Exception:
                    continue
            next_token = r.get('NextToken')
            if not next_token or r.get('ListOver', True):
                break
        except Exception as e:
            sys.stderr.write(f'[WARN] CloudAudit DescribeEvents page {page} failed: {e}\n')
            break
        page += 1

    return audit_logins


suspect_uins = identify_suspect_uins(cam_data) if is_root_caller else []
audit_logins = cloudaudit_verify_logins(suspect_uins) if is_root_caller else {}
audit_scan_status = 'ok' if is_root_caller else 'skipped_subuser_caller'

# 写回 cam_data: 增加 audit_last_login 字段
for p in cam_data:
    uin_str = str(p['uin'])
    if uin_str in audit_logins:
        p['audit_last_login'] = audit_logins[uin_str]
    else:
        p['audit_last_login'] = ''


# ── Phase 4: AK 闲置检测(GetSecurityLastUsed, 每批 ≤10) ───────────
def fetch_ak_last_used(cam_data):
    """批量获取每个 AK 的最后使用时间。并行发请求以利用 20 QPS 预算。"""
    all_ak_ids = []
    ak_owner_map = {}  # akid → principal name
    for p in cam_data:
        for ak in p.get('aks', []):
            akid = ak.get('AccessKeyId', '')
            if akid:
                all_ak_ids.append(akid)
                ak_owner_map[akid] = p['name']
    last_used_map = {}  # akid → {last_used_date, owner}
    BATCH = 10

    def _fetch_batch(batch):
        try:
            r = cam('GetSecurityLastUsed', {'SecretIdList': batch})
            items = r.get('SecretIdLastUsedRows', [])
            return [(item.get('SecretId', ''), {
                'last_used_date': item.get('LastUsedDate', ''),
                'last_secret_used_date': item.get('LastSecretUsedDate', ''),
            }) for item in items]
        except Exception as e:
            sys.stderr.write(f'[WARN] GetSecurityLastUsed batch failed: {e}\n')
            return []

    # 并行发 batch 请求,利用 GetSecurityLastUsed 20 QPS
    batches = [all_ak_ids[i:i+BATCH] for i in range(0, len(all_ak_ids), BATCH)]
    with ThreadPoolExecutor(max_workers=10) as ex:
        for results in ex.map(_fetch_batch, batches):
            for akid, info in results:
                last_used_map[akid] = info
    # 写回 cam_data 的每个 AK
    for p in cam_data:
        for ak in p.get('aks', []):
            akid = ak.get('AccessKeyId', '')
            info = last_used_map.get(akid, {})
            ak['last_used_date'] = info.get('last_used_date', '')
            ak['last_secret_used_date'] = info.get('last_secret_used_date', '')

fetch_ak_last_used(cam_data)


# ── Phase 5: 用户组归属(ListGroupsForUser, 8 路并发) ──────────────
# 来源: references/01-iam-cam.md "用户组归属检查"
# 判定标准在 reference 里(未归属任何组 = 🟡 中风险),脚本只采字段不做判定。
# 2026-05-12: 子账号身份调 ListGroupsForUser(SubUin=他人) 通常被拒,空结果
# 会被下游误判"未归属任何组"。短路标 skipped。
def fetch_user_groups(cam_data):
    if not is_root_caller:
        # 2026-05-13: 不再无条件跳过。先探测一个 UIN, 成功则正常采集。
        test_target = next((p for p in cam_data if (p.get('console_login') == 1 or p.get('type') == 'collaborator')), None)
        can_fetch_groups = False
        if test_target:
            try:
                r = cam('ListGroupsForUser', {'SubUin': int(test_target['uin'])})
                err = r.get('Error') if isinstance(r, dict) else None
                if not err:
                    can_fetch_groups = True
            except Exception:
                pass
        if not can_fetch_groups:
            for p in cam_data:
                if p.get('console_login') == 1 or p.get('type') == 'collaborator':
                    p['groups'] = []
                    p['groups_scan_status'] = 'skipped_subuser_caller'
            return
        # 探测成功, fall through 到正常采集

    targets = [p for p in cam_data if p.get('console_login') == 1 or p.get('type') == 'collaborator']

    def one(p):
        try:
            r = cam('ListGroupsForUser', {'SubUin': int(p['uin'])})
            return p['uin'], [{'group_id': g.get('GroupId'), 'group_name': g.get('GroupName', '')}
                              for g in r.get('GroupInfo', [])]
        except Exception:
            return p['uin'], []

    with ThreadPoolExecutor(max_workers=15) as ex:
        for uin, groups in ex.map(one, targets):
            for p in cam_data:
                if p['uin'] == uin:
                    p['groups'] = groups
                    p['groups_scan_status'] = 'ok'
                    break

fetch_user_groups(cam_data)


# ── Phase 6: Admin 权限来源(预设 AdministratorAccess + 自定义 *:* 策略) ──
# 来源: references/01-iam-cam.md "Administrator 权限审查方法"
# 字段产出 admin_grants: [{uin, name, source}] 让 LLM 写报告时直接出表。
import urllib.parse


def is_wildcard_allow(policy_doc):
    try:
        if isinstance(policy_doc, str):
            policy_doc = json.loads(urllib.parse.unquote(policy_doc))
        stmts = policy_doc.get('statement', policy_doc.get('Statement', []))
        if isinstance(stmts, dict):
            stmts = [stmts]
        for s in stmts:
            effect = (s.get('effect') or s.get('Effect', '')).lower()
            if effect != 'allow':
                continue
            act = s.get('action') or s.get('Action', [])
            res = s.get('resource') or s.get('Resource', [])
            act = [act] if isinstance(act, str) else act
            res = [res] if isinstance(res, str) else res
            if '*' in act and '*' in res:
                return True
    except Exception:
        pass
    return False


def list_entities_for_policy(policy_id):
    out, page = [], 1
    while True:
        try:
            d = cam('ListEntitiesForPolicy', {'PolicyId': policy_id, 'Page': page, 'Rp': 100})
        except Exception:
            break
        ents = d.get('List', []) or []
        if not ents:
            break
        out.extend(ents)
        if len(out) >= d.get('TotalNum', 0):
            break
        page += 1
    return out


def _cam_with_retry(action, payload, retries=2, sleep_s=0.5):
    """call_api 将网络/SSL 异常包成 {'Error': {...}} 静默返回,这里识别并重试。
    真实业务错误(如权限不足)也在 Error 里,但重试无害——成功就覆盖,失败照样返回最后一次。"""
    last = None
    for i in range(retries + 1):
        r = cam(action, payload)
        err = r.get('Error')
        if not err:
            return r
        last = r
        code = (err.get('Code') or '') if isinstance(err, dict) else ''
        print(f"[RETRY {i+1}/{retries+1}] {action} payload={payload} err={err}", file=sys.stderr)
        # 业务错误(AuthFailure/InvalidParameter 等)不重试
        if not ('NetworkError' in code or 'Timeout' in code or 'InternalError' in code):
            break
        time.sleep(sleep_s)
    return last


def expand_admin_entities(policy_id, policy_name, grants):
    ents = list_entities_for_policy(policy_id)
    direct_users = [e for e in ents if e.get('RelatedType') == 1]
    groups = [e for e in ents if e.get('RelatedType') == 2]
    collaborators = [e for e in ents if e.get('RelatedType') == 3]
    for u in direct_users:
        grants.append({'uin': str(u.get('Uin', '')), 'name': u.get('Name', ''),
                       'source': f'{policy_name}(直接)'})
    for c in collaborators:
        grants.append({'uin': str(c.get('Uin', '')), 'name': c.get('Name', ''),
                       'source': f'{policy_name}(协助者)'})
    for g in groups:
        gid, gname = g.get('Id'), g.get('Name', '')
        try:
            gid_int = int(gid) if gid is not None else None
            gd = _cam_with_retry('ListUsersForGroup', {'GroupId': gid_int, 'Rp': 100})
            if gd.get('Error'):
                print(f"[WARN-ADMIN] ListUsersForGroup policy={policy_name} gid={gid} gname={gname} "
                      f"final-err={gd.get('Error')}", file=sys.stderr)
                continue
            members = gd.get('UserInfo', []) or []
            print(f"[DEBUG] ListUsersForGroup policy={policy_name} gid={gid} gname={gname} "
                  f"TotalNum={gd.get('TotalNum')} members={len(members)}", file=sys.stderr)
            for m in members:
                grants.append({'uin': str(m.get('Uin', '')), 'name': m.get('Name', ''),
                               'source': f'{policy_name}>组[{gname}]'})
        except Exception as e:
            print(f"[EXC] ListUsersForGroup policy={policy_name} gid={gid} gname={gname} err={e!r}",
                  file=sys.stderr)


# ── 自定义策略(扫描用,单次调用,Phase 6 依赖) ───────────────────────
policies = cam('ListPolicies', {'Scope': 'Local', 'Rp': 200}).get('List', [])
print(f"[DEBUG] ListPolicies Scope=Local count={len(policies)}", file=sys.stderr)

admin_grants = []
custom_admin_policies = []
# 1) 预设 AdministratorAccess
try:
    pl = cam('ListPolicies', {'Keyword': 'AdministratorAccess', 'Scope': 'All'}).get('List', [])
    print(f"[DEBUG] ListPolicies Keyword=AdministratorAccess count={len(pl)}", file=sys.stderr)
    for p in pl:
        if p.get('PolicyName') == 'AdministratorAccess':
            expand_admin_entities(p['PolicyId'], 'AdministratorAccess', admin_grants)
            break
except Exception as e:
    print(f"[EXC] ListPolicies(AdministratorAccess) err={e!r}", file=sys.stderr)
# 2) 自定义策略中等效 *:* 全量权限 — 先并行 GetPolicy 筛出 *:* 策略,再串行 expand
def _check_wildcard_policy(p):
    """检查单条自定义策略是否等效 *:*,返回 (policy_id, policy_name) 或 None。"""
    pid, pname = p.get('PolicyId'), p.get('PolicyName', '')
    if pname == 'AdministratorAccess':
        return None
    try:
        pd = cam('GetPolicy', {'PolicyId': pid})
        if is_wildcard_allow(pd.get('PolicyDocument', '')):
            return (pid, pname)
    except Exception as e:
        print(f"[EXC] GetPolicy pid={pid} pname={pname} err={e!r}", file=sys.stderr)
    return None

# GetPolicy 20 QPS → 15 路并发
with ThreadPoolExecutor(max_workers=15) as ex:
    wildcard_results = list(ex.map(_check_wildcard_policy, policies))

for wr in wildcard_results:
    if wr:
        pid, pname = wr
        custom_admin_policies.append({'policy_id': pid, 'policy_name': pname})
        expand_admin_entities(pid, pname, admin_grants)


# ── 主账号 AK 探测(不传 TargetUin) ─────────────────────────────────
# 2026-05-12 修复:ListAccessKeys 不传 TargetUin 返回的是"调用者自己"的 AK。
# 子账号身份下绝对不能把这个结果写进 root_aks(否则报告里"主账号活跃 AK"完全失真,
# 且"最近使用=今天"的本质是 inspector 自己刚发的请求)。
# 只有 is_root_caller=True 时才扫;否则置空并标 skipped。
if is_root_caller:
    try:
        _r = cam('ListAccessKeys', {})
        err = _r.get('Error') if isinstance(_r, dict) else None
        if err:
            root_aks = []
            root_aks_scan_status = 'denied' if 'Denied' in (err.get('Code') or '') else 'error'
        else:
            root_aks = _r.get('AccessKeys', [])
            root_aks_scan_status = 'ok'
    except Exception:
        root_aks = []
        root_aks_scan_status = 'error'
    # 主账号 AK 闲置检测
    if root_aks:
        root_ak_ids = [ak.get('AccessKeyId', '') for ak in root_aks if ak.get('AccessKeyId')]
        if root_ak_ids:
            try:
                r = cam('GetSecurityLastUsed', {'SecretIdList': root_ak_ids[:10]})
                lu_map = {item['SecretId']: item for item in r.get('SecretIdLastUsedRows', [])}
                for ak in root_aks:
                    info = lu_map.get(ak.get('AccessKeyId', ''), {})
                    ak['last_used_date'] = info.get('LastUsedDate', '')
                    ak['last_secret_used_date'] = info.get('LastSecretUsedDate', '')
            except Exception:
                pass
else:
    root_aks = []
    root_aks_scan_status = 'skipped_subuser_caller'


# ── 输出 ──────────────────────────────────────────────────────────────
# principals 内已挂 groups 字段(Phase 5),admin_grants/custom_admin_policies 顶层(Phase 6)
# 2026-05-12: caller_identity 顶层标识调用者身份,*_scan_status 标识每域降级状态
# LLM 在报告 §01/§09 看到 is_root_caller=False 必须按降级模板写,严禁照搬 root_aks/aks/mfa_flags 等值。
output = {
    'caller_identity': caller_identity,
    'principals': cam_data,
    'policies': policies,
    'root_aks': root_aks,
    'root_aks_scan_status': root_aks_scan_status,
    'admin_grants': admin_grants,
    'custom_admin_policies': custom_admin_policies,
    'collaborators_scan_status': collaborators_scan_status,
    'audit_cross_check': {
        'suspect_count': len(suspect_uins),
        'audit_verified_count': len(audit_logins),
        'verified_uins': audit_logins,
        'scan_status': audit_scan_status,
    },
}
json.dump(output, open(os.path.join(OUT_DIR, 'inspect_cam.json'), 'w'), default=str)

# AK 闲置统计
now = datetime.now()
idle_ak_count = 0
for p in cam_data:
    for ak in p.get('aks', []):
        lud = ak.get('last_used_date', '')
        if not lud:
            idle_ak_count += 1
        else:
            try:
                dt = datetime.strptime(lud, '%Y-%m-%d')
                if (now - dt).days > 90:
                    idle_ak_count += 1
            except (ValueError, TypeError):
                pass

print(json.dumps({
    'caller_uin': caller_uin,
    'owner_uin': owner_uin,
    'is_root_caller': is_root_caller,
    'users': len(users),
    'collabs': len(collabs),
    'with_ak': sum(1 for x in cam_data if x['aks']),
    'no_mfa': sum(1 for x in cam_data if x['console_login'] == 1 and not x['mfa_flags']),
    'custom_policies': len(policies),
    'suspect_inactive': len(suspect_uins),
    'audit_verified_active': len(audit_logins),
    'idle_ak_90d': idle_ak_count,
    'root_aks': len(root_aks),
    'root_aks_scan_status': root_aks_scan_status,
    'admin_total': len(admin_grants),
    'custom_admin_policies': len(custom_admin_policies),
    'out_dir': OUT_DIR,
}, ensure_ascii=False))
