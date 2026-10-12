#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_billing_html.py — 把 extract_step3_data.py 的 JSON 渲染为单文件 HTML 看板

输入:
  --data /path/to/step3.json       # extract_step3_data.py 的输出
  --output /path/to/billing-tencent-{display_name}-uin{uin}.html   # 落盘路径（不指定则打印到 stdout）

输出:
  单文件 HTML（深色看板风 + Chart.js + 全量资源展示）
  · 默认通过 jsdelivr CDN 引入 Chart.js
  · --offline 选项内嵌 Chart.js（约 +250KB，离线场景用）

设计原则:
  · 全量展示：增长/降低/新增/下线/产品/标签 6 张表全数据出，长表 max-height 滚动
  · 不做 Top10 截断；不输出 Markdown
  · 单文件无外链资源（除 Chart.js CDN 一行 script）

字段契约（来自 extract_step3_data.py）:
  account_name / analysis_month / comparison_month / uin / budget
  totals: { current, previous, diff, ratio, ... }
  top_products: [...]                    # 按本月费用降序
  product_trend: { product_name: [m1, m2, ...] }
  trend_months_ordered: [...]            # 月份顺序
  resource_diff: {
    growth: { items: [...] },           # 已含 Tags / ResourceId / RealTotalCost / Prev / Diff
    decline: { items: [...] },
    new: { items: [...] },
    offline: { items: [...] },
  }
  summary_tag: { tag_key: { tag_value: { current, previous, diff, ratio } } }
  insights: [{ title, desc, level }]    # level: info/warn/bad
"""

import argparse
import json
import os
import re
import sys
import pathlib
from html import escape

# 团队共享 zh→en 枚举字典（唯一来源，避免各脚本各拷一份会漂移的字典）
_LIB = pathlib.Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from i18n import tr, _ZH2EN_RAW, inject_bizcode_map, reset_misses, get_translation_report, set_llm_hook, create_llm_hook, _flush_llm_cache  # shared team dictionary + dynamic BusinessCode injection + translation audit + LLM fallback

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"

# ───────────── i18n（中英双语 UI 文案） ─────────────
# 仅覆盖"面向用户可见"的文案；数据值（产品名/资源ID/地域/标签值/金额）不在此处。
I18N = {
    "zh": {
        # 报告头
        "billing_analysis": "账单分析",
        "billing_report": "账单分析报告",
        "analysis_window": "分析窗口 {a} vs 对照月 {c}",
        "data_source": "数据源 · 腾讯云账单 API",
        "kpi_monthly_budget": "月预算 {m}",
        "account_prefix": "账户 {uin}",
        "unknown_account": "未知账户",
        # KPI 卡片
        "current_spend": "本月总消费",
        "previous_spend": "上月消费",
        "budget_usage": "预算消耗率",
        "baseline": "对照基准",
        "resource_changes": "资源变动",
        "new_offline": "新增 / 下线",
        "top_product": "高消费产品 (Top 1)",
        "kpi_top1_delta": "{m} · 占比 {p}%",
        "kpi_delta_ratio": "{arrow} {m} vs 上月 ({r})",
        "kpi_delta_noratio": "{arrow} {m} vs 上月",
        # 区块标题 / meta
        "trend_title": "近 {n} 月费用趋势",
        "unit_yuan": "单位：元",
        "product_share_title": "本月按产品占比",
        "products_count": "共 {n} 个产品",
        "product_breakdown": "按产品拆解（全量 {n} 项）",
        "scrollable": "支持滚动查看",
        "region_breakdown": "按地域拆解（共 {n} 个地域）",
        "cost_desc": "费用降序",
        "cost_growth_res": "成本增长资源（共 {n} 个，全量列出）",
        "sorted_mom_inc": "环比涨幅排序",
        "cost_decline_res": "成本降低资源（共 {n} 个，全量列出）",
        "sorted_mom_dec": "环比降幅排序",
        "new_res_title": "新增资源（共 {n} 个，全量列出）",
        "new_this_month": "本月新出现",
        "offline_res_title": "已下线资源（共 {n} 个，全量列出）",
        "no_cost_this_month": "本月无费用",
        "by_tag_title": "按 {tk} 标签维度（共 {n} 项）",
        "biz_line_class": "业务条线归类",
        "tag_allocation": "标签分账",
        "smart_insights": "智能洞察",
        "insights_meta": "基于规则引擎自动生成 · 共 {n} 条",
        # 表头 / 通用标签
        "product": "产品",
        "resource_id": "资源 ID",
        "business": "业务",
        "curr_cost": "本月费用",
        "prev_cost": "上月费用",
        "curr_month": "本月",
        "prev_month": "上月",
        "diff": "差额",
        "mom": "环比",
        "share": "占比",
        "change": "变化",
        "region": "地域",
        # 洞察统计条
        "risk_items": "风险项",
        "attention_items": "关注项",
        "info_items": "提示项",
        "positive_items": "正向项",
        # footer
        "footer": "渲染 by tencent-billing-analyst@workbuddy · 单账号模式",
        # 前端 JS 可见词
        "rise": "上升",
        "fall": "下降",
        "flat": "持平",
        "untagged": "未打标",
        "unknown": "未识别",
        "no_data": "暂无数据",
        "total_cost": "总费用",
        "this_month_paren": "（本月）",
        # 洞察标题
        "ins_total_mom": "本月总费用环比",
        "ins_top1": "Top 1 产品集中度",
        "ins_prod_max_up": "产品维度：最大涨幅",
        "ins_prod_max_dn": "产品维度：最大降幅",
        "ins_res_change": "资源变动盘点",
        "ins_single_res_up": "单资源最大涨幅",
        "ins_tag_gov": "{label} 标签治理",
        "ins_head_top1": "head 维度 Top 1",
        "ins_trend": "近 {n} 月趋势研判",
        "ins_steady": "稳态运行产品",
        "ins_tide": "资源潮汐变化",
        "insight_fallback": "洞察",
        # 洞察徽标
        "first_month": "首月",
        "abnormal_surge": "异常激增",
        "marked_increase": "明显上涨",
        "slight_increase": "小幅上涨",
        "marked_decrease": "明显下降",
        "slight_decrease": "小幅下降",
        "roughly_flat": "基本持平",
        "highly_concentrated": "高度集中",
        "high_concentration": "集中度高",
        "notable_share": "占比较高",
        "balanced_dist": "分布均衡",
        "needs_review": "需复核",
        "cost_pullback": "成本回落",
        "frequent_changes": "高频变动",
        "active_changes": "活跃变更",
        "stable_changes": "变更平稳",
        "key_focus": "重点关注",
        "governance_urgent": "治理紧急",
        "tagging_needed": "需补打标",
        "overall_controllable": "总体可控",
        "ownership_profile": "归属画像",
        "trend_abnormal": "趋势异常",
        "trend_deviation": "趋势偏离",
        "trend_stable": "趋势稳定",
        "batch_changes": "批量变更",
        # 洞察 metric 标签
        "up_down": "上涨/下降",
        "total_changes": "总变更",
        "owner": "负责人",
        "untagged_cost": "未打标费用",
        "tagged_share": "已打标占比",
        "trend_n_avg": "近 N 月均值",
        "deviation": "偏离",
        "monthly_avg_cost": "月均费用",
        "volatility": "波动幅度",
        "new": "新增",
        "offline": "下线",
        "net_change": "净变更",
        # 标签治理维度标签
        "owner_head": "负责人(head)",
        "biz_line_business": "业务条线(business)",
        # 洞察 desc / action（模板）
        "ins_total_mom_desc": "差额 {d}，环比基准为上月。",
        "ins_total_mom_action_high": "若涨幅>10%，建议结合下方「产品维度变化」定位主要驱动产品。",
        "ins_total_mom_action_low": "总体波动在可控范围内，持续监控即可。",
        "ins_top1_desc": "{name} 单产品贡献了本月 {p}% 的开销。",
        "ins_top1_action_high": "建议按 region/AZ/业务条线进一步拆分，避免单点波动放大整体风险。",
        "ins_top1_action_low": "占比正常，可在产品维度下钻业务标签做精细归集。",
        "ins_prod_max_up_desc": "{a} → {b}，是本月环比涨幅最大的产品。",
        "ins_prod_max_up_action": "下钻该产品下的资源列表，定位上涨来自新增资源还是已有资源放量。",
        "ins_prod_max_dn_desc": "{a} → {b}，可能来自下线/降配/促销结束等。",
        "ins_prod_max_dn_action": "确认是预期内动作还是业务收缩，避免误读为成本优化成效。",
        "ins_res_change_desc": "本月共 {n} 个资源发生费用层面变化。",
        "ins_res_change_action": "优先关注新增 + 上涨资源是否打了 head/business 标签，确保归集口径完整。",
        "ins_single_res_up_desc": "资源 {id}：{a} → {b}。",
        "ins_single_res_up_action": "排查该资源近期是否有规格升配、计费模式切换或调用量突增。",
        "ins_tag_gov_desc": "未打 {tk} 标签的资源月费占该维度 {p}%。",
        "ins_tag_gov_action": "建议通过资源标签策略（CAM 标签 + 创建时强制）补齐，避免分账缺口。",
        "ins_head_top1_desc": "在已打标 head 维度中，{name} 是本月最大成本归属方。",
        "ins_head_top1_action": "可与该负责人核对预算口径，确认其下的资源使用预期。",
        "ins_trend_desc": "本月较前 {n} 月均值偏离 {r}。",
        "ins_trend_action": "若偏离>15%，建议结合产品/标签维度做归因；偏离<5% 可视为稳态运行。",
        "ins_steady_desc": "{names} 等产品已连续多月费用波动 ≤ 5%，处于稳态。",
        "ins_steady_action": "稳态产品适合作为预算基线，可单独抽出做预算锁定与提醒阈值设置。",
        "ins_steady_badge": "{n} 个",
        "ins_tide_desc": "本月新增 {nw} 个、下线 {go} 个资源，变动量较大。",
        "ins_tide_action": "建议批量复核资源归属：是项目上下线、容量伸缩还是测试残留。",
        "sep": "、",
    },
    "en": {
        # Report header
        "billing_analysis": "Billing Analysis",
        "billing_report": "Billing Analysis Report",
        "analysis_window": "Analysis period {a} vs baseline {c}",
        "data_source": "Data source · Tencent Cloud Billing API",
        "kpi_monthly_budget": "Monthly budget {m}",
        "account_prefix": "Account {uin}",
        "unknown_account": "Unknown account",
        # KPI cards
        "current_spend": "Total Spend (This Month)",
        "previous_spend": "Last Month Spend",
        "budget_usage": "Budget Usage",
        "baseline": "Baseline",
        "resource_changes": "Resource Changes",
        "new_offline": "New / Offline",
        "top_product": "Top Spender (Top 1)",
        "kpi_top1_delta": "{m} · {p}% share",
        "kpi_delta_ratio": "{arrow} {m} vs last month ({r})",
        "kpi_delta_noratio": "{arrow} {m} vs last month",
        # Section titles / meta
        "trend_title": "Cost Trend (Last {n} Months)",
        "unit_yuan": "Unit: CNY",
        "product_share_title": "Cost Share by Product",
        "products_count": "{n} products",
        "product_breakdown": "Cost Breakdown by Product (all {n})",
        "scrollable": "Scroll to view all",
        "region_breakdown": "Cost Breakdown by Region ({n} regions)",
        "cost_desc": "Sorted by cost (desc)",
        "cost_growth_res": "Cost-Increasing Resources (all {n})",
        "sorted_mom_inc": "Sorted by MoM increase",
        "cost_decline_res": "Cost-Decreasing Resources (all {n})",
        "sorted_mom_dec": "Sorted by MoM decrease",
        "new_res_title": "New Resources (all {n})",
        "new_this_month": "New this month",
        "offline_res_title": "Decommissioned Resources (all {n})",
        "no_cost_this_month": "No cost this month",
        "by_tag_title": "By Tag: {tk} ({n} values)",
        "biz_line_class": "Business-line grouping",
        "tag_allocation": "Tag-based allocation",
        "smart_insights": "Smart Insights",
        "insights_meta": "Auto-generated by rule engine · {n} insights",
        # Table headers / common labels
        "product": "Product",
        "resource_id": "Resource ID",
        "business": "Business",
        "curr_cost": "Current",
        "prev_cost": "Previous",
        "curr_month": "Current",
        "prev_month": "Previous",
        "diff": "Difference",
        "mom": "MoM",
        "share": "Share",
        "change": "Trend",
        "region": "Region",
        # Insight summary bar
        "risk_items": "Risk",
        "attention_items": "Watch",
        "info_items": "Info",
        "positive_items": "Positive",
        # footer
        "footer": "Rendered by tencent-billing-analyst@workbuddy · Single-account mode",
        # Front-end JS visible words
        "rise": "Up",
        "fall": "Down",
        "flat": "Flat",
        "untagged": "Untagged",
        "unknown": "Unknown",
        "no_data": "No data",
        "total_cost": "Total Cost",
        "this_month_paren": "(current)",
        # Insight titles
        "ins_total_mom": "Total Cost MoM",
        "ins_top1": "Top-1 Product Concentration",
        "ins_prod_max_up": "Product: Largest Increase",
        "ins_prod_max_dn": "Product: Largest Decrease",
        "ins_res_change": "Resource Change Overview",
        "ins_single_res_up": "Single Resource: Largest Increase",
        "ins_tag_gov": "{label} Tag Governance",
        "ins_head_top1": "Top-1 by Head",
        "ins_trend": "{n}-Month Trend Assessment",
        "ins_steady": "Steady-State Products",
        "ins_tide": "Resource Churn",
        "insight_fallback": "Insight",
        # Insight badges
        "first_month": "First Month",
        "abnormal_surge": "Abnormal Surge",
        "marked_increase": "Marked Increase",
        "slight_increase": "Slight Increase",
        "marked_decrease": "Marked Decrease",
        "slight_decrease": "Slight Decrease",
        "roughly_flat": "Roughly Flat",
        "highly_concentrated": "Highly Concentrated",
        "high_concentration": "High Concentration",
        "notable_share": "Notable Share",
        "balanced_dist": "Balanced",
        "needs_review": "Needs Review",
        "cost_pullback": "Cost Pullback",
        "frequent_changes": "Frequent Changes",
        "active_changes": "Active Changes",
        "stable_changes": "Stable",
        "key_focus": "Key Focus",
        "governance_urgent": "Governance Urgent",
        "tagging_needed": "Tagging Needed",
        "overall_controllable": "Under Control",
        "ownership_profile": "Ownership Profile",
        "trend_abnormal": "Trend Abnormal",
        "trend_deviation": "Trend Deviation",
        "trend_stable": "Trend Stable",
        "batch_changes": "Batch Changes",
        # Insight metric labels
        "up_down": "Up/Down",
        "total_changes": "Total Changes",
        "owner": "Owner",
        "untagged_cost": "Untagged Cost",
        "tagged_share": "Tagged Share",
        "trend_n_avg": "N-Month Avg",
        "deviation": "Deviation",
        "monthly_avg_cost": "Monthly Avg",
        "volatility": "Volatility",
        "new": "New",
        "offline": "Offline",
        "net_change": "Net Change",
        # Tag-governance dimension labels
        "owner_head": "Owner (head)",
        "biz_line_business": "Business Line (business)",
        # Insight desc / action (templates)
        "ins_total_mom_desc": "Difference {d}; baseline is last month.",
        "ins_total_mom_action_high": "If growth exceeds 10%, use the product-level changes below to pinpoint the main cost drivers.",
        "ins_total_mom_action_low": "Overall fluctuation is within a manageable range; keep monitoring.",
        "ins_top1_desc": "{name} alone accounts for {p}% of this month's spend.",
        "ins_top1_action_high": "Break it down further by region/AZ/business line to prevent a single point from amplifying overall risk.",
        "ins_top1_action_low": "Share is normal; drill into business tags under the product dimension for finer allocation.",
        "ins_prod_max_up_desc": "{a} → {b}; the largest MoM increase this month.",
        "ins_prod_max_up_action": "Drill into this product's resource list to see whether the rise comes from new resources or scaled-up existing ones.",
        "ins_prod_max_dn_desc": "{a} → {b}; may stem from decommissioning, downsizing, or promo expiry.",
        "ins_prod_max_dn_action": "Confirm whether this is an expected action or a business contraction; avoid misreading it as a cost-optimization win.",
        "ins_res_change_desc": "{n} resources had cost-level changes this month.",
        "ins_res_change_action": "Prioritize checking whether new and increasing resources carry head/business tags to keep allocation complete.",
        "ins_single_res_up_desc": "Resource {id}: {a} → {b}.",
        "ins_single_res_up_action": "Check whether this resource was recently upsized, switched billing mode, or saw a usage spike.",
        "ins_tag_gov_desc": "Resources without the {tk} tag account for {p}% of this dimension's monthly cost.",
        "ins_tag_gov_action": "Backfill via a resource tagging policy (CAM tags + enforce-on-create) to avoid allocation gaps.",
        "ins_head_top1_desc": "Among tagged head values, {name} is the largest cost owner this month.",
        "ins_head_top1_action": "Reconcile the budget scope with this owner and confirm expected resource usage.",
        "ins_trend_desc": "This month deviates {r} from the prior {n}-month average.",
        "ins_trend_action": "If deviation exceeds 15%, attribute it across product/tag dimensions; below 5% can be treated as steady state.",
        "ins_steady_desc": "{names} and others have held monthly cost volatility ≤ 5% for consecutive months — steady state.",
        "ins_steady_action": "Steady products make good budget baselines; isolate them for budget locking and alert-threshold setup.",
        "ins_steady_badge": "{n} items",
        "ins_tide_desc": "{nw} resources added and {go} decommissioned this month — significant churn.",
        "ins_tide_action": "Batch-review resource ownership: project on/offboarding, capacity scaling, or test leftovers.",
        "sep": ", ",
    },
}


def L(lang, key):
    """按语言取 UI 文案；缺失时回退中文，再回退 key 本身。"""
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


# ───────────── 数据值 zh→en 术语映射 ─────────────
# tr() / _ZH2EN_RAW 已抽取到团队共享模块 cloudops-accounts-pro/scripts/lib/i18n.py
# （文件顶部已 import）。此处不再保留内联副本——单一数据源，避免字典漂移。
# ❌ 提醒：tr() 仅用于标准枚举值（产品名/地域名/账户前缀/洞察话术），
#    绝不用于自定义标签值（head/business/customer 等真实业务名）。


# ───────────── 工具 ─────────────

def fmt_money(v):
    if v is None:
        return "-"
    try:
        return f"¥{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "-"


def fmt_pct(v, sign=False):
    if v is None:
        return "-"
    try:
        f = float(v)
        s = f"{f:+.1f}%" if sign else f"{f:.1f}%"
        return s
    except (TypeError, ValueError):
        return "-"


def safe_json_dumps(obj):
    """嵌入 <script> 时避免 </script> 注入"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ───────────── 数据加工 ─────────────

