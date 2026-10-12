#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""query_resources.py — 腾讯云资源清点（只读盘点）。

凭证唯一来源 = SSM（KMS 加密）。本脚本接收 --cloud/--project，进程内调
build_credential_env.resolve(cloud, project) 从 SSM 取该账号专属凭证（KMS 服务端解密），
全程内存传递给 sign.py，绝不写共享 os.environ、绝不落盘明文凭证。跨账号 STS AssumeRole
已收敛进 resolve()：若 secret 带 role_arn，resolve() 已在进程内换好临时凭证 + Token，
本脚本经 sign.set_process_token() 自动带上 X-TC-Token，无需感知。

设计要点：
  · 多地域并发：先 DescribeRegions 拉地域，再 ThreadPoolExecutor(max_workers=8) 并发各域
    region-scoped 采集器（CVM/CBS/CLB/CDB/Redis/EIP/Lighthouse/SG/TKE）。
  · 全局服务只查一次：CAM 子账户、COS bucket 列表是 region 无关的，单独跑。
  · 大结果落盘 JSON，stdout 只 print summary（避免 LLM context 膨胀）。
  · 任一采集器失败降级标注（status/error），不中断整体；凭证缺失返回 need_credentials。

只读盘点，绝不创建/修改/删除任何云资源。

用法：
  python3 query_resources.py --cloud tencent --project YX --lang zh \
      --out-dir <WORKSPACE>/cache/resource/YX
退出码：0 成功（含 need_credentials，由上层渲染引导）；非 0 仅用于参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))

# 注入 cloudops-accounts-pro/scripts 以 import build_credential_env（SSM 凭证唯一来源）
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)

from sign import call_api, cos_get, set_process_token  # noqa: E402

# CVM GPU 机型族前缀（DescribeInstances 返回 InstanceType 形如 GN10X.2XLARGE40）。
# GPU 实例族：GN/GI/GT/PNV 系列（含 GN6/GN7/GN10/GI3X/GT4/PNV4 等），统一按前缀判定。
_GPU_PREFIXES = ("GN", "GI", "GT", "PNV", "GNV")


# ───────────────────────── 凭证解析（SSM-only） ─────────────────────────

def load_credentials(cloud: str, project: str) -> dict:
    """进程内解析 (cloud, project) 凭证，返回扁平 dict（不污染 os.environ）。

    凭证字段缺失则返回空串，上层据此判 need_credentials。跨账号 token 由 resolve()
    在带 role_arn 时回填，直连场景为空。
    """
    empty = {"secret_id": "", "secret_key": "", "token": "", "region": ""}
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
        "token": env.get("TENCENTCLOUD_TOKEN", "").strip(),
        "region": env.get("TENCENTCLOUD_REGION", "").strip() or "ap-guangzhou",
    }


def detect_account(sid: str, sk: str, token: str = "") -> dict:
    """cam.GetUserAppId 自动派生 UIN / AppId。失败返回 {ok:False,error}。"""
    resp = call_api(
        host="cam.tencentcloudapi.com", service="cam", action="GetUserAppId",
        version="2019-01-16", payload={}, region="ap-guangzhou",
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"ok": False, "error": f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}"}
    uin = str(resp.get("OwnerUin") or resp.get("Uin") or "").strip()
    app_id = str(resp.get("AppId") or "").strip()
    if not uin:
        return {"ok": False, "error": f"GetUserAppId 未返回 UIN: {resp}"}
    return {"ok": True, "uin": uin, "app_id": app_id}


# ───────────────────────── 地域枚举 ─────────────────────────

def list_regions(sid: str, sk: str, token: str = "") -> list[str]:
    """DescribeRegions 拉取 AVAILABLE 地域码；失败回退常用 5 地域。"""
    resp = call_api(
        host="cvm.tencentcloudapi.com", service="cvm", action="DescribeRegions",
        version="2017-03-12", payload={}, region="ap-guangzhou",
        secret_id=sid, secret_key=sk, token=token,
    )
    regions = []
    for r in resp.get("RegionSet", []) or []:
        if r.get("RegionState") == "AVAILABLE" and r.get("Region"):
            regions.append(r["Region"])
    return regions or ["ap-guangzhou", "ap-shanghai", "ap-beijing", "ap-chengdu", "ap-hongkong"]


# ───────────────────────── 全局采集器（region 无关） ─────────────────────────

