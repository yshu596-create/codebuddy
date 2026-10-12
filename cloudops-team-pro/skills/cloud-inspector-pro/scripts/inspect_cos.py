#!/usr/bin/env python3
"""Step 3 · COS 存储桶 ACL 公有权限并发扫描。

用法:
    python3 scripts/inspect_cos.py

输入:环境变量 TENCENTCLOUD_SECRET_ID / _KEY
输出:
    $OUT_DIR/inspect_cos.json    ← { buckets: [...], acl_findings: {...} }
    stdout 只返 5 字段 summary JSON

核心设计:
  A. 专属集群 `-adc` 后缀 bucket 自动 skip —— ADC bucket 的 API 协议存在,腾讯
     内网 DNS 也能解析(`cos.ap-shanghai-adc.myqcloud.com` 解到内网 9.x.x.x),
     **但 TLS 握手会被服务端拒绝(`tlsv1 alert access denied`)** —— ADC 集群
     在 TLS 层启用了客户端白名单,只放行客户专属 VPN/专线 + 腾讯 ADC 运维节点。
     公有云控制节点(本脚本运行处)即便能 DNS 解析、TCP 也通,TLS 握手就被切断。
     硬扫会卡 TLS 超时浪费几十秒,所以这里直接 short-circuit。
     报告中单列 "专属集群 N 个需手工核查",由客户在 ADC 内网自行运行扫描脚本。
  B. ThreadPoolExecutor(max_workers=8) 并发扫 ACL,上一版串行 24 bucket 要 273s,
     现在并发 + retry=1 + Timeout=10 后最坏 20s/bucket,8 路并行单批天花板 20s。
  C. 明细落盘到 $OUT_DIR/inspect_cos.json,stdout 只给 summary 不污染 LLM context。
  D. 账户隔离:与其他 inspect_*.py 同策略,$INSPECT_OUT_DIR → /tmp/inspect_$UIN/
     → 都没设则硬失败(2026-05-11 并发污染事故根因,严禁 /tmp 兜底)。
  E. 零外部依赖:使用 sign.py 的 cos_get() 替代 qcloud_cos SDK,无需 pip install。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# 使用 sign.py 的 COS XML API 封装,零外部依赖
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
from sign import cos_get


def resolve_out_dir():
    d = os.environ.get('INSPECT_OUT_DIR', '').strip()
    if d:
        return d
    uin = os.environ.get('INSPECTION_UIN', '').strip()
    if uin:
        return f'/tmp/inspect_{uin}'
    sys.stderr.write('[FATAL] INSPECTION_UIN 或 INSPECT_OUT_DIR 必须至少设置其一\n')
    sys.exit(2)


def get_api_region(location: str):
    """list_buckets 返的 Location 映射到 COS API 支持的地域。

    - 专属集群(如 ap-shanghai-adc):返 None,调用方 skip。
      原因:ADC 集群在 TLS 层启用客户端白名单,公有云控制节点握手会被拒绝
      (`tlsv1 alert access denied`),硬扫只会浪费 TLS 超时时间。
    - 其他地域:直接用。
    """
    if not location:
        return 'ap-guangzhou'
    if location.endswith('-adc'):
        return None
    return location


def _endpoint(region, bucket_name=None):
    """构造 COS XML API endpoint。指定 bucket_name 时用虚拟托管样式。"""
    if bucket_name:
        return f"https://{bucket_name}.cos.{region}.myqcloud.com"
    return f"https://cos.{region}.myqcloud.com"


def _fetch_tags(sid, skey, bucket_name, region):
    """get_bucket_tagging:NoSuchTagSetError 视为合法空,其他异常按空数组兜底不阻塞 ACL。"""
    try:
        resp = cos_get(
            endpoint=_endpoint(region, bucket_name),
            path="/",
            params={"tagging": ""},
            secret_id=sid, secret_key=skey,
            timeout=10,
        )
        tagset = resp.get("Tagging", {}).get("TagSet", {}).get("Tag", []) or []
        out = []
        if isinstance(tagset, dict):
            tagset = [tagset]
        for t in (tagset if isinstance(tagset, list) else []):
            k = t.get("Key", "") if isinstance(t, dict) else ""
            v = t.get("Value", "") if isinstance(t, dict) else ""
            if k:
                out.append({"Key": k, "Value": v})
        return out
    except Exception:
        return []


def check_one(sid, skey, bucket_name, location):
    """检查单个 bucket 的 ACL。"""
    region = get_api_region(location)
    if region is None:
        return {"bucket": bucket_name, "region": location, "skipped": "dedicated-cluster", "tags": []}

    try:
        resp = cos_get(
            endpoint=_endpoint(region, bucket_name),
            path="/",
            params={"acl": ""},
            secret_id=sid, secret_key=skey,
            timeout=10,
        )

        if "Error" in resp:
            return {"bucket": bucket_name, "region": region, "error": resp["Error"].get("Message", str(resp["Error"]))[:200], "tags": []}

        # 解析 ACL grants(cos_get 返回根节点的子节点映射)
        grants_data = resp.get("AccessControlList", {}).get("Grant", [])

        # Normalize: single grant comes as dict, multiple as list
        if isinstance(grants_data, dict):
            grants_data = [grants_data]

        tags = _fetch_tags(sid, skey, bucket_name, region)

        for g in (grants_data or []):
            grantee = g.get("Grantee", {}) or {}
            uri = grantee.get("URI", "")
            if "AllUsers" in uri or "global/AllUsers" in uri:
                perm = g.get("Permission", "")
                if perm in ("WRITE", "FULL_CONTROL"):
                    return {"bucket": bucket_name, "region": region, "public_type": "公有读写", "tags": tags}
                if perm == "READ":
                    return {"bucket": bucket_name, "region": region, "public_type": "公有读", "tags": tags}

        return {"bucket": bucket_name, "region": region, "public_type": None, "tags": tags}
    except Exception as e:
        return {"bucket": bucket_name, "region": region, "error": str(e)[:200], "tags": []}


def main():
    sid = os.environ["TENCENTCLOUD_SECRET_ID"]
    skey = os.environ["TENCENTCLOUD_SECRET_KEY"]

    out_dir = resolve_out_dir()
    os.makedirs(out_dir, exist_ok=True)

    # 1) list_buckets(单次全局,< 2s) —— 用 ap-guangzhou 做全局查询
    resp = cos_get(
        endpoint="https://cos.ap-guangzhou.myqcloud.com",
        path="/",
        secret_id=sid, secret_key=skey,
        timeout=15,
    )

    if "Error" in resp:
        err = resp["Error"]
        sys.stderr.write(f"[FATAL] list_buckets failed: {err.get('Code')}: {err.get('Message')}\n")
        # 写空结果避免下游报文件缺失
        empty = {"buckets": [], "acl_findings": {"total": 0, "public_rw": [], "public_r": [], "skipped": [], "errors": []}}
        json.dump(empty, open(os.path.join(out_dir, "inspect_cos.json"), "w"), ensure_ascii=False)
        print(json.dumps({"total": 0, "public_rw": 0, "public_r": 0, "skipped": 0, "errors": 1, "out_dir": out_dir}, ensure_ascii=False))
        return

    # 解析 list_buckets 响应(cos_get 返回根节点的子节点映射)
    # 防御:无 bucket 时 cos_get 把空 <Buckets/> 解析为空字符串而非空 dict
    buckets_node = resp.get("Buckets", {})
    if not isinstance(buckets_node, dict):
        buckets_raw = []
    else:
        buckets_raw = buckets_node.get("Bucket", [])
        if isinstance(buckets_raw, dict):
            buckets_raw = [buckets_raw]

    # 标准化 bucket 字段
    buckets = []
    for b in buckets_raw:
        name = b.get("Name", "")
        location = b.get("Location", "")
        creation_date = b.get("CreationDate", "")
        buckets.append({"Name": name, "Location": location, "CreationDate": creation_date})

    # 2) 并发扫 ACL + Tags
    findings = {"total": len(buckets),
                "public_rw": [],
                "public_r": [],
                "skipped": [],
                "errors": []}
    tags_map = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(check_one, sid, skey, b["Name"], b.get("Location", ""))
                for b in buckets]
        for fut in as_completed(futs):
            r = fut.result()
            tags_map[r["bucket"]] = r.get("tags", [])
            if r.get("skipped"):
                findings["skipped"].append(r)
            elif r.get("error"):
                findings["errors"].append(r)
            elif r.get("public_type") == "公有读写":
                findings["public_rw"].append(r)
            elif r.get("public_type") == "公有读":
                findings["public_r"].append(r)
            # public_type is None → 合规 bucket,不入表(节省文件大小)

    # 回填 buckets[].Tags
    for b in buckets:
        b["Tags"] = tags_map.get(b["Name"], [])

    # 3) 明细落盘,stdout 只返 summary
    out = {"buckets": buckets, "acl_findings": findings}
    json.dump(out, open(os.path.join(out_dir, "inspect_cos.json"), "w"),
              ensure_ascii=False, default=str)
    print(json.dumps({
        "total": len(buckets),
        "public_rw": len(findings["public_rw"]),
        "public_r": len(findings["public_r"]),
        "skipped": len(findings["skipped"]),
        "errors": len(findings["errors"]),
        "out_dir": out_dir,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
