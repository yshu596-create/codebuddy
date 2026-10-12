#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_billing_html.py — 把 extract_step3_data.py 的 JSON 渲染为单文件 HTML 看板（华为云）

用法：
    python3 render_billing_html.py --data /path/to/step3.json --output /path/to/billing-huawei-{project}-{domain_id}.html [--lang zh|en]
"""

import argparse
import json
import os
import sys
from html import escape
from pathlib import Path
from typing import Any, Dict, List

# ── shared team i18n dictionary (single source of truth) ──
# Anchor by file path (parents[2]: scripts -> skill -> skills root) so import is
# robust regardless of the cwd each skill is invoked from.
_LIB = Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from i18n import tr, set_llm_hook, create_llm_hook, _flush_llm_cache  # shared zh->en enum translator + LLM fallback
except Exception:
    def tr(s, lang="zh"):  # graceful fallback if shared module is unavailable
        return s
    set_llm_hook = lambda hook: None
    create_llm_hook = lambda: None
    _flush_llm_cache = lambda: None

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"


# ───────────── i18n ─────────────

I18N = {
    "zh": {
        "html_lang": "zh-CN",
        "doc_title": "华为云账单",
        # header / report
        "not_ready": "报告未就绪",
        "report_title": "☁️ 华为云账单分析报告",
        "account": "账号",
        "analysis_window": "分析窗口",
        "currency_label": "币种",
        "data_source": "数据源 · 华为云 BSS API",
        "footer": "由 cloud-billing-huawei-pro skill 渲染 · 数据来源：华为云 BSS 客户运营 API",
        # KPI
        "kpi_analysis_cost": "分析月费用",
        "kpi_mom_sub": "环比 %s（%s）",
        "kpi_comparison_cost": "对照月费用",
        "kpi_resource_change": "资源变化",
        "kpi_res_sub": "🆕 %d 新增 / ⬇️ %d 下线",
        "kpi_budget_usage": "预算消耗",
        "kpi_no_budget": "未设预算",
        "kpi_monthly_budget": "月度预算 %s",
        "kpi_set_budget_hint": "可设 HUAWEICLOUD_MONTHLY_BUDGET",
        # counts
        "cnt_ge": "（共 %d 个）",
        "cnt_item": "（共 %d 项）",
        "cnt_4": "（共 %d / %d / %d / %d）",
        # service table
        "svc_title": "📦 按云服务类型拆解",
        "th_service": "云服务",
        "th_code": "编码",
        "th_cur_cost": "当月费用",
        "th_pct": "占比",
        "th_prev_cost": "上月费用",
        "th_mom_diff": "环比差额",
        "th_mom": "环比",
        "th_change": "变化",
        # simple dim
        "ep_title": "🏢 按企业项目拆解",
        "ep_label": "企业项目 ID",
        "region_title": "🌏 按地域拆解",
        "region_label": "地域",
        # resource diff
        "rd_title": "⚖️ 资源变化",
        "rd_growth": "成本增长资源",
        "rd_decline": "成本降低资源",
        "rd_new": "新增资源",
        "rd_off": "已下线资源",
        "empty_none": "无",
        "th_resource_id": "资源 ID",
        "th_resource_name": "资源名",
        "th_region": "地域",
        "th_cur": "当月",
        "th_prev": "上月",
        "th_diff": "差额",
        # top resources
        "top_title": "🔥 Top 50 资源",
        "th_ep": "企业项目",
        # insights
        "insights_title": "🤖 智能洞察",
        # chart
        "chart_title": "📊 Top 10 云服务费用",
        "chart_dataset_label": "当月费用",
        # new modules
        "trend_title": "📈 费用趋势与预测",
        "trend_meta": "单位：万元 · 实测4月+预测3月",
        "trend_meta_nofc": "单位：万元",
        "product_share_title": "🍩 本月按云服务占比",
        "product_share_meta": "共 %d 个云服务",
        "region_chart_title": "🌏 按地域拆解",
        "region_chart_meta": "图表 + 明细",
        "ep_chart_title": "🏢 按企业项目拆解",
        "ep_chart_meta": "ID · 当月 · 上月 · 环比 · 占比",
        "ep_name_note": "ℹ️ 企业项目以 ID 标识；当前账单数据未返回项目名称，如需显示中文名，需在华为云「企业项目管理」为项目命名后下月生效，或为子账号开通 EPS 读权限后重新采集。",
        "ep_new_badge": "新增",
        "ep_prev_est_note": "ℹ️ 上月企业项目费用为估算值（按账户整体环比反推），仅供参考；如需精确值，请刷新重新采集上月资源记录。",
        # KPI Top1
        "kpi_top_product": "高消费产品 (Top 1)",
        "kpi_top_product_sub": "%s · 占比 %s",
        # forecast detail card (4-card horizontal, aligns with Tencent)
        "fc_range": "区间 %s ~ %s",
        "fc_runrate": "当月预估",
        "fc_runrate_sub": "本月实测总额",
        "fc_next_label": "下月预测 %s",
        "fc_mape_label": "回测误差 MAPE",
        "fc_method_sub": "加权移动平均 + OLS",
        "fc_mom_label": "月环比",
        "fc_mom_sub": "本月 vs 上月",
        "fc_card_title": "💡 费用预测",
        "fc_card_meta": "趋势外推 · 仅供参考",
        "fc_curmonth_label": "当月预估 %s",
        "fc_curmonth_sub": "进行中 · 趋势外推",
        "fc_basis_text": "采用「加权移动平均 + 线性外推」融合法，对近 6 个月全量总额做趋势拟合。当月为进行中预估、下月起为纯未来预测；区间宽度由历史回测误差（MAPE %s）推导。",
        "fc_disclaimer": "⚠️ 预测基于历史趋势外推，仅供参考；该账号历史月波动较大（环比绝对均值 ±%s%%），区间相应较宽，实际受新增/退订/计费调整影响。",
        # resource diff cards (flat)
        "rd_card_meta": "共 %d 项",
        "js_total_cost": "总费用",
        "js_forecast": "预测",
        "js_this_month": "（本月）",
        "js_other": "其他",
        # insights summary
        "ins_risk": "风险",
        "ins_attention": "关注",
        "ins_info": "提示",
        "ins_positive": "正向",
    },
    "en": {
        "html_lang": "en",
        "doc_title": "Huawei Cloud Billing",
        # header / report
        "not_ready": "Report Not Ready",
        "report_title": "☁️ Huawei Cloud Billing Analysis Report",
        "account": "Account",
        "analysis_window": "Analysis Window",
        "currency_label": "Currency",
        "data_source": "Data source · Huawei Cloud BSS API",
        "footer": "Rendered by cloud-billing-huawei-pro skill · Data source: Huawei Cloud BSS Customer Operations API",
        # KPI
        "kpi_analysis_cost": "Analysis Month Cost",
        "kpi_mom_sub": "MoM %s (%s)",
        "kpi_comparison_cost": "Comparison Month Cost",
        "kpi_resource_change": "Resource Changes",
        "kpi_res_sub": "🆕 %d new / ⬇️ %d decommissioned",
        "kpi_budget_usage": "Budget Usage",
        "kpi_no_budget": "No Budget Set",
        "kpi_monthly_budget": "Monthly budget %s",
        "kpi_set_budget_hint": "Set HUAWEICLOUD_MONTHLY_BUDGET",
        # counts
        "cnt_ge": " (%d total)",
        "cnt_item": " (%d total)",
        "cnt_4": " (%d / %d / %d / %d)",
        # service table
        "svc_title": "📦 Breakdown by Cloud Service",
        "th_service": "Cloud Service",
        "th_code": "Code",
        "th_cur_cost": "Current Cost",
        "th_pct": "Share",
        "th_prev_cost": "Previous Cost",
        "th_mom_diff": "MoM Diff",
        "th_mom": "MoM",
        "th_change": "Change",
        # simple dim
        "ep_title": "🏢 Breakdown by Enterprise Project",
        "ep_label": "Enterprise Project ID",
        "region_title": "🌏 Breakdown by Region",
        "region_label": "Region",
        # resource diff
        "rd_title": "⚖️ Resource Changes",
        "rd_growth": "Cost-Increasing Resources",
        "rd_decline": "Cost-Decreasing Resources",
        "rd_new": "New Resources",
        "rd_off": "Decommissioned Resources",
        "empty_none": "None",
        "th_resource_id": "Resource ID",
        "th_resource_name": "Resource Name",
        "th_region": "Region",
        "th_cur": "Current",
        "th_prev": "Previous",
        "th_diff": "Diff",
        # top resources
        "top_title": "🔥 Top 50 Resources",
        "th_ep": "Enterprise Project",
        # insights
        "insights_title": "🤖 Smart Insights",
        # chart
        "chart_title": "📊 Top 10 Cloud Service Costs",
        "chart_dataset_label": "Current Cost",
        # new modules
        "trend_title": "📈 Cost Trend & Forecast",
        "trend_meta": "Unit: 10k · 4mo actual + 3mo forecast",
        "trend_meta_nofc": "Unit: 10k",
        "product_share_title": "🍩 Cost Share by Cloud Service",
        "product_share_meta": "%d cloud services",
        "region_chart_title": "🌏 Breakdown by Region",
        "region_chart_meta": "Chart + Details",
        "ep_chart_title": "🏢 Breakdown by Enterprise Project",
        "ep_chart_meta": "ID · Current · Previous · MoM · Share",
        "ep_name_note": "ℹ️ Enterprise projects are shown by ID; the current billing data returns no project names. To display names, name the projects under Huawei Cloud 'Enterprise Project Management' (effective next month), or grant the sub-account EPS read permission and re-collect.",
        "ep_new_badge": "New",
        "ep_prev_est_note": "ℹ️ Previous-month enterprise-project costs are estimated (derived from the account-wide MoM), for reference only; refresh to re-collect last month's resource records for exact values.",
        # KPI Top1
        "kpi_top_product": "Top Spender (Top 1)",
        "kpi_top_product_sub": "%s · %s share",
        # forecast detail card (4-card horizontal, aligns with Tencent)
        "fc_range": "Range %s ~ %s",
        "fc_runrate": "This Month Est.",
        "fc_runrate_sub": "Actual month total",
        "fc_next_label": "Next Month %s",
        "fc_mape_label": "Backtest MAPE",
        "fc_method_sub": "Weighted MA + OLS",
        "fc_mom_label": "MoM Change",
        "fc_mom_sub": "This vs last month",
        "fc_card_title": "💡 Cost Forecast",
        "fc_card_meta": "Trend extrapolation · reference only",
        "fc_curmonth_label": "This Month Est. %s",
        "fc_curmonth_sub": "In progress · extrapolated",
        "fc_basis_text": "Uses a 'weighted moving average + linear extrapolation' blend, fitting the trend over the last 6 months of total spend. The current month is an in-progress estimate; from next month on it is pure forecast. Band width is derived from historical backtest error (MAPE %s).",
        "fc_disclaimer": "⚠️ Forecast is extrapolated from historical trend, for reference only; this account is historically volatile (avg abs MoM ±%s%%), so the band is correspondingly wide and subject to new/cancelled resources and billing adjustments.",
        # resource diff cards (flat)
        "rd_card_meta": "%d total",
        "js_total_cost": "Total Cost",
        "js_forecast": "Forecast",
        "js_this_month": " (this month)",
        "js_other": "Other",
        # insights summary
        "ins_risk": "Risk",
        "ins_attention": "Attention",
        "ins_info": "Info",
        "ins_positive": "Positive",
    },
}


def L(lang, key):
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


# ───────────── 工具 ─────────────

def fmt_money(v: Any, currency: str = "¥") -> str:
    if v is None:
        return "-"
    try:
        return "%s%s" % (currency, format(int(round(float(v))), ","))
    except (TypeError, ValueError):
        return "-"


def fmt_pct(v: Any, sign: bool = False) -> str:
    if v is None:
        return "-"
    try:
        f = float(v) * 100 if abs(float(v)) <= 5 else float(v)
        # extract 里 ratio 已经是 0~N 的小数（如 0.06 = 6%）；这里用阈值兼容两种表示
        # 为安全起见统一假定 ratio 已经是小数（0.06 → 6%）
        f = float(v) * 100
        return ("%+.1f%%" % f) if sign else ("%.1f%%" % f)
    except (TypeError, ValueError):
        return "-"


def fmt_wan(v: Any, currency: str = "¥", lang: str = "zh") -> str:
    """金额按「万」展示（CNY 专用）；非 CNY 退化为普通千分位金额。"""
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if currency in ("¥", "CNY"):
        unit = "万" if lang == "zh" else "0k"
        return "%s%.1f%s" % (currency, f / 10000.0, unit)
    return fmt_money(v, currency)


def safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def currency_symbol(cur: str) -> str:
    return {"CNY": "¥", "USD": "$"}.get(cur or "CNY", cur or "")


# ───────────── 渲染各模块 ─────────────

def render_kpi(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    totals = d.get("totals") or {}
    cur = totals.get("analysis") or 0
    prev = totals.get("comparison") or 0
    diff = totals.get("diff") or 0
    ratio = totals.get("ratio")

    growth_n = len((d.get("resource_diff") or {}).get("growth") or [])
    decline_n = len((d.get("resource_diff") or {}).get("decline") or [])
    new_n = len((d.get("resource_diff") or {}).get("new") or [])
    off_n = len((d.get("resource_diff") or {}).get("decommissioned") or [])

    ratio_str = ("%+.1f%%" % (ratio * 100)) if ratio is not None else "-"
    delta_color = "up" if (diff or 0) > 0 else "down"

    # 第4卡：高消费产品 Top 1
    by_service = d.get("by_service") or []
    top1 = by_service[0] if by_service else None
    if top1:
        top1_name = tr(top1.get("service_name") or "-", lang)
        top1_amt = top1.get("amount") or 0
        top1_share = (top1_amt / cur * 100) if cur > 0 else 0
        top1_value = escape(top1_name)
        top1_sub = L(lang, "kpi_top_product_sub") % (fmt_money(top1_amt, cs), "%.1f%%" % top1_share)
    else:
        top1_value = "-"
        top1_sub = "-"

    return f"""
    <div class="kpis">
      <div class="kpi">
        <div class="label">{L(lang, "kpi_analysis_cost")}</div>
        <div class="value">{fmt_money(cur, cs)}</div>
        <div class="delta {delta_color}">{L(lang, "kpi_mom_sub") % (ratio_str, fmt_money(diff, cs))}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, "kpi_comparison_cost")}</div>
        <div class="value">{fmt_money(prev, cs)}</div>
        <div class="delta flat">{escape(d.get("comparison_month") or "")}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, "kpi_resource_change")}</div>
        <div class="value">📈 {growth_n} / 📉 {decline_n}</div>
        <div class="delta flat">{L(lang, "kpi_res_sub") % (new_n, off_n)}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, "kpi_top_product")}</div>
        <div class="value" style="font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="{top1_value}">{top1_value}</div>
        <div class="delta flat">{top1_sub}</div>
      </div>
    </div>
    """


def render_service_table(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    rows = d.get("by_service") or []
    total = d.get("totals", {}).get("analysis") or 0
    if not rows:
        return ""
    html = ['<div class="card"><h2>%s<span class="meta">%s</span></h2>' % (L(lang, "svc_title"), L(lang, "cnt_ge") % len(rows))]
    html.append('<div class="scroll-y"><table>')
    html.append("<thead><tr><th>%s</th><th>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th><th>%s</th></tr></thead>" % (
        L(lang, "th_service"), L(lang, "th_code"), L(lang, "th_cur_cost"), L(lang, "th_pct"),
        L(lang, "th_prev_cost"), L(lang, "th_mom_diff"), L(lang, "th_mom"), L(lang, "th_change"),
    ))
    html.append("<tbody>")
    for r in rows:
        amt = r.get("amount") or 0
        ratio = r.get("ratio")
        ratio_str = ("%+.1f%%" % (ratio * 100)) if ratio is not None else "-"
        dval = r.get("diff") or 0
        ratio_cls = "up" if dval > 0 else ("down" if dval < 0 else "")
        # 变化 pill 徽章
        if dval > 0:
            pill = '<span class="pill up">▲</span>'
        elif dval < 0:
            pill = '<span class="pill down">▼</span>'
        else:
            pill = '<span class="pill gone">—</span>'
        pct = (amt / total * 100) if total > 0 else 0
        html.append(
            "<tr><td>%s</td><td><code>%s</code></td><td class='num'>%s</td><td class='num'>%.1f%%</td><td class='num'>%s</td><td class='num %s'>%s</td><td class='num %s'>%s</td><td>%s</td></tr>"
            % (
                escape(tr(r.get("service_name") or "-", lang)),
                escape(r.get("service_code") or "-"),
                fmt_money(amt, cs),
                pct,
                fmt_money(r.get("prev_amount"), cs),
                ratio_cls,
                fmt_money(r.get("diff"), cs),
                ratio_cls,
                ratio_str,
                pill,
            )
        )
    html.append("</tbody></table></div></div>")
    return "".join(html)


def render_simple_dim(title: str, rows: List[Dict[str, Any]], key: str, label: str, cs: str, total: float, lang: str = "zh", translate: bool = False) -> str:
    if not rows:
        return ""
    html = ['<div class="card"><h2>%s<span class="meta">%s</span></h2>' % (title, L(lang, "cnt_item") % len(rows))]
    html.append('<div class="scroll-y"><table>')
    html.append("<thead><tr><th>%s</th><th class='num'>%s</th><th class='num'>%s</th></tr></thead><tbody>" % (
        label, L(lang, "th_cur_cost"), L(lang, "th_pct"),
    ))
    for r in rows:
        amt = r.get("amount") or 0
        pct = (amt / total * 100) if total > 0 else 0
        cell = str(r.get(key) or "-")
        if translate:
            cell = tr(cell, lang)  # enum dimension (e.g. region) -> translate; business IDs stay as-is
        html.append(
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%.1f%%</td></tr>"
            % (escape(cell), fmt_money(amt, cs), pct)
        )
    html.append("</tbody></table></div></div>")
    return "".join(html)


def render_resource_diff(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """资源变化：4 张独立平铺 card（成本增长/降低/新增/下线），对齐腾讯云，不折叠。"""
    diff = d.get("resource_diff") or {}
    sections = [
        (L(lang, "rd_growth"), "growth", "up"),
        (L(lang, "rd_decline"), "decline", "down"),
        (L(lang, "rd_new"), "new", "new"),
        (L(lang, "rd_off"), "decommissioned", "gone"),
    ]
    out = []
    for title, key, pill in sections:
        items = diff.get(key) or []
        out.append('<div class="card"><h2>%s <span class="pill %s">%d</span><span class="meta">%s</span></h2>' % (
            title, pill, len(items), L(lang, "rd_card_meta") % len(items),
        ))
        if not items:
            out.append("<p class='empty'>%s</p></div>" % L(lang, "empty_none"))
            continue
        out.append('<div class="scroll-y"><table>')
        out.append("<thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th></tr></thead><tbody>" % (
            L(lang, "th_resource_id"), L(lang, "th_resource_name"), L(lang, "th_service"),
            L(lang, "th_region"), L(lang, "th_ep"), L(lang, "th_cur"), L(lang, "th_prev"), L(lang, "th_diff"),
        ))
        for it in items:
            d_amt = it.get("diff") or 0
            cls = "up" if d_amt > 0 else ("down" if d_amt < 0 else "")
            out.append(
                "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td class='num'>%s</td><td class='num'>%s</td><td class='num %s'>%s</td></tr>"
                % (
                    escape(it.get("resource_id") or "-"),
                    escape(it.get("resource_name") or "-"),
                    escape(tr(it.get("service_name") or "-", lang)),
                    escape(tr(it.get("region_code") or "-", lang)),
                    escape(it.get("enterprise_project_id") or "-"),
                    fmt_money(it.get("amount"), cs),
                    fmt_money(it.get("prev_amount"), cs),
                    cls,
                    fmt_money(d_amt, cs),
                )
            )
        out.append("</tbody></table></div></div>")
    return "".join(out)


def render_top_resources(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    rows = d.get("top_resources") or []
    if not rows:
        return ""
    html = ['<div class="card"><h2>%s</h2>' % L(lang, "top_title")]
    html.append('<div class="scroll-y"><table>')
    html.append("<thead><tr><th>#</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th class='num'>%s</th></tr></thead><tbody>" % (
        L(lang, "th_resource_id"), L(lang, "th_resource_name"), L(lang, "th_service"),
        L(lang, "th_region"), L(lang, "th_ep"), L(lang, "th_cur_cost"),
    ))
    for i, r in enumerate(rows[:50], 1):
        html.append(
            "<tr><td>%d</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td class='num'>%s</td></tr>"
            % (
                i,
                escape(r.get("resource_id") or "-"),
                escape(r.get("resource_name") or "-"),
                escape(tr(r.get("service_name") or "-", lang)),
                escape(tr(r.get("region_code") or "-", lang)),
                escape(r.get("enterprise_project_id") or "-"),
                fmt_money(r.get("amount"), cs),
            )
        )
    html.append("</tbody></table></div></div>")
    return "".join(html)


def _insights_summary(items: List[Dict[str, Any]], lang: str = "zh") -> str:
    """洞察顶部统计条（按 level 计数：风险/关注/提示/正向）。"""
    counter = {"bad": 0, "warn": 0, "info": 0, "good": 0}
    for i in items or []:
        lv = i.get("level", "info")
        if lv not in counter:
            lv = "info"
        counter[lv] += 1
    labels = [
        ("bad", L(lang, "ins_risk")),
        ("warn", L(lang, "ins_attention")),
        ("info", L(lang, "ins_info")),
        ("good", L(lang, "ins_positive")),
    ]
    parts = []
    for k, label in labels:
        if counter[k] > 0:
            parts.append('<div class="stat"><span class="dot %s"></span>%s <b>%d</b></div>' % (k, label, counter[k]))
    if not parts:
        return ""
    return '<div class="ins-summary">' + "".join(parts) + '</div>'


def render_insights(d: Dict[str, Any], lang: str = "zh") -> str:
    items = d.get("insights") or []
    if not items:
        return ""
    # 按严重度排序：bad > warn > info > good
    order = {"bad": 0, "warn": 1, "info": 2, "good": 3}
    items = sorted(items, key=lambda x: order.get(x.get("level", "info"), 9))
    html = ['<div class="card"><h2>%s</h2>%s<div class="insights-wrap">' % (
        L(lang, "insights_title"), _insights_summary(items, lang))]
    for it in items:
        level = it.get("level", "info")
        cls = level if level in ("warn", "bad", "good") else ""
        emoji = {"warn": "🟡", "bad": "🔴", "good": "🟢", "info": "🔵"}.get(level, "🔵")
        html.append(
            '<div class="ins-card %s"><div class="ins-head"><div class="ins-icon">%s</div>'
            '<div class="ins-title">%s</div></div><div class="ins-desc">%s</div></div>' % (
                cls, emoji, escape(it.get("title") or ""), escape(it.get("desc") or ""),
            )
        )
    html.append("</div></div>")
    return "".join(html)


def render_forecast_card(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """费用预测：独立整行 .forecast-card —— 横向 4 格 + 预测依据 + 免责/波动说明，对齐腾讯云。"""
    fc = d.get("forecast") or {}
    pts = fc.get("point") or []
    los = fc.get("lower") or []
    ups = fc.get("upper") or []
    mons = fc.get("months") or []
    if not pts:
        return ""
    # 月份语义（对齐自然日历）：forecast.months 基于 analysis_month 外推。
    # 账单分析典型场景=月中分析上月账单，analysis_month+1 == 当前自然月（进行中）。
    # 格1「当月预估」= months[0]（当前自然月,进行中）；格2「下月预测」= months[1]（首个纯未来月）。
    cur_m = mons[0] if len(mons) >= 1 else "-"          # 当前自然月（进行中）
    cur_point = pts[0] if len(pts) >= 1 else 0
    cur_lo = los[0] if len(los) >= 1 else cur_point
    cur_up = ups[0] if len(ups) >= 1 else cur_point
    next_m = mons[1] if len(mons) >= 2 else "-"          # 下月（首个纯未来月）
    next_point = pts[1] if len(pts) >= 2 else 0
    next_lo = los[1] if len(los) >= 2 else next_point
    next_up = ups[1] if len(ups) >= 2 else next_point

    mape = fc.get("mape_backtest")
    mape_str = ("%.1f%%" % mape) if mape is not None else "-"

    totals = d.get("totals") or {}
    cur = totals.get("analysis") or 0       # 分析月实测总额
    prev = totals.get("comparison") or 0

    if prev > 0:
        mom = (cur - prev) / prev * 100
        mom_cls = "up" if mom > 0 else ("down" if mom < 0 else "")
        mom_val = "%+.1f%%" % mom
    else:
        mom_cls = ""
        mom_val = "-"

    cur_range = L(lang, "fc_range") % (fmt_wan(cur_lo, cs, lang), fmt_wan(cur_up, cs, lang))
    next_range = L(lang, "fc_range") % (fmt_wan(next_lo, cs, lang), fmt_wan(next_up, cs, lang))

    # 预测依据（MAPE 注入）+ 免责/波动说明
    basis = L(lang, "fc_basis_text") % mape_str
    vol = fc.get("volatility_pct")
    disclaimer = L(lang, "fc_disclaimer") % (("%.0f" % vol) if vol is not None else "-")

    return f"""
    <div class="forecast-card">
      <h2>{L(lang, "fc_card_title")}<span class="meta">{L(lang, "fc_card_meta")}</span></h2>
      <div class="fc-metrics">
        <div class="fc-metric">
          <div class="fc-m-label">{L(lang, "fc_curmonth_label") % escape(cur_m)}</div>
          <div class="fc-m-value">{fmt_wan(cur_point, cs, lang)}</div>
          <div class="fc-m-sub">{L(lang, "fc_curmonth_sub")}</div>
        </div>
        <div class="fc-metric">
          <div class="fc-m-label">{L(lang, "fc_next_label") % escape(next_m)}</div>
          <div class="fc-m-value up">{fmt_wan(next_point, cs, lang)}</div>
          <div class="fc-m-sub">{next_range}</div>
        </div>
        <div class="fc-metric">
          <div class="fc-m-label">{L(lang, "fc_mape_label")}</div>
          <div class="fc-m-value">±{mape_str}</div>
          <div class="fc-m-sub">{L(lang, "fc_method_sub")}</div>
        </div>
        <div class="fc-metric">
          <div class="fc-m-label">{L(lang, "fc_mom_label")}</div>
          <div class="fc-m-value {mom_cls}">{mom_val}</div>
          <div class="fc-m-sub">{L(lang, "fc_mom_sub")}</div>
        </div>
      </div>
      <div class="fc-basis">{basis}</div>
      <div class="fc-disclaimer">{disclaimer}</div>
    </div>"""


def render_trend_card(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """趋势卡内容（左栏 1.4fr）：仅折线+预测带（预测明细已抽到独立 forecast-card）。返回单个 .card。"""
    trend = d.get("trend") or {}
    months = trend.get("months") or []
    if not months:
        return ""
    has_fc = bool(d.get("forecast"))
    trend_meta = L(lang, "trend_meta") if has_fc else L(lang, "trend_meta_nofc")
    return f"""
    <div class="card">
      <h2>{L(lang, "trend_title")}<span class="meta">{trend_meta}</span></h2>
      <div class="chart-box tall"><canvas id="trendChart"></canvas></div>
    </div>"""


def render_pie_card(d: Dict[str, Any], lang: str = "zh") -> str:
    """饼图卡内容（右栏 1fr）：本月按云服务占比环形 + 可滚动图例。返回单个 .card。"""
    by_service = d.get("by_service") or []
    if not by_service:
        return ""
    return f"""
    <div class="card">
      <h2>{L(lang, "product_share_title")}<span class="meta">{L(lang, "product_share_meta") % len(by_service)}</span></h2>
      <div class="pie-wrap">
        <div class="pie-canvas"><canvas id="productPie"></canvas></div>
        <div class="pie-legend" id="pieLegend"></div>
      </div>
    </div>"""


def render_trend_and_pie(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """趋势卡(左1.4fr) + 饼图卡(右1fr) 并排同行，对齐腾讯云 row-2 布局。"""
    left = render_trend_card(d, cs, lang)
    right = render_pie_card(d, lang)
    if not left and not right:
        return ""
    return '<div class="row-2">%s%s</div>' % (left, right)


def render_region(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """地域：左柱图 + 右滚动表，row-2.eq 并排。"""
    rows = d.get("by_region") or []
    if not rows:
        return ""
    total = d.get("totals", {}).get("analysis") or 0
    body = ['<div class="card"><h2>%s<span class="meta">%s</span></h2>' % (
        L(lang, "region_chart_title"), L(lang, "region_chart_meta"))]
    body.append('<div class="row-2 eq"><div class="chart-box"><canvas id="regionChart"></canvas></div>')
    body.append('<div class="scroll-y"><table>')
    body.append("<thead><tr><th>%s</th><th class='num'>%s</th><th class='num'>%s</th></tr></thead><tbody>" % (
        L(lang, "region_label"), L(lang, "th_cur_cost"), L(lang, "th_pct")))
    for r in rows:
        amt = r.get("amount") or 0
        pct = (amt / total * 100) if total > 0 else 0
        body.append("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%.1f%%</td></tr>" % (
            escape(tr(str(r.get("region_code") or "-"), lang)), fmt_wan(amt, cs, lang), pct))
    body.append("</tbody></table></div></div></div>")
    return "".join(body)


def render_enterprise_project(d: Dict[str, Any], cs: str, lang: str = "zh") -> str:
    """企业项目：上柱图 + 下滚动表。标签用可读名优先、id 兜底；金额按万展示。"""
    rows = d.get("by_enterprise_project") or []
    if not rows:
        return ""
    total = d.get("totals", {}).get("analysis") or 0
    body = ['<div class="card"><h2>%s<span class="meta">%s</span></h2>' % (
        L(lang, "ep_chart_title"), L(lang, "ep_chart_meta"))]
    body.append('<div class="chart-box ep-chart"><canvas id="epChart"></canvas></div>')
    body.append('<div class="scroll-y" style="margin-top:12px;"><table>')
    # 6 列：企业项目ID(完整UUID) · 当月费用(万) · 上月费用(万) · 差额(万) · 环比(%) · 占比(%)
    body.append("<thead><tr><th>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th><th class='num'>%s</th></tr></thead><tbody>" % (
        L(lang, "ep_label"), L(lang, "th_cur_cost"), L(lang, "th_prev_cost"),
        L(lang, "th_mom_diff"), L(lang, "th_mom"), L(lang, "th_pct")))
    for r in rows:
        amt = r.get("amount") or 0
        pct = (amt / total * 100) if total > 0 else 0
        # 展示完整 UUID（不截断），用 code 字体；特殊友好名（默认项目/未归集）保留可读名
        epid = str(r.get("enterprise_project_id") or "-")
        nm = r.get("enterprise_project_name") or ""
        if nm and not nm.endswith("…") and nm not in (epid,):
            label = escape(nm)
        else:
            label = "<code>%s</code>" % escape(epid)
        prev_amt = r.get("prev_amount")
        ratio = r.get("ratio")
        dval = r.get("diff") or 0
        ratio_cls = "up" if dval > 0 else ("down" if dval < 0 else "")
        # 新 EP（上月费用 0）→ 环比显"新增"徽章，避免除零；diff 显当月全额（涨色）
        if prev_amt is None or prev_amt == 0:
            ratio_str = '<span class="pill new">%s</span>' % L(lang, "ep_new_badge")
        elif ratio is not None:
            ratio_str = "%+.1f%%" % (ratio * 100)
        else:
            ratio_str = "-"
        body.append(
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num %s'>%s</td><td class='num %s'>%s</td><td class='num'>%.1f%%</td></tr>"
            % (
                label,
                fmt_wan(amt, cs, lang),
                fmt_wan(prev_amt, cs, lang),
                ratio_cls,
                fmt_wan(dval, cs, lang),
                ratio_cls,
                ratio_str,
                pct,
            )
        )
    body.append("</tbody></table></div>")
    # 项目名缺失说明（实事求是：当前账单数据未返回项目名，只有 UUID）
    body.append('<div class="ep-note">%s</div>' % L(lang, "ep_name_note"))
    # 上月费用为估算值时追加标注
    if d.get("ep_prev_estimated"):
        body.append('<div class="ep-note">%s</div>' % L(lang, "ep_prev_est_note"))
    body.append("</div>")
    return "".join(body)


def build_chart_payload(d: Dict[str, Any], lang: str = "zh") -> Dict[str, Any]:
    """汇总各图表所需数据，供页面底部统一 JS Chart.js 初始化使用。"""
    trend = d.get("trend") or {}
    by_service = d.get("by_service") or []
    by_region = d.get("by_region") or []
    by_ep = d.get("by_enterprise_project") or []
    fc = d.get("forecast")
    # 产品占比：Top 12 单列，其余归"其他"
    TOPN = 12
    prods = [{"name": tr(s.get("service_name") or "-", lang), "value": round(s.get("amount") or 0, 2)}
             for s in by_service]
    if len(prods) > TOPN:
        head = prods[:TOPN]
        rest = sum(p["value"] for p in prods[TOPN:])
        head.append({"name": L(lang, "js_other"), "value": round(rest, 2)})
        prods = head
    # 趋势图只展示最近 4 个实测月（用户要前4月=最近4实测月）
    all_months = trend.get("months") or []
    all_totals = trend.get("totals") or []
    act_months = all_months[-4:]
    act_totals = all_totals[-4:]
    cs_sym = currency_symbol(d.get("currency"))
    use_wan = cs_sym in ("¥", "CNY")
    return {
        "trend": {
            "months": act_months,
            "totals": act_totals,
        },
        "forecast": fc,    # {months:[3], point:[3], lower:[3], upper:[3], method, mape_backtest}
        "products": prods,
        "regions": [{"name": tr(str(r.get("region_code") or "-"), lang), "value": round(r.get("amount") or 0, 2)}
                    for r in by_region],
        # 柱图：X 轴 label 用完整 36 位 UUID（不截断），竖排 90° 排下；full 同值供 tooltip
        "eps": [{"name": str(e.get("enterprise_project_id") or "-"),
                 "full": str(e.get("enterprise_project_id") or "-"),
                 "value": round(e.get("amount") or 0, 2)}
                for e in by_ep],
        "currency": cs_sym,
        "use_wan": use_wan,
        "wan_label": ("万" if lang == "zh" else "0k"),
        "i18n": {
            "total_cost": L(lang, "js_total_cost"),
            "forecast": L(lang, "js_forecast"),
            "this_month": L(lang, "js_this_month"),
        },
    }


def render_charts_js(d: Dict[str, Any], lang: str = "zh") -> str:
    """页面底部统一 Chart.js 初始化脚本。"""
    payload = safe_json(build_chart_payload(d, lang))
    # 用占位符注入，避免 JS 中大量字面 % 与 %-格式化冲突
    return _CHARTS_JS_TEMPLATE.replace("/*__PAYLOAD__*/", payload)


_CHARTS_JS_TEMPLATE = """
<script>
(function(){
  if (typeof Chart === 'undefined') return;
  var DATA = /*__PAYLOAD__*/;
  var CS = DATA.currency || '\\u00a5';
  var COLORS = ['#5b8cff','#8b5cff','#36d399','#f5b455','#ef4f6f','#67c8f5',
                '#a8d36b','#ffb6c1','#9b8cff','#ffd166','#06b6d4','#f87171',
                '#34d399','#fb923c','#a78bfa'];
  Chart.defaults.color = '#aab4dc';
  Chart.defaults.borderColor = 'rgba(120,144,200,0.16)';
  var USE_WAN = !!DATA.use_wan;
  var WANL = DATA.wan_label || '\\u4e07';
  // 金额完整展示（tooltip）：万-aware
  function money(v){
    if (v == null) return '-';
    if (USE_WAN) return CS + (v/10000).toFixed(1) + WANL;
    return CS + Math.round(v).toLocaleString();
  }
  // 坐标轴刻度：万-aware
  function axisFmt(v){
    if (USE_WAN) return CS + (v/10000).toFixed(1) + WANL;
    return CS + (v/1000).toFixed(1) + 'k';
  }
  var GRID = 'rgba(120,144,200,0.10)';

  // ── Trend line：4 实测月 + 3 预测月（断点拼接 + 区间带）──
  var tEl = document.getElementById('trendChart');
  if (tEl && DATA.trend.months.length) {
    var actMonths = DATA.trend.months.slice();   // 实测月（最近4）
    var actTotals = DATA.trend.totals.slice();
    var aLen = actTotals.length;
    var fc = DATA.forecast;
    var fcMonths = (fc && fc.months) ? fc.months.slice() : [];
    var fcPts = (fc && fc.point) ? fc.point.slice() : [];
    var fcLo = (fc && fc.lower) ? fc.lower.slice() : [];
    var fcUp = (fc && fc.upper) ? fc.upper.slice() : [];
    var hasFc = fcPts.length > 0;

    // 标签 = 实测月 + 预测月
    var labels = actMonths.concat(fcMonths);
    var N = labels.length;

    // 实测 dataset：后段填 null
    var actData = actTotals.slice();
    for (var i = aLen; i < N; i++) actData.push(null);

    // 预测 dataset：前 aLen-1 个 null，在第 aLen-1 点（最后实测）衔接，再接 3 个预测点
    var fcLine = [];
    var loData = [];
    var upData = [];
    for (var j = 0; j < N; j++){ fcLine.push(null); loData.push(null); upData.push(null); }
    if (hasFc) {
      fcLine[aLen - 1] = actTotals[aLen - 1];
      loData[aLen - 1] = actTotals[aLen - 1];
      upData[aLen - 1] = actTotals[aLen - 1];
      for (var k = 0; k < fcPts.length; k++){
        fcLine[aLen + k] = fcPts[k];
        loData[aLen + k] = fcLo[k];
        upData[aLen + k] = fcUp[k];
      }
    }

    var ctx = tEl.getContext('2d');
    var grad = ctx.createLinearGradient(0, 0, 0, 280);
    grad.addColorStop(0, 'rgba(91,140,255,0.40)');
    grad.addColorStop(1, 'rgba(91,140,255,0.02)');
    var ds = [{
      label: DATA.i18n.total_cost, data: actData,
      borderColor: '#5b8cff', backgroundColor: grad, fill: true,
      tension: 0.35, borderWidth: 2.5, spanGaps: false,
      pointBackgroundColor: '#5b8cff', pointBorderColor: '#0b1020',
      pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 7
    }];
    if (hasFc) {
      ds.push({
        label: DATA.i18n.forecast, data: fcLine,
        borderColor: '#8b5cff', borderDash: [6,4], borderWidth: 2,
        backgroundColor: 'transparent', fill: false, tension: 0.2,
        pointBackgroundColor: '#8b5cff', pointBorderColor: '#0b1020',
        pointBorderWidth: 2, pointRadius: function(c){ return c.dataIndex >= aLen ? 5 : 0; },
        spanGaps: true
      });
      ds.push({
        label: 'upper', data: upData, borderColor: 'transparent',
        backgroundColor: 'rgba(139,92,255,0.16)', fill: '+1', pointRadius: 0, spanGaps: true
      });
      ds.push({
        label: 'lower', data: loData, borderColor: 'transparent',
        backgroundColor: 'rgba(139,92,255,0.16)', fill: false, pointRadius: 0, spanGaps: true
      });
    }
    new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: hasFc, labels: { filter: function(i){ return i.text !== 'upper' && i.text !== 'lower'; }, boxWidth: 10 } },
          tooltip: { callbacks: { label: function(c){ if (c.parsed.y == null) return null; return c.dataset.label + ': ' + money(c.parsed.y); } } }
        },
        scales: {
          y: { ticks: { maxTicksLimit: 6, callback: axisFmt }, grid: { color: GRID } },
          x: { grid: { color: 'rgba(120,144,200,0.06)' } }
        }
      }
    });
  }

  // ── Product doughnut + scrollable legend ──
  var pEl = document.getElementById('productPie');
  if (pEl && DATA.products.length) {
    var pTotal = DATA.products.reduce(function(s,p){ return s + (p.value||0); }, 0);
    var pColors = DATA.products.map(function(_,i){ return COLORS[i % COLORS.length]; });
    var pie = new Chart(pEl, {
      type: 'doughnut',
      data: { labels: DATA.products.map(function(p){ return p.name; }),
        datasets: [{ data: DATA.products.map(function(p){ return p.value; }), backgroundColor: pColors, borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: '64%',
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function(c){ return c.label + ': ' + money(c.parsed) + ' (' + (pTotal>0?(c.parsed/pTotal*100).toFixed(1):'0.0') + '%)'; } } } } }
    });
    var legend = document.getElementById('pieLegend');
    DATA.products.forEach(function(p, idx){
      var pct = pTotal > 0 ? (p.value / pTotal * 100) : 0;
      var item = document.createElement('div');
      item.className = 'legend-item';
      item.innerHTML = '<span class="legend-dot" style="background:' + pColors[idx] + '"></span>' +
        '<span class="legend-name" title="' + p.name + '">' + p.name + '</span>' +
        '<span class="legend-pct">' + pct.toFixed(1) + '%</span>';
      item.addEventListener('click', function(){
        var meta = pie.getDatasetMeta(0);
        meta.data[idx].hidden = !meta.data[idx].hidden;
        item.classList.toggle('dim', meta.data[idx].hidden);
        pie.update();
      });
      legend.appendChild(item);
    });
  }

  // ── Region bar (horizontal) ──
  var rEl = document.getElementById('regionChart');
  if (rEl && DATA.regions.length) {
    var rTotal = DATA.regions.reduce(function(s,r){ return s + (r.value||0); }, 0);
    new Chart(rEl, {
      type: 'bar',
      data: { labels: DATA.regions.map(function(r){ return r.name; }),
        datasets: [{ data: DATA.regions.map(function(r){ return r.value; }),
          backgroundColor: DATA.regions.map(function(_,i){ return COLORS[i % COLORS.length]; }),
          borderRadius: 6, maxBarThickness: 48 }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function(c){ return money(c.parsed.x) + ' (' + (rTotal>0?(c.parsed.x/rTotal*100).toFixed(1):'0.0') + '%)'; } } } },
        scales: { x: { ticks: { maxTicksLimit: 6, callback: axisFmt }, grid: { color: GRID } }, y: { grid: { display: false } } } }
    });
  }

  // ── Enterprise project bar (vertical) ──
  var eEl = document.getElementById('epChart');
  if (eEl && DATA.eps.length) {
    var eps = DATA.eps.slice(0, 20);
    new Chart(eEl, {
      type: 'bar',
      data: { labels: eps.map(function(e){ return e.name; }),
        datasets: [{ label: DATA.i18n.total_cost, data: eps.map(function(e){ return e.value; }),
          backgroundColor: '#5b8cff', borderRadius: 6, maxBarThickness: 40 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: {
            title: function(items){ var i = items[0] && items[0].dataIndex; return (i != null && eps[i]) ? eps[i].full : ''; },
            label: function(c){ return money(c.parsed.y); }
          } } },
        scales: { y: { ticks: { maxTicksLimit: 6, callback: axisFmt }, grid: { color: GRID } },
          x: { ticks: { maxRotation: 45, minRotation: 45, autoSkip: false, font: { size: 10 } }, grid: { display: false } } } }
    });
  }
})();
</script>
"""


# ───────────── 主入口 ─────────────

CSS = """
:root {
  color-scheme: dark;
  --bg-0: #0b1020; --bg-1: #111933; --line: rgba(120,144,200,0.16);
  --text-0: #e7ecff; --text-1: #aab4dc; --text-2: #6f7aa8;
  --accent: #5b8cff; --accent-2: #8b5cff;
  --good: #36d399; --warn: #f5b455; --bad: #ef4f6f;
  --shadow: 0 8px 32px rgba(0,0,0,0.32);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background:
  radial-gradient(1200px 600px at 10% -10%, rgba(91,140,255,0.18), transparent 60%),
  radial-gradient(900px 500px at 110% 10%, rgba(139,92,255,0.16), transparent 60%),
  var(--bg-0); color: var(--text-0); font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
  "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif; font-size: 14px; line-height: 1.55; }
.wrap { max-width: min(90vw, 1920px); margin: 0 auto; padding: 24px 28px 80px; }

/* ── hero ── */
.hero { background: linear-gradient(135deg, rgba(91,140,255,0.12), rgba(139,92,255,0.10));
  border: 1px solid var(--line); border-radius: 16px; padding: 24px 28px; margin-bottom: 20px;
  backdrop-filter: blur(8px); box-shadow: var(--shadow); }
.hero-top { display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 16px; }
.hero h1 { font-size: 22px; margin: 0 0 6px; }
.hero .sub { color: var(--text-1); font-size: 13px; }
.tag { display: inline-block; padding: 4px 10px; border-radius: 999px;
  background: rgba(91,140,255,0.18); color: #b8caff; font-size: 12px;
  border: 1px solid rgba(91,140,255,0.32); margin-left: 8px; }
.tag.warn { background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }

/* ── KPI ── */
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 20px; }
.kpi { background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 16px; position: relative; overflow: hidden; }
.kpi::after { content: ""; position: absolute; right: -30px; top: -30px;
  width: 100px; height: 100px; border-radius: 50%;
  background: radial-gradient(circle, rgba(91,140,255,0.20), transparent 70%); }
