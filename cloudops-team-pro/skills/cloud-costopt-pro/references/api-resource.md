# 各产品资源列表 API 参考（纯 API 调用信息）

> **本文件只包含 API 调用信息**（host/action/version/关注字段/标签获取/分页）。
> **闲置判定/降配/优化策略规则见 `analysis-rules.md`（唯一权威源）。**

---

## 标签规范

资源归属通过标签 `head` 字段识别，不同产品标签字段名不同：

| 产品 | 标签字段 | 格式 |
|------|---------|------|
| CVM | inst.Tags | [{Key, Value}] 取 head |
| CBS | disk.Tags | [{Key, Value}] 取 head |
| Redis | inst.InstanceTags | [{TagKey, TagValue}] 取 head |
| MySQL | inst.TagList | [{TagKey, TagValue}] 取 head |
| MongoDB/PostgreSQL/CLB/NAT/VPN/EIP | 调用 tag API 补查 | 见下方兜底方案 |

**兜底方案（任何产品）**：
```
API: tag.tencentcloudapi.com/DescribeResourceTagsByResourceIds
参数: ServiceType（如 cvm/cdb/redis）, ResourcePrefix（如 instance/instanceId）, ResourceIds
返回: Tags[{TagKey, TagValue}]  取 TagKey==head 的 TagValue 即为负责人
```

报告中每条资源必须展示 head 标签（负责人），无标签时显示 "-"

---

## 计算

### CVM 云服务器

```
ProductCode: p_cvm
API: cvm.tencentcloudapi.com/DescribeInstances
Version: 2017-03-12
关注字段: InstanceId, InstanceName, InstanceType, CPU, Memory, InstanceState, InstanceChargeType, CreatedTime, Tags
分页: Offset/Limit(最大100)
ResponseListKey: InstanceSet
```

### CBS 云硬盘

```
ProductCode: p_cbs
API: cbs.tencentcloudapi.com/DescribeDisks
Version: 2017-03-12
关注字段: DiskId, DiskName, DiskSize, DiskType, DiskState, DiskChargeType, CreateTime, Tags
分页: Offset/Limit(最大100)
ResponseListKey: DiskSet
```

---

## 数据库

### MySQL

```
ProductCode: p_cdb
API: cdb.tencentcloudapi.com/DescribeDBInstances
Version: 2017-03-20
关注字段: InstanceId, InstanceName, DBEngineVersion, Memory, Volume, Cpu, Status, ChargeType, CreateTime, TagList,
         InstanceType   # F7 关键字段：1=主实例, 2=灾备实例, 3=只读实例
         MasterInfo     # 只读/灾备实例的主实例信息，含主实例 ID
分页: Offset/Limit(最大100)
ResponseListKey: Items
⚠️ Memory 字段单位 MB，需 ÷1024 换算 GB；Memory=0 时标注"规格字段异常"
⚠️ [F7 HARD] InstanceType != 1（非主实例）必须跳过闲置判定（见 analysis-rules.md 0.5）
```

### Redis

```
ProductCode: p_redis
API: redis.tencentcloudapi.com/DescribeInstances
Version: 2018-04-12
关注字段: InstanceId, InstanceName, Type, Size, Status, BillingMode, Createtime, DeadlineTime, InstanceTags,
         RedisShardNum, RedisReplicasNum   # F7 关键字段：分片数和副本数
         NodeSet                            # F7 关键字段：节点列表，含 NodeType (master/replica)
分页: Offset/Limit(最大100)
ResponseListKey: InstanceSet
⚠️ BillingMode: 0=按量, 1=包年包月（与直觉相反）
⚠️ [F7 HARD] 集群实例的副本节点（NodeType=replica）不独立判定闲置；整实例级按主节点监控数据判定
⚠️ [F7] 主从架构的备节点，若 API 返回 NodeSet 中 master 节点独立计费，则仅对 master 做闲置判定
```

### MongoDB

