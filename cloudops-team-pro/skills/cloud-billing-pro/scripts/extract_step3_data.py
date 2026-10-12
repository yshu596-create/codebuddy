#!/usr/bin/env python3
"""
extract_step3_data.py — 一次性提取 Step 3 报告渲染所需的全部数据

设计目的：
  agent 过去用 44 次 execute_code 逐字段读 prep_output.json / summarize_*.json，
  每次 3.2s sandbox 开销，总共 ~140s 纯浪费 + 60 轮耗尽。
  本脚本把"读数据 → 结构化提取 → 格式化"合并成一条 exec，
  输出一个紧凑 JSON，agent 拿到后直接拼 markdown，零探索。

用法：
  python3 extract_step3_data.py <workspace_dir>

输出 JSON 结构：
  {
    "account_name": "...",
    "analysis_month": "2026-05",
    "comparison_month": "2026-04",
    "uin": "...",
    "budget": null,
    "totals": { "curr_total": ..., "prev_total": ..., "diff": ..., "ratio_pct": ..., "icon": "📈" },
    "top_products": [ ... ],       // 按产品拆解
    "top_regions": [ ... ],        // 按地域拆解
    "product_trend": { ... },      // 近 3 月趋势
    "trend_months_ordered": [ ... ],
    "summary_highlights": { ... }, // 总结节素材
    "no_discount_products": [ ... ],
    "insights": [ ... ],
    "top10_resources": [ ... ],    // Top 10 成本资源（含环比）
    "resource_diff": {             // 环比三表（增长/降低/新增/下线）
      "growth": { "items": [...], "count": N },
      "decline": { "items": [...], "count": N },
      "new": { "items": [...], "count": N },
      "offline": { "items": [...], "count": N }
    },
    "summary_tag": { ... },        // 标签分账数据
    "effective_tag_keys": [ ... ], // 有效标签键列表
    "product_code_to_name": { ... }, // ProductCode → ProductName 映射
  }
"""

import json
import sys
import os
from pathlib import Path


def load_json(path):
    """Load JSON file, return empty dict on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fmt_money(v):
    """Format money value with ¥ and comma separator."""
    if v is None:
        return "—"
    if abs(v) < 0.01:
        return "¥0"
    return f"¥{v:,.0f}" if abs(v) >= 1 else f"¥{v:.2f}"


def fmt_ratio(v):
    """Format ratio as percentage string."""
    if v is None:
        return "—"
    return f"{v:+.1f}%"


def fmt_diff(diff, ratio, pay_mode=None):
    """Format diff + ratio for display, considering pay mode."""
    if pay_mode == "PREPAID":
        return "—", "—"
    if diff is None or ratio is None:
        return "—", "—"
    if abs(diff) < 1 and abs(ratio) < 0.1:
        return "— 持平", "— 持平"
    if abs(diff) < 10 or abs(ratio) < 1:
        # 微小波动
        return f"+¥{diff:,.0f}" if diff >= 0 else f"¥{diff:,.0f}", fmt_ratio(ratio)
    # 正常环比
    icon = "📈" if diff > 0 else "📉"
    diff_str = f"{icon} +¥{diff:,.0f}" if diff >= 0 else f"{icon} ¥{diff:,.0f}"
    return diff_str, fmt_ratio(ratio)


def build_product_code_map(prep_data):
    """Build ProductCode → ProductName mapping from summary_product data."""
    mapping = {}
    for month_key in ("analysis_summary", "comparison_summary"):
        summary = prep_data.get(month_key, {})
        sp = summary.get("summary_product", {})
        products = sp.get("products", [])
        for p in products:
            code = p.get("ProductCode", "")
            name = p.get("ProductName", "")
            if code and name:
                mapping[code] = name
    return mapping


def extract_totals(prep_data):
    """Extract totals from report_ready or compute from summary."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available") and rr.get("totals"):
        return rr["totals"]
    # Fallback: compute from summary
    # This shouldn't happen if report_ready is available, but just in case
    return {}


def extract_top_products(prep_data, n=None):
    """Extract products for HTML dashboard.

    HTML 看板默认全量展示（前端用滚动表呈现）。
    n=None 表示返回全部产品；指定 n 时按降序截取前 n 条（兼容历史调用）。
    """
    rr = prep_data.get("report_ready", {})
    if rr.get("available") and rr.get("top_products"):
        items = rr["top_products"]
        if n is None:
            return items
        return items[:n]
    return []


