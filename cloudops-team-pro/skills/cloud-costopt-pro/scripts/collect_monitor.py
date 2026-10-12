#!/usr/bin/env python3
"""
collect_monitor.py — Step 2 监控数据采集脚本（含 advisor 跳过逻辑）

用法:
  python3 collect_monitor.py --work-dir <WORK_DIR> [--uin <UIN>] [--appid <APPID>]
      凭证：仅从 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 读取
      UIN：参数 > bill_summary.json > cam.GetUserAppId 反查

功能:
  1. 读取 advisor_cost.json 构建 advisor_hit_ids 跳过名单
  2. 读取 resource_discovery.json 获取资源列表
  3. 跳过 advisor 已命中的资源（直接用 advisor 结论）
  4. 对剩余资源并发采集 30 天监控数据
  5. 压缩监控数据为统计摘要（调用 summarize_monitor.py）
  6. 输出 monitor_summary.json

环境变量（唯一来源）:
  TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY
"""

import os
import subprocess
import sys
import json
import time
import argparse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
from sign import call_api  # noqa: E402
from summarize_monitor import summarize_points  # noqa: E402

LOCK = Lock()
ERRORS = []


def log_err(rid, msg):
    with LOCK:
        ERRORS.append({"rid": rid, "msg": msg[:200]})


# ============== Advisor 跳过逻辑 ==============

def load_advisor_hit_ids(uin, work_dir):
    """
    读取 advisor_cost.json 构建 advisor_hit_ids。
    ⚠️ 路径是 /tmp/costopt_{uin}/advisor_cost.json（/ 分隔，不是下划线）
    如果 work_dir 下也有 advisor_cost.json 则优先使用。
    """
    advisor_hit_ids = set()
    # 优先从 work_dir 读取（同一个工作目录）
    adv_path = os.path.join(work_dir, "advisor_cost.json")
    if not os.path.exists(adv_path):
        # 回退到 /tmp/costopt_{uin}/advisor_cost.json
        adv_path = f"/tmp/costopt_{uin}/advisor_cost.json"
    if not os.path.exists(adv_path):
        print(f"[advisor] no advisor_cost.json found, skip set empty", file=sys.stderr)
        return advisor_hit_ids

    try:
        adv = json.load(open(adv_path))
    except Exception as e:
        print(f"[advisor] failed to load {adv_path}: {e}", file=sys.stderr)
        return advisor_hit_ids

    status = adv.get("scan_status")
    if status not in ("ok", "partial"):
        print(f"[advisor] scan_status={status}, skip set empty (denied/failed)", file=sys.stderr)
        return advisor_hit_ids

    hits = adv.get("hits", [])
    advisor_hit_ids = {h["instance_id"] for h in hits if h.get("instance_id")}
    print(f"[advisor] loaded {len(advisor_hit_ids)} hit IDs from {adv_path}", file=sys.stderr)
    return advisor_hit_ids


# ============== 监控采集函数 ==============

END = datetime.now(timezone.utc) - timedelta(hours=2)  # 2h delay margin
START = END - timedelta(days=30)


def get_metric(sid, sk, region, namespace, metric, instances, retries=3):
    """Call GetMonitorData with retry."""
    for attempt in range(retries):
        try:
            params = {
                "Namespace": namespace,
                "MetricName": metric,
                "Period": 3600,
                "StartTime": START.strftime("%Y-%m-%dT%H:00:00+00:00"),
                "EndTime": END.strftime("%Y-%m-%dT%H:00:00+00:00"),
                "Instances": instances,
            }
            resp = call_api(
                host="monitor.tencentcloudapi.com", service="monitor",
                action="GetMonitorData", version="2018-07-24",
                payload=params, region=region,
                secret_id=sid, secret_key=sk,
            )
            if "Error" not in resp:
                return resp
            err = resp.get("Error", {})
            code = err.get("Code", "")
            if code in ("RequestLimitExceeded", "InternalError"):
                time.sleep(2 ** attempt)
                continue
            return {"_error": code, "_msg": err.get("Message", "")}
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"_error": "Exception", "_msg": str(e)[:150]}
    return {"_error": "Retries", "_msg": "max retries"}


def extract_points(resp):
    if "_error" in resp:
        return None, resp["_error"]
    dps = resp.get("DataPoints", [])
    if not dps:
        return [], None
    return dps[0].get("Values", []), None


def collect_metrics(sid, sk, region, namespace, metrics, dim_name, rid, alt_dim_name=None):
    out = {}
    for metric in metrics:
        instances = [{"Dimensions": [{"Name": dim_name, "Value": rid}]}]
        resp = get_metric(sid, sk, region, namespace, metric, instances)
        pts, err = extract_points(resp)
        if err and alt_dim_name:
            instances = [{"Dimensions": [{"Name": alt_dim_name, "Value": rid}]}]
            resp = get_metric(sid, sk, region, namespace, metric, instances)
            pts, err = extract_points(resp)
        if err:
            out[metric] = {"error": err, "coverage": 0, "avg": None, "max": None, "p95": None}
        else:
            out[metric] = summarize_points(pts, metric)
    return out


