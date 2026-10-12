#!/usr/bin/env python3
"""Step 1c · 基线/网络/安全产品/合规 四域自扫(advisor 不覆盖的配置层)。

2026-05-25 新增:advisor 单源覆盖资源类风险(CVM/CDB/EIP/NAT/CLB/...),
本脚本补齐 advisor 不覆盖的 4 类配置层检查:

  - baseline  组织归属 + 安全联系人 + 消息订阅(advisor 不审 Organization/联系人/告警)
  - network   VPC 数量 / 缺 ACL 子网 / NAT 网关 / EIP 散落 / 堡垒机(advisor 粗粒度,缺明细)
  - security  WAF 域名绑定 / DDoS 高防 / CFW 启用(advisor 只标"未开启",不给实例明细)
  - compliance 配置审计合规率(CIS/等保)(advisor 不审 Config 规则评估)

数据库审计不在本脚本查:advisor 已有 MySQL/Redis/PostgreSQL 等 90 个 strategy
部分涉及审计配置,LLM 在 Step 4 从 inspect_advisor_digest.json 推断。

用法:
    python3 scripts/inspect_infra.py

前置:inspect_prepare 已产出 $OUT_DIR/inspect_meta.json,带 out_dir 字段。

输出:
    $OUT_DIR/inspect_infra.json         四域合并结果
    stdout  summary JSON 给 LLM

工程契约:
    1. 复用 sign.py call_api,零外部依赖
    2. 各域独立 scan_status 三态(ok/partial/denied),互不影响
    3. 大结果落盘,stdout 只 print summary
    4. 8 路并发(ThreadPoolExecutor),与 inspect_scan.py 一致
    5. 脚本只采字段不做判定,判定留给 LLM 按 references/17-infra-baseline.md
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    out, offset, limit = [], 0, 100
    while True:
        off, lim = (str(offset), str(limit)) if str_paging else (offset, limit)
        resp = call_api(host=host, service=service, action=action, version=version,
                        payload={**payload, 'Offset': off, 'Limit': lim},
                        region=region, secret_id=sid, secret_key=sk)
        if resp.get('Error'):
            break
        node = resp
        for k in list_key.split('.'):
            node = (node or {}).get(k, []) if isinstance(node, dict) else []
        out.extend(node or [])
        if not node or len(node) < limit:
            break
        offset += limit
    return out


# ============================================================
# 1) baseline — 组织归属 / 安全联系人 / 消息订阅
# ============================================================
baseline = {
    'scan_status': 'ok',
    'organization': {
        'joined': False,
        'org_id': '',
        'org_name': '',
        'member_count': 0,
        'dept_depth': 0,
        'scan_status': 'ok',
    },
    'contacts': {'configured': False, 'count': 0, 'details': []},
    'alarm_notices': {'configured': False, 'count': 0, 'details': []},
}

# 1a) 组织归属
try:
    r = call_api(host='organization.tencentcloudapi.com', service='organization',
                 action='DescribeOrganization', version='2018-12-25',
                 payload={}, region='ap-guangzhou',
                 secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and r.get('Error'):
        code = r['Error'].get('Code', '')
        if 'Denied' in code or 'AuthFailure' in code or 'ResourceNotFound' in code:
            baseline['organization']['scan_status'] = 'denied'
        else:
            baseline['organization']['scan_status'] = 'denied'
        sys.stderr.write(f"[WARN] DescribeOrganization: {r['Error']}\n")
    else:
        org = r.get('OrgInfo') or r
        baseline['organization']['joined'] = True
        baseline['organization']['org_id'] = str(org.get('OrgId', ''))
        baseline['organization']['org_name'] = org.get('OrgName', '') or org.get('NickName', '')

        # 1b) 成员列表 + 部门层级(仅组织已加入时)
        try:
            rm = call_api(host='organization.tencentcloudapi.com', service='organization',
                          action='DescribeOrganizationMembers', version='2018-12-25',
                          payload={'Limit': 100, 'Offset': 0},
                          region='ap-guangzhou', secret_id=sid, secret_key=sk)
            if not rm.get('Error'):
                members = rm.get('Members') or []
                baseline['organization']['member_count'] = rm.get('TotalCount', len(members)) or len(members)
                # 部门层级:取最大 DeptDepth
                depths = [m.get('DeptDepth', 0) for m in members if isinstance(m, dict)]
                baseline['organization']['dept_depth'] = max(depths) if depths else 0
        except Exception as e:
            sys.stderr.write(f"[WARN] DescribeOrganizationMembers: {e}\n")
except Exception as e:
    baseline['organization']['scan_status'] = 'denied'
    sys.stderr.write(f"[WARN] DescribeOrganization exception: {e}\n")

# 1c) 安全联系人
try:
    r = call_api(host='cam.tencentcloudapi.com', service='cam',
                 action='DescribeAccountAttributes', version='2019-01-16',
                 payload={}, region='ap-guangzhou',
                 secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        attrs = r.get('AccountAttributes') or r.get('Data') or []
        if isinstance(attrs, list):
            baseline['contacts']['count'] = len(attrs)
            baseline['contacts']['configured'] = len(attrs) > 0
            baseline['contacts']['details'] = attrs[:20]  # 最多保留 20 条
    else:
        if r.get('Error'):
            sys.stderr.write(f"[WARN] DescribeAccountAttributes: {r['Error']}\n")
except Exception as e:
    sys.stderr.write(f"[WARN] DescribeAccountAttributes exception: {e}\n")

# 1d) 消息订阅(告警通知)
try:
    r = call_api(host='monitor.tencentcloudapi.com', service='monitor',
                 action='DescribeAlarmNotices', version='2018-07-24',
                 payload={'Limit': 50, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        notices = r.get('Notices') or r.get('AlarmNoticeSet') or []
        baseline['alarm_notices']['count'] = len(notices)
        baseline['alarm_notices']['configured'] = len(notices) > 0
        # 只保留关键字段避免过大
        baseline['alarm_notices']['details'] = [
            {'id': n.get('Id', ''), 'name': n.get('Name', ''),
             'type': n.get('NoticeType', ''), 'enabled': n.get('Enabled', True)}
            for n in notices[:20]
        ] if isinstance(notices, list) else []
    else:
        if r.get('Error'):
            sys.stderr.write(f"[WARN] DescribeAlarmNotices: {r['Error']}\n")
except Exception as e:
    sys.stderr.write(f"[WARN] DescribeAlarmNotices exception: {e}\n")

# baseline scan_status 聚合
if baseline['organization']['scan_status'] == 'denied':
    baseline['scan_status'] = 'partial'  # 联系人/消息可能正常


# ============================================================
# 2) network — VPC / Subnet+ACL / NAT / EIP / 堡垒机
# ============================================================
network = {
    'scan_status': 'ok',
    'vpc_summary': {'total': 0, 'by_region': {}},
    'subnets_no_acl': [],
    'nat_gateways': {'total': 0, 'by_region': {}},
    'eip_scattered': [],
    'eip_total': 0,
    'bastion_host': {'deployed': False, 'count': 0, 'instances': []},
    'nat_subnet_ids': set(),  # 内部用,不落盘
}

VPC_HOST = 'vpc.tencentcloudapi.com'
VPC_VER = '2017-03-12'


def scan_network_region(region):
    """单 region 网络资源扫描,返回 (region, result_dict)"""
    result = {
        'vpcs': [],
        'subnets': [],
        'acls': [],
        'nat_gws': [],
        'eips': [],
    }

    # VPC
    try:
        vpcs = paginated(VPC_HOST, 'vpc', 'DescribeVpcs', VPC_VER,
                         {}, region, 'VpcSet', str_paging=True)
        result['vpcs'] = vpcs
    except Exception:
        pass

    # Subnets
    try:
        subnets = paginated(VPC_HOST, 'vpc', 'DescribeSubnets', VPC_VER,
                            {}, region, 'SubnetSet', str_paging=True)
        result['subnets'] = subnets
    except Exception:
        pass

    # Network ACL
    try:
        acls = paginated(VPC_HOST, 'vpc', 'DescribeNetworkAcls', VPC_VER,
                         {}, region, 'NetworkAclSet', str_paging=True)
        result['acls'] = acls
    except Exception:
        pass

    # NAT Gateways
    try:
        nats = paginated(VPC_HOST, 'vpc', 'DescribeNatGateways', VPC_VER,
                         {}, region, 'NatGatewaySet', str_paging=True)
        result['nat_gws'] = nats
    except Exception:
        pass

    # EIP
    try:
        eips = paginated(VPC_HOST, 'vpc', 'DescribeAddresses', VPC_VER,
                         {}, region, 'AddressSet', str_paging=True)
        result['eips'] = eips
    except Exception:
        pass

    return region, result


# 按 region 并发扫
region_results = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(scan_network_region, r): r for r in regions}
    for f in as_completed(futs):
        try:
            region, result = f.result()
            region_results[region] = result
        except Exception as e:
            sys.stderr.write(f"[WARN] scan_network_region: {e}\n")

# 聚合 VPC
for region, result in region_results.items():
    vpcs = result.get('vpcs', [])
    network['vpc_summary']['by_region'][region] = len(vpcs)
    network['vpc_summary']['total'] += len(vpcs)

# 聚合 NAT 网关 + 收集 NAT 关联的子网 ID
nat_subnet_ids = set()
for region, result in region_results.items():
    nats = result.get('nat_gws', [])
    network['nat_gateways']['by_region'][region] = len(nats)
    network['nat_gateways']['total'] += len(nats)
    for nat in nats:
        # NAT 网关关联的子网
        for sn in (nat.get('Subnet') or []):
            sid_ = sn.get('SubnetId', '') if isinstance(sn, dict) else ''
            if sid_:
                nat_subnet_ids.add(sid_)

# 聚合缺 ACL 的子网
for region, result in region_results.items():
    subnets = result.get('subnets', [])
    acls = result.get('acls', [])
    # 收集所有 ACL 关联的子网 ID
    acl_subnet_ids = set()
    for acl in acls:
        for sn in (acl.get('SubnetAssociation') or []):
            sn_id = sn.get('SubnetId', '') if isinstance(sn, dict) else ''
            if sn_id:
                acl_subnet_ids.add(sn_id)
    # 缺 ACL 的子网
    for subnet in subnets:
        subnet_id = subnet.get('SubnetId', '')
        if subnet_id and subnet_id not in acl_subnet_ids:
            network['subnets_no_acl'].append({
                'region': region,
                'subnet_id': subnet_id,
                'name': subnet.get('SubnetName', ''),
                'vpc_id': subnet.get('VpcId', ''),
                'cidr': subnet.get('CidrBlock', ''),
            })

# 聚合 EIP 散落直连
for region, result in region_results.items():
    eips = result.get('eips', [])
    network['eip_total'] += len(eips)
    for eip in eips:
        eip_id = eip.get('AddressId', '')
        address = eip.get('AddressIp', '')
        bound_type = eip.get('InstanceType', '')  # CVM/NAT/ENI/...
        bound_id = eip.get('InstanceId', '')
        # EIP 散落:绑定了 CVM/ENI 但没走 NAT(即绑定资源的子网不在 NAT 子网集合中)
        is_scattered = False
        if bound_type in ('CVM', 'ENI') and bound_id:
            # 查该 EIP 所在子网是否在 NAT 子网集合中
            # EIP 本身不直接带子网信息,取 NetworkInterface 的子网
            # 简化判定:EIP 绑了 CVM/ENI 但未绑定 NAT → 视为散落
            is_scattered = True
        if is_scattered:
            network['eip_scattered'].append({
                'region': region,
                'eip_id': eip_id,
                'address': address,
                'bound_resource': bound_id,
                'bound_type': bound_type,
            })

# 堡垒机(全局 API)
try:
    r = call_api(host='bh.tencentcloudapi.com', service='bh',
                 action='DescribeBhInstances', version='2021-04-10',
                 payload={'Limit': 50, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        instances = r.get('Instances') or r.get('BhInstanceSet') or []
        network['bastion_host']['count'] = len(instances)
        network['bastion_host']['deployed'] = len(instances) > 0
        network['bastion_host']['instances'] = [
            {'id': i.get('InstanceId', ''), 'name': i.get('InstanceName', ''),
             'status': i.get('Status', ''), 'region': i.get('Region', '')}
            for i in instances[:10]
        ] if isinstance(instances, list) else []
    else:
        if r.get('Error'):
            sys.stderr.write(f"[WARN] DescribeBhInstances: {r['Error']}\n")
            # 堡垒机 API 可能未开通,不把整个 network 标 denied
except Exception as e:
    sys.stderr.write(f"[WARN] DescribeBhInstances exception: {e}\n")

# 清理内部字段
if 'nat_subnet_ids' in network:
    del network['nat_subnet_ids']


# ============================================================
# 3) security — WAF / DDoS / CFW
# ============================================================
security = {
    'scan_status': 'ok',
    'waf': {'domains_total': 0, 'domains_bound_lb': [], 'domains_unbound_lb': [],
            'scan_status': 'ok'},
    'ddos': {'bgp_instances': 0, 'instances': [], 'scan_status': 'ok'},
    'cfw': {'enabled': False, 'acl_count': 0, 'scan_status': 'ok'},
    'db_audit_note': '由 LLM 从 advisor finding 推断,脚本不枚举 CDB 实例',
}
security_apis_failed = 0
security_apis_total = 3  # waf + ddos + cfw

# 3a) WAF 域名
try:
    r = call_api(host='waf.tencentcloudapi.com', service='waf',
                 action='DescribeDomains', version='2018-01-25',
                 payload={'Limit': 100, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        domains = r.get('Domains') or r.get('DomainList') or []
        security['waf']['domains_total'] = len(domains) if isinstance(domains, list) else 0
        for d in (domains if isinstance(domains, list) else []):
            item = {
                'domain': d.get('Domain', '') or d.get('DomainId', ''),
                'domain_id': d.get('DomainId', ''),
                'status': d.get('Status', ''),
                'lb_bindings': d.get('ClbList') or d.get('LbList') or [],
            }
            if item.get('lb_bindings'):
                security['waf']['domains_bound_lb'].append(item)
            else:
                security['waf']['domains_unbound_lb'].append(item)
    else:
        security['waf']['scan_status'] = 'denied'
        security_apis_failed += 1
        if r.get('Error'):
            sys.stderr.write(f"[WARN] WAF DescribeDomains: {r['Error']}\n")
except Exception as e:
    security['waf']['scan_status'] = 'denied'
    security_apis_failed += 1
    sys.stderr.write(f"[WARN] WAF DescribeDomains exception: {e}\n")

# 3b) DDoS 高防
try:
    r = call_api(host='antiddos.tencentcloudapi.com', service='antiddos',
                 action='DescribeListBGPInstances', version='2020-03-09',
                 payload={'Limit': 50, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        instances = r.get('InstanceList') or r.get('BGPInstanceList') or []
        security['ddos']['bgp_instances'] = len(instances) if isinstance(instances, list) else 0
        security['ddos']['instances'] = [
            {'id': i.get('InstanceId', ''), 'name': i.get('InstanceName', ''),
             'ip_line': i.get('IpLine', ''), 'status': i.get('Status', '')}
            for i in (instances[:20] if isinstance(instances, list) else [])
        ]
    else:
        security['ddos']['scan_status'] = 'denied'
        security_apis_failed += 1
        if r.get('Error'):
            sys.stderr.write(f"[WARN] DDoS DescribeListBGPInstances: {r['Error']}\n")
except Exception as e:
    security['ddos']['scan_status'] = 'denied'
    security_apis_failed += 1
    sys.stderr.write(f"[WARN] DDoS DescribeListBGPInstances exception: {e}\n")

# 3c) CFW
try:
    r = call_api(host='cfw.tencentcloudapi.com', service='cfw',
                 action='DescribeAcls', version='2019-09-04',
                 payload={'Limit': 100, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and not r.get('Error'):
        acls = r.get('Data') or r.get('AclList') or []
        acl_list = acls if isinstance(acls, list) else []
        security['cfw']['acl_count'] = len(acl_list)
        security['cfw']['enabled'] = len(acl_list) > 0
    else:
        security['cfw']['scan_status'] = 'denied'
        security_apis_failed += 1
        if r.get('Error'):
            sys.stderr.write(f"[WARN] CFW DescribeAcls: {r['Error']}\n")
except Exception as e:
    security['cfw']['scan_status'] = 'denied'
    security_apis_failed += 1
    sys.stderr.write(f"[WARN] CFW DescribeAcls exception: {e}\n")

# security scan_status 聚合
if security_apis_failed == security_apis_total:
    security['scan_status'] = 'denied'
elif security_apis_failed > 0:
    security['scan_status'] = 'partial'


# ============================================================
# 4) compliance — 配置审计合规率
# ============================================================
compliance = {
    'scan_status': 'ok',
    'config_enabled': False,
    'rules_total': 0,
    'rules_by_framework': {'CIS': 0, '等保': 0, 'other': 0},
    'compliance_rate': None,
    'non_compliant_count': 0,
    'compliant_count': 0,
    'total_evaluated': 0,
    'key_missing_rules': [],
    'evaluation_summary': [],
}

CONFIG_HOST = 'config.tencentcloudapi.com'
CONFIG_VER = '2022-06-01'
CIS_KEYWORDS = ['CIS', 'cis', 'CIS Benchmark']
DBP_KEYWORDS = ['等保', 'MLPS', 'mlps', 'ClassifiedProtection']

# 4a) 配置审计规则
try:
    r = call_api(host=CONFIG_HOST, service='config',
                 action='DescribeConfigRules', version=CONFIG_VER,
                 payload={'Limit': 100, 'Offset': 0},
                 region='ap-guangzhou', secret_id=sid, secret_key=sk)
    if isinstance(r, dict) and r.get('Error'):
        code = r.get('Error', {}).get('Code', '')
        if 'ResourceNotFound' in code or 'Denied' in code or 'AuthFailure' in code:
            compliance['scan_status'] = 'denied'
        else:
            compliance['scan_status'] = 'denied'
        sys.stderr.write(f"[WARN] DescribeConfigRules: {r['Error']}\n")
    else:
        rules = r.get('ConfigRules') or r.get('Rules') or []
        rule_list = rules if isinstance(rules, list) else []
        compliance['config_enabled'] = True
        compliance['rules_total'] = len(rule_list)

        # 按 framework 分类
        for rule in rule_list:
            name = rule.get('ConfigRuleName', '') or rule.get('Name', '')
            if any(kw in name for kw in CIS_KEYWORDS):
                compliance['rules_by_framework']['CIS'] += 1
            elif any(kw in name for kw in DBP_KEYWORDS):
                compliance['rules_by_framework']['等保'] += 1
            else:
                compliance['rules_by_framework']['other'] += 1

        # 4b) 合规评估结果
        try:
            re_ = call_api(host=CONFIG_HOST, service='config',
                           action='DescribeConfigRulesEvaluations', version=CONFIG_VER,
                           payload={'Limit': 100, 'Offset': 0},
                           region='ap-guangzhou', secret_id=sid, secret_key=sk)
            if isinstance(re_, dict) and not re_.get('Error'):
                evaluations = re_.get('Evaluations') or re_.get('ConfigRulesEvaluationResults') or []
                eval_list = evaluations if isinstance(evaluations, list) else []

                non_compliant = 0
                compliant = 0
                for ev in eval_list:
                    compliance_type = ev.get('ComplianceType', '') or ev.get('Compliance', '')
                    if 'NON_COMPLIANT' in str(compliance_type).upper() or 'NO' in str(compliance_type).upper():
                        non_compliant += 1
                    else:
                        compliant += 1

                compliance['non_compliant_count'] = non_compliant
                compliance['compliant_count'] = compliant
                compliance['total_evaluated'] = non_compliant + compliant
                if compliance['total_evaluated'] > 0:
                    compliance['compliance_rate'] = round(
                        compliant / compliance['total_evaluated'], 4)

                # 不合规评估摘要(最多 20 条)
                compliance['evaluation_summary'] = [
                    {
                        'rule_name': ev.get('ConfigRuleName', '') or ev.get('Name', ''),
                        'compliance': ev.get('ComplianceType', '') or ev.get('Compliance', ''),
                        'resource_count': ev.get('ResourceCount', 0),
                    }
                    for ev in eval_list[:20]
                ]
        except Exception as e:
            sys.stderr.write(f"[WARN] DescribeConfigRulesEvaluations exception: {e}\n")

except Exception as e:
    compliance['scan_status'] = 'denied'
    sys.stderr.write(f"[WARN] DescribeConfigRules exception: {e}\n")


# ============================================================
# 落盘
# ============================================================
output = {
    'baseline': baseline,
    'network': network,
    'security': security,
    'compliance': compliance,
}
json.dump(output, open(os.path.join(OUT_DIR, 'inspect_infra.json'), 'w'),
          ensure_ascii=False, default=str)


# ============================================================
# summary(stdout 只给 LLM 看这段)
# ============================================================
summary = {
    'baseline': {
        'scan_status': baseline['scan_status'],
        'org_joined': baseline['organization']['joined'],
        'org_scan_status': baseline['organization']['scan_status'],
        'contacts_configured': baseline['contacts']['configured'],
        'alarm_notices_configured': baseline['alarm_notices']['configured'],
    },
    'network': {
        'scan_status': network['scan_status'],
        'vpc_total': network['vpc_summary']['total'],
        'subnets_no_acl': len(network['subnets_no_acl']),
        'nat_total': network['nat_gateways']['total'],
        'eip_total': network['eip_total'],
        'eip_scattered': len(network['eip_scattered']),
        'bastion_deployed': network['bastion_host']['deployed'],
        'bastion_count': network['bastion_host']['count'],
    },
    'security': {
        'scan_status': security['scan_status'],
        'waf_domains': security['waf']['domains_total'],
        'waf_unbound_lb': len(security['waf']['domains_unbound_lb']),
        'waf_scan_status': security['waf']['scan_status'],
        'ddos_bgp': security['ddos']['bgp_instances'],
        'ddos_scan_status': security['ddos']['scan_status'],
        'cfw_enabled': security['cfw']['enabled'],
        'cfw_scan_status': security['cfw']['scan_status'],
    },
    'compliance': {
        'scan_status': compliance['scan_status'],
        'config_enabled': compliance['config_enabled'],
        'rules_total': compliance['rules_total'],
        'compliance_rate': compliance['compliance_rate'],
        'non_compliant': compliance['non_compliant_count'],
        'frameworks': compliance['rules_by_framework'],
    },
}

print(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"[Step1c] out_dir={OUT_DIR}", file=sys.stderr)
