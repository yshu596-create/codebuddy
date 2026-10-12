# 账单 HTML 看板渲染规范

> 本文档规定 `render_billing_html.py` 输出的单文件 HTML 数据看板的**渲染契约**：
> 必有章节、字段映射、视觉约束、全量展示规则。
>
> 模型不需要手写 HTML——所有渲染逻辑都在 `render_billing_html.py` 内实现。
> 本文档的作用是：让维护脚本的人 / 校对看板效果的人 / 排查渲染问题的人，
> 在不读源码的前提下知道"这张看板长什么样、字段从哪来、改动哪里要注意什么"。

---

## 一、最终交付物

```
<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html
```
（v3 PROJECT 码命名；`{cloud}`/`{project}`/`{uin}` 取自 `prepare_billing_data.py` 返回的 `credentials.active_cloud`/`active_project`/`uin`；en 版加 `-en` 后缀。**不再用** `{display_name}`/`uin{uin}`。）

- **单文件**：所有数据内联进同一个 `.html`，无外部 CSS/JS/图片依赖
- **唯一外部依赖**：Chart.js，默认通过 jsdelivr CDN 一行 `<script>` 引入；极端离线场景用 `--offline /path/to/chart.umd.min.js` 把 Chart.js 内嵌
- **可双击打开**：本地 file:// 直接渲染；在 WorkBuddy 客户端通过预览工具直接展示

---

## 二、视觉风格契约

### 配色（深色玻璃拟态）

| 用途 | 取色 |
|:------|:------|
| 页面背景 | 线性渐变 `#0b1020 → #131933` |
| 卡片背景 | `rgba(255,255,255,0.04)` + `backdrop-filter: blur(8px)` |
| 卡片描边 | `rgba(255,255,255,0.08)` |
| 主前景文字 | `#f5f6fb` |
| 次级文字 | `rgba(245,246,251,0.65)` |
| 弱化文字 | `rgba(245,246,251,0.45)` |
| 涨幅强调（红） | `#ff6b6b` |
| 降幅强调（绿） | `#4ade80` |
| KPI / 链接强调 | `#7aa2ff` |

### 字号与间距

- 报告主标题（账户名 + 月份）：32px / `font-weight: 700`
- KPI 数字：28px / `font-weight: 700`
- 章节标题：18px / `font-weight: 600`
- 表头：13px / `font-weight: 600` / 次级文字色
- 表格正文：13px / 主前景色
- 卡片间距：32px；卡片内 padding：24px

### 字体

- 主体：`-apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", "Microsoft YaHei", sans-serif`
- 数字：`"SF Mono", "JetBrains Mono", Consolas, Menlo, monospace`（提升数字对齐感）

---

## 三、必有章节（顺序固定）

| 顺序 | 章节 | 渲染元素 | 数据字段 |
|:------|:------|:------|:------|
| 1 | 报告头 | 一级标题 + 分析窗口副标题 + 数据生成时间 | `analysis_month` / `account_name` / `prev_month` |
| 2 | KPI 四宫格 | 4 张玻璃卡片 | `totals.curr_total` / `totals.diff` / `totals.ratio_pct` / `top_products[0]` / `summary_highlights.most_expensive` 等 |
| 3 | 产品分布环图 | Chart.js doughnut | `top_products[]` |
| 4 | 趋势线图 | Chart.js line（多产品 × 多月） | `product_trend.*` + `trend_months_ordered` |
| 5 | 地域分布 | Chart.js 横向 bar + 滚动表格 | `top_regions[]` |
| 6 | 产品全量表 | 全量产品 + 滚动 | `top_products[]`（**全部**，不截断） |
| 7 | 标签分账（每个 TagKey 一张表） | 每个 `effective_tag_keys` 一张表 + 滚动 | `summary_tag.{TagKey}[]` |
| 8 | 资源变化四张表 | 增长 / 降低 / 新增 / 下线 | `resource_diff.{growth, decline, new, decommissioned}.items[]` |
| 9 | 智能洞察卡片 | bullets 列表 + level 标签 | `insights[]` + `summary_highlights` |
| 10 | 未享受折扣产品 | 列表 | `no_discount_products[]` |

❌ 禁止省略任何章节；❌ 禁止因为某章节为空就静默丢掉（应渲染为"暂无数据"占位卡片）。

---

## 四、全量展示约束（HARD）

- 所有"产品 / 标签 / 资源变化（增长/降低/新增/下线）"表必须**全量行**渲染
- 长表通过 `max-height: 460px; overflow-y: auto` 滚动呈现，不在数据层截断
- ❌ 禁止 Top 10 / Top 20 截断；❌ 禁止"还有 N 条未展示"提示
- 表头需 `position: sticky; top: 0` 在长表滚动时常驻顶部
- 资源变化表的标签列：由 `effective_tag_keys` 动态决定，缺标签的资源填"未打标"

---

## 五、Chart.js 引入策略

### 默认：CDN 引入

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

- 用户在有网络的 WorkBuddy 客户端打开 → 渲染立即可用
- 文件体积小（HTML 只有数据 + 样式 + 图表配置，约 20–80KB）

### 离线 fallback：`--offline /path/to/chart.umd.min.js`

```bash
python3 render_billing_html.py \
  --data step3_data.json \
  --output billing-{cloud}-{project}-{uin}.html \
  --lang <output_lang> \
  --offline /Users/x/.cache/chartjs/chart.umd.min.js
```