```
ProductCode: p_mongodb
API: mongodb.tencentcloudapi.com/DescribeDBInstances
Version: 2019-07-25
关注字段: InstanceId, InstanceName, Memory, Volume, Status, ChargeType, CreateTime,
         ClusterType       # F7 关键字段：1=副本集, 0=分片集
         ReplicaSetList    # F7 关键字段：副本集节点信息，含 Role (PRIMARY/SECONDARY)
分页: Offset/Limit(最大100)
ResponseListKey: InstanceDetails
标签: 调用 tag API 补查（ServiceType=mongodb）
⚠️ [F7] SECONDARY 节点不独立计费（同实例摊销），按整实例级判定即可；若未来 API 返回每节点独立计费则需对 SECONDARY 跳过
```

### PostgreSQL

```
ProductCode: p_postgresql
API: postgres.tencentcloudapi.com/DescribeDBInstances
Version: 2017-03-12
关注字段: DBInstanceId, DBInstanceName, DBInstanceMemory(GB), DBInstanceStorage, DBInstanceStatus, PayType, CreateTime,
         DBInstanceType    # F7 关键字段：primary / readonly / guard (灾备)
         MasterDBInstanceId  # 只读/灾备实例的主实例 ID
分页: Offset/Limit(最大100)
ResponseListKey: DBInstanceSet
标签: 调用 tag API 补查（ServiceType=postgres）
⚠️ [F7 HARD] DBInstanceType = readonly / guard 必须跳过闲置判定
```

---

## 存储

### ES（Elasticsearch）

```
ProductCode: p_es
API: es.tencentcloudapi.com/DescribeInstances
Version: 2018-04-16
关注字段: InstanceId, InstanceName, NodeNum, NodeType, EsVersion, ChargeType, CreateTime, Status
分页: Offset/Limit(最大100)
ResponseListKey: InstanceList
标签: 调用 tag API 补查（ServiceType=es）
```

### CKafka（消息队列）

```
ProductCode: p_ckafka
API: ckafka.tencentcloudapi.com/DescribeInstances
Version: 2019-08-19
关注字段: InstanceId, InstanceName, InstanceType, BandwidthIn, BandwidthOut, DiskSize, Status, TopicNum
分页: Offset/Limit(最大100)
ResponseListKey: Result.InstanceList
⚠️ 监控缺失时兜底: DescribeInstanceAttributes 查 Healthy + TopicNum
```

### CFS（文件存储）

```
ProductCode: p_cfs
API: cfs.tencentcloudapi.com/DescribeCfsFileSystems
Version: 2019-07-19
关注字段: FileSystemId, FsName, SizeLimit(GB), LifeCycleState
ResponseListKey: FileSystems
⚠️ BusinessCode=p_cfs（不是 p_cos）
```

### COS（对象存储）

```
ProductCode: p_cos
Special: bill-resource-summary   # 表驱动扫描器遇此 Special 不再走 API 分页,由账单路径驱动
资源发现（两步）：
  Step 1：调用账单 API 提取 bucket 列表
    billing.tencentcloudapi.com / DescribeBillResourceSummary
    参数： BeginTime="{yyyymm}-01 00:00:00", EndTime="{yyyymm}月末日 23:59:59"
    过滤： BusinessCode = "p_cos"
    提取字段： ResourceId（即 bucket 名，格式： {name}-{appid}）、RealTotalCost
  Step 2：对每个 bucket 查询 30 天监控（Namespace: QCE/COS）
    Dimensions: [{"Name": "appid", "Value": "{appid}"}, {"Name": "bucket", "Value": "{bucket_name}"}]
    指标： GetRequests（GET 请求次数）、InternetTraffic（外网下行流量）
    appid 取自 bucket 名后缀数字部分，如 custom-map-data-1330453250 → appid=1330453250
判定规则：
  如 30 天内 GetRequests 均均 < 100，且费用 ≥ ¥1000/月 → 建议降档为低频存储
```

---

## 网络

### CLB 负载均衡

```
ProductCode: p_clb
Special: two-stage-targets   # 表驱动扫描器遇此 Special → 主列表后对每个 LB 串行 DescribeTargets
API: clb.tencentcloudapi.com/DescribeLoadBalancers
Version: 2018-03-17
关注字段: LoadBalancerId, LoadBalancerName, LoadBalancerType, ChargeType
ResponseListKey: LoadBalancerSet
⚠️ API 不返回 BackendCount，需通过 DescribeTargets 逐一查询后端绑定情况
⚠️ [HARD] DescribeTargets 参数名为 `LoadBalancerId`（**单数**），不是 `LoadBalancerIds`（复数）。传 LoadBalancerIds 会返回 MissingParameter 错误，导致误判为无后端
```

