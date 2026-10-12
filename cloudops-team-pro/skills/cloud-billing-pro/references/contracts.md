# cloud-billing-pro 数据契约

> 本文档定义 Step 1 → Step 3 之间的字段契约，**`prepare_billing_data.py` / `extract_step3_data.py` / `render_billing_html.py` 三个脚本必须按此契约对齐**。
>
> 修改任意一端的字段命名或结构时，必须同步更新另外两端 + 本文档。

---

## Stage 1：`prepare_billing_data.py` 输出

落盘到 stdout，由调用方重定向：
- **多账号并发**：必须落到 per-project 路径 `<WORKSPACE>/cache/billing/<CODE>/prep_output.json`（`<CODE>` = `--project` 值，并发各 worker 唯一，规避第二层竞态）
- **单账号**：可落 `<WORKSPACE>/prep_output.json`（无并发冲突）

关键字段：

```json
{
  "credentials": {"alias": "...", "uin": "...", "display_name": "...", "budget": "..."},
  "analysis_month": "202605",
  "comparison_month": "202604",
  "skip_cache": false,
  "cache_base": "/path/to/cache/billing/{uin}",
  "cache_inventory": {"202605": [...files], "202604": [...]},
  "analysis_summary": { "summary_product": {...}, "summary_region": [...] },
  "comparison_summary": { "summary_product": {...}, "summary_region": [...] },
  "summary_tag": {
    "head":     [ {"TagValue": "...", "202605": 80070, "202604": 92117}, ... ],
    "business": [ {"TagValue": "...", "202605": ..., "202604": ...}, ... ]
  },
  "analysis_resources": { "total_resources": N, "total_cost": float, "resources": [...] },
  "resource_diff": {
    "growth":  {"count": N, "items": [{ResourceId, ProductCode, ProductName, CurrCost, PrevCost, _tags: {head, business}}, ...]},
    "decline": {"count": N, "items": [...]},
    "new":     {"count": N, "items": [{ResourceId, ProductCode, ProductName, RealTotalCost, _tags}, ...]},
    "offline": {"count": N, "items": [...]}
  },
  "report_ready": {
    "available": true,
    "totals":      {"curr_total": float, "prev_total": float, "diff": float, "ratio_pct": float, "icon": "📈/📉/➡️"},
    "top_products": [ {"name": "...", "curr_cost": float, "prev_cost": float, "diff_str": "...", "ratio_str": "...", "ratio_in_total": "..."} ],
    "top_regions":  [ {"name": "...", "cost": float, "ratio_pct": "..."} ],
    "product_trend": {
      "云服务器CVM": {"icon": "📈", "label": "持续上升", "costs_by_month": {"202603": ..., "202604": ..., "202605": ...}}
    },
    "trend_months_ordered": ["202603", "202604", "202605", "202605"],   // [T-3, T-2, T-1, T]，含分析月本身
    "no_discount_products": [ {"name": "...", "cost": float, "discount_ratio": float} ],
    "insights": [ {"level": "🔴/🟡/🟢", "text": "**title**: desc"} ],
    "summary_highlights": {
      "top_products_summary": {"p1": "...", "p2": "...", "combined": "..."},
      "max_growth":     {"ResourceId": "...", "ProductName": "...", "Diff": float, "Ratio": "..."},
      "most_expensive": {"ResourceId": "...", "ProductName": "...", "RealTotalCost": float}
    }
  }
}
```

**HARD 约束**：
- `top_products`：**全量**（不做 Top N 截断），按本月费用降序
- `summary_tag.{TagKey}`：列表，每项含 `TagValue` + 月份键 `YYYYMM` 作为费用值
- `trend_months_ordered`：长度 4，**最后一项是分析月本身**（含 T 月）
- `resource_diff.{kind}.items[]._tags`：必填，dict（即使全部"未打标"），**键必须包含所有 effective_tag_keys**

---

## Stage 2：`extract_step3_data.py` 输出

读 prep_output（第 2 位置参数显式传 `<WORKSPACE>/cache/billing/<CODE>/prep_output.json`；省略时退化读 `<workspace>/prep_output.json`），输出紧凑 JSON 给 render 用：

