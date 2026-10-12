# 17 基线/网络/安全产品/合规 判定标准

> SOP: TC-SEC-SOP-001 | 章节 17
> 数据源:`scripts/inspect_infra.py` 产出的 `inspect_infra.json`

## 0. 数据源说明

- **inspect_infra.py** (Step 1c) 与 inspect_scan.py / inspect_advisor.py 并行执行
- 4 个域各自独立 `scan_status` 三态(ok / partial / denied),互不影响
- 数据源:organization / cam / monitor / vpc / bh / waf / antiddos / cfw / config 服务 API
- **脚本只采字段不做判定**,判定留给 LLM 按本文档下表
- **数据库审计不在本脚本查**:advisor 已有 MySQL/Redis/PostgreSQL 等 90 个 strategy 部分涉及审计配置,LLM 在 Step 4 从 `inspect_advisor_digest.json` 推断

---

## 1. baseline — 组织/基线检查

### 1.1 组织归属

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 是否加入集团账号 | `baseline.organization.joined` | `joined=False` | 🟡 中(单账号无法统一管控) |
| 组织成员数 | `baseline.organization.member_count` | 仅供参考 | — |
| 部门层级深度 | `baseline.organization.dept_depth` | `dept_depth > 5` 层级过深,管理效率低 | 🟡 中 |
| 组织探测失败 | `baseline.organization.scan_status` | `denied` → 报告写"需主账号或组织管理员权限确认" | ⚠️ 降级 |

**降级规则**:
- `scan_status=denied` 时**不可断言**"未加入集团",报告写"扫描凭证无组织管理权限,无法判定集团归属,需主账号或组织管理员确认"

### 1.2 安全联系人

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 联系人配置 | `baseline.contacts.configured` | `configured=False` | 🔴 严重(安全事件无法触达) |
| 联系人数量 | `baseline.contacts.count` | `count=0` 同上 | 🔴 严重 |

### 1.3 消息订阅

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 告警通知渠道 | `baseline.alarm_notices.configured` | `configured=False` | 🟠 高(告警无法推送) |
| 通知渠道数量 | `baseline.alarm_notices.count` | `count=0` 同上 | 🟠 高 |

---

## 2. network — 网络深度检查

### 2.1 VPC 数量

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| VPC 总数 | `network.vpc_summary.total` | `total > 20` 异常多,可能存在资源浪费或管理混乱 | 🟡 中 |
| 单 region VPC 数 | `network.vpc_summary.by_region[region]` | 单 region > 10 | 🟡 中 |

### 2.2 缺 ACL 的子网

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 无网络 ACL 的子网 | `network.subnets_no_acl` | 列表非空 → 逐条列出 | 🟠 高(子网缺少访问控制层) |

**说明**:`DescribeSubnets` 取全部子网 → `DescribeNetworkAcls` 取全部 ACL → 子网不在任何 ACL 的关联子网列表中 = 缺 ACL。这是网络层面的访问控制缺失,与安全组(SG)互补。

### 2.3 NAT 网关

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| NAT 网关总数 | `network.nat_gateways.total` | 仅供参考 | — |
| 无 NAT 网关 | `network.nat_gateways.total=0` 且 EIP > 0 | 🟡 中(EIP 全散落,无集中出口) |

### 2.4 EIP 散落直连

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| EIP 绑 CVM/ENI 未走 NAT | `network.eip_scattered` | 列表非空 → 逐条列出 | 🟡 中(绕过 NAT,无法集中管控公网出口) |

**判定逻辑**:EIP 绑定了 CVM 或 ENI 但未绑定 NAT 网关 = 散落直连。这些实例的公网流量不经 NAT,无法做集中 SNAT / 流量审计 / 访问控制。

### 2.5 堡垒机

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 堡垒机部署 | `network.bastion_host.deployed` | `deployed=False` | 🟠 高(无集中运维通道,运维安全无法保障) |
| 堡垒机数量 | `network.bastion_host.count` | `count=0` 同上 | 🟠 高 |

---

## 3. security — 安全产品检查

### 3.1 WAF

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| WAF 域名总数 | `security.waf.domains_total` | `domains_total=0` → WAF 未接入任何域名 | 🟠 高 |
| WAF 域名未绑定 LB | `security.waf.domains_unbound_lb` | 列表非空 → WAF 已接入但未绑定 LB,防护不生效 | 🟡 中 |
| WAF 探测失败 | `security.waf.scan_status` | `denied` → 报告写"需 waf 只读权限" | ⚠️ 降级 |