def normalize_resource_items(items, kind, product_code_to_name=None, lang="zh"):
    """把 resource_diff items 归一化成前端友好的扁平结构。

    支持的输入字段（兼容 extract_step3_data.py 输出 + 历史 mock 格式）：
    - id: ResourceId / InstanceId / ResourceID
    - 产品名: ProductName 优先；为空时用 ProductCode 反查 product_code_to_name
    - 当前费用: CurrCost (extract) / RealTotalCost / Current / Cost
    - 上月费用: PrevCost (extract) / Previous / Prev
    - 标签: _tags (extract，已展开 head/business 等) / Tags / ResourceTags
    """
    pcm = product_code_to_name or {}
    out = []
    for it in items:
        rid = it.get("ResourceId") or it.get("InstanceId") or it.get("ResourceID") or "-"
        # 产品名：直接用上游 ProductName（已在 extract 阶段从账单原始数据回填）；都没有就 fallback
        pname = (it.get("ProductName") or "").strip()
        pcode = it.get("ProductCode") or ""
        if not pname:
            pname = pcm.get(pcode) or it.get("ProductCodeName") or it.get("BusinessCodeName") or pcode or "-"
        # 当前/上月费用
        cur = (
            it.get("CurrCost")
            if it.get("CurrCost") is not None
            else it.get("RealTotalCost") or it.get("Current") or it.get("Cost") or 0
        )
        prev = (
            it.get("PrevCost")
            if it.get("PrevCost") is not None
            else it.get("Previous") or it.get("Prev") or 0
        )
        try:
            cur = float(cur)
        except (TypeError, ValueError):
            cur = 0.0
        try:
            prev = float(prev)
        except (TypeError, ValueError):
            prev = 0.0
        # tags：head / biz 各自独立，未打标统一成空串（前端样式区分）
        tags_obj = it.get("_tags") or it.get("Tags") or it.get("ResourceTags") or {}
        head_v = ""
        biz_v = ""
        if isinstance(tags_obj, dict):
            head_v = tags_obj.get("head") or ""
            biz_v = (
                tags_obj.get("business")
                or tags_obj.get("customer")
                or tags_obj.get("BizName")
                or tags_obj.get("biz_name")
                or ""
            )
        # 兼容上一版“未打标”字面量
        if head_v == "未打标":
            head_v = ""
        if biz_v == "未打标":
            biz_v = ""
        out.append({
            "id": rid,
            "prod": tr(pname, lang),
            "head": head_v,
            "biz": biz_v,
            "cur": cur,
            "prev": prev,
            "diff": cur - prev,
            "kind": kind,
        })
    # 按 |diff| 降序（new 资源用 cur 排序）
    out.sort(key=lambda r: abs(r["diff"]) if r["diff"] else r["cur"], reverse=True)
    return out


