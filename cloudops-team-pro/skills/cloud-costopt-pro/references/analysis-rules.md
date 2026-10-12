# 闲置判定 / 降配 / 优化策略规则（唯一权威源）

> **本文件是所有产品闲置判定、降配建议、优化策略的唯一权威定义。**
> api-resource.md 和 api-monitor.md 中不再包含任何判定规则，只保留 API 参数参考。
> **本规则面向腾讯云**。各产品的判定条件和指标名直接使用腾讯云 Namespace / 字段名，不做抽象层。

---

## 零、通用原则

### 0.1 资源豁免机制（F9 分层升级，本节为权威定义）

⚠️ [MUST] 豁免标签支持**分维度 / 分产品 / 带过期**多种语义。本节为 F9 的**唯一权威定义**，SKILL.md 仅保留指针。

#### 标签语义表

| 标签键 | 标签值 | 含义 |
|--------|--------|------|
| `finops-skip` | `true` | **完全豁免**，跳过所有判定（兼容旧规则） |
| `finops-skip` | `downgrade` | 允许闲置判定，**禁止降配建议**（只提示释放） |
| `finops-skip` | `relax` | 允许判定，但**降低风险等级**（[高]→[中]，[中]→[低]） |
| `finops-skip` | `cvm,cbs,...` | **分产品**豁免（逗号分隔，仅跳过匹配的产品扫描模块） |
| `finops-skip-expire` | `YYYY-MM-DD` | **过期日期**（与 finops-skip 配合使用，过期后豁免失效） |

> 所有标签键**大小写不敏感**（如 `FinOps-Skip=TRUE` 等效于 `finops-skip=true`）。

#### 判定流程

```
1. 读取资源标签 finops-skip
   ├─ 值 = true       → 完全跳过，进「已豁免资源」节
   ├─ 值 = downgrade  → 判定闲置但不建议降配
   ├─ 值 = relax      → 判定且给出建议，但风险等级降一级
   └─ 值 = "cvm,cbs,..." → 若当前扫描的产品在列表中则跳过
2. 读取资源标签 finops-skip-expire
   ├─ 有且今天 ≤ expire → 应用上述豁免
   └─ 有且今天 > expire → 忽略 finops-skip 标签（豁免已过期）
3. 无 finops-skip 标签 → 正常判定
```

#### 豁免汇总展示（报告末尾）

```
### 🛡 已豁免资源（共 N 个）

| 产品 | 资源ID | 资源名称 | 豁免类型 | 过期日期 |
|------|--------|---------|---------|---------|
| CVM | ins-xxx | test-env | 完全豁免 | 2026-06-30 |
| MySQL | cdb-xxx | prod-db | 仅禁止降配 | 永久 |
```

- 用户可在对话中临时指定其他豁免标签名（如"排除标签 env=dr 的资源"）→ 视为当次会话追加的保护名单，不写入 accounts.json

### 0.2 风险等级定义（F13 增强：可回滚性 + 执行时机）

每条优化建议必须标注风险等级，并附可回滚性和建议执行时机，完整格式：`[等级] 可逆性 · 执行时机`。

#### 风险等级三档

| 等级 | 适用场景 | 报告标注 |
|------|---------|---------|
| **低风险** | 删除未挂载 CBS/未绑定 EIP、COS 生命周期沉降/压缩、竞价实例建议、非生产定时关机、孤儿快照清理 | `[低]` |
| **中风险** | CVM 降配、按量转包年包月、NAT/VPN 降带宽、CBS 类型降级、ES/CKafka 降配 | `[中]` |
| **高风险** | 数据库（MySQL/Redis/MongoDB/PostgreSQL）降配/缩容、关停有连接的资源 | `[高] 请评审后执行` |

#### 可回滚性（F13 新增）

| 标注 | 含义 | 适用场景 |
|------|------|---------|
| `可逆` | 误操作后可恢复，不影响数据 | CVM/数据库降配（调规格不丢数据）、NAT/VPN 降带宽、COS 存储类型切换 |
| `不可逆` | 数据删除后无法恢复，或恢复成本极高 | 释放 CVM/CBS/EIP、删除快照、释放数据库 |

#### 执行时机（F13 新增）

| 标注 | 含义 |
|------|------|
| `即时` | 业务无影响，可立即执行（如释放未挂载 CBS） |
| `业务低峰` | 可能短暂抖动，建议业务低峰期（如 CVM 降配、数据库降配） |
| `维护窗口` | 需要停机或重启，必须提前通知（如数据库缩容、释放有连接的资源） |

#### 完整标注格式

```
[低] 不可逆 · 即时          ← 释放未挂载 CBS
[中] 可逆 · 业务低峰         ← CVM 降配
[中] 可逆 · 即时             ← 按量转包年（合同变更不影响运行）
[高] 不可逆 · 维护窗口       ← 释放 MySQL 实例
[高] 可逆 · 业务低峰         ← MySQL 降配（降完可再升配）
```

⚠️ [MUST] 所有建议必须使用完整格式，报告中展示在建议列或独立「风险」列。

### 0.3 监控数据覆盖率校验（F8 增强：多指标取最小）