# ============== 产品采集器 ==============

def task_cvm(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CVM",
                           ["CPUUsage", "MemUsage"], "InstanceId", rid)


def task_cdb(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CDB",
                           ["CPUUseRate", "MemoryUseRate", "ConnectionUseRate"], "InstanceId", rid)


def task_redis(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/REDIS_MEM",
                           ["CpuUs", "MemUsed", "Connections", "Qps"], "instanceid", rid)


def task_mongodb(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CMONGO",
                           ["MonogdAvgCpuUsage", "MongodAvgMemUsage", "Qps"], "target", rid)


def task_postgres(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/POSTGRES",
                           ["Cpu", "MemoryRate", "Connections"], "resourceId", rid)


def task_es(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CES",
                           ["CpuUsageAvg", "JvmMemUsageAvg", "SearchLatencyAvg"], "uInstanceId", rid)


def task_ckafka(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CKAFKA",
                           ["InstanceProFlow", "InstanceConFlow", "InstanceDiskUsage", "InstanceConnectCount"],
                           "instanceid", rid)


def task_clb(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/LB_PUBLIC",
                           ["ClientConnum", "InTraffic", "OutTraffic"], "vip", rid)


def task_eip(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/EIP",
                           ["TrafficFlowOut", "TrafficFlowIn"], "eipId", rid)


def task_nat(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/NAT_GATEWAY",
                           ["OutBandwidth", "InBandwidth", "Conns"], "natId", rid)


def task_vpn(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/VPNGW",
                           ["VpnOutbandwidth", "VpnInbandwidth"], "vpnGwId", rid)


def task_cfs(sid, sk, rid, region, **kw):
    return collect_metrics(sid, sk, region, "QCE/CFS",
                           ["DataReadIoBytes", "DataWriteIoBytes", "ClientCount"], "FileSystemId", rid)


def task_cos(sid, sk, rid, region, appid, **kw):
    instances = [{"Dimensions": [{"Name": "appid", "Value": appid}, {"Name": "bucket", "Value": rid}]}]
    out = {}
    for m in ["StdStorage", "GetRequests", "InternetTraffic"]:
        resp = get_metric(sid, sk, region, "QCE/COS", m, instances)
        pts, err = extract_points(resp)
        if err:
            out[m] = {"error": err, "coverage": 0, "avg": None, "max": None, "p95": None}
        else:
            out[m] = summarize_points(pts, m)
    return out


def task_tke(sid, sk, cluster_id, region, appid, **kw):
    instances = [{"Dimensions": [
        {"Name": "appid", "Value": appid},
        {"Name": "region", "Value": region},
        {"Name": "tke_cluster_instance_id", "Value": cluster_id},
    ]}]
    out = {}
    for m in ["K8sClusterRateCpuCoreUsedCluster", "K8sClusterRateMemUsageBytesCluster"]:
        resp = get_metric(sid, sk, region, "QCE/TKE2", m, instances)
        pts, err = extract_points(resp)
        if err:
            out[m] = {"error": err, "coverage": 0, "avg": None, "max": None, "p95": None}
        else:
            out[m] = summarize_points(pts, m)
    return out


# ============== Job 构建 ==============

PRODUCT_KEYMAP = {
    "p_cvm": ("cvm", task_cvm),
    "p_cdb": ("cdb", task_cdb),
    "p_redis": ("redis", task_redis),
    "p_mongodb": ("mongodb", task_mongodb),
    "p_postgresql": ("postgres", task_postgres),
    "p_es": ("es", task_es),
    "p_ckafka": ("ckafka", task_ckafka),
    "p_clb": ("clb", task_clb),
    "p_eip": ("eip", task_eip),
    "p_nat": ("nat", task_nat),
    "p_vpn": ("vpn", task_vpn),
    "p_cfs": ("cfs", task_cfs),
    "p_eks": ("tke", task_tke),
    "p_011691": ("tke", task_tke),
}