def normalize_products(top_products, lang="zh"):
    """拍平 top_products 为前端表格用结构。

    extract_step3_data.py 输出字段：name / curr_cost / prev_cost / ratio_in_total
    历史 mock 字段：ProductName / Current / Previous
    """
    out = []
    for p in top_products or []:
        name = (
            p.get("name")
            or p.get("ProductName")
            or p.get("ProductCode")
            or "-"
        )
        cur = (
            p.get("curr_cost")
            if p.get("curr_cost") is not None
            else p.get("Current") or p.get("RealTotalCost") or p.get("cur") or 0
        )
        prev = (
            p.get("prev_cost")
            if p.get("prev_cost") is not None
            else p.get("Previous") or p.get("PrevCost") or p.get("prev") or 0
        )
        try:
            cur = float(cur)
        except (TypeError, ValueError):
            cur = 0.0
        try:
            prev = float(prev)
        except (TypeError, ValueError):
            prev = 0.0
        out.append({"name": tr(name, lang), "cur": cur, "prev": prev})
    out.sort(key=lambda p: p["cur"], reverse=True)
    return out


def normalize_regions(top_regions, lang="zh"):
    """拍平 top_regions 为前端表格用结构。

    extract_step3_data.py 输出字段：name / cost / ratio_pct
    """
    out = []
    for r in top_regions or []:
        name = (
            r.get("name")
            or r.get("RegionName")
            or r.get("RegionId")
            or "-"
        )
        cost = (
            r.get("cost")
            if r.get("cost") is not None
            else r.get("RealTotalCost") or r.get("Cost") or 0
        )
        ratio = r.get("ratio_pct") or r.get("ratio") or 0
        # ratio_pct 可能是 "80.3%" 格式字符串，需剥离百分号
        if isinstance(ratio, str):
            ratio = ratio.replace("%", "").strip()
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            ratio = 0.0
        out.append({"name": tr(name, lang), "cur": cost, "ratio_pct": ratio})
    out.sort(key=lambda r: r["cur"], reverse=True)
    return out


def normalize_tags(summary_tag):
    """summary_tag 兼容两种结构，返回所有维度的扁平列表 + 默认主维度。

    新格式（extract_step3_data.py）:
      { tag_key: [ {TagValue, 202605, 202604}, ... ] }
    旧格式（mock）:
      { tag_key: { tag_value: { current, previous } } }

    返回 (tag_groups: {tag_key: [{name, cur, prev}, ...]}, default_tag_key)
    """
    if not summary_tag:
        return {}, ""
    groups = {}
    for tag_key, sub in summary_tag.items():
        flat = []
        if isinstance(sub, list):
            if sub:
                sample = sub[0]
                month_keys = sorted([k for k in sample.keys() if isinstance(k, str) and k.isdigit() and len(k) == 6])
                cur_key = month_keys[-1] if month_keys else None
                prev_key = month_keys[-2] if len(month_keys) >= 2 else None
                for item in sub:
                    name = item.get("TagValue") or item.get("tag_value") or ""
                    if not str(name).strip():
                        name = ""
                    cur = item.get(cur_key, 0) if cur_key else 0
                    prev = item.get(prev_key, 0) if prev_key else 0
                    try:
                        cur = float(cur or 0)
                    except (TypeError, ValueError):
                        cur = 0.0
                    try:
                        prev = float(prev or 0)
                    except (TypeError, ValueError):
                        prev = 0.0
                    flat.append({"name": str(name), "cur": cur, "prev": prev})
        elif isinstance(sub, dict):
            for tag_value, vals in sub.items():
                if not isinstance(vals, dict):
                    continue
                cur = vals.get("current") or vals.get("Current") or 0
                prev = vals.get("previous") or vals.get("Previous") or 0
                try:
                    cur = float(cur)
                except (TypeError, ValueError):
                    cur = 0.0
                try:
                    prev = float(prev)
                except (TypeError, ValueError):
                    prev = 0.0
                flat.append({"name": str(tag_value), "cur": cur, "prev": prev})
        flat.sort(key=lambda x: x["cur"], reverse=True)
        if flat:
            groups[tag_key] = flat

    # 默认主维度优先级：head > business > customer > 其他第一个
    preferred = [k for k in ("head", "business", "customer", "BizName", "biz_name") if k in groups]
    default_key = preferred[0] if preferred else (next(iter(groups.keys()), "") if groups else "")
    return groups, default_key


def normalize_trend(product_trend, months_ordered):
    """汇总每月总费用 + 趋势线。

    支持两种 product_trend 格式：
    - extract 输出：{ name: {icon, label, costs_by_month: {YYYYMM: cost}} }
    - 旧 mock：{ name: [v1, v2, v3] }（数组）
    """
    months = list(months_ordered or [])
    # 收集所有月份（兜底）
    if not months and product_trend:
        for v in product_trend.values():
            if isinstance(v, dict):
                cbm = v.get("costs_by_month", {})
                months = sorted(cbm.keys())
                break
            elif isinstance(v, list):
                months = [f"M-{i+1}" for i in range(len(v))]
                break
    total_per_month = [0.0] * len(months)
    for v in (product_trend or {}).values():
        if isinstance(v, dict):
            cbm = v.get("costs_by_month", {})
            for i, m in enumerate(months):
                try:
                    total_per_month[i] += float(cbm.get(m, 0) or 0)
                except (TypeError, ValueError):
                    pass
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if i >= len(total_per_month):
                    break
                try:
                    total_per_month[i] += float(x or 0)
                except (TypeError, ValueError):
                    pass
    return months, total_per_month


# ───────────── 渲染 ─────────────

def normalize_insights(insights, lang="zh"):
    """归一化洞察项，统一为前端 AIOps 卡片所需结构。

    支持三种输入：
    - AIOps 格式（derive_insights 新版）：{title, desc, level, icon, badge, metrics, action}
    - extract 输出: [{level: "🔴/🟡/🟢", text: "**title**: desc..."}]
    - 旧 mock: [{title, desc, level: "info/warn/bad"}]
    """
    out = []
    icon_to_level = {"🔴": "bad", "🟡": "warn", "🟢": "info"}
    valid_levels = ("warn", "bad", "info", "good")
    for ins in insights or []:
        if not isinstance(ins, dict):
            continue
        # 新 AIOps 格式（已含 metrics）— 翻译 title/desc/action 后透传
        if "metrics" in ins or "icon" in ins:
            out.append({
                "title": tr(ins.get("title", "-"), lang),
                "desc": tr(ins.get("desc", ""), lang),
                "level": ins.get("level") if ins.get("level") in valid_levels else "info",
                "icon": ins.get("icon", "🔍"),
                "badge": ins.get("badge", ""),
                "metrics": ins.get("metrics") or [],
                "action": tr(ins.get("action", ""), lang),
            })
            continue
        if "title" in ins or "desc" in ins:
            # 旧格式 — 补默认字段
            out.append({
                "title": ins.get("title", "-"),
                "desc": ins.get("desc", ""),
                "level": ins.get("level") if ins.get("level") in valid_levels else "info",
                "icon": "📌",
                "badge": "",
                "metrics": [],
                "action": "",
            })
            continue
        # extract 格式
        text = ins.get("text", "")
        level_icon = ins.get("level", "")
        level = icon_to_level.get(level_icon, "info")
        title = ""
        desc = text
        if text.startswith("**"):
            end = text.find("**", 2)
            if end > 2:
                title = text[2:end]
                desc = text[end+2:].lstrip(":：").strip()
        if not title:
            title = L(lang, "insight_fallback")
        # 翻译标准枚举型洞察话术（产品/地域/固定措辞）；不含自定义标签值
        out.append({
            "title": tr(title, lang), "desc": tr(desc, lang), "level": level,
            "icon": "🔍", "badge": "", "metrics": [], "action": "",
        })
    return out


def build_data(raw, lang="zh"):
    """把 extract_step3_data.py 的 JSON 处理成前端用的简洁结构"""
    pcm = raw.get("product_code_to_name") or {}
    rd = raw.get("resource_diff", {}) or {}
    growth = normalize_resource_items((rd.get("growth") or {}).get("items", []), "up", pcm, lang)
    decline = normalize_resource_items((rd.get("decline") or {}).get("items", []), "down", pcm, lang)
    new_res = normalize_resource_items((rd.get("new") or {}).get("items", []), "new", pcm, lang)
    gone = normalize_resource_items((rd.get("offline") or {}).get("items", []), "gone", pcm, lang)

    products = normalize_products(raw.get("top_products", []), lang)
    regions = normalize_regions(raw.get("top_regions", []), lang)
    tag_groups, default_tag_key = normalize_tags(raw.get("summary_tag", {}))
    months, total_per_month = normalize_trend(
        raw.get("product_trend", {}), raw.get("trend_months_ordered", [])
    )

    totals = raw.get("totals", {}) or {}

    base = {
        "meta": {
            "account_name": tr(raw.get("account_name"), lang) or (L(lang, "account_prefix").format(uin=raw.get("uin")) if raw.get("uin") else L(lang, "unknown_account")),
            "analysis_month": raw.get("analysis_month") or "-",
            "comparison_month": raw.get("comparison_month") or "-",
            "uin": raw.get("uin") or "-",
            "budget": raw.get("budget"),
            "current": (
                totals.get("curr_total")
                if totals.get("curr_total") is not None
                else totals.get("current") or totals.get("Current") or 0
            ),
            "previous": (
                totals.get("prev_total")
                if totals.get("prev_total") is not None
                else totals.get("previous") or totals.get("Previous") or 0
            ),
            "diff": totals.get("diff") or 0,
            "ratio": totals.get("ratio_pct") or totals.get("ratio"),
            "resource_total": (
                len(new_res) + len(gone) + len(growth) + len(decline)
            ),
        },
        "products": products,
        "regions": regions,
        "tag_groups": tag_groups,
        "default_tag_key": default_tag_key,
        "trend": {"months": months, "total": total_per_month},
        "growth": growth,
        "decline": decline,
        "new": new_res,
        "gone": gone,
    }
    # 优先用上游 insights；为空或不足 3 条则自动衍生
    upstream_insights = normalize_insights(raw.get("insights") or [], lang)
    derived = derive_insights(base, raw, lang)
    if len(upstream_insights) >= 3:
        base["insights"] = upstream_insights + [d for d in derived if d["title"] not in {x["title"] for x in upstream_insights}]
    else:
        base["insights"] = (upstream_insights + derived)[:14]
    return base


