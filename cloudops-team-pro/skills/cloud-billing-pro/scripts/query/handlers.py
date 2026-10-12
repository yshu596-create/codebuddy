"""16 个 P0 handler 的查询逻辑。

每个 handler 接收 (loader, slots, dicts) 并返回渲染好的三段式 markdown 字符串。

intent_id → handler 映射在文件末尾的 HANDLERS dict 中。
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Callable

from .billing_data_loader import BillingDataLoader, ResourceView, prev_month_str
from .renderers import (
    fmt_money, fmt_pct, render_table, render_three_section, render_empty, MAX_ROWS,
)
from .router import Slots
from .slot_dicts import SlotDicts


# ─── 共用工具 ─────────────────────────────────────────────


def _resources_of_head(loader: BillingDataLoader, head: str) -> list[ResourceView]:
    return [r for r in loader.resources() if r.head() == head]


def _resources_of_business(loader: BillingDataLoader, b: str) -> list[ResourceView]:
    return [r for r in loader.resources() if r.business() == b]


def _delta(r: ResourceView, curr: str, comp: str) -> tuple[float, float, float]:
    """返回 (curr_cost, prev_cost, delta)。"""
    c = r.cost(curr)
    p = r.cost(comp)
    return c, p, c - p


def _row_resource_growth(r: ResourceView, curr: str, comp: str) -> list[str]:
    c, p, d = _delta(r, curr, comp)
    return [
        r.resource_id,
        r.resource_name or "-",
        r.product or "-",
        fmt_money(p),
        fmt_money(c),
        fmt_money(d, signed=True),
        fmt_pct(c, p),
    ]


_GROWTH_HEADERS = ["资源ID", "资源名", "产品", "上月", "本月", "增长额", "增长率"]


# ─── H 维度 ──────────────────────────────────────────────


def handle_H_01(loader, slots: Slots, dicts) -> str:
    """{head} 这个月哪些资源涨了"""
    head = slots.head
    if not head:
        return render_empty("缺少 head 槽位，无法定位负责人。",
                            hint=f"已知 head：{', '.join(sorted(dicts.heads)[:5])} ...")
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = _resources_of_head(loader, head)
    grown = [r for r in rs if r.cost(curr) - r.cost(comp) > 0.5 and r.statuses.get(curr) != "新增"]
    grown.sort(key=lambda r: r.cost(curr) - r.cost(comp), reverse=True)
    if not grown:
        return render_empty(f"{head} 名下本月没有资源费用上涨。")

    total_diff = sum(r.cost(curr) - r.cost(comp) for r in grown)
    prev_total = sum(r.cost(comp) for r in rs)
    overall_pct = (total_diff / prev_total * 100) if prev_total > 0 else 0
    conclusion = (
        f"本月 **{head}** 名下有 **{len(grown)}** 个资源费用上涨，"
        f"总增长 **{fmt_money(total_diff, signed=True)}**（环比 {overall_pct:+.1f}%）。"
    )
    rows = [_row_resource_growth(r, curr, comp) for r in grown]
    table = render_table(_GROWTH_HEADERS, rows, total_count=len(grown))
    insights = []
    if grown:
        top = grown[0]
        insights.append(
            f"最大涨幅：**{top.resource_name or top.resource_id}** "
            f"（{top.product}），增长 {fmt_money(top.cost(curr) - top.cost(comp), signed=True)}"
        )
    return render_three_section(conclusion=conclusion, table=table, insights=insights)


def handle_H_02(loader, slots: Slots, dicts) -> str:
    """{head} 哪些资源降低了"""
    head = slots.head
    if not head:
        return render_empty("缺少 head 槽位。")
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = _resources_of_head(loader, head)
    down = [r for r in rs if r.cost(comp) - r.cost(curr) > 0.5 and r.statuses.get(curr) != "已下线"]
    down.sort(key=lambda r: r.cost(comp) - r.cost(curr), reverse=True)
    if not down:
        return render_empty(f"{head} 名下本月没有资源费用下降。")
    total_save = sum(r.cost(comp) - r.cost(curr) for r in down)
    conclusion = (
        f"**{head}** 本月有 **{len(down)}** 个资源费用下降，"
        f"累计节省 **{fmt_money(total_save)}**。"
    )
    rows = [_row_resource_growth(r, curr, comp) for r in down]
    table = render_table(_GROWTH_HEADERS, rows, total_count=len(down))
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_H_03(loader, slots: Slots, dicts) -> str:
    """{head} 这个月新增了哪些资源"""
    head = slots.head
    if not head:
        return render_empty("缺少 head 槽位。")
    curr = loader.analysis_month
    rs = _resources_of_head(loader, head)
    newly = [r for r in rs if r.statuses.get(curr) == "新增"]
    newly.sort(key=lambda r: r.cost(curr), reverse=True)
    if not newly:
        return render_empty(f"{head} 名下本月没有新增资源。")
    total = sum(r.cost(curr) for r in newly)
    conclusion = f"**{head}** 本月新增 **{len(newly)}** 个资源，合计 **{fmt_money(total)}**。"
    rows = [
        [r.resource_id, r.resource_name or "-", r.product or "-", fmt_money(r.cost(curr))]
        for r in newly
    ]
    table = render_table(["资源ID", "资源名", "产品", "本月费用"], rows, total_count=len(newly))
    insights = []
    if total > 5000:
        insights.append("本月新增金额较大，建议确认是否全部为预期内开通。")
    return render_three_section(conclusion=conclusion, table=table, insights=insights)


def handle_H_04(loader, slots: Slots, dicts) -> str:
    """{head} 本月花了多少"""
    head = slots.head
    if not head:
        return render_empty("缺少 head 槽位。")
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = _resources_of_head(loader, head)
    if not rs:
        return render_empty(f"未找到 {head} 名下任何资源。")
    cur_total = sum(r.cost(curr) for r in rs)
    prev_total = sum(r.cost(comp) for r in rs)
    diff = cur_total - prev_total
    pct = (diff / prev_total * 100) if prev_total > 0 else 0
    conclusion = (
        f"**{head}** 本月共花费 **{fmt_money(cur_total)}**，"
        f"上月 {fmt_money(prev_total)}，环比 **{diff:+.0f}**（{pct:+.1f}%）。"
    )
    top5 = sorted(rs, key=lambda r: r.cost(curr), reverse=True)[:5]
    rows = [
        [r.resource_id, r.resource_name or "-", r.product or "-", fmt_money(r.cost(curr))]
        for r in top5
    ]
    table = render_table(["资源ID", "资源名", "产品", "本月费用"], rows)
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_H_06(loader, slots: Slots, dicts) -> str:
    """谁这个月花钱最多"""
    curr = loader.analysis_month
    comp = loader.comparison_month()
    by_head_curr: dict[str, float] = defaultdict(float)
    by_head_prev: dict[str, float] = defaultdict(float)
    for r in loader.resources():
        h = r.head() or "(未认领)"
        by_head_curr[h] += r.cost(curr)
        by_head_prev[h] += r.cost(comp)
    rows_data = sorted(by_head_curr.items(), key=lambda x: x[1], reverse=True)
    n = slots.top_n or 10
    rows_top = rows_data[:n]
    if not rows_top:
        return render_empty("无 head 维度数据。")
    top_head, top_cost = rows_top[0]
    total = sum(by_head_curr.values())
    conclusion = (
        f"本月成本最高的 head 是 **{top_head}**，{fmt_money(top_cost)}（占比 {top_cost / total * 100:.1f}%）。"
    )
    rows = []
    for h, c in rows_top:
        p = by_head_prev.get(h, 0)
        d = c - p
        rows.append([
            h, fmt_money(c), fmt_money(p), fmt_money(d, signed=True), fmt_pct(c, p),
        ])
    table = render_table(["head", "本月", "上月", "环比", "增长率"], rows,
                         total_count=len(rows_data))
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_H_08(loader, slots: Slots, dicts) -> str:
    """没有 head 的资源"""
    curr = loader.analysis_month
    rs = [r for r in loader.resources() if not r.head() and r.cost(curr) > 0]
    rs.sort(key=lambda r: r.cost(curr), reverse=True)
    if not rs:
        return render_empty("本月所有有费用的资源都已认领 head。")
    total = sum(r.cost(curr) for r in rs)
    conclusion = f"本月有 **{len(rs)}** 个资源未认领 head，合计 **{fmt_money(total)}**。"
    rows = [
        [r.resource_id, r.resource_name or "-", r.product or "-", fmt_money(r.cost(curr))]
        for r in rs
    ]
    table = render_table(["资源ID", "资源名", "产品", "本月费用"], rows, total_count=len(rs))
    insights = []
    if rs:
        insights.append(f"建议优先认领最贵的 {min(3, len(rs))} 个：" +
                        "、".join(r.resource_id for r in rs[:3]))
    return render_three_section(conclusion=conclusion, table=table, insights=insights)


def handle_H_10(loader, slots: Slots, dicts) -> str:
    """每个 head 的成本拆解（透视表）"""
    return handle_H_06(loader, Slots(top_n=999, raw=slots.raw), dicts)


# ─── B 维度 ──────────────────────────────────────────────


def handle_B_01(loader, slots: Slots, dicts) -> str:
    """{business} 业务花了多少"""
    b = slots.business
    if not b:
        return render_empty("缺少 business 槽位。",
                            hint=f"已知业务：{', '.join(sorted(dicts.businesses)[:5])} ...")
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = _resources_of_business(loader, b)
    if not rs:
        return render_empty(f"未找到 {b} 业务下任何资源。")
    cur_total = sum(r.cost(curr) for r in rs)
    prev_total = sum(r.cost(comp) for r in rs)
    diff = cur_total - prev_total
    pct = (diff / prev_total * 100) if prev_total > 0 else 0
    conclusion = (
        f"**{b}** 业务本月共花费 **{fmt_money(cur_total)}**，"
        f"环比 **{diff:+.0f}**（{pct:+.1f}%），共 {len(rs)} 个资源。"
    )
    top10 = sorted(rs, key=lambda r: r.cost(curr), reverse=True)[:10]
    rows = [_row_resource_growth(r, curr, comp) for r in top10]
    table = render_table(_GROWTH_HEADERS, rows, total_count=len(rs))
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_B_02(loader, slots: Slots, dicts) -> str:
    """{business} 哪些资源涨了"""
    b = slots.business
    if not b:
        return render_empty("缺少 business 槽位。")
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = _resources_of_business(loader, b)
    grown = [r for r in rs if r.cost(curr) - r.cost(comp) > 0.5]
    grown.sort(key=lambda r: r.cost(curr) - r.cost(comp), reverse=True)
    if not grown:
        return render_empty(f"{b} 业务本月没有资源费用上涨。")
    total = sum(r.cost(curr) - r.cost(comp) for r in grown)
    conclusion = f"**{b}** 本月有 **{len(grown)}** 个资源上涨，累计 **{fmt_money(total, signed=True)}**。"
    rows = [_row_resource_growth(r, curr, comp) for r in grown]
    table = render_table(_GROWTH_HEADERS, rows, total_count=len(grown))
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_B_04(loader, slots: Slots, dicts) -> str:
    """各业务线成本拆解"""
    curr = loader.analysis_month
    comp = loader.comparison_month()
    by_curr: dict[str, float] = defaultdict(float)
    by_prev: dict[str, float] = defaultdict(float)
    for r in loader.resources():
        b = r.business() or "(未打标)"
        by_curr[b] += r.cost(curr)
        by_prev[b] += r.cost(comp)
    rows_data = sorted(by_curr.items(), key=lambda x: x[1], reverse=True)
    if not rows_data:
        return render_empty("无 business 维度数据。")
    total = sum(by_curr.values())
    conclusion = f"本月按业务线拆解共 **{len(rows_data)}** 条业务线，合计 **{fmt_money(total)}**。"
    rows = []
    for b, c in rows_data:
        p = by_prev.get(b, 0)
        d = c - p
        rows.append([
            b, fmt_money(c), fmt_money(p), fmt_money(d, signed=True),
            fmt_pct(c, p), f"{c / total * 100:.1f}%",
        ])
    table = render_table(["业务线", "本月", "上月", "环比", "增长率", "占比"], rows,
                         total_count=len(rows_data))
    return render_three_section(conclusion=conclusion, table=table, insights=[])


# ─── T 维度 ──────────────────────────────────────────────


def handle_T_01(loader, slots: Slots, dicts) -> str:
    """这三个月什么资源增长最快"""
    months = loader.months_available()
    # 取最近 3 个月（含分析月）
    recent = months[:3]
    if len(recent) < 2:
        return render_empty("历史数据不足 2 个月，无法做趋势分析。")
    earliest = recent[-1]
    latest = recent[0]
    growth = []
    for r in loader.resources():
        d = r.cost(latest) - r.cost(earliest)
        if d > 0.5:
            growth.append((d, r))
    growth.sort(key=lambda x: x[0], reverse=True)
    n = slots.top_n or 10
    top = growth[:n]
    if not top:
        return render_empty(f"近 {len(recent)} 月（{earliest} → {latest}）无明显增长资源。")
    conclusion = (
        f"近 **{len(recent)}** 个月（{earliest} → {latest}）累计增长最快 Top {len(top)} 资源："
    )
    rows = []
    for d, r in top:
        rows.append([
            r.resource_id, r.resource_name or "-", r.product or "-",
            fmt_money(r.cost(earliest)), fmt_money(r.cost(latest)),
            fmt_money(d, signed=True),
        ])
    table = render_table(
        ["资源ID", "资源名", "产品", earliest, latest, "累计增长"],
        rows, total_count=len(growth),
    )
    return render_three_section(conclusion=conclusion, table=table, insights=[])


def handle_T_03(loader, slots: Slots, dicts) -> str:
    """连续 N 个月在涨的资源（默认 N=3）"""
    n = slots.consec_months or 3
    months = loader.months_available()
    if len(months) < n + 1:
        return render_empty(f"历史数据不足 {n + 1} 个月，无法判断连续 {n} 月增长。")
    # months 是 [T-0, T-1, T-2, ...]，需要按时间正序看连续
    consec_months = months[:n + 1][::-1]  # [T-n, T-(n-1), ..., T-0]
    out = []
    for r in loader.resources():
        ok = True
        for i in range(1, len(consec_months)):
            if r.cost(consec_months[i]) - r.cost(consec_months[i - 1]) <= 0.5:
                ok = False
                break
        if ok and r.cost(consec_months[-1]) > 0:
            out.append(r)
    out.sort(key=lambda r: r.cost(consec_months[-1]) - r.cost(consec_months[0]),
             reverse=True)
    if not out:
        return render_empty(f"无连续 {n} 月在涨的资源。")
    conclusion = f"共 **{len(out)}** 个资源近 {n} 月连续上涨。"
    rows = []
    for r in out:
        prev = r.cost(consec_months[0])
        last = r.cost(consec_months[-1])
        rows.append([
            r.resource_id, r.resource_name or "-", r.product or "-",
            " → ".join(fmt_money(r.cost(m)) for m in consec_months),
            fmt_money(last - prev, signed=True),
        ])
    table = render_table(
        ["资源ID", "资源名", "产品", f"近 {n + 1} 月走势", "累计增长"],
        rows, total_count=len(out),
    )
    return render_three_section(conclusion=conclusion, table=table, insights=[])


# ─── A 维度 ──────────────────────────────────────────────


def handle_A_03(loader, slots: Slots, dicts) -> str:
    """本月异常飙升的资源（z-score > 2σ，基于近 3 月）"""
    months = loader.months_available()
    if len(months) < 3:
        return render_empty("历史数据不足 3 个月，无法做异常检测。")
    curr = months[0]
    history = months[1:4]  # T-1, T-2, T-3
    flagged: list[tuple[float, ResourceView, float, float, float]] = []
    for r in loader.resources():
        c = r.cost(curr)
        if c < 100:  # 噪音过滤：低于 ¥100 不算异常
            continue
        hist_costs = [r.cost(m) for m in history]
        if max(hist_costs) < 1:
            # 全是 0，本月新增——交给 A-05
            continue
        mean = statistics.mean(hist_costs)
        try:
            stdev = statistics.stdev(hist_costs)
        except statistics.StatisticsError:
            stdev = 0
        if stdev < 1:
            continue
        z = (c - mean) / stdev
        if z > 2:
            flagged.append((z, r, mean, c, c - mean))
    flagged.sort(key=lambda x: x[0], reverse=True)
    if not flagged:
        return render_empty("本月无异常飙升资源（z-score > 2σ）。")
    conclusion = f"本月异常飙升资源 **{len(flagged)}** 个（基于近 3 月 z-score）。"
    rows = []
    for z, r, mean, c, diff in flagged[:MAX_ROWS]:
        rows.append([
            r.resource_id, r.resource_name or "-", r.product or "-",
            fmt_money(mean), fmt_money(c), fmt_money(diff, signed=True),
            f"{z:.1f}σ",
        ])
    table = render_table(
        ["资源ID", "资源名", "产品", "近 3 月均值", "本月", "偏差", "z-score"],
        rows, total_count=len(flagged),
    )
    insights = ["建议立即检查 z-score 最高的 3 个资源，确认是否为预期内变化。"]
    return render_three_section(conclusion=conclusion, table=table, insights=insights)


def handle_A_05(loader, slots: Slots, dicts) -> str:
    """本月新增资源中花钱最多的"""
    curr = loader.analysis_month
    newly = [r for r in loader.resources() if r.statuses.get(curr) == "新增"]
    newly.sort(key=lambda r: r.cost(curr), reverse=True)
    if not newly:
        return render_empty("本月没有新增资源。")
    n = slots.top_n or 10
    top = newly[:n]
    total = sum(r.cost(curr) for r in newly)
    conclusion = f"本月新增资源 **{len(newly)}** 个，合计 **{fmt_money(total)}**，Top {len(top)}："
    rows = [
        [r.resource_id, r.resource_name or "-", r.product or "-",
         r.head() or "-", r.business() or "-", fmt_money(r.cost(curr))]
        for r in top
    ]
    table = render_table(
        ["资源ID", "资源名", "产品", "head", "business", "本月费用"],
        rows, total_count=len(newly),
    )
    insights = []
    if top and top[0].cost(curr) > 5000:
        insights.append(f"最贵新增资源 **{top[0].resource_name or top[0].resource_id}**"
                        f"（{fmt_money(top[0].cost(curr))}）请确认是否预期开通。")
    return render_three_section(conclusion=conclusion, table=table, insights=insights)


# ─── C 维度 ──────────────────────────────────────────────


def handle_C_05(loader, slots: Slots, dicts) -> str:
    """本月增长贡献度 Top N"""
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = loader.resources()
    total_diff = sum(r.cost(curr) - r.cost(comp) for r in rs)
    contributions = []
    for r in rs:
        d = r.cost(curr) - r.cost(comp)
        if d > 0:
            contributions.append((d, r))
    contributions.sort(key=lambda x: x[0], reverse=True)
    n = slots.top_n or 10
    top = contributions[:n]
    if not top or total_diff <= 0:
        return render_empty("本月总成本未上涨，无增长贡献。")
    conclusion = (
        f"本月总增长 **{fmt_money(total_diff, signed=True)}**，前 {len(top)} 个资源贡献了 "
        f"**{sum(d for d, _ in top) / total_diff * 100:.1f}%**。"
    )
    rows = []
    for d, r in top:
        rows.append([
            r.resource_id, r.resource_name or "-", r.product or "-",
            r.head() or "-", fmt_money(d, signed=True),
            f"{d / total_diff * 100:.1f}%",
        ])
    table = render_table(
        ["资源ID", "资源名", "产品", "head", "增长额", "贡献占比"],
        rows, total_count=len(contributions),
    )
    return render_three_section(conclusion=conclusion, table=table, insights=[])


# ─── I 维度 ──────────────────────────────────────────────


def handle_I_04(loader, slots: Slots, dicts) -> str:
    """本月最该警惕的 3 件事 = 飙升 + 大额新增 + 未认领。"""
    curr = loader.analysis_month
    comp = loader.comparison_month()
    rs = loader.resources()

    # 1. 单资源最大涨幅
    biggest_growth = max(rs, key=lambda r: r.cost(curr) - r.cost(comp), default=None)
    growth_diff = (biggest_growth.cost(curr) - biggest_growth.cost(comp)) if biggest_growth else 0

    # 2. 最贵新增资源
    newly = [r for r in rs if r.statuses.get(curr) == "新增"]
    biggest_new = max(newly, key=lambda r: r.cost(curr), default=None)

    # 3. 未认领总额
    unowned_total = sum(r.cost(curr) for r in rs if not r.head() and r.cost(curr) > 0)
    unowned_count = sum(1 for r in rs if not r.head() and r.cost(curr) > 0)

    items = []
    if biggest_growth and growth_diff > 100:
        items.append(
            f"🔥 **最大涨幅**：`{biggest_growth.resource_name or biggest_growth.resource_id}` "
            f"（{biggest_growth.product}）增长 **{fmt_money(growth_diff, signed=True)}**"
            + (f"，负责人：{biggest_growth.head()}" if biggest_growth.head() else "，**无负责人**")
        )
    if biggest_new and biggest_new.cost(curr) > 100:
        items.append(
            f"🆕 **最贵新增**：`{biggest_new.resource_name or biggest_new.resource_id}` "
            f"（{biggest_new.product}）本月开始计费 **{fmt_money(biggest_new.cost(curr))}**"
            + (f"，负责人：{biggest_new.head()}" if biggest_new.head() else "，**无负责人**")
        )
    if unowned_total > 100:
        items.append(
            f"❓ **未认领资源**：{unowned_count} 个资源累计 **{fmt_money(unowned_total)}** 没有 head 标签"
        )

    if not items:
        return render_empty("本月一切正常，无需警惕。")

    conclusion = "本月最该警惕的 " + ("3 件事" if len(items) >= 3 else f"{len(items)} 件事") + "："
    body = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(items[:3]))
    return f"**【一句话结论】**\n{conclusion}\n\n{body}"


# ─── 注册表 ─────────────────────────────────────────────


HANDLERS: dict[str, Callable[..., str]] = {
    "H-01": handle_H_01,
    "H-02": handle_H_02,
    "H-03": handle_H_03,
    "H-04": handle_H_04,
    "H-06": handle_H_06,
    "H-08": handle_H_08,
    "H-10": handle_H_10,
    "B-01": handle_B_01,
    "B-02": handle_B_02,
    "B-04": handle_B_04,
    "T-01": handle_T_01,
    "T-03": handle_T_03,
    "A-03": handle_A_03,
    "A-05": handle_A_05,
    "C-05": handle_C_05,
    "I-04": handle_I_04,
}


__all__ = ["HANDLERS"]