def extract_top_regions(prep_data):
    """Extract top regions for HTML dashboard."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available") and rr.get("top_regions"):
        return rr["top_regions"]
    return []


def extract_trend(prep_data):
    """Extract product trend data."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available"):
        return {
            "product_trend": rr.get("product_trend", {}),
            "trend_months_ordered": rr.get("trend_months_ordered", []),
        }
    return {"product_trend": {}, "trend_months_ordered": []}


def extract_summary_highlights(prep_data):
    """Extract summary highlights for conclusion section."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available"):
        return rr.get("summary_highlights", {})
    return {}


def extract_no_discount(prep_data):
    """Extract no-discount products."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available"):
        return rr.get("no_discount_products", [])
    return []


def extract_insights(prep_data):
    """Extract insights for conclusion section."""
    rr = prep_data.get("report_ready", {})
    if rr.get("available"):
        return rr.get("insights", [])
    return []


def enrich_resource_with_tags(resource, tag_map, effective_tag_keys, billing_index=None):
    """Add tag values + ProductName 来自原始账单 ResourceSummarySet 的 BusinessCodeName。

    billing_index: { ResourceId -> {BusinessCodeName, ProductCodeName, ProductCode, BusinessCode} }
    直接从 cache/billing/<uin>/<yyyymm>/resource_summary.json 加载，是腾讯账单 API 原文，最权威。
    """
    rid = resource.get("ResourceId", "")
    tags = tag_map.get(rid, {})
    resource["_tags"] = {}
    for tk in effective_tag_keys:
        resource["_tags"][tk] = tags.get(tk, "未打标")
    # 直接从账单原始数据回填，不再依赖任何映射/启发式
    if billing_index and rid in billing_index:
        ref = billing_index[rid]
        if not resource.get("ProductName"):
            # 优先 BusinessCodeName（一级品类，如「云服务器CVM」），不用 ProductCodeName（带规格如「CVM-标准型SA9」）
            resource["ProductName"] = ref.get("BusinessCodeName") or ref.get("ProductCodeName") or ""
        if not resource.get("ProductCode"):
            resource["ProductCode"] = ref.get("BusinessCode") or ref.get("ProductCode") or ""
    return resource


def build_billing_resource_index(workspace, prep_data):
    """从 cache/billing/<uin>/<yyyymm>/resource_summary.json 直接读取账单原始数据。

    返回：{ ResourceId -> {BusinessCodeName, ProductCodeName, BusinessCode, ProductCode} }
    覆盖所有 cache_inventory 中可用月份，越近的月份优先。
    """
    idx = {}
    uin = (prep_data.get("credentials") or {}).get("uin")
    if not uin:
        return idx
    cache_root = workspace / "cache" / "billing" / str(uin)
    if not cache_root.is_dir():
        return idx
    # 按月份倒序（202605, 202604, ...），保证最新月份的产品分类优先
    months = sorted(
        [p.name for p in cache_root.iterdir() if p.is_dir() and p.name.isdigit()],
        reverse=True,
    )
    for ym in months:
        rs_path = cache_root / ym / "resource_summary.json"
        if not rs_path.exists():
            continue
        try:
            with open(rs_path) as f:
                data = json.load(f)
        except Exception:
            continue
        items = data.get("ResourceSummarySet") or []
        for it in items:
            rid = it.get("ResourceId", "")
            if not rid or rid in idx:
                continue
            idx[rid] = {
                "BusinessCodeName": it.get("BusinessCodeName", ""),
                "ProductCodeName": it.get("ProductCodeName", ""),
                "BusinessCode": it.get("BusinessCode", ""),
                "ProductCode": it.get("ProductCode", ""),
            }
    return idx