def derive_insights(d, raw=None, lang="zh"):
    """基于 totals/products/resource_diff/tag_groups/trend 自动衍生 AIOps 风格洞察。

    输出字段（每条洞察）：
      title    - 卡片标题
      desc     - 一句话描述
      level    - info / warn / bad / good (good 用于稳态/正向趋势)
      icon     - 卡片左上角小图标（emoji）
      badge    - 卡片右上角徽标（如"风险" "关注" "稳态"）
      metrics  - [{label, value, mode: up/down/flat/none}, ...] 关键数值（最多 3 个）
      action   - 一句行动建议（不做降本判定，只提示运维动作方向）
    """
    out = []
    cur = float(d["meta"].get("current") or 0)
    prev = float(d["meta"].get("previous") or 0)
    diff = cur - prev
    ratio = (diff / prev * 100) if prev > 0 else None
    am = d["meta"].get("analysis_month", "")
    cm = d["meta"].get("comparison_month", "")

    def m(label, value, mode="none"):
        return {"label": label, "value": value, "mode": mode}

    # 1) 总费用环比
    if cur > 0 or prev > 0:
        if ratio is None:
            lvl, badge = "info", L(lang, "first_month")
        elif ratio >= 20:
            lvl, badge = "bad", L(lang, "abnormal_surge")
        elif ratio >= 10:
            lvl, badge = "bad", L(lang, "marked_increase")
        elif ratio >= 3:
            lvl, badge = "warn", L(lang, "slight_increase")
        elif ratio <= -10:
            lvl, badge = "good", L(lang, "marked_decrease")
        elif ratio <= -3:
            lvl, badge = "info", L(lang, "slight_decrease")
        else:
            lvl, badge = "info", L(lang, "roughly_flat")
        ratio_str = f"{ratio:+.1f}%" if ratio is not None else "—"
        diff_mode = "up" if diff > 0 else "down" if diff < 0 else "flat"
        out.append({
            "title": L(lang, "ins_total_mom"), "level": lvl, "icon": "💰", "badge": badge,
            "metrics": [
                m(am, fmt_money(cur)),
                m(cm, fmt_money(prev)),
                m(L(lang, "mom"), ratio_str, diff_mode),
            ],
            "desc": L(lang, "ins_total_mom_desc").format(d=f"{('+' if diff>=0 else '−')}{fmt_money(abs(diff))}"),
            "action": L(lang, "ins_total_mom_action_high") if (ratio and ratio >= 10) else L(lang, "ins_total_mom_action_low"),
        })

    products = d.get("products") or []

    # 2) Top 1 产品占比（高占比风险）
    if products and cur > 0:
        top1 = products[0]
        share = top1["cur"] / cur * 100
        if share >= 70:
            lvl, badge = "bad", L(lang, "highly_concentrated")
        elif share >= 50:
            lvl, badge = "warn", L(lang, "high_concentration")
        elif share >= 30:
            lvl, badge = "info", L(lang, "notable_share")
        else:
            lvl, badge = "info", L(lang, "balanced_dist")
        out.append({
            "title": L(lang, "ins_top1"), "level": lvl, "icon": "🎯", "badge": badge,
            "metrics": [
                m(L(lang, "product"), top1["name"]),
                m(L(lang, "curr_cost"), fmt_money(top1["cur"])),
                m(L(lang, "share"), f"{share:.1f}%"),
            ],
            "desc": L(lang, "ins_top1_desc").format(name=top1['name'], p=f"{share:.1f}"),
            "action": L(lang, "ins_top1_action_high") if share >= 50 else L(lang, "ins_top1_action_low"),
        })

    # 3) 产品维度环比涨/跌幅最大
    pmoves = []
    for p in products:
        if p["prev"] > 0:
            r = (p["cur"] - p["prev"]) / p["prev"] * 100
            pmoves.append((p["name"], p["cur"], p["prev"], p["cur"] - p["prev"], r))
    if pmoves:
        max_up = max(pmoves, key=lambda x: x[4])
        if max_up[4] >= 20:
            lvl = "bad" if max_up[4] >= 50 else "warn"
            out.append({
                "title": L(lang, "ins_prod_max_up"), "level": lvl, "icon": "📈", "badge": L(lang, "needs_review"),
                "metrics": [
                    m(L(lang, "product"), max_up[0]),
                    m(L(lang, "diff"), f"+{fmt_money(max_up[3])}", "up"),
                    m(L(lang, "mom"), f"+{max_up[4]:.1f}%", "up"),
                ],
                "desc": L(lang, "ins_prod_max_up_desc").format(a=fmt_money(max_up[2]), b=fmt_money(max_up[1])),
                "action": L(lang, "ins_prod_max_up_action"),
            })
        max_dn = min(pmoves, key=lambda x: x[4])
        if max_dn[4] <= -10:
            out.append({
                "title": L(lang, "ins_prod_max_dn"), "level": "good", "icon": "📉", "badge": L(lang, "cost_pullback"),
                "metrics": [
                    m(L(lang, "product"), max_dn[0]),
                    m(L(lang, "diff"), f"{fmt_money(max_dn[3])}", "down"),
                    m(L(lang, "mom"), f"{max_dn[4]:+.1f}%", "down"),
                ],
                "desc": L(lang, "ins_prod_max_dn_desc").format(a=fmt_money(max_dn[2]), b=fmt_money(max_dn[1])),
                "action": L(lang, "ins_prod_max_dn_action"),
            })

    # 4) 资源变动盘点
    g = len(d.get("growth", [])); de = len(d.get("decline", []))
    nw = len(d.get("new", [])); go = len(d.get("gone", []))
    if g + de + nw + go > 0:
        total_change = g + de + nw + go
        if nw + go >= 50 or g + de >= 80:
            lvl, badge = "warn", L(lang, "frequent_changes")
        elif total_change >= 20:
            lvl, badge = "info", L(lang, "active_changes")
        else:
            lvl, badge = "info", L(lang, "stable_changes")
        out.append({
            "title": L(lang, "ins_res_change"), "level": lvl, "icon": "🔄", "badge": badge,
            "metrics": [
                m(L(lang, "up_down"), f"{g} / {de}"),
                m(L(lang, "new_offline"), f"{nw} / {go}"),
                m(L(lang, "total_changes"), str(total_change)),
            ],
            "desc": L(lang, "ins_res_change_desc").format(n=total_change),
            "action": L(lang, "ins_res_change_action"),
        })

    # 5) 单资源最大涨幅
    if d.get("growth"):
        top_r = d["growth"][0]
        if top_r["prev"] > 0:
            r = (top_r["cur"] - top_r["prev"]) / top_r["prev"] * 100
            lvl = "bad" if r >= 100 else "warn"
            out.append({
                "title": L(lang, "ins_single_res_up"), "level": lvl, "icon": "🚨", "badge": L(lang, "key_focus"),
                "metrics": [
                    m(L(lang, "product"), top_r["prod"] if top_r["prod"] not in ("-", "") else L(lang, "unknown")),
                    m(L(lang, "diff"), f"+{fmt_money(top_r['diff'])}", "up"),
                    m(L(lang, "mom"), f"+{r:.1f}%", "up"),
                ],
                "desc": L(lang, "ins_single_res_up_desc").format(id=top_r['id'], a=fmt_money(top_r['prev']), b=fmt_money(top_r['cur'])),
                "action": L(lang, "ins_single_res_up_action"),
            })

    # 6/7) 标签治理：head 和 business 的未打标占比
    tag_groups = d.get("tag_groups") or {}
    for tk_name, label_key in (("head", "owner_head"), ("business", "biz_line_business")):
        label = L(lang, label_key)
        items = tag_groups.get(tk_name) or []
        if not items: continue
        tot_cur = sum(t["cur"] for t in items) or 1
        unset = next((t for t in items if not str(t["name"]).strip()), None)
        if unset:
            pct = unset["cur"] / tot_cur * 100
            r_unset = ((unset["cur"] - unset["prev"]) / unset["prev"] * 100) if unset["prev"] > 0 else None
            if pct >= 30:
                lvl, badge = "bad", L(lang, "governance_urgent")
            elif pct >= 10:
                lvl, badge = "warn", L(lang, "tagging_needed")
            else:
                lvl, badge = "info", L(lang, "overall_controllable")
            metrics = [
                m(L(lang, "untagged_cost"), fmt_money(unset["cur"])),
                m(L(lang, "share"), f"{pct:.1f}%"),
            ]
            if r_unset is not None:
                metrics.append(m(L(lang, "mom"), f"{r_unset:+.1f}%", "up" if r_unset > 0 else "down"))
            out.append({
                "title": L(lang, "ins_tag_gov").format(label=label), "level": lvl, "icon": "🏷️", "badge": badge,
                "metrics": metrics,
                "desc": L(lang, "ins_tag_gov_desc").format(tk=tk_name, p=f"{pct:.1f}"),
                "action": L(lang, "ins_tag_gov_action"),
            })

    # 8) 标签集中度（除未打标外的 Top 1）
    head_items = [t for t in (tag_groups.get("head") or []) if str(t["name"]).strip()]
    if head_items:
        top_head = max(head_items, key=lambda t: t["cur"])
        tot = sum(t["cur"] for t in head_items) or 1
        pct = top_head["cur"] / tot * 100
        out.append({
            "title": L(lang, "ins_head_top1"), "level": "info", "icon": "👤", "badge": L(lang, "ownership_profile"),
            "metrics": [
                m(L(lang, "owner"), top_head["name"]),
                m(L(lang, "curr_cost"), fmt_money(top_head["cur"])),
                m(L(lang, "tagged_share"), f"{pct:.1f}%"),
            ],
            "desc": L(lang, "ins_head_top1_desc").format(name=top_head['name']),
            "action": L(lang, "ins_head_top1_action"),
        })

    # 9) 趋势观察
    months = d.get("trend", {}).get("months", [])
    totals = d.get("trend", {}).get("total", [])
    if len(totals) >= 3:
        last = totals[-1]; mavg = sum(totals[:-1]) / max(1, len(totals)-1)
        if mavg > 0:
            r = (last - mavg) / mavg * 100
            if abs(r) >= 30:
                lvl, badge = "bad", L(lang, "trend_abnormal")
            elif abs(r) >= 15:
                lvl, badge = "warn", L(lang, "trend_deviation")
            else:
                lvl, badge = "good", L(lang, "trend_stable")
            out.append({
                "title": L(lang, "ins_trend").format(n=len(totals)), "level": lvl, "icon": "📊", "badge": badge,
                "metrics": [
                    m(L(lang, "curr_month"), fmt_money(last)),
                    m(L(lang, "trend_n_avg"), fmt_money(mavg)),
                    m(L(lang, "deviation"), f"{r:+.1f}%", "up" if r > 0 else "down"),
                ],
                "desc": L(lang, "ins_trend_desc").format(n=len(totals)-1, r=f"{r:+.1f}%"),
                "action": L(lang, "ins_trend_action"),
            })

    # 10) 持续平稳产品（基于 product_trend.costs_by_month 多月波动 < 5%）
    if raw is not None:
        ptrend = raw.get("product_trend", {}) or {}
        steady = []
        for pname, pinfo in ptrend.items():
            if not isinstance(pinfo, dict):
                continue
            cbm = pinfo.get("costs_by_month") or {}
            vals = [float(cbm.get(mt, 0) or 0) for mt in months]
            vals = [v for v in vals if v > 0]
            if len(vals) >= 3:
                avg = sum(vals) / len(vals)
                if avg < 100:
                    continue
                spread = (max(vals) - min(vals)) / avg * 100
                if spread <= 5:
                    steady.append((tr(pname, lang), vals[-1]))
        if steady:
            steady.sort(key=lambda x: -x[1])
            top_steady = steady[:3]
            names = [n for n, _ in top_steady]
            out.append({
                "title": L(lang, "ins_steady"), "level": "good", "icon": "✅", "badge": L(lang, "ins_steady_badge").format(n=len(steady)),
                "metrics": [
                    m("Top 1", names[0] if names else "-"),
                    m(L(lang, "monthly_avg_cost"), fmt_money(top_steady[0][1]) if top_steady else "-"),
                    m(L(lang, "volatility"), "≤ 5%"),
                ],
                "desc": L(lang, "ins_steady_desc").format(names=L(lang, "sep").join(names)),
                "action": L(lang, "ins_steady_action"),
            })

    # 11) 新增/下线集中度提示
    nw = len(d.get("new", [])); go = len(d.get("gone", []))
    if nw >= 20 or go >= 10:
        lvl = "warn" if nw + go >= 50 else "info"
        out.append({
            "title": L(lang, "ins_tide"), "level": lvl, "icon": "🌊", "badge": L(lang, "batch_changes"),
            "metrics": [
                m(L(lang, "new"), f"+{nw}", "up" if nw > 0 else "flat"),
                m(L(lang, "offline"), f"-{go}", "down" if go > 0 else "flat"),
                m(L(lang, "net_change"), f"{nw - go:+d}"),
            ],
            "desc": L(lang, "ins_tide_desc").format(nw=nw, go=go),
            "action": L(lang, "ins_tide_action"),
        })

    return out