### EIP 弹性公网IP

```
ProductCode: p_eip
API: vpc.tencentcloudapi.com/DescribeAddresses
Version: 2017-03-12
关注字段: AddressId, AddressIp, AddressStatus, AddressType, CreatedTime
ResponseListKey: AddressSet
```

### NAT 网关

```
ProductCode: p_nat
API: vpc.tencentcloudapi.com/DescribeNatGateways
Version: 2017-03-12
关注字段: NatGatewayId, NatGatewayName, InternetMaxBandwidthOut, CreatedTime
ResponseListKey: NatGatewaySet
```

### VPN 网关

```
ProductCode: p_vpn
API: vpc.tencentcloudapi.com/DescribeVpnGateways
Version: 2017-03-12
关注字段: VpnGatewayId, VpnGatewayName, Type, InstanceChargeType, CreatedTime
ResponseListKey: VpnGatewaySet
```

---

## 专线

### DC（Direct Connect）

```
ProductCode: p_directconnect
API: dc.tencentcloudapi.com/DescribeDirectConnects
Version: 2018-04-10
关注字段: DirectConnectId, DirectConnectName, Bandwidth(Mbps), State
ResponseListKey: DirectConnectSet
⚠️ 只扫 State=AVAILABLE 的专线，BUILDING 跳过
```

---

## 大数据

### EMR（弹性MapReduce）

```
ProductCode: p_emr
API: emr.tencentcloudapi.com/DescribeInstances
Version: 2019-01-03
关注字段: Id, ClusterName, Status, ChargeType, CreateTime
ResponseListKey: ClusterList
```

---

## 容器

### TKE（容器服务,托管集群）

**两路并存扫描**:
- **路径 A(保留)**:通过 CVM API 识别 `as-tke-*` 前缀实例,按 CVM 节点级规则判定停机/闲置/低利用率(`analysis-rules.md` §5.1)
- **路径 B(新增)**:直接扫集群级资源,判定整个集群的 CPU/内存利用率(`analysis-rules.md` §5.9)

```
ProductCode: p_011691,p_eks   # TKE p_011691(数字编码), EKS p_eks,同一 API 一起扫
API: tke.tencentcloudapi.com/DescribeClusters
Version: 2018-05-25
关注字段: ClusterId, ClusterName, ClusterStatus, ClusterType, ClusterNodeNum,
         ClusterChargeType, CreatedTime, TagSpecification
分页: Offset/Limit(最大100)
ResponseListKey: Clusters
⚠️ ClusterType = "INDEPENDENT"(独立集群,用户自管 master) / "MANAGED_CLUSTER"(托管) / "EKS"(Serverless)
⚠️ ClusterStatus ≠ "Running" 的集群跳过监控采集,但在报告中标注状态异常
⚠️ 去重:路径 A 扫出的 CVM 节点若其 ClusterId 已在路径 B 的集群列表中,**只保留集群级判定**,不再重复 CVM 节点级判定(避免双重计费)
```

### EKS（Serverless 容器,TKE 子类型)

**合入 TKE 同一 API,通过 ClusterType 过滤**,不另行扫描(ProductCode p_tke_serverless 已并入 TKE 行):

```
过滤条件: Cluster.ClusterType == "EKS"
关注字段: 同 TKE;额外取 Pod 数(通过 DescribeClusterInstances 或 K8s API,可选)
⚠️ EKS 集群级监控受限(无整集群 CPU/内存聚合),仅做"空集群清理"判定(Pod 数=0 → 释放建议)
⚠️ 不做利用率判定;利用率判定需 Pod 级监控遍历,本 skill 不支持
```

---

## 消息队列

### TDMQ Pulsar

**两段扫描**:
- Step 1: 获取集群列表 + 环境(namespace)
- Step 2: 按集群 × 环境遍历 topic