⚠️ **例外条款(2026-05-21 新增)**:**已被 advisor 命中(`advisor_cost.json` hits[].instance_id 命中)的资源跳过本节校验**,直接采用 advisor 结论(advisor 是腾讯云官方策略级判定,不依赖本地监控覆盖率)。Step 2 跳过名单逻辑详见 SKILL.md §Step 2 advisor 跳过名单。

- 覆盖率 = 有效数据点数 / 理论总数据点数（30天 × 24h = 720 个点）
- **≥ 80%**：正常出结论
- **50%~80%**：结论后标注 `[数据不足，仅供参考]`
- **< 50%**：**不出闲置/降配结论**，只标注 `[监控数据严重不足，无法判定]`
- 覆盖率计算方式：`len([v for v in values if v is not None]) / 720`

#### F8 多指标产品取最小值

⚠️ [MUST] 多指标联合判定的产品（CVM/MySQL/MongoDB/PostgreSQL/ES），**覆盖率 = min(各指标覆盖率)**：

```
# CVM 示例
cpu_coverage = len([v for v in cpu_values if v is not None]) / 720
mem_coverage = len([v for v in mem_values if v is not None]) / 720
coverage = min(cpu_coverage, mem_coverage)  # 取最小，短板决定
```

**原因**：若只看 CPU 覆盖率高但内存数据严重缺失，基于"CPU+内存双低"的闲置判定会失准。取最小值强制要求两指标都充分。

#### 时间窗口对齐

- ⚠️ [MUST] 统一窗口：`end = now`，`start = now - 30d`（精确到小时）
- ⚠️ [MUST] 每个资源的拉监控调用必须用相同的 `start/end`，不得各用各的

### 0.4 资源过滤

- 过滤创建时长 < 24 小时的资源（避免误判新建资源）
- 不自动排除名称含 test/dev 的资源（由用户决定是否处置，但在报告中标注）

### 0.5 关键资源保护（F7，HARD 级）

❌ [HARD] 以下资源**禁止纳入闲置判定**，直接跳过并在报告末尾「已保护的关键资源」节列出：

#### 自动识别的关键资源

| 产品 | 识别字段 | 匹配值 | 保护原因 |
|------|---------|--------|---------|
| MySQL | `InstanceType` | `2` = 灾备实例 `disaster`；`3` = 只读实例 `readonly` | 只读实例 CPU/连接低是正常的（主库分流）；灾备实例设计上就是闲置备份 |
| Redis | `NodeSet[*].NodeType` | `replica` | 读流量分发，主节点判定即可 |
| MongoDB | `ReplicaSetList[*].Role` | `SECONDARY` | 副本节点 |
| PostgreSQL | `DBInstanceType` | `readonly` / `guard` | 只读 / 灾备（guard）|
| CVM | 标签 `finops-critical=true` | - | 用户手动保护名单 |
| 所有产品 | 标签 `finops-critical=true` | - | 通用保护名单 |

> ℹ️ **腾讯云 CDB InstanceType 编码参考**（官方 API 文档）：
> - `1` = 主实例（master，正常判定，不保护）
> - `2` = 灾备实例（disaster，**保护**）
> - `3` = 只读实例（readonly，**保护**）
> 三个文件（analysis-rules.md / api-resource.md / SKILL.md）的编码映射必须统一为本表。

#### 报告展示

```
### 🛡 已保护的关键资源（共 N 个）

| 产品 | 资源ID | 资源名称 | 保护原因 |
|------|--------|---------|---------|
| MySQL | cdb-ro-xxx | prod-user-db-readonly | 只读实例（InstanceType=readonly） |
| Redis | redis-xxx-replica | cache-cluster-slave | 集群副本节点 |
| CVM | ins-xxx | dr-backup-srv | finops-critical=true |
```

#### 测试用例（HARD-F7）

**✅ passing 示例**：
- MySQL `cdb-ro-abc123` 返回 `InstanceType=3`（只读）→ 跳过闲置判定 → 出现在「已保护的关键资源」节

**❌ failing 示例**（违反 F7）：
- MySQL `cdb-ro-abc123` `InstanceType=3`，CPU 均值 2%，连接使用率 1%，内存使用率 5%
- 报告中建议：`🔴 MySQL 闲置 - 建议释放 [高]`
- 错误：未识别只读实例（InstanceType=3 应跳过），导致误伤主备架构

⚠️ [MUST] 无法识别 `InstanceType` 字段（API 异常）时，**降级策略**：
- 查询资源标签中是否有 `role=readonly/replica/standby/dr/disaster/backup`
- 有 → 跳过判定
- 无 → 按普通资源判定但在报告中标注「⚠️ 未能识别主备角色，请人工核查」

---

## 一、闲置判定规则

### CVM（云服务器）

- **停机按量** → 🔴 `[低]`
  - 条件：InstanceState=STOPPED 且 InstanceChargeType=POSTPAID_BY_HOUR
  - ⚠️ [MUST] **必须读取 StopChargingMode 字段区分计费模式**（pricing-rules.md §3.2）：
    - `STOP_CHARGING`（关机不收费）：CVM 计算已停收，月省 = 挂载 CBS 磁盘费；❌ 禁止用 CVM 整机 baseline
    - `KEEP_CHARGING`（关机仍收费）：月省 = CVM baseline 全量
  - 建议：确认后释放或关机不计费

