#!/usr/bin/env python3
"""
prepare_billing_data.py — 一键数据准备脚本

将 Step 0~2 的所有数据加载和预处理合并为一次调用，
避免 LLM 逐文件 execute_code 导致的 sandbox 启动开销（每次 3.2s × 29 次 ≈ 93s）。

用法:
  python3 prepare_billing_data.py --account-name "一中心管控账户" --month 202604
  python3 prepare_billing_data.py --uin 100039081678 --month 202604

输出: 标准输出 JSON，包含 LLM 生成报告所需的全部预处理数据。
"""

import argparse
import calendar
import json
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# 复用 summarize_bill.py 的函数
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from summarize_bill import (
    load_json,
    fmt_amount,
    summarize_resource_summary,
    summarize_summary_product,
    summarize_summary_tag,
)

# ─── 进程内凭证隔离（方案 A，2026-06-13 修复凭证竞态）─────────────────
#
# 历史 bug：主理人对每个 specialist 下发 `eval "$(build_credential_env.py --project XXX)"`，
# build_credential_env 把 {CLOUD}_{PROJECT}_SECRET_ID 全部归一到裸名 TENCENTCLOUD_SECRET_ID，
# 三个并发 agent 共享同一进程 os.environ，后写覆盖先写 → 三份报告全是最后一个项目的数据。
#
# 方案 A：本脚本接收 --cloud/--project，直接调 build_credential_env.resolve() 在**进程内**
# 从 SSM 拿到该项目专属的凭证 dict（KMS 服务端解密，源头是永不冲突的 SSM secret cloudops/{cloud}/{project}），
# 全程内存传递给 sign_and_call，绝不经过共享 os.environ、命令行参数或临时文件。
# resolve() 内部只走 SSM：没有 env 前缀路径、也没有裸名兜底。
_ACCOUNTS_LIB = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "cloudops-accounts-pro", "scripts")
)
if _ACCOUNTS_LIB not in sys.path:
    sys.path.insert(0, _ACCOUNTS_LIB)


def load_env_credentials(cloud="tencent", project=None):
    """进程内解析某 (cloud, project) 的凭证，返回扁平 dict（不污染 os.environ）。

    凭证唯一来源 = SSM（KMS 加密）。build_credential_env.resolve() 内部只走 SSM，
    没有 env 前缀路径、也没有裸名兜底。本函数因此**强制要求 project 非空**：
      - project 非空 → resolve(cloud, project) 从 SSM 取密钥（KMS 服务端解密）。
      - project 为空 → 直接返回空凭证（上层走 need_credentials 引导）。绝不读裸名 env。

    跨账号（STS AssumeRole）已**收敛进 resolve()**：若 SSM secret 带 role_arn，
    resolve() 已在进程内完成 AssumeRole，返回的 secret_id/secret_key 即临时凭证、
    并多带一个 token；role_arn 被 resolve() 主动剔除（返回里取不到），所以本层
    **不再做任何 assume**，直接透传 token 即可，彻底杜绝 double-assume。

    注意：BUDGET（月度预算）不是机密，仍允许从 env 读，方便部署侧灵活配置预算线。

    返回字段：secret_id / secret_key / token / role_arn(恒空) / external_id / duration / region / budget
    （凭证字段缺失为空串，由上层 resolve_credentials 判定 need_credentials）。
    """
    # BUDGET 非机密，无论是否有 project 都先从 env 取（带项目前缀优先，回退全局）
    if project:
        budget = (os.environ.get(f"TENCENTCLOUD_{project}_MONTHLY_BUDGET")
                  or os.environ.get("TENCENTCLOUD_MONTHLY_BUDGET", ""))
    else:
        budget = os.environ.get("TENCENTCLOUD_MONTHLY_BUDGET", "")

    empty = {
        "secret_id": "", "secret_key": "", "token": "", "role_arn": "", "external_id": "",
        "duration": "3600", "region": "", "budget": (budget or "").strip(),
    }

    # SSM-only：没有 project 就无法定位 SSM secret，直接返回空触发引导。
    if not project:
        return empty

    try:
        import build_credential_env as _bce
    except Exception:
        return empty
    # resolve() 在 SSM 不可用 / secret 缺失 / AssumeRole 失败时会 sys.exit(4/5/6)；
    # 捕获以便上层走友好引导
    try:
        env = _bce.resolve(cloud, project)
    except SystemExit:
        return empty

    return {
        "secret_id": env.get("TENCENTCLOUD_SECRET_ID", "").strip(),
        "secret_key": env.get("TENCENTCLOUD_SECRET_KEY", "").strip(),
        # token：resolve() 若做了 AssumeRole 会带回临时 Token；直连场景为空。
        "token": env.get("TENCENTCLOUD_TOKEN", "").strip(),
        # role_arn 已被 resolve() 剔除（跨账号已在 resolve 内完成）；恒为空，仅保留键兼容旧读取。
        "role_arn": "",
        "external_id": env.get("TENCENTCLOUD_ROLE_EXTERNAL_ID", "").strip(),
        "duration": (env.get("TENCENTCLOUD_ROLE_DURATION", "").strip() or "3600"),
        "region": env.get("TENCENTCLOUD_REGION", "").strip(),
        "budget": (budget or "").strip(),
    }


# ─── Step 0: 凭证解析 ───────────────────────────────────────────────
#
# 凭证读取策略（2026-06-08 重构，单账号 + 双路径，多账号 ACCOUNT_*_ 兼容已移除）：
#
#   路径 1：AK/SK 直连（推荐，本账号自查）
#     - 仅需 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY
#     - UIN / AppId / 账户名 由脚本调用 cam.GetUserAppId 自动获取
#     - 用户不需要也不应该手填 UIN
#
#   路径 2：STS AssumeRole（跨账号代查 / 最小权限收敛）
#     - TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 是调用方主账号 AK/SK
#     - TENCENTCLOUD_ROLE_ARN 指向目标账号的角色 ARN
#     - 可选 TENCENTCLOUD_ROLE_EXTERNAL_ID / TENCENTCLOUD_ROLE_DURATION
#     - 脚本调 sts.AssumeRole 拿临时 AK/SK + Token，再用临时凭证查目标账号账单
#
# 缺失态：返回 status=need_credentials + 友好引导文本（由 SKILL.md 渲染给用户）。