.kpi .label { color: var(--text-2); font-size: 12px; }
.kpi .value { font-size: 26px; font-weight: 700; margin-top: 6px; }
.kpi .delta { font-size: 12px; margin-top: 4px; color: var(--text-1); }
.kpi .delta.up { color: var(--bad); } .kpi .delta.down { color: var(--good); }
.kpi .delta.flat { color: var(--text-2); }

/* ── card ── */
.card { background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }
.card h2 { font-size: 15px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }
.card h2::before { content: ""; width: 3px; height: 14px; background: var(--accent); border-radius: 2px; }
.card .meta { color: var(--text-2); font-size: 12px; font-weight: 400; margin-left: auto; }

/* ── table ── */
.scroll-y { max-height: 460px; overflow: auto; border-radius: 8px; border: 1px solid var(--line); }
.scroll-y::-webkit-scrollbar { width: 8px; height: 8px; }
.scroll-y::-webkit-scrollbar-thumb { background: rgba(120,144,200,0.28); border-radius: 4px; }
.scroll-y::-webkit-scrollbar-track { background: transparent; }
table { width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }
th, td { padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--line); }
th { color: var(--text-2); font-weight: 500; font-size: 12px; letter-spacing: 0.6px;
  background: rgba(255,255,255,0.02); position: sticky; top: 0; z-index: 1; }