- **疑似闲置** → 🔴 `[中]`
  - 条件：RUNNING + CPU均值 < 5% **且** 内存均值 < 20%，持续 30 天
  - ⚠️ 双条件 AND，缺任意一项不判定闲置
  - ⚠️ MemUsage 返回空时，单独用 CPU < 3% 兜底，报告标注"内存数据缺失"
  - 腾讯云指标：CPUUsage / MemUsage（QCE/CVM）

- **低利用率** → 🟡 `[中]`
  - 条件：CPU均值 < 15% 且 内存均值 < 40%（不满足疑似闲置条件）
  - 建议：降配至当前规格的 1/2

- **大规格标注**：≥16核实例排在同节前列，加 `⚠️ 大规格实例（≥16核），成本损耗高，优先核查`

### CBS（云硬盘）

- **未挂载** → 🔴 `[低]`
  - 条件：DiskState=UNATTACHED
  - 建议：快照备份后删除

**CBS 月省计算特殊处理（F41，2026-06-01 新增）**：

CBS 按量计费磁盘在 `billing_lookup.json` 里 **baseline 恒为 0**（账单 API `DescribeBillResourceSummary` 对 CBS 按产品级汇总，不拆分到 disk-xxx 资源 ID）。因此 CBS 月省**不能从 billing_lookup 取 baseline**，必须用刊例价兜底：

```python
# CBS 月省刊例价兜底
CBS_PRICE_PER_GB = {
    'CLOUD_PREMIUM':  0.35,   # 高效云盘 ¥0.35/GB/月（按量折算：¥0.000486/GB/h × 720h）
    'CLOUD_SSD':      1.10,   # SSD 云盘 ¥1.10/GB/月
    'CLOUD_BSSD':     0.50,   # 增强型 SSD ¥0.50/GB/月
    'CLOUD_HSSD':     0.35,   # 高性能 SSD ¥0.35/GB/月（与高效云盘同档）
    'CLOUD_TSSD':     1.80,   # 极速型 SSD ¥1.80/GB/月
}
# resource_discovery.json 里有 DiskSize（GB）和 DiskType
disk_size = disk.get('DiskSize', 0)
disk_type = disk.get('DiskType', 'CLOUD_PREMIUM')
unit_price = CBS_PRICE_PER_GB.get(disk_type, 0.35)
saving = int(disk_size * unit_price)  # 删除后全量节省
saving_display = f'¥{saving:,} (={disk_size}GB×¥{unit_price}/GB·月，刊例价估算)' if saving > 0 else '需评审'
```

- ✅ 删除未挂载磁盘节省 = `DiskSize × 单价/GB/月`（100% 节省）
- ⚠️ 刊例价为公开定价，实际按量账单可能有折扣，报告标注"刊例价估算"
- ❌ 不写"需评审"（磁盘容量已知，可以计算）

### MySQL（云数据库）

- **闲置** → 🔴 `[高] 请评审后执行`
  - 条件：CPU均值 < 5% **且** 连接使用率 < 5% **且** 内存使用率 < 20%，持续 30 天
  - ⚠️ 单独 CPU 低不判定闲置（可能有定时批量任务）
  - 腾讯云指标：CPUUseRate / ConnectionUseRate / MemoryUseRate（QCE/CDB）
  - **规格字段**：从 DescribeDBInstances 的 `Cpu`（核）和 `Memory`（MB÷1024=GB）取值
    - ✅ 正确：`2核4GB`、`4核8GB`
    - ❌ 错误：`3.9GB/100GB`（内存/磁盘，不是规格）
  - ⚠️ Memory=0 时（实例初始化/迁移中），规格列显示"-"并标注"规格字段异常"

### Redis（云数据库）

- **闲置** → 🔴 `[高] 请评审后执行`
  - 条件：内存使用率 < 10% **且** 连接数均值 < 5，持续 30 天
  - ⚠️ 内存使用率 = MemUsed(MB) / Size(MB) × 100
  - ⚠️ 不用 CPU 判断闲置
  - ⚠️ BillingMode：0=按量，1=包年包月（与直觉相反）
  - 腾讯云指标：MemUsed / Connections（QCE/REDIS_MEM）

### MongoDB

- **闲置** → 🔴 `[高] 请评审后执行`
  - 条件：CPU均值 < 5% **且** QPS均值 < 1 **且** 连接数均值 < 10
  - ⚠️ 三指标联合，任意一项活跃即有业务
  - 腾讯云指标：MonogdAvgCpuUsage / Qps / ClusterConn（QCE/CMONGO）
  - ⚠️ ClusterConn 返回 unauthorized 时降级为仅 Qps 判断

### PostgreSQL

- **闲置** → 🔴 `[高] 请评审后执行`
  - 条件：CPU均值 < 5% **且** 内存使用率 < 20% **且** 连接数 < 5
  - 腾讯云指标：Cpu / MemoryRate / Connections（QCE/POSTGRES）

### CLB（负载均衡）

- **无后端绑定** → 🟡 `[低]`（需人工确认，不是 🔴）
  - 条件：DescribeTargets 返回空
  - 建议：确认后释放

### NAT 网关

- **低利用率** → 🟡 `[中]`
  - 条件：出带宽均值 < 购买带宽 × 5%，持续 30 天
  - ⚠️ 长期合同，仅供参考
  - 腾讯云指标：OutBandwidth（QCE/NAT_GATEWAY）

### CFS（文件存储）