```json
{
  "analysis_month": "202605",
  "comparison_month": "202604",
  "account_name": "主账号-100047497187",
  "uin": "100047497187",
  "totals": { ... },                       // 透传 report_ready.totals
  "top_products": [ ... ],                 // 全量，透传 report_ready.top_products
  "top_regions":  [ ... ],                 // 透传 report_ready.top_regions
  "product_trend": { ... },                // 透传 report_ready.product_trend
  "trend_months_ordered": [...],           // 透传，含分析月
  "summary_tag": {                         // 透传 prep_data.summary_tag（列表格式）
    "head":     [ {TagValue, YYYYMM: cost, ...} ],
    "business": [ ... ]
  },
  "effective_tag_keys": ["head", "business"],
  "top10_resources":   [ {ResourceId, ProductCode, ProductName, RealTotalCost, ...} ],
  "resource_diff":     { "growth": {count, items}, "decline": {...}, "new": {...}, "offline": {...} },
  "no_discount_products": [...],
  "insights": [...],
  "summary_highlights": { ... },
  "product_code_to_name": { "p_cvm": "云服务器CVM", ... }
}
```

**HARD 约束**：
- `top_products`：**禁止 [:10] 截断**，按 prep_data.report_ready.top_products 全量透传
- `summary_tag` 仅透传非空 TagKey；空数据的 TagKey 不进 effective_tag_keys
- 字段命名严格使用 prep_data 的 snake_case 命名（`curr_total` 而非 `current`）

---

## Stage 3：`render_billing_html.py` 输入字段

按 Stage 2 输出读入。`build_data()` 内部归一化为：

```python
{
  "meta":           {account_name, analysis_month, comparison_month, uin, current, previous, diff, ratio, ...},
  "products":       [ {name, cur, prev}, ... ],   # 全量
  "regions":        [ {name, cur, ratio_pct}, ... ],  # 按地域拆解
  "tag_groups":     {"head": [{name, cur, prev}, ...], "business": [...]},  # 所有 TagKey
  "default_tag_key": "head",                      # 渲染默认主维度（仅装饰用，所有 TagKey 都会渲染）
  "trend":          {"months": ["202603","202604","202605","202605"], "total": [v0, v1, v2, v3]},
  "growth":         [ {id, prod, biz, cur, prev, diff, kind: "up"}, ... ],   # 全量
  "decline":        [ ... ],
  "new":            [ {id, prod, biz, cur, kind: "new"}, ... ],
  "gone":           [ {id, prod, biz, prev, kind: "gone"}, ... ],
  "insights":       [ {title, desc, level: "info|warn|bad"}, ... ]
}
```

**HARD 约束**：
- `tag_groups`：**所有非空 TagKey 都要渲染独立卡片**（head + business 都必须出，不得只渲染 default_tag_key）
- `trend.months`：**最后一项 = 分析月**，趋势图最后一根柱子高亮（区分当前月）
- `products`：**全量**渲染（前端用 `max-height: 460px + overflow-y: auto` 滚动），禁止任何 N=10 截断

---

## 字段命名错位历史

为防止 demo（mock JSON）和真实 prep 输出再次出现契约错位，所有 normalize 函数必须**同时兼容**两种来源：

| 字段 | extract 输出键 | mock 历史键 |
|---|---|---|
| 总费用-本月 | `totals.curr_total` | `totals.current` |
| 总费用-上月 | `totals.prev_total` | `totals.previous` |
| 产品名 | `top_products[].name` | `top_products[].ProductName` |
| 产品本月 | `top_products[].curr_cost` | `top_products[].Current` |
| 资源本月费 | `resource_diff.{kind}.items[].CurrCost` | `RealTotalCost` |
| 资源标签 | `_tags: {head, business}` | `Tags: {BizName, ...}` |
| 趋势 | `product_trend[name].costs_by_month` (dict) | `[v1, v2, v3, v4]` (list) |
| 洞察 | `insights[]: {level: emoji, text}` | `{title, desc, level}` |

`render_billing_html.py::normalize_*` 已通过 `if/elif` 同时支持两种格式。

---

## 自检建议

每次改动数据 pipeline，执行：
```bash
python3 <SKILL_BASE>/scripts/extract_step3_data.py <WORKSPACE> > /tmp/check.json
python3 <SKILL_BASE>/scripts/render_billing_html.py --data /tmp/check.json --output /tmp/check.html
```
任意一步报错 → 字段契约错位，回到本文档对照修复。
