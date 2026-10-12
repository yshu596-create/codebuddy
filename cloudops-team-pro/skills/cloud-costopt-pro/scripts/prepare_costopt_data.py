#!/usr/bin/env python3
"""
prepare_costopt_data.py — 一键数据准备脚本（Step 0.3/0.4 + Step 1）

将账单拉取 + 资源发现合并为一次调用，消灭 Step 0/1 碎片 exec。
避免 LLM 逐 exec 调用导致的 sandbox 启动开销（每次 3.2s × N 次）。

用法:
  python3 prepare_costopt_data.py \
    --account-name "一中心管控账户" \
    --work-dir /tmp/costopt_YIZHONG \
    [--bill-month 2026-05]          # 默认上月(T-1)

输出:
  stdout: scan_ready JSON（统计摘要 + 凭证信息）
  文件落盘（work-dir）:
    billing_lookup.json     — {rid: {cost, product, charge, region, name}}
    bill_summary.json       — {products, region_ids, total_cost, bill_month, product_totals}
    resource_discovery.json — {results, errors, unknown_products}
"""

import argparse
import calendar
import json
import os
import re
import sys
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from sign import call_api  # noqa: E402

# ── 并发凭证隔离（2026-06-13，与 cloud-billing-pro 同方案）────────────────────
# 历史 bug：多账号并发时各 specialist 读同一裸名 TENCENTCLOUD_SECRET_ID，last-write-wins
# 互相覆盖 → 多份报告同账号数据。现方案：接 --cloud/--project，进程内经
# build_credential_env.resolve(cloud, project) 从 SSM 取该项目专属凭证（KMS 服务端解密），
# 绝不读裸名 os.environ、也不依赖前缀变量。
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)


def load_env_credentials(cloud="tencent", project=None):
    """进程内解析某 (cloud, project) 的裸凭证 dict（不污染 os.environ）。

    凭证唯一来源 = SSM（KMS 加密）。build_credential_env.resolve() 内部只走 SSM，
    没有 env 前缀路径、也没有裸名兜底。本函数因此**强制要求 project 非空**：
      - project 非空 → resolve(cloud, project) 从 SSM 取密钥（KMS 服务端解密）。
      - project 为空 → 直接返回空凭证（上层走 need_credentials 引导）。绝不读裸名 env。

    跨账号（STS AssumeRole）已**收敛进 resolve()**：若 secret 带 role_arn，resolve()
    已在进程内完成 AssumeRole，返回的 secret_id/secret_key 即临时凭证、并多带一个 token；
    role_arn 被 resolve() 剔除（取不到），本层不再 assume，直接透传 token，杜绝 double-assume。
    """
    empty = {
        "secret_id": "", "secret_key": "", "token": "", "role_arn": "", "external_id": "",
        "duration": "3600", "region": "",
    }
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
        # token：resolve() 做过 AssumeRole 时为临时凭证 Token；直连场景为空。
        "token": env.get("TENCENTCLOUD_TOKEN", "").strip(),
        # role_arn 已被 resolve() 剔除（跨账号已在 resolve 内完成）；恒空，仅保留键兼容。
        "role_arn": "",
        "external_id": env.get("TENCENTCLOUD_ROLE_EXTERNAL_ID", "").strip(),
        "duration": (env.get("TENCENTCLOUD_ROLE_DURATION", "").strip() or "3600"),
        "region": env.get("TENCENTCLOUD_REGION", "").strip(),
    }

# ─── RegionId 数字 → API 地域码映射（内置，不读文件）────────────────
REGION_MAP = {
    1:  "ap-guangzhou",
    4:  "ap-shanghai",
    5:  "ap-hongkong",
    7:  "ap-shanghai-fsi",
    8:  "ap-beijing",
    9:  "ap-singapore",
    11: "ap-shenzhen-fsi",
    15: "na-siliconvalley",
    16: "ap-chengdu",
    17: "eu-frankfurt",
    18: "ap-seoul",
    19: "ap-chongqing",
    21: "ap-mumbai",
    22: "na-ashburn",
    23: "ap-bangkok",
    25: "ap-tokyo",
    33: "ap-beijing-fsi",
    39: "ap-jakarta",
    46: "ap-nanjing",
    47: "ap-shanghai-fsi",
    72: "sa-saopaulo",
    78: "ap-shanghai-adc",
    102: "ap-singapore",
    9999: "ap-other",
}