- **近无IO** → 🟡 `[中]`
  - 条件：DataReadIoBytes < 0.01 且 DataWriteIoBytes < 0.01，30 天均值
  - ClientCount > 0 时标注"挂载但无 IO，疑似低活跃"
  - 腾讯云指标：DataReadIoBytes / DataWriteIoBytes / ClientCount（QCE/CFS）

### EIP（弹性公网IP）

- **未绑定** → 🔴 `[低]`
  - 条件：AddressStatus=UNBIND
  - 建议：释放回收（仍按小时计费）

### ES（Elasticsearch）

- **闲置** → 🔴 `[中]`
  - 条件：CpuUsageAvg < 5% 且 JvmMemUsageAvg < 20%（双条件 AND）
  - ⚠️ 指标名必须用 CpuUsageAvg / JvmMemUsageAvg（带 Avg 后缀），CpuUsage 等旧名报错
  - 腾讯云指标：CpuUsageAvg / JvmMemUsageAvg（QCE/CES）

- **低利用率可降配** → 🟡 `[中]`
  - 条件：CpuUsageAvg < 15% 且 JvmMemUsageAvg < 50%

### CKafka（消息队列）

- **闲置** → 🔴 `[中]`
  - 条件：InstanceProFlow均值 < 0.1 且 InstanceConFlow均值 < 0.1（双条件 AND）
  - ⚠️ dimension 全小写 instanceid
  - ⚠️ 监控返回 0 点/空时，调用 DescribeInstanceAttributes 查 Healthy + TopicNum 辅助判断，标注"监控数据缺失，请人工核查"
  - 腾讯云指标：InstanceProFlow / InstanceConFlow（QCE/CKAFKA）

### VPN 网关

- **低利用率** → 🟡 `[中]`
  - 条件：VpnOutbandwidth均值 < 0.01（30天）
  - 腾讯云指标：VpnOutbandwidth（QCE/VPNGW）

### DC（专线接入）

- **带宽利用率低** → 🟡 `[中]`（仅供参考，长期合同不建议退订）
  - 条件：InBandwidth均值 < 购买带宽 × 5% 且 OutBandwidth均值 < 购买带宽 × 5%
  - ⚠️ 只扫 State=AVAILABLE 的专线
  - 建议：联系业务确认是否可缩减
  - 腾讯云指标：InBandwidth / OutBandwidth（QCE/DC）

### EMR（弹性MapReduce）

- **已终止仍计费** → 🔴 `[低]`
  - 条件：Status=terminated_with_prepaid

- **低利用率** → 🟡 `[中]`
  - 条件：CPU均值 < 5%（30天）

### COS（对象存储）

- **低频访问** → 🟡 `[低]`
  - 条件：GetRequests < 10/天 且 StdStorage > 100GB
  - 建议：配置生命周期转低频存储（预计节省 32%）
  - 条件：GetRequests < 1/天 → 建议转归档存储（预计节省 72%）
  - ⚠️ StdStorage=0 说明数据在低频/归档类型中，不判定为空桶
  - 腾讯云指标：StdStorage / GetRequests（QCE/COS）

### TKE（容器服务，集群级）

- **空集群** → 🔴 `[低]`
  - 条件：ClusterNodeNum=0 **且** ClusterStatus=Running，持续 ≥ 7 天
  - 建议：确认后销毁集群
- **低利用率集群** → 🟡 `[中]`
  - 条件：K8sClusterRateCpuCoreUsedCluster 均值 < 15% **且** K8sClusterRateMemUsageBytesCluster 均值 < 30%，30 天
  - 建议：缩减节点池规模 / 启用 ClusterAutoscaler
  - 腾讯云指标：K8sClusterRateCpuCoreUsedCluster / K8sClusterRateMemUsageBytesCluster（QCE/TKE2）
  - ⚠️ 去重：CVM 扫描中以 `as-tke-*` 前缀的机器已被 TKE 集群管理,在 TKE 建议汇总时按 ClusterId 合并,CVM 节点不重复给出"释放"建议
  - ⚠️ 0 节点集群无集群级监控数据,只走空集群判定

### EKS（Serverless 容器，TKE 子集）

- **无作业** → 🔴 `[低]`
  - 条件：DescribeClusters 过滤 ClusterType=="EKS" 且该集群关联的 Pod 数 = 0,持续 ≥ 7 天
  - 建议：删除集群（EKS 按 Pod 使用量计费,无 Pod 仍会保留管控成本）
  - ⚠️ EKS 无独立集群级监控 Namespace,只做 Pod 级判定；无法获取时降级"仅列费用"

### TDMQ Pulsar（消息队列）

- **空集群** → 🔴 `[低]`
  - 条件：DescribeEnvironments 返回空 **或** 所有 environment 下 topic 数 = 0
  - 建议：销毁集群
- **低吞吐 topic** → 🟡 `[低]`
  - 条件：MsgRateIn + MsgRateOut 均值 < 1 条/s，30 天 **且** BacklogSize=0
  - 建议：删除 topic / 合并到共用 namespace
  - 腾讯云指标：MsgRateIn / MsgRateOut / BacklogSize（QCE/TDMQ）
  - ⚠️ 监控维度是 topic 级,汇总到集群级时按 ClusterId 聚合建议

### 向量数据库（VectorDB）

