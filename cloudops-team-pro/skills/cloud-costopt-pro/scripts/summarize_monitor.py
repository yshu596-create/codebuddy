#!/usr/bin/env python3
"""
summarize_monitor.py — 监控数据压缩工具

把单个实例的 30 天监控原始数据点（720点/指标）压缩成统计摘要，
避免监控数据污染 agent context。

用法（单实例）：
  echo '<monitor_json>' | python3 summarize_monitor.py --stdin
  python3 summarize_monitor.py --file monitor_raw.json

用法（直接传指标数组）：
  python3 summarize_monitor.py --points '[1.2, 0.8, ...]' --metric CPUUsage

输出：JSON 摘要，包含 avg / max / p95 / coverage 等统计量。
"""

import json
import sys
import os
import argparse
import math


def percentile(data, p):
    """计算百分位数（不依赖 numpy）"""
    if not data:
        return 0
    sorted_data = sorted(data)
    idx = (len(sorted_data) - 1) * p / 100
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_data):
        return sorted_data[lo]
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def summarize_points(points, metric_name=""):
    """
    压缩一组监控数据点。
    points: list of float（已过滤 None/null）
    """
    if not points:
        return {
            "metric": metric_name,
            "count": 0,
            "coverage": 0,
            "avg": None,
            "max": None,
            "p95": None,
            "note": "无数据"
        }

    valid = [p for p in points if p is not None and not (isinstance(p, float) and math.isnan(p))]
    total_slots = 720  # 30天 × 24点/天

    avg = round(sum(valid) / len(valid), 2) if valid else 0
    max_val = round(max(valid), 2) if valid else 0
    p95_val = round(percentile(valid, 95), 2) if valid else 0
    coverage = round(len(valid) / total_slots * 100)

    return {
        "metric": metric_name,
        "count": len(valid),
        "coverage": coverage,
        "avg": avg,
        "max": max_val,
        "p95": p95_val,
    }


def summarize_instance_monitor(raw_data):
    """
    压缩单个实例的监控响应（来自 GetMonitorData API）。
    raw_data 结构：
      {"Response": {"DataPoints": [{"Timestamps": [...], "Values": [...]}], "MetricName": "CPUUsage"}}
    或多指标结构：
      {"CPUUsage": {...}, "MemUsage": {...}}
    """
    result = {}

    # 已剥 Response 外壳的单指标响应(call_api 返回 Response 内容):顶层有 MetricName + DataPoints
    if isinstance(raw_data, dict) and ("DataPoints" in raw_data or "MetricName" in raw_data) and not raw_data.get("Response"):
        metric_name = raw_data.get("MetricName", "unknown")
        all_values = []
        for dp in raw_data.get("DataPoints", []):
            all_values.extend([v for v in dp.get("Values", []) if v is not None])
        result[metric_name] = summarize_points(all_values, metric_name)
        return result

    # 多指标字典格式
    if isinstance(raw_data, dict) and not raw_data.get("Response"):
        for metric, data in raw_data.items():
            points = _extract_points(data)
            result[metric] = summarize_points(points, metric)
        return result

    # 单指标 API 响应格式(完整 {Response:{...}})
    resp = raw_data.get("Response", raw_data) if isinstance(raw_data, dict) else {}
    metric_name = resp.get("MetricName", "unknown")
    data_points = resp.get("DataPoints", [])

    all_values = []
    for dp in data_points:
        values = dp.get("Values", [])
        all_values.extend([v for v in values if v is not None])

    result[metric_name] = summarize_points(all_values, metric_name)
    return result


def _extract_points(data):
    """从各种格式中提取数值数组"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        resp = data.get("Response", data)
        dps = resp.get("DataPoints", [])
        values = []
        for dp in dps:
            values.extend(dp.get("Values", []))
        return values
    return []


def main():
    parser = argparse.ArgumentParser(description="压缩监控数据点为统计摘要")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取 JSON")
    parser.add_argument("--file", type=str, help="从文件读取 JSON")
    parser.add_argument("--points", type=str, help="直接传入数据点 JSON 数组")
    parser.add_argument("--metric", type=str, default="metric", help="指标名称（配合 --points 使用）")
    args = parser.parse_args()

    if args.points:
        points = json.loads(args.points)
        result = {args.metric: summarize_points(points, args.metric)}
    elif args.stdin:
        raw = json.loads(sys.stdin.read())
        result = summarize_instance_monitor(raw)
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = summarize_instance_monitor(raw)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