td.num, th.num, .num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:hover { background: rgba(91,140,255,0.06); }
.up { color: var(--bad); font-weight: 600; } .down { color: var(--good); font-weight: 600; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; background: rgba(255,255,255,0.05);
  padding: 1px 5px; border-radius: 3px; font-size: 12px; color: #b8caff; }

/* ── resource-diff pills ── */
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; vertical-align: middle; }
.pill.new { background: rgba(54,211,153,0.16); color: var(--good); border: 1px solid rgba(54,211,153,0.32); }
.pill.gone { background: rgba(111,122,168,0.20); color: var(--text-1); border: 1px solid rgba(111,122,168,0.32); }
.pill.up { background: rgba(239,79,111,0.16); color: var(--bad); border: 1px solid rgba(239,79,111,0.32); }
.pill.down { background: rgba(54,211,153,0.16); color: var(--good); border: 1px solid rgba(54,211,153,0.32); }
details { margin-bottom: 12px; }
summary { cursor: pointer; padding: 8px 0; color: var(--text-1); font-weight: 500; }
summary:hover { color: var(--text-0); }
.empty { color: var(--text-2); font-size: 13px; padding: 8px; }

/* ── AIOps-style insights ── */
.insights-wrap { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.ins-card { position: relative; background: linear-gradient(135deg, rgba(91,140,255,0.06), rgba(139,92,255,0.04));
  border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; overflow: hidden;
  transition: transform .15s, box-shadow .15s; }
.ins-card:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(91,140,255,0.15); }
.ins-card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--accent); border-radius: 2px 0 0 2px; }
.ins-card.warn::before { background: var(--warn); }
.ins-card.bad::before { background: var(--bad); }
.ins-card.good::before { background: var(--good); }
.ins-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ins-icon { width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center;
  justify-content: center; font-size: 14px; flex-shrink: 0;
  background: rgba(91,140,255,0.18); color: var(--accent); }