def build_tag_map(prep_data):
    """Build resource_id → {tag_key: tag_value} mapping from resource tags."""
    tag_map = {}
    # Tags are in analysis_resources or resource_diff items
    for key in ("analysis_resources", "comparison_resources"):
        resources = prep_data.get(key, {}).get("resources", [])
        for r in resources:
            rid = r.get("ResourceId", "")
            if not rid:
                continue
            raw_tags = r.get("Tags", [])
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    if isinstance(t, dict):
                        tag_map.setdefault(rid, {})[t.get("TagKey", "")] = t.get("TagValue", "")
            elif isinstance(raw_tags, dict):
                tag_map[rid] = raw_tags

    # Also check resource_diff items for tags
    for cat in ("growth", "decline", "new", "offline"):
        items = prep_data.get("resource_diff", {}).get(cat, {}).get("items", [])
        for item in items:
            rid = item.get("ResourceId", "")
            if not rid:
                continue
            raw_tags = item.get("Tags", {})
            if isinstance(raw_tags, dict):
                tag_map.setdefault(rid, {}).update(raw_tags)
            elif isinstance(raw_tags, list):
                for t in raw_tags:
                    if isinstance(t, dict):
                        tag_map.setdefault(rid, {})[t.get("TagKey", "")] = t.get("TagValue", "")

    return tag_map


def extract_effective_tag_keys(prep_data):
    """Discover effective tag keys from summary_tag data.
    
    summary_tag can be in two locations:
    1. Top-level prep_data["summary_tag"] = { "head": [...], "business": [...] }
    2. Inside analysis_summary["summary_tag"] = { "TagKey": { "yyyymm": {...} } }
    """
    # Try top-level first (prepare_billing_data.py output format)
    top_level_st = prep_data.get("summary_tag", {})
    if top_level_st and isinstance(top_level_st, dict):
        # Format: { "head": [...], "business": [...] } → keys are the tag keys
        keys = [k for k in top_level_st.keys() if not k.startswith("_") and isinstance(top_level_st[k], list)]
        if keys:
            return keys

    # Fallback: nested inside analysis_summary
    summary_tag = prep_data.get("analysis_summary", {}).get("summary_tag", {})
    if not summary_tag:
        summary_tag = prep_data.get("comparison_summary", {}).get("summary_tag", {})

    # summary_tag structure: { "TagKey": { "yyyymm": { "Response": { "SummaryOverview": [...] } } } }
    effective = []
    if isinstance(summary_tag, dict):
        for tag_key, months_data in summary_tag.items():
            if tag_key.startswith("_"):
                continue
            if isinstance(months_data, dict):
                for ym, resp in months_data.items():
                    overview = None
                    if isinstance(resp, dict):
                        overview = resp.get("Response", resp).get("SummaryOverview", [])
                    if overview:
                        effective.append(tag_key)
                        break
    return effective


def extract_top10_resources(prep_data, product_code_map):
    """Extract Top 10 cost resources with enriched data."""
    resources = prep_data.get("analysis_resources", {}).get("resources", [])
    top10 = resources[:10]

    result = []
    for r in top10:
        item = {
            "ResourceId": r.get("ResourceId", ""),
            "ProductName": r.get("ProductName", "") or product_code_map.get(r.get("ProductCode", ""), ""),
            "ProductCode": r.get("ProductCode", ""),
            "RealTotalCost": r.get("RealTotalCost", 0),
        }
        result.append(item)
    return result


def extract_resource_diff(prep_data, tag_map, effective_tag_keys, resource_index=None):
    """Extract resource diff (growth/decline/new/offline) with tags + product backfill."""
    diff = prep_data.get("resource_diff", {})
    result = {}

    for cat in ("growth", "decline", "new", "offline"):
        cat_data = diff.get(cat, {})
        items = cat_data.get("items", [])
        count = cat_data.get("count", len(items))

        enriched_items = []
        for item in items:
            enriched = dict(item)
            enrich_resource_with_tags(enriched, tag_map, effective_tag_keys, resource_index)
            enriched_items.append(enriched)

        result[cat] = {
            "items": enriched_items,
            "count": count,
        }

    return result