def _build_insights_summary(insights, lang="zh"):
    """生成洞察顶部统计条 HTML（按 level 分组计数）"""
    counter = {"bad": 0, "warn": 0, "info": 0, "good": 0}
    for i in insights or []:
        lv = i.get("level", "info")
        if lv not in counter:
            lv = "info"
        counter[lv] += 1
    badge_label = [
        ("bad",  L(lang, "risk_items")),
        ("warn", L(lang, "attention_items")),
        ("info", L(lang, "info_items")),
        ("good", L(lang, "positive_items")),
    ]
    parts = []
    for k, label in badge_label:
        if counter[k] > 0:
            parts.append(f'<div class="stat"><span class="dot {k}"></span>{label} <b>{counter[k]}</b></div>')
    if not parts:
        return ""
    return '<div class="ins-summary">' + ''.join(parts) + '</div>'


def render_html(data, *, offline_chartjs_path=None, lang="zh"):
    """生成单文件 HTML"""
    # Chart.js script 标签
    if offline_chartjs_path:
        with open(offline_chartjs_path, "r", encoding="utf-8") as f:
            chartjs = f.read()
        chartjs_tag = f"<script>{chartjs}</script>"
    else:
        chartjs_tag = f'<script src="{CHART_JS_CDN}"></script>'

    meta = data["meta"]
    title = (
        f"{escape(meta['account_name'])} {escape(meta['analysis_month'])} {L(lang, 'billing_analysis')}"
    )

    # KPI 计算
    cur = float(meta.get("current") or 0)
    prev = float(meta.get("previous") or 0)
    diff = cur - prev
    ratio = (diff / prev * 100) if prev > 0 else None
    diff_cls = "up" if diff > 0 else "down" if diff < 0 else "flat"
    diff_arrow = "▲" if diff > 0 else "▼" if diff < 0 else "—"
    ratio_str = (
        L(lang, "kpi_delta_ratio").format(arrow=diff_arrow, m=fmt_money(abs(diff)), r=f"{ratio:+.1f}%")
        if ratio is not None
        else L(lang, "kpi_delta_noratio").format(arrow=diff_arrow, m=fmt_money(abs(diff)))
    )
    budget = meta.get("budget")
    budget_pct = (cur / float(budget) * 100) if (budget and float(budget) > 0) else None

    # 头部 Top1 产品
    top1 = data["products"][0] if data["products"] else None
    top1_share = (top1["cur"] / cur * 100) if (top1 and cur > 0) else None

    # 全量数据序列化（前端 JS 直接用）
    payload = {
        "products": data["products"],
        "regions": data["regions"],
        "tag_groups": data["tag_groups"],
        "default_tag_key": data["default_tag_key"],
        "trend": data["trend"],
        "growth": data["growth"],
        "decline": data["decline"],
        "new": data["new"],
        "gone": data["gone"],
        "insights": data["insights"],
    }

    tag_counts = {k: len(v) for k, v in data["tag_groups"].items()}
    counts = {
        "products": len(data["products"]),
        "regions": len(data["regions"]),
        "tags": tag_counts,
        "growth": len(data["growth"]),
        "decline": len(data["decline"]),
        "new": len(data["new"]),
        "gone": len(data["gone"]),
    }

    # 前端 JS 内可见词（按 lang 注入页面）
    i18n_js = {k: L(lang, k) for k in (
        "rise", "fall", "flat", "untagged", "unknown", "no_data",
        "total_cost", "curr_cost", "curr_month", "prev_month", "this_month_paren",
    )}
    html_lang = "en" if lang == "en" else "zh-CN"
    analysis_window_html = L(lang, "analysis_window").format(
        a=f"<b>{escape(meta['analysis_month'])}</b>",
        c=f"<b>{escape(meta['comparison_month'])}</b>",
    )

    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
{chartjs_tag}
<style>
:root {{
  --bg-0: #0b1020; --bg-1: #111933; --line: rgba(120,144,200,0.16);
  --text-0: #e7ecff; --text-1: #aab4dc; --text-2: #6f7aa8;
  --accent: #5b8cff; --accent-2: #8b5cff;
  --good: #36d399; --warn: #f5b455; --bad: #ef4f6f;
  --shadow: 0 8px 32px rgba(0,0,0,0.32);
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background:
  radial-gradient(1200px 600px at 10% -10%, rgba(91,140,255,0.18), transparent 60%),
  radial-gradient(900px 500px at 110% 10%, rgba(139,92,255,0.16), transparent 60%),
  var(--bg-0); color: var(--text-0); font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
  "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif; font-size: 14px; line-height: 1.55; }}
.wrap {{ max-width: 1320px; margin: 0 auto; padding: 24px 28px 80px; }}
.hero {{ background: linear-gradient(135deg, rgba(91,140,255,0.12), rgba(139,92,255,0.10));
  border: 1px solid var(--line); border-radius: 16px; padding: 24px 28px; margin-bottom: 20px;
  backdrop-filter: blur(8px); box-shadow: var(--shadow); }}
.hero-top {{ display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 16px; }}
.hero h1 {{ font-size: 22px; margin: 0 0 6px; }}
.hero .sub {{ color: var(--text-1); font-size: 13px; }}
.tag {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
  background: rgba(91,140,255,0.18); color: #b8caff; font-size: 12px;
  border: 1px solid rgba(91,140,255,0.32); margin-left: 8px; }}
.tag.warn {{ background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 20px; }}
.kpi {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 16px; position: relative; overflow: hidden; }}
.kpi::after {{ content: ""; position: absolute; right: -30px; top: -30px;
  width: 100px; height: 100px; border-radius: 50%;
  background: radial-gradient(circle, rgba(91,140,255,0.20), transparent 70%); }}
.kpi .label {{ color: var(--text-2); font-size: 12px; }}
.kpi .value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
.kpi .delta {{ font-size: 12px; margin-top: 4px; color: var(--text-1); }}
.kpi .delta.up {{ color: var(--bad); }} .kpi .delta.down {{ color: var(--good); }}
.kpi .delta.flat {{ color: var(--text-2); }}
.card {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }}
.card h2 {{ font-size: 15px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }}
.card h2::before {{ content: ""; width: 3px; height: 14px; background: var(--accent); border-radius: 2px; }}
.card .meta {{ color: var(--text-2); font-size: 12px; font-weight: 400; margin-left: auto; }}
.row-2 {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }}
.row-2.eq {{ grid-template-columns: 1fr 1fr; }}
    .chart-box {{ position: relative; height: 280px; }}
    .chart-box.tall {{ height: 340px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }}