- **闲置实例** → 🔴 `[高] 请评审后执行`
  - 条件：ClusterNodesAvgCpuUtil < 5% **且** ClusterNodesAvgMemUtil < 20% **且** ClusterNodesAvgDiskUsageUtil < 10%，30 天
  - 建议：确认后释放 / 降规格
  - 腾讯云指标：ClusterNodesAvgCpuUtil / ClusterNodesAvgMemUtil / ClusterNodesAvgDiskUsageUtil（QCE/VECDB）
  - ⚠️ AppId 大写 A

### TCHouse-D（数据仓库）

- **低利用率集群** → 🟡 `[中]`
  - 条件：所有 BE 节点 CpuUsage 均值 < 10% **且** DiskCapacityData/DiskCapacityTotal < 30%，30 天
  - 建议：缩节点 / 降节点规格
  - 腾讯云指标：CpuUsage / DiskCapacityData / DiskCapacityTotal（QCE/CDWDRS）
  - ⚠️ 节点级监控,需先 DescribeInstanceNodesInfo 拿 BE 节点 IP,按节点汇总后对集群做判定

### CynosDB（TDSQL-C 兼容 MySQL）[推测]

- **闲置集群** → 🔴 `[高] 请评审后执行`
  - 条件：CPUUseRate < 5% **且** ConnectionUseRate < 5% **且** MemoryUseRate < 20%，30 天
  - 腾讯云指标：CPUUseRate / MemoryUseRate / ConnectionUseRate（QCE/CYNOSDB 推测）
  - ⚠️ Namespace/字段均为推测;unauthorized 时降级仅列费用,不做判定
  - ⚠️ 只读实例保护：DescribeClusterInstances.InstanceType 识别 readonly 实例跳过判定

### TDSQL（dcdb 分布式）[推测]

- **闲置实例** → 🔴 `[高] 请评审后执行`
  - 条件：CpuUsageRate < 5% **且** ConnectionActive 均值 < 5 **且** MemUsageRate < 20%，30 天
  - 腾讯云指标：CpuUsageRate / MemUsageRate / ConnectionActive（QCE/TDMYSQL 推测）
  - ⚠️ 字段与 Namespace 均推测,命中失败降级仅列费用

### MariaDB [推测]

- **闲置实例** → 🔴 `[高] 请评审后执行`
  - 条件：CpuUsageRate < 5% **且** MemUsageRate < 20% **且** Connections 均值 < 5，30 天
  - 腾讯云指标：CpuUsageRate / MemUsageRate / Connections（QCE/MARIADB 推测）
  - ⚠️ 与 TDSQL 同家族,Namespace 可能重用,命中失败降级仅列费用

### CCN 云联网（网络）[推测]

- **无挂载** → 🔴 `[低]`
  - 条件：DescribeCcnAttachedInstances 返回空 **且** CCN State=Available
  - 建议：删除云联网（避免月费）
- **低带宽利用** → 🟡 `[低]`
  - 条件：Outbandwidth + Inbandwidth 均值 < 购买带宽 × 5%，30 天
  - 建议：降带宽配额（DescribeCcnRegionBandwidthLimits）
  - 腾讯云指标：Outbandwidth / Inbandwidth（QCE/CCN 推测）
  - ⚠️ 监控 Namespace 推测,命中失败时降级为仅按 DescribeCcnRegionBandwidthLimits 配额+费用判断

### Oceanus 流计算

- **空集群** → 🔴 `[低]`
  - 条件：DescribeJobs 返回 TotalCount=0 **或** 所有 Job Status 为 Stopped/Failed，持续 ≥ 7 天
  - 建议：销毁集群
- **低 CU 利用率** → 🟡 `[中]`
  - 条件：CuUsageRate 均值 < 10%，30 天 **且** 运行中作业数 < 集群 CU 上限 × 10%
  - 建议：缩减集群 CU 配额
  - 腾讯云指标：CuUsageRate / JobTotalCuNum（QCE/OCEANUS 推测）
  - ⚠️ 监控 Namespace 推测；unauthorized 时仅按 DescribeJobs 作业数做空集群判定

---

## 二、降配建议规则

### 通用原则

- 双指标必须同时满足，单项低不判定可降配
- 建议列 **只写目标规格 + 风险等级**，不加"降至/建议/💡降至"等修饰词

### CVM 降配

可降配条件：CPU均值 < 15% 且 CPU峰值 < 40% 且 内存均值 < 40% 且 内存峰值 < 70%
→ 建议降至当前规格的 1/2（核数和内存同比例缩减）`[中]`

谨慎降配：
- 仅 CPU 低（内存均值 ≥ 40%）→ 降 CPU 核数，保持内存（换内存优化型）
- 仅内存低（CPU均值 ≥ 15%）→ 减内存，保持 CPU（换计算优化型）
- 任意项峰值高（CPU峰值 > 60% 或 内存峰值 > 80%）→ 不建议降配

### MySQL 降配

条件：CPU均值 < 5% 且 连接使用率 < 20% 且 内存使用率 < 30% `[高] 请评审后执行`
```
32GB → 4核8GB
8GB  → 2核4GB 或 1核2GB
4GB  → 1核2GB
```

### Redis 降配

仅看内存峰值：建议规格 = 实际用量峰值 × 2，向上取最近档位 `[高] 请评审后执行`
档位：256MB / 512MB / 1GB / 2GB / 4GB / 8GB / 16GB / 32GB / 64GB

