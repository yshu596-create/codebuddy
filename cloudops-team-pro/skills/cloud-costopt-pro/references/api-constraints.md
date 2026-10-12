# 腾讯云 API 通用约束

> 本文件由 SKILL.md 引用，包含签名规则、限流、错误处理、地域/产品码映射表、账单约束。

---

## 一、通用规则

- 签名方式：TC3-HMAC-SHA256（见 lib/sign.py）
- 请求方式：POST，Content-Type: application/json
- 限流：每账户每秒最多 20 次请求；超限返回 RequestLimitExceeded，指数退避重试（1s/2s/4s）
- 重试：RequestLimitExceeded / InternalError 可重试，最多 3 次；AuthFailure / UnauthorizedOperation 不重试
- 权限不足（UnauthorizedOperation）：跳过该产品，报告标注"权限不足，跳过"

---

## 二、账单 API 约束（仅用于确定扫描范围）

```
host: billing.tencentcloudapi.com
version: 2018-07-09
```

> 巡检 Skill 只用账单汇总 API 决定**扫哪些产品 / 扫哪些地域**，不做账单分析。
> 资源明细 / 标签分账 / 环比相关 API 由 billing Skill 负责，本 Skill 不调用。

| API | 时间参数 | 说明 |
|---|---|---|
| DescribeBillSummaryByProduct | BeginTime/EndTime | 格式 "YYYY-MM-DD HH:MM:SS"，不得用 Month |
| DescribeBillSummaryByRegion  | BeginTime/EndTime | 同上 |

---

## 三、监控 API 约束

```
host: monitor.tencentcloudapi.com
action: GetMonitorData
```

参数格式（⚠️ 必须用 Instances，不是 Dimensions）：
```json
{
  "Namespace": "QCE/CVM",
  "MetricName": "CPUUsage",
  "Period": 3600,
  "StartTime": "2026-03-01T00:00:00+08:00",
  "EndTime": "2026-03-31T23:59:59+08:00",
  "Instances": [{"Dimensions": [{"Name": "InstanceId", "Value": "ins-xxx"}]}]
}
```

- 时间范围：单次最多 31 天，最多 1440 个数据点
- Period：3600（小时粒度，30天≈720点）

---

## 四、地域名称映射表（账单 RegionName → API 地域码）

> ⚠️ **优先用 RegionId(数字)直接查映射,不要用 RegionName**:账单 API 返回的 `RegionName` 含全角括号(如 `华东地区（上海）`),与本表中的半角括号写法不同,字符串匹配会失败。

### 4.1 RegionId(数字) → API 地域码(权威源,首选)

| RegionId | API 地域码 | 中文名 |
|---:|---|---|
| 1 | ap-guangzhou | 华南地区(广州) |
| 4 | ap-shanghai | 华东地区(上海) |
| 5 | ap-hongkong | 港澳台地区(中国香港) |
| 7 | ap-shanghai-fsi | 华东地区(上海金融) |
| 8 | ap-beijing | 华北地区(北京) |
| 9 | ap-singapore | 亚太东南(新加坡) |
| 11 | ap-shenzhen-fsi | 华南地区(深圳金融) |
| 15 | na-siliconvalley | 美国西部(硅谷) |
| 16 | ap-chengdu | 西南地区(成都) |
| 17 | eu-frankfurt | 欧洲地区(法兰克福) |
| 18 | ap-seoul | 亚太东北(首尔) |
| 19 | ap-chongqing | 西南地区(重庆) |
| 21 | ap-mumbai | 亚太南部(孟买) |
| 22 | na-ashburn | 美国东部(弗吉尼亚) |
| 23 | ap-bangkok | 亚太东南(曼谷) |
| 25 | ap-tokyo | 亚太东北(东京) |
| 33 | ap-beijing-fsi | 华北地区(北京金融) |
| 39 | ap-jakarta | 亚太东南(雅加达) |
| 46 | ap-nanjing | 华东地区(南京) |
| 47 | ap-shanghai-fsi | 金融区(上海) |
| 72 | sa-saopaulo | 南美地区(圣保罗) |
| 78 | ap-shanghai-adc | 上海自动驾驶云 |
| 102 | ap-singapore | 亚太东南(新加坡)(混部) |
| 9999 | ap-other | 其他/未识别地域 |

