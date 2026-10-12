# 腾讯云 API 通用约束（账单分析专用）

> 本文件为账单分析 Skill 专用精简版，只保留账单相关的 API 约束。

---

## 一、通用规则

- 签名方式：TC3-HMAC-SHA256（见 `lib/sign.py`）
- 请求方式：POST，Content-Type: application/json
- 限流：每账户每秒最多 20 次请求；超限返回 RequestLimitExceeded，指数退避重试（1s/2s/4s）
- 重试：RequestLimitExceeded / InternalError 可重试，最多 3 次；AuthFailure / UnauthorizedOperation 不重试

---

## 二、账单 API 约束

```
host: billing.tencentcloudapi.com
version: 2018-07-09
```

| API | 时间参数 | 说明 |
|---|---|---|
| DescribeBillSummaryByProduct | BeginTime/EndTime | 格式 "YYYY-MM-DD HH:MM:SS"，不得用 Month |
| DescribeBillSummaryByRegion  | BeginTime/EndTime | 同上 |
| DescribeBillSummaryByTag     | BeginTime/EndTime | 同上 |
| DescribeTagKeys              | 无时间参数 | Limit/Offset 分页；返回 `Tags`(标签键列表) + `TotalCount` |
| DescribeBillResourceSummary  | **Month** | 格式 "YYYY-MM"，**不得用** BeginTime/EndTime |
| DescribeBillDetail           | Month 或 BeginTime/EndTime | 分页，每页最多 100 条;可用 ResourceId / BusinessCode 过滤 |

**DescribeBillResourceSummary 特殊说明：**
- 必须用 `Month: "YYYY-MM"` 参数
- 支持 `BusinessCode` 过滤单个产品
- 分页字段：`Limit`（最大100）、`Offset`，循环直到取完
- `Total` 字段可能为 `None`，不能用于判断结束；以返回批次为空（`ResourceSummarySet` 长度为 0）作为终止条件
- 返回字段包含 `PayModeName`(如「包年包月」/「按量计费」)，用于 F34 计费明细判定

**DescribeBillDetail 补充说明（F34 计费明细）：**
- 用于文档版「计费明细」展开，仅对按需产品的增长资源调用
- ❌ [HARD] **禁止用 `BusinessCode` 按产品批量拉取**（F35）：TKE/EKS 等产品单产品明细可达 5000+ 行，分页拉取极易超时。实测：TKE `BusinessCode=p_011691` 拉取 20s 仍未完成（>5400 行）
- ✅ [MUST] **必须用 `ResourceId` 过滤逐资源拉取**：每个资源约 744 行/2-3秒，可控且稳定
- 只拉增长额 Top N 资源（每产品 ≤12 个），增长额 < ¥10 的可跳过
- 返回 `DetailSet[].ComponentSet`：包含 `ItemCodeName`(计费项)、`RealTotalCost`(费用)、`UsedAmount`/`UsedAmountUnit`(用量)
- 分页：`Limit`(100) + `Offset`，以返回为空终止
- 并行拉取多个资源时并发度 ≤5，避免 RequestLimitExceeded

---

## 二-b、资源标签 API 约束（动态标签列）

```
host: tag.tencentcloudapi.com
version: 2018-08-13
```

| API | 说明 |
|---|---|
| DescribeTagKeys | 列出账户下所有标签键，返回 `{Tags: [key1, key2, ...], TotalCount}` |
| DescribeResourceTagsByResourceIds | 按 ResourceId 批量查询资源标签 |

**标签发现流程（Step 1）：**

1. 调用 `DescribeTagKeys` 获取所有标签键列表
2. 过滤掉系统标签（`tencentcloud:*`、`tke-*`、`tke-*`）和测试标签（`test`）
3. 对每个候选标签键，调用 `DescribeBillSummaryByTag` 验证是否有账单分账数据
4. 只保留 `SummaryOverview` 非空的标签键作为**有效标签键**
5. ❌ **禁止硬编码标签键**（如 `customer`/`head`）— 不同账户的有效标签键不同（TSP 只有 `business`，营销有 `customer`+`head`）

**资源标签查询（Step 2c）：**