### 3.2 DDoS 高防

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| BGP 高防实例 | `security.ddos.bgp_instances` | `bgp_instances=0` → 未部署 DDoS 高防 | 🟡 中(小规模业务可接受) |
| DDoS 探测失败 | `security.ddos.scan_status` | `denied` → 报告写"需 antiddos 只读权限" | ⚠️ 降级 |

### 3.3 CFW(云防火墙)

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| CFW 启用 | `security.cfw.enabled` | `enabled=False` | 🟠 高(互联网边界/VPC 间无防火墙) |
| CFW ACL 规则数 | `security.cfw.acl_count` | `acl_count=0` 同上 | 🟠 高 |
| CFW 探测失败 | `security.cfw.scan_status` | `denied` → 报告写"需 cfw 只读权限" | ⚠️ 降级 |

### 3.4 数据库审计(LLM 从 advisor 推断)

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 数据库审计开启率 | advisor digest 中数据库类 finding | 涉及审计的 finding(如"数据库审计未开启")命中 → 🟠 高 | 🟠 高 |
| 审计日志保留 | 同上 | 保留天数 < 90 → 🟡 中 | 🟡 中 |

**说明**:advisor 已有 MySQL/Redis/PostgreSQL 等 90 个 strategy,部分涉及审计配置(如 strategy 3 MySQL root 安全、strategy 4 MySQL 高危命令等)。LLM 在 Step 4 从 `inspect_advisor_digest.json` 的数据库相关 finding 中查找审计关键词,推断审计状态。**不需要额外调 API**。

---

## 4. compliance — 配置审计合规率

### 4.1 配置审计启用

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 配置审计开通 | `compliance.config_enabled` | `config_enabled=False` → 未开通配置审计 | 🟠 高 |
| 配置审计探测失败 | `compliance.scan_status` | `denied` → 未开通或无权限 | 🟠 高 |

### 4.2 合规率

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| 合规率 | `compliance.compliance_rate` | `rate < 0.6` → 🔴 严重 / `0.6 ≤ rate < 0.8` → 🟠 高 / `rate ≥ 0.8` → 🟢 合规 | 🔴/🟠/🟢 |
| 不合规资源数 | `compliance.non_compliant_count` | 逐条列出 evaluation_summary | 按合规率分档 |
| 合规率为空 | `compliance.compliance_rate is None` | 无法计算,需手工核查 | ⚠️ 降级 |

### 4.3 规则框架覆盖

| 检查项 | 数据路径 | 判定 | 风险等级 |
|---|---|---|---|
| CIS 规则数 | `compliance.rules_by_framework.CIS` | `CIS=0` → 缺 CIS 基线规则 | 🟡 中 |
| 等保规则数 | `compliance.rules_by_framework.等保` | `等保=0` → 缺等保规则 | 🟡 中 |

---

## 5. scan_status 三态语义(各域独立)

| 值 | 含义 | 下游报告处理 |
|---|---|---|
| `ok` | 该域全部 API 调用成功 | 正常出表 |
| `partial` | 该域部分 API 调用失败 | 出能拉到的部分 + 标"⚠️ 部分数据获取失败" |
| `denied` | 该域全部 API 调用失败(权限不足或产品未开通) | 写"⚠️ 权限不足(需 XX 只读策略),建议加只读策略后复跑" |

---

## 6. 报告渲染位置

| 域 | 报告大类 | 小节标题 |
|---|---|---|
| baseline(联系人/消息订阅) | 合规与资产标签 | #### 基线配置检查 |
| baseline(组织归属) | 合规与资产标签 | #### 基线配置检查(组织归属子项) |
| network(VPC/ACL/NAT/EIP/堡垒机) | 云安全 | #### 网络基础设施 |
| security(WAF/DDoS/CFW) | 云安全 | #### 安全产品覆盖 |
| security(数据库审计) | 云安全 | #### 安全产品覆盖(数据库审计子项,advisor 推断) |
| compliance(配置审计) | 合规与资产标签 | #### 配置审计合规率 |

**报告格式**:与现有护城河小节一致,统一 7 列风险表:
`| 严重度 | 资源 ID | 名称 | 地域 | 风险描述 | 修复 |`