# ─── 凭证解析 ──────────────────────────────────────────────────────
#
# 凭证读取策略（2026-06-08 简化为单一路径）：
#
#   唯一路径（环境变量）：
#     - 必填：TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY（shell export）
#     - UIN / AppId / 账户昵称 由脚本调用 cam.GetUserAppId 自动获取
#     - `--account-name` 仅作展示别名（用户对账户的可读称呼），不参与匹配
#     - 不再支持 .env 文件 / ACCOUNT_{ALIAS}_ 多账号别名映射
#
#   缺失态：返回 status=need_credentials + 友好引导文本（prompt 字段）。

def _build_need_credentials_prompt(lang="zh"):
    """组装"凭证未就绪"的友好引导话术（SSM+KMS 单一来源模式）。

    本专家团强制以腾讯云 SSM（KMS 加密）为唯一凭证来源：业务 AK/SK 不进环境变量，
    而是由管理员存入 SSM，运行端只持有一把 runner（bootstrap）密钥用于拉取。

    lang="en" 时返回**确定性英文版**（非机翻），保证英文提问场景下引导语种一致。
    """
    if lang == "en":
        return _need_credentials_prompt_en()
    return (
        "没能从 SSM 取到这个账号的凭证，降本巡检没法跑起来。\n\n"
        "## ⚠️ 凭证未就绪：SSM 里没有对应密钥\n\n"
        "本专家团**强制以腾讯云 SSM（凭据管理，KMS 加密）为唯一凭证来源**——业务 AK/SK "
        "不会出现在环境变量里，而是由管理员加密存放在 SSM，运行时按 `(cloud, project)` 动态拉取。\n\n"
        "---\n\n"
        "### 一、运行端只需配一把 runner 密钥（一次性）\n\n"
        "运行 WorkBuddy 的机器上，环境变量里**只放 runner（bootstrap）密钥**，它只用来读 SSM：\n\n"
        "```bash\n"
        'export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="<runner 密钥 SecretId>"\n'
        'export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<runner 密钥 SecretKey>"\n'
        '# 可选，SSM 所在地域，默认 ap-guangzhou\n'
        'export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"\n'
        '# 配好 runner 密钥即自动启用 SSM；CLOUDOPS_SSM_ENABLED 是可选急停开关，\n'
        '# 仅在想临时关闭 SSM（不删 key）时设为 false/0/no/off。\n'
        "```\n\n"
        "> runner 密钥**只需要 SSM 读取一类权限**（`ssm:GetSecretValue` / `ssm:ListSecrets`），"
        "**不需要任何 `kms:*` 权限**——解密由 SSM 服务端用服务角色（`SSM_QCSRole`，开通 SSM 时一次性授权）"
        "完成，明文经 TLS 返回。它**不能**直接签发业务 API，泄露风险远低于裸 AK/SK。\n\n"
        "---\n\n"
        "### 二、管理员把业务凭证存入 SSM（每个账号一条）\n\n"
        "到 [腾讯云 SSM 控制台](https://console.cloud.tencent.com/ssm) 新建**用户凭据**，"
        "名称按约定 `cloudops/{cloud}/{project}`，凭据内容是一段 JSON：\n\n"
        "```\n"
        "凭据名称: cloudops/tencent/YX\n"
        "凭据内容(SecretString):\n"
        '{\n'
        '  "secret_id": "AKIDxxxxxxxx",\n'
        '  "secret_key": "xxxxxxxx",\n'
        '  "uin": "100012345678",\n'
        '  "region": "ap-guangzhou"\n'
        '}\n'
        "```\n\n"
        "> 创建凭据时选择 **KMS 加密**（SSM 默认即用 KMS 信封加密），密钥就以密文落库，控制台与 API 都不回显明文。\n\n"
        "给该账号子用户授予降本所需策略（`QcloudFinanceFullAccess` + `QcloudMonitorReadOnlyAccess` + 各产品 `ReadOnly`）。\n\n"
        "---\n\n"
        "### 三、四个专家共用一套 SSM 凭据\n\n"
        "账单 / 审计 / 巡检 / 降本 4 个专家都从同一条 SSM 凭据取密钥，因此一个账号只需在 SSM 存一次。"
        "建议给该子账号一次性授予 `ReadOnlyAccess` + `QcloudFinanceFullAccess`，4 个专家即可全部跑通。\n\n"
        "---\n\n"
        "### 一旦凭据就位，云降本医生会输出：\n\n"
        "| 维度 | 内容 |\n"
        "|---|---|\n"
        "| 💰 **账单驱动扫描** | 只扫真正在花钱的产品，零费用产品自动跳过 |\n"
        "| 📊 **30 天监控采集** | CPU / 内存 / 网络 / IOPS / 连接数，覆盖率 ≥80% 才下判定 |\n"
        "| 🔴 **风险等级化建议** | 🔴严重 / 🟠高 / 🟡中 / 🟢低 四档 + 预计月省金额 |\n"
        "| 🛡️ **关键资源保护** | MySQL 灾备/只读、Redis 副本、MongoDB SECONDARY、`finops-critical` 标签自动豁免 |\n"
        "| 📦 **闲置/降配/释放清单** | 空集群、未挂载磁盘、未绑定 EIP、低利用率实例分门别类 |\n"
        "| 🗓️ **降本计划排期** | 高/中/低风险分周排期 + 预计累计月省 |\n"
        "| 📑 **单文件 HTML 看板** | 深色玻璃拟态 + Chart.js + 全量明细，落盘工作区由 WorkBuddy 客户端直接预览 |\n"
        "| 🤖 **Markdown 概览** | 回复框 ≤600 字五段式 Markdown：风险总览 / 产品洞察 / Top 闲置资源 / 关键资源保护 / 降本建议 |\n\n"
        "输出形式：**单文件 HTML 数据看板** + **Markdown 概览**（≤600 字五段式，回复框直接看）。\n\n"
        "---\n\n"
        "**请确认下一步：**\n\n"
        "1. runner 密钥和 SSM 凭据都已就绪？— 告诉我\"配好了\"，立刻启动闲置巡检\n"
        "2. 需要我详细说明**如何在 SSM 控制台建凭据 / 给 runner 授权**？\n"
        "3. 先看一份样例降本报告了解输出格式？\n"
    )