- `ServiceType`(必填)：从 `DescribeBillResourceSummary` 的 `BusinessCode` 推导，常见映射:
  | BusinessCode | ServiceType |
  |---|---|
  | p_cvm | cvm |
  | p_cos | cos |
  | p_cbs | cbs |
  | p_clb | clb |
  | p_cdb | cdb(MySQL) |
  | p_redis | redis |
  | p_mongodb | mongodb |
  | p_tke | tke |
  | p_eks | eks |
  | p_cfs | cfs |
  | p_nat | nat |
  | p_vpngw | vpngw |
  | p_eip | eip |
  | p_cls | cls |
  | p_ccn | ccn |
  | 其他 | 去掉 `p_` 前缀尝试;失败则降级为「未打标」 |
- `ResourceRegion`(必填)：从资源的地域信息推导，如 `ap-shanghai`
- `ResourceIds`(必填)：数组，每批最多 **50** 个
- 返回 `Tags[].TagKey` / `Tags[].TagValue`，提取**Step 1 发现的所有有效标签键**
- API 报错时降级为全部显示「未打标」，不中止流程

---

## 三、地域名称映射表（账单 RegionName → API 地域码）

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
| （未识别）| 原样保留，报告中标注 |

---

## 四、高频错误预防

| 场景 | 正确做法 |
|---|---|
| 账单汇总 API | 用 BeginTime/EndTime，不用 Month |
| DescribeBillResourceSummary | 用 Month 参数，不得用 BeginTime/EndTime |
| 环比计算 | 必须用代码/AI推理计算，不得手动归类；上涨/下降两表严格独立 |
| 两张环比表格断开 | 必须用 `---` + `####` 标题隔断，禁止只用加粗文字 |
| 标签分账 | 无数据的 TagKey 不展示；proj 标签不展示；每个 TagKey 独立节；**必须双调用分析月+对照月（F19）** |
| 费用概览 | 只展示实际总费用和原价，不展示激励抵扣/现金支付等细项 |
| 折扣分析 | 不单独展示折扣分析节；无折扣产品进智能洞察🟡关注节 |
| 实例增量明细门槛 | 环比 > 15% **且**增量 > ¥200 才展开；主因实例占比 ≥ 10% 才逐行，其余合并 |
| 金额精度 | 展示整数（F20），中间计算 4 位小数，差额用 API 值直接相减（F2） |
| 包年包月识别 | 必须用 PayMode 字段（F4），禁止用"金额一致"等启发式 |

---

## 五、环比精度口径（F2）

⚠️ [MUST] 环比差额的计算口径必须严格统一：

1. **差额来源**：直接用两个账单 API 返回的 `RealTotalCost` 相减，不做中间二次聚合
   ```
   diff = curr_month.RealTotalCost - prev_month.RealTotalCost
   ```
2. **精度保留**：中间计算保留 **4 位小数**（防止浮点误差累积）
3. **展示层 round**：最终展示时对差额做 `round(diff)` 得到整数（F20）
4. ❌ [HARD] **禁止**先把两个月的数字分别 round 到整数再相减（会产生累积偏差）

### 正反例

**实测场景**：CVM 03 月 ¥23,007.09，02 月 ¥22,434.70
- ✅ 正确：`diff = 23007.09 - 22434.70 = 572.39` → 展示 `¥572`
- ❌ 错误 1：`round(23007.09) - round(22434.70) = 23007 - 22435 = 572`（结果碰巧对，但口径错）
- ❌ 错误 2：把 572.39 显示为 `¥572.40`（历史 bug，四舍五入方向不一致导致偏差 ¥0.01）

### 环比比例

```
ratio = diff / prev_month.RealTotalCost
展示：保留 1 位小数，如 +2.6% / -1.5%
```

- 当 `prev_month.RealTotalCost < 1` 时，显示「近新增」而非百分比（避免除数过小引起的虚高）

---

## 六、包年包月识别（F4）

❌ [HARD] 账单 API `DescribeBillSummaryByProduct` 的返回字段中包含 **PayMode**，必须用此字段判定计费模式：

| PayMode 值 | 含义 | 环比列展示 |
|-----------|------|-----------|
| `PREPAID` | 包年包月（预付费） | 「— 包年包月」 |
| `POSTPAID_BY_HOUR` | 按量计费 | 正常环比计算 |
| `RI_FEE` | 预留实例费用 | 「— 包年包月」（同类长约产品） |
| `SP_FEE` | 节省计划 | 「— 包年包月」 |