def auto_detect_account(secret_id, secret_key, token=None):
    """通过腾讯云 API 自动获取 OwnerUin / AppId / Nickname。

    依赖 cam.tencentcloudapi.com / GetUserAppId（无需任何额外授权，主账号可调，
    子账号也可读自身 OwnerUin）。失败时返回带 error 字段的 dict，调用方决定降级策略。

    token: 可选 STS 临时凭证 SessionToken。
    """
    try:
        from sign import sign_and_call  # 同目录脚本
    except Exception as e:
        return {"ok": False, "error": f"sign 模块加载失败: {e}"}

    try:
        resp = sign_and_call(
            host="cam.tencentcloudapi.com",
            service="cam",
            action="GetUserAppId",
            version="2019-01-16",
            payload={},
            secret_id=secret_id,
            secret_key=secret_key,
            token=token,
        )
    except Exception as e:
        return {"ok": False, "error": f"GetUserAppId 调用失败: {e}"}

    # 兼容 {"Response": {...}} 与 {...} 两种返回结构
    body = resp.get("Response", resp) if isinstance(resp, dict) else {}
    err = body.get("Error") if isinstance(body, dict) else None
    if err:
        return {"ok": False, "error": f"{err.get('Code', 'APIError')}: {err.get('Message', '')}"}

    # 优先取 OwnerUin（主账号），账单挂在主账号下；Uin 可能是子账号
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
# 本脚本不再内嵌 sts_assume_role()，凭证一律经 load_env_credentials() → resolve() 取得。


def _build_need_credentials_prompt(lang="zh"):
    """组装"凭证未就绪"的友好引导话术（SSM+KMS 单一来源模式）。

    本专家团强制以腾讯云 SSM（KMS 加密）为唯一凭证来源：业务 AK/SK 不进环境变量，
    而是由管理员存入 SSM，运行端只持有一把 runner（bootstrap）密钥用于拉取。

    lang="en" 时返回**确定性英文版**（非机翻），保证英文提问场景下引导语种一致。
    """
    if str(lang).lower().startswith("en"):
        return _need_credentials_prompt_en()
    return (
        "没能从 SSM 取到这个账号的凭证，账单分析没法跑起来。\n\n"
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
        "新建凭据时给该账号子用户授予所需只读策略即可（账单需 `QcloudFinanceFullAccess` + `QcloudCamReadOnlyAccess`）。\n\n"
        "---\n\n"
        "### 三、四个专家共用一套 SSM 凭据\n\n"
        "账单 / 审计 / 巡检 / 降本 4 个专家都从同一条 SSM 凭据取密钥，因此一个账号只需在 SSM 存一次。"
        "建议给该子账号一次性授予 `ReadOnlyAccess` + `QcloudFinanceFullAccess`，4 个专家即可全部跑通。\n\n"
        "---\n\n"
        "**一旦凭据就位，账单分析专家会输出**\n\n"
        "| 维度 | 内容 |\n"
        "|---|---|\n"
        "| 💰 **总费用** | 月度总额 + 环比 + 预算消耗率 |\n"
        "| 🌏 **按地域** | 各地域费用 + 占比 |\n"
        "| 📦 **按产品** | Top 10 产品费用 + 环比 + 占比 |\n"
        "| 📈 **近 3 月趋势** | Top 10 产品 sparkline |\n"
        "| 🏷️ **按标签分账** | head / business 维度分账 |\n"
        "| 🔥 **Top 10 资源** | 最贵资源 + 产品 + 环比 |\n"
        "| ⚖️ **环比变化** | 增长 / 降低 / 新增 / 已下线四类完整列表 |\n"
        "| 📝 **总结洞察** | 涨幅最大、未享受折扣产品 |\n\n"
        "输出形式：单文件 HTML 数据看板 + AIOps 智能概览（Markdown）。\n\n"
        "---\n\n"
        "**请确认下一步：**\n\n"
        "1. runner 密钥和 SSM 凭据都已就绪？— 告诉我\"配好了\"，立刻启动账单分析\n"
        "2. 需要我详细说明**如何在 SSM 控制台建凭据 / 给 runner 授权**？\n"
        "3. 先看一份**样例账单报告**了解输出格式？"
    )


def _need_credentials_prompt_en():
    """English version of the SSM-only credentials-missing guidance. Deterministic, NOT machine-translated.
    Structure/links/commands/security notes are 1:1 aligned with the Chinese version."""
    return (
        "Couldn't fetch this account's credentials from SSM — the billing analysis can't start yet.\n\n"
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
        "It **cannot** sign business APIs directly, so its blast radius is far smaller than a raw AK/SK.\n\n"
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
        "Grant the account's sub-user the required read-only policies (billing needs "
        "`QcloudFinanceFullAccess` + `QcloudCamReadOnlyAccess`).\n\n"
        "---\n\n"
        "### 3. All four experts share one SSM secret\n\n"
        "Billing / audit / inspection / cost-opt all read the key from the same SSM secret, so each account "
        "is stored in SSM only once. Granting the sub-account `ReadOnlyAccess` + `QcloudFinanceFullAccess` "
        "gets all four experts working.\n\n"
        "---\n\n"
        "**Once the secret is in place, the billing analyst will output**\n\n"
        "| Dimension | Content |\n"
        "|---|---|\n"
        "| 💰 **Total cost** | Monthly total + MoM + budget consumption rate |\n"
        "| 🌏 **By region** | Per-region cost + share |\n"
        "| 📦 **By product** | Top 10 product cost + MoM + share |\n"
        "| 📈 **Last 3 months** | Top 10 product sparklines |\n"
        "| 🏷️ **By tag** | head / business dimension allocation |\n"
        "| 🔥 **Top 10 resources** | Most expensive resources + product + MoM |\n"
        "| ⚖️ **MoM changes** | Full lists of increased / decreased / new / retired |\n"
        "| 📝 **Insights** | Largest increases, products missing discounts |\n\n"
        "Output format: single-file HTML dashboard + AIOps smart overview (Markdown).\n\n"
        "---\n\n"
        "**Please confirm the next step:**\n\n"
        "1. Runner key and SSM secret both ready? — tell me \"done\" and I'll start the billing analysis\n"
        "2. Want me to walk through **creating the secret in SSM / granting the runner key**?\n"
        "3. See a **sample billing report** first to understand the output format?"
    )


