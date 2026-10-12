# 02 网络与安全组巡检

> SOP: TC-SEC-SOP-001 | 章节 02
> 操作路径:VPC > 安全组 → https://console.cloud.tencent.com/vpc/securitygroup

## 2.1 安全组规则审计

**风险等级:🔴 严重**

| 检查项 | 操作方法 | 判定标准 | 异常处置 |
|--------|---------|---------|---------|
| 全端口开放 (0.0.0.0/0) | 入站规则 > ALL 端口 | 严禁对所有 IP 开放全端口 | 删除规则,按需开放 |
| 高危端口对外 | 入站规则筛选 22/3389/3306/6379 等 | 高危端口禁止对外网开放 | 修改为仅内网/跳板机 IP |
| 空安全组 | 安全组 > 关联实例 | 无实例的安全组应清理 | 评估后删除 |

### 高危端口清单

| 端口 | 服务 | 风险 |
|------|------|------|
| 22 | SSH | 暴力破解 |
| 3389 | RDP | 远程桌面暴力破解 |
| 3306 | MySQL | 数据库未授权访问 |
| 6379 | Redis | 未鉴权数据泄露 |
| 27017 | MongoDB | 未鉴权数据泄露 |
| 9200/9300 | Elasticsearch | 未授权访问 |
| 5432 | PostgreSQL | 暴力破解 |
| 11211 | Memcached | DDoS 反射攻击 |
| 2181 | ZooKeeper | 未授权访问 |
| 8080/8443 | 管理后台 | 未授权管理接口 |

### 巡检步骤

> **重要**:安全组是地域级资源,必须遍历所有地域才能全量覆盖。

**执行方法:** 由 `inspect_scan.py` Step 1 全地域并发扫描,落盘 `inspect_sg.json`(字段:`all_port_risks` / `hr_port_risks` / `empty_sgs`)。涉及 API:`cvm.DescribeRegions` + `vpc.DescribeSecurityGroups` + `vpc.DescribeSecurityGroupAssociationStatistics` + `vpc.DescribeSecurityGroupPolicies`。

**判定标准:**

| 风险等级 | 条件 | 处置 |
|---------|------|------|
| 🔴 严重 | 全端口开放 `0.0.0.0/0` 且有关联实例 | 立即删除规则,按需开放 |
| 🔴 严重 | 高危端口(22/3389/3306/6379等)对 `0.0.0.0/0` 开放且有关联实例 | 修改为仅内网/跳板机 IP |
| 🟡 中风险 | 全端口/高危端口开放但无关联实例(空安全组) | 评估后删除规则或清理安全组 |
| 🟡 中风险 | 空安全组(无关联实例) | 评估后删除 |
| ✅ 合规 | 无全端口开放,无高危端口对外,无空安全组 | 无需处置 |

---

## 2.2 VPC 网络隔离

**风险等级:🟠 高**

| 检查项 | 操作方法 | 判定标准 | 异常处置 |
|--------|---------|---------|---------|
| 云联网 | VPC > 云联网 | 检查跨 UIN VPC 网络打通 | 有跨 UIN 打通网络需记录到巡检报告 |
| 对等连接 | VPC > 对等连接 | 检查跨 UIN VPC 网络打通 | 有跨 UIN 打通网络需记录到巡检报告 |

### 巡检步骤

> **2026-05-12 变更**:本段跨 UIN 扫描已**收编进 Step 1 `inspect_scan.py`**(第二波并发抓取),落盘 `$OUT_DIR/inspect_vpc_cross.json`。LLM 写 §02 报告时**直接 Read 该文件**,**禁止**再跑下面"执行方法"里的 `python3 -c` 脚本(历史保留仅作故障应急回退参考)。
>
> 产物结构见 `SKILL.md` schema 速查表;报告列法见 `references/13-report-template.md §02 VPC 网络隔离`。
>
> **历史 Bug 修正记录**(实现时已修,文档保留供查):
> - `DescribeVpcPeeringConnections` 的 `Limit` 上限是 **50**,旧脚本写 `100` 会触发 `InvalidParameterValue.Range`
> - 返回顶层 key 是 `PeerConnectionSet`(不是旧脚本写的 `PeeringConnectionSet`)
> - 跨 UIN 判定字段是 `SourceUin` / `DestinationUin`(不是旧脚本写的 `PeerUin`)
>
> **重要**:云联网和对等连接是地域级资源,必须遍历所有地域。CCN 是全局 API(`ap-guangzhou` 即可),Peering 逐地域。

**判定标准:**

| 风险等级 | 条件 | 处置 |
|---------|------|------|
| ⚠️ 需记录 | 存在跨 UIN 云联网或对等连接 | 记录到巡检报告,评估必要性 |
| ✅ 合规 | 无跨 UIN 网络互通 | 无需处置 |

---

## 2.3 NAT / EIP

| 检查项 | 操作方法 | 判定标准 | 风险等级 |
|--------|---------|---------|---------|
| NAT 高危端口暴露 | NAT 网关 > 端口转发规则 | 检查是否有 22/3389/3306/6379/27017/9200 等高危端口通过 DNAT 暴露公网 | 🔴 严重 |
| 闲置 EIP | 弹性公网 IP 列表 | 未绑定实例的 EIP 及时释放 | 🟡 中 |

### 高危端口清单(与安全组审计一致)

| 端口 | 服务 | 风险 |
|------|------|------|
| 22 | SSH | 暴力破解 |
| 3389 | RDP | 远程桌面暴力破解 |
| 3306 | MySQL | 数据库未授权访问 |
| 6379 | Redis | 未鉴权数据泄露 |
| 27017 | MongoDB | 未鉴权数据泄露 |
| 9200/9300 | Elasticsearch | 未授权访问 |
| 5432 | PostgreSQL | 暴力破解 |
| 11211 | Memcached | DDoS 反射攻击 |
| 2181 | ZooKeeper | 未授权访问 |
| 8080/8443 | 管理后台 | 未授权管理接口 |

### 巡检步骤

> **重要**:NAT 网关和 EIP 是地域级资源,必须遍历所有地域。

**执行方法:** 由 `inspect_scan.py` Step 1 全地域并发扫描,落盘 `inspect_nat_eip.json`(字段:`nat_risks` / `idle_eips`)。涉及 API:`cvm.DescribeRegions` + `vpc.DescribeNatGateways` + `vpc.DescribeNatGatewayDestinationIpPortTranslationNatRules` + `vpc.DescribeAddresses`。

**判定标准:**

| 风险等级 | 条件 | 处置 |
|---------|------|------|
| 🔴 严重 | NAT 端口转发规则中公网端口为高危端口(22/3389/3306/6379/27017/9200等) | 立即删除规则或改为内网访问 |
| 🟡 中 | EIP 未绑定任何实例 | 评估后释放,减少攻击面和费用 |
| ✅ 合规 | 无高危端口 DNAT 暴露,无闲置 EIP | 无需处置 |