def _need_credentials_prompt_en():
    """English version of the SSM-only credentials-missing guidance. Deterministic, NOT machine-translated.
    Structure / links / commands / security notes are 1:1 aligned with the Chinese version."""
    return (
        "Couldn't fetch this account's credentials from SSM — the cost-optimization scan can't start yet.\n\n"
        "## ⚠️ Credentials not ready: no matching secret in SSM\n\n"
        "This expert team uses **Tencent Cloud SSM (Secrets Manager, KMS-encrypted) as the SOLE "
        "credential source** — business AK/SK never live in environment variables. An admin stores them "
        "encrypted in SSM, and they are pulled at runtime by `(cloud, project)`.\n\n"
        "---\n\n"
        "### 1. The runtime host only needs ONE runner key (one-time)\n\n"
        "On the machine running WorkBuddy, the environment holds **only the runner (bootstrap) key**, "
        "used solely to read SSM:\n\n"
        "```bash\n"
        'export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="<runner key SecretId>"\n'
        'export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<runner key SecretKey>"\n'
        '# Optional, SSM region, default ap-guangzhou\n'
        'export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"\n'
        '# Having the runner key enables SSM automatically; CLOUDOPS_SSM_ENABLED is an\n'
        '# optional kill-switch — set it to false/0/no/off only to disable SSM.\n'
        "```\n\n"
        "> The runner key needs **only SSM read perms** (`ssm:GetSecretValue` / `ssm:ListSecrets`), "
        "and **no `kms:*` permission** — SSM decrypts server-side under its own service role "
        "(`SSM_QCSRole`, authorized once when SSM is activated) and returns plaintext over TLS. "
        "It **cannot** sign business APIs "
        "directly, so its blast radius is far smaller than a raw AK/SK.\n\n"
        "---\n\n"
        "### 2. The admin stores business credentials in SSM (one secret per account)\n\n"
        "In the [Tencent Cloud SSM console](https://console.cloud.tencent.com/ssm), create a **user secret** "
        "named by convention `cloudops/{cloud}/{project}`, whose content is a JSON blob:\n\n"
        "```\n"
        "Secret name: cloudops/tencent/YX\n"
        "SecretString:\n"
        '{\n'
        '  "secret_id": "AKIDxxxxxxxx",\n'
        '  "secret_key": "xxxxxxxx",\n'
        '  "uin": "100012345678",\n'
        '  "region": "ap-guangzhou"\n'
        '}\n'
        "```\n\n"
        "> Choose **KMS encryption** when creating the secret (SSM uses KMS envelope encryption by default), "
        "so the key is stored as ciphertext and never echoed in plaintext by console or API.\n\n"
        "Grant the account's sub-user the cost-opt policies (`QcloudFinanceFullAccess` + "
        "`QcloudMonitorReadOnlyAccess` + per-product `ReadOnly`).\n\n"
        "---\n\n"
        "### 3. All four experts share one SSM secret\n\n"
        "Billing / audit / inspection / cost-opt all read the key from the same SSM secret, so each account "
        "is stored in SSM only once. Granting the sub-account `ReadOnlyAccess` + `QcloudFinanceFullAccess` "
        "gets all four experts working.\n\n"
        "---\n\n"
        "### Once the secret is in place, the Cost-Opt Doctor will output:\n\n"
        "| Dimension | Content |\n"
        "|---|---|\n"
        "| 💰 **Bill-driven scan** | Only scans products actually spending money; zero-cost products auto-skipped |\n"
        "| 📊 **30-day monitoring** | CPU / memory / network / IOPS / connections; judgment only when coverage ≥80% |\n"
        "| 🔴 **Risk-graded advice** | 🔴Critical / 🟠High / 🟡Medium / 🟢Low + estimated monthly saving |\n"
        "| 🛡️ **Critical-resource protection** | MySQL DR/read-replica, Redis replica, MongoDB SECONDARY, `finops-critical` tag auto-exempt |\n"
        "| 📦 **Idle/downsize/release list** | Empty clusters, unattached disks, unbound EIPs, low-utilization instances categorized |\n"
        "| 🗓️ **Cost-saving schedule** | High/medium/low risk scheduled by week + estimated cumulative monthly saving |\n"
        "| 📑 **Single-file HTML dashboard** | Dark glassmorphism + Chart.js + full detail, saved to workspace and previewed directly in the WorkBuddy client |\n"
        "| 🤖 **Markdown overview** | ≤600-word five-part Markdown in the reply: risk overview / product insights / Top idle resources / critical-resource protection / saving advice |\n\n"
        "Output form: **single-file HTML dashboard** + **Markdown overview** (≤600-word five-part, readable directly in the reply box).\n\n"
        "---\n\n"
        "**Please confirm the next step:**\n\n"
        "1. Runner key and SSM secret both ready? — tell me \"done\" and I'll start the idle-resource scan\n"
        "2. Want me to walk through **creating the secret in SSM / granting the runner key**?\n"
        "3. Want to see a sample cost-optimization report first to understand the output format?\n"
    )


