#!/usr/bin/env python3
"""
summarize_bill.py — 账单缓存数据压缩工具

把 resource_summary.json（800KB+）压缩成 LLM 可用的摘要（<30KB），
避免原始数据污染 context。

用法：
  python3 summarize_bill.py <cache_dir> [--top N] [--month YYYYMM]

输出：标准输出 JSON，结构见下方 schema。
"""

import json
import sys
import os
import argparse
from collections import defaultdict


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_amount(v):
    """整数展示金额"""
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return 0


def summarize_resource_summary(data, top_n=50):
    """
    压缩 resource_summary.json。
    原始结构：{"Response": {"ResourceSummarySet": [...], "Total": N}}
    每条 item 约 30+ 字段，我们只保留关键字段。

    返回两个视图：
    - top：按费用降序的 top_n 条（用于成本资源排行展示）
    - all_slim：全量资源的精简版（仅 ResourceId + ProductName + RealTotalCost + Tags），
                用于环比变化分析（增长/降低/新增/下线），避免遗漏中小资源
    """
    items = []
    if isinstance(data, dict):
        resp = data.get("Response", data)
        raw = resp.get("ResourceSummarySet", resp.get("ResourceSummaryList", []))
    else:
        raw = data

    for r in raw:
        items.append({
            "ResourceId":    r.get("ResourceId", ""),
            "ResourceName":  r.get("ResourceName", ""),
            "ProductCode":   r.get("ProductCode", ""),
            "ProductName":   r.get("ProductName", "") or r.get("ProductCodeName", "") or r.get("BusinessCodeName", ""),
            "RegionName":    r.get("RegionName", ""),
            "PayModeName":   r.get("PayModeName", ""),
            "RealTotalCost": fmt_amount(r.get("RealTotalCost", 0)),
            "Tags":          _extract_tags(r.get("Tags", [])),
        })

    # 按费用降序
    items.sort(key=lambda x: x["RealTotalCost"], reverse=True)
    top = items[:top_n]

    # 全量精简版：只保留环比分析所需字段，去掉 RegionName/PayModeName 等
    all_slim = [
        {
            "ResourceId":    i["ResourceId"],
            "ProductName":   i["ProductName"],
            "ProductCode":   i["ProductCode"],
            "RealTotalCost": i["RealTotalCost"],
            "Tags":          i["Tags"],
        }
        for i in items
    ]

    total = sum(i["RealTotalCost"] for i in items)
    top_total = sum(i["RealTotalCost"] for i in top)

    return {
        "total_resources": len(items),
        "total_cost": total,
        "top_n": top_n,
        "top_cost": top_total,
        "top_coverage_pct": round(top_total / total * 100, 1) if total else 0,
        "resources": top,           # 用于成本排行展示
        "all_slim": all_slim,        # 用于环比变化分析（全量，字段精简）
    }


def _extract_tags(tags, active_tag_keys=None):
    """保留所有有效标签键（由 DescribeTagKeys 动态发现），若无指定则保留全部非系统标签"""
    if active_tag_keys is None:
        # 兼容：未指定时过滤系统标签
        active_tag_keys = None
    result = {}
    system_prefixes = ('tencentcloud:', 'tke-', 'qcloud:')
    for t in tags:
        key = t.get("TagKey", "")
        val = t.get("TagValue", "")
        # 跳过系统标签
        if any(key.startswith(p) or key == p for p in system_prefixes):
            continue
        if key == 'test':
            continue
        if active_tag_keys is not None:
            if key in active_tag_keys:
                result[key] = val
        else:
            result[key] = val
    return result


def summarize_summary_product(data):
    """压缩 summary_product.json，保留所有产品（字段精简）"""
    items = []
    if isinstance(data, dict):
        resp = data.get("Response", data)
        raw = resp.get("SummaryOverview", resp.get("SummaryTotal", []))
    else:
        raw = data

    for r in raw:
        items.append({
            "ProductCode":    r.get("ProductCode", r.get("BusinessCode", "")),
            "ProductName":    r.get("ProductName", "") or r.get("BusinessCodeName", ""),
            "RealTotalCost":  fmt_amount(r.get("RealTotalCost", 0)),
            "TotalCost":      fmt_amount(r.get("TotalCost", 0)),
            "RealTotalCostRatio": r.get("RealTotalCostRatio", ""),
            "PayMode":        r.get("PayMode", ""),
        })

    items.sort(key=lambda x: x["RealTotalCost"], reverse=True)
    return {"products": items, "count": len(items)}


