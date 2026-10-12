#!/usr/bin/env python3
"""
华为云账单数据一键准备脚本（Step 0~2 合并）

职责：
1. 凭证解析：经 build_credential_env.resolve('huawei', project) 从 SSM 取 AK/SK（KMS 服务端解密）
2. 自动获取 domain_id / domain_name（调用 IAM v3 listAuthDomains，等价于腾讯云的 GetUserAppId）
3. 并发拉取分析月 + 对照月（环比）的：
   - 按云服务类型汇总（ShowCustomerMonthlySum）
   - 按企业项目汇总（同一接口加 enterprise_project_id 维度筛选 + 拉项目列表）
   - 资源消费记录（ListCustomerselfResourceRecords）
4. 输出 JSON：包含 `report_ready` 字段，下游 extract / render 直接消费

凭证缺失时返回 status=need_credentials + 友好引导 prompt（agent 必须原文返回给用户）

用法：
    python3 prepare_billing_data.py --month 202605
    python3 prepare_billing_data.py --month 202605 --auto-fetch
    python3 prepare_billing_data.py --month 202605 --refresh   # 清空缓存
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让本脚本既能 import scripts.sign（同目录），也能被外部 -m 调用
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from sign import sign_and_call_get  # noqa: E402

# ── 并发凭证隔离（2026-06-13，与 cloud-billing-pro/cloud-costopt-pro 同方案）──────────
# 多账号并发时各 specialist 若读同一裸名 HUAWEICLOUD_SDK_AK/SK，会 last-write-wins 互相覆盖。
# 凭证唯一来源 = SSM：接 --cloud/--project，进程内调 build_credential_env.resolve('huawei', project)
# 从 SSM 取该项目专属凭证（KMS 服务端解密），绝不读裸名 env。
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(str(Path(__file__).parent.resolve()), "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)


def load_env_credentials(cloud: str = "huawei", project: Optional[str] = None) -> Dict[str, str]:
    """进程内解析某 (cloud, project) 的华为云裸凭证 dict（不污染 os.environ）。

    凭证唯一来源 = SSM（KMS 加密）。build_credential_env.resolve() 内部只走 SSM，
    没有 env 前缀路径、也没有裸名兜底。本函数因此**强制要求 project 非空**：
      - project 非空 → resolve(cloud, project) 从 SSM 取密钥（KMS 服务端解密）。
      - project 为空 → 直接返回空凭证（上层走 need_credentials 引导）。绝不读裸名 env。

    注意：BUDGET（月度预算）不是机密，仍允许从 env 读，方便部署侧灵活配置预算线。
    """
    if project:
        budget = (os.environ.get("HUAWEICLOUD_%s_MONTHLY_BUDGET" % project, "")
                  or os.environ.get("HUAWEICLOUD_MONTHLY_BUDGET", ""))
    else:
        budget = os.environ.get("HUAWEICLOUD_MONTHLY_BUDGET", "")

    empty = {"ak": "", "sk": "", "bss_endpoint": "", "budget": budget.strip()}
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
        "ak": env.get("HUAWEICLOUD_SDK_AK", "").strip(),
        "sk": env.get("HUAWEICLOUD_SDK_SK", "").strip(),
        "bss_endpoint": env.get("HUAWEICLOUD_BSS_ENDPOINT", "").strip(),
        "domain_id": env.get("HUAWEICLOUD_SDK_DOMAIN_ID", "").strip(),
        "domain_name": env.get("HUAWEICLOUD_SDK_DOMAIN_NAME", "").strip(),
        "security_token": env.get("HUAWEICLOUD_SECURITY_TOKEN", "").strip(),
        "credential_expires_at": env.get("HUAWEICLOUD_CREDENTIAL_EXPIRES_AT", "").strip(),
        "budget": budget.strip(),
    }

BSS_HOST = "bss.myhuaweicloud.com"
BSS_INTL_HOST = "bss-intl.myhuaweicloud.com"
IAM_HOST = "iam.myhuaweicloud.com"
EPS_HOST = "eps.myhuaweicloud.com"


# -------------------------- Workspace --------------------------

def get_workspace() -> str:
    return os.environ.get("WORKBUDDY_WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cache_dir_for(domain_id: str, yyyymm: str) -> Path:
    return Path(get_workspace()) / "cache" / "billing-huawei" / domain_id / yyyymm


# -------------------------- Credentials --------------------------

def _build_need_credentials_prompt(lang: str = "zh") -> str:
    """凭证缺失友好引导（SSM+KMS 单一来源模式）。lang="en" 时返回确定性英文版（非机翻）。"""
    if lang == "en":
        return _need_credentials_prompt_en()
    return """没能从 SSM 取到这个华为云账号的凭证，账单分析没法启动。

## ⚠️ 凭证未就绪：SSM 里没有对应密钥

本专家团**强制以腾讯云 SSM（凭据管理，KMS 加密）为唯一凭证来源**——华为云 AK/SK 也不进环境变量，而是由管理员加密存放在 SSM，运行时按 `(cloud, project)` 动态拉取。