def auto_detect_account(secret_id, secret_key, token=None):
    """通过 cam.GetUserAppId 自动获取 OwnerUin / AppId / Nickname。

    GetUserAppId 主子账号皆可调，子账号也能读到自身 OwnerUin/AppId，
    无需任何额外授权。失败时返回带 error 字段的 dict，调用方决定降级策略。

    `token` 为 STS 临时凭证 Token（路径 2 AssumeRole 后必填），普通 AK/SK 直连不传。
    """
    try:
        resp = call_api(
            host="cam.tencentcloudapi.com",
            service="cam",
            action="GetUserAppId",
            version="2019-01-16",
            payload={},
            region="",
            secret_id=secret_id,
            secret_key=secret_key,
            token=token,
        )
    except Exception as e:
        return {"ok": False, "error": f"GetUserAppId 调用失败: {e}"}

    body = resp.get("Response", resp) if isinstance(resp, dict) else {}
    err = body.get("Error") if isinstance(body, dict) else None
    if err:
        return {"ok": False, "error": f"{err.get('Code', 'APIError')}: {err.get('Message', '')}"}

    # 优先取 OwnerUin（主账号），账单/资源/产物目录都挂在主账号下；Uin 可能是 caller 子账号
    # 与 cloud-billing-pro / cloud-auditor-pro / cloud-inspector-pro 口径保持一致，
    # 确保四专家产物落到同一个 reports/{OwnerUin}/ 目录而非按 caller 子账号分散
    uin = str(body.get("OwnerUin") or body.get("Uin") or "").strip()
    app_id = str(body.get("AppId") or "").strip()
    if not uin:
        return {"ok": False, "error": "GetUserAppId 返回未包含 Uin 字段"}
    return {
        "ok": True,
        "uin": uin,
        "app_id": app_id,
        "nickname": body.get("Nickname") or body.get("Name") or "",
    }


# 注：跨账号 STS AssumeRole 已统一收敛到 cloudops-accounts-pro/scripts/build_credential_env.resolve()
# 内部完成（secret 带 role_arn 即在进程内 assume，返回临时凭证 + token，并剔除 role_arn 防 double-assume）。
# 本脚本不再内嵌 assume_role()，凭证一律经 load_env_credentials() → resolve() 取得。


