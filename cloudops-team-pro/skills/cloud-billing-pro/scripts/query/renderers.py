"""三段式渲染：一句话结论 + 数据明细表 + 建议/洞察。

输出形式（PRD §5.1）：
```
【一句话结论】
本月 andyyfang 名下有 3 个资源费用上涨，总增长 ¥14（环比 +4%）。

【数据明细表】
| 资源ID | 资源名 | 产品 | 上月 | 本月 | 增长额 | 增长率 |
| --- | --- | --- | --- | --- | --- | --- |
| nat-jbv21cuw | Pubilc-Nat-01 | NAT网关 | ¥244 | ¥253 | +¥9 | +4% |

【建议/洞察】
- ...
```

输出形态（PRD §5.2）：
  - 默认纯文本表格 + 简短结论 + 建议
  - 单提示词扩展查询不输出文档
  - 表格行数上限 20 行；超过则截断 + "完整列表 N 项"
"""
from __future__ import annotations

from typing import Iterable


MAX_ROWS = 20


def fmt_money(v: float, *, signed: bool = False) -> str:
    if v == 0:
        return "¥0"
    sign = "+" if (signed and v > 0) else ("-" if v < 0 else "")
    return f"{sign}¥{abs(v):,.0f}"


def fmt_pct(curr: float, prev: float) -> str:
    if prev <= 0:
        return "新增" if curr > 0 else "—"
    if curr <= 0:
        return "下线"
    pct = (curr - prev) / prev * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def render_table(headers: list[str], rows: list[list[str]], *,
                 max_rows: int = MAX_ROWS, total_count: int | None = None) -> str:
    """渲染 markdown 表格。超过 max_rows 时截断并加注。"""
    truncated = False
    n = len(rows)
    if total_count is None:
        total_count = n
    if n > max_rows:
        rows = rows[:max_rows]
        truncated = True

    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    out = "\n".join(lines)
    if truncated:
        out += f"\n\n_仅展示前 {max_rows} 行，共 {total_count} 项_"
    return out


def render_three_section(*, conclusion: str, table: str, insights: list[str]) -> str:
    parts = ["**【一句话结论】**", conclusion.strip()]
    if table:
        parts.append("")
        parts.append("**【数据明细表】**")
        parts.append(table)
    if insights:
        parts.append("")
        parts.append("**【建议/洞察】**")
        for s in insights:
            parts.append(f"- {s}")
    return "\n".join(parts)


def render_empty(conclusion: str, *, hint: str = "") -> str:
    body = f"**【一句话结论】**\n{conclusion}"
    if hint:
        body += f"\n\n_提示_：{hint}"
    return body


__all__ = [
    "render_table", "render_three_section", "render_empty",
    "fmt_money", "fmt_pct", "MAX_ROWS",
]