def summarize_summary_region(data):
    """
    压缩 summary_region.json，输出扁平化的地域费用列表。

    注意：summary_region.json 的实际顶层结构没有 `Response` 包装，
    而是直接 `{Ready, SummaryOverview, RequestId}`，与 summary_product 不同。
    本函数兼容两种结构（带/不带 Response 外壳）。

    输出 schema：
      {
        "regions": [{"RegionName": "...", "RealTotalCost": int, "RealTotalCostRatio": "..."}],
        "count": N,
        "total_cost": int,
      }
    """
    items = []
    if isinstance(data, dict):
        # 兼容两种顶层结构：带 Response 包装的 vs 直接平铺的
        resp = data.get("Response", data)
        raw = resp.get("SummaryOverview", [])
    else:
        raw = data

    for r in raw:
        items.append({
            "RegionId":           r.get("RegionId", ""),
            "RegionName":         r.get("RegionName", "(未知地域)"),
            "RealTotalCost":      fmt_amount(r.get("RealTotalCost", 0)),
            "RealTotalCostRatio": r.get("RealTotalCostRatio", ""),
        })

    items.sort(key=lambda x: x["RealTotalCost"], reverse=True)
    total = sum(i["RealTotalCost"] for i in items)
    return {
        "regions": items,
        "count": len(items),
        "total_cost": total,
    }


def summarize_summary_tag(data):
    """压缩 summary_tag.json，保留 customer + head 两个维度。

    实际缓存结构为 {tag_key: {yyyymm: {Response/SummaryOverview}}}
    例如: {"head": {"2026-04": {"SummaryOverview": [...]}, "2026-03": {...}}}
    需要按月份展开后合并。
    """
    result = {}
    if isinstance(data, dict):
        for tag_key, tag_data in data.items():
            if not isinstance(tag_data, dict):
                continue
            # tag_data 可能是 {yyyymm: {SummaryOverview: [...]}} 或 {Response: {SummaryOverview: [...]}}
            month_items = {}  # TagValue -> {yyyymm: cost}
            for sub_key, sub_val in tag_data.items():
                # 兼容直接 Response 包装和月份嵌套两种结构
                if sub_key == "Response":
                    resp = sub_val
                    raw = resp.get("SummaryOverview", [])
                    yyyymm = "unknown"
                elif isinstance(sub_val, dict) and ("SummaryOverview" in sub_val or "Response" in sub_val):
                    resp = sub_val.get("Response", sub_val)
                    raw = resp.get("SummaryOverview", [])
                    yyyymm = sub_key  # 如 "2026-04"
                else:
                    continue
                for r in raw:
                    tv = r.get("TagValue", "(未标记)")
                    if tv not in month_items:
                        month_items[tv] = {}
                    month_items[tv][yyyymm] = fmt_amount(r.get("RealTotalCost", 0))

            # 转为列表，按分析月费用降序
            items = []
            for tv, costs in month_items.items():
                items.append({
                    "TagValue": tv,
                    **costs,  # 各月费用直接展开为字段，如 "2026-04": 140000
                })
            # 按最近月费用排序
            if items:
                sort_key = None
                for k in items[0]:
                    if k != "TagValue":
                        sort_key = k
                        break
                if sort_key:
                    items.sort(key=lambda x: x.get(sort_key, 0), reverse=True)
            result[tag_key] = items
    return result


def main():
    parser = argparse.ArgumentParser(description="压缩账单缓存数据")
    parser.add_argument("cache_dir", help="账单缓存目录，如 <WORKSPACE>/cache/billing/100039081678/202604")
    parser.add_argument("--top", type=int, default=50, help="资源明细保留 Top N（默认50）")
    parser.add_argument("--month", type=str, default="", help="月份标识（仅用于输出元数据）")
    args = parser.parse_args()

    cache_dir = args.cache_dir
    if not os.path.isdir(cache_dir):
        print(json.dumps({"error": f"目录不存在: {cache_dir}"}))
        sys.exit(1)

    result = {"cache_dir": cache_dir, "month": args.month}

    # resource_summary.json
    rs_path = os.path.join(cache_dir, "resource_summary.json")
    if os.path.exists(rs_path):
        result["resource_summary"] = summarize_resource_summary(load_json(rs_path), top_n=args.top)

    # summary_product.json（当前月 + 对照月都可能有，统一处理）
    for fname in ["summary_product.json", "summary_product_prev.json"]:
        fpath = os.path.join(cache_dir, fname)
        if os.path.exists(fpath):
            key = "summary_product" if fname == "summary_product.json" else "summary_product_prev"
            result[key] = summarize_summary_product(load_json(fpath))

    # summary_region.json（顶层结构特殊：无 Response 外壳，直接 SummaryOverview）
    rg_path = os.path.join(cache_dir, "summary_region.json")
    if os.path.exists(rg_path):
        result["summary_region"] = summarize_summary_region(load_json(rg_path))

    # summary_tag.json
    st_path = os.path.join(cache_dir, "summary_tag.json")
    if os.path.exists(st_path):
        result["summary_tag"] = summarize_summary_tag(load_json(st_path))

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