```
ProductCode: p_tdmq,p_rocketmq,p_pulsar_pro   # TDMQ 家族,三个子类型
Special: tdmq-three-flavors   # 表驱动扫描器遇此 Special → 依次调 3 个 action

API 1: tdmq.tencentcloudapi.com/DescribeClusters           # Pulsar 普通集群
Version: 2020-02-17
ResponseListKey: ClusterSet

API 2: tdmq.tencentcloudapi.com/DescribeRocketMQClusters   # RocketMQ
Version: 2020-02-17
ResponseListKey: ClusterList

API 3: tdmq.tencentcloudapi.com/DescribePulsarProInstances # Pulsar Pro
Version: 2020-02-17
ResponseListKey: Instances

可选后续(按需):
API: tdmq.tencentcloudapi.com/DescribeEnvironments  Payload: {ClusterId}
API: tdmq.tencentcloudapi.com/DescribeTopics        Payload: {ClusterId, EnvironmentId}

分页: Offset/Limit(最大100)
⚠️ TDMQ 监控维度是 topic 级(api-monitor.md §TDMQ),必须完整遍历 cluster × environment × topic 后采监控
⚠️ 无 topic 的 cluster 可直接判定为"可清理",无需采监控
```

---

## 向量数据库 VectorDB

```
ProductCode: p_vectormind
API: vdb.tencentcloudapi.com/DescribeInstances
Version: 2023-06-16
关注字段: InstanceId, InstanceName, Status, ChargeType, CreatedTime, TagList
分页: Offset/Limit(最大100)
ResponseListKey: InstanceList
⚠️ 监控 Namespace=QCE/VECDB,Dimensions AppId(大写 A) + instance_id(见 api-monitor.md)
⚠️ API Version 可能随 SDK 更新变化,unauthorized 时归入"其他产品"只列费用
```

---

## TCHouse-D (原云数仓 Doris)

```
ProductCode: p_cdwdoris
API: cdwdoris.tencentcloudapi.com/DescribeInstancesV3
Version: 2021-12-28
关注字段: InstanceId, InstanceName, Status, ChargeType, CreateTime,
         BeNodeCount, FeNodeCount   # 节点数,用于推算监控维度数
分页: Offset/Limit(最大100)
ResponseListKey: InstancesList

节点级信息(可选,用于节点粒度监控):
API: cdwdoris.tencentcloudapi.com/DescribeInstanceNodesInfo
Payload: {InstanceId}
关注字段: NodeList[].Ip, NodeList[].NodeRole(BE/FE)

⚠️ 监控 Namespace=QCE/CDWDRS,维度是节点级(clusterid + ip);实例级无聚合指标
⚠️ 判定时按"多数 BE 节点 CPU 低"聚合;FE 节点只承担元数据,不参与闲置判定
```

---

## 数据库 - 扩展

### TDSQL-C / CynosDB

```
ProductCode: p_cynosdb
API: cynosdb.tencentcloudapi.com/DescribeInstances
Version: 2019-01-07
关注字段: InstanceId, InstanceName, Status, DbType, ChargeType, CreateTime,
         Cpu, Memory(GB), StoragePayMode
分页: Offset/Limit(最大100)
ResponseListKey: InstanceSet
⚠️ 监控 Namespace=QCE/CYNOSDB (**推测**,见 api-monitor.md 推测脚注);unauthorized 时归入"其他产品"
⚠️ 判定规则复用 MySQL §5.3(CPU/内存/连接数三指标),DbType=MYSQL 时完全复用;DbType=POSTGRESQL 时参考 PostgreSQL 规则
```

### TDSQL (分布式数据库)

```
ProductCode: p_dcdb
API: dcdb.tencentcloudapi.com/DescribeDCDBInstances
Version: 2018-04-11
关注字段: InstanceId, InstanceName, Status, ChargeType, CreateTime, Cpu, Memory(GB)
分页: Offset/Limit(最大100)
ResponseListKey: Instances
⚠️ 监控 Namespace=QCE/TDMYSQL (**推测**);unauthorized 时归入"其他产品"
⚠️ 判定规则复用 MySQL §5.3,分片架构下按 Shard 粒度采监控
```

### MariaDB

```
ProductCode: p_mariadb
API: mariadb.tencentcloudapi.com/DescribeDBInstances
Version: 2017-03-12
关注字段: InstanceId, InstanceName, Status, ChargeType, CreateTime, Memory, Storage
分页: Offset/Limit(最大100)
ResponseListKey: Instances
⚠️ 监控 Namespace=QCE/MARIADB (**推测**);unauthorized 时归入"其他产品"
⚠️ 判定规则完全复用 MySQL §5.3
```

