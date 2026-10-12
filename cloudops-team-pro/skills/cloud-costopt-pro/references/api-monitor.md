# 云监控 API 参数参考（纯监控参数）

> **本文件只包含 Namespace / MetricName / Dimensions / 注意事项。**
> **闲置判定/降配规则见 `analysis-rules.md`（唯一权威源）。**
>
> ⚠️ GetMonitorData 使用 `Instances` 参数（非 `Dimensions`）：
> ```python
> "Instances": [{"Dimensions": [{"Name": "InstanceId", "Value": "ins-xxx"}]}]
> ```

---

## 通用参数

```
API: monitor.tencentcloudapi.com
Action: GetMonitorData
Version: 2018-07-24
Period: 3600（小时粒度，30天≈720点）
时间范围: 单次最多 31 天，最多 1440 个数据点
```

---

## 各产品监控参数

### CVM（Namespace: QCE/CVM）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CPUUsage | CPU使用率 | % |
| MemUsage | 内存使用率 | % |
| DiskUsage | 磁盘使用率 | % |
| LanOuttraffic | 内网出带宽 | Mbps |
| WanOuttraffic | 公网出带宽 | Mbps |

Dimensions: InstanceId

### MySQL（Namespace: QCE/CDB）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CPUUseRate | CPU使用率 | % |
| MemoryUseRate | 内存使用率 | % |
| ConnectionUseRate | 连接数使用率 | % |
| ThreadsConnected | 当前连接线程数 | 个 |
| ThreadsRunning | 运行中线程数 | 个 |

Dimensions: InstanceId

⚠️ ConnectionCount 指标名无效，用 ConnectionUseRate 替代
⚠️ MemoryUseRate 报 unauthorized → 尝试其他地域（实例可能跨地域）

### Redis（Namespace: QCE/REDIS_MEM）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CpuUs | CPU使用率 | %（如 0.19 代表 0.19%） |
| MemUsed | 内存使用量 | MB（需除以实例 Size 换算%） |
| Connections | 连接数 | 个 |
| Qps | 每秒请求数 | 个 |

Dimensions: instanceid（**全小写**）

### MongoDB（Namespace: QCE/CMONGO）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| MonogdAvgCpuUsage | Mongod节点平均CPU | % |
| MongodAvgMemUsage | Mongod节点平均内存 | % |
| ClusterConn | 集群连接数 | 个 |
| Qps | 每秒成功请求数 | 个 |

Dimensions: target（值为实例ID，**全小写**，如 "cmgo-xxxxxxxx"）

⚠️ 正确调用：`"Instances": [{"Dimensions": [{"Name": "target", "Value": "cmgo-xxxxxxxx"}]}]`
⚠️ 指标黑名单（调用会报 InvalidParameterValue）：Cpu、Conn、MemUsage、Cpuusage、Conncount
⚠️ ClusterConn 返回 unauthorized → 降级为仅 Qps 判断

### PostgreSQL（Namespace: QCE/POSTGRES）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| Cpu | CPU使用率 | % |
| MemoryRate | 内存使用率 | % |
| Connections | 连接数 | 个 |

Dimensions: resourceId

⚠️ 用 MemoryRate（%）而非 Memory（MB）

### ES（Namespace: QCE/CES）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CpuUsageAvg | CPU平均使用率 | % |
| JvmMemUsageAvg | JVM内存平均使用率 | % |
| SearchLatencyAvg | 查询平均延迟 | ms |
| IndexLatencyAvg | 写入平均延迟 | ms |

Dimensions: uInstanceId

⚠️ 必须用 CpuUsageAvg（带 Avg 后缀），CpuUsage 报错

### CKafka（Namespace: QCE/CKAFKA）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| InstanceProFlow | 实例生产流量 | MB/s |
| InstanceConFlow | 实例消费流量 | MB/s |
| InstanceDiskUsage | 磁盘使用率 | % |
| InstanceConnectCount | 连接数 | 个 |

Dimensions: instanceid（**全小写**）

⚠️ 返回 0 点/空说明监控数据缺失

### NAT 网关（Namespace: QCE/NAT_GATEWAY）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| OutBandwidth | 外网出带宽 | Mbps |
| InBandwidth | 外网入带宽 | Mbps |
| Conns | 并发连接数 | 个 |

Dimensions: natId

⚠️ 旧字段 Outtraffic/Intrafficpacket 已废弃

### VPN 网关（Namespace: QCE/VPNGW）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| VpnOutbandwidth | VPN出流量 | Mbps |
| VpnInbandwidth | VPN入流量 | Mbps |

Dimensions: vpnGwId

⚠️ 旧字段 Outtraffic 是错误名

### DC 专线（Namespace: QCE/DC）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| InBandwidth | 物理专线入带宽 | Mbps |
| OutBandwidth | 物理专线出带宽 | Mbps |

Dimensions: directConnectId

⚠️ 仅 AVAILABLE 状态的专线有监控数据

### CFS 文件存储（Namespace: QCE/CFS）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| DataReadIoBytes | 读IO | KB/s |
| DataWriteIoBytes | 写IO | KB/s |
| Storage | 已用存储量 | GB |
| ClientCount | 挂载客户端数 | 个 |

Dimensions: FileSystemId

### COS 对象存储（Namespace: QCE/COS）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| StdStorage | 标准存储量 | Byte |
| GetRequests | GET请求次数 | 次 |
| InternetTraffic | 外网下行流量 | Byte |

Dimensions: appid, bucket