def resolve_credentials(account_name=None, lang="zh", cloud="tencent", project=None):
    """解析账户凭证（进程内隔离，不污染 os.environ）。

    支持两种路径（均由 build_credential_env.resolve() 在 SSM 侧完成）：
      1. 直连：业务账号 AK/SK 经 resolve(cloud, project) 从 SSM 取（KMS 服务端解密）
      2. 跨账号代查：SSM secret 内带 role_arn（+ 可选 assume_from），resolve() 自动
         在进程内 AssumeRole 换临时凭证 + token 返回，本函数直接透传，不再二次 assume。
         → 调用方主账号 AK/SK 通过 STS AssumeRole 换取目标账号的临时凭证（含 Token）

    `cloud`/`project`：多账号并发隔离键 + SSM 定位键。project 非空 → resolve(cloud, project) 从 SSM 取；
       为空 → 返回空凭证走 need_credentials 引导；绝不读裸名 env。由 main() 从 --cloud/--project 透传。
    `account_name` 仅作展示别名（写入报告标题），不参与匹配。
    `lang` 决定缺失态 prompt 语种（zh/en），由 main() 从 --lang 透传。
    缺失 → 返回 status=need_credentials + prompt 友好引导（含路径 1 / 路径 2 配置说明）。
    """
    env_cred = load_env_credentials(cloud=cloud, project=project)
    sid = env_cred["secret_id"]
    sk = env_cred["secret_key"]

    if not sid or not sk:
        return {
            "status": "need_credentials",
            "display_name": account_name or "",
            "uin": "",
            "missing": [
                v for v, present in (
                    ("TENCENTCLOUD_SECRET_ID", sid),
                    ("TENCENTCLOUD_SECRET_KEY", sk),
                ) if not present
            ],
            "prompt": _build_need_credentials_prompt(lang),
        }

    # 跨账号 STS AssumeRole 已收敛进 build_credential_env.resolve()：若 SSM secret 带
    # role_arn，resolve() 已在进程内 assume 完毕，sid/sk 即临时凭证、token 已带回，
    # 且 role_arn 被剔除。本层不再 assume，直接消费透传的 token（杜绝 double-assume）。
    token = env_cred.get("token", "").strip()
    role_arn = ""  # resolve() 已剔除；保留变量供下方返回字段兼容
    source = "sts_assume_role" if token else "primary_env"

    detected = auto_detect_account(sid, sk, token=token)
    if not detected.get("ok"):
        return {
            "status": "need_credentials",
            "display_name": account_name or "",
            "uin": "",
                "missing": ["UIN(auto_detect_failed)"],
                "error": detected.get("error", "auto_detect_account failed"),
                "prompt": _build_need_credentials_prompt(lang),
            }

    display_name = account_name or detected.get("nickname") or f"UIN-{detected['uin']}"
    return {
        "status": "ok",
        "display_name": display_name,
        "uin": detected["uin"],
        "app_id": detected.get("app_id", ""),
        "secret_id": sid,
        "secret_key": sk,
        "token": token,             # 路径 2 时非空，需作为 X-TC-Token 透传给后续 API
        "role_arn": role_arn,
        "source": source,
        "active_cloud": cloud,      # 供落盘命名直接取用
        "active_project": project or "",
    }


# ─── 月份工具 ──────────────────────────────────────────────────────

def default_bill_month():
    """默认账单月：上月 YYYY-MM。"""
    today = datetime.now()
    if today.month == 1:
        return f"{today.year - 1:04d}-12"
    return f"{today.year:04d}-{today.month - 1:02d}"


def month_range(yyyymm):
    """返回 (begin_time, end_time) 字符串 "YYYY-MM-DD HH:MM:SS"。"""
    y, m = int(yyyymm[:4]), int(yyyymm[5:7])
    last_day = calendar.monthrange(y, m)[1]
    return (f"{y:04d}-{m:02d}-01 00:00:00",
            f"{y:04d}-{m:02d}-{last_day:02d} 23:59:59")


# ─── Step 0.3 账单汇总 ────────────────────────────────────────────

def fetch_bill_summary(sid, sk, bill_month):
    """并发拉账单汇总（产品 + 地域），返回 (products, region_ids, product_totals, total_cost)。"""
    begin, end = month_range(bill_month)

    def _by_product():
        return call_api(
            host="billing.tencentcloudapi.com", service="billing",
            action="DescribeBillSummaryByProduct", version="2018-07-09",
            payload={"BeginTime": begin, "EndTime": end},
            region="", secret_id=sid, secret_key=sk,
        )

    def _by_region():
        return call_api(
            host="billing.tencentcloudapi.com", service="billing",
            action="DescribeBillSummaryByRegion", version="2018-07-09",
            payload={"BeginTime": begin, "EndTime": end},
            region="", secret_id=sid, secret_key=sk,
        )

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_prod = ex.submit(_by_product)
        f_reg = ex.submit(_by_region)
        prod_resp = f_prod.result()
        reg_resp = f_reg.result()

    prod_items = prod_resp.get("SummaryOverview", [])
    reg_items = reg_resp.get("SummaryOverview", [])

    products = [i["BusinessCode"] for i in prod_items
                if float(i.get("RealTotalCost", 0)) > 0]
    product_totals = {i["BusinessCode"]: float(i.get("RealTotalCost", 0))
                      for i in prod_items if float(i.get("RealTotalCost", 0)) > 0}
    total_cost = sum(product_totals.values())

    # RegionId 是字符串数字，转 int 再查映射
    region_ids = []
    for i in reg_items:
        if float(i.get("RealTotalCost", 0)) > 0:
            rid_int = int(i.get("RegionId", 0))
            api_region = REGION_MAP.get(rid_int)
            if api_region and api_region not in region_ids:
                region_ids.append(api_region)

    return products, region_ids, product_totals, total_cost