def extract_summary_tag_for_report(prep_data):
    """Extract summary tag data structured for report rendering.
    
    Two possible structures:
    1. Top-level: prep_data["summary_tag"] = { "head": [{TagValue, 202605, 202604}, ...], ... }
    2. Nested: analysis_summary["summary_tag"] = { "TagKey": { "yyyymm": {Response:{SummaryOverview}} } }
    """
    result = {}

    # Try top-level first (prepare_billing_data.py format)
    top_level_st = prep_data.get("summary_tag", {})
    if top_level_st and isinstance(top_level_st, dict):
        # Check if any value is a list (top-level format)
        list_keys = [k for k in top_level_st.keys() if not k.startswith("_") and isinstance(top_level_st[k], list)]
        if list_keys:
            # Top-level format: { "head": [...], "business": [...] }
            for tag_key in list_keys:
                result[tag_key] = top_level_st[tag_key]
            return result

    # Fallback: nested inside analysis_summary / comparison_summary
    for summary_key in ("analysis_summary", "comparison_summary"):
        summary = prep_data.get(summary_key, {})
        st = summary.get("summary_tag", {})
        if st:
            for tag_key, months_data in st.items():
                if tag_key.startswith("_"):
                    continue
                if tag_key not in result:
                    result[tag_key] = {}
                if isinstance(months_data, dict):
                    for ym, resp in months_data.items():
                        if isinstance(resp, dict):
                            overview = resp.get("Response", resp).get("SummaryOverview", [])
                            if overview:
                                result[tag_key][ym] = overview
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_step3_data.py <workspace_dir> [prep_output_path]", file=sys.stderr)
        sys.exit(1)

    workspace = Path(sys.argv[1])
    if not workspace.is_dir():
        print(f"Error: {workspace} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Load prep_output.json (primary data source).
    # 并发 fan-out 隔离（2026-06-13）：默认仍读 <workspace>/prep_output.json（单账号兼容），
    # 但允许显式传入 per-UIN 路径作为第二位置参数，避免多账号并行时共享同一文件被覆盖。
    if len(sys.argv) >= 3 and sys.argv[2].strip():
        prep_path = Path(sys.argv[2])
    else:
        prep_path = workspace / "prep_output.json"
    prep_data = load_json(prep_path)

    if not prep_data:
        # Try loading from summarize files
        for fname in ("summarize_202605.json", "summarize_202604.json"):
            p = workspace / fname
            if p.exists():
                data = load_json(p)
                if data:
                    key = "analysis_summary" if "202605" in fname else "comparison_summary"
                    prep_data[key] = data

    if not prep_data:
        print(json.dumps({"error": f"No data found in {workspace}"}, ensure_ascii=False))
        sys.exit(1)

    # ── Resolve account_name: prefer top-level, fallback to credentials.display_name ──
    if not prep_data.get("account_name"):
        creds = prep_data.get("credentials", {})
        prep_data["account_name"] = creds.get("display_name", "")

    # Build product code → name mapping
    product_code_map = build_product_code_map(prep_data)

    # Build resource tag map
    tag_map = build_tag_map(prep_data)

    # Discover effective tag keys
    effective_tag_keys = extract_effective_tag_keys(prep_data)

    # 直接从账单原始 cache（resource_summary.json）建索引，覆盖率 100%
    resource_index = build_billing_resource_index(workspace, prep_data)

    # Extract all data
    result = {
        "account_name": prep_data.get("account_name", ""),
        "analysis_month": prep_data.get("analysis_month", ""),
        "comparison_month": prep_data.get("comparison_month", ""),
        "uin": prep_data.get("credentials", {}).get("uin", ""),
        "budget": prep_data.get("credentials", {}).get("budget"),
        "totals": extract_totals(prep_data),
        "top_products": extract_top_products(prep_data),
        "top_regions": extract_top_regions(prep_data),
        "product_trend": extract_trend(prep_data).get("product_trend", {}),
        "trend_months_ordered": extract_trend(prep_data).get("trend_months_ordered", []),
        "summary_highlights": extract_summary_highlights(prep_data),
        "no_discount_products": extract_no_discount(prep_data),
        "insights": extract_insights(prep_data),
        "top10_resources": extract_top10_resources(prep_data, product_code_map),
        "resource_diff": extract_resource_diff(prep_data, tag_map, effective_tag_keys, resource_index),
        "summary_tag": extract_summary_tag_for_report(prep_data),
        "effective_tag_keys": effective_tag_keys,
        "product_code_to_name": product_code_map,
        # Hard reminder — echo from prep_data
        "_HARD_REMINDER": prep_data.get("_HARD_REMINDER", ""),
        "_TOTAL_EXEC_BUDGET": prep_data.get("report_ready", {}).get("_TOTAL_EXEC_BUDGET", ""),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