th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }}
th {{ color: var(--text-2); font-weight: 500; font-size: 12px; letter-spacing: 0.6px;
  background: rgba(255,255,255,0.02); position: sticky; top: 0; z-index: 1; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tbody tr:hover {{ background: rgba(91,140,255,0.06); }}
td.delta-up {{ color: var(--bad); font-weight: 600; }}
td.delta-down {{ color: var(--good); font-weight: 600; }}
td.delta-flat {{ color: var(--text-1); }}
td.code {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; color: var(--text-1);
  max-width: 360px; overflow: hidden; text-overflow: ellipsis; }}
/* Truncate long text cells + tooltip */
td.cell-ellipsis {{ max-width: 240px; overflow: hidden; text-overflow: ellipsis; }}
.pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; vertical-align: middle; }}
.pill.new {{ background: rgba(54,211,153,0.16); color: var(--good); border: 1px solid rgba(54,211,153,0.32); }}
.pill.gone {{ background: rgba(111,122,168,0.20); color: var(--text-1); border: 1px solid rgba(111,122,168,0.32); }}
.pill.up {{ background: rgba(239,79,111,0.16); color: var(--bad); border: 1px solid rgba(239,79,111,0.32); }}
.pill.down {{ background: rgba(54,211,153,0.16); color: var(--good); border: 1px solid rgba(54,211,153,0.32); }}
.scroll-y {{ max-height: 460px; overflow: auto; border-radius: 8px; border: 1px solid var(--line); }}
.scroll-y::-webkit-scrollbar {{ width: 8px; height: 8px; }}
.scroll-y::-webkit-scrollbar-thumb {{ background: rgba(120,144,200,0.28); border-radius: 4px; }}
.scroll-y::-webkit-scrollbar-track {{ background: transparent; }}
.empty {{ padding: 40px 20px; text-align: center; color: var(--text-2); font-size: 13px; }}
.muted {{ color: var(--text-2); font-style: italic; }}

/* ── Doughnut chart + custom scrollable legend ── */
.pie-wrap {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: center; height: 320px; }}
.pie-canvas {{ position: relative; height: 100%; }}
.pie-legend {{ max-height: 300px; overflow-y: auto; padding-right: 6px; font-size: 12px; }}
.pie-legend::-webkit-scrollbar {{ width: 6px; }}
.pie-legend::-webkit-scrollbar-thumb {{ background: rgba(120,144,200,0.30); border-radius: 3px; }}
.pie-legend::-webkit-scrollbar-track {{ background: transparent; }}
.legend-item {{ display: grid; grid-template-columns: 12px 1fr auto; gap: 8px; align-items: center;
  padding: 5px 8px; border-radius: 6px; cursor: pointer; transition: background .15s; }}
.legend-item:hover {{ background: rgba(91,140,255,0.10); }}
.legend-item.dim {{ opacity: 0.35; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }}
.legend-name {{ color: var(--text-0); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.legend-pct {{ color: var(--text-2); font-variant-numeric: tabular-nums; font-size: 11.5px; }}

/* ── AIOps-style insights ── */
.insights-wrap {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
.ins-card {{ position: relative; background: linear-gradient(135deg, rgba(91,140,255,0.06), rgba(139,92,255,0.04));
  border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; overflow: hidden;
  transition: transform .15s, box-shadow .15s; }}
.ins-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(91,140,255,0.15); }}
.ins-card::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--accent); border-radius: 2px 0 0 2px; }}
.ins-card.warn::before {{ background: var(--warn); }}
.ins-card.bad::before {{ background: var(--bad); }}
.ins-card.good::before {{ background: var(--good); }}
.ins-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
.ins-icon {{ width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center;
  justify-content: center; font-size: 14px; flex-shrink: 0;
  background: rgba(91,140,255,0.18); color: var(--accent); }}
.ins-card.warn .ins-icon {{ background: rgba(245,180,85,0.18); color: var(--warn); }}
.ins-card.bad .ins-icon {{ background: rgba(239,79,111,0.18); color: var(--bad); }}
.ins-card.good .ins-icon {{ background: rgba(54,211,153,0.18); color: var(--good); }}
.ins-title {{ font-weight: 600; font-size: 13.5px; color: var(--text-0); flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.ins-badge {{ font-size: 10.5px; padding: 2px 8px; border-radius: 999px; font-weight: 600;
  background: rgba(91,140,255,0.16); color: #b8caff; border: 1px solid rgba(91,140,255,0.28); }}
.ins-card.warn .ins-badge {{ background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }}
.ins-card.bad .ins-badge {{ background: rgba(239,79,111,0.18); color: #ffb3c1; border-color: rgba(239,79,111,0.36); }}
.ins-card.good .ins-badge {{ background: rgba(54,211,153,0.16); color: #9ee9c9; border-color: rgba(54,211,153,0.32); }}
.ins-metric {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0 10px;
  padding: 8px 10px; background: rgba(0,0,0,0.20); border-radius: 8px;
  border: 1px solid rgba(120,144,200,0.10); }}
.ins-metric .m {{ display: flex; flex-direction: column; gap: 1px; }}
.ins-metric .m-label {{ color: var(--text-2); font-size: 10.5px; letter-spacing: 0.3px; }}
.ins-metric .m-value {{ color: var(--text-0); font-size: 14px; font-weight: 600;
  font-variant-numeric: tabular-nums; }}
.ins-metric .m-value.up {{ color: var(--bad); }}
.ins-metric .m-value.down {{ color: var(--good); }}
.ins-desc {{ color: var(--text-1); font-size: 12.5px; line-height: 1.55; }}
.ins-action {{ margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(120,144,200,0.14);
  color: var(--text-2); font-size: 11.5px; display: flex; align-items: center; gap: 6px; }}
.ins-action::before {{ content: "💡"; font-size: 12px; }}
.ins-summary {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px;
  padding: 10px 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--line);
  border-radius: 10px; font-size: 12px; }}
.ins-summary .stat {{ display: flex; align-items: center; gap: 6px; color: var(--text-1); }}
.ins-summary .stat b {{ color: var(--text-0); font-weight: 700; font-size: 14px;
  font-variant-numeric: tabular-nums; }}
.ins-summary .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
.ins-summary .dot.bad {{ background: var(--bad); }}
.ins-summary .dot.warn {{ background: var(--warn); }}
.ins-summary .dot.info {{ background: var(--accent); }}
.ins-summary .dot.good {{ background: var(--good); }}
footer {{ color: var(--text-2); font-size: 11.5px; text-align: center; margin-top: 24px; }}
@media (max-width: 980px) {{
  .kpis {{ grid-template-columns: repeat(2, 1fr); }}
  .row-2, .row-2.eq {{ grid-template-columns: 1fr; }}
  .insights {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <div class="hero-top">
      <div>
        <h1>💰 {escape(meta['account_name'])} · {L(lang, 'billing_report')}</h1>
        <div class="sub">
          {analysis_window_html}
          <span class="tag">UIN: {escape(str(meta['uin']))}</span>
          {f'<span class="tag warn">{L(lang, "kpi_monthly_budget").format(m=fmt_money(budget))}</span>' if budget else ''}
        </div>
      </div>
      <div style="text-align: right; color: var(--text-1); font-size: 12px;">
        <div>{L(lang, 'data_source')}</div>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi">
        <div class="label">{L(lang, 'current_spend')}</div>
        <div class="value">{fmt_money(cur)}</div>
        <div class="delta {diff_cls}">{ratio_str}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, 'budget_usage') if budget_pct else L(lang, 'previous_spend')}</div>
        <div class="value">{f'{budget_pct:.1f}%' if budget_pct else fmt_money(prev)}</div>
        <div class="delta flat">{L(lang, 'kpi_monthly_budget').format(m=fmt_money(budget)) if budget_pct else L(lang, 'baseline')}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, 'resource_changes')}</div>
        <div class="value">+{counts['new']} / -{counts['gone']}</div>
        <div class="delta flat">{L(lang, 'new_offline')}</div>
      </div>
      <div class="kpi">
        <div class="label">{L(lang, 'top_product')}</div>
        <div class="value">{escape(top1['name']) if top1 else '-'}</div>
        <div class="delta flat">{L(lang, 'kpi_top1_delta').format(m=fmt_money(top1['cur']), p=f"{top1_share:.1f}") if top1 and top1_share is not None else '-'}</div>
      </div>
    </div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>{L(lang, 'trend_title').format(n=len(data['trend']['months']))} <span class="meta">{L(lang, 'unit_yuan')}</span></h2>
      <div class="chart-box"><canvas id="trendChart"></canvas></div>
    </div>
    <div class="card">
      <h2>{L(lang, 'product_share_title')} <span class="meta">{L(lang, 'products_count').format(n=counts['products'])}</span></h2>
      <div class="pie-wrap">
        <div class="pie-canvas"><canvas id="productPie"></canvas></div>
        <div class="pie-legend" id="pieLegend"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'product_breakdown').format(n=counts['products'])}<span class="meta">{L(lang, 'scrollable')}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{L(lang, 'product')}</th><th class="num">{L(lang, 'curr_cost')}</th><th class="num">{L(lang, 'prev_cost')}</th>
          <th class="num">{L(lang, 'diff')}</th><th class="num">{L(lang, 'mom')}</th><th class="num">{L(lang, 'share')}</th><th>{L(lang, 'change')}</th>
        </tr></thead>
        <tbody id="productTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'region_breakdown').format(n=counts['regions'])}<span class="meta">{L(lang, 'cost_desc')}</span></h2>
    <div class="row-2 eq">
      <div class="chart-box"><canvas id="regionChart"></canvas></div>
      <div class="scroll-y">
        <table>
          <thead><tr>
            <th>{L(lang, 'region')}</th><th class="num">{L(lang, 'curr_cost')}</th><th class="num">{L(lang, 'share')}</th>
          </tr></thead>
          <tbody id="regionTable"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'cost_growth_res').format(n=counts['growth'])}<span class="meta">{L(lang, 'sorted_mom_inc')}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{L(lang, 'product')}</th><th>{L(lang, 'resource_id')}</th><th>head</th><th>{L(lang, 'business')}</th>
          <th class="num">{L(lang, 'curr_month')}</th><th class="num">{L(lang, 'prev_month')}</th><th class="num">{L(lang, 'diff')}</th>
        </tr></thead>
        <tbody id="growthTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'cost_decline_res').format(n=counts['decline'])}<span class="meta">{L(lang, 'sorted_mom_dec')}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{L(lang, 'product')}</th><th>{L(lang, 'resource_id')}</th><th>head</th><th>{L(lang, 'business')}</th>
          <th class="num">{L(lang, 'curr_month')}</th><th class="num">{L(lang, 'prev_month')}</th><th class="num">{L(lang, 'diff')}</th>
        </tr></thead>
        <tbody id="declineTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'new_res_title').format(n=counts['new'])}<span class="meta">{L(lang, 'new_this_month')}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{L(lang, 'product')}</th><th>{L(lang, 'resource_id')}</th><th>head</th><th>{L(lang, 'business')}</th><th class="num">{L(lang, 'curr_cost')}</th>
        </tr></thead>
        <tbody id="newTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{L(lang, 'offline_res_title').format(n=counts['gone'])}<span class="meta">{L(lang, 'no_cost_this_month')}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{L(lang, 'product')}</th><th>{L(lang, 'resource_id')}</th><th>head</th><th>{L(lang, 'business')}</th><th class="num">{L(lang, 'prev_cost')}</th>
        </tr></thead>
        <tbody id="goneTable"></tbody>
      </table>
    </div>
  </div>

  {(
    ''.join(
      '<div class="card"><h2>' + L(lang, 'by_tag_title').format(tk='<code>' + escape(tk) + '</code>', n=len(items)) +
      '<span class="meta">' + (L(lang, 'biz_line_class') if tk in ('head','business','customer','BizName','biz_name') else L(lang, 'tag_allocation')) + '</span></h2>'
      '<div class="chart-box tall"><canvas id="tagChart_' + escape(tk) + '"></canvas></div>'
      '<div class="scroll-y" style="margin-top:12px;"><table><thead><tr>'
      '<th>' + escape(tk) + '</th>'
      '<th class="num">' + L(lang, 'curr_month') + '</th><th class="num">' + L(lang, 'prev_month') + '</th>'
      '<th class="num">' + L(lang, 'diff') + '</th><th class="num">' + L(lang, 'mom') + '</th><th class="num">' + L(lang, 'share') + '</th>'
      '</tr></thead><tbody id="tagTable_' + escape(tk) + '"></tbody></table></div></div>'
      for tk, items in data['tag_groups'].items() if items
    )
  ) if data['tag_groups'] else ''}

  {(
    '<div class="card"><h2>🤖 ' + L(lang, 'smart_insights') + ' <span class="meta">' + L(lang, 'insights_meta').format(n=len(data['insights'])) + '</span></h2>'
    + _build_insights_summary(data['insights'], lang) +
    '<div class="insights-wrap" id="insightsWrap"></div></div>'
  ) if data['insights'] else ''}

  <footer>{L(lang, 'footer')}</footer>