- 把 Chart.js 内联进 `<script>` 标签（HTML 体积约 +200KB）
- 完全离线场景使用（飞行模式 / 内网无 CDN 出口）

---

## 六、字段契约（`extract_step3_data.py` 输出 JSON）

`render_billing_html.py` 的输入 JSON 必须包含以下字段（缺失时按对应"缺失策略"渲染）：

### 6.1 顶层字段

| 字段 | 类型 | 用途 | 缺失策略 |
|:------|:------|:------|:------|
| `analysis_month` | string `"YYYY-MM"` | 报告标题月份 | 致命，渲染失败 |
| `account_name` | string | 报告头账户名 | 渲染为 `腾讯云账户` |
| `prev_month` | string `"YYYY-MM"` | 副标题对照月份 | 由 analysis_month 推导 |
| `data_generated_at` | string ISO 时间 | 报告头水印 | 用脚本运行时间填充 |

### 6.2 KPI 区（`totals` + `summary_highlights`）

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `totals.curr_total` | float | 分析月总费用（元） |
| `totals.prev_total` | float | 对照月总费用（元） |
| `totals.diff` | float | 环比差额（元） |
| `totals.ratio_pct` | float | 环比百分比（如 `12.3` 代表 +12.3%） |
| `totals.icon` | string | 趋势图标（`↑` / `↓` / `—`） |
| `summary_highlights.top_products_summary` | object | KPI 卡 - 费用最高产品（p1/p2/combined） |
| `summary_highlights.max_growth` | object | KPI 卡 - 涨幅最大资源 |
| `summary_highlights.most_expensive` | object | KPI 卡 - 单月最贵资源 |

### 6.3 产品 / 趋势 / 地域

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `top_products` | list[object] | **全量产品**（看板侧不截断），每个 obj 含 `name / prev_cost / curr_cost / diff_str / ratio_str / ratio_in_total` |
| `product_trend` | object | key=产品名，value=`{costs_by_month: {YYYYMM: cost}, icon, label}` |
| `trend_months_ordered` | list[string] | 趋势图横坐标月份顺序 |
| `top_regions` | list[object] | 地域柱图，每个 obj 含 `name / cost / ratio_pct` |

### 6.4 标签分账

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `effective_tag_keys` | list[string] | 当前账户有效标签键，每个键渲染一张表 |
| `summary_tag.{TagKey}` | list[object] | 每个 TagKey 的分账数据，obj 含 `value / prev_cost / curr_cost / ratio_pct / diff` |

### 6.5 资源变化（四类）

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `resource_diff.growth.items` | list[object] | 增长资源**全量**，每个 obj 含 `ResourceId / ProductCode / ProductName / prev_cost / curr_cost / diff / ratio_pct / _tags` |
| `resource_diff.decline.items` | list[object] | 降低资源**全量** |
| `resource_diff.new.items` | list[object] | 新增资源**全量**（无 prev_cost） |
| `resource_diff.decommissioned.items` | list[object] | 下线资源**全量**（无 curr_cost） |
| `resource_diff.*.count` | int | 配套总数，用于章节副标题"共 N 个" |

`_tags` 字段示例：`{"head": "andyfang", "business": "tsp", ...}`，渲染时按 `effective_tag_keys` 顺序展开为表格列。

### 6.6 资源详情 / 折扣

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `top10_resources` | list[object] | 单月成本 Top 10 资源（含 ProductName 反查） |
| `no_discount_products` | list[object] | 未享受折扣产品列表，每个 obj 含 `name / cost` |
| `product_code_to_name` | object | ProductCode → ProductName 映射表 |
| `insights` | list[object] | 智能洞察 bullets，每个 obj 含 `level (info/warn/danger) / text` |

---

## 七、数值精度（与 SKILL.md §五保持一致）

- 费用展示**整数**（`¥12,580`），不带小数点
- 占比保留 1 位小数（`56.9%`）
- 万元以上 KPI 卡可同时展示万元（`¥1.26万`），表格内统一保留整数
- 中间计算保留 4 位小数防浮点误差，渲染层 round 到整数

---

## 八、降级 / 异常渲染

| 场景 | 渲染 |
|:------|:------|
| `totals` 缺失 | 报告头标记"❌ 数据获取失败"，页面其余章节按"暂无数据"占位 |
| 某产品无费用 | 对应行展示 `—` 或"无费用"，不用 `0.00` 占位 |
| 某资源标签缺失 | 标签列填"未打标" |
| 标签分账某 TagKey 全部为空 | 该 TagKey 表渲染为"暂无标签数据"占位卡片，**不**静默丢弃章节 |
| Chart.js 加载失败 | 在图表区域渲染降级表格（`<table>`），数据不丢 |

---

## 九、变更指引（给维护者）

1. **新增字段**：先改 `extract_step3_data.py` 输出 → 再改 `render_billing_html.py` 渲染 → 最后回头更新本文档"§六字段契约"
2. **改样式**：只动 `render_billing_html.py` 的 `STYLE` 常量，不改其他
3. **新增章节**：在本文档"§三必有章节"表加一行 + `render_html()` 内增加渲染块
4. **去除双 Markdown 残留**：本文档已不含历史模板；如发现 SKILL.md / agent.md 还引用 `output-templates.md` / `billing_doc.md` / `validate_doc_md.py`，**直接清理**
