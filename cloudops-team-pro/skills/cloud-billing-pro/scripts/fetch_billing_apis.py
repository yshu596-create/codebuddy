#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_billing_apis.py — 一键并发拉取账单 API 写入缓存

用途：在 prepare_billing_data.py 检测到缓存空时被 --auto-fetch 调用，
也可独立运行做缓存预热。

工作流：
  1. 解析凭证 (resolve_credentials)
  2. 计算需要的月份范围（默认分析月 + T-1 + 趋势 T-3..T-1）
  3. 并发拉 5 类 API：
     - DescribeBillSummaryByProduct（按月）
     - DescribeBillSummaryByRegion（仅分析月）
     - DescribeBillSummaryByTag（每个 TagKey × 分析月+T-1）
     - DescribeTagKeys（一次）
     - DescribeBillResourceSummary（分析月+T-1，分页）
  4. 限频时退避重试（最多 3 次）
  5. 全部写入 <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/

输出：
  打印 JSON: {"success": True, "missed": [...], "elapsed_sec": N}
"""

import argparse
import json
import os
import sys
import time
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed

# 复用 prepare_billing_data 的凭证解析 + sign
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sign import sign_and_call  # noqa
from prepare_billing_data import resolve_credentials, prev_month_str  # noqa


BILLING_HOST = "billing.tencentcloudapi.com"
BILLING_SVC = "billing"
BILLING_VER = "2018-07-09"

TAG_HOST = "tag.tencentcloudapi.com"
TAG_SVC = "tag"
TAG_VER = "2018-08-13"


def month_to_begin_end(yyyymm):
    """将 'YYYYMM' 转为账单 API 要求的 BeginTime/EndTime 格式。"""
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    last_day = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01 00:00:00", f"{y:04d}-{m:02d}-{last_day:02d} 23:59:59"


def call_with_retry(payload_label, action, payload, secret_id, secret_key, max_retry=3,
                    host=None, service=None, version=None):
    """对单个 API 调用做限频退避重试。默认用 billing 端点；可覆盖。"""
    _host = host or BILLING_HOST
    _svc = service or BILLING_SVC
    _ver = version or BILLING_VER
    backoff = 1.0
    for attempt in range(max_retry):
        try:
            resp = sign_and_call(
                host=_host, service=_svc,
                action=action, version=_ver,
                payload=payload, secret_id=secret_id, secret_key=secret_key,
            )
        except Exception as e:
            if attempt == max_retry - 1:
                return {"Error": {"Code": "Exception", "Message": str(e)}}
            time.sleep(backoff)
            backoff *= 2
            continue
        err = resp.get("Error") if isinstance(resp, dict) else None
        if not err:
            return resp
        code = err.get("Code", "")
        if code == "RequestLimitExceeded" and attempt < max_retry - 1:
            time.sleep(backoff)
            backoff *= 2
            continue
        return resp
    return {"Error": {"Code": "MaxRetryExceeded", "Message": payload_label}}


def fetch_summary_by_product(month, secret_id, secret_key):
    begin, end = month_to_begin_end(month)
    return call_with_retry(
        f"product@{month}", "DescribeBillSummaryByProduct",
        {"BeginTime": begin, "EndTime": end, "PayerUin": ""},
        secret_id, secret_key,
    )


def fetch_summary_by_region(month, secret_id, secret_key):
    begin, end = month_to_begin_end(month)
    return call_with_retry(
        f"region@{month}", "DescribeBillSummaryByRegion",
        {"BeginTime": begin, "EndTime": end, "PayerUin": ""},
        secret_id, secret_key,
    )


def fetch_tag_keys(secret_id, secret_key):
    items = []
    offset, limit = 0, 1000
    while True:
        resp = call_with_retry(
            "tag-keys", "DescribeTagKeys",
            {"Offset": offset, "Limit": limit},
            secret_id, secret_key,
            host=TAG_HOST, service=TAG_SVC, version=TAG_VER,
        )
        if resp.get("Error"):
            return resp
        batch = resp.get("Tags", [])
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return {"Tags": items, "TotalCount": len(items)}


def fetch_summary_by_tag(month, tag_key, secret_id, secret_key):
    begin, end = month_to_begin_end(month)
    return call_with_retry(
        f"tag-{tag_key}@{month}", "DescribeBillSummaryByTag",
        {"BeginTime": begin, "EndTime": end, "TagKey": tag_key},
        secret_id, secret_key,
    )


def fetch_resource_summary(month, secret_id, secret_key):
    """month 为 YYYYMM 格式，API 的 Month 参数要求 YYYY-MM 格式"""
    items = []
    offset, limit = 0, 100
    backoff = 0.3
    api_month = f"{month[:4]}-{month[4:6]}"
    while True:
        resp = call_with_retry(
            f"resource@{month}", "DescribeBillResourceSummary",
            {"Month": api_month, "Offset": offset, "Limit": limit},
            secret_id, secret_key,
        )
        if resp.get("Error"):
            return resp
        batch = resp.get("ResourceSummarySet", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(backoff)
    return {"ResourceSummarySet": items, "TotalCount": len(items)}


def write_cache(cache_base, month, fname, data):
    path = os.path.join(cache_base, month, fname)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_all(creds, cache_base, analysis_month, include_trend=True):
    """并发拉取并写盘。返回 {"missed": [...], "ok": int}"""
    secret_id = creds["secret_id"]
    secret_key = creds["secret_key"]

    # 月份范围
    months_for_summary = [analysis_month, prev_month_str(analysis_month)]
    if include_trend:
        m = prev_month_str(analysis_month)
        for _ in range(2):  # T-2, T-3
            m = prev_month_str(m)
            months_for_summary.append(m)

    months_for_resource = [analysis_month, prev_month_str(analysis_month)]
    months_for_tag = [analysis_month, prev_month_str(analysis_month)]

    missed = []
    ok = 0

    # 1. 先拉 TagKeys（同步，后续 tag 拉取依赖它）
    tag_keys = []
    tk_resp = fetch_tag_keys(secret_id, secret_key)
    if tk_resp.get("Error"):
        missed.append("tag-keys")
    else:
        # DescribeTagKeys 返回 Tags 列表：元素可能是 str 或 {"TagKey": "xxx"} dict
        raw_tags = tk_resp.get("Tags", [])
        for t in raw_tags:
            if isinstance(t, str):
                tag_keys.append(t)
            elif isinstance(t, dict) and t.get("TagKey"):
                tag_keys.append(t["TagKey"])
        write_cache(cache_base, "_meta", "tag_keys.json", tk_resp)
        ok += 1

    # 2. 并发拉 product / region / tag / resource
    # tag 数据按 (month, tag_key) 收到后聚合到 {month: {tag_key: response}}
    tag_buckets = {m: {} for m in months_for_tag}

    futures = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for m in months_for_summary:
            futures[ex.submit(fetch_summary_by_product, m, secret_id, secret_key)] = ("product", m, None)
        # region 只需要分析月
        futures[ex.submit(fetch_summary_by_region, analysis_month, secret_id, secret_key)] = ("region", analysis_month, None)
        for m in months_for_tag:
            for tk in tag_keys:
                futures[ex.submit(fetch_summary_by_tag, m, tk, secret_id, secret_key)] = ("tag", m, tk)
        for m in months_for_resource:
            futures[ex.submit(fetch_resource_summary, m, secret_id, secret_key)] = ("resource", m, None)

        for fut in as_completed(futures):
            kind, month, tk = futures[fut]
            try:
                resp = fut.result()
            except Exception as e:
                missed.append(f"{kind}/{tk or '-'}@{month}: {e}")
                continue
            if resp.get("Error"):
                missed.append(f"{kind}/{tk or '-'}@{month}: {resp['Error'].get('Code')}")
                continue
            if kind == "product":
                write_cache(cache_base, month, "summary_product.json", resp)
            elif kind == "region":
                write_cache(cache_base, month, "summary_region.json", resp)
            elif kind == "tag":
                # 聚合：summary_tag.json = {tag_key: {Response: <api response>}}
                # API 返回的就是 SummaryOverview 等字段，外面包一层 Response 兼容现有 prep 逻辑
                tag_buckets[month][tk] = {"Response": resp}
            elif kind == "resource":
                write_cache(cache_base, month, "resource_summary.json", resp)
            ok += 1

    # 3. tag 聚合写盘 → 每月一个 summary_tag.json
    for month, bucket in tag_buckets.items():
        if bucket:
            write_cache(cache_base, month, "summary_tag.json", bucket)

    return {"missed": missed, "ok": ok}


def main():
    ap = argparse.ArgumentParser(description="并发拉取账单 API 写入缓存")
    ap.add_argument("--month", required=True, help="分析月 YYYYMM，例如 202605")
    ap.add_argument("--cache-base", default="", help="缓存根目录（默认 <WORKSPACE>/cache/billing/{uin}）")
    ap.add_argument("--no-trend", action="store_true", help="不拉趋势 T-2/T-3 月")
    args = ap.parse_args()

    t0 = time.time()
    creds = resolve_credentials()
    if not creds.get("secret_id"):
        print(json.dumps({"error": "missing credentials"}))
        sys.exit(2)

    uin = creds["uin"]
    if args.cache_base:
        cache_base = args.cache_base
    else:
        cache_base = os.path.join(
            os.environ.get("WORKBUDDY_WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "cache", "billing", uin,
        )

    result = fetch_all(creds, cache_base, args.month, include_trend=not args.no_trend)
    result["elapsed_sec"] = round(time.time() - t0, 1)
    result["uin"] = uin
    result["cache_base"] = cache_base
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