def resolve_credentials(account_name=None, uin=None, lang="zh", cloud="tencent", project=None):
    """解析账户凭证（SSM-only，跨账号已收敛进 resolve()）。

    凭证源头（SSM-only）：
      - project 非空 → resolve(cloud, project) 从 SSM 取（KMS 服务端解密，并发隔离）
      - project 为空 → 返回空凭证走 need_credentials 引导；绝不读裸名 env
    全程不经过共享 os.environ 的覆盖路径，不落临时文件，不进命令行。

    跨账号代查（STS AssumeRole）已统一收敛到 build_credential_env.resolve() 内部：
    若 SSM secret 带 role_arn，resolve() 已在进程内 AssumeRole 完毕，返回的
    secret_id/secret_key 即目标账号临时凭证、并带回 token；本函数因此**不再自己 assume**，
    直接消费 load_env_credentials() 透传的 (sid, sk, token) 即可（杜绝 double-assume）。

    返回字段：
      - status: "ok" / "need_credentials"
      - display_name / uin / app_id / secret_id / secret_key / token / budget
      - need_credentials 状态额外含 prompt 字段（友好引导话术）
      - account_name / uin 入参仅用于报告展示名校验，**不**参与凭证查找
    """
    env_cred = load_env_credentials(cloud=cloud, project=project)
    sid = env_cred["secret_id"]
    sk = env_cred["secret_key"]
    # token：resolve() 做过 AssumeRole 时为临时凭证 Token；直连场景为空串。
    token = env_cred.get("token", "")
    used_path = "sts" if token else "direct"

    if not sid or not sk:
        # 缺失字段名按是否带 project 给出，方便用户定位是哪个项目没配
        if project:
            vendor = {"tencent": "TENCENTCLOUD", "huawei": "HUAWEICLOUD",
                      "aliyun": "ALIYUNCLOUD"}.get(cloud.lower(), "TENCENTCLOUD")
            missing = [
                f"{vendor}_{project}_SECRET_ID" if not sid else None,
                f"{vendor}_{project}_SECRET_KEY" if not sk else None,
            ]
        else:
            missing = [
                "TENCENTCLOUD_SECRET_ID" if not sid else None,
                "TENCENTCLOUD_SECRET_KEY" if not sk else None,
            ]
        return {
            "status": "need_credentials",
            "missing": [m for m in missing if m],
            "prompt": _build_need_credentials_prompt(lang),
        }

    # ─── 调 cam.GetUserAppId 派生 UIN ─────────────────────────────────
    detect = auto_detect_account(sid, sk, token=token)
    if not detect.get("ok"):
        _is_en = str(lang).lower().startswith("en")
        return {
            "status": "need_credentials",
            "missing": ["UIN_AUTO_DETECT"],
            "prompt": _build_need_credentials_prompt(lang) +
                      (f"\n\n> AK/SK configured (path = {used_path}), but auto-resolving UIN failed: {detect.get('error', 'unknown error')}\n"
                       "> Please check the key/role is correct and `cam.tencentcloudapi.com` is reachable."
                       if _is_en else
                       f"\n\n> AK/SK 已配置（路径 = {used_path}），但自动获取 UIN 失败：{detect.get('error', '未知错误')}\n"
                       "> 请检查密钥/角色是否正确、网络是否可达 `cam.tencentcloudapi.com`。"),
        }

    resolved_uin = detect["uin"]
    nickname = detect.get("nickname", "") or f"主账号-{resolved_uin}"

    # 若调用方指定了 account_name / uin，仅作展示名校验，不强制
    display = nickname
    if account_name:
        display = account_name  # 用户明确指定的展示名优先

    return {
        "status": "ok",
        "display_name": display,
        "uin": resolved_uin,
        "app_id": detect.get("app_id", ""),
        "secret_id": sid,
        "secret_key": sk,
        "token": token,
        "auth_path": used_path,
        "budget": env_cred.get("budget", ""),
    }


def compute_skip_cache(analysis_month_str, today=None):
    """判断是否跳过缓存（当前月 / 关账期 T-1 月）。"""
    if today is None:
        today = datetime.now()
    yyyymm = int(analysis_month_str)
    cur_yyyymm = today.year * 100 + today.month
    # 当前月
    if yyyymm == cur_yyyymm:
        return True
    # 本月 1-4 号，T-1 月也跳过
    if today.day <= 4:
        # T-1 月
        prev_month = today.replace(day=1) - timedelta(days=1)
        prev_yyyymm = prev_month.year * 100 + prev_month.month
        if yyyymm == prev_yyyymm:
            return True
    return False


# ─── Step 1: 汇总数据加载 ────────────────────────────────────────────

def month_range(yyyymm_str):
    """返回 (begin_time, end_time) 字符串，格式 "YYYY-MM-DD HH:MM:SS"。"""
    y = int(yyyymm_str[:4])
    m = int(yyyymm_str[4:6])
    last_day = calendar.monthrange(y, m)[1]
    begin = f"{y:04d}-{m:02d}-01 00:00:00"
    end = f"{y:04d}-{m:02d}-{last_day:02d} 23:59:59"
    return begin, end