**异常情况**：
- ⚠️ [MUST] 产品汇总级 `DescribeBillSummaryByProduct` 的 `PayMode` 字段可能为空字符串（已确认复现）→ **回退到资源级 `PayModeName` 字段**（`DescribeBillResourceSummary` 返回的 `PayModeName` 字段始终有值，如「包年包月」/「按量计费」）
- ⚠️ [MUST] 同一产品不同资源可能有不同 PayMode（如部分按量 + 部分包月）→ 按账单汇总的主 PayMode 判定；若混合比例 > 10% 则展示「— 混合计费」
- ⚠️ [MUST] 专线接入等产品可能显示为「按量计费」但实际为包年包月合同（金额每月固定）→ 当 PayModeName=按量计费 但两月金额完全一致且金额较大（>¥1000）时，环比列应标注「— 合同固定费」而非「— 持平」

### 正反例

**实测场景**：专线接入 dc-335lgz59 两月均 ¥35,000

- ✅ API 返回 `PayMode: "PREPAID"` → 展示「— 包年包月」
- ❌ 历史 bug：基于"金额两月一致"启发式判定 → 展示「一持平」（既错过了"包年包月"的正确语义，又触发了 F3 持平规则但显示异常字符「一」）

---

## 七、标签分账对照月双调用（F19）

⚠️ [MUST] 每个有效 TagKey 必须调用 **2 次** `DescribeBillSummaryByTag`（分析月 + 对照月），用于计算环比：

```
# 分析月
billing / DescribeBillSummaryByTag  {BeginTime, EndTime, TagKey}

# 对照月
billing / DescribeBillSummaryByTag  {对照月BeginTime, 对照月EndTime, TagKey}
```

**合并逻辑**：
1. 按 `TagValue` 对齐两月数据
2. 计算每个 TagValue 的 `diff` 和 `ratio`（遵循第五章口径）
3. **无对照月数据的 TagValue**（本月新出现）→ 差额列显示「— 新增」，环比%列显示「-」
4. **无分析月数据的 TagValue**（本月消失）→ 默认不展示；若金额 > ¥1000 可用 🟡 洞察提示

**API 调用量评估**：
- 有效 TagKey 上限 5 个（见 DescribeTagKeys 过滤规则）
- 对照月调用 +5 次，量级可控
- 可与分析月调用并行（asyncio.gather 或同步多线程），不增加串行耗时

---

## 八、F11 趋势分析的多月调用

✅ [SHOULD] 趋势分析依赖 T-3 月、T-4 月的产品汇总数据（用于"近 3 月趋势"节展示 T-3/T-2/T-1 三个月）。

### 8.1 调用清单

```
# 必需（环比对照）
DescribeBillSummaryByProduct  {分析月 BeginTime/EndTime}      # T-1 月
DescribeBillSummaryByProduct  {对照月 BeginTime/EndTime}      # T-2 月

# 新增（F11 趋势）
DescribeBillSummaryByProduct  {T-3月 BeginTime/EndTime}       # SHOULD
DescribeBillSummaryByProduct  {T-4月 BeginTime/EndTime}       # SHOULD（用于展示 T-3 月的环比对照）

# 可选（F11 同比，仅用户明确要求）
DescribeBillSummaryByProduct  {T-12月 BeginTime/EndTime}      # MAY
```

### 8.2 总调用量

- 环比基础：2 次
- F11 趋势：2 次 → 总 4 次
- F11 同比（用户要求时）：+1 次 → 总 5 次

均可**并行调用**（asyncio.gather），不影响串行耗时。

### 8.3 用户关闭趋势分析

⚠️ [MUST] 用户明确说 "跳过趋势分析" / "只看环比" 时：
- 不调用 T-3 / T-4 月 API
- 不展示「近 3 月趋势」节
- 其他流程不变

### 8.4 Pre-Output Check 扩展（非强制）

F11 趋势节不在 HARD/MUST 自检清单中（SHOULD 级），不参与 Pre-Output Check 的通过/失败判定。但：
- 若趋势节已生成，必须遵循 F20 整数化 和 F3 持平阈值规则
- 趋势节的产品范围应与主报告「按产品拆解」Top 10 保持一致（不单独计算 Top N）
