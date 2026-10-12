# JSON 输入结构规范

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `report_type` | string | ✅ | `billing` / `audit` / `inspect` / `costopt` |
| `title` | string | ✅ | 封面主标题 |
| `subtitle` | string | ⛔️ | 封面副标题（建议带账号 + 时间） |
| `uin` | string | ⛔️ | 主账号 ID（不在 PPT 显示，仅用于落盘路径） |
| `date` | string | ⛔️ | YYYY-MM-DD，落盘路径用 |
| `summary_md` | string | ✅ | AIOps 智能概览 markdown，≤600 字 |
| `highlights` | array | ⛔️ | 3 条核心数据，渲染到三栏要点页 |
| `sections` | array | ✅ | 内容章节，每个 1-N 页 |
| `actions` | array | ⛔️ | 行动建议，最后一页 |

## highlights 元素结构

```json
{"title": "本月总支出", "value": "¥123,456", "delta": "+12.3%", "note": "环比上涨主要由 CVM 拉动"}
```

| 字段 | 说明 |
|---|---|
| `title` | 指标名（如"本月总支出"） |
| `value` | 数值（带单位，如"¥123,456"） |
| `delta` | 变化率（"+12%" / "-5%" / "占 38%"） |
| `note` | 一句话补充（≤30 字） |

**最多取前 3 个**，多余的会被丢弃（模板该页固定 3 栏）。

## sections 元素结构

```json
{
  "heading": "按产品拆解",
  "subheading": "TOP 10 高消费产品（可选）",
  "table": { ... },     // 二选一
  "chart": { ... }      // 二选一
}
```

### table 子结构

```json
{
  "headers": ["产品", "金额", "占比", "环比"],
  "rows": [
    ["CVM", "¥45,678", "38%", "+12%"],
    ["COS", "¥23,456", "19%", "+5%"]
  ]
}
```

- 行数 > 12：自动分页（`heading (1/N)` / `(2/N)`）
- 列数最多 6 列，超出会截断
- 单元格全部按 string 渲染（数字、百分比都要先 format 成字符串）

### chart 子结构

```json
{
  "type": "bar",                              // bar | line | pie | hbar
  "categories": ["业务A","业务B","业务C"],
  "series": [
    {"name": "金额", "values": [45678, 23456, 12345]}
  ]
}
```

> 注意：本 skill 当前实现采用「表格 + 形状条」兜底渲染，不创建原生 Chart 对象，避免模板 chart binding 失效问题。视觉上仍然是带数值的横向条形图。

## actions 元素结构

```json
{"priority": "P0", "text": "整改：把 0.0.0.0/0 的 22/3389 端口收敛到办公出口"}
```

- `priority`: P0 / P1 / P2 / P3
- 渲染时按优先级排序，P0 用红色 chip，P1 黄色，P2 浅蓝，P3 灰
- 超过 6 条自动分页

## 完整示例

见 `examples/billing-sample.json`。

## 字段缺失策略

- `summary_md` / `title` / `report_type` / `sections` 缺失：报错退出
- 其它字段缺失：跳过对应章节，**不**报错
- `sections` 为空数组：只生成封面 + 概览页（最简版本）