def prev_month_str(yyyymm_str):
    """返回上个月的 YYYYMM 字符串。"""
    y = int(yyyymm_str[:4])
    m = int(yyyymm_str[4:6])
    if m == 1:
        return f"{y - 1:04d}12"
    else:
        return f"{y:04d}{m - 1:02d}"


def load_summary_data(cache_base, yyyymm_str):
    """加载某月的汇总缓存数据（summary_product, summary_region, summary_tag）。"""
    month_dir = os.path.join(cache_base, yyyymm_str)
    if not os.path.isdir(month_dir):
        return None

    result = {"month": yyyymm_str}

    # summary_product
    sp_path = os.path.join(month_dir, "summary_product.json")
    if os.path.exists(sp_path):
        result["summary_product"] = summarize_summary_product(load_json(sp_path))

    # summary_region（本身不大）
    sr_path = os.path.join(month_dir, "summary_region.json")
    if os.path.exists(sr_path):
        raw = load_json(sr_path)
        items = []
        if isinstance(raw, dict):
            resp = raw.get("Response", raw)
            region_set = resp.get("RegionSummarySet",
                                  resp.get("SummaryOverview", []))
        else:
            region_set = raw
        for r in region_set:
            items.append({
                "RegionName": r.get("RegionName", ""),
                "RealTotalCost": fmt_amount(r.get("RealTotalCost", 0)),
                "RealTotalCostRatio": r.get("RealTotalCostRatio", ""),
            })
        items.sort(key=lambda x: x["RealTotalCost"], reverse=True)
        result["summary_region"] = items

    # summary_tag — 单独处理，留给 merge_tag_data 合并
    st_path = os.path.join(month_dir, "summary_tag.json")
    if os.path.exists(st_path):
        result["summary_tag_raw"] = load_json(st_path)

    return result


def merge_tag_data(analysis_raw, comparison_raw, analysis_month, comp_month):
    """合并分析月+对照月的标签数据为环比友好的结构。

    输入：两个月的 summary_tag.json 原始数据。
    输出：{tag_key: [{TagValue, analysis_month: cost, comp_month: cost}, ...]}
    """
    result = {}

    for raw, yyyymm in [(analysis_raw, analysis_month), (comparison_raw, comp_month)]:
        if not raw or not isinstance(raw, dict):
            continue
        for tag_key, tag_data in raw.items():
            if not isinstance(tag_data, dict):
                continue
            if tag_key not in result:
                result[tag_key] = {}
            for sub_key, sub_val in tag_data.items():
                # 兼容直接 Response 包装和月份嵌套两种结构
                if sub_key == "Response":
                    resp = sub_val
                elif isinstance(sub_val, dict) and ("SummaryOverview" in sub_val or "Response" in sub_val):
                    resp = sub_val.get("Response", sub_val)
                else:
                    continue
                raw_items = resp.get("SummaryOverview", [])
                for r in raw_items:
                    tv = r.get("TagValue", "(未标记)")
                    if tv not in result[tag_key]:
                        result[tag_key][tv] = {}
                    result[tag_key][tv][yyyymm] = fmt_amount(r.get("RealTotalCost", 0))

    # 转为列表
    output = {}
    for tag_key, tv_map in result.items():
        items = []
        for tv, costs in tv_map.items():
            items.append({"TagValue": tv, **costs})
        # 按分析月费用排序
        if items:
            items.sort(key=lambda x: x.get(analysis_month, 0), reverse=True)
        output[tag_key] = items

    return output


# ─── Step 2: 资源环比预计算 ──────────────────────────────────────────

def compute_resource_diff(curr_summary, prev_summary, threshold=1.0):
    """预计算资源环比变化分类（growth/decline/new/offline/stable）。

    输入：summarize_bill.py 输出的 resource_summary dict。
    输出：分类结果 dict。
    """
    if not curr_summary or not prev_summary:
        return None

    # 构建 ResourceId → 数据 的映射
    curr_map = {}
    for r in curr_summary.get("all_slim", []):
        curr_map[r["ResourceId"]] = r

    prev_map = {}
    for r in prev_summary.get("all_slim", []):
        prev_map[r["ResourceId"]] = r

    growth = []
    decline = []
    new_res = []
    offline = []
    stable = []

    all_ids = set(curr_map.keys()) | set(prev_map.keys())

    for rid in all_ids:
        c = curr_map.get(rid)
        p = prev_map.get(rid)

        c_cost = c["RealTotalCost"] if c else 0
        p_cost = p["RealTotalCost"] if p else 0
        diff = c_cost - p_cost

        if c and not p:
            new_res.append({
                "ResourceId": rid,
                "ProductName": c["ProductName"],
                "ProductCode": c.get("ProductCode", ""),
                "RealTotalCost": c_cost,
                "Tags": c.get("Tags", {}),
            })
        elif p and not c:
            offline.append({
                "ResourceId": rid,
                "ProductName": p["ProductName"],
                "ProductCode": p.get("ProductCode", ""),
                "PrevCost": p_cost,
                "Tags": p.get("Tags", {}),
            })
        elif abs(diff) > threshold:
            ratio = diff / p_cost if p_cost > 0 else 0
            entry = {
                "ResourceId": rid,
                "ProductName": c["ProductName"],
                "ProductCode": c.get("ProductCode", ""),
                "PrevCost": p_cost,
                "CurrCost": c_cost,
                "Diff": round(diff, 4),
                "Ratio": round(ratio * 100, 1),
                "Tags": c.get("Tags", {}),
                "IsNearNew": p_cost < 1,
            }
            if diff > 0:
                growth.append(entry)
            else:
                decline.append(entry)
        else:
            stable.append({
                "ResourceId": rid,
                "ProductName": c["ProductName"],
                "CurrCost": c_cost,
            })

    # 排序
    growth.sort(key=lambda x: x["Diff"], reverse=True)
    decline.sort(key=lambda x: x["Diff"])  # 最负的在前
    new_res.sort(key=lambda x: x["RealTotalCost"], reverse=True)
    offline.sort(key=lambda x: x["PrevCost"], reverse=True)

    return {
        "growth": {"count": len(growth), "items": growth},
        "decline": {"count": len(decline), "items": decline},
        "new": {"count": len(new_res), "items": new_res},
        "offline": {"count": len(offline), "items": offline},
        "stable": {"count": len(stable)},
    }


