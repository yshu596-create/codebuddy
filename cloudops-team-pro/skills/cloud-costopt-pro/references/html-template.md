# 闲置巡检 HTML 看板渲染规范

> 本文档规定 `render_costopt_html.py` 输出的单文件 HTML 看板的**渲染契约**：
> 必有章节、字段映射、视觉约束、全量展示规则。
>
> 模型不需要手写 HTML — 所有渲染逻辑都在 `render_costopt_html.py` 内实现。
> 本文档是给"维护脚本的人 / 校对看板的人 / 排查渲染问题的人"看的参考。

---

## 一、最终交付物

```
<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html
```

- **单文件**：所有数据内联在同一个 `.html` 中，无外部 CSS/JS/图片依赖
- **唯一外部依赖**：Chart.js，默认通过 jsdelivr CDN 一行 `<script>` 引入；极端离线场景可用 `--offline` 内嵌
- **可双击打开**：本地 file:// 直接渲染；在 WorkBuddy 客户端通过 preview_url 工具直接展示

❌ 最终交付物只有 `costopt-{cloud}-{project}-{uin}.html` + 回复框中的 **Markdown 概览**（WorkBuddy 单专家模式，不走任何编排层）。

---

## 二、视觉风格契约（与 billing 看板对齐）

### 配色（深色玻璃拟态）

| 用途 | 取色 |
|:------|:------|
| 页面背景 | 线性渐变 `#0b1020 → #131933` |
| 卡片背景 | `rgba(255,255,255,0.04)` + `backdrop-filter: blur(8px)` |
| 卡片描边 | `rgba(255,255,255,0.08)` |
| 主前景文字 | `#f5f6fb` |
| 次级文字 | `rgba(245,246,251,0.65)` |
| 弱化文字 | `rgba(245,246,251,0.45)` |
| 严重等级红 | `#ff6b6b` |
| 高优黄 | `#fbbf24` |
| 中等浅黄 | `#facc15` |
| 低风险绿 | `#4ade80` |
| KPI / 链接强调 | `#7aa2ff` |

### 字号与间距

- 报告主标题：32px / `font-weight: 700`
- KPI 数字：28px / `font-weight: 700`
- 章节标题：18px / `font-weight: 600`
- 表头：13px / `font-weight: 600` / 次级文字色 + sticky top:0
- 表格正文：13px / 主前景色
- 卡片间距：32px；卡片内 padding：24px

---

## 三、必有章节（顺序固定）

| 顺序 | 章节 | 渲染元素 | 数据字段 |
|:------|:------|:------|:------|
| 1 | 报告头 | 标题（账户名 + "闲置资源巡检看板"）+ 监控窗口副标题 | `account_name` / `bill_month` / `monitor_window` |
| 2 | KPI 四宫格 | 4 张玻璃卡片 | `totals.{total_bill_cost, estimated_saving, finding_count, protected_count, exempted_count}` + `severity_counts` |
| 3 | 风险等级环图 + 产品柱图 | Chart.js doughnut + bar 并排 | `severity_counts` / `severity_savings` / `products[]` |
| 4 | 优化机会全量清单（滚动表） | sticky 表头 + 长表滚动 | `findings[]`（按 severity 排序，**全量**） |
| 5 | 按产品聚合表 | 滚动表 | `products[]` |
| 6 | 关键资源保护清单 | 滚动表 | `protected[]` |
| 7 | 标签豁免清单 | 滚动表 | `exempted[]` |
| 8 | 智能洞察卡片 | level 标签 + 说明 | `insights[]` |

❌ 禁止省略任何章节；空数据渲染为"暂无数据"占位提示。

---

## 四、全量展示约束（HARD）

- **优化机会全量清单**：必须把 `findings` 全部列出，不允许"只展示 Top N"或"完整 N 项见文档"。长表用 `max-height: 460px; overflow-y: auto` 滚动呈现，sticky 表头常驻。
- **保护清单 / 豁免清单**：同样全量展示。
- 产品聚合柱图：只取 Top 12 防止图表挤压，但下方表格仍要全量。

---

## 五、字段契约（`judge_costopt.py` 输出 `step3_results.json`）

### 5.1 顶层