def collect_subaccounts(sid: str, sk: str, token: str = "") -> dict:
    """CAM 子账户清单（ListUsers）+ 协作者（ListCollaborators）。"""
    out = {"status": "ok", "sub_users": [], "collaborators": [], "error": ""}
    resp = call_api(
        host="cam.tencentcloudapi.com", service="cam", action="ListUsers",
        version="2019-01-16", payload={}, region="ap-guangzhou",
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        out["status"] = "error"
        out["error"] = f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}"
        return out
    for u in resp.get("Data", []) or []:
        out["sub_users"].append({
            "uin": str(u.get("Uin", "")),
            "name": u.get("Name", ""),
            "uid": str(u.get("Uid", "")),
            "remark": u.get("Remark", ""),
            "console_login": u.get("ConsoleLogin", ""),
            "last_login": u.get("LastLoginTime", "") or "",
        })
    # 协作者（可选，无权时降级）
    cresp = call_api(
        host="cam.tencentcloudapi.com", service="cam", action="ListCollaborators",
        version="2019-01-16", payload={}, region="ap-guangzhou",
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" not in cresp:
        for c in cresp.get("Data", []) or []:
            out["collaborators"].append({
                "uin": str(c.get("Uin", "")), "name": c.get("Name", ""),
            })
    return out


def collect_cos_buckets(sid: str, sk: str, app_id: str, token: str = "") -> dict:
    """COS bucket 全量清单（service GET，region 无关）。返回每桶名/地域。"""
    out = {"status": "ok", "buckets": [], "error": ""}
    resp = cos_get(
        endpoint="https://service.cos.myqcloud.com", path="/", params=None,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        out["status"] = "error"
        out["error"] = f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}"
        return out
    buckets = resp.get("Buckets", {})
    items = buckets.get("Bucket", []) if isinstance(buckets, dict) else buckets
    if isinstance(items, dict):
        items = [items]
    for b in items or []:
        if isinstance(b, dict):
            out["buckets"].append({
                "name": b.get("Name", ""),
                "region": b.get("Location", b.get("Region", "")),
                "create_date": b.get("CreationDate", ""),
            })
    return out


def _days_left(expire_str: str) -> int | None:
    """把 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD' 解析为距今天数（向下取整）。失败返回 None。"""
    if not expire_str:
        return None
    import datetime as _dt
    s = str(expire_str).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = _dt.datetime.strptime(s[:len(fmt) + 2] if "%H" in fmt else s[:10], fmt)
            return (dt.date() - _dt.date.today()).days
        except Exception:
            continue
    return None


# ───────────────── 跨资源到期/续费归一化 helper ─────────────────
# 背景：腾讯云各产品的"到期日 / 续费标志"字段名与类型完全不统一——
#   CVM   : ExpiredTime  + RenewFlag(String 枚举)
#   CDB   : ExpireTime   + AutoRenewFlag(Int 0未开/1已开/2已关)
#   CBS   : DeadlineTime + RenewFlag(String 枚举)
#   Redis : DeadlineTime + AutoRenewFlag(Int 0未设/1已设/2不再续)
#   Light : ExpiredTime  + RenewFlag(String，部分版本无此字段)
# 因此用统一 helper 吸收差异：多候选字段名 + 双类型续费值，归一成中性事实结构。
# 注意：纯陈列视角——只回答"开没开自动续费"，不打风险等级、不催续费。

# 包年包月计费标识：各产品都用这些值之一表示"预付费/包年包月"
_PREPAID_VALUES = {"PREPAID", "PREPAID_BY_MONTH", "1"}

def _is_prepaid(charge) -> bool:
    """判定是否包年包月（吃 String/Int 两类计费标识）。"""
    if charge is None:
        return False
    return str(charge).strip().upper() in {v.upper() for v in _PREPAID_VALUES}


def _pick_deadline(item: dict) -> str:
    """多候选字段名提取到期日（兼容各产品差异）。取不到返回空串。"""
    for key in ("ExpiredTime", "ExpireTime", "DeadlineTime", "deadlineTime", "EndTime"):
        v = item.get(key)
        if v and str(v).strip() and not str(v).startswith("0000"):
            return str(v).strip()
    return ""


def _norm_renew(item: dict):
    """归一化续费方式为 (state, auto_renew_bool)。

    state ∈ {auto, manual, off, unknown}，中性事实标注：
      - String 枚举(RenewFlag): NOTIFY_AND_AUTO_RENEW=auto / NOTIFY_AND_MANUAL_RENEW=manual /
        DISABLE_NOTIFY_AND_MANUAL_RENEW=off
      - Int(AutoRenewFlag): 1=auto / 0=manual(未开) / 2=off(不再续)
    取不到字段时返回 unknown（保守，不臆断）。
    """
    # 优先 String 枚举（RenewFlag）
    rf = item.get("RenewFlag")
    if isinstance(rf, str) and rf.strip():
        u = rf.strip().upper()
        if u == "NOTIFY_AND_AUTO_RENEW":
            return "auto", True
        if u == "DISABLE_NOTIFY_AND_MANUAL_RENEW":
            return "off", False
        if u == "NOTIFY_AND_MANUAL_RENEW":
            return "manual", False
    # 再看 Int 类（AutoRenewFlag / autoRenew / autoRenewFlag）
    for key in ("AutoRenewFlag", "autoRenew", "autoRenewFlag"):
        v = item.get(key)
        if v is not None and str(v).strip() != "":
            try:
                iv = int(v)
            except Exception:
                continue
            if iv == 1:
                return "auto", True
            if iv == 2:
                return "off", False
            return "manual", False  # 0
    return "unknown", False


def _mk_expiry_item(item: dict, rtype: str, region: str,
                    id_keys=("InstanceId", "unInstanceId", "DiskId"),
                    name_keys=("InstanceName", "instanceName", "DiskName")) -> dict | None:
    """把任意产品的一条实例归一成统一到期项；非包年包月或无到期日则返回 None。

    rtype: 资源类型显示键（cvm/cdb/redis/cbs/lighthouse/mongodb/postgres/cynosdb）。
    """
    charge = (item.get("InstanceChargeType") or item.get("DiskChargeType")
              or item.get("PayMode") or item.get("BillingMode") or item.get("ChargeType"))
    if charge is not None and not _is_prepaid(charge):
        return None
    deadline = _pick_deadline(item)
    if not deadline:
        return None
    rid = next((str(item.get(k)) for k in id_keys if item.get(k)), "")
    name = next((str(item.get(k)) for k in name_keys if item.get(k)), "")
    state, auto = _norm_renew(item)
    return {
        "resource_type": rtype,
        "instance_id": rid,
        "name": name,
        "region": region,
        "deadline": deadline,
        "days_left": _days_left(deadline),
        "renew_state": state,
        "auto_renew": auto,
    }


def collect_ssl_certs(sid: str, sk: str, token: str = "") -> dict:
    """SSL 证书清单（DescribeCertificates，region 无关）——纯清点视角。

    云管家只陈列证书清单 + 到期日（事实属性），**不做风险分级、不催续签**：
    SSL 到期风险分级与续签处置归云巡检（`cloud-inspector-pro` §12 SSL，🔴🟠分级 + 应急响应）。
    这里 `days_left` 只是一个事实数字（距到期天数），不映射任何风险等级。
    """
    out = {"status": "ok", "total": 0, "certs": [], "error": ""}
    resp = call_api(
        host="ssl.tencentcloudapi.com", service="ssl", action="DescribeCertificates",
        version="2019-12-05", payload={"Limit": 100}, region="ap-guangzhou",
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        out["status"] = "error"
        out["error"] = f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}"
        return out
    out["total"] = resp.get("TotalCount", 0)
    for c in resp.get("Certificates", []) or []:
        end = c.get("CertEndTime", "") or ""
        dl = _days_left(end)
        out["certs"].append({
            "domain": c.get("Domain", "") or "",
            "alias": c.get("Alias", "") or "",
            "status": c.get("StatusName", "") or str(c.get("Status", "")),
            "end_time": end,
            "days_left": dl if dl is not None else "",
        })
    return out


# ───────────────────────── 区域采集器（per-region） ─────────────────────────

def _cvm_in_region(region, sid, sk, token):
    """单地域 CVM：分页 DescribeInstances，统计总数 + GPU 实例。"""
    instances, offset, limit = [], 0, 100
    while True:
        resp = call_api(
            host="cvm.tencentcloudapi.com", service="cvm", action="DescribeInstances",
            version="2017-03-12", payload={"Offset": offset, "Limit": limit},
            region=region, secret_id=sid, secret_key=sk, token=token,
        )
        if "Error" in resp:
            return {"region": region, "error": f"{resp['Error'].get('Code')}: {resp['Error'].get('Message')}", "instances": []}
        batch = resp.get("InstanceSet", []) or []
        for i in batch:
            itype = i.get("InstanceType", "") or ""
            is_gpu = (i.get("GPUInfo") not in (None, {}, [])) or itype.upper().startswith(_GPU_PREFIXES)
            charge = i.get("InstanceChargeType", "") or ""
            # 到期日：仅包年包月(PREPAID)有意义，按量计费无到期日。纯事实属性，不分级、不催续费。
            deadline = i.get("ExpiredTime", "") if charge == "PREPAID" else ""
            # 续费方式(RenewFlag)：仅包年包月有意义。纯事实属性——只陈列"是否开了自动续费"，
            # 不催续费、不打风险。枚举：NOTIFY_AND_AUTO_RENEW=自动续费，
            # NOTIFY_AND_MANUAL_RENEW=手动续费(到期提醒)，DISABLE_NOTIFY_AND_MANUAL_RENEW=不续费不提醒。
            renew_flag = i.get("RenewFlag", "") if charge == "PREPAID" else ""
            auto_renew = (renew_flag == "NOTIFY_AND_AUTO_RENEW")
            # 标签清单：用于全量标签覆盖率统计（纯统计，不判 head/business 必填）
            tags = [t.get("Key", "") for t in (i.get("Tags") or []) if t.get("Key")]
            instances.append({
                "instance_id": i.get("InstanceId", ""),
                "name": i.get("InstanceName", ""),
                "instance_type": itype,
                "cpu": i.get("CPU", 0), "memory": i.get("Memory", 0),
                "state": i.get("InstanceState", ""),
                "charge_type": charge,
                "deadline": deadline,
                "renew_flag": renew_flag,
                "auto_renew": auto_renew,
                "tags": tags,
                "tagged": bool(tags),
                "private_ip": (i.get("PrivateIpAddresses") or [""])[0] if i.get("PrivateIpAddresses") else "",
                "public_ip": (i.get("PublicIpAddresses") or [""])[0] if i.get("PublicIpAddresses") else "",
                "zone": (i.get("Placement") or {}).get("Zone", ""),
                "os": i.get("OsName", ""),
                "is_gpu": bool(is_gpu),
            })
        total = resp.get("TotalCount", 0)
        offset += limit
        if offset >= total or not batch:
            break
    return {"region": region, "error": "", "instances": instances}


def _cbs_in_region(region, sid, sk, token):
    resp = call_api(
        host="cbs.tencentcloudapi.com", service="cbs", action="DescribeDisks",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "total_gb": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    disks = resp.get("DiskSet", []) or []
    # CBS: DeadlineTime + RenewFlag(String)，DiskName 作名称，DiskId 作 ID
    exp = []
    for d in disks:
        it = _mk_expiry_item(d, "cbs", region, id_keys=("DiskId",), name_keys=("DiskName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(disks)),
            "total_gb": sum(d.get("DiskSize", 0) for d in disks), "expiry_items": exp, "error": ""}


def _clb_in_region(region, sid, sk, token):
    resp = call_api(
        host="clb.tencentcloudapi.com", service="clb", action="DescribeLoadBalancers",
        version="2018-03-17", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _vpc_in_region(region, sid, sk, token):
    out = {"region": region, "vpc": 0, "subnet": 0, "error": ""}
    v = call_api(host="vpc.tencentcloudapi.com", service="vpc", action="DescribeVpcs",
                 version="2017-03-12", payload={"Limit": 100}, region=region,
                 secret_id=sid, secret_key=sk, token=token)
    if "Error" in v:
        out["error"] = v["Error"].get("Code", ""); return out
    out["vpc"] = v.get("TotalCount", 0)
    s = call_api(host="vpc.tencentcloudapi.com", service="vpc", action="DescribeSubnets",
                 version="2017-03-12", payload={"Limit": 100}, region=region,
                 secret_id=sid, secret_key=sk, token=token)
    if "Error" not in s:
        out["subnet"] = s.get("TotalCount", 0)
    return out


def _eip_in_region(region, sid, sk, token):
    resp = call_api(
        host="vpc.tencentcloudapi.com", service="vpc", action="DescribeAddresses",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "unbound": 0, "error": resp["Error"].get("Code", "")}
    addrs = resp.get("AddressSet", []) or []
    unbound = sum(1 for a in addrs if a.get("AddressStatus") == "UNBIND")
    return {"region": region, "count": resp.get("TotalCount", len(addrs)), "unbound": unbound, "error": ""}


def _sg_in_region(region, sid, sk, token):
    resp = call_api(
        host="vpc.tencentcloudapi.com", service="vpc", action="DescribeSecurityGroups",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _cdb_in_region(region, sid, sk, token):
    resp = call_api(
        host="cdb.tencentcloudapi.com", service="cdb", action="DescribeDBInstances",
        version="2017-03-20", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # CDB: ExpireTime + AutoRenewFlag(Int 0/1/2)，计费 PayType(0包月/1按量)用 BillingMode 兜底
    items = resp.get("Items", []) or []
    exp = []
    for d in items:
        it = _mk_expiry_item(d, "cdb", region,
                             id_keys=("InstanceId",), name_keys=("InstanceName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(items)),
            "expiry_items": exp, "error": ""}


def _redis_in_region(region, sid, sk, token):
    resp = call_api(
        host="redis.tencentcloudapi.com", service="redis", action="DescribeInstances",
        version="2018-04-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # Redis: DeadlineTime + AutoRenewFlag(Int)，BillingMode(0按量/1包月)
    instset = resp.get("InstanceSet", []) or []
    exp = []
    for d in instset:
        it = _mk_expiry_item(d, "redis", region,
                             id_keys=("InstanceId", "unInstanceId"),
                             name_keys=("InstanceName", "instanceName"))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(instset)),
            "expiry_items": exp, "error": ""}


def _lighthouse_in_region(region, sid, sk, token):
    resp = call_api(
        host="lighthouse.tencentcloudapi.com", service="lighthouse", action="DescribeInstances",
        version="2020-03-24", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # Lighthouse: 套餐均为包年包月，ExpiredTime + RenewFlag(部分版本无)
    instset = resp.get("InstanceSet", []) or []
    exp = []
    for d in instset:
        # Lighthouse 默认全是预付费，不带 charge 字段时也应纳入：临时补一个 PREPAID 标识
        d.setdefault("InstanceChargeType", "PREPAID")
        it = _mk_expiry_item(d, "lighthouse", region,
                             id_keys=("InstanceId",), name_keys=("InstanceName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(instset)),
            "expiry_items": exp, "error": ""}


def _mongodb_in_region(region, sid, sk, token):
    """云数据库 MongoDB（DescribeDBInstances）。"""
    resp = call_api(
        host="mongodb.tencentcloudapi.com", service="mongodb", action="DescribeDBInstances",
        version="2019-07-25", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # MongoDB: InstanceDetails，DeadLine/ExpireTime + AutoRenewFlag(Int)，PayMode(0包月/1按量)
    rows = resp.get("InstanceDetails", []) or []
    exp = []
    for d in rows:
        it = _mk_expiry_item(d, "mongodb", region,
                             id_keys=("InstanceId",), name_keys=("InstanceName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(rows)),
            "expiry_items": exp, "error": ""}


def _postgres_in_region(region, sid, sk, token):
    """云数据库 PostgreSQL（DescribeDBInstances）。"""
    resp = call_api(
        host="postgres.tencentcloudapi.com", service="postgres", action="DescribeDBInstances",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # PostgreSQL: DBInstanceSet，ExpireTime + AutoRenewFlag(Int)，PayType(prepaid/postpaid)
    rows = resp.get("DBInstanceSet", []) or []
    exp = []
    for d in rows:
        # PG PayType 用字符串 prepaid/postpaid，统一成 PREPAID 供 helper 判定
        pt = str(d.get("PayType", "") or "").lower()
        if pt == "prepaid":
            d.setdefault("InstanceChargeType", "PREPAID")
        it = _mk_expiry_item(d, "postgres", region,
                             id_keys=("DBInstanceId",), name_keys=("DBInstanceName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(rows)),
            "expiry_items": exp, "error": ""}


def _cynosdb_in_region(region, sid, sk, token):
    """TDSQL-C（CynosDB）集群（DescribeClusters）。"""
    resp = call_api(
        host="cynosdb.tencentcloudapi.com", service="cynosdb", action="DescribeClusters",
        version="2019-01-07", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "expiry_items": [], "error": resp["Error"].get("Code", "")}
    # CynosDB: ClusterSet，PayMode(0按量/1包月)，到期日多用 ExpireTime；RenewFlag(Int)
    rows = resp.get("ClusterSet", []) or []
    exp = []
    for d in rows:
        it = _mk_expiry_item(d, "cynosdb", region,
                             id_keys=("ClusterId",), name_keys=("ClusterName",))
        if it:
            exp.append(it)
    return {"region": region, "count": resp.get("TotalCount", len(rows)),
            "expiry_items": exp, "error": ""}


def _ckafka_in_region(region, sid, sk, token):
    """消息队列 CKafka 实例（DescribeInstances）。"""
    resp = call_api(
        host="ckafka.tencentcloudapi.com", service="ckafka", action="DescribeInstances",
        version="2019-08-19", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    # CKafka 总数在 Result.TotalCount
    res = resp.get("Result") or {}
    total = res.get("TotalCount", resp.get("TotalCount", 0))
    return {"region": region, "count": total, "error": ""}


def _es_in_region(region, sid, sk, token):
    """Elasticsearch Service 集群（DescribeInstances）。"""
    resp = call_api(
        host="es.tencentcloudapi.com", service="es", action="DescribeInstances",
        version="2018-04-16", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _scf_in_region(region, sid, sk, token):
    """云函数 SCF（ListFunctions）。"""
    resp = call_api(
        host="scf.tencentcloudapi.com", service="scf", action="ListFunctions",
        version="2018-04-16", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", len(resp.get("Functions", []) or [])), "error": ""}


def _tcr_in_region(region, sid, sk, token):
    """容器镜像仓库 TCR 企业版实例（DescribeInstances）。"""
    resp = call_api(
        host="tcr.tencentcloudapi.com", service="tcr", action="DescribeInstances",
        version="2019-09-24", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _nat_in_region(region, sid, sk, token):
    """NAT 网关（DescribeNatGateways）。"""
    resp = call_api(
        host="vpc.tencentcloudapi.com", service="vpc", action="DescribeNatGateways",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _vpn_in_region(region, sid, sk, token):
    """VPN 网关（DescribeVpnGateways）。"""
    resp = call_api(
        host="vpc.tencentcloudapi.com", service="vpc", action="DescribeVpnGateways",
        version="2017-03-12", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    return {"region": region, "count": resp.get("TotalCount", 0), "error": ""}


def _apigw_in_region(region, sid, sk, token):
    """API 网关服务（DescribeServicesStatus）。"""
    resp = call_api(
        host="apigateway.tencentcloudapi.com", service="apigateway", action="DescribeServicesStatus",
        version="2018-08-08", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    res = resp.get("Result") or {}
    total = res.get("TotalCount", resp.get("TotalCount", 0))
    return {"region": region, "count": total, "error": ""}


def _cfs_in_region(region, sid, sk, token):
    """文件存储 CFS（DescribeCfsFileSystems）。"""
    resp = call_api(
        host="cfs.tencentcloudapi.com", service="cfs", action="DescribeCfsFileSystems",
        version="2019-07-19", payload={}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in resp:
        return {"region": region, "count": 0, "error": resp["Error"].get("Code", "")}
    fs = resp.get("FileSystems", []) or []
    return {"region": region, "count": resp.get("TotalCount", len(fs)), "error": ""}


def _tke_in_region(region, sid, sk, token):
    """单地域 TKE：DescribeClusters → 每集群 DescribeClusterInstances 拉节点规格。"""
    cl = call_api(
        host="tke.tencentcloudapi.com", service="tke", action="DescribeClusters",
        version="2018-05-25", payload={"Limit": 100}, region=region,
        secret_id=sid, secret_key=sk, token=token,
    )
    if "Error" in cl:
        return {"region": region, "clusters": [], "error": cl["Error"].get("Code", "")}
    clusters = []
    for c in cl.get("Clusters", []) or []:
        cid = c.get("ClusterId", "")
        node_specs = {}
        node_total = 0
        ni = call_api(
            host="tke.tencentcloudapi.com", service="tke", action="DescribeClusterInstances",
            version="2018-05-25", payload={"ClusterId": cid, "Limit": 100},
            region=region, secret_id=sid, secret_key=sk, token=token,
        )
        if "Error" not in ni:
            for n in ni.get("InstanceSet", []) or []:
                node_total += 1
                spec = n.get("InstanceType") or "unknown"
                node_specs[spec] = node_specs.get(spec, 0) + 1
        clusters.append({
            "cluster_id": cid,
            "name": c.get("ClusterName", ""),
            "status": c.get("ClusterStatus", ""),
            "k8s_version": (c.get("ClusterVersion") or ""),
            "type": c.get("ClusterType", ""),
            "node_total": node_total or (c.get("ClusterNodeNum") or 0),
            "node_specs": node_specs,
        })
    return {"region": region, "clusters": clusters, "error": ""}


# 区域采集器注册表：key → 函数
_REGION_COLLECTORS = {
    "cvm": _cvm_in_region,
    "cbs": _cbs_in_region,
    "clb": _clb_in_region,
    "vpc": _vpc_in_region,
    "eip": _eip_in_region,
    "sg": _sg_in_region,
    "cdb": _cdb_in_region,
    "redis": _redis_in_region,
    "lighthouse": _lighthouse_in_region,
    "tke": _tke_in_region,
    # ── 新增：数据库栈 ──
    "mongodb": _mongodb_in_region,
    "postgres": _postgres_in_region,
    "cynosdb": _cynosdb_in_region,
    "ckafka": _ckafka_in_region,
    "es": _es_in_region,
    # ── 新增：Serverless / 容器 ──
    "scf": _scf_in_region,
    "tcr": _tcr_in_region,
    # ── 新增：扩展网络 / 中间件 / 存储 ──
    "nat": _nat_in_region,
    "vpn": _vpn_in_region,
    "apigw": _apigw_in_region,
    "cfs": _cfs_in_region,
}


def run_region_collectors(regions, sid, sk, token, max_workers=8):
    """对 (collector, region) 笛卡尔积并发执行，返回 {key: [per-region result]}。"""
    results = {k: [] for k in _REGION_COLLECTORS}
    tasks = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futmap = {}
        for key, fn in _REGION_COLLECTORS.items():
            for region in regions:
                fut = ex.submit(fn, region, sid, sk, token)
                futmap[fut] = key
        for fut in as_completed(futmap):
            key = futmap[fut]
            try:
                results[key].append(fut.result())
            except Exception as e:  # noqa: BLE001
                results[key].append({"region": "?", "error": str(e)})
    return results


# ───────────────────────── 汇总 ─────────────────────────

def aggregate(region_results: dict) -> dict:
    """把 per-region 结果聚合成全局摘要 + 明细。"""
    agg = {}

    # CVM
    cvm_regions = region_results.get("cvm", [])
    all_cvm = []
    for r in cvm_regions:
        for inst in r.get("instances", []):
            inst["region"] = r["region"]
            all_cvm.append(inst)
    gpu = [c for c in all_cvm if c.get("is_gpu")]
    running = [c for c in all_cvm if c.get("state") == "RUNNING"]
    # CVM 状态分布（运行/关机/到期/隔离/其它），供"状态分布"维度展示
    state_dist = {}
    for c in all_cvm:
        st = (c.get("state") or "UNKNOWN").upper()
        state_dist[st] = state_dist.get(st, 0) + 1
    # 包年包月到期日清单（纯事实陈列：到期日 + 续费方式都是属性，不分级、不催续费）。
    # 只挑有 deadline 的 PREPAID 实例，按到期日升序，方便用户一眼看到最近到期的机器。
    # 同时带上 auto_renew(是否已开自动续费)，支持"快到期且未开自动续费"的纯属性筛选。
    prepaid_deadlines = sorted(
        ({"instance_id": c.get("instance_id", ""), "name": c.get("name", ""),
          "region": c.get("region", ""), "deadline": c.get("deadline", ""),
          "renew_flag": c.get("renew_flag", ""), "auto_renew": bool(c.get("auto_renew")),
          "days_left": _days_left(c.get("deadline", ""))} for c in all_cvm if c.get("deadline")),
        key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0),
    )
    # 未开自动续费的包年包月实例数（纯计数，供 meta 展示；不打风险等级）。
    prepaid_no_autorenew = sum(1 for c in prepaid_deadlines if not c["auto_renew"])
    # 全量标签覆盖率（纯统计：N 个资源里 M 个没打任何标签，覆盖率 X%）。
    # 与云巡检的"标签合规判定(head/business 必填→风险分级)"刻意区分：这里不判必填、不给风险，
    # 且覆盖全量资源（含零费用），补云巡检 billing 维度"仅上月有消费资源"的盲区。
    cvm_total = len(all_cvm)
    cvm_tagged = sum(1 for c in all_cvm if c.get("tagged"))
    tag_coverage = {
        "scanned": cvm_total,
        "tagged": cvm_tagged,
        "untagged": cvm_total - cvm_tagged,
        "rate": round(cvm_tagged / cvm_total * 100, 1) if cvm_total else None,
        "untagged_samples": [
            {"instance_id": c.get("instance_id", ""), "name": c.get("name", ""), "region": c.get("region", "")}
            for c in all_cvm if not c.get("tagged")
        ][:50],
    }
    agg["cvm"] = {
        "total": cvm_total, "running": len(running), "gpu_total": len(gpu),
        "state_dist": state_dist,
        "prepaid_deadlines": prepaid_deadlines,
        "prepaid_no_autorenew": prepaid_no_autorenew,
        "tag_coverage": tag_coverage,
        "by_region": {r["region"]: len(r.get("instances", [])) for r in cvm_regions if r.get("instances") or not r.get("error")},
        "gpu_instances": gpu,
        "instances": all_cvm,
        "errors": [{"region": r["region"], "error": r["error"]} for r in cvm_regions if r.get("error")],
    }

    # TKE
    tke_regions = region_results.get("tke", [])
    all_clusters = []
    node_spec_total = {}
    node_total = 0
    for r in tke_regions:
        for c in r.get("clusters", []):
            c["region"] = r["region"]
            all_clusters.append(c)
            node_total += c.get("node_total", 0)
            for spec, n in (c.get("node_specs") or {}).items():
                node_spec_total[spec] = node_spec_total.get(spec, 0) + n
    agg["tke"] = {
        "cluster_total": len(all_clusters), "node_total": node_total,
        "node_specs": node_spec_total, "clusters": all_clusters,
        "errors": [{"region": r["region"], "error": r["error"]} for r in tke_regions if r.get("error")],
    }

    # 计数型资源：cbs/clb/vpc/eip/sg/cdb/redis/lighthouse
    def _sum(key, field="count"):
        rows = region_results.get(key, [])
        total = sum(x.get(field, 0) for x in rows)
        by_region = {x["region"]: x.get(field, 0) for x in rows if x.get(field, 0)}
        return {"total": total, "by_region": by_region,
                "errors": [{"region": x["region"], "error": x["error"]} for x in rows if x.get("error")]}

    agg["cbs"] = _sum("cbs"); agg["cbs"]["total_gb"] = sum(x.get("total_gb", 0) for x in region_results.get("cbs", []))
    agg["clb"] = _sum("clb")
    agg["vpc"] = {"total": sum(x.get("vpc", 0) for x in region_results.get("vpc", [])),
                  "subnet_total": sum(x.get("subnet", 0) for x in region_results.get("vpc", [])),
                  "by_region": {x["region"]: x.get("vpc", 0) for x in region_results.get("vpc", []) if x.get("vpc", 0)},
                  "errors": [{"region": x["region"], "error": x["error"]} for x in region_results.get("vpc", []) if x.get("error")]}
    eip = region_results.get("eip", [])
    agg["eip"] = {"total": sum(x.get("count", 0) for x in eip), "unbound": sum(x.get("unbound", 0) for x in eip),
                  "by_region": {x["region"]: x.get("count", 0) for x in eip if x.get("count", 0)},
                  "errors": [{"region": x["region"], "error": x["error"]} for x in eip if x.get("error")]}
    agg["sg"] = _sum("sg")
    agg["cdb"] = _sum("cdb")
    agg["redis"] = _sum("redis")
    agg["lighthouse"] = _sum("lighthouse")
    # ── 新增计数型资源 ──
    for key in ("mongodb", "postgres", "cynosdb", "ckafka", "es",
                "scf", "tcr", "nat", "vpn", "apigw", "cfs"):
        agg[key] = _sum(key)

    # ── 跨资源到期清单（统一视角）──────────────────────────────
    # 把所有包年包月资源的到期项汇成一张表，每项带 resource_type，按到期日升序。
    # 纯事实陈列：到期日 + 续费方式都是属性，不分级、不催续费。
    # 支持直接回答"哪些资源快到期 且 没开自动续费"——跨资源一眼看全。
    expiry_items = []
    # CVM：复用已构建的 prepaid_deadlines，补 resource_type
    for c in agg.get("cvm", {}).get("prepaid_deadlines", []) or []:
        item = dict(c)
        item.setdefault("resource_type", "cvm")
        item.setdefault("renew_state", "auto" if c.get("auto_renew") else "manual")
        expiry_items.append(item)
    # 其余资源：从各 region 结果的 expiry_items 收集
    for key in ("cdb", "redis", "cbs", "lighthouse", "mongodb", "postgres", "cynosdb"):
        for r in region_results.get(key, []):
            for it in r.get("expiry_items", []) or []:
                expiry_items.append(it)
    # 按剩余天数升序（None 排最后）
    expiry_items.sort(key=lambda x: (x.get("days_left") is None,
                                     x.get("days_left") if x.get("days_left") is not None else 0))
    no_autorenew = sum(1 for it in expiry_items if not it.get("auto_renew"))
    # 按资源类型分组计数（供概览）
    by_type = {}
    for it in expiry_items:
        rt = it.get("resource_type", "other")
        by_type[rt] = by_type.get(rt, 0) + 1
    agg["expiry_inventory"] = {
        "total": len(expiry_items),
        "no_autorenew": no_autorenew,
        "by_type": by_type,
        "items": expiry_items,
    }
    return agg


# ───────────────────────── 主流程 ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="腾讯云资源清点（只读）")
    ap.add_argument("--cloud", default="tencent")
    ap.add_argument("--project", default="")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--regions", default="", help="逗号分隔指定地域，缺省自动全量")
    ap.add_argument("--max-workers", type=int, default=8)
    args = ap.parse_args()

    t0 = time.time()
    creds = load_credentials(args.cloud, args.project)
    if not creds["secret_id"] or not creds["secret_key"]:
        print(json.dumps({"status": "need_credentials",
                          "cloud": args.cloud, "project": args.project,
                          "lang": args.lang}, ensure_ascii=False))
        return 0

    sid, sk, token = creds["secret_id"], creds["secret_key"], creds["token"]
    if token:
        set_process_token(token)

    acct = detect_account(sid, sk, token)
    if not acct.get("ok"):
        print(json.dumps({"status": "auth_failed", "error": acct.get("error", ""),
                          "cloud": args.cloud, "project": args.project}, ensure_ascii=False))
        return 0
    uin, app_id = acct["uin"], acct["app_id"]

    regions = ([r.strip() for r in args.regions.split(",") if r.strip()]
               if args.regions else list_regions(sid, sk, token))

    # 全局采集（region 无关）
    subaccounts = collect_subaccounts(sid, sk, token)
    cos = collect_cos_buckets(sid, sk, app_id, token)
    ssl_certs = collect_ssl_certs(sid, sk, token)

    # 区域并发采集
    region_results = run_region_collectors(regions, sid, sk, token, max_workers=args.max_workers)
    agg = aggregate(region_results)

    result = {
        "status": "ok",
        "account": {
            "cloud": args.cloud, "project": args.project,
            "uin": uin, "app_id": app_id, "region_default": creds["region"],
        },
        "scanned_regions": regions,
        "elapsed_s": round(time.time() - t0, 1),
        "subaccounts": subaccounts,
        "cos": cos,
        "ssl_certs": ssl_certs,
        "resources": agg,
    }

    # 落盘
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        out_path = os.path.join(args.out_dir, "resources.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        result["_out_path"] = out_path

    # stdout 只 print summary（不含明细，避免 context 膨胀）
    summary = {
        "status": "ok",
        "uin": uin, "project": args.project,
        "scanned_regions": len(regions),
        "elapsed_s": result["elapsed_s"],
        "out_path": result.get("_out_path", ""),
        "counts": {
            "sub_users": len(subaccounts.get("sub_users", [])),
            "collaborators": len(subaccounts.get("collaborators", [])),
            "cos_buckets": len(cos.get("buckets", [])),
            "cvm_total": agg["cvm"]["total"], "cvm_running": agg["cvm"]["running"],
            "gpu_cvm": agg["cvm"]["gpu_total"],
            "tke_clusters": agg["tke"]["cluster_total"], "tke_nodes": agg["tke"]["node_total"],
            "cbs": agg["cbs"]["total"], "clb": agg["clb"]["total"],
            "vpc": agg["vpc"]["total"], "eip": agg["eip"]["total"],
            "cdb": agg["cdb"]["total"], "redis": agg["redis"]["total"],
            "lighthouse": agg["lighthouse"]["total"], "sg": agg["sg"]["total"],
            # 新增资源计数
            "mongodb": agg["mongodb"]["total"], "postgres": agg["postgres"]["total"],
            "cynosdb": agg["cynosdb"]["total"], "ckafka": agg["ckafka"]["total"],
            "es": agg["es"]["total"], "scf": agg["scf"]["total"], "tcr": agg["tcr"]["total"],
            "nat": agg["nat"]["total"], "vpn": agg["vpn"]["total"],
            "apigw": agg["apigw"]["total"], "cfs": agg["cfs"]["total"],
            "ssl_certs": ssl_certs.get("total", 0),
            # 清点增量维度（纯事实/纯统计）
            "cvm_prepaid_expiring": len(agg["cvm"].get("prepaid_deadlines", [])),
            "cvm_prepaid_no_autorenew": agg["cvm"].get("prepaid_no_autorenew", 0),
            "cvm_tag_rate": agg["cvm"].get("tag_coverage", {}).get("rate"),
            "cvm_untagged": agg["cvm"].get("tag_coverage", {}).get("untagged", 0),
            # 跨资源到期清单（CVM/CDB/Redis/CBS/Lighthouse/MongoDB/PG/CynosDB）
            "expiry_total": agg.get("expiry_inventory", {}).get("total", 0),
            "expiry_no_autorenew": agg.get("expiry_inventory", {}).get("no_autorenew", 0),
        },
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