### 一、运行端只需配一把 runner 密钥（一次性）

运行 WorkBuddy 的机器上，环境变量里**只放腾讯云 runner（bootstrap）密钥**，它只用来读 SSM（华为云凭证也存在腾讯云 SSM 里统一托管）：

```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="<runner 密钥 SecretId>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<runner 密钥 SecretKey>"
# 可选，SSM 所在地域，默认 ap-guangzhou
export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"
# 配好 runner 密钥即自动启用 SSM；CLOUDOPS_SSM_ENABLED 是可选急停开关，仅在想临时关闭 SSM（不删 key）时设为 false/0/no/off。
```

> runner 密钥**只需 SSM 读取一类权限**（`ssm:GetSecretValue` / `ssm:ListSecrets`），**不需要任何 `kms:*` 权限**——解密由 SSM 服务端用服务角色（`SSM_QCSRole`，开通 SSM 时一次性授权）完成，明文经 TLS 返回，不能直接签发业务 API。

### 二、管理员把华为云凭证存入 SSM

到 [腾讯云 SSM 控制台](https://console.cloud.tencent.com/ssm) 新建**用户凭据**，名称按约定 `cloudops/huawei/{project}`，凭据内容是一段 JSON：

```
凭据名称: cloudops/huawei/TSP
凭据内容(SecretString):
{
  "ak": "your-access-key-id",
  "sk": "your-secret-access-key",
  "bss_endpoint": "bss.myhuaweicloud.com"
}
```

如果你拿到的是临时凭证，额外放入 token；脚本会把它作为 `X-Security-Token` 并纳入签名：

```json
{
  "ak": "temporary-access",
  "sk": "temporary-secret",
  "securitytoken": "temporary-security-token",
  "expires_at": "2026-06-24T12:00:00.000Z",
  "bss_endpoint": "bss.myhuaweicloud.com",
  "domain_id": "optional-domain-id"
}
```

> 华为云 AK/SK 到 [华为云「我的凭证 - 访问密钥」](https://console.huaweicloud.com/iam/?locale=zh-cn#/mine/accessKey) 创建，推荐 IAM 子账号 AK + `BSS Administrator`（或只读 `billing:bill:view`）。国际站账号把 `bss_endpoint` 填 `bss-intl.myhuaweicloud.com`。创建 SSM 凭据时选 **KMS 加密**，密钥以密文落库。

domain_id（账号 ID）/ 账号名称优先读 SSM；未填写时自动通过 IAM `ListAuthDomains` 接口识别。

**一旦凭据就位，华为云账单分析专家会输出**

| 维度 | 内容 |
|---|---|
| 💰 总费用 | 月度总额 + 环比 + 预算消耗率 |
| 📦 按云服务类型 | 全部产品费用 + 环比 + 占比 |
| 🏢 按企业项目 | 各企业项目费用拆解 |
| 🔥 Top N 资源 | 最贵资源 + 服务类型 + 环比 |
| ⚖️ 环比变化 | 增长 / 降低 / 新增 / 已下线四类完整列表 |
| 📈 近 3 月趋势 | Top 产品趋势线 |

**请确认下一步：**
1. runner 密钥和 SSM 凭据都已就绪？— 告诉我"配好了"，立即启动账单分析
2. 需要我详细说明**如何在 SSM 控制台建凭据 / 给 runner 授权**？
3. 先看一份样例报告了解输出格式？
"""


def _need_credentials_prompt_en() -> str:
    """English version of the SSM-only credentials-missing guidance. Deterministic, NOT machine-translated."""
    return """Couldn't fetch this Huawei Cloud account's credentials from SSM — billing analysis can't start.

## ⚠️ Credentials not ready: no matching secret in SSM

This expert team uses **Tencent Cloud SSM (Secrets Manager, KMS-encrypted) as the SOLE credential source** — Huawei Cloud AK/SK never live in environment variables either. An admin stores them encrypted in SSM, and they are pulled at runtime by `(cloud, project)`.

### 1. The runtime host only needs ONE runner key (one-time)

On the machine running WorkBuddy, the environment holds **only the Tencent Cloud runner (bootstrap) key**, used solely to read SSM (Huawei Cloud credentials are also centrally managed in Tencent Cloud SSM):

```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="<runner key SecretId>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<runner key SecretKey>"
# Optional, SSM region, default ap-guangzhou
export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"
# Having the runner key enables SSM automatically; CLOUDOPS_SSM_ENABLED is an optional kill-switch — set it to false/0/no/off only to disable SSM.
```

> The runner key needs **only SSM read perms** (`ssm:GetSecretValue` / `ssm:ListSecrets`) and **no `kms:*` permission** — SSM decrypts server-side under its own service role (`SSM_QCSRole`, authorized once when SSM is activated) and returns plaintext over TLS. It cannot sign business APIs directly.

### 2. The admin stores Huawei Cloud credentials in SSM

In the [Tencent Cloud SSM console](https://console.cloud.tencent.com/ssm), create a **user secret** named by convention `cloudops/huawei/{project}`, whose content is a JSON blob:

```
Secret name: cloudops/huawei/TSP
SecretString:
{
  "ak": "your-access-key-id",
  "sk": "your-secret-access-key",
  "bss_endpoint": "bss.myhuaweicloud.com"
}
```

For temporary credentials, also store the token; the script sends it as `X-Security-Token` and includes it in SignedHeaders:

```json
{
  "ak": "temporary-access",
  "sk": "temporary-secret",
  "securitytoken": "temporary-security-token",
  "expires_at": "2026-06-24T12:00:00.000Z",
  "bss_endpoint": "bss.myhuaweicloud.com",
  "domain_id": "optional-domain-id"
}
```

> Create the Huawei AK/SK at [Huawei Cloud "My Credentials - Access Keys"](https://console.huaweicloud.com/iam/?locale=en-us#/mine/accessKey); recommended IAM sub-account AK + `BSS Administrator` (or read-only `billing:bill:view`). For international-site accounts set `bss_endpoint` to `bss-intl.myhuaweicloud.com`. Choose **KMS encryption** when creating the SSM secret so the key is stored as ciphertext.

domain_id (account ID) / account name are read from SSM first; if omitted, they are auto-detected via the IAM `ListAuthDomains` API.

**Once the secret is in place, the Huawei Cloud billing analyst will output**

| Dimension | Content |
|---|---|
| 💰 Total cost | Monthly total + MoM + budget consumption rate |
| 📦 By cloud service type | All product costs + MoM + share |
| 🏢 By enterprise project | Cost breakdown per enterprise project |
| 🔥 Top N resources | Most expensive resources + service type + MoM |
| ⚖️ MoM changes | Four complete lists: increased / decreased / new / retired |
| 📈 Last 3-month trend | Trend lines for top products |

**Please confirm the next step:**
1. Runner key and SSM secret both ready? — tell me "done" and I'll start the billing analysis immediately
2. Want me to walk through creating the secret in SSM / granting the runner key?
3. Want to see a sample report first to understand the output format?
"""


def resolve_credentials(lang: str = "zh", cloud: str = "huawei",
                        project: Optional[str] = None) -> Dict[str, Any]:
    """从环境变量读取华为云 AK/SK（进程内隔离），并通过 IAM 自动派生 domain_id/domain_name。

    cloud/project：多账号并发隔离键 + SSM 定位键。project 非空 → resolve(cloud, project) 从 SSM 取（KMS 服务端解密）；
       project 为空 → 返回空凭证走 need_credentials 引导。绝不读裸名 env。由 main() 从 --cloud/--project 透传。
    lang 决定缺失态 prompt 语种（zh/en），由 main() 从 --lang 透传。
    """
    env_cred = load_env_credentials(cloud=cloud, project=project)
    ak = env_cred["ak"]
    sk = env_cred["sk"]
    if not ak or not sk:
        return {
            "status": "need_credentials",
            "prompt": _build_need_credentials_prompt(lang),
        }

    bss_endpoint = (env_cred.get("bss_endpoint") or BSS_HOST)
    security_token = (env_cred.get("security_token") or "").strip()

    # 优先使用 SSM 中显式提供的 domain_id/domain_name；未提供时再通过 IAM v3
    # listAuthDomains 自动派生。临时凭证场景会把 X-Security-Token 一并签入请求。
    preset_domain_id = (env_cred.get("domain_id") or "").strip()
    preset_domain_name = (env_cred.get("domain_name") or "").strip()
    if preset_domain_id:
        d = {"id": preset_domain_id, "name": preset_domain_name or preset_domain_id}
    else:
        iam_resp = sign_and_call_get(
            host=IAM_HOST,
            path="/v3/auth/domains",
            params={},
            ak=ak,
            sk=sk,
            security_token=security_token,
        )
        if "_error" in iam_resp:
            msg = iam_resp["_error"].get("Message", "")
            suffix = (
                "\n\n> ❌ IAM call failed (possibly wrong AK/SK, missing X-Security-Token for temporary credentials, clock skew, or insufficient permissions): %s" % msg
                if lang == "en"
                else "\n\n> ❌ IAM 调用失败（可能是 AK/SK 错误、临时凭证缺少 X-Security-Token、机器时间偏差或权限不足）：%s" % msg
            )
            return {
                "status": "need_credentials",
                "prompt": _build_need_credentials_prompt(lang) + suffix,
            }

        domains = iam_resp.get("domains") or []
        if not domains:
            suffix = (
                "\n\n> ❌ IAM ListAuthDomains returned empty — please confirm the AK/SK belongs to a valid account."
                if lang == "en"
                else "\n\n> ❌ IAM ListAuthDomains 返回为空，请确认 AK/SK 是否归属一个有效账号。"
            )
            return {
                "status": "need_credentials",
                "prompt": _build_need_credentials_prompt(lang) + suffix,
            }

        d = domains[0]
    budget_raw = (env_cred.get("budget") or "").strip()
    try:
        budget = float(budget_raw) if budget_raw else None
    except ValueError:
        budget = None

    return {
        "status": "ok",
        "ak": ak,
        "sk": sk,
        "domain_id": d.get("id"),
        "domain_name": d.get("name"),
        "bss_endpoint": bss_endpoint,
        "security_token": security_token,
        "credential_expires_at": env_cred.get("credential_expires_at", ""),
        "budget": budget,
        "active_cloud": cloud,          # 供落盘命名直接取用（huawei）
        "active_project": project or "",
    }


# -------------------------- API Calls --------------------------

def _bill_cycle(yyyymm: str) -> str:
    """yyyymm（202605）→ bill_cycle（2026-05）。"""
    if "-" in yyyymm:
        return yyyymm
    return "%s-%s" % (yyyymm[:4], yyyymm[4:6])


def fetch_monthly_sum(creds: Dict[str, Any], yyyymm: str) -> Dict[str, Any]:
    """ShowCustomerMonthlySum：按云服务类型汇总。"""
    items: List[Dict[str, Any]] = []
    offset = 0
    limit = 1000
    total_count = 0
    currency = "CNY"
    while True:
        resp = sign_and_call_get(
            host=creds["bss_endpoint"],
            path="/v2/bills/customer-bills/monthly-sum",
            params={
                "bill_cycle": _bill_cycle(yyyymm),
                "method": "all",
                "offset": offset,
                "limit": limit,
            },
            ak=creds["ak"],
            sk=creds["sk"],
            security_token=creds.get("security_token", ""),
        )
        if "_error" in resp:
            return {"status": "error", "message": resp["_error"], "items": items}
        currency = resp.get("currency") or currency
        page = resp.get("bill_sums") or []
        items.extend(page)
        total = resp.get("total_count") or 0
        total_count = total_count or total
        offset += len(page)
        if not page or offset >= total:
            break
    return {"status": "ok", "currency": currency, "items": items, "total_count": total_count or len(items)}


def fetch_enterprise_projects(creds: Dict[str, Any]) -> Dict[str, str]:
    """ListEnterpriseProject（EPS 服务）：返回 {id: name} 映射表。

    轻量接口，一次返回全部企业项目，不分页大量数据。失败时返回空 map（降级，
    渲染端回退显示 id 前缀），不中止整体流程。
    """
    id_name: Dict[str, str] = {}
    try:
        resp = sign_and_call_get(
            host=EPS_HOST,
            path="/v1.0/enterprise-projects",
            params={"limit": 1000, "offset": 0},
            ak=creds["ak"],
            sk=creds["sk"],
            security_token=creds.get("security_token", ""),
        )
    except Exception:
        return id_name
    if "_error" in resp:
        return id_name
    for ep in resp.get("enterprise_projects") or []:
        eid = ep.get("id") or ""
        name = ep.get("name") or ""
        if eid:
            id_name[eid] = name
    return id_name


def fetch_resource_records(creds: Dict[str, Any], yyyymm: str) -> Dict[str, Any]:
    """ListCustomerselfResourceRecords：资源消费记录。

    全口径(method=all)在大账号下可返回数十万条明细。为避免把百万级明细
    全量驻留内存/落盘，这里分页拉取并按 resource_id 做等价聚合；下游
    aggregate_by_resource 原本也是按 resource_id 聚合，因此看板口径保持一致。
    """
    limit = 1000

    def _fetch_page(offset: int) -> Dict[str, Any]:
        return sign_and_call_get(
            host=creds["bss_endpoint"],
            path="/v2/bills/customer-bills/res-fee-records",
            params={
                "cycle": _bill_cycle(yyyymm),
                "method": "all",
                "offset": offset,
                "limit": limit,
            },
            ak=creds["ak"],
            sk=creds["sk"],
            security_token=creds.get("security_token", ""),
        )

    bucket: Dict[str, Dict[str, Any]] = {}
    raw_count = 0

    def _merge(page: List[Dict[str, Any]]) -> None:
        nonlocal raw_count
        raw_count += len(page)
        for r in page:
            rid = r.get("resource_id") or r.get("res_instance_id") or "UNKNOWN"
            b = bucket.setdefault(
                rid,
                {
                    "resource_id": rid,
                    "resource_name": r.get("resource_name") or "",
                    "cloud_service_type_code": r.get("cloud_service_type_code") or r.get("cloud_service_type") or "",
                    "cloud_service_type_name": r.get("cloud_service_type_name") or "",
                    "region": r.get("region") or r.get("region_code") or "",
                    "region_name": r.get("region_name") or "",
                    "enterprise_project_id": r.get("enterprise_project_id") or "",
                    "enterprise_project_name": r.get("enterprise_project_name") or "",
                    "amount": 0.0,
                    "official_amount": 0.0,
                    "credit_amount": 0.0,
                    "cash_amount": 0.0,
                    "coupon_amount": 0.0,
                },
            )
            b["amount"] += _to_float(r.get("consume_amount") or r.get("amount"))
            b["official_amount"] += _to_float(r.get("official_amount"))
            b["credit_amount"] += _to_float(r.get("credit_amount"))
            b["cash_amount"] += _to_float(r.get("cash_amount"))
            b["coupon_amount"] += _to_float(r.get("coupon_amount"))

    first = _fetch_page(0)
    if "_error" in first:
        return {"status": "error", "message": first["_error"], "items": []}
    total_count = first.get("total_count") or 0
    _merge(first.get("fee_records") or [])

    offsets = list(range(limit, total_count, limit))
    if offsets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(_fetch_page, off) for off in offsets]
            for fut in concurrent.futures.as_completed(futures):
                resp = fut.result()
                if "_error" in resp:
                    return {"status": "error", "message": resp["_error"], "items": list(bucket.values())}
                _merge(resp.get("fee_records") or [])

    items = sorted(bucket.values(), key=lambda x: -x.get("amount", 0.0))
    return {"status": "ok", "items": items, "total_count": total_count or raw_count, "raw_record_count": raw_count, "aggregated_by_resource": True}


def fetch_ep_prev_cost(creds: Dict[str, Any], yyyymm: str, max_workers: int = 8) -> Dict[str, Any]:
    """轻量拉取某月 res-fee-records，仅按 enterprise_project_id 聚合 cost。

    与当月版 fetch_resource_records 共用**完全相同的并发分页骨架**
    （path=/v2/bills/customer-bills/res-fee-records, method=all, limit=1000,
    ThreadPool max_workers=8），但聚合粒度是企业项目（约几十个 key）：
    **每个 future 的 resp 在 _accumulate(累加 ep[id]+=cost) 后立即 del page/resp**，
    8 并发瞬时只持有 8×1000 条 transient（几 MB），聚合后只剩 ~58 key 小 dict，
    既不 OOM、又能像当月版一样在 ~30-60s 拉完 37 万条（上轮串行 limit=300 慢到 10min 超时）。

    max_workers 可调：默认 8，遇限流(429)由调用方传 5/4 降速。容错：单页失败记 partial 不中止。
    """
    limit = 1000

    def _fetch_page(offset: int) -> Dict[str, Any]:
        return sign_and_call_get(
            host=creds["bss_endpoint"],
            path="/v2/bills/customer-bills/res-fee-records",
            params={
                "cycle": _bill_cycle(yyyymm),
                "method": "all",
                "offset": offset,
                "limit": limit,
            },
            ak=creds["ak"],
            sk=creds["sk"],
            security_token=creds.get("security_token", ""),
        )

    cost: Dict[str, float] = {}
    raw_count = 0
    _lock = __import__("threading").Lock()

    def _accumulate(page: List[Dict[str, Any]]) -> None:
        """只累加 ep→cost，逐条读完即弃（不外存明细 list）。线程安全。"""
        nonlocal raw_count
        local: Dict[str, float] = {}
        n = 0
        for r in page:
            ep = r.get("enterprise_project_id") or "未归集"
            local[ep] = local.get(ep, 0.0) + _to_float(r.get("consume_amount") or r.get("amount"))
            n += 1
        with _lock:
            raw_count += n
            for k, v in local.items():
                cost[k] = cost.get(k, 0.0) + v

    # 与 fetch_resource_records 第455-469行同款并发模式：先拉第0页拿 total_count，
    # 再 offsets=range(1000,total,1000) 用 executor 并发 submit、as_completed 收集。
    first = _fetch_page(0)
    if "_error" in first:
        return {"status": "error", "message": first["_error"], "cost": {}}
    total_count = first.get("total_count") or 0
    _accumulate(first.get("fee_records") or [])
    del first

    partial = False
    last_err = None
    offsets = list(range(limit, total_count, limit))
    if offsets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_fetch_page, off) for off in offsets]
            for fut in concurrent.futures.as_completed(futures):
                resp = fut.result()
                if "_error" in resp:
                    # 单页失败：记 partial，继续收其余页（不整体中止）
                    partial = True
                    last_err = resp["_error"]
                    del resp
                    continue
                page = resp.get("fee_records") or []
                _accumulate(page)
                del page
                del resp

    status = "partial" if partial else "ok"
    out = {"status": status, "cost": cost, "raw_record_count": raw_count,
           "ep_count": len(cost), "total_count": total_count, "workers": max_workers}
    if partial:
        out["message"] = last_err
    return out


# -------------------------- Cache --------------------------

def write_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_cache(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_with_cache(
    creds: Dict[str, Any],
    yyyymm: str,
    api_name: str,
    fetcher,
    skip_cache: bool,
) -> Dict[str, Any]:
    cache_path = cache_dir_for(creds["domain_id"], yyyymm) / ("%s.json" % api_name)
    if not skip_cache:
        cached = read_cache(cache_path)
        if cached is not None:
            cached["_from_cache"] = True
            return cached
    data = fetcher(creds, yyyymm)
    if data.get("status") == "ok" and not skip_cache:
        write_cache(cache_path, data)
    return data


# -------------------------- Aggregation --------------------------

def _to_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def aggregate_by_service(monthly_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按云服务类型聚合月度汇总数据。"""
    bucket: Dict[str, Dict[str, Any]] = {}
    for it in monthly_items:
        code = (
            it.get("cloud_service_type_code")
            or it.get("service_type_code")
            or "UNKNOWN"
        )
        name = it.get("cloud_service_type_name") or it.get("service_type_name") or code
        b = bucket.setdefault(code, {"service_code": code, "service_name": name, "amount": 0.0})
        b["amount"] += _to_float(it.get("total_amount") or it.get("consume_amount"))
    out = sorted(bucket.values(), key=lambda x: -x["amount"])
    return out


def aggregate_by_resource(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按资源 ID 聚合资源消费记录。"""
    bucket: Dict[str, Dict[str, Any]] = {}
    for r in records:
        rid = r.get("resource_id") or r.get("res_instance_id") or "UNKNOWN"
        b = bucket.setdefault(
            rid,
            {
                "resource_id": rid,
                "resource_name": r.get("resource_name") or "",
                "service_code": r.get("cloud_service_type_code") or r.get("service_type_code") or "",
                "service_name": r.get("cloud_service_type_name") or r.get("service_type_name") or "",
                "region_code": r.get("region") or r.get("region_code") or "",
                "enterprise_project_id": r.get("enterprise_project_id") or "",
                "amount": 0.0,
            },
        )
        b["amount"] += _to_float(r.get("consume_amount") or r.get("amount"))
    return sorted(bucket.values(), key=lambda x: -x["amount"])


def compute_resource_diff(
    analysis: List[Dict[str, Any]], comparison: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """对齐分析月与对照月，计算 growth / decline / new / decommissioned / stable。"""
    a_map = {r["resource_id"]: r for r in analysis}
    c_map = {r["resource_id"]: r for r in comparison}
    growth, decline, new_res, offline, stable = [], [], [], [], []
    threshold = 1.0  # 差额阈值（元）

    for rid, ar in a_map.items():
        cr = c_map.get(rid)
        if cr is None:
            new_res.append({**ar, "diff": ar["amount"], "ratio": None})
        else:
            diff = ar["amount"] - cr["amount"]
            ratio = diff / cr["amount"] if cr["amount"] > 0 else None
            entry = {**ar, "prev_amount": cr["amount"], "diff": diff, "ratio": ratio}
            if diff > threshold:
                growth.append(entry)
            elif diff < -threshold:
                decline.append(entry)
            else:
                stable.append(entry)
    for rid, cr in c_map.items():
        if rid not in a_map:
            offline.append({**cr, "prev_amount": cr["amount"], "diff": -cr["amount"], "ratio": None})

    growth.sort(key=lambda x: -x["diff"])
    decline.sort(key=lambda x: x["diff"])
    new_res.sort(key=lambda x: -x["amount"])
    offline.sort(key=lambda x: -x["prev_amount"])
    return {
        "growth": growth,
        "decline": decline,
        "new": new_res,
        "decommissioned": offline,
        "stable_count": len(stable),
    }


# -------------------------- Main --------------------------

def prev_month(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    if m == 1:
        return "%d12" % (y - 1)
    return "%d%02d" % (y, m - 1)


def last_n_months(yyyymm: str, n: int) -> List[str]:
    """返回截至 yyyymm（含）往前推共 n 个月，按时间升序（最早在前）。"""
    out = [yyyymm]
    cur = yyyymm
    for _ in range(n - 1):
        cur = prev_month(cur)
        out.append(cur)
    return list(reversed(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="分析月份，YYYYMM 或 YYYY-MM")
    ap.add_argument("--cloud", default="huawei",
                    help="云厂商（默认 huawei），并发隔离 + 落盘命名用")
    ap.add_argument("--project", default="",
                    help="项目/账号代号；必传，进程内 resolve('huawei', project) 从 SSM 取该账号专属凭证 "
                         "（KMS 服务端解密），规避竞态。省略则无凭证（不读裸名 env，走 need_credentials 引导）")
    ap.add_argument("--auto-fetch", action="store_true", help="缓存缺失时自动拉 API（默认）")
    ap.add_argument("--cache-only", action="store_true", help="只走缓存，不调 API")
    ap.add_argument("--refresh", action="store_true", help="清空缓存重拉")
    ap.add_argument("--trend-months", type=int, default=6,
                    help="近 N 月趋势序列长度（默认 6），用于 trend_months 字段")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"],
                    help="缺失态引导文案语种（zh=中文，en=English），跟随主理人 output_lang")
    args = ap.parse_args()

    month = args.month.replace("-", "")
    if len(month) != 6 or not month.isdigit():
        print(json.dumps({"status": "error", "message": "month must be YYYYMM"}))
        return 1
    comp = prev_month(month)

    creds = resolve_credentials(lang=args.lang, cloud=args.cloud,
                                project=args.project or None)
    if creds.get("status") != "ok":
        print(json.dumps(creds, ensure_ascii=False))
        return 0

    # 当前月或本月 1-4 号查 T-1：跳过缓存
    today = time.localtime()
    cur_yyyymm = "%04d%02d" % (today.tm_year, today.tm_mon)
    skip_cache_analysis = (month == cur_yyyymm) or (
        today.tm_mday <= 4 and month == prev_month(cur_yyyymm)
    )

    if args.refresh:
        for m in (month, comp):
            d = cache_dir_for(creds["domain_id"], m)
            if d.exists():
                for f in d.glob("*.json"):
                    f.unlink()

    auto_fetch_result = {"ok": [], "missed": []}

    def _do(yyyymm: str, skip: bool) -> Dict[str, Any]:
        out = {}
        if args.cache_only:
            out["monthly"] = read_cache(cache_dir_for(creds["domain_id"], yyyymm) / "monthly.json") or {"status": "missing"}
            out["resources"] = read_cache(cache_dir_for(creds["domain_id"], yyyymm) / "resources.json") or {"status": "missing"}
        else:
            out["monthly"] = fetch_with_cache(creds, yyyymm, "monthly", fetch_monthly_sum, skip)
            out["resources"] = fetch_with_cache(creds, yyyymm, "resources", fetch_resource_records, skip)
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_a = ex.submit(_do, month, skip_cache_analysis)
        f_c = ex.submit(_do, comp, False)
        analysis_raw = f_a.result()
        comparison_raw = f_c.result()

    for tag, payload in (("analysis", analysis_raw), ("comparison", comparison_raw)):
        for api, data in payload.items():
            ok = data.get("status") == "ok"
            (auto_fetch_result["ok"] if ok else auto_fetch_result["missed"]).append(
                {"month": month if tag == "analysis" else comp, "api": api}
            )

    a_services = aggregate_by_service((analysis_raw["monthly"] or {}).get("items") or [])
    c_services = aggregate_by_service((comparison_raw["monthly"] or {}).get("items") or [])
    a_resources = aggregate_by_resource((analysis_raw["resources"] or {}).get("items") or [])
    c_resources = aggregate_by_resource((comparison_raw["resources"] or {}).get("items") or [])

    a_total = sum(s["amount"] for s in a_services)
    c_total = sum(s["amount"] for s in c_services)

    # 加环比
    c_map = {s["service_code"]: s["amount"] for s in c_services}
    for s in a_services:
        prev_amt = c_map.get(s["service_code"], 0.0)
        s["prev_amount"] = prev_amt
        s["diff"] = s["amount"] - prev_amt
        s["ratio"] = (s["diff"] / prev_amt) if prev_amt > 0 else None

    resource_diff = compute_resource_diff(a_resources, c_resources)

    # 按企业项目维度（从资源记录里聚合）
    def _by_ep(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bucket: Dict[str, Dict[str, Any]] = {}
        for r in records:
            ep = r.get("enterprise_project_id") or "未归集"
            b = bucket.setdefault(ep, {"enterprise_project_id": ep, "amount": 0.0})
            b["amount"] += r["amount"]
        return sorted(bucket.values(), key=lambda x: -x["amount"])

    a_eps = _by_ep(a_resources)

    # ── 上月企业项目费用（环比用）────────────────────────────────────────────
    # 上月(comp) 缓存通常只有 monthly.json（按服务汇总），没有 EP 维度；这里轻量补拉
    # 上月 res-fee-records 但只聚合 ep→cost 小 dict（不留明细，避 OOM）。
    # 拉取失败/部分失败 → 降级用账户整体环比估算上月 EP 费用，并标注。
    ep_prev_estimated = False
    ep_prev_cost: Dict[str, float] = {}
    if not args.cache_only:
        # 缓存键 ep_prev_cost.json：上月数据不变，可复用
        ep_prev_cache_path = cache_dir_for(creds["domain_id"], comp) / "ep_prev_cost.json"
        cached_prev = read_cache(ep_prev_cache_path)
        if isinstance(cached_prev, dict) and cached_prev.get("status") == "ok" and isinstance(cached_prev.get("cost"), dict):
            ep_prev_cost = {k: _to_float(v) for k, v in cached_prev["cost"].items()}
        else:
            prev_resp = fetch_ep_prev_cost(creds, comp)
            if prev_resp.get("status") in ("ok", "partial") and prev_resp.get("cost"):
                ep_prev_cost = {k: _to_float(v) for k, v in prev_resp["cost"].items()}
                if prev_resp.get("status") == "ok":
                    write_cache(ep_prev_cache_path, prev_resp)
    if not ep_prev_cost:
        # 降级：上月费用 = 当月EP费用 ÷ (1 + 账户整体环比)；整体环比缺失时用 0.116 兜底
        overall_ratio = None
        if c_total > 0:
            overall_ratio = (a_total - c_total) / c_total
        if overall_ratio is None:
            overall_ratio = 0.116
        ep_prev_estimated = True
        for ep in a_eps:
            ep_prev_cost[ep["enterprise_project_id"]] = ep["amount"] / (1.0 + overall_ratio)

    # merge prev_amount / diff / ratio 到每个当月 EP（口径对齐 by_service 环比）
    for ep in a_eps:
        epid = ep["enterprise_project_id"]
        prev_amt = _to_float(ep_prev_cost.get(epid, 0.0))
        ep["prev_amount"] = prev_amt
        ep["diff"] = ep["amount"] - prev_amt
        ep["ratio"] = ((ep["amount"] - prev_amt) / prev_amt) if prev_amt > 0 else None

    # 企业项目 id→name 映射
    # 注意：ListEnterpriseProject（EPS 服务）需要 EPS 读权限，账单 AK 通常无此权限（返回 401
    # EPS.0003）。res-fee-records 本身已返回 enterprise_project_name，将来如需中文名应从资源
    # 记录聚合时一并带出，无需 EPS。本轮不调 EPS（避免每次跑触发 401），仅做特殊 id + 前缀降级。
    ep_name_map: Dict[str, str] = {
        "0": "默认项目",
        "default": "默认项目",
        "未归集": "未归集",
    }
    # 复用既有缓存映射（若历史轮次曾成功写过）
    cached_map = read_cache(cache_dir_for(creds["domain_id"], month) / "ep_name_map.json")
    if isinstance(cached_map, dict):
        for k, v in cached_map.items():
            if v:
                ep_name_map[k] = v
    # 给每条企业项目带上 name（map 命中优先，否则 id 前8位+…）
    for ep in a_eps:
        epid = ep.get("enterprise_project_id") or ""
        nm = ep_name_map.get(epid)
        if not nm:
            nm = (epid[:8] + "…") if len(epid) > 8 else epid
        ep["enterprise_project_name"] = nm

    # 按地域（region_code）维度
    def _by_region(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bucket: Dict[str, Dict[str, Any]] = {}
        for r in records:
            rg = r.get("region_code") or "未知"
            b = bucket.setdefault(rg, {"region_code": rg, "amount": 0.0})
            b["amount"] += r["amount"]
        return sorted(bucket.values(), key=lambda x: -x["amount"])

    a_regions = _by_region(a_resources)

    # ── 近 N 月趋势序列（trend_months）──────────────────────────────────────
    # 复用本次已拉的 analysis(month)+comparison(comp) 两月 monthly 数据，避免重复 API。
    # 其余历史月走 fetch_with_cache（已有缓存不重拉），串行低并发拉取，规避 BSS QPS。
    n_trend = max(1, min(args.trend_months, 24))
    trend_seq = last_n_months(month, n_trend)  # 升序：最早 → 当月
    monthly_cache: Dict[str, Dict[str, Any]] = {
        month: (analysis_raw["monthly"] or {}),
        comp: (comparison_raw["monthly"] or {}),
    }
    trend_months: List[Dict[str, Any]] = []
    for ym in trend_seq:
        md = monthly_cache.get(ym)
        if md is None:
            if args.cache_only:
                md = read_cache(cache_dir_for(creds["domain_id"], ym) / "monthly.json") or {"status": "missing"}
            else:
                # 历史月用当月跳缓存策略：仅当月需 skip，历史月恒用缓存
                md = fetch_with_cache(creds, ym, "monthly", fetch_monthly_sum, False)
            monthly_cache[ym] = md
        svcs = aggregate_by_service((md or {}).get("items") or [])
        total_m = sum(s["amount"] for s in svcs)
        has_data = (md or {}).get("status") == "ok"
        trend_months.append({
            "month": ym,
            "total": total_m,
            "available": bool(has_data),
        })

    available = bool(a_services and a_resources)

    report_ready = {
        "available": available,
        "domain_id": creds["domain_id"],
        "domain_name": creds["domain_name"],
        "active_cloud": creds.get("active_cloud") or args.cloud,
        "active_project": creds.get("active_project") or args.project,
        "currency": (analysis_raw["monthly"] or {}).get("currency") or "CNY",
        "budget": creds.get("budget"),
        "analysis_month": month,
        "comparison_month": comp,
        "totals": {
            "analysis": a_total,
            "comparison": c_total,
            "diff": a_total - c_total,
            "ratio": ((a_total - c_total) / c_total) if c_total > 0 else None,
        },
        "by_service": a_services,
        "by_enterprise_project": a_eps,
        "ep_prev_estimated": ep_prev_estimated,
        "by_region": a_regions,
        "top_resources": a_resources[:50],
        "resource_diff": resource_diff,
    }

    out = {
        "status": "ok",
        "credentials": {
            "domain_id": creds["domain_id"],
            "domain_name": creds["domain_name"],
            "budget": creds.get("budget"),
        },
        "skip_cache": skip_cache_analysis,
        "trend_months": trend_months,
        "ep_name_map": ep_name_map,
        "auto_fetch_result": auto_fetch_result,
        "cache_inventory": {
            month: list((cache_dir_for(creds["domain_id"], month)).glob("*.json")) and [
                f.name for f in cache_dir_for(creds["domain_id"], month).glob("*.json")
            ],
            comp: list((cache_dir_for(creds["domain_id"], comp)).glob("*.json")) and [
                f.name for f in cache_dir_for(creds["domain_id"], comp).glob("*.json")
            ],
        },
        "report_ready": report_ready,
    }

    # 写盘 prep_output.json，供 extract 脚本使用
    out_path = cache_dir_for(creds["domain_id"], month) / "prep_output.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    out["prep_output_path"] = str(out_path)

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
