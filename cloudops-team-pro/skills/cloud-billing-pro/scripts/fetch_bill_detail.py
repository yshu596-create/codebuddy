"""
腾讯云 DescribeBillDetail 批量拉取脚本
签名: fetch_bill_detail(month, resource_ids, secret_id, secret_key, concurrency=8) -> dict[resource_id, detail_list]

每个 resource_id 独立拉取(禁止按 BusinessCode 批量拉)，并发度受 concurrency 参数控制。
返回 dict: {resource_id: [detail_items]}，每个 detail_item 包含 ComponentSet。

⚠️ SK 仅以函数参数形式传入。禁止 CLI / argv / 环境变量 / 临时文件传递(SOUL §12.4 红线延伸)。
"""

import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from sign import call_api


def _fetch_single_resource(month, resource_id, secret_id, secret_key):
    """拉取单个资源的计费明细，分页直到为空。"""
    all_details = []
    offset = 0
    limit = 100
    max_retries = 3

    while True:
        payload = {
            "Month": month,
            "ResourceId": resource_id,
            "Limit": limit,
            "Offset": offset,
        }

        resp = None
        for attempt in range(max_retries):
            resp = call_api(
                host="billing.tencentcloudapi.com",
                service="billing",
                action="DescribeBillDetail",
                version="2018-07-09",
                payload=payload,
                secret_id=secret_id,
                secret_key=secret_key,
            )
            if "Error" in resp:
                err_code = resp["Error"].get("Code", "")
                if err_code in ("RequestLimitExceeded", "InternalError"):
                    time.sleep(2 ** attempt)
                    continue
                else:
                    # Non-retryable error
                    return resource_id, all_details
            break

        if resp is None or "Error" in resp:
            break

        detail_set = resp.get("DetailSet", [])
        if not detail_set:
            break

        all_details.extend(detail_set)
        offset += limit
        time.sleep(0.3)  # Rate limit protection

        if len(detail_set) < limit:
            break

    return resource_id, all_details


def fetch_bill_detail(month, resource_ids, secret_id, secret_key, concurrency=8):
    """
    批量拉取多个资源的 DescribeBillDetail。

    Args:
        month: "YYYY-MM" 格式
        resource_ids: list of resource_id strings
        secret_id: 腾讯云 SecretId
        secret_key: 腾讯云 SecretKey
        concurrency: 并发度，默认 5

    Returns:
        dict: {resource_id: [detail_items]}
    """
    results = {}

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _fetch_single_resource, month, rid, secret_id, secret_key
            ): rid
            for rid in resource_ids
        }

        for future in as_completed(futures):
            rid, details = future.result()
            results[rid] = details

    return results