.ins-card.warn .ins-icon { background: rgba(245,180,85,0.18); color: var(--warn); }
.ins-card.bad .ins-icon { background: rgba(239,79,111,0.18); color: var(--bad); }
.ins-card.good .ins-icon { background: rgba(54,211,153,0.18); color: var(--good); }
.ins-title { font-weight: 600; font-size: 13.5px; color: var(--text-0); flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ins-desc { color: var(--text-1); font-size: 12.5px; line-height: 1.55; }

/* ── insights summary bar ── */
.ins-summary { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px;
  padding: 10px 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--line);
  border-radius: 10px; font-size: 12px; }
.ins-summary .stat { display: flex; align-items: center; gap: 6px; color: var(--text-1); }
.ins-summary .stat b { color: var(--text-0); font-weight: 700; font-size: 14px;
  font-variant-numeric: tabular-nums; }
.ins-summary .dot { width: 8px; height: 8px; border-radius: 50%; }
.ins-summary .dot.bad { background: var(--bad); }
.ins-summary .dot.warn { background: var(--warn); }
.ins-summary .dot.info { background: var(--accent); }
.ins-summary .dot.good { background: var(--good); }

/* ── forecast: standalone full-width card ── */
.forecast-card { background: linear-gradient(135deg, rgba(91,140,255,0.06), rgba(139,92,255,0.04));
  border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-bottom: 16px;
  box-shadow: var(--shadow); }