# ─── Step 0.4 资源级账单预取 ──────────────────────────────────────

def fetch_billing_lookup(sid, sk, bill_month, products):
    """并发拉 DescribeBillResourceSummary，构建 rid → 费用索引。"""

    def fetch_product(product_code):
        offset, limit, out = 0, 100, {}
        while True:
            resp = call_api(
                host="billing.tencentcloudapi.com", service="billing",
                action="DescribeBillResourceSummary", version="2018-07-09",
                payload={
                    "Month": bill_month, "Offset": offset, "Limit": limit,
                    "BusinessCode": product_code, "NeedRecordNum": 1,
                },
                region="", secret_id=sid, secret_key=sk,
            )
            items = resp.get("ResourceSummarySet", [])
            for it in items:
                rid = it.get("ResourceId") or ""
                if rid:
                    out[rid] = {
                        "cost": float(it.get("RealTotalCost", 0)),
                        "product": product_code,
                        "charge": it.get("PayModeName", ""),
                        "region": it.get("RegionId", ""),
                        "name": it.get("ResourceName", ""),
                    }
            total = int(resp.get("Total", 0))
            if not items or offset + limit >= total:
                break
            offset += limit
        return out

    billing_lookup = {}
    with ThreadPoolExecutor(max_workers=3) as ex:  # billing API 限速较严
        futs = [ex.submit(fetch_product, code) for code in products]
        for f in as_completed(futs):
            billing_lookup.update(f.result())

    return billing_lookup


# ─── Step 1 资源发现 ──────────────────────────────────────────────

def parse_product_registry(skill_dir):
    """从 api-resource.md 正则解析 PRODUCT_REGISTRY。

    返回 {product_code: {title, host, service, action, version, list_key, special}}
    """
    doc_path = Path(skill_dir) / "references" / "api-resource.md"
    doc = doc_path.read_text(encoding="utf-8")

    registry = {}
    for section in re.split(r"\n##+ ", "\n" + doc)[1:]:
        title = section.split("\n", 1)[0].strip()
        block_match = re.search(r"```(.*?)```", section, re.S)
        if not block_match:
            continue
        block = block_match.group(1)

        def _get(key):
            ms = list(re.finditer(rf"^{key}(?:\s*\d+)?:\s*(.+?)$", block, re.M))
            if not ms:
                return ""
            return ms[0].group(1).split("#")[0].strip()

        codes = [c.strip() for c in _get("ProductCode").split(",") if c.strip()]
        if not codes:
            continue

        api_line = _get("API")
        if "/" in api_line:
            host, action = api_line.split("/", 1)
        else:
            host, action = api_line, ""
        host = host.strip()
        action = action.strip()
        service = host.split(".")[0] if host else ""

        spec = {
            "title": title,
            "host": host,
            "service": service,
            "action": action,
            "version": _get("Version"),
            "list_key": _get("ResponseListKey") or "InstanceSet",
            "special": _get("Special"),
        }
        for code in codes:
            registry[code] = spec

    return registry


def _paginated(sid, sk, host, service, action, version, payload, region, list_key):
    """通用分页拉取。"""
    out, offset, limit = [], 0, 100
    while True:
        resp = call_api(
            host=host, service=service, action=action, version=version,
            payload={**payload, "Offset": offset, "Limit": limit},
            region=region, secret_id=sid, secret_key=sk,
        )
        # list_key 可能是嵌套路径，如 "Result.InstanceList"
        node = resp
        for k in list_key.split("."):
            node = (node or {}).get(k, []) if isinstance(node, dict) else []
        out.extend(node or [])
        if not node or len(node) < limit:
            break
        offset += limit
    return out


def scan_generic(sid, sk, spec, region):
    return _paginated(sid, sk, spec["host"], spec["service"], spec["action"],
                      spec["version"], {}, region, spec["list_key"])


def scan_clb_two_stage(sid, sk, spec, region):
    """CLB: 先 DescribeLoadBalancers, 再逐个 DescribeTargets。"""
    lbs = scan_generic(sid, sk, spec, region)
    for lb in lbs:
        try:
            resp = call_api(
                host="clb.tencentcloudapi.com", service="clb",
                action="DescribeTargets", version=spec["version"],
                payload={"LoadBalancerId": lb["LoadBalancerId"]},
                region=region, secret_id=sid, secret_key=sk,
            )
            lb["Targets"] = resp.get("Listeners", [])
        except Exception:
            lb["Targets"] = []
    return lbs