---

## 三、按量转包年包月规则

判定条件（全部满足）：
1. 计费模式 = POSTPAID_BY_HOUR
2. 实例状态 = RUNNING
3. 创建时长 ≥ 30 天

⚠️ 无论 CPU 高低都必须列出 `[中]`：
- CPU < 5%：备注填 `低负载，确认保留后再转包`
- CPU ≥ 5%：备注填 `正常使用中`

预计节省：包年约省 40-50%，包月约省 20-30%

---

## 四、监控窗口

- 默认：30 天（Period=3600s，约 720 个数据点）
- 用户明确指定天数时按用户要求执行

---

## 五、按产品类别专属优化策略矩阵

> 每个产品不只是"闲置/不闲置"的二元判定，而是有**多条专属优化路径**。
> 判定条件和字段直接使用腾讯云 Namespace / 字段名。
> ℹ️ **本章各路径的「预计节省」区间仅作语义参考，实际展示金额以 `pricing-rules.md` 比例系数为准。**

### 5.1 计算（CVM）— 4 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 闲置/低利用率 | CPU<5%+内存<20%（30天） | 降配或关停 | 中/高 | 50-70% |
| 按量长期运行 | POSTPAID+运行>30天 | 转包年包月 | 中 | 40-50% |
| 可中断工作负载 | 名称含 test/dev/ci 或标签 Environment=dev/staging | 建议竞价实例 | 低 | 70-90% |
| 非生产定时开关 | 标签 Environment≠prod 且非 7×24 | 建议定时关机（夜间/周末） | 低 | **仅计算费用**（磁盘照收） |

### 5.2 对象存储（COS）— 3 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 生命周期沉降 | GetRequests<10/天+StdStorage>100GB | 标准→低频（省32%）；<1/天→归档（省72%）；基本不访问→深度归档（省90%） | 低 | 32-90% |
| 碎片/过期清理 | 不完整分块上传、历史版本文件 | 配置生命周期规则自动清理 | 低 | 视数据量 |
| 图片压缩 | 图片类 bucket + 存储>10GB | AVIF 压缩（省59%）/ HEIF 压缩（省49%） | 低 | 26-59% |

### 5.3 数据库（MySQL/Redis/MongoDB/PostgreSQL/CynosDB/TDSQL/MariaDB）— 3 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 闲置实例 | 各产品专属多指标联合判定（见第一章） | 关停或转为按量 | 高 | 100% |
| Right-sizing 降配 | 各产品专属降配条件（见第二章） | 降至合适规格 | 高 | 30-50% |

⚠️ **降配≠释放**：降配节省 = 当前规格费用 - 降配后规格费用（约30-50%）；释放节省 = 全量费用（100%）。两者禁止混淆，详见 pricing-rules.md §3.1。
| 按量转包 | POSTPAID+运行>30天 | 转包年包月 | 中 | 40-50% |

⚠️ CynosDB/TDSQL/MariaDB 监控 Namespace 为推测，命中失败时只走"按量转包"路径，不出降配建议。

### 5.4 云硬盘（CBS）— 3 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 未挂载回收 | DiskState=UNATTACHED | 快照备份后删除 | 低 | 100% |
| 存储类型降级 | SSD 类型 + DiskIO均值<100IOPS（30天） | 降级为高效云盘 | 中 | 40-60% |
| 孤儿快照清理 | 快照关联的源 CBS 已删除 / 快照过期>90天 | 删除孤儿快照 | 低 | 视快照量 |

### 5.5 网络（CLB/NAT/EIP/VPN）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 未绑定/无后端回收 | EIP UNBIND / CLB 无 RS | 释放回收 | 低 | 100% |
| 带宽降档 | NAT/VPN 带宽利用率<5%（30天） | 降至更低带宽档位 | 中 | 视降档幅度 |

### 5.6 中间件/大数据（ES/CKafka/EMR）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 低利用率降配 | 各产品专属监控指标低（见第一章） | 缩节点/降配 | 中 | 30-50% |
| 按量转包 | POSTPAID+长期运行 | 转包年包月 | 中 | 40-50% |

### 5.7 文件存储（CFS）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 近无IO释放 | ReadIO<0.01+WriteIO<0.01（30天） | 确认后释放 | 中 | 100% |
| 存储类型降级 | 性能型 + IO均值低 | 转标准型/Turbo标准型 | 低 | 30-40% |

### 5.8 专线（DC）— 仅供参考

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 带宽利用率低 | 带宽<购买量5%（30天） | 联系业务确认是否可缩减（长期合同，仅供参考） | 中 | 视协商结果 |

### 5.9 容器（TKE/EKS）— 3 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 空集群销毁 | TKE NodeNum=0 持续≥7天 / EKS 关联 Pod=0 持续≥7天 | 销毁集群 | 低 | 100% |
| 低利用率缩容 | TKE 集群 CPU<15% + 内存<30%（30天） | 缩节点池规模 / 启 CA | 中 | 30-50% |
| 节点池去重 | 同集群存在 as-tke-* CVM 建议 | TKE 建议优先,CVM 节点不再单独给释放 | — | — |

⚠️ EKS 仅在 ClusterType=EKS 过滤后出建议,无 Pod 级监控时只走"空集群销毁"路径。