.forecast-card h2 { font-size: 15px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }
.forecast-card h2::before { content: ""; width: 3px; height: 14px; background: var(--accent); border-radius: 2px; }
.forecast-card .meta { color: var(--text-2); font-size: 12px; font-weight: 400; margin-left: auto; }
.fc-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.fc-metric { background: rgba(0,0,0,0.20); border: 1px solid rgba(120,144,200,0.12);
  border-radius: 10px; padding: 14px 16px; }
.fc-m-label { color: var(--text-2); font-size: 11.5px; letter-spacing: 0.3px; }
.fc-m-value { color: var(--text-0); font-size: 22px; font-weight: 700; margin-top: 5px;
  font-variant-numeric: tabular-nums; }
.fc-m-value.up { color: var(--bad); } .fc-m-value.down { color: var(--good); }
.fc-m-sub { color: var(--text-2); font-size: 11px; margin-top: 4px; }
.fc-basis { margin-top: 14px; padding: 10px 12px; font-size: 12px; color: var(--text-1);
  background: rgba(91,140,255,0.06); border: 1px solid rgba(91,140,255,0.16);
  border-radius: 8px; line-height: 1.55; }
.fc-disclaimer { margin-top: 8px; padding: 8px 12px; font-size: 11.5px; color: var(--text-1);
  background: rgba(245,180,85,0.08); border: 1px solid rgba(245,180,85,0.22);
  border-radius: 8px; line-height: 1.5; }