> RegionId=`9999` 或表中未列编号 → 跳过资源 API 扫描,只在报告里标 `非资源地域`(常见于 CDN/全球加速等无 region 概念的产品)

### 4.2 RegionName(中文) → API 地域码(兜底,字符串可能含全角括号)

| 账单 RegionName | API 地域码 |
|---|---|
| 华北地区(北京) | ap-beijing |
| 华东地区(上海) | ap-shanghai |
| 华南地区(广州) | ap-guangzhou |
| 西南地区(成都) | ap-chengdu |
| 西南地区(重庆) | ap-chongqing |
| 华东地区(南京) | ap-nanjing |
| 港澳台地区(中国香港) | ap-hongkong |
| 亚太东南(新加坡) | ap-singapore |
| 亚太东南(曼谷) | ap-bangkok |
| 亚太南部(孟买) | ap-mumbai |
| 亚太东北(首尔) | ap-seoul |
| 亚太东北(东京) | ap-tokyo |
| 美国东部(弗吉尼亚) | na-ashburn |
| 美国西部(硅谷) | na-siliconvalley |
| 欧洲地区(法兰克福) | eu-frankfurt |
| 上海自动驾驶云 | ap-shanghai-adc |
| （未识别）| 原样保留,报告中标注 |

---

## 五、产品码映射表（账单 BusinessCode → 扫描模块）

| BusinessCode | 产品 | 扫描模块 | 闲置判断 |
|---|---|---|---|
| p_cvm | 云服务器 | CVM | CPU均值+内存均值双指标 |
| p_cbs | 云硬盘 | CBS | DiskState=UNATTACHED |
| p_cdb | 云数据库MySQL | MySQL | CPU+连接使用率+内存使用率 |
| p_redis | 云数据库Redis | Redis | 内存使用率+连接数 |
| p_cmongo | 云数据库MongoDB | MongoDB | CPU+QPS+连接数三指标 |
| p_postgres | 云数据库PostgreSQL | PostgreSQL | CPU+内存率+连接数 |
| p_clb | 负载均衡 | CLB | DescribeTargets 无绑定 RS |
| p_eip | 弹性公网IP | EIP | AddressStatus=UNBIND |
| p_nat | NAT网关 | NAT | 出带宽均值 |
| p_vpngw | VPN网关 | VPN | VPN出带宽均值 |
| p_cos | 对象存储COS | COS | 存储量+请求次数 |
| p_cfs | 文件存储CFS | CFS | 读写IO双指标 |
| p_es | Elasticsearch | ES | CpuUsageAvg+JvmMemUsageAvg |
| p_ckafka | 消息队列CKafka | CKafka | ProFlow+ConFlow |
| p_directconnect | 专线接入 | DC | 带宽利用率（仅参考，不建议退订） |
| p_tke / p_011691 | 容器服务TKE | 通过CVM扫 as-tke- 前缀 | 同CVM |
| 其他（p_tdmysql / p_cynosdb / p_tcr / p_lighthouse / p_eks / p_tdmq 等） | 各产品 | **通用兜底** | 归入"其他产品"节，只报费用不做闲置判定，不算入节省金额 |

---

## 六、高频错误预防（巡检场景）

| 场景 | 正确做法 |
|---|---|
| 扫描范围 | 产品列表与地域列表全部来自账单 API，禁止硬编码 |
| 账单汇总 API | 用 BeginTime/EndTime，不用 Month |
| 监控数据采集 | 30 天窗口（Period=3600）；CVM 必须同时采集 CPUUsage 和 MemUsage |
| 监控覆盖率 | 多指标产品取**最小**覆盖率；<50% 不做闲置判定 |
| 关键资源保护 | MySQL 灾备/只读、Redis 副本、MongoDB SECONDARY、`finops-critical` 标签的实例**禁止**进入闲置判定 |
| 豁免标签 | `finops-skip` 支持 true/downgrade/relax/分产品/带过期，按 analysis-rules §0.1 流程判定 |
| 风险等级 | 每条建议必须 `[低/中/高] · 可逆性 · 执行时机` 完整三段 |
| 预计月省 | 必须 `baseline × ratio%`，禁止凭空估算；baseline 取自资源级账单 |
| AK/SK 处理 | 只从 shell 环境变量 `TENCENTCLOUD_SECRET_ID/KEY` 读取，运行期只在内存使用，禁止写入任何文件 |