# ─── 趋势数据聚合 ─────────────────────────────────────────────────

def compute_trend_data(cache_base, analysis_month_str, top_n=10):
    """聚合 T-3~T 月的产品趋势数据（含分析月本身）。"""
    months = [analysis_month_str]  # T (分析月本身)
    m = analysis_month_str
    for _ in range(3):  # T-1, T-2, T-3
        m = prev_month_str(m)
        months.append(m)
    months.reverse()  # T-3, T-2, T-1, T (即 oldest first)

    trend = {}
    for yyyymm in months:
        sp_path = os.path.join(cache_base, yyyymm, "summary_product.json")
        if os.path.exists(sp_path):
            data = summarize_summary_product(load_json(sp_path))
            for p in data.get("products", []):
                name = p["ProductName"]
                if name not in trend:
                    trend[name] = {}
                trend[name][yyyymm] = p["RealTotalCost"]

    # 只保留 Top N 产品的趋势
    analysis_sp_path = os.path.join(cache_base, analysis_month_str, "summary_product.json")
    top_products = []
    if os.path.exists(analysis_sp_path):
        data = summarize_summary_product(load_json(analysis_sp_path))
        top_products = [p["ProductName"] for p in data.get("products", [])[:top_n]]

    result = {}
    for name in top_products:
        if name in trend:
            result[name] = trend[name]

    return result


# ─── 报告就绪预计算 ─────────────────────────────────────────────

# 按需后付费产品白名单（不参与"未折扣"检测）—— 这些产品按量计费，
# RealTotalCost==TotalCost 是常态，不属于"漏谈折扣"
_PAY_PER_USE_PRODUCTS = {
    "对象存储COS", "负载均衡CLB", "弹性公网IP", "弹性容器服务EKS",
    "云联网CCN", "云硬盘CBS", "对象存储", "负载均衡",
}


def _classify_trend(costs_by_month, months_ordered):
    """根据 4 个月数据判断趋势。

    months_ordered: [T-3, T-2, T-1, T] (oldest first)
    返回 (icon, label)。
    """
    vals = [costs_by_month.get(m, 0) for m in months_ordered]
    # 用 T-2 / T-1 / T 三个月做趋势判断（最新三个）
    t3, t2, t1 = vals[1], vals[2], vals[3]
    if t3 == 0 and t2 == 0 and t1 == 0:
        return ("➡️", "无数据")
    # 持续上升: T-2 < T-1 < T 且累计涨幅 > 10%
    if t3 < t2 < t1 and t3 > 0 and (t1 - t3) / t3 > 0.10:
        return ("📈", "持续上升")
    # 持续优化: T-2 > T-1 > T 且累计降幅 > 10%
    if t3 > t2 > t1 and t3 > 0 and (t3 - t1) / t3 > 0.10:
        return ("📉", "持续优化")
    # 检测平稳：变异系数 < 1%
    avg = (t3 + t2 + t1) / 3 if (t3 + t2 + t1) > 0 else 1
    max_dev = max(abs(t3 - avg), abs(t2 - avg), abs(t1 - avg))
    if avg > 0 and max_dev / avg < 0.01:
        return ("➡️", "平稳")
    return ("📊", "波动")


def _detect_no_discount_products(summary_product, threshold_amount=1000, threshold_ratio=0.99):
    """检测未享受折扣的产品。

    判定: real/total >= 0.99 且月费 >= 1000 且非按需后付费产品。
    返回 [{"name": "...", "cost": int, "discount_ratio": float}, ...]，按 cost 降序。
    """
    if not summary_product:
        return []
    products = summary_product.get("products", [])
    out = []
    for p in products:
        real = p.get("RealTotalCost", 0)
        total = p.get("TotalCost", 0)
        name = p.get("ProductName", "")
        if not name or name in _PAY_PER_USE_PRODUCTS:
            continue
        # COS / CLB / EIP / EKS / CCN / CBS 按量子串匹配（产品名可能略不同）
        if any(kw in name for kw in ["对象存储", "负载均衡", "弹性公网", "云联网", "云硬盘"]):
            continue
        if real < threshold_amount or total < 1:
            continue
        ratio = real / total if total > 0 else 1
        if ratio >= threshold_ratio:
            out.append({
                "name": name,
                "cost": real,
                "discount_ratio": round(ratio, 4),
            })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def _build_insights(curr_total, prev_total, summary_product, region_list, no_discount, budget):
    """生成智能洞察 bullets。

    规则参考 references/html-template.md §三必有章节·智能洞察。
    返回 [{"level": "🔴/🟡/🟢", "text": "..."}, ...]
    """
    insights = []
    products = (summary_product or {}).get("products", [])
    regions = region_list or []

    # 超预算
    if budget:
        try:
            b = float(budget)
            if curr_total > b:
                pct = (curr_total / b - 1) * 100
                insights.append({
                    "level": "🔴",
                    "text": f"**超预算**: 本月 ¥{curr_total:,} 超出预算 ¥{int(b):,}（+{pct:.1f}%）",
                })
        except (TypeError, ValueError):
            pass

    # 单产品占比 >50% (PREPAID 不触发 — 但产品级 PayMode 可能为空，
    # 用名称启发：含"专线/包年/合同" 视为 PREPAID）
    for p in products[:5]:
        ratio_str = p.get("RealTotalCostRatio", "0")
        try:
            r = float(ratio_str)
        except (TypeError, ValueError):
            r = 0
        name = p.get("ProductName", "")
        if r > 50 and not any(kw in name for kw in ["专线", "包年"]):
            insights.append({
                "level": "🟡",
                "text": f"**单产品高占比**: {name} 占 {r:.1f}%，建议拆分负载",
            })
            break

    # 单地域 >80%
    if regions:
        total_region = sum(r.get("RealTotalCost", 0) for r in regions)
        if total_region > 0:
            top = regions[0]
            ratio = top.get("RealTotalCost", 0) / total_region * 100
            if ratio > 80:
                insights.append({
                    "level": "🟡",
                    "text": f"**单地域高度集中**: {top.get('RegionName', '')} 占 {ratio:.1f}%，单点风险",
                })

    # 未享受折扣产品
    if no_discount:
        names = "、".join(f"{x['name']}（¥{x['cost']:,}/月）" for x in no_discount[:5])
        insights.append({
            "level": "🟡",
            "text": f"**未享受折扣的产品**: {names}",
        })

    # 总费用环比
    if prev_total > 0:
        ratio = (curr_total - prev_total) / prev_total * 100
        if ratio > 30:
            insights.append({
                "level": "🔴",
                "text": f"**总费用大幅上升**: 较上月 +{ratio:.1f}%",
            })
        elif 15 < ratio <= 30:
            insights.append({
                "level": "🟡",
                "text": f"**总费用上升**: 较上月 +{ratio:.1f}%",
            })

    return insights


