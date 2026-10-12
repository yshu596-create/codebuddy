#!/usr/bin/env python3
"""Step 1a:护城河自扫(advisor 无同等粒度的 6 类)。

2026-05-21 重构(advisor 单源 + 自扫补丁方案):
  advisor 兜底所有资源类(CVM/CBS/EIP/NAT/CLB/CDB/Redis/Mongo/ES/CKafka/TKE/LH 等),
  本脚本只保留 advisor 无法替代的 6 类:
    - SG    安全组聚合(全端口/高危端口/空规则,advisor 只给 1 个粗粒度 strategy)
    - vpc_cross  跨 UIN 对等/CCN(advisor 只看本账号 VPC)
    - cwp   CWP 主机安全(advisor 只标"未防护",缺暴破成功明细 root@IP/SUCCESS)
    - cloudaudit 跟踪集配置(advisor 不审 CloudAudit)
    - ssl   SSL 证书过期分级(advisor 仅 1 个粗粒度)
  CAM / 资源标签 / 审计事件流分别由独立脚本 inspect_cam.py /
  Step 2.6 prompt / inspect_audit_events.py 负责。

用法:
    python3 scripts/inspect_scan.py

前置:inspect_prepare 已产出 $OUT_DIR/inspect_meta.json,带 out_dir 字段。

输出:
    $OUT_DIR/inspect_sg_rules.json       SG 聚合按地域
    $OUT_DIR/inspect_vpc_cross.json      跨 UIN 关联
    $OUT_DIR/inspect_cwp.json            CWP 主机安全
    $OUT_DIR/inspect_cloudaudit.json     CloudAudit 跟踪集
    $OUT_DIR/inspect_ssl.json            SSL 证书(全局)
    $OUT_DIR/inspect_errors.json         降级项
    stdout  summary JSON 给 LLM

OUT_DIR 解析:
  1. $INSPECT_OUT_DIR
  2. /tmp/inspect_$INSPECTION_UIN/
  其余兜底/裸 /tmp 已禁,2026-05-11 事故根因。
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

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
meta = json.load(open(os.path.join(OUT_DIR, 'inspect_meta.json')))
regions = meta['regions']
sid = os.environ['TENCENTCLOUD_SECRET_ID']
sk = os.environ['TENCENTCLOUD_SECRET_KEY']


def paginated(host, service, action, version, payload, region, list_key,
              str_paging=False):
    # str_paging=True 时 Offset/Limit 传 string。
    # 大多数腾讯云 API 字段定义为 int64,传 string 被严校验拒掉;
    # 但 vpc DescribeSecurityGroups 反过来要求 string,传 int 静默返回空 list
    # → errors=0 而数据全丢(2026-05-11 一中心管控账户 21 SG 漏扫)。
    out, offset, limit = [], 0, 100
    while True:
        off, lim = (str(offset), str(limit)) if str_paging else (offset, limit)
        resp = call_api(host=host, service=service, action=action, version=version,
                        payload={**payload, 'Offset': off, 'Limit': lim},
                        region=region, secret_id=sid, secret_key=sk)
        node = resp
        for k in list_key.split('.'):
            node = (node or {}).get(k, []) if isinstance(node, dict) else []
        out.extend(node or [])
        if not node or len(node) < limit:
            break
        offset += limit
    return out


errors = []
HIGH_RISK_PORTS = {
    '22': 'SSH', '3389': 'RDP', '3306': 'MySQL', '6379': 'Redis', '27017': 'MongoDB',
    '9200': 'Elasticsearch', '9300': 'Elasticsearch', '5432': 'PostgreSQL',
    '11211': 'Memcached', '2181': 'ZooKeeper', '8080': 'Web', '8443': 'Web',
}


# ============================================================
# 1) SG 安全组聚合(advisor strategy 170 只给"开放公网/内网"1 项,
#    不分桶全端口/高危端口/空规则,运维需要原始 4 类聚合)
# ============================================================
def scan_sg(region):
    sgs = paginated('vpc.tencentcloudapi.com', 'vpc', 'DescribeSecurityGroups', '2017-03-12',
                    {}, region, 'SecurityGroupSet', str_paging=True)
    assoc_map = {}
    for i in range(0, len(sgs), 20):
        batch = [sg['SecurityGroupId'] for sg in sgs[i:i + 20]]
        try:
            r = call_api(host='vpc.tencentcloudapi.com', service='vpc',
                         action='DescribeSecurityGroupAssociationStatistics', version='2017-03-12',
                         payload={'SecurityGroupIds': batch}, region=region,
                         secret_id=sid, secret_key=sk)
            for s in r.get('SecurityGroupAssociationStatisticsSet', []):
                assoc_map[s['SecurityGroupId']] = s.get('TotalCount', 0)
        except Exception:
            pass
    risks_all, risks_hr, empty = [], [], []
    for sg in sgs:
        sg_id = sg['SecurityGroupId']
        ac = assoc_map.get(sg_id, -1)
        if ac == 0:
            empty.append({'region': region, 'id': sg_id, 'name': sg.get('SecurityGroupName', '')})
        try:
            r = call_api(host='vpc.tencentcloudapi.com', service='vpc',
                         action='DescribeSecurityGroupPolicies', version='2017-03-12',
                         payload={'SecurityGroupId': sg_id}, region=region,
                         secret_id=sid, secret_key=sk)
            for rule in r.get('SecurityGroupPolicySet', {}).get('Ingress', []):
                if rule.get('Action', '').upper() != 'ACCEPT':
                    continue
                cidr = rule.get('CidrBlock', '') or rule.get('Ipv6CidrBlock', '')
                if cidr not in ('0.0.0.0/0', '::/0'):
                    continue
                port = str(rule.get('Port', 'ALL'))
                proto = rule.get('Protocol', 'ALL').upper()
                if port.upper() == 'ALL' and proto in ('ALL', 'TCP', 'UDP'):
                    risks_all.append({'region': region, 'sg': sg_id,
                                      'name': sg.get('SecurityGroupName', ''),
                                      'proto': proto, 'assoc': ac})
                else:
                    for p in port.split(','):
                        p = p.strip()
                        if p in HIGH_RISK_PORTS:
                            risks_hr.append({'region': region, 'sg': sg_id, 'port': p,
                                             'service': HIGH_RISK_PORTS[p], 'assoc': ac})
        except Exception:
            pass
    return {'total_sgs': len(sgs), 'all_port_risks': risks_all,
            'hr_port_risks': risks_hr, 'empty_sgs': empty}


sg_rules = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(scan_sg, region): region for region in regions}
    for f in as_completed(futs):
        region = futs[f]
        try:
            sg_rules[region] = f.result()
        except Exception as e:
            errors.append({'kind': 'sg', 'key': 'sg_rules', 'region': region, 'err': str(e)[:200]})


# ============================================================
# 2) VPC 跨 UIN 关联(advisor 只看本账号 VPC,不审跨账号互通)
# ============================================================
own_uin_for_cross = str(meta.get('owner_uin') or meta.get('uin') or '')
vpc_cross = {'ccn_cross_uin': [], 'peer_cross_uin': [],
             'ccn_total': 0, 'peer_total': 0, 'own_uin': own_uin_for_cross,
             'scan_status': 'ok' if own_uin_for_cross else 'skipped_no_own_uin'}


def fetch_ccn_attached(ccn_id, ccn_name):
    r = call_api(host='vpc.tencentcloudapi.com', service='vpc',
                 action='DescribeCcnAttachedInstances', version='2017-03-12',
                 payload={'CcnId': ccn_id, 'Limit': 100}, region='ap-guangzhou',
                 secret_id=sid, secret_key=sk)
    if r.get('Error'):
        return ('ccn_err', ccn_id, ccn_name, r['Error'])
    out = []
    for inst in (r.get('InstanceSet') or []):
        iuin = str(inst.get('InstanceUin', ''))
        if iuin and own_uin_for_cross and iuin != own_uin_for_cross:
            out.append({
                'ccn_id': ccn_id, 'ccn_name': ccn_name,
                'inst_type': inst.get('InstanceType'),
                'inst_id': inst.get('InstanceId'),
                'inst_region': inst.get('InstanceRegion'),
                'inst_uin': iuin,
            })
    return ('ccn_ok', ccn_id, ccn_name, out)


def fetch_peer_region(region):
    r = call_api(host='vpc.tencentcloudapi.com', service='vpc',
                 action='DescribeVpcPeeringConnections', version='2017-03-12',
                 payload={'Limit': 50}, region=region,
                 secret_id=sid, secret_key=sk)
    if r.get('Error'):
        return ('peer_err', region, r['Error'])
    peers = r.get('PeerConnectionSet') or []
    cross = []
    for p in peers:
        src = str(p.get('SourceUin', ''))
        dst = str(p.get('DestinationUin', ''))
        is_cross = False
        if own_uin_for_cross:
            if src and src != own_uin_for_cross:
                is_cross = True
            if dst and dst != own_uin_for_cross:
                is_cross = True
        if is_cross:
            cross.append({
                'region': region,
                'peer_id': p.get('PeeringConnectionId'),
                'peer_name': p.get('PeeringConnectionName'),
                'src_vpc': p.get('SourceVpcId'),
                'src_uin': src, 'src_region': p.get('SourceRegion'),
                'dst_vpc': p.get('DestinationVpcId') or p.get('PeerVpcId'),
                'dst_uin': dst, 'dst_region': p.get('DestinationRegion'),
                'state': p.get('State'),
            })
    return ('peer_ok', region, len(peers), cross)


if own_uin_for_cross:
    r_ccn = call_api(host='vpc.tencentcloudapi.com', service='vpc',
                     action='DescribeCcns', version='2017-03-12',
                     payload={'Limit': 100}, region='ap-guangzhou',
                     secret_id=sid, secret_key=sk)
    if r_ccn.get('Error'):
        errors.append({'kind': 'vpc_cross', 'key': 'DescribeCcns',
                       'region': 'ap-guangzhou', 'err': str(r_ccn['Error'])[:200]})
        vpc_cross['scan_status'] = 'denied'
    else:
        ccns = r_ccn.get('CcnSet') or []
        vpc_cross['ccn_total'] = len(ccns)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {}
            for ccn in ccns:
                futs[ex.submit(fetch_ccn_attached, ccn['CcnId'], ccn.get('CcnName', ''))] = ('ccn', ccn['CcnId'])
            for region in regions:
                futs[ex.submit(fetch_peer_region, region)] = ('peer', region)
            peer_total = 0
            for f in as_completed(futs):
                try:
                    res = f.result()
                    if res[0] == 'ccn_ok':
                        vpc_cross['ccn_cross_uin'].extend(res[3])
                    elif res[0] == 'ccn_err':
                        errors.append({'kind': 'vpc_cross', 'key': res[1],
                                       'region': '-', 'err': str(res[3])[:200]})
                    elif res[0] == 'peer_ok':
                        peer_total += res[2]
                        vpc_cross['peer_cross_uin'].extend(res[3])
                    else:
                        errors.append({'kind': 'vpc_cross', 'key': 'Peering',
                                       'region': res[1], 'err': str(res[2])[:200]})
                except Exception as e:
                    errors.append({'kind': 'vpc_cross', 'key': '?',
                                   'region': '?', 'err': str(e)[:200]})
            vpc_cross['peer_total'] = peer_total


# ============================================================
# 3) CWP 主机安全(advisor 只标"未防护",缺暴破成功明细等真入侵证据)
# ============================================================
CWP_HOST = 'cwp.tencentcloudapi.com'
CWP_VER  = '2018-02-28'
cwp = {
    'machine_general': {}, 'machines_by_region': {},
    'brute_attack': [], 'host_login': [],
    'emergency_vul': [], 'vul_summary': {},
    'scan_status': 'ok',
}


def _cwp_call(action, payload, region='ap-guangzhou'):
    return call_api(host=CWP_HOST, service='cwp', action=action, version=CWP_VER,
                    payload=payload, region=region, secret_id=sid, secret_key=sk)


def fetch_cwp_machines(region):
    all_ms = []
    offset = 0
    while True:
        r = _cwp_call('DescribeMachines',
                      {'MachineType': 'CVM', 'MachineRegion': region,
                       'Limit': 100, 'Offset': offset})
        if r.get('Error'):
            return ('err', region, r['Error'])
        ms = r.get('Machines') or []
        total = r.get('TotalCount', 0)
        all_ms.extend(ms)
        if len(all_ms) >= total or not ms:
            break
        offset += len(ms)
        if offset > 1000:
            break
    return ('ok', region, all_ms)


r = _cwp_call('DescribeMachineGeneral', {})
if r.get('Error'):
    errors.append({'kind': 'cwp', 'key': 'DescribeMachineGeneral', 'region': '-',
                   'err': str(r['Error'])[:200]})
    cwp['scan_status'] = 'denied'
else:
    cwp['machine_general'] = {k: v for k, v in r.items()
                              if k != 'RequestId' and not isinstance(v, dict)}

if cwp['scan_status'] == 'ok':
    r = _cwp_call('DescribeBruteAttackList', {'Limit': 100})
    if not r.get('Error'):
        cwp['brute_attack'] = r.get('BruteAttackList') or []
        cwp['brute_total_all'] = r.get('TotalCount', 0)
    else:
        errors.append({'kind': 'cwp', 'key': 'DescribeBruteAttackList',
                       'region': '-', 'err': str(r['Error'])[:200]})

    r = _cwp_call('DescribeHostLoginList', {'Limit': 100})
    if not r.get('Error'):
        cwp['host_login'] = r.get('HostLoginList') or []
        cwp['login_total_all'] = r.get('TotalCount', 0)
    else:
        errors.append({'kind': 'cwp', 'key': 'DescribeHostLoginList',
                       'region': '-', 'err': str(r['Error'])[:200]})

    r = _cwp_call('DescribeEmergencyVulList', {'Limit': 50})
    if not r.get('Error'):
        cwp['emergency_vul'] = {
            'List': r.get('List') or [],
            'ExistsRisk': r.get('ExistsRisk'),
            'TotalCount': r.get('TotalCount'),
        }
    else:
        errors.append({'kind': 'cwp', 'key': 'DescribeEmergencyVulList',
                       'region': '-', 'err': str(r['Error'])[:200]})

    r = _cwp_call('DescribeVulHostCountScanTime', {})
    if not r.get('Error'):
        cwp['vul_summary'] = {k: v for k, v in r.items() if k != 'RequestId'}
    else:
        errors.append({'kind': 'cwp', 'key': 'DescribeVulHostCountScanTime',
                       'region': '-', 'err': str(r['Error'])[:200]})

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_cwp_machines, region): region for region in regions}
        for f in as_completed(futs):
            region = futs[f]
            try:
                res = f.result()
                if res[0] == 'ok':
                    if res[2]:
                        cwp['machines_by_region'][region] = res[2]
                else:
                    errors.append({'kind': 'cwp', 'key': 'DescribeMachines',
                                   'region': region, 'err': str(res[2])[:200]})
            except Exception as e:
                errors.append({'kind': 'cwp', 'key': 'DescribeMachines',
                               'region': region, 'err': str(e)[:200]})


# ============================================================
# 4) CloudAudit 跟踪集(配置层,advisor 不审)
# 双接口拉 + caller_identity 三态降级,逻辑保持不变
# ============================================================
CA_HOST = 'cloudaudit.tencentcloudapi.com'
CA_VER  = '2019-03-19'
cloudaudit = {
    'audits': [], 'tracks': [],
    'audits_total_all': 0, 'tracks_total_all': 0,
    'caller_uin': '', 'owner_uin': '',
    'is_root_caller': None,
    'scan_status': 'ok',
}

try:
    me = call_api(host='cam.tencentcloudapi.com', service='cam',
                  action='GetUserAppId', version='2019-01-16',
                  payload={}, region='ap-guangzhou',
                  secret_id=sid, secret_key=sk)
    if isinstance(me, dict) and not me.get('Error'):
        cloudaudit['caller_uin'] = str(me.get('Uin', ''))
        cloudaudit['owner_uin'] = str(me.get('OwnerUin', ''))
        cloudaudit['is_root_caller'] = (
            bool(cloudaudit['caller_uin']) and
            cloudaudit['caller_uin'] == cloudaudit['owner_uin']
        )
        if not cloudaudit['is_root_caller']:
            cloudaudit['scan_status'] = 'caller_subuser'
    else:
        cloudaudit['scan_status'] = 'caller_subuser'
        errors.append({'kind': 'cloudaudit', 'key': 'GetUserAppId',
                       'region': '-', 'err': str(me.get('Error'))[:200] if isinstance(me, dict) else 'unknown'})
except Exception as e:
    cloudaudit['scan_status'] = 'caller_subuser'
    errors.append({'kind': 'cloudaudit', 'key': 'GetUserAppId',
                   'region': '-', 'err': str(e)[:200]})

try:
    r = call_api(host=CA_HOST, service='cloudaudit', action='ListAudits',
                 version=CA_VER, payload={}, region='ap-guangzhou',
                 secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and r.get('Error'):
        code = r['Error'].get('Code', '')
        if 'CamNoAuth' in code or 'Unauthorized' in code:
            cloudaudit['scan_status'] = 'denied'
        errors.append({'kind': 'cloudaudit', 'key': 'ListAudits',
                       'region': 'ap-guangzhou', 'err': str(r['Error'])[:200]})
    else:
        cloudaudit['audits'] = r.get('AuditSummarys') or []
        cloudaudit['audits_total_all'] = len(cloudaudit['audits'])
except Exception as e:
    errors.append({'kind': 'cloudaudit', 'key': 'ListAudits',
                   'region': 'ap-guangzhou', 'err': str(e)[:200]})

try:
    r = call_api(host=CA_HOST, service='cloudaudit', action='DescribeAuditTracks',
                 version=CA_VER, payload={'PageNumber': 1, 'PageSize': 50},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and r.get('Error'):
        code = r['Error'].get('Code', '')
        if 'CamNoAuth' in code or 'Unauthorized' in code:
            cloudaudit['scan_status'] = 'denied'
        errors.append({'kind': 'cloudaudit', 'key': 'DescribeAuditTracks',
                       'region': 'ap-guangzhou', 'err': str(r['Error'])[:200]})
    else:
        cloudaudit['tracks'] = r.get('Tracks') or []
        cloudaudit['tracks_total_all'] = r.get('TotalCount', 0)
except Exception as e:
    errors.append({'kind': 'cloudaudit', 'key': 'DescribeAuditTracks',
                   'region': 'ap-guangzhou', 'err': str(e)[:200]})


# ============================================================
# 5) SSL 证书(全局,advisor 仅 1 个粗粒度 strategy 不给到期天数)
# ============================================================
ssl_certs = []
try:
    ssl_certs = paginated('ssl.tencentcloudapi.com', 'ssl', 'DescribeCertificates',
                          '2019-12-05', {}, 'ap-shanghai', 'Certificates')
except Exception as e:
    errors.append({'kind': 'ssl', 'key': 'DescribeCertificates',
                   'region': 'ap-shanghai', 'err': str(e)[:200]})

today = date.today()
for cert in ssl_certs:
    end = cert.get('CertEndTime') or ''
    try:
        d = datetime.strptime(end[:10], '%Y-%m-%d').date()
        cert['days_to_expire'] = (d - today).days
    except Exception:
        cert['days_to_expire'] = None


# ============================================================
# 落盘
# ============================================================
json.dump(sg_rules,    open(os.path.join(OUT_DIR, 'inspect_sg_rules.json'), 'w'), default=str)
json.dump(vpc_cross,   open(os.path.join(OUT_DIR, 'inspect_vpc_cross.json'), 'w'), default=str)
json.dump(cwp,         open(os.path.join(OUT_DIR, 'inspect_cwp.json'), 'w'), default=str)
json.dump(cloudaudit,  open(os.path.join(OUT_DIR, 'inspect_cloudaudit.json'), 'w'), default=str)
json.dump(ssl_certs,   open(os.path.join(OUT_DIR, 'inspect_ssl.json'), 'w'), default=str)
json.dump(errors,      open(os.path.join(OUT_DIR, 'inspect_errors.json'), 'w'))


# ============================================================
# summary(stdout 只给 LLM 看这段,5 大块)
# ============================================================
summary = {}

# 5a) sg_rules 聚合
summary['sg_rules'] = {
    'total_sgs':      sum(v.get('total_sgs', 0) for v in sg_rules.values()),
    'all_port_open':  sum(len(v.get('all_port_risks', [])) for v in sg_rules.values()),
    'high_risk_port': sum(len(v.get('hr_port_risks', [])) for v in sg_rules.values()),
    'empty_sg':       sum(len(v.get('empty_sgs', [])) for v in sg_rules.values()),
}

# 5b) vpc_cross 聚合
summary['vpc_cross'] = {
    'ccn_total':         vpc_cross['ccn_total'],
    'ccn_cross_uin':     len(vpc_cross['ccn_cross_uin']),
    'peer_total':        vpc_cross['peer_total'],
    'peer_cross_uin':    len(vpc_cross['peer_cross_uin']),
    'scan_status':       vpc_cross['scan_status'],
}

# 5c) cwp 聚合
mg = cwp.get('machine_general') or {}
all_machines = [m for lst in cwp.get('machines_by_region', {}).values() for m in lst]
agent_online      = sum(1 for m in all_machines if m.get('AgentStatus') == 'ONLINE')
agent_offline     = sum(1 for m in all_machines if m.get('AgentStatus') == 'OFFLINE')
agent_uninstalled = sum(1 for m in all_machines if m.get('AgentStatus') == 'UNINSTALLED')
brute = cwp.get('brute_attack') or []
brute_success = sum(1 for b in brute if str(b.get('Status', '')).upper() == 'SUCCESS')
host_login = cwp.get('host_login') or []
login_risk = sum(1 for l in host_login
                 if (l.get('RiskLevel') or 0) > 0
                 or l.get('IsRiskArea') == 1
                 or l.get('IsRiskUser') == 1
                 or l.get('IsRiskTime') == 1
                 or l.get('IsRiskSrcIp') == 1)
vul_sum = cwp.get('vul_summary') or {}
ev = cwp.get('emergency_vul') or {}
summary['cwp'] = {
    'total_machines':       mg.get('MachineCnt', 0),
    'protect_machines':     mg.get('ProtectMachineCnt', 0),
    'not_protect_machines': mg.get('NotProtectMachineCnt', 0),
    'risk_machines':        mg.get('RiskMachineCnt', 0),
    'agent_online':         agent_online,
    'agent_offline':        agent_offline,
    'agent_uninstalled':    agent_uninstalled,
    'brute_sampled':        len(brute),
    'brute_total_all':      cwp.get('brute_total_all', 0),
    'brute_success':        brute_success,
    'login_sampled':        len(host_login),
    'login_total_all':      cwp.get('login_total_all', 0),
    'login_risk':           login_risk,
    'emergency_vul_exists_risk': ev.get('ExistsRisk'),
    'emergency_vul_total':       ev.get('TotalCount'),
    'vul_host_count':       vul_sum.get('VulHostCount'),
    'vul_total_count':      vul_sum.get('TotalVulCount'),
    'vul_if_first_scan':    vul_sum.get('IfFirstScan'),
    'scan_status':          cwp['scan_status'],
}

# 5d) ssl 聚合
summary['ssl'] = {
    'total':         len(ssl_certs),
    'expired':       sum(1 for c in ssl_certs if (c.get('days_to_expire') or 0) < 0),
    'expiring_30d':  sum(1 for c in ssl_certs if 0 <= (c.get('days_to_expire') or 999) <= 30),
}

# 5e) cloudaudit 聚合(三态)
summary['cloudaudit'] = {
    'audits_total':           cloudaudit['audits_total_all'],
    'tracks_total':           cloudaudit['tracks_total_all'],
    'any_track_configured':   bool(cloudaudit['audits']) or cloudaudit['tracks_total_all'] > 0,
    'caller_uin':             cloudaudit['caller_uin'],
    'owner_uin':              cloudaudit['owner_uin'],
    'is_root_caller':         cloudaudit['is_root_caller'],
    'scan_status':            cloudaudit['scan_status'],
}

print(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"[Step1a] errors={len(errors)} out_dir={OUT_DIR} (详情 {OUT_DIR}/inspect_errors.json;errors 非空 ≠ 失败,只要 summary 有数就继续)", file=sys.stderr)
