# advisor 成本组覆盖参考(吸收 cloudq 闲置/成本能力,2026-05-21)

> 本文件是 Step 1.5 `scripts/scan_advisor.py` 的判定指南。
> 严重度映射、product 映射、repair_url 抽取规则的唯一权威源。
> scan_advisor.py 只抓数据,**不做严重度判定**;LLM 在 Step 3 按本文表格判定。

---

## §0 为什么吸收 advisor

cloudq skill 提供的"巡检/成本优化建议/最佳实践"等能力,在腾讯云 advisor 产品中已沉淀为 450 个官方策略。
其中 `GroupName="成本"` 22 个 strategy 覆盖 15 个产品,**与 costopt 现有 17 产品有 14 个交集 + 1 个独家(LH 轻量应用服务器)**。

吸收后 costopt 的能力补强:
- ✅ 官方策略口径(腾讯云权威结论,客户认账度高)
- ✅ 官方修复链接 repair_url(直接给客户文档导引)
- ✅ 双源交叉验证(advisor 命中且 costopt 也命中 → 高置信)
- ✅ Step 2 监控提速(advisor 已命中的资源跳过本地监控采集,节省 35-40%)
- ✅ LH 独家补全(costopt 不直扫,advisor 给出建议)

cloudq 不在本次删除;等 inspector(已完成) + costopt(本次) + billing(后续) 三方都收编完成后统一删除。

---

## §1 22 个成本组 strategy 元数据快照

| StrategyId | Name | advisor Product | costopt ProductCode | 备注 | pricing-rules 路径 | ratio% |
|------------|------|------------------|---------------------|------|--------------------|--------|
| 38   | 负载均衡（CLB）实例闲置                       | clb        | p_clb       | 闲置        | CLB 无后端释放    | 100% |
| 39   | 云服务器（CVM）实例利用率不足                  | cvm        | p_cvm       | 低利用率     | CVM Right-sizing 降配 1/2 | 50% |
| 40   | 弹性公网 IP（EIP）闲置                        | eip        | p_eip       | 未绑定       | EIP 未绑定释放    | 100% |
| 41   | 云硬盘（CBS）利用率不足                       | cbs        | p_cbs       | 低 IO        | CBS SSD→高效云盘  | 50% |
| 61   | 对象存储（COS）存储桶未配置碎片清理规则        | cos        | p_cos       | 碎片清理     | 需评审(无固定 ratio) | - |
| 91   | 云服务器（CVM）计费模式不合理                 | cvm        | p_cvm       | 按量转包     | CVM 按量转包年    | 40% |
| 141  | 云数据库（MySQL）利用率不足                   | mysql      | p_cdb       | 闲置/降配   | MySQL 降配 1/2    | 40% |
| 142  | 云数据库（MariaDB）利用率不足                 | tdsql      | p_mariadb   | 闲置        | MySQL 降配 1/2    | 40% |
| 143  | 云数据库（MongoDB）利用率不足                 | mongodb    | p_mongodb   | 闲置        | MongoDB 降配      | 30% |
| 162  | 云数据库（Redis）利用率不足                   | redis      | p_redis     | 内存低       | Redis 降内存规格  | 40% |
| 238  | 负载均衡（CLB）低利用率                       | clb        | p_clb       | 流量低       | CLB 无后端释放    | 100% |
| 2003 | 私有网络公网 NAT 网关闲置                     | nat        | p_nat       | 出带宽低     | NAT 带宽降档      | 30% |
| 2016 | 对象存储（COS）存储桶未启用数据降冷            | cos        | p_cos       | 生命周期     | COS 标准→低频     | 32% |
| 4013 | 云原生数据库 TDSQL-C MySQL 版利用率不足        | cynosdb    | p_cynosdb   | 闲置        | MySQL 降配 1/2    | 40% |
| 4161 | Elasticsearch 集群利用率不足                  | Elasticsearch Service | p_es | CPU/JVM 低 | ES 降配          | 35% |
| 5030 | 负载均衡（CLB）带宽利用率不足                 | clb        | p_clb       | 带宽降档     | CLB 无后端释放    | 100% |
| 5140 | 负载均衡（CLB）QPS 利用率过低                 | clb        | p_clb       | QPS 低       | CLB 无后端释放    | 100% |
| 6026 | 文件存储（CFS）未充分利用                     | cfs        | p_cfs       | 无 IO        | CFS 近无IO释放    | 100% |
| 7302 | 轻量应用服务器（LH）实例低使用率              | lh         | **独家**    | costopt 未扫 | 需评审(无 baseline) | - |
| 7400 | 弹性公网IP（EIP）带宽利用率不足               | eip        | p_eip       | 带宽降档     | EIP 未绑定释放    | 100% |
| 300153 | 云服务器（CVM）实例磁盘利用率过低           | cvm        | p_cvm       | 磁盘低       | CBS SSD→高效云盘  | 50% |
| 300158 | 弹性 MapReduce (EMR on CVM) 集群资源洞察异常 | emr        | p_emr       | 节点优化     | EMR 低利用率降配  | 40% |