def scan_tdmq_three(sid, sk, spec, region):
    """TDMQ: 依次拉 Pulsar / RocketMQ / PulsarPro 三子类型。"""
    kw = dict(host="tdmq.tencentcloudapi.com", service="tdmq",
              version=spec["version"], payload={}, region=region,
              secret_id=sid, secret_key=sk)
    result = {}
    try:
        result["pulsar"] = call_api(action="DescribeClusters", **kw).get("ClusterSet", [])
    except Exception:
        result["pulsar"] = []
    try:
        result["rocketmq"] = call_api(action="DescribeRocketMQClusters", **kw).get("ClusterList", [])
    except Exception:
        result["rocketmq"] = []
    try:
        result["pulsar_pro"] = call_api(action="DescribePulsarProInstances", **kw).get("Instances", [])
    except Exception:
        result["pulsar_pro"] = []
    return result


def discover_resources(sid, sk, products, region_ids, billing_lookup, registry):
    """按 products × regions 并发扫描资源，返回 (results, errors, unknown_products)。"""
    unknown = []
    tasks = []  # (title, region, fn, spec, code)

    # COS 产品码可能是 p_cos 或 p_011603 等，从 billing_lookup 反查
    cos_rids = [rid for rid, info in billing_lookup.items()
                if info.get("product", "").startswith("p_cos") or
                "p_cos" in info.get("product", "")]

    # 构建 title → primary code 映射（同一 spec 可能挂多个 code，取第一个）
    title_code_map = {}
    for code, spec in registry.items():
        t = spec.get("title", "")
        if t and t not in title_code_map:
            title_code_map[t] = code

    for code in products:
        spec = registry.get(code)
        if not spec:
            unknown.append(code)
            continue
        special = spec.get("special", "")
        if special == "bill-resource-summary":
            # COS: 不调资源 API，直接从 billing_lookup 取
            continue
        for region in region_ids:
            tasks.append((spec["title"], region, spec, code))

    results, errors = {}, {}

    def _run(title, region, spec, code):
        # 重新绑定正确的 region（lambda 闭包问题绕过）
        special = spec.get("special", "")
        try:
            if special == "two-stage-targets":
                return title, region, scan_clb_two_stage(sid, sk, spec, region), code
            elif special == "tdmq-three-flavors":
                return title, region, scan_tdmq_three(sid, sk, spec, region), code
            else:
                return title, region, scan_generic(sid, sk, spec, region), code
        except Exception as e:
            return title, region, e, code

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_run, title, region, spec, code): (title, region)
                for title, region, spec, code in tasks}
        for f in as_completed(futs):
            title, region = futs[f]
            val = f.result()
            _, _, data, code = val
            # key 格式: "标题|产品码" 供 collect_monitor.py 的 PRODUCT_KEYMAP 匹配
            key = f"{title}|{code}"
            if isinstance(data, Exception):
                errors.setdefault(key, {})[region] = str(data)[:200]
            else:
                results.setdefault(key, {})[region] = data

    # COS 特殊路径：从 billing_lookup 取桶列表
    cos_spec = registry.get("p_cos") or next(
        (v for k, v in registry.items() if k.startswith("p_cos")), None)
    if cos_spec and cos_rids:
        cos_code = "p_cos"
        cos_title = cos_spec.get("title", "COS 对象存储")
        cos_key = f"{cos_title}|{cos_code}"
        results.setdefault(cos_key, {})["billing_lookup"] = [
            {"Bucket": rid, "ResourceId": rid, **billing_lookup.get(rid, {})}
            for rid in cos_rids
        ]

    return results, errors, unknown