def build_jobs(disc, lookup, advisor_hit_ids, appid):
    """
    构建 monitor job 列表，跳过 advisor 已命中的资源。
    每个 job = (prod_key, fn, kwargs_dict)
    """
    jobs = []
    skipped_by_advisor = 0

    for label, region_map in disc.get("results", {}).items():
        # Accept both legacy "<name>|<pid>" labels and bare "<pid>" keys
        pid = label.split("|", 1)[1] if "|" in label else label
        entry = PRODUCT_KEYMAP.get(pid)
        if not entry:
            continue
        prod_key, fn = entry
        if not isinstance(region_map, dict):
            continue
        for region, items in region_map.items():
            if not isinstance(items, list):
                continue
            for ins in items:
                # 提取 instance ID
                rid = None
                if prod_key == "cvm":
                    rid = ins.get("InstanceId")
                elif prod_key == "cdb":
                    rid = ins.get("InstanceId")
                elif prod_key == "redis":
                    rid = ins.get("InstanceId")
                elif prod_key == "mongodb":
                    rid = ins.get("InstanceId")
                elif prod_key == "postgres":
                    rid = ins.get("DBInstanceId") or ins.get("InstanceId")
                elif prod_key == "es":
                    rid = ins.get("InstanceId")
                elif prod_key == "ckafka":
                    rid = ins.get("InstanceId")
                elif prod_key == "clb":
                    vips = ins.get("LoadBalancerVips") or []
                    rid = vips[0] if vips else None
                elif prod_key == "eip":
                    rid = ins.get("AddressId")
                elif prod_key == "nat":
                    rid = ins.get("NatGatewayId")
                elif prod_key == "vpn":
                    rid = ins.get("VpnGatewayId")
                elif prod_key == "cfs":
                    rid = ins.get("FileSystemId")
                elif prod_key == "tke":
                    rid = ins.get("ClusterId")

                if not rid:
                    continue

                # ⭐ Advisor 跳过检查
                if rid in advisor_hit_ids:
                    skipped_by_advisor += 1
                    continue

                kw = {"rid": rid, "region": region, "raw": ins}
                if prod_key == "clb":
                    kw["lb_id"] = ins.get("LoadBalancerId")
                if prod_key == "tke":
                    kw["cluster_id"] = rid
                    kw["appid"] = appid
                if prod_key == "cos":
                    kw["appid"] = appid
                jobs.append((prod_key, fn, kw))

    # COS — from billing_lookup
    for rid, info in lookup.items():
        if info.get("product") == "p_cos":
            if rid in advisor_hit_ids:
                skipped_by_advisor += 1
                continue
            jobs.append(("cos", task_cos, {"rid": rid, "region": "ap-shanghai", "raw": info, "appid": appid}))

    print(f"[jobs] total={len(jobs)}, skipped_by_advisor={skipped_by_advisor}", file=sys.stderr)
    return jobs


def run_job(sid, sk, appid, job):
    prod_key, fn, kw = job
    rid = kw.get("rid") or kw.get("cluster_id") or "?"
    # Pop appid from kw if present (some products like cos/tke set it explicitly);
    # otherwise inject the global appid. Avoids "multiple values for keyword" errors.
    call_kw = dict(kw)
    if "appid" in call_kw:
        ap = call_kw.pop("appid")
    else:
        ap = appid
    try:
        out = fn(sid=sid, sk=sk, appid=ap, **call_kw)
    except Exception as e:
        log_err(rid, f"{prod_key}: {e}")
        out = {"_exception": str(e)[:200]}
    return prod_key, rid, kw.get("raw", {}), out