</div>

<script>
const I18N_JS = {safe_json_dumps(i18n_js)};
const DATA = {safe_json_dumps(payload)};
const COLORS = ['#5b8cff','#8b5cff','#36d399','#f5b455','#ef4f6f','#67c8f5',
                '#a8d36b','#ffb6c1','#9b8cff','#ffd166','#06b6d4','#f87171',
                '#34d399','#fb923c','#a78bfa'];

function money(v) {{ return '¥' + Math.round(v).toLocaleString(); }}

// ── Product table
const totalCur = DATA.products.reduce((s,p) => s + (p.cur||0), 0);
const ptbody = document.getElementById('productTable');
DATA.products.forEach(p => {{
  const diff = p.cur - p.prev;
  const ratio = p.prev > 0 ? (diff / p.prev * 100) : (p.cur > 0 ? 100 : 0);
  const cls = diff > 0 ? 'delta-up' : diff < 0 ? 'delta-down' : 'delta-flat';
  const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '—';
  const pillCls = diff > 0 ? 'up' : diff < 0 ? 'down' : 'gone';
  const pillTxt = diff > 0 ? I18N_JS.rise : diff < 0 ? I18N_JS.fall : I18N_JS.flat;
  ptbody.insertAdjacentHTML('beforeend',
    '<tr><td>' + p.name + '</td>' +
    '<td class="num">' + money(p.cur) + '</td>' +
    '<td class="num">' + money(p.prev) + '</td>' +
    '<td class="num ' + cls + '">' + arrow + ' ' + money(Math.abs(diff)) + '</td>' +
    '<td class="num ' + cls + '">' + (diff >= 0 ? '+' : '') + ratio.toFixed(1) + '%</td>' +
    '<td class="num">' + (totalCur > 0 ? (p.cur / totalCur * 100).toFixed(1) : '0.0') + '%</td>' +
    '<td><span class="pill ' + pillCls + '">' + pillTxt + '</span></td></tr>');
}});

// ── Region table + chart
const regionTotal = DATA.regions.reduce((s,r) => s + (r.cur||0), 0);
const rtbody = document.getElementById('regionTable');
if (rtbody && DATA.regions.length) {{
  DATA.regions.forEach(r => {{
    const pct = regionTotal > 0 ? (r.cur / regionTotal * 100) : 0;
    rtbody.insertAdjacentHTML('beforeend',
      '<tr><td>' + r.name + '</td>' +
      '<td class="num">' + money(r.cur) + '</td>' +
      '<td class="num">' + pct.toFixed(1) + '%</td></tr>');
  }});
}}

// ── Growth / Decline / New / Gone
function renderResRow(r, mode) {{
  const headRaw = r.head || '';
  const bizRaw  = r.biz  || '';
  const prodRaw = (r.prod && r.prod !== '-') ? r.prod : '';
  const head = headRaw ? escapeHtml(headRaw) : '<span class="muted">' + I18N_JS.untagged + '</span>';
  const biz  = bizRaw  ? escapeHtml(bizRaw)  : '<span class="muted">' + I18N_JS.untagged + '</span>';
  const prod = prodRaw ? escapeHtml(prodRaw) : '<span class="muted">' + I18N_JS.unknown + '</span>';
  const prodTd = '<td class="cell-ellipsis" title="' + escapeHtml(prodRaw || I18N_JS.unknown) + '">' + prod + '</td>';
  const headTd = '<td class="cell-ellipsis" title="' + escapeHtml(headRaw || I18N_JS.untagged) + '">' + head + '</td>';
  const bizTd  = '<td class="cell-ellipsis" title="' + escapeHtml(bizRaw  || I18N_JS.untagged) + '">' + biz  + '</td>';
  if (mode === 'gone') {{
    return '<tr>' + prodTd +
      '<td class="code" title="' + escapeHtml(r.id) + '">' + escapeHtml(r.id) + ' <span class="pill gone">GONE</span></td>' +
      headTd + bizTd +
      '<td class="num">' + money(r.prev) + '</td></tr>';
  }}
  if (mode === 'new') {{
    return '<tr>' + prodTd +
      '<td class="code" title="' + escapeHtml(r.id) + '">' + escapeHtml(r.id) + ' <span class="pill new">NEW</span></td>' +
      headTd + bizTd +
      '<td class="num">' + money(r.cur) + '</td></tr>';
  }}
  const diff = r.diff;
  const cls = mode === 'up' ? 'delta-up' : 'delta-down';
  const arrow = mode === 'up' ? '▲' : '▼';
  return '<tr>' + prodTd +
    '<td class="code" title="' + escapeHtml(r.id) + '">' + escapeHtml(r.id) + '</td>' +
    headTd + bizTd +
    '<td class="num">' + money(r.cur) + '</td>' +
    '<td class="num">' + money(r.prev) + '</td>' +
    '<td class="num ' + cls + '">' + arrow + ' ' + money(Math.abs(diff)) + '</td></tr>';
}}

function fillTable(id, items, mode) {{
  const tb = document.getElementById(id);
  if (!tb) return;
  if (!items.length) {{
    tb.parentElement.parentElement.insertAdjacentHTML(
      'afterend', '<div class="empty">' + I18N_JS.no_data + '</div>');
    tb.parentElement.style.display = 'none';
    return;
  }}
  tb.innerHTML = items.map(r => renderResRow(r, mode)).join('');
}}
fillTable('growthTable', DATA.growth, 'up');
fillTable('declineTable', DATA.decline, 'down');
fillTable('newTable', DATA.new, 'new');
fillTable('goneTable', DATA.gone, 'gone');

// ── Tag tables (all dimensions)
Object.keys(DATA.tag_groups || {{}}).forEach(tk => {{
  const items = DATA.tag_groups[tk] || [];
  const tagTotal = items.reduce((s,t) => s + (t.cur||0), 0);
  const tbody = document.getElementById('tagTable_' + tk);
  if (!tbody) return;
  items.forEach(t => {{
    const diff = t.cur - t.prev;
    const ratio = t.prev > 0 ? (diff / t.prev * 100) : (t.cur > 0 ? 100 : 0);
    const cls = diff > 0 ? 'delta-up' : diff < 0 ? 'delta-down' : 'delta-flat';
    const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '—';
    tbody.insertAdjacentHTML('beforeend',
      '<tr><td>' + (t.name && t.name.trim() ? t.name : '<span class="muted">' + I18N_JS.untagged + '</span>') + '</td>' +
      '<td class="num">' + money(t.cur) + '</td>' +
      '<td class="num">' + money(t.prev) + '</td>' +
      '<td class="num ' + cls + '">' + arrow + ' ' + money(Math.abs(diff)) + '</td>' +
      '<td class="num ' + cls + '">' + (diff >= 0 ? '+' : '') + ratio.toFixed(1) + '%</td>' +
      '<td class="num">' + (tagTotal > 0 ? (t.cur / tagTotal * 100).toFixed(1) : '0.0') + '%</td></tr>');
  }});
}});

