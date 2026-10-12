# 03 CVM 主机安全巡检

> SOP: TC-SEC-SOP-001 | 章节 03
> 操作路径：主机安全 → https://console.cloud.tencent.com/cwp

---

## 巡检标准

| 巡检项 | 检查方法 | 期望结果 | 风险等级（异常时） |
|--------|---------|---------|-------------------|
| Agent 在线状态 | 控制台 → 主机列表，确认所有 CVM 的 Agent 状态为"在线" | 全部在线 | 🔴 严重 |
| Agent 版本是否最新 | 主机列表查看 Agent 版本号 | 版本为最新 | 🟡 中危 |
| 异常登录事件 | 控制台 → 入侵检测 → 异常登录，检查近7天/30天异常登录记录 | 无未处理异常登录 | 🔴 严重 |
| 密码破解事件 | 控制台 → 入侵检测 → 密码破解，检查近7天/30天暴力破解记录 | 无持续暴力破解 | 🔴 严重 |
| 应急漏洞通告 | 控制台 → 漏洞管理 → 应急漏洞，检查是否有未处理的高危漏洞推送 | 所有推送漏洞已确认/修复 | 🔴 严重 |

---

## ⚠️ 前置依赖关系（关键逻辑）

**巡检项 1（Agent 在线状态）是后续所有巡检项的前置条件。**

```
巡检项 1: Agent 在线状态
    ├─ 全部在线 ✅ → 继续巡检项 2~5
    ├─ 部分在线 → 对在线实例继续巡检项 2~5，未安装/离线的单独列出
    └─ 全部未安装/离线 🔴 → 巡检项 2~5 直接标记为「⛔ 因 Agent 未安装，无法检测」
                              不再调用后续 API，避免产生误导性结果
```

**报告输出规则：**
- 当所有 CVM 均未安装 Agent 时，报告中巡检项 2~5 统一输出：
  `⛔ 因 Agent 未安装，该项无法检测。需先完成 Agent 部署后重新巡检。`
- 不应输出 API 返回的"0 条记录"等结果，因为这不代表安全，只是没有数据来源

---

## 自动化巡检步骤

### 步骤 1：CVM 实例清单采集

遍历所有可用地域,获取全量 CVM 实例(由 `inspect_scan.py` 调 `cvm.DescribeInstances`,字段 `Offset/Limit=100/Region`)。

采集字段：
- InstanceId / InstanceName / Region
- OsName（操作系统）
- PrivateIpAddresses / PublicIpAddresses
- LoginSettings（登录方式：密码/密钥）
- SecurityGroupIds
- InstanceState

### 步骤 2：云镜 Agent 状态（巡检项 1）

> **2026-05-12 变更**:本文档所有 CWP 相关步骤(2~6)已**收编进 Step 1 `inspect_scan.py`**,落盘 `$OUT_DIR/inspect_cwp.json`。LLM 写 §03 报告时**直接 Read 该文件**,**禁止**再跑下面的 `cwp.*` 命令(历史保留仅作故障应急)。
>
> 产物结构见 `SKILL.md` schema 速查表;报告列法见 `references/13-report-template.md §03 CVM 主机安全`。
>
> **历史 Bug 修正记录**(实现时已修,文档保留供查):
> - `DescribeNonlocalLoginPlaces` **已被腾讯云废弃**(所有账户返 `ActionOffline: 该接口已废弃`),改用 `DescribeHostLoginList`(返 HostLoginList[] 含 RiskLevel/IsRiskArea/IsRiskUser/IsRiskTime/IsRiskSrcIp 五个风险维度)
> - `DescribeMachineList` **必须传 `MachineRegion` 且只能一个地域**,不能用 `all`;旧命令 `cwp.DescribeMachineList`(`Offset=0,Limit=100,MachineType=CVM`)直接报 `MissingParameter`
> - 实现里用**新接口 `DescribeMachines`** 替代(字段比 MachineList 多 AgentStatus/AgentVersion,更适合判定 Agent 在线状态),按 regions 并发
> - `DescribeMachineGeneral` 全局 API `ap-guangzhou` 一次搞定,字段含 ProtectMachineCnt/NotProtectMachineCnt/RiskMachineCnt

---

## 报告输出格式

### 场景 A：Agent 全部未安装

```markdown
### CVM 主机安全

CVM 总数：{N} 台

| 巡检项 | 期望结果 | 实际结果 | 风险 |
|--------|---------|---------|------|
| Agent 在线状态 | 全部在线 | ❌ 全部 {N}/{N} 台未安装 Agent | 🔴 严重 |
| Agent 版本 | 最新版本 | ⛔ 因 Agent 未安装，无法检测 | 🔴 |
| 异常登录事件 | 无未处理 | ⛔ 因 Agent 未安装，无法检测 | 🔴 |
| 密码破解事件 | 无持续破解 | ⛔ 因 Agent 未安装，无法检测 | 🔴 |
| 应急漏洞通告 | 全部已修复 | ⛔ 因 Agent 未安装，无法检测 | 🔴 |

> ⚠️ 所有 CVM 均未安装主机安全 Agent，后续 4 项检测无数据来源，
> 需先完成 Agent 部署后重新巡检。

--- 未安装 Agent 实例明细 ---

| 地域 | 实例 ID | 实例名 | 操作系统 | 内网 IP |
|------|---------|--------|---------|---------|
| ... | ... | ... | ... | ... |
```

### 场景 B：Agent 已安装（全部或部分）

```markdown
### CVM 主机安全

CVM 总数：{N} 台

| 巡检项 | 期望结果 | 实际结果 | 风险 |
|--------|---------|---------|------|
| Agent 在线状态 | 全部在线 | 在线 {N} / 离线 {N} / 未安装 {N} | ✅ / 🔴 |
| Agent 版本 | 最新版本 | {版本分布} | ✅ / 🟡 |
| 异常登录事件 | 无未处理 | {N} 条，待处理 {N} 条 | ✅ / 🔴 |
| 密码破解事件 | 无持续破解 | {N} 条，成功 {N} 条 | ✅ / 🔴 |
| 应急漏洞通告 | 全部已修复 | {N} 个，未处理 {N} 个 | ✅ / 🔴 |

（如有未安装 Agent 的实例，额外列出明细表）
```

---

## 数据来源

| 接口 | 用途 |
|------|------|
| `cvm.DescribeInstances` | CVM 实例列表 |
| `cwp DescribeMachines` | 云镜机器列表(新接口,含 AgentStatus/AgentVersion) |
| `cwp DescribeMachineGeneral` | 云镜总体统计(兜底) |
| `cwp DescribeBruteAttackList` | 暴力破解记录 |
| `cwp DescribeHostLoginList` | 异常登录记录(`DescribeNonlocalLoginPlaces` 已废弃) |
| `cwp DescribeEmergencyVulList` | 应急漏洞通告 |
| `cwp DescribeVulHostCountScanTime` | 漏洞扫描统计 |