.ep-note { margin-top: 12px; padding: 8px 12px; font-size: 11.5px; color: var(--text-2);
  background: rgba(91,140,255,0.06); border: 1px solid rgba(91,140,255,0.16);
  border-radius: 8px; line-height: 1.55; }
@media (max-width: 980px) { .fc-metrics { grid-template-columns: repeat(2, 1fr); } }

/* ── charts + layout ── */
.row-2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
.row-2.eq { grid-template-columns: 1fr 1fr; }
.chart-box { position: relative; height: 280px; }
.chart-box.tall { height: 340px; }
.chart-box.ep-chart { height: 440px; }
.pie-wrap { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: center; height: 320px; }
.pie-canvas { position: relative; height: 100%; }
.pie-legend { max-height: 300px; overflow-y: auto; padding-right: 6px; font-size: 12px; }
.pie-legend::-webkit-scrollbar { width: 6px; }
.pie-legend::-webkit-scrollbar-thumb { background: rgba(120,144,200,0.30); border-radius: 3px; }
.pie-legend::-webkit-scrollbar-track { background: transparent; }
.legend-item { display: grid; grid-template-columns: 12px 1fr auto; gap: 8px; align-items: center;
  padding: 5px 8px; border-radius: 6px; cursor: pointer; transition: background .15s; }