### TKE 容器服务（Namespace: QCE/TKE2）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| K8sClusterRateCpuCoreUsedCluster | 集群 CPU 利用率 | % |
| K8sClusterRateMemUsageBytesCluster | 集群内存利用率 | % |
| K8sClusterCpuCoreUsed | 集群 CPU 使用核数 | 核 |
| K8sClusterCpuCoreTotal | 集群 CPU 总核数 | 核 |

Dimensions: appid, region, tke_cluster_instance_id

⚠️ 必须传 appid + region + tke_cluster_instance_id 三个维度，缺任何一个返回空
⚠️ 0 节点集群（如 Serverless/VPC 网络集群）无集群级监控数据
⚠️ 参考文档: https://cloud.tencent.com/document/product/248/53821

### TCHouse-D 数据仓库（Namespace: QCE/CDWDRS）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CpuUsage | 节点 CPU 使用率 | % |
| DiskCapacityData | 数据磁盘用量 | GB |
| DiskCapacityTotal | 磁盘总量 | GB |

Dimensions: clusterid, ip

⚠️ 监控维度是节点级（需通过 DescribeInstanceNodesInfo 获取 BE/FE 节点 IP）
⚠️ 参考文档: https://cloud.tencent.com/document/product/248/100566

### 向量数据库（Namespace: QCE/VECDB）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| ClusterNodesAvgCpuUtil | 集群平均 CPU 利用率 | % |
| ClusterNodesAvgMemUtil | 集群平均内存利用率 | % |
| ClusterNodesAvgDiskUsageUtil | 集群平均磁盘利用率 | % |

Dimensions: AppId, instance_id

⚠️ AppId 大写 A（不是 appid）
⚠️ 参考文档: https://cloud.tencent.com/document/product/248/113868

### TDMQ Pulsar（Namespace: QCE/TDMQ）

| 指标名 | 说明 | 单位 |
|--------|------|------|
| MsgRateIn | 消息生产速率 | 条/s |
| MsgRateOut | 消息消费速率 | 条/s |
| StorageSize | 存储大小 | Byte |
| BacklogSize | 积压消息大小 | Byte |

Dimensions: environmentId, tenantId, topicName

⚠️ 监控维度是 topic 级，需先通过 DescribeEnvironments + DescribeTopics 获取 namespace 和 topic 列表
⚠️ tenantId = ClusterId（如 pulsar-xxx）

### CynosDB（Namespace: QCE/CYNOSDB）[推测]

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CPUUseRate | CPU使用率 | % |
| MemoryUseRate | 内存使用率 | % |
| ConnectionUseRate | 连接数使用率 | % |

Dimensions: InstanceId（集群级用 ClusterId）

⚠️ **推测**：参照 QCE/CDB 命名惯例，命中失败或 unauthorized 时降级为"仅列费用,不做闲置判定",归入报告"其他产品"区块
⚠️ 参考：https://cloud.tencent.com/document/product/1098/40615

### TDSQL MySQL（Namespace: QCE/TDMYSQL）[推测]

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CpuUsageRate | CPU使用率 | % |
| MemUsageRate | 内存使用率 | % |
| ConnectionActive | 活跃连接数 | 个 |

Dimensions: InstanceId（或 ShardSerialId 分片级）

⚠️ **推测**：TDSQL 有多种形态(dcdb/tdsqlc/tdsql_mysql),Namespace 可能按形态区分,调用失败时降级为"仅列费用"
⚠️ 参考：https://cloud.tencent.com/document/product/557/35745

### MariaDB（Namespace: QCE/MARIADB）[推测]

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CpuUsageRate | CPU使用率 | % |
| MemUsageRate | 内存使用率 | % |
| Connections | 当前连接数 | 个 |

Dimensions: InstanceId

⚠️ **推测**：仅做字段名比对，unauthorized 时降级"仅列费用"
⚠️ 参考：https://cloud.tencent.com/document/product/237/16288

### CCN 云联网（Namespace: QCE/CCN）[推测]

| 指标名 | 说明 | 单位 |
|--------|------|------|
| Outbandwidth | 地域出带宽 | Mbps |
| Inbandwidth | 地域入带宽 | Mbps |

Dimensions: ccnId, sourceRegion, destinationRegion

⚠️ **推测**：CCN 监控按"地域间通道"维度采样,需先 DescribeCcnAttachedInstances 得到 region 对；若 Namespace 或 Dimensions 不被识别,降级为"仅按 DescribeCcnRegionBandwidthLimits 带宽配额+费用判断"
⚠️ 参考：https://cloud.tencent.com/document/product/248/51936

### Oceanus 流计算（Namespace: QCE/OCEANUS）[推测]

| 指标名 | 说明 | 单位 |
|--------|------|------|
| CuUsageRate | 计算资源 CU 使用率 | % |
| JobTotalCuNum | 作业占用 CU 数 | 个 |

Dimensions: ClusterId（或 JobId 作业级）

⚠️ **推测**：Oceanus 集群空转的判定优先用 DescribeJobs 的作业数/状态(TotalCount==0 或全部 Stopped),监控数据作辅助
⚠️ unauthorized 时降级为"仅按作业数判断"

---

## 监控数据处理

- 计算平均值：`Σ(value) / count`（sign.py 中 avg_values 已封装）
- 计算最大值：`max(value)`（sign.py 中 max_values 已封装）
- 覆盖率校验：`len([v for v in values if v is not None]) / 720`

> **闲置判定/降配规则见 `analysis-rules.md`。**