| 字段 | 类型 | 用途 | 缺失策略 |
|:------|:------|:------|:------|
| `account_name` | string | 报告头 | 渲染为 `腾讯云账户` |
| `uin` | string | 落盘路径 | 必填 |
| `bill_month` | string `"YYYY-MM"` | 副标题 | 渲染为 `-` |
| `monitor_window.start/end` | string | 副标题"监控窗口" | 不渲染 |
| `totals.total_bill_cost` | float | KPI 1 | 0 |
| `totals.estimated_saving` | float | KPI 2 | 0 |
| `totals.finding_count` | int | KPI 3 | findings 长度 |
| `totals.protected_count` | int | KPI 4 一半 | protected 长度 |
| `totals.exempted_count` | int | KPI 4 一半 | exempted 长度 |

### 5.2 findings

```jsonc
{
  "severity": "critical"|"high"|"medium"|"low",
  "product": "云服务器CVM",
  "resource_id": "ins-xxxx",
  "resource_name": "可选别名",
  "region": "ap-shanghai",
  "spec": "S5.MEDIUM4 / 2C4G",
  "issue": "CPU 1.2% · 内存 7.8% · 30d",
  "baseline": 320,
  "saving": 224,
  "saving_ratio": 70,
  "reversible": "low|mid|high",
  "timing": "立即可执行|下个维护窗口|...",
  "owner": "andyyfang",
  "advice": "降配到 S5.SMALL2 或回收",
  "console_url": "https://console.cloud.tencent.com/cvm/..."
}
```

- `severity` 缺省 / 未知 → `low`
- `console_url` 非空时把 `resource_id` 渲染成超链接（控制台免密直达）
- 排序：脚本统一按 `severity_order ASC, saving DESC`

### 5.3 protected / exempted

```jsonc
// protected[]
{"product": "云数据库MySQL", "resource_id": "cdb-xxx", "spec": "8C32G", "region": "ap-shanghai", "reason": "主从架构 disaster_recovery"}

// exempted[]
{"product": "云服务器CVM", "resource_id": "ins-yyy", "tag": "finops-critical", "reason": "用户显式标记关键资源"}
```

### 5.4 insights

```jsonc
{"level": "info|warn|danger", "text": "本月预计可省 ¥8,732，占总账单 12.4%"}
```

---

## 六、Markdown 概览（回复框输出）

**先**在回复框发一段 **≤600 字 Markdown** 概览（**五段式**），**再** present_files 打开 HTML 看板，确保概览不被预览面板遮挡：

```markdown
## 🔍 {YYYY-MM} 闲置资源巡检概览

### 💰 全局摘要
月度账单 **¥X,XXX** | 预计月省 **¥X,XXX**（占 X.X%）| 发现 N 条优化项

### 🔴 风险结构
🔴 严重 N 条 / 🟠 高 N / 🟡 中 N / 🟢 低 N
Top 3 高优：产品 A ¥X / 产品 B ¥X / 产品 C ¥X

### 📦 产品洞察
| 产品 | 问题数 | 预计月省 | 占比 |
|---|---|---|---|
| ⚠️ XXX | N | ¥X | X.X% |
| ... |

### 🛡️ 关键资源保护
主备/只读自动豁免 N 条；finops-critical 标签豁免 N 条；无误判。

### 🗓️ 建议执行节奏
- 第 1 周（🔴 严重 / 立即可执行）：释放 / 降配 N 条 → ¥X 月省
- 第 2~3 周（🟠 高 / 下个维护窗口）：N 条 → ¥X
- 后续观察（🟡🟢）：N 条 → ¥X

📊 完整看板见上方 HTML 预览 ↑
```

**编写规则**：
- 数字全部从 `step3_results.json` 读，禁止脑补 / 估算
- 异常环比、单条预计月省 >¥5,000 等 → 用 ⚠️ 标注
- "降本计划"段按 `reversible` × `timing` 自然分周；禁止超过五段
- ❌ 禁止把 HTML 内全量明细复制到对话；HTML 已承载完整数据

---

## 七、Chart.js 引入策略

### 默认：CDN

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
```

### 离线 fallback

```bash
python3 render_costopt_html.py \
  --data step3_results.json \
  --output costopt-{cloud}-{project}-{uin}.html \
  --offline /Users/x/.cache/chartjs/chart.umd.min.js
```

把 Chart.js 内联进 `<script>`，体积约 +200KB。