⚠️ 元数据快照仅供 LLM 反查,**不做缓存**(scan_advisor.py 每次现拉 DescribeStrategies)。
⚠️ 如 advisor 后续新增/调整成本组策略,本表需手动更新;StrategyId 不在本表中的命中视为"未识别成本策略",报告中标注 ⚠️ 提示扩表。

---

## §2 严重度映射(LLM 在 Step 3 按本表判定)

advisor `Level` 字段(string,scan_advisor.py 已转 int)+ `RiskDays` 字段联合判定:

| 条件 | costopt 严重度 | 含义 |
|------|----------------|------|
| `level >= 3`                          | 🔴 立即处理 | advisor 标"高"档,无论天数 |
| `level == 2` 且 `risk_days > 30`       | 🟠 建议处理 | 中档持续超月 |
| `level == 2` 且 `risk_days <= 30`      | 🟡 关注观察 | 中档短期 |
| `level == 1` 或 其他                   | 🟢 仅记录   | 低档/无信息,只列在统计不进建议表 |

⚠️ 与 inspector `references/14-advisor-coverage.md §2` 完全同表;两个 skill 通用判定标准。

---

## §3 product 映射策略(Step 2/3 资源去重)

LLM 在 Step 3 把 advisor 的 hit 与 costopt 自扫资源对齐时,按 `product_key`(advisor Product 字段)→ `ProductCode` 映射:

```
clb     → p_clb        (单独 p_clb,4 条 strategy 都映射这里)
cvm     → p_cvm        (4 条 strategy)
eip     → p_eip        (2 条)
cbs     → p_cbs
cos     → p_cos        (2 条)
mysql   → p_cdb        # ⚠️ MySQL 在 costopt 用 p_cdb
tdsql   → p_mariadb    # ⚠️ advisor strategy 142 实际是 MariaDB,Product=tdsql 误导,以 ProductDesc 为准
mongodb → p_mongodb
redis   → p_redis
nat     → p_nat
cynosdb → p_cynosdb
Elasticsearch Service → p_es   # ⚠️ advisor 用全名,需 trim/normalize
cfs     → p_cfs
lh      → 【独家】     # costopt 不扫,直接以 advisor 结论出建议
emr     → p_emr
```

⚠️ Step 3 LLM 必须先按上表把 advisor `product_key` 映射到 costopt `ProductCode`,再做 instance_id 去重。
⚠️ Strategy 142(`Product=tdsql`/`ProductDesc=云数据库（MariaDB）`)以 ProductDesc 为准映射到 p_mariadb,不是 p_dcdb。

---

## §4 repair_url 抽取

scan_advisor.py 已从 `strategy.Repair`(markdown 文本)抽第一个 `[文本](url)` 链接到 `hit.repair_url`。

实测覆盖率(inspect_advisor.py Test-2):88-90%(部分 strategy 元数据 Repair 字段为空或纯文本)。

📌 **不强制 100%**:repair_url 缺失时报告该行的"处置参考"列显示 `-` 即可,不算缺陷。

---

## §5 monthly_saving_estimate 抽取

scan_advisor.py 按以下顺序提取月省金额:

1. risk 行字段 `EstimatedSaving` / `MonthlySaving` / `SavingAmount`(advisor 部分 strategy 自带)
2. strategy 元数据字段 `EstimatedSaving` / `MonthlySaving`
3. 都没有 → `0.0`(LLM 在 Step 3 用 costopt 自算 baseline×ratio% 补)

📌 **实测(2026-05-21 营销账号 100036108683 端到端验证):advisor 22 个成本 strategy 18 hits 中 `monthly_saving_estimate` 100% 返 0**(即 EstimatedSaving / MonthlySaving / SavingAmount 三字段全空)。结论:
- ❌ [HARD] **不要假设 advisor 会带金额**;按"100% 返 0,costopt 兜底"设计 Step 3,把 advisor 金额视为锦上添花
- ❌ [HARD] **不要因 advisor=0 就触发"双源月省偏差 >20% ⚠️"告警**;偏差告警前置守卫见 `analysis-rules.md §7.3` —— 仅当双源金额都 >0 才比较
- ✅ 当 advisor=0 且 costopt baseline 可拿到时,月省列直接显示 costopt 金额(无 `(advisor)`/`(costopt)` 后缀);来源列仍写 `advisor`,体现"判定权威 + 金额本地兜底"
- ✅ 当 advisor=0 且 baseline 也缺失(MongoDB/Redis/ES/LH 等账单按集群汇总场景),月省列写 `需评审`,**不写 ¥0**

