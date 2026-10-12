#!/usr/bin/env python3
"""Run cloud-inspector-pro collectors with one deterministic entrypoint.

Purpose: remove LLM orchestration overhead from full inspection. The script
reads TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY from the process
environment (no .env / dotenv lookup), derives UIN via cam.GetUserAppId,
writes inspect_meta.json, runs existing collectors concurrently, and emits
collect_summary.json with per-collector elapsed seconds.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
sys.path.insert(0, str(LIB_DIR))
from sign import call_api  # noqa: E402

# ── 并发凭证隔离（2026-06-13，与 cloud-billing-pro/cloud-costopt-pro 同方案）──────────
# 历史 bug：多账号并发时各 specialist 读同一裸名 TENCENTCLOUD_SECRET_ID，last-write-wins
# 互相覆盖 → 多份报告同账号数据。现方案：接 --cloud/--project，进程内经
# build_credential_env.resolve(cloud, project) 从 SSM 取该项目专属凭证（KMS 服务端解密），
# 注入给子进程的私有 env dict，绝不读裸名 os.environ、也不依赖前缀变量。
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(str(SCRIPT_DIR), "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)


def load_env_credentials(cloud: str = "tencent", project: str = None):
    """进程内解析某 (cloud, project) 的裸凭证 dict（不污染 os.environ）。

    凭证唯一来源 = SSM（KMS 加密）。build_credential_env.resolve() 内部只走 SSM，
    没有 env 前缀路径、也没有裸名兜底。本函数因此**强制要求 project 非空**：
      - project 非空 → resolve(cloud, project) 从 SSM 取密钥（KMS 服务端解密）。
      - project 为空 → 直接返回空凭证（上层 build_env 给出引导）。绝不读裸名 env。
    """
    empty = {"secret_id": "", "secret_key": "", "token": ""}
    if not project:
        return empty
    try:
        import build_credential_env as _bce
    except Exception:
        return empty
    try:
        env = _bce.resolve(cloud, project)
    except SystemExit:
        return empty
    return {
        "secret_id": env.get("TENCENTCLOUD_SECRET_ID", "").strip(),
        "secret_key": env.get("TENCENTCLOUD_SECRET_KEY", "").strip(),
        # token：resolve() 做过 AssumeRole（跨账号）时为临时凭证 Token；直连场景为空。
        # 需透传给各采集子进程作 X-TC-Token（注入子进程私有 env，非共享 os.environ）。
        "token": env.get("TENCENTCLOUD_TOKEN", "").strip(),
    }


def resolve_uin(secret_id: str, secret_key: str, token: str = "") -> tuple[str, str]:
    """Call cam.GetUserAppId once to derive (uin, app_id) from AK/SK (+ optional STS token).

    Raises SystemExit on auth failure with a clear hint so the LLM can fall
    back to the 'environment not ready' template defined in SKILL.md.
    """
    try:
        resp = call_api(
            host="cam.tencentcloudapi.com",
            service="cam",
            action="GetUserAppId",
            version="2019-01-16",
            payload={},
            region="ap-guangzhou",
            secret_id=secret_id,
            secret_key=secret_key,
            token=token or None,
        )
    except Exception as e:
        raise SystemExit(
            "GetUserAppId call failed; AK/SK invalid or network unreachable: "
            f"{str(e)[:240]}"
        )
    uin = str(resp.get("OwnerUin") or resp.get("Uin") or "").strip()
    app_id = str(resp.get("AppId") or "").strip()
    if not uin:
        raise SystemExit(
            "GetUserAppId returned no UIN; verify the provided credentials "
            "have cam:GetUserAppId permission."
        )
    return uin, app_id


def setup_env(account_name: str, out_dir: Path, cloud: str = "tencent", project: str = None):
    cred = load_env_credentials(cloud=cloud, project=project)
    sid = cred["secret_id"]
    sk = cred["secret_key"]
    token = cred.get("token", "")
    if not sid or not sk:
        raise SystemExit(
            "Could not resolve credentials from SSM for "
            f"(cloud={cloud}, project={project}). "
            "Credentials live ONLY in SSM (KMS-encrypted); this script does NOT read "
            "plain TENCENTCLOUD_SECRET_ID/KEY from the environment.\n"
            "Ensure the host has the SSM runner key set and a secret exists for this account:\n"
            "  Linux/macOS (bash/zsh):  export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=... && export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=...\n"
            "  Windows PowerShell:      $env:TENCENTCLOUD_BOOTSTRAP_SECRET_ID=\"...\"; $env:TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=\"...\"\n"
            "  Windows CMD:             set TENCENTCLOUD_BOOTSTRAP_SECRET_ID=... && set TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=...\n"
            "The runner key needs only SSM read perms (GetSecretValue/ListSecrets); no kms:* required "
            "(SSM decrypts server-side). Having the runner key auto-enables SSM."
        )
    # 跨账号：resolve() 若做过 AssumeRole，sid/sk 已是临时凭证、token 非空；
    # parent 的 GetUserAppId 也需带这个 token 才能认到目标账号。
    uin, app_id = resolve_uin(sid, sk, token=token)
    display = (account_name or "").strip() or uin
    out_dir.mkdir(parents=True, exist_ok=True)
    # 子进程拿到的是**私有 env 副本**（含本项目专属凭证），与并发其它 worker 互不干扰；
    # 不向共享 os.environ 写 SECRET_ID/KEY，从根上规避凭证竞态。
    env = os.environ.copy()
    env.update({
        "SCRIPT_DIR": str(SCRIPT_DIR),
        "TENCENTCLOUD_SECRET_ID": sid,
        "TENCENTCLOUD_SECRET_KEY": sk,
        "INSPECTION_ACCOUNT": display,
        "INSPECTION_UIN": uin,
        "INSPECTION_APP_ID": app_id,
        "INSPECT_OUT_DIR": str(out_dir),
        "OUT_DIR": str(out_dir),
        "REPORT_SCOPE": "full",
        "CLOUDOPS_ACTIVE_CLOUD": cloud,
        "CLOUDOPS_ACTIVE_PROJECT": project or "",
    })
    # 跨账号临时 Token：注入子进程私有 env（非共享 os.environ）。各采集子进程的
    # sign.py._default_token() 会从自己的私有 env 读到，自动带 X-TC-Token。直连场景 token 为空，不注入。
    if token:
        env["TENCENTCLOUD_TOKEN"] = token
        env["TENCENTCLOUD_SECURITY_TOKEN"] = token  # SDK-naming compat
    # 全链路零污染：派生身份（UIN/AppId/账户名）只随私有 env 副本下发给 collector 子进程，
    # 并写入 inspect_meta.json 供 generate_inspector_report 读取——**不**回写共享 os.environ。
    # 即便本进程顺序处理多账户也绝不互相覆盖（凭证/身份竞态从根上消除）。
    return display, uin, app_id, env


def write_meta(display: str, uin: str, out_dir: Path, env: dict):
    r = call_api(
        host="cvm.tencentcloudapi.com",
        service="cvm",
        action="DescribeRegions",
        version="2017-03-12",
        payload={},
        region="ap-shanghai",
        secret_id=env["TENCENTCLOUD_SECRET_ID"],
        secret_key=env["TENCENTCLOUD_SECRET_KEY"],
    )
    regions = [x["Region"] for x in r.get("RegionSet", []) if x.get("RegionState") == "AVAILABLE"]
    meta = {"regions": regions, "account": display, "uin": uin, "out_dir": str(out_dir)}
    (out_dir / "inspect_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def run_script(name: str, env: dict, timeout: int = 300):
    t0 = time.time()
    cmd = [sys.executable, str(SCRIPT_DIR / name)]
    p = subprocess.run(cmd, env=env, cwd=str(SCRIPT_DIR), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return {
        "name": name,
        "status": "ok" if p.returncode == 0 else "fail",
        "exit_code": p.returncode,
        "elapsed_s": round(time.time() - t0, 3),
        "stdout_tail": p.stdout[-2000:],
        "stderr_tail": p.stderr[-2000:],
    }


def collect_resource_tags(env: dict, out_dir: Path):
    t0 = time.time()
    errors = []
    try:
        month = os.environ.get("TAG_SCAN_MONTH")
        if not month:
            today = dt.date.today()
            first = today.replace(day=1)
            month = (first - dt.timedelta(days=1)).strftime("%Y-%m")
        required = ["head", "business", "customer"]

        def pull_one_key(tag_key):
            out = []
            offset = 0
            while True:
                try:
                    r = call_api(
                        host="billing.tencentcloudapi.com",
                        service="billing",
                        action="DescribeBillResourceSummary",
                        version="2018-07-09",
                        payload={"Month": month, "Offset": offset, "Limit": 1000, "TagKey": tag_key, "TagValue": ""},
                        region="ap-shanghai",
                        secret_id=env["TENCENTCLOUD_SECRET_ID"],
                        secret_key=env["TENCENTCLOUD_SECRET_KEY"],
                    )
                except Exception as e:  # keep inspection running on billing permission failures
                    errors.append({"tag_key": tag_key, "offset": offset, "error": str(e)[:240]})
                    return out, "error"
                batch = r.get("ResourceSummarySet", [])
                out.extend(batch)
                if len(batch) < 1000:
                    break
                offset += 1000
            return out, "ok"

        raw, status = {}, {}
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(pull_one_key, k): k for k in required}
            for fut, k in futs.items():
                raw[k], status[k] = fut.result()
        ok_cnt = sum(1 for s in status.values() if s == "ok")
        scan_status = "ok" if ok_cnt == 3 else ("denied" if ok_cnt == 0 else "partial")
        by_rid = {}
        for tk, items in raw.items():
            for it in items:
                rid = it.get("ResourceId") or f"__noid_{tk}_{it.get('ResourceName','')}"
                by_rid.setdefault(rid, {
                    "biz_code": it.get("BusinessCode", ""),
                    "biz_name": it.get("BusinessCodeName", ""),
                    "product_code": it.get("ProductCode", ""),
                    "product_name": it.get("ProductCodeName", ""),
                    "region": it.get("RegionName") or it.get("RegionId") or "",
                    "resource_id": rid,
                    "resource_name": it.get("ResourceName", ""),
                    "total_cost": float(it.get("RealTotalCost") or it.get("TotalCost") or 0),
                    "missing_keys": [],
                })
                if tk not in by_rid[rid]["missing_keys"]:
                    by_rid[rid]["missing_keys"].append(tk)

        def is_noncompliant(missing):
            mk = set(missing)
            return ("head" in mk) or ("business" in mk and "customer" in mk)

        def verdict(missing):
            mk = set(missing)
            return "🔴🟠" if ("head" in mk and "business" in mk and "customer" in mk) else ("🔴" if "head" in mk else "🟠")

        details = [{**d, "verdict": verdict(d["missing_keys"])} for d in by_rid.values() if is_noncompliant(d["missing_keys"])]
        details.sort(key=lambda x: (-x["total_cost"], x["product_code"], x["resource_id"]))
        by_product = {}
        for d in details:
            bc = d["biz_code"] or "__unknown__"
            by_product.setdefault(bc, {"biz_name": d["biz_name"], "count": 0, "cost_total": 0.0})
            by_product[bc]["count"] += 1
            by_product[bc]["cost_total"] = round(by_product[bc]["cost_total"] + d["total_cost"], 4)
        result = {"required_logic": "head AND (business OR customer)", "month": month, "scan_status": scan_status, "total_untagged_resources": len(details), "by_product": by_product, "untagged_details": details, "errors": errors}
        (out_dir / "inspect_resource_tags.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return {"name": "resource_tags", "status": "ok", "elapsed_s": round(time.time() - t0, 3), "summary": {"month": month, "scan_status": scan_status, "total_untagged": len(details), "errors": len(errors)}}
    except Exception as e:
        return {"name": "resource_tags", "status": "fail", "elapsed_s": round(time.time() - t0, 3), "error": str(e)[:500], "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-name", required=False, default="",
                    help="Display name for report header (optional). Falls back to UIN.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--cloud", default="tencent",
                    help="云厂商（默认 tencent），并发隔离 + 报告头标识用")
    ap.add_argument("--project", default="",
                    help="项目/账号代号（如 ADC/TSP/YX）；必传——进程内经 "
                         "build_credential_env.resolve() 从 SSM 取该账号专属凭证（KMS 服务端解密），"
                         "规避竞态。省略则返回空凭证、走环境未就绪引导（不读裸名 env）")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).resolve()
    display, uin, app_id, env = setup_env(
        args.account_name, out_dir, cloud=args.cloud, project=args.project or None)

    t0 = time.time()
    meta = write_meta(display, uin, out_dir, env)
    collectors = ["inspect_scan.py", "inspect_advisor.py", "inspect_infra.py", "inspect_cam.py", "inspect_cos.py", "inspect_audit_events.py"]
    results = []
    with cf.ThreadPoolExecutor(max_workers=len(collectors) + 1) as ex:
        futs = [ex.submit(run_script, c, env, args.timeout) for c in collectors]
        futs.append(ex.submit(collect_resource_tags, env, out_dir))
        for fut in cf.as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda x: x["name"])
    summary = {
        "status": "ok" if all(r.get("status") == "ok" for r in results) else "partial",
        "account": display,
        "uin": uin,
        "app_id": app_id,
        "out_dir": str(out_dir),
        "regions": len(meta.get("regions", [])),
        "elapsed_s": round(time.time() - t0, 3),
        "collectors": results,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "collect_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ["status", "account", "uin", "out_dir", "regions", "elapsed_s"]}, ensure_ascii=False))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