def main():
    parser = argparse.ArgumentParser(description="Step 2: 监控数据采集（含 advisor 跳过）")
    parser.add_argument("--uin", default="", help="账户 UIN（可选，未传则从 bill_summary.json 或 cam.GetUserAppId 反查）")
    parser.add_argument("--work-dir", required=True, help="工作目录（含 resource_discovery.json 等）")
    parser.add_argument("--appid", default="", help="APPID（COS/TKE 监控需要）")
    parser.add_argument("--max-workers", type=int, default=8, help="并发线程数（默认 8）")
    args = parser.parse_args()

    # ── 凭证读取：仅环境变量 TENCENTCLOUD_SECRET_ID/KEY ──────────────
    # 注：路径 2 AssumeRole 场景下，prepare_costopt_data.py 会把 STS 临时
    # AK/SK + Token 写回 TENCENTCLOUD_SECRET_ID/KEY/TOKEN，本脚本无需感知。
    sid = os.environ.get("TENCENTCLOUD_SECRET_ID", "")
    sk = os.environ.get("TENCENTCLOUD_SECRET_KEY", "")
    if not sid or not sk:
        print("ERROR: TENCENTCLOUD_SECRET_ID/KEY 环境变量未设置", file=sys.stderr)
        sys.exit(1)

    # ── UIN 解析：参数优先，其次 bill_summary.json，最后 GetUserAppId 反查 ──
    uin = args.uin
    if not uin:
        bill_path = os.path.join(args.work_dir, "bill_summary.json")
        if os.path.exists(bill_path):
            try:
                with open(bill_path, "r", encoding="utf-8") as fp:
                    uin = str(json.load(fp).get("uin", "")).strip()
            except Exception:
                pass
    if not uin:
        # 走 cam.GetUserAppId 反查（与 prepare_costopt_data.py 主路径同款）
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from prepare_costopt_data import auto_detect_account  # noqa: E402
            detected = auto_detect_account(sid, sk)
            if detected.get("ok"):
                uin = detected["uin"]
        except Exception as e:
            print(f"[uin] auto_detect_account 失败: {e}", file=sys.stderr)
    if not uin:
        print("ERROR: 无法获取 UIN（参数/bill_summary/GetUserAppId 全部失败）", file=sys.stderr)
        sys.exit(1)

    # ── advisor 自动兜底（Step 1.5 未并行执行时的保障）──────────────────────
    # 若 advisor_cost.json 不存在，说明 LLM 未在 Step 1 中并行发起 Step 1.5，
    # 此处自动补跑 scan_advisor.py，确保跳过名单始终有效。
    _adv_paths = [
        os.path.join(args.work_dir, "advisor_cost.json"),
        f"/tmp/costopt_{uin}/advisor_cost.json",
    ]
    if not any(os.path.exists(p) for p in _adv_paths):
        print("[advisor] advisor_cost.json missing — running scan_advisor.py now (Step 1.5 fallback)", file=sys.stderr)
        _script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_advisor.py")
        _env = {**os.environ,
                "INSPECTION_UIN": uin,
                "TENCENTCLOUD_SECRET_ID": sid,
                "TENCENTCLOUD_SECRET_KEY": sk,
                "COSTOPT_OUT_DIR": args.work_dir}
        try:
            _proc = subprocess.run(
                [sys.executable, _script],
                capture_output=True, timeout=90, env=_env,
            )
            if _proc.returncode == 0:
                print("[advisor] scan_advisor.py completed (fallback)", file=sys.stderr)
            else:
                print(f"[advisor] scan_advisor.py failed (rc={_proc.returncode}): {_proc.stderr[-200:]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("[advisor] scan_advisor.py timed out after 90s — continuing without advisor data", file=sys.stderr)
        except Exception as e:
            print(f"[advisor] scan_advisor.py error: {e}", file=sys.stderr)
    # ────────────────────────────────────────────────────────────────────────

    work = args.work_dir

    # 1. 加载 advisor 跳过名单
    advisor_hit_ids = load_advisor_hit_ids(uin, work)

    # 2. 加载资源发现结果和账单查找表
    disc_path = os.path.join(work, "resource_discovery.json")
    lookup_path = os.path.join(work, "billing_lookup.json")
    if not os.path.exists(disc_path):
        print(f"ERROR: {disc_path} not found", file=sys.stderr)
        sys.exit(1)
    disc = json.load(open(disc_path))
    lookup = json.load(open(lookup_path)) if os.path.exists(lookup_path) else {}

    # 3. 构建 job 列表（跳过 advisor 命中资源）
    jobs = build_jobs(disc, lookup, advisor_hit_ids, args.appid)

    # 4. 并发执行
    by_product = {}
    done_count = 0
    total = len(jobs)
    t0 = time.time()

    print(f"[monitor] starting {total} jobs with {args.max_workers} workers...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futs = [ex.submit(run_job, sid, sk, args.appid, j) for j in jobs]
        for fut in as_completed(futs):
            prod_key, rid, raw, summary = fut.result()
            by_product.setdefault(prod_key, {})[rid] = {
                "summary": summary,
                "raw": raw,
            }
            done_count += 1
            if done_count % 20 == 0 or done_count == total:
                elapsed = time.time() - t0
                print(f"  [{done_count}/{total}] {prod_key} {rid} ({elapsed:.0f}s)", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"\n[monitor] done in {elapsed:.1f}s, errors={len(ERRORS)}", file=sys.stderr)

    # 5. 输出结果
    result = {
        "window": {
            "start": START.strftime("%Y-%m-%d %H:00:00"),
            "end": END.strftime("%Y-%m-%d %H:00:00"),
            "tz": "UTC",
        },
        "advisor_skipped": len(advisor_hit_ids),
        "by_product": by_product,
        "errors": ERRORS[:50],
    }

    out_path = os.path.join(work, "monitor_summary.json")
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    # 6. 覆盖率统计
    print("\n=== Coverage Stats ===", file=sys.stderr)
    for prod, items in by_product.items():
        n = len(items)
        ok = 0
        for rid, e in items.items():
            sm = e.get("summary", {})
            if sm and not sm.get("_exception"):
                for mk, mv in sm.items():
                    if isinstance(mv, dict) and mv.get("coverage", 0) >= 50:
                        ok += 1
                        break
        print(f"  {prod}: {ok}/{n} have >=50% coverage", file=sys.stderr)

    print(f"\nOutput: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