.legend-item:hover { background: rgba(91,140,255,0.10); }
.legend-item.dim { opacity: 0.35; }
.legend-dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.legend-name { color: var(--text-0); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.legend-pct { color: var(--text-2); font-variant-numeric: tabular-nums; font-size: 11.5px; }

footer { color: var(--text-2); font-size: 12px; text-align: center; margin-top: 24px; }
@media (max-width: 980px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .insights-wrap { grid-template-columns: 1fr; }
  .row-2, .row-2.eq, .pie-wrap { grid-template-columns: 1fr; }
}
"""


def render_html(d: Dict[str, Any], lang: str = "zh") -> str:
    if not d.get("available"):
        return "<html><body><h1>%s</h1><p>%s</p></body></html>" % (
            L(lang, "not_ready"), escape(d.get("reason", "")),
        )
    cs = currency_symbol(d.get("currency"))
    am = d.get("analysis_month") or ""
    cm = d.get("comparison_month") or ""
    am_fmt = ("%s-%s" % (am[:4], am[4:6])) if len(am) == 6 else am
    cm_fmt = ("%s-%s" % (cm[:4], cm[4:6])) if len(cm) == 6 else cm
    total = d.get("totals", {}).get("analysis") or 0

    body = []
    body.append(f"""
    <div class="hero">
      <div class="hero-top">
        <div>
          <h1>{L(lang, "report_title")}</h1>
          <div class="sub">{L(lang, "account")}{'：' if lang == 'zh' else ':'} {escape(d.get("active_cloud") or "-")} · {escape(d.get("active_project") or "-")} · {escape(d.get("domain_name") or "-")}
          <span class="tag">domain_id {escape(d.get("domain_id") or "-")}</span>
          <span class="tag">{L(lang, "analysis_window")} {escape(am_fmt)} vs {escape(cm_fmt)}</span></div>
        </div>
        <div style="text-align: right; color: var(--text-1); font-size: 12px;">
          <div>{L(lang, "data_source")}</div>
          <div style="margin-top:4px;">{L(lang, "currency_label")} {escape(d.get("currency") or "CNY")}</div>
        </div>
      </div>
      {render_kpi(d, cs, lang)}
    </div>
    """)
    # 区块顺序（对齐腾讯云）：KPI → [趋势折线 | 饼图]同行 → 费用预测(独立整行) → 地域 → 云服务拆解 → 企业项目 → 资源变化 → Top50 → 洞察
    body.append(render_trend_and_pie(d, cs, lang))       # 趋势(左1.4fr)+饼图(右1fr) 并排 row-2
    body.append(render_forecast_card(d, cs, lang))       # 费用预测 独立整行（4格+依据+免责）
    body.append(render_region(d, cs, lang))              # 地域柱图+表（移到费用预测行下方）
    body.append(render_service_table(d, cs, lang))       # 云服务拆解表（含变化 pill 列）
    body.append(render_enterprise_project(d, cs, lang))  # 企业项目柱图+表（中文名/万）
    body.append(render_resource_diff(d, cs, lang))       # 资源变化 4 张平铺 card
    body.append(render_top_resources(d, cs, lang))
    body.append(render_insights(d, lang))                # 洞察（含 summary 统计条）
    body.append('<footer>%s</footer>' % L(lang, "footer"))

    return f"""<!DOCTYPE html>
<html lang="{L(lang, "html_lang")}"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{L(lang, "doc_title")} · {escape(am_fmt)}</title>
<script src="{CHART_JS_CDN}"></script>
<style>{CSS}</style>
</head><body>
<div class="wrap">
{''.join(body)}
</div>
{render_charts_js(d, lang)}
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="step3_data.json 路径")
    ap.add_argument("--output", help="输出 HTML 路径，省略则打印到 stdout")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="UI language for the dashboard")
    args = ap.parse_args()

    # 在任何 tr() 调用之前注册 LLM 兜底钩子
    if args.lang == "en":
        set_llm_hook(create_llm_hook())

    d = json.loads(Path(args.data).read_text(encoding="utf-8"))
    html = render_html(d, lang=args.lang)

    if args.output:
        out = Path(args.output)
        # Resolve output path: if relative, prefix with WORKBUDDY_WORKSPACE (ensures artifacts panel visibility)
        if not out.is_absolute():
            ws = os.environ.get("WORKBUDDY_WORKSPACE")
            if ws:
                out = Path(ws) / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(json.dumps({"status": "ok", "path": str(out), "bytes": len(html)}))
    else:
        sys.stdout.write(html)

    # 持久化 LLM 翻译缓存
    if args.lang == "en":
        _flush_llm_cache()

    return 0


if __name__ == "__main__":
    sys.exit(main())