---

## §6 双源合并优先级(LLM Step 3 判定指南)

参见 `analysis-rules.md §7 advisor 双源融合规则`。本文件只列字段;具体合并条款在 analysis-rules.md。

简要:
- advisor 命中 → 主结论,标 `来源: advisor`
- costopt 自判命中 + advisor 未命中 → 标 `来源: costopt`
- 双源都命中(instance_id 相同)→ 标 `来源: advisor+costopt`,严重度取最高,月省 advisor 主显
- advisor scan_status=denied → 报告头部加显式标注"advisor 不可用,以下结论仅基于本地监控",严禁静默

---

## §6.1 advisor 命中资源的「规格」字段补全(F36,2026-06-01)

advisor hit 只带 `instance_id` + `instance_name`,**不带规格信息**。Step 3 输出报告时,必须按以下优先级为每条 advisor 命中资源补全「规格」列:

1. **Step 1 `resource_discovery.json`**:按 `instance_id` 查对应产品的资源列表,取规格字段(CVM 取 `InstanceType`,MySQL 取 `Memory`/`CPU`,Redis 取 `Type`+`Size`,ES 取节点规格等,具体字段名见 `api-resource.md`)
2. **`billing_lookup.json`**:按 `instance_id` 查 `ResourceName` 字段,若含规格信息则用
3. **兜底**:写 `规格待确认`

❌ [HARD] **禁止直接写"-"或空列**。advisor 命中且本地扫描也扫到的资源(双源命中),规格必然在 resource_discovery.json 中;advisor 独家命中(LH 等)尝试从 billing_lookup.json 补。

---

## §7 输入/输出契约

### 输入(scan_advisor.py)
```
TENCENTCLOUD_SECRET_ID
TENCENTCLOUD_SECRET_KEY
INSPECTION_UIN              # 必填,用于 out_dir
COSTOPT_OUT_DIR             # 可选,默认 /tmp/costopt_{uin}
```

### 输出 `advisor_cost.json`
```json
{
  "uin": "100036108683",
  "scan_status": "ok",       // ok | partial | denied
  "strategies_total": 22,
  "hits_total": 17,
  "elapsed_s": 12.3,
  "hits": [
    {
      "strategy_id": "39",
      "strategy_name": "云服务器（CVM）实例利用率不足",
      "product": "云服务器（CVM）",
      "product_key": "cvm",
      "instance_id": "ins-xxx",
      "instance_name": "web-01",
      "region": "ap-shanghai",
      "zone": "ap-shanghai-2",
      "level": 2,
      "risk_days": 45,
      "tags": [{"TagKey": "head", "TagValue": "andy"}],
      "monthly_saving_estimate": 1200.0,
      "repair_url": "https://cloud.tencent.com/document/product/...",
      "pri_id": "p-xxx",
      "status": ""
    }
  ],
  "by_strategy": {
    "39": {
      "strategy_name": "云服务器（CVM）实例利用率不足",
      "product": "云服务器（CVM）",
      "hit_count": 5,
      "total_saving": 6000.0,
      "repair_url": "https://...",
      "level_max": 2
    }
  },
  "errors": []
}
```

### stdout summary
```json
{
  "scan_status": "ok",
  "strategies_total": 22,
  "hits_total": 17,
  "level_max": 3,
  "failed_strategies": 0,
  "total_saving_estimate": 25600.0,
  "elapsed_s": 12.3,
  "out_dir": "/tmp/costopt_100036108683"
}
```

---

## §8 失败语义(scan_status 三态)

| scan_status | 触发条件 | LLM 处理 |
|-------------|---------|---------|
| `ok`        | 全 22 strategy 拉成功 | 正常合并 advisor 结论 |
| `partial`   | 1-21 strategy 失败  | 失败的 strategy 在报告"⚠️ advisor 部分降级"提示;其他正常合并 |
| `denied`    | DescribeStrategies 失败 / 全部 22 strategy 失败 | 报告头部加 ⚠️ 红框 "advisor 完全不可用,以下结论仅基于本地监控";严禁静默 |

⚠️ HARD: scan_status=denied 不允许触发流程中断,Step 2/3 必须照常跑完(一个产品失败不影响整体)