### 5.10 消息队列（TDMQ Pulsar）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 空集群销毁 | 无 environment 或 topic 数=0 | 销毁集群 | 低 | 100% |
| 低吞吐 topic 清理 | 单 topic MsgRateIn+MsgRateOut<1条/s+BacklogSize=0（30天） | 删除 topic / 合并 namespace | 低 | 视 topic 占比 |

### 5.11 向量数据库（VectorDB）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 闲置集群 | CPU+内存+磁盘均<阈值（见第一章） | 释放或降规格 | 高 | 50-100% |
| 按量转包 | POSTPAID+运行>30天 | 转包年包月 | 中 | 40-50% |

### 5.12 大数据（TCHouse-D）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 低利用率缩节点 | BE 节点 CPU<10% + 磁盘<30%（30天） | 缩节点数 | 中 | 30-50% |
| 按量转包 | POSTPAID+长期运行 | 转包年包月 | 中 | 40-50% |

### 5.13 云联网（CCN）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 无挂载释放 | 无 AttachedInstance + State=Available | 删除云联网 | 低 | 100% |
| 带宽配额降档 | 地域间带宽<配额 5%（30天） | 调小 DescribeCcnRegionBandwidthLimits 配额 | 中 | 视降档幅度 |

⚠️ CCN 监控 Namespace 为推测,命中失败时只走"无挂载释放"路径。

### 5.14 流计算（Oceanus）— 2 条优化路径

| 路径 | 判定条件 | 建议 | 风险 | 预计节省 |
|------|---------|------|------|---------|
| 空集群销毁 | TotalCount=0 或全部 Stopped 持续≥7天 | 销毁集群 | 低 | 100% |
| CU 配额缩减 | CuUsageRate<10%（30天） | 缩 CU 配额 | 中 | 30-50% |

⚠️ 监控推测命中失败时只走"空集群销毁"路径。

---

## 六、降本计划分周排期规则

> 用户在巡检完成后说"生成降本计划"时，基于上下文中的巡检结果按以下规则排期。

### 第一周（低风险，立即执行）
- 删除未挂载 CBS / 未绑定 EIP / 孤儿快照
- COS 生命周期沉降配置
- 竞价实例替换建议
- 非生产环境定时关机建议
- EMR 已终止仍计费的实例处理

### 第二周（中风险，需确认）
- CVM 降配 / 按量转包年包月
- CBS 存储类型降级（SSD→高效云盘）
- NAT / VPN 带宽降档
- ES / CKafka 降配
- CFS 存储类型降级

### 第三周（高风险，需评审）
- MySQL / Redis / MongoDB / PostgreSQL 降配 / 缩容
- 关停有连接的闲置资源（需业务确认）

### 降本计划报告格式

```
## 📋 降本计划 — {YYYY-MM}

**账户：** {account_name}（{uin}）
**基于巡检：** {scan_date} 巡检结果
**预计潜在月度节省：** ¥{total}

### 第一周 — 低风险（立即执行）

| 产品 | 资源ID | 资源名称 | 建议操作 | 预计月省 | 风险 |
|------|--------|---------|---------|---------|------|
| CBS | disk-xxx | test-disk | 快照备份后删除 | ¥680 | [低] |

### 第二周 — 中风险（需确认）

| 产品 | 资源ID | 资源名称 | 建议操作 | 预计月省 | 风险 |
|:------|:--------|:---------|:---------|:---------|:------|
| CVM | ins-xxx | web-server | SA5.XLARGE8 (4核8G) | ¥1,200 | [中] |

### 第三周 — 高风险（需评审）

| 产品 | 资源ID | 资源名称 | 建议操作 | 预计月省 | 风险 |
|:------|:--------|:---------|:---------|:---------|:------|
| MySQL | cdb-xxx | prod-db | 2核4GB | ¥1,800 | [高] |

💡 低风险项可直接执行；中风险项请与业务确认；高风险项请组织评审后执行。
```

---

## 七、advisor 双源融合规则（2026-05-21 新增,吸收 cloudq 成本能力）

> 本章是 advisor 与 costopt 自判定的合并优先级、严重度映射、月省金额口径的唯一权威源。
> 配套文档:`docs/15-advisor-cost-coverage.md`(22 strategy 元数据 + product 映射)。
> Step 1.5 调度详见 SKILL.md;本章只定义判定/合并规则。

### 7.1 严重度映射(advisor Level + RiskDays → costopt 严重度)

| 条件 | 严重度 | 含义 |
|------|--------|------|
| `level >= 3`                          | 🔴 立即处理 | advisor 标"高"档,无论天数 |
| `level == 2` 且 `risk_days > 30`       | 🟠 建议处理 | 中档持续超月 |
| `level == 2` 且 `risk_days <= 30`      | 🟡 关注观察 | 中档短期 |
| `level == 1` 或 其他                   | 🟢 仅记录   | 低档/无信息,只列在统计不进建议表 |

⚠️ 与 inspector `references/14-advisor-coverage.md §2` 同表;两个 skill 通用判定标准。

### 7.2 合并优先级(advisor > costopt)

⚠️ [MUST] **advisor 命中是主结论**,costopt 自判仅作明细补充:

| 场景 | 报告展示 | 来源列 |
|------|---------|--------|
| advisor 命中 + costopt 也命中(instance_id 相同) | 一行,严重度取两者最高 | `advisor+costopt` |
| advisor 命中 + costopt 未命中 | 一行,采用 advisor 结论 | `advisor` |
| advisor 未命中 + costopt 命中 | 一行,采用 costopt 自判 | `costopt` |
| advisor 命中 strategy 7302(LH) | 一行,采用 advisor 结论(LH costopt 不直扫) | `advisor(独家)` |

❌ [HARD] **去重维度是 instance_id 完全相等**;不允许"前缀相同就算同一资源"(advisor `ins-xxx` 与 costopt `ins-xxx-replica` 是不同资源)。

### 7.3 月省金额口径(costopt 主算 + advisor 补充,2026-06-01 修订)

> ⚠️ **修订说明**:原文"advisor 主显"逻辑已通过实测证伪。实测(2026-05-21 营销账号)advisor 成本组 22 strategy 命中的月省金额 100% 返回 0。因此**正确主算口径是 costopt: billing_lookup.json baseline × pricing-rules.md ratio%**。advisor 金额仅在 >0 时作辅助展示。

- **主算**:从 `billing_lookup.json` 按 `instance_id` 查 `cost` 字段作为 `baseline`,乘以 `pricing-rules.md §三` 对应路径的 ratio%,得到月省金额
- **advisor 金额辅助**:若 advisor `monthly_saving_estimate > 0`,在主算金额后括号附加
  ```
  ¥1,050                          ← 只有 costopt 有金额(常见情况)
  ¥1,200(参考: ¥1,050)            ← advisor 主+costopt 辅,差 >20% 时加 ⚠️
  ¥1,200                          ← 只有 advisor 有金额,无 costopt baseline
  ```
- **偏差告警(HARD · 前置守卫)**:仅当 `advisor_saving > 0 AND costopt_saving > 0` **两者都有数**时才比较;abs(advisor - costopt) / max(advisor, costopt) > 0.2 时该行末尾加 `⚠️ 双源月省偏差 >20%,请评审`
  - ❌ [HARD] `advisor_saving=0` 时**严禁触发 ⚠️ 偏差告警**;实测 2026-05-21 营销账号 18 条 advisor 命中 100% `monthly_saving_estimate=0`(成本组 22 strategy 普遍不带金额是已知现象,非异常),若按"任意差就告警"会把全部 24 行染 ⚠️,告警噪音 = 失效信号
  - ❌ [HARD] `costopt_saving=0`(baseline 缺失,如 MongoDB/Redis/ES/LH/MySQL 账单按集群汇总)时也**严禁告警**;此时只能依赖 advisor 单源,无可对比基准
- **advisor 金额=0 且 baseline 也缺失**:月省列写 `需评审`,**不写 ¥0**。典型场景:MongoDB/Redis/ES/LH 账单按集群汇总,ResourceId 级别无对应账单条目

### 7.3.1 advisor 命中资源的 baseline 获取步骤(HARD)

advisor hit 只带 `instance_id`,不带账单金额。Step 3 **必须**按以下步骤主动查 baseline:

```python
# Step 3 advisor 命中资源月省计算骨架
lookup = json.load(open('/tmp/billing_lookup.json'))
disc   = json.load(open(f'{work_dir}/resource_discovery.json'))

for hit in advisor_hits:
    rid = hit['instance_id']
    # 1. baseline from billing_lookup
    baseline = lookup.get(rid, {}).get('cost')  # 可能为 0 或 None(集群汇总场景)
    # 2. 规格 from resource_discovery(按产品找对应实例)
    spec = _find_spec_in_disc(disc, rid, hit['product_key'])  # 见 15-advisor-cost-coverage.md §6.1
    # 3. ratio from pricing-rules.md(按 strategy → 优化路径 → ratio%)
    ratio = RATIO_MAP.get(hit['strategy_id'], default_ratio)
    # 4. 计算
    saving = int(baseline * ratio) if baseline else None
    # 5. 展示
    saving_display = f'¥{saving:,}' if saving else '需评审'
```

❌ [HARD] **不允许因"advisor 路径跳过了监控采集"就跳过月省计算**。监控跳过不影响 billing_lookup 查价,两件事无关联。

### 7.4 LH(轻量应用服务器)独家处理

- LH 在 `api-resource.md` 无对应条目,costopt 不直扫
- advisor strategy 7302 命中 → 直接出建议
- 严重度按 7.1 映射
- 报告渲染**不暴露来源**(F34),与其他建议格式一致
- 不要求人工评审月省偏差(没有 costopt 自算可对比)

### 7.5 scan_status=denied 时的报告语义

❌ [HARD] 报告头部必须显式标注(用户可见,**禁止出现 advisor / 智能顾问 / strategy** 等内部字眼,F34):
```
> ⚠️ **云端策略巡检不可用**(权限不足或接口异常)
> 以下结论仅基于本地账单 + 监控数据自判,可能漏掉以下成本场景:
> CLB 闲置/低利用率、CVM 计费模式、LH 低使用率、COS 碎片清理、EIP 带宽利用率不足等
> 建议人工检查腾讯云控制台的"成本优化"相关建议
```

❌ [HARD] **严禁静默降级**:不允许 advisor 失败就当无事发生只出 costopt 结论;必须显式告知用户(用户可见文案用"云端策略巡检",内部日志/字段仍用 advisor)。