// ── Charts
if (typeof Chart !== 'undefined') {{
  Chart.defaults.color = '#aab4dc';
  Chart.defaults.borderColor = 'rgba(120,144,200,0.16)';
  Chart.defaults.font.family = '-apple-system, "PingFang SC", sans-serif';

  if (DATA.trend.months.length) {{
    // Format month 202605 → 2026-05; highlight current (last) month
    const monthLabels = DATA.trend.months.map(m =>
      (m && m.length === 6) ? (m.slice(0,4) + '-' + m.slice(4,6)) : m
    );
    const lastIdx = DATA.trend.months.length - 1;
    const ctxTrend = document.getElementById('trendChart').getContext('2d');
    const grad = ctxTrend.createLinearGradient(0, 0, 0, 280);
    grad.addColorStop(0, 'rgba(91,140,255,0.45)');
    grad.addColorStop(1, 'rgba(91,140,255,0.02)');
    const pointBg = DATA.trend.months.map((_, i) =>
      i === lastIdx ? '#8b5cff' : '#5b8cff'
    );
    const pointRadius = DATA.trend.months.map((_, i) =>
      i === lastIdx ? 6 : 4
    );
    new Chart(ctxTrend, {{
      type: 'line',
      data: {{
        labels: monthLabels,
        datasets: [{{
          label: I18N_JS.total_cost, data: DATA.trend.total,
          borderColor: '#5b8cff',
          backgroundColor: grad,
          fill: true,
          tension: 0.35,
          borderWidth: 2.5,
          pointBackgroundColor: pointBg,
          pointBorderColor: '#0b1020',
          pointBorderWidth: 2,
          pointRadius: pointRadius,
          pointHoverRadius: 7,
        }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{
            label: c => money(c.parsed.y) + (c.dataIndex === lastIdx ? ' ' + I18N_JS.this_month_paren : '')
          }} }}
        }},
        scales: {{
          y: {{ ticks: {{ maxTicksLimit: 6, callback: v => '¥' + (v/1000).toFixed(1) + 'k' }}, grid: {{ color: 'rgba(120,144,200,0.10)' }} }},
          x: {{ grid: {{ color: 'rgba(120,144,200,0.06)' }} }}
        }}
      }}
    }});
  }}

  if (DATA.products.length) {{
    // Cycle colors across products
    const pieColors = DATA.products.map((_, i) => COLORS[i % COLORS.length]);
    const pieChart = new Chart(document.getElementById('productPie'), {{
      type: 'doughnut',
      data: {{
        labels: DATA.products.map(p => p.name),
        datasets: [{{ data: DATA.products.map(p => p.cur), backgroundColor: pieColors, borderWidth: 0 }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false, cutout: '64%',
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: c => c.label + ': ' + money(c.parsed) +
            ' (' + (totalCur > 0 ? (c.parsed/totalCur*100).toFixed(1) : '0.0') + '%)' }} }}
        }}
      }}
    }});
    // Custom scrollable legend
    const legendEl = document.getElementById('pieLegend');
    DATA.products.forEach((p, idx) => {{
      const pct = totalCur > 0 ? (p.cur / totalCur * 100) : 0;
      const item = document.createElement('div');
      item.className = 'legend-item';
      item.dataset.idx = idx;
      item.innerHTML =
        '<span class="legend-dot" style="background:' + pieColors[idx] + '"></span>' +
        '<span class="legend-name" title="' + p.name + '">' + p.name + '</span>' +
        '<span class="legend-pct">' + pct.toFixed(1) + '%</span>';
      item.addEventListener('click', () => {{
        const meta = pieChart.getDatasetMeta(0);
        meta.data[idx].hidden = !meta.data[idx].hidden;
        item.classList.toggle('dim', meta.data[idx].hidden);
        pieChart.update();
      }});
      legendEl.appendChild(item);
    }});
  }}

  // ── Region bar chart
  if (DATA.regions.length) {{
    const regionCanvas = document.getElementById('regionChart');
    if (regionCanvas) {{
      new Chart(regionCanvas, {{
        type: 'bar',
        data: {{
          labels: DATA.regions.map(r => r.name),
          datasets: [{{
            label: I18N_JS.curr_cost,
            data: DATA.regions.map(r => r.cur),
            backgroundColor: DATA.regions.map((_, i) => COLORS[i % COLORS.length]),
            borderRadius: 6,
            maxBarThickness: 48,
          }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{ callbacks: {{
              label: c => money(c.parsed.x) + ' (' +
                (regionTotal > 0 ? (c.parsed.x / regionTotal * 100).toFixed(1) : '0.0') + '%)'
            }} }}
          }},
          scales: {{
            x: {{ ticks: {{ maxTicksLimit: 6, callback: v => '¥' + (v/1000).toFixed(1) + 'k' }}, grid: {{ color: 'rgba(120,144,200,0.10)' }} }},
            y: {{ grid: {{ display: false }} }}
          }}
        }}
      }});
    }}
  }}

  Object.keys(DATA.tag_groups || {{}}).forEach(tk => {{
    const items = DATA.tag_groups[tk] || [];
    if (!items.length) return;
    const canvas = document.getElementById('tagChart_' + tk);
    if (!canvas) return;
    new Chart(canvas, {{
      type: 'bar',
      data: {{
        labels: items.map(t => (t.name && t.name.trim()) ? t.name : I18N_JS.untagged),
        datasets: [
          {{ label: I18N_JS.curr_month, data: items.map(t => t.cur), backgroundColor: '#5b8cff', borderRadius: 6 }},
          {{ label: I18N_JS.prev_month, data: items.map(t => t.prev), backgroundColor: 'rgba(139,92,255,0.6)', borderRadius: 6 }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ position: 'top', align: 'end', labels: {{ boxWidth: 10 }} }} }},
        scales: {{
          y: {{ ticks: {{ maxTicksLimit: 6, callback: v => '¥' + (v/1000).toFixed(1) + 'k' }}, grid: {{ color: 'rgba(120,144,200,0.10)' }} }},
          x: {{ grid: {{ display: false }} }}
        }}
      }}
    }});
  }});
}}

// ── Render AIOps-style insight cards
function escapeHtml(s) {{
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}}
const insWrap = document.getElementById('insightsWrap');
if (insWrap && DATA.insights && DATA.insights.length) {{
  // Sort by severity: bad > warn > info > good
  const order = {{ bad: 0, warn: 1, info: 2, good: 3 }};
  const sorted = DATA.insights.slice().sort((a, b) => (order[a.level] || 9) - (order[b.level] || 9));
  insWrap.innerHTML = sorted.map(ins => {{
    const lvl = ['bad','warn','info','good'].includes(ins.level) ? ins.level : 'info';
    const icon = ins.icon || '🔍';
    const badge = ins.badge ? '<span class="ins-badge">' + escapeHtml(ins.badge) + '</span>' : '';
    let metricsHtml = '';
    if (Array.isArray(ins.metrics) && ins.metrics.length) {{
      metricsHtml = '<div class="ins-metric">' +
        ins.metrics.map(m => {{
          const mode = ['up','down','flat'].includes(m.mode) ? m.mode : '';
          return '<div class="m">' +
            '<span class="m-label">' + escapeHtml(m.label) + '</span>' +
            '<span class="m-value ' + mode + '">' + escapeHtml(m.value) + '</span>' +
          '</div>';
        }}).join('') + '</div>';
    }}
    const action = ins.action ? '<div class="ins-action">' + escapeHtml(ins.action) + '</div>' : '';
    return '<div class="ins-card ' + lvl + '">' +
      '<div class="ins-head">' +
        '<div class="ins-icon">' + icon + '</div>' +
        '<div class="ins-title">' + escapeHtml(ins.title) + '</div>' +
        badge +
      '</div>' +
      metricsHtml +
      '<div class="ins-desc">' + escapeHtml(ins.desc) + '</div>' +
      action +
    '</div>';
  }}).join('');
}}
</script>
</body>
</html>
"""


# ───────────── CLI ─────────────

def main():
    ap = argparse.ArgumentParser(description="Render billing JSON to single-file HTML dashboard")
    ap.add_argument("--data", required=True, help="extract_step3_data.py 输出的 JSON 文件路径（或 - 表示 stdin）")
    ap.add_argument("--output", "-o", default="-", help="输出 HTML 路径，默认 stdout")
    ap.add_argument("--offline", default="", help="内嵌 Chart.js（提供本地 chart.umd.min.js 路径，否则用 CDN）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="UI language for the dashboard")
    args = ap.parse_args()

    if args.data == "-":
        raw = json.loads(sys.stdin.read())
    else:
        with open(args.data, "r", encoding="utf-8") as f:
            raw = json.load(f)

    # 在任何 tr() 调用之前：注入 BusinessCode → 英文名映射 + LLM 兜底钩子 + 清空审计记录
    # 这样 API 返回的新产品也能被自动翻译（不需要手动加静态字典条目）
    inject_bizcode_map(raw.get("product_code_to_name"))
    if args.lang == "en":
        set_llm_hook(create_llm_hook())  # Layer 5 兜底：字典查不到的中文走 LLM 实时翻译
    reset_misses()  # 清空上一轮的未翻译记录

    data = build_data(raw, args.lang)
    html = render_html(data, offline_chartjs_path=args.offline or None, lang=args.lang)

    if args.output == "-":
        sys.stdout.write(html)
    else:
        # Resolve output path: if relative, prefix with WORKBUDDY_WORKSPACE (ensures artifacts panel visibility)
        out_path = args.output
        if not os.path.isabs(out_path):
            ws = os.environ.get("WORKBUDDY_WORKSPACE")
            if ws:
                out_path = os.path.join(ws, out_path)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 已生成 HTML 看板: {out_path}", file=sys.stderr)

    # ── 翻译审计：输出未翻译条目报告 ──
    report = get_translation_report()
    if report["miss_count"] > 0:
        # 报告文件与 HTML 同目录，后缀 .i18n-audit.json
        audit_path = re.sub(r'\.html?$', '.i18n-audit.json', out_path) if args.output != "-" else None
        if audit_path:
            with open(audit_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"⚠️ i18n 审计: {report['miss_count']} 条未翻译 → {audit_path}", file=sys.stderr)
            for m in report["misses"][:5]:
                print(f"   ❌ {m['input'][:50]} → {m['output'][:50]} (残留: {', '.join(m['chinese_segments'][:3])})", file=sys.stderr)
            if report["miss_count"] > 5:
                print(f"   ... 共 {report['miss_count']} 条，详见 {audit_path}", file=sys.stderr)
        else:
            # stdout 模式：打印摘要到 stderr
            print(f"⚠️ i18n audit: {report['miss_count']} untranslated entries", file=sys.stderr)
            for m in report["misses"][:3]:
                print(f"   ❌ {m['input'][:50]} → {m['output'][:50]}", file=sys.stderr)
    else:
        print(f"✅ i18n 审计: 全部翻译完成，零残留中文", file=sys.stderr)

    # ── 持久化 LLM 翻译缓存 ──
    if args.lang == "en":
        _flush_llm_cache()


if __name__ == "__main__":
    main()