def _format_pct(num, denom):
    if not denom:
        return "0.0%"
    return f"{num / denom * 100:.1f}%"


def build_report_ready(result):
    """从 prepare 结果中提炼"报告就绪 dict"。

    - totals: 总费用 + 环比
    - product_trend: 每个 Top 10 产品的 (icon, label)
    - no_discount_products: 未折扣产品列表
    - insights: 智能洞察 bullets
    - top_products: Top 10 产品逐行渲染数据（含环比图标）
    - top_regions: 地域逐行渲染数据
    - summary_highlights: 总结节最贵资源 / 涨幅最大资源
    """
    out = {"available": False}

    analysis_summary = result.get("analysis_summary") or {}
    comparison_summary = result.get("comparison_summary") or {}
    sp_curr = analysis_summary.get("summary_product")
    sp_prev = comparison_summary.get("summary_product")
    sr_curr = analysis_summary.get("summary_region")

    if not sp_curr or not sr_curr:
        return out

    # 总费用
    curr_total = sum(p.get("RealTotalCost", 0) for p in sp_curr.get("products", []))
    prev_total = sum(p.get("RealTotalCost", 0) for p in (sp_prev or {}).get("products", []))
    diff = curr_total - prev_total
    ratio = (diff / prev_total * 100) if prev_total > 0 else 0
    icon = "📈" if diff > 0 else ("📉" if diff < 0 else "➡️")

    out["totals"] = {
        "curr_total": curr_total,
        "prev_total": prev_total,
        "diff": diff,
        "ratio_pct": round(ratio, 1),
        "icon": icon,
    }

    # 产品趋势判定（基于 trend dict, T-3 ~ T，含分析月本身）
    trend = result.get("trend") or {}
    analysis_month = result.get("analysis_month", "")
    months_ordered = [analysis_month]  # T (oldest 在前，先放分析月再前推)
    m = analysis_month
    for _ in range(3):
        m = prev_month_str(m)
        months_ordered.append(m)
    months_ordered.reverse()  # [T-3, T-2, T-1, T]

    product_trend = {}
    for name, costs in trend.items():
        # trend 里的 key 是 yyyymm 字符串
        icon, label = _classify_trend(costs, months_ordered)
        product_trend[name] = {"icon": icon, "label": label, "costs_by_month": costs}

    out["product_trend"] = product_trend
    out["trend_months_ordered"] = months_ordered  # T-3, T-2, T-1, T
    out["analysis_month"] = analysis_month
    out["comparison_month"] = result.get("comparison_month", "")

    # 全量产品行（带环比图标）— HTML 看板按滚动表全量呈现，禁止 Top N 截断
    prev_map = {p.get("ProductName", ""): p.get("RealTotalCost", 0)
                for p in (sp_prev or {}).get("products", [])}
    top_products = []
    for p in sp_curr.get("products", []):
        name = p.get("ProductName", "")
        curr = p.get("RealTotalCost", 0)
        prev = prev_map.get(name, 0)
        d = curr - prev
        r = (d / prev * 100) if prev > 0 else 0
        # 包年包月/专线 → '—'
        is_prepaid = (p.get("PayMode", "") == "PREPAID") or "专线" in name
        if is_prepaid:
            diff_str, ratio_str = "—", "—"
        elif abs(d) < 1 and abs(r) < 0.1:
            diff_str, ratio_str = "— 持平", "— 持平"
        elif abs(d) < 10 or abs(r) < 1:
            diff_str = f"{d:+,.0f}"
            ratio_str = f"{r:+.1f}%"
        else:
            picon = "📈" if d > 0 else "📉"
            diff_str = f"{picon} {'+' if d > 0 else '-'}¥{abs(int(d)):,}"
            ratio_str = f"{r:+.1f}%"
        top_products.append({
            "name": name,
            "prev_cost": prev,
            "curr_cost": curr,
            "diff_str": diff_str,
            "ratio_str": ratio_str,
            "ratio_in_total": p.get("RealTotalCostRatio", "0"),
        })
    out["top_products"] = top_products

    # 地域行
    top_regions = []
    # sr_curr 在 load_summary_data 里被赋为 list[dict]（不是 {"regions":[...]} 包装）
    region_list = sr_curr if isinstance(sr_curr, list) else (sr_curr or {}).get("regions", [])
    total_region = sum(r.get("RealTotalCost", 0) for r in region_list)
    for r in region_list:
        cost = r.get("RealTotalCost", 0)
        top_regions.append({
            "name": r.get("RegionName", ""),
            "cost": cost,
            "ratio_pct": _format_pct(cost, total_region),
        })
    out["top_regions"] = top_regions

    # 未折扣产品
    no_discount = _detect_no_discount_products(sp_curr)
    out["no_discount_products"] = no_discount

    # 智能洞察
    budget = result.get("credentials", {}).get("budget", "")
    insights = _build_insights(curr_total, prev_total, sp_curr, region_list, no_discount, budget)
    out["insights"] = insights

    # 总结亮点
    highlights = {}
    # 涨幅最大资源
    rd = result.get("resource_diff") or {}
    growth_items = (rd.get("growth") or {}).get("items", [])
    if growth_items:
        top1 = growth_items[0]
        highlights["max_growth"] = {
            "ResourceId": top1.get("ResourceId", ""),
            "ProductName": top1.get("ProductName", ""),
            "Diff": top1.get("Diff", 0),
            "Ratio": top1.get("Ratio", 0),
        }
    # 最贵资源
    ar = result.get("analysis_resources") or {}
    top_res = ar.get("resources", [])
    if top_res:
        r0 = top_res[0]
        highlights["most_expensive"] = {
            "ResourceId": r0.get("ResourceId", ""),
            "ProductName": r0.get("ProductName", ""),
            "RealTotalCost": r0.get("RealTotalCost", 0),
        }
    # 费用最高产品（前 2 个 + 合计）
    if len(sp_curr.get("products", [])) >= 2:
        p1, p2 = sp_curr["products"][0], sp_curr["products"][1]
        highlights["top_products_summary"] = {
            "p1_name": p1.get("ProductName", ""),
            "p1_ratio": p1.get("RealTotalCostRatio", "0"),
            "p2_name": p2.get("ProductName", ""),
            "p2_ratio": p2.get("RealTotalCostRatio", "0"),
            "combined_ratio": (
                float(p1.get("RealTotalCostRatio", 0) or 0)
                + float(p2.get("RealTotalCostRatio", 0) or 0)
            ),
        }
    out["summary_highlights"] = highlights

    out["available"] = True
    return out