---

## 网络 - 扩展

### CCN 云联网

```
ProductCode: p_ccn
API: vpc.tencentcloudapi.com/DescribeCcns
Version: 2017-03-12
关注字段: CcnId, CcnName, InstanceChargeType, CreateTime, InstanceCount,
         QosLevel(PT/AU/AG 金银铜)
分页: Offset/Limit(最大100)
ResponseListKey: CcnSet

附加 API(可选,判定无流量时使用):
API: vpc.tencentcloudapi.com/DescribeCcnAttachedInstances
Payload: {CcnId}
关注字段: InstanceCount(关联实例数)

⚠️ 监控 Namespace=QCE/CCN (**推测**);unauthorized 时归入"其他产品"
⚠️ 无关联实例(InstanceCount=0)可直接判定"可释放"
```

---

## 流计算

### Oceanus 流计算

```
ProductCode: p_oceanus
API: oceanus.tencentcloudapi.com/DescribeClusters
Version: 2019-04-22
关注字段: ClusterId, Name, Status, ChargeType, CreateTime, CuNum(CU 数)
ResponseListKey: ClusterSet

附加 API(作业级,可选):
API: oceanus.tencentcloudapi.com/DescribeJobs
Payload: {ClusterId}
关注字段: JobId, Status(1=未启动 / 2=运行中 / 3=已停止 / 等)

⚠️ 监控 Namespace=QCE/OCEANUS (**推测**);unauthorized 时归入"其他产品"只列费用
⚠️ 判定口径:集群长期无运行中作业(全部 Status≠2)→ 可关停;CU 数远超实际作业并发 → 可缩容
⚠️ 本产品判定规则定制度高,详见 analysis-rules.md §5.14
```

---

## 日志服务 CLS

```
ProductCode: p_cls
API: cls.tencentcloudapi.com/DescribeLogsets
Version: 2020-10-16
关注字段: LogsetId, LogsetName, AssumerName, CreateTime
分页: Offset/Limit(最大100)
ResponseListKey: Logsets

附加 API(主题级,可选,用于无写入流量判定):
API: cls.tencentcloudapi.com/DescribeTopics
Payload: {Filters: [{Key: "logsetId", Values: [LogsetId]}]}
关注字段: TopicId, TopicName, Status, StorageType, MaxSplitPartitions

⚠️ 监控 Namespace=QCE/CLS,Dimensions topicId;指标 traffic_write(写入流量)
⚠️ 判定口径:30 天 traffic_write=0 且 logset 内所有 topic 均无新数据 → 可清理(费用 ≥¥100/月才提醒)
⚠️ StorageType=HOT 但无写入 → 建议改 STANDARD 冷存储
```

---

## 密钥管理 KMS

```
ProductCode: p_kms
API: kms.tencentcloudapi.com/ListKeys
Version: 2019-01-18
关注字段: KeyId
分页: Offset/Limit(最大100)
ResponseListKey: Keys

附加 API(密钥详情,判定时按需):
API: kms.tencentcloudapi.com/DescribeKey
Payload: {KeyId}
关注字段: KeyState(Enabled/Disabled/PendingDelete/Archived), KeyUsage, NextRotateTime

⚠️ KMS 按调用次数 + 启用密钥数计费,无监控 namespace,判定走密钥状态
⚠️ 判定口径:KeyState=Disabled 超过 30 天 → 建议彻底删除(收回每月 ¥1/key 闲置费);Archived 状态可释放
⚠️ KeyUsage=ENCRYPT_DECRYPT 但 30 天 0 调用 → 提醒梳理(需走云审计交叉验证,本 skill 不实现)
```

---

## 扫描执行规则

1. 所有产品 **并行采集**，不串行等待
2. 某产品 API 无权限（UnauthorizedOperation）→ 跳过并在报告中标注"权限不足，跳过"
3. 某产品无资源（返回空列表）→ 报告中标注"✅ 无资源"
4. 不允许因某产品失败而中止整体巡检
5. 过滤创建时长 < 24 小时的资源

> **闲置判定/降配/优化策略规则见 `analysis-rules.md`。**