# ─── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="costopt 一键数据准备（Step 0.3/0.4 + Step 1）")
    parser.add_argument("--account-name", type=str, default="", help="账户中文名")
    parser.add_argument("--cloud", type=str, default="tencent",
                        help="云厂商（tencent/huawei/...），并发隔离 + 落盘命名用")
    parser.add_argument("--project", type=str, default="",
                        help="项目/账号代号（如 ADC/TSP/YX）；必传——进程内经 "
                             "build_credential_env.resolve() 从 SSM 取该账号专属凭证（KMS 服务端解密），"
                             "规避竞态。省略则返回空凭证、走 need_credentials 引导（不读裸名 env）")
    parser.add_argument("--work-dir", type=str, required=True, help="工作目录（产物落盘位置）")
    parser.add_argument("--bill-month", type=str, default="",
                        help="账单月份 YYYY-MM（默认上月）")
    parser.add_argument("--skill-dir", type=str, default="",
                        help="Skill 根目录（默认从脚本自身位置推断）")
    parser.add_argument("--lang", type=str, default="zh", choices=["zh", "en"],
                        help="缺失态引导文案语种（zh=中文，en=English），跟随主理人 output_lang")
    args = parser.parse_args()

    # ── 凭证（进程内隔离，多账号并发安全）──
    creds = resolve_credentials(
        account_name=args.account_name or None,
        lang=args.lang,
        cloud=args.cloud,
        project=args.project or None,
    )
    if creds is None or creds.get("status") == "need_credentials":
        # 主路径 / 兼容路径都缺失 → 输出友好引导（含 prompt 文本，由调用方渲染）
        if creds is None:
            creds = {
                "status": "need_credentials",
                "alias": "",
                "display_name": args.account_name or "",
                "uin": "",
                "missing": ["TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY"],
                "prompt": _build_need_credentials_prompt(args.lang),
            }
        print(json.dumps(creds, ensure_ascii=False))
        sys.exit(0)

    sid = creds["secret_id"]
    sk = creds["secret_key"]
    uin = creds["uin"]
    display_name = creds["display_name"]

    # 路径 2 STS AssumeRole：把 Token 注入 sign 模块的**进程局部 holder**，让下游
    # call_api() 自动透传 X-TC-Token。改用 set_process_token 而非写 os.environ：
    # 全程零污染共享环境，与 billing 的「token 内存传递」契约对齐，并发 fan-out 绝不串台。
    if creds.get("token"):
        import sign as _sign
        _sign.set_process_token(creds["token"])

    # ── 月份 ──
    bill_month = args.bill_month or default_bill_month()

    # ── 工作目录 ──
    work_dir = args.work_dir
    os.makedirs(work_dir, exist_ok=True)

    # ── Skill 目录（用于读取 api-resource.md）──
    # 优先用 --skill-dir，否则从脚本自身位置推断（scripts/ 的上级目录）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    skill_dir = args.skill_dir or os.path.dirname(script_dir)

    # ── Step 0.3 账单汇总 ──
    products, region_ids, product_totals, total_cost = fetch_bill_summary(sid, sk, bill_month)

    # ── Step 0.4 资源级账单预取 ──
    billing_lookup = fetch_billing_lookup(sid, sk, bill_month, products)

    # 落盘 billing_lookup + bill_summary
    with open(os.path.join(work_dir, "billing_lookup.json"), "w", encoding="utf-8") as fp:
        json.dump(billing_lookup, fp, ensure_ascii=False)

    with open(os.path.join(work_dir, "bill_summary.json"), "w", encoding="utf-8") as fp:
        json.dump({
            "products": products,
            "region_ids": region_ids,
            "product_totals": product_totals,
            "total_cost": total_cost,
            "bill_month": bill_month,
            "uin": uin,
            "account_name": display_name,
        }, fp, ensure_ascii=False)

    # ── Step 1 资源发现 ──
    registry = parse_product_registry(skill_dir)
    results, errors, unknown = discover_resources(
        sid, sk, products, region_ids, billing_lookup, registry)

    disc_out = {
        "results": results,
        "errors": errors,
        "unknown_products": unknown,
        "uin": uin,
        "account_name": display_name,
        "bill_month": bill_month,
    }
    with open(os.path.join(work_dir, "resource_discovery.json"), "w", encoding="utf-8") as fp:
        json.dump(disc_out, fp, ensure_ascii=False, default=str)

    # ── 资源总数统计 ──
    total_resources = 0
    per_product = {}
    for prod_title, regions in results.items():
        cnt = 0
        for region, items in regions.items():
            if isinstance(items, list):
                cnt += len(items)
            elif isinstance(items, dict):
                # TDMQ three-flavors
                for sub in items.values():
                    if isinstance(sub, list):
                        cnt += len(sub)
        per_product[prod_title] = cnt
        total_resources += cnt

    # ── stdout 输出 ──
    print(json.dumps({
        "status": "ok",
        "scan_ready": {
            "available": True,
            "products_count": len(products),
            "resources_count": total_resources,
            "total_bill_cost": int(total_cost),
            "bill_month": bill_month,
            "regions": region_ids,
            "per_product": per_product,
            "unknown_products": unknown,
            "error_products": list(errors.keys()),
            "billing_lookup_entries": len(billing_lookup),
        },
        "credentials": {
            "display_name": display_name,
            "uin": uin,
            "app_id": creds.get("app_id", ""),
            "source": creds.get("source", "primary_env"),
            "role_arn": creds.get("role_arn", ""),
            "has_sts_token": bool(creds.get("token")),
        },
        "work_dir": work_dir,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