# ─── 主流程 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="一键准备账单分析数据")
    parser.add_argument("--account-name", type=str, default="",
                        help="账户中文名（如 一中心管控账户）")
    parser.add_argument("--uin", type=str, default="",
                        help="腾讯云 UIN（备选匹配方式）")
    parser.add_argument("--month", type=str, required=True,
                        help="分析月份 YYYYMM（如 202604）")
    parser.add_argument("--cloud", type=str, default="tencent",
                        help="云厂商：tencent|huawei|aliyun，默认 tencent")
    parser.add_argument("--project", type=str, default="",
                        help="项目码（如 ADC/TSP/YX）。必传——进程内经 "
                             "build_credential_env.resolve(cloud, project) 从 SSM 取该账号凭证"
                             "（KMS 服务端解密），规避并发竞态；缺省则返回空凭证走 need_credentials 引导（不读裸名 env）")
    parser.add_argument("--cache-base", type=str, default="",
                        help="缓存根目录（默认 <WORKSPACE>/cache/billing/{uin}）")
    parser.add_argument("--cache-only", action="store_true", default=False,
                        help="跳过凭证解析，直接从缓存加载数据（用于 worker 已拿到凭证的场景）")
    parser.add_argument("--skip-cache", action="store_true", default=False,
                        help="强制跳过缓存（忽略缓存命中判断）")
    parser.add_argument("--auto-fetch", action="store_true", default=False,
                        help="缓存空时自动调用 fetch_billing_apis.py 并发拉取（凭证已配置时）")
    parser.add_argument("--lang", type=str, default="zh", choices=["zh", "en"],
                        help="凭证缺失引导话术的语种（跟随主理人 output_lang），zh=中文 en=English")
    args = parser.parse_args()

    # ─── Step 0: 凭证 ────────────────────────
    if args.cache_only:
        # cache-only 模式：凭证由 worker 已在外层解析，只需 uin 定位缓存
        uin = args.uin
        if not uin:
            print(json.dumps({"status": "error", "error": "--cache-only 需要 --uin"}, ensure_ascii=False))
            sys.exit(1)
        creds = {"status": "ok", "display_name": args.account_name or "",
                 "uin": uin, "secret_id": "", "secret_key": "", "token": "", "budget": ""}
    else:
        creds = resolve_credentials(
            account_name=args.account_name or None,
            uin=args.uin or None,
            lang=args.lang,
            cloud=args.cloud,
            project=args.project or None,
        )

    if creds is None:
        print(json.dumps({
            "status": "need_credentials",
            "error": "未找到可用的腾讯云凭证（TENCENTCLOUD_SECRET_ID / KEY 必填）",
            "prompt": _build_need_credentials_prompt(args.lang),
        }, ensure_ascii=False))
        sys.exit(0)

    if creds.get("status") == "need_credentials":
        print(json.dumps(creds, ensure_ascii=False))
        sys.exit(0)

    # ─── 缓存路径 ────────────────────────
    uin = creds["uin"]
    # WorkBuddy 工作区缓存路径：<WORKSPACE>/cache/billing/{uin}
    default_cache_base = os.path.join(
        os.environ.get("WORKBUDDY_WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "cache", "billing", uin,
    )
    cache_base = args.cache_base or default_cache_base

    analysis_month = args.month
    comp_month = prev_month_str(analysis_month)

    skip_cache = args.skip_cache or compute_skip_cache(analysis_month)

    # ─── 结果容器 ────────────────────────
    result = {
        "status": "ok",
        "credentials": {
            "display_name": creds.get("display_name", ""),
            "uin": creds["uin"],
            "app_id": creds.get("app_id", ""),
            "auth_path": creds.get("auth_path", "direct"),
            "budget": creds.get("budget", ""),
            # 命名/落盘上下文（方案 A 后由脚本回填，agent 直接取用，禁止再读 env 拼名）
            "active_cloud": args.cloud,
            "active_project": args.project or os.environ.get("CLOUDOPS_ACTIVE_PROJECT", ""),
        },
        "analysis_month": analysis_month,
        "comparison_month": comp_month,
        "skip_cache": skip_cache,
        "cache_base": cache_base,
    }

    # ─── Step 0.5: auto-fetch（缓存空时自动并发拉取） ────
    if args.auto_fetch and not args.cache_only:
        analysis_dir = os.path.join(cache_base, analysis_month)
        comp_dir = os.path.join(cache_base, comp_month)
        analysis_empty = (not os.path.exists(analysis_dir)
                          or not os.path.exists(os.path.join(analysis_dir, "summary_product.json")))
        comp_empty = (not os.path.exists(comp_dir)
                      or not os.path.exists(os.path.join(comp_dir, "summary_product.json")))
        if analysis_empty or comp_empty:
            try:
                from fetch_billing_apis import fetch_all
                fetch_result = fetch_all(creds, cache_base, analysis_month, include_trend=True)
                result["auto_fetch_result"] = {
                    "ok": fetch_result.get("ok"),
                    "missed": fetch_result.get("missed", []),
                }
            except Exception as e:
                result["auto_fetch_error"] = str(e)

    # ─── Step 1: 汇总数据（分析月 + 对照月） ────
    analysis_data = load_summary_data(cache_base, analysis_month)
    comparison_data = load_summary_data(cache_base, comp_month)

    if analysis_data:
        result["analysis_summary"] = analysis_data
    if comparison_data:
        result["comparison_summary"] = comparison_data

    # ─── Step 1b: 合并标签数据（环比需要双月对齐） ────
    tag_raw_a = (analysis_data or {}).pop("summary_tag_raw", None)
    tag_raw_c = (comparison_data or {}).pop("summary_tag_raw", None)
    if tag_raw_a or tag_raw_c:
        result["summary_tag"] = merge_tag_data(tag_raw_a, tag_raw_c, analysis_month, comp_month)

    # ─── Step 2: 资源环比 ─────────────────
    curr_rs_path = os.path.join(cache_base, analysis_month, "resource_summary.json")
    prev_rs_path = os.path.join(cache_base, comp_month, "resource_summary.json")

    curr_resource = None
    prev_resource = None

    if os.path.exists(curr_rs_path):
        raw = load_json(curr_rs_path)
        curr_resource = summarize_resource_summary(raw, top_n=50)
        # 只输出 Top 50 + 统计，不含 all_slim（环比数据在 resource_diff 中）
        result["analysis_resources"] = {
            "total_resources": curr_resource["total_resources"],
            "total_cost": curr_resource["total_cost"],
            "top_n": curr_resource["top_n"],
            "top_cost": curr_resource["top_cost"],
            "top_coverage_pct": curr_resource["top_coverage_pct"],
            "resources": curr_resource["resources"],
        }

    if os.path.exists(prev_rs_path):
        raw = load_json(prev_rs_path)
        prev_resource = summarize_resource_summary(raw, top_n=50)
        # 对照月只输出统计（环比细节在 resource_diff 中）
        result["comparison_resources"] = {
            "total_resources": prev_resource["total_resources"],
            "total_cost": prev_resource["total_cost"],
        }

    # 环比预计算
    if curr_resource and prev_resource:
        result["resource_diff"] = compute_resource_diff(curr_resource, prev_resource)

    # ─── 趋势数据 ──────────────────────
    result["trend"] = compute_trend_data(cache_base, analysis_month)

    # ─── 缓存清单（帮助 LLM 判断哪些需要 API 拉取） ────
    cache_inventory = {}
    for yyyymm in [analysis_month, comp_month]:
        month_dir = os.path.join(cache_base, yyyymm)
        if os.path.isdir(month_dir):
            cache_inventory[yyyymm] = sorted(os.listdir(month_dir))
        else:
            cache_inventory[yyyymm] = []
    result["cache_inventory"] = cache_inventory

    # ─── 报告就绪预计算（report_ready） ─────────────────
    # 当分析月+对照月主要数据齐全时，预计算所有"展示前最后一公里"的字段，
    # 让 LLM 拿到 dict 就能逐字段渲染、零分析、零推导。
    result["report_ready"] = build_report_ready(result)

    # ─── HARD REMINDER（防 LLM context 膨胀后遗忘约束） ─────────
    # 历史根因：agent 拿到 report_ready 后不信任，开始逐字段 exec 读 JSON，
    # 44 次碎片 exec × 3.2s = 140.8s 浪费 + 60 轮耗尽 → protocol_violation。
    # 在输出最末尾（LLM 看到的最后内容）反复提醒，对抗 context 遗忘。
    if result["report_ready"].get("available"):
        result["_HARD_REMINDER"] = (
            "⚠️ report_ready.available=True → ALL DATA IS READY. "
            "DO NOT exec/Read/cat any cache JSON file. "
            "DO NOT write scripts to re-fetch or re-compute. "
            "Step 3: pipeline = extract_step3_data.py → render_billing_html.py → preview. "
            "Violation = F-EXEC-FRAG + F-EXPLORE-LOOP (HARD). "
            "See SKILL.md §⚡执行效率 HARD 约束."
        )
        result["report_ready"]["_REMINDER"] = (
            "ZERO EXPLORATION NEEDED. Pipeline: extract_step3_data.py → render_billing_html.py. "
            "No exec/Read/cat of any file. All numbers are pre-computed."
        )
        result["report_ready"]["_TOTAL_EXEC_BUDGET"] = (
            "Step 3 exec budget: ≤2 total (1 for extract_step3_data.py, "
            "1 for render_billing_html.py). NO exec for reading raw cache."
        )

    # ─── 输出 ──────────────────────────
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
