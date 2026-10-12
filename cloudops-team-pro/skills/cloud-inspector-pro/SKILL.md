---
name: cloud-inspector-pro
description: 腾讯公有云资源安全巡检(SOP TC-SEC-SOP-001 V1.0)。按用户表述分流四种 scope:【full】"巡检 {账户名}""扫一下 XX 账户""出本周报告""全面检查合不合规"等覆盖整账户/多类资源 → 走 Step 0→4 全套(4 大类×动态产品 + HTML 报告)。【narrow】"看看 SSL 有没有快过期""检查 SG 对不对""列闲置 AK"等【单一资源 + 是否…/有没有…】→ 只跑目标域,markdown 贴回对话。【narrow_forensic】"这周有没有人删 COS 桶""上周谁改了 IAM 策略"等【单一事件 + 时间窗 + 是否发生过】→ 直接调 LookUpEvents 单事件,**不**走全扫脚本,三段骨架报告。【sla_query】"🔴 严重漏洞修复要多久""AK 闲置阈值多少天"等纯问询 → 读 references 直接答,**不**加载凭证、**不**跑脚本。覆盖 IAM/SG/CVM/COS/数据库/CLB/TKE/中间件/密钥/日志/合规/漏洞 12 类资源。凭证只读环境变量 `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY`,UIN 与账号信息由 `cam.GetUserAppId` 自动派生,**不**读 `.env`、**不**用 ACCOUNT_* 别名表。
---

# cloud-inspector-pro

腾讯公有云资源安全巡检。覆盖 CAM / 云审计 / 安全组 / CVM / COS / SSL / 数据库 / CLB / TKE / 中间件 / 标签合规等;按用户表述自动分流 full / narrow / narrow_forensic / sla_query 四种 scope;**唯一凭证源是用户当前 shell 的环境变量** `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY`,UIN 和账号信息全部由本 Skill 调用 `cam.GetUserAppId` 自动获取,**不**读任何 `.env` 文件、**不**依赖 `ACCOUNT_*` 别名表。

## ⚠️ output_lang 一致性（最常见 bug · 写概览前必检）
- **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问时 `output_lang=en`，概览必须全英文；中文提问时 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。这在中文运行环境下极易发生。
- ✅ 正确做法：在写第一个字之前，默念「`output_lang` 是什么？」，然后用该语言写。
- ✅ `--lang` 参数透传给渲染脚本，确保 HTML 看板界面骨架语言一致。英文报告需执行 i18n 自动补全流程（见下方）。
- ✅ 枚举型中文名（巡检域名、风险等级、产品名）当 `output_lang=en` 时必须翻译为英文，禁止保留中文原文。

## 巡检大类索引(reference 文件清单)

| 编号 | 巡检域 | 参考文件 |
|------|--------|---------|
| 00 | 总览与巡检准备 | [references/00-overview.md](references/00-overview.md) |
| 01 | IAM / CAM 账号安全巡检 | [references/01-iam-cam.md](references/01-iam-cam.md) |
| 02 | 网络与安全组巡检 | [references/02-network-security-group.md](references/02-network-security-group.md) |
| 03 | CVM 主机安全巡检 | [references/03-cvm-host.md](references/03-cvm-host.md) |
| 04 | COS 对象存储安全巡检 | [references/04-cos-storage.md](references/04-cos-storage.md) |
| 05 | 云数据库安全巡检 | [references/05-database.md](references/05-database.md) |
| 06 | CLB 负载均衡安全巡检 | [references/06-clb.md](references/06-clb.md) |
| 07 | TKE 容器安全巡检 | [references/07-tke-container.md](references/07-tke-container.md) |
| 08 | 中间件与大数据服务安全巡检 | [references/08-middleware-bigdata.md](references/08-middleware-bigdata.md) |
| 09 | 密钥与凭据管理巡检 | [references/09-secrets-credentials.md](references/09-secrets-credentials.md) |
| 10 | 日志与监控巡检 | [references/10-logging-monitoring.md](references/10-logging-monitoring.md) |
| 11 | 合规与资产标签巡检 | [references/11-compliance-tagging.md](references/11-compliance-tagging.md) |
| 12 | 漏洞处置与应急响应 | [references/12-incident-response.md](references/12-incident-response.md) |
| 13 | 巡检报告生成 | [references/13-report-template.md](references/13-report-template.md) |
| 14 | 智能顾问 advisor 覆盖映射 | [references/14-advisor-coverage.md](references/14-advisor-coverage.md) |
| 17 | 基线/网络/安全产品/合规判定标准 | [references/17-infra-baseline.md](references/17-infra-baseline.md) |

> 上表为 **reference 文件清单**(LLM 读判定标准用),不等于报告章节结构。报告章节按下面「四大类」组织,详见 §报告章节四大类。

## 报告章节四大类(用户可见结构)

报告 §二项目详情按腾讯云控制台「巡检概览」**4 大类**横排呈现,advisor `GroupName` 字段作为路由键。**每大类下的小节由 advisor 实际命中的产品动态生成**,无命中即不出现。**advisor `group=成本` finding 整体丢弃**(由独立的成本优化 skill 承载,本报告完全不涉成本维度)。

| 大类 | 来源(GroupName) | 固定小节(强出) | 动态小节(advisor 命中即出) |
|:-----|:---------------|:---------------|:-------------------------|
| 云安全 | 安全(62 strategy) | CAM 账户安全 / 云审计 / 网络与安全组 / 网络基础设施 / CVM 主机安全 / 安全产品覆盖 / SSL 证书 / COS 对象存储 | 安全类命中的其他产品 |
| 云可靠 | 可靠(173 strategy) | — | 可靠类命中的产品 |
| 云性能 | 性能(107 strategy) | — | 性能类命中的产品 |
| 云限制 | 服务限制(82 strategy) | — | 限制类命中的产品 |
| 合规与资产标签 | 自扫补丁(billing 维度) | 标签合规扫描 / 基线配置检查 / 配置审计合规率 | — |

> **(HARD)** `GroupName` → 大类映射只走 advisor 字段,**禁止** LLM 凭直觉再分桶;护城河域(CAM/云审计/网络与安全组/CVM 主机安全/标签)强制归类,不读 advisor。
> **(HARD · 标题层级)** `## 二、{项目名} 项目详情` 是 §二外壳(2 级);**4 大类降为 3 级** `### 云安全 / ### 云可靠 / ### 云性能 / ### 云限制 / ### 合规与资产标签`;护城河/动态产品小节为 4 级 `#### {名称}`。**5 个大类词全份报告统一**,**禁止**写「安全合规/可靠性最佳实践」等同义异称。
> **(HARD)** `GroupName=成本` 的 finding 在 Step 4 报告生成时**整体丢弃**,不入任何大类,**禁止出现** `### 云成本` 大类标题;也**不写**"详见成本优化 skill"提示(F34 顾虑)。

## 凭证管理

> **唯一凭证源:SSM + KMS** —— 各云账号 AK/SK 以 KMS 加密存放在腾讯云凭据管理服务(SSM),脚本进程内 `resolve(cloud, project)` 从 SSM 在线读取并由服务端解密。宿主只需配一把 **runner key**(`TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`,仅 SSM 只读权限,无需 kms 权限)。**UIN、账号别名、AppId 全部由本 Skill 调用 `cam.GetUserAppId` 自动获取,用户不需要也不应该手填 UIN**。
>
> ❌ **禁止**读取任何 `.env` 文件 / `dotenv` / 自定义路径配置文件
> ❌ **禁止**使用 `ACCOUNT_{ALIAS}_*` 别名变量(老方案,已废弃)
> ❌ **禁止**让用户在终端 export 业务账号明文 AK/SK(账号凭证只存于 SSM)
> ❌ **禁止**通过对话直接接收 AK/SK 后写入文件(安全红线)
> ✅ 宿主在终端 `export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=... && export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=...` 配好 runner key 后(即自动启用 SSM,无需 `CLOUDOPS_SSM_ENABLED=true`),Skill 在同一进程从 SSM 取密钥并自动派生身份

### 凭证查找流程

> 🚨 **强制实测红线(违反即任务失败)**
>
> 凭证存在性判断**必须**通过 `execute_code` 真实运行 Python 后,基于 `print()` 输出做判断。**禁止**凭印象、凭历史经验直接返 `need_credentials`(2026-05-25 costopt 连续 3 次误判的根因)。

**必须执行的强制核查代码**(原样运行):

```python
import os
sid = os.environ.get("TENCENTCLOUD_SECRET_ID", "").strip()
sk = os.environ.get("TENCENTCLOUD_SECRET_KEY", "").strip()
print(f"TENCENTCLOUD_SECRET_ID: {'set (' + sid[:8] + '...)' if sid else 'MISSING'}")
print(f"TENCENTCLOUD_SECRET_KEY: {'set (****)' if sk else 'MISSING'}")
print(f"ready: {bool(sid and sk)}")
```

- 当且仅当 `sid` 或 `sk` 缺失时才允许判定环境未就绪,且要把 print 输出写入 metadata.evidence。
- `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` 都有 → 进入「派生身份」步骤,**不**再向用户索取 UIN/账号名。

### 跨账号代查:STS AssumeRole(已收敛进 SSM,自动透明)

> 跨账号代查现已**完全收敛到 `build_credential_env.resolve()` 内部**——编排脚本 `run_inspection_collectors.py` **零感知**。
> 你照常 `--project <CODE>` 运行,`resolve()` 取到该 secret 后自动判断:
> - secret 无 `role_arn` → 直连本账号。
> - secret 有 `role_arn` → `resolve()` 在**进程内**用主账号 AK 调一次 `sts.AssumeRole`,返回临时
>   `TmpSecretId/TmpSecretKey/Token`;编排脚本把临时凭证 + Token 注入各采集子进程的**私有 env 副本**
>   (`TENCENTCLOUD_TOKEN`),十几个并行采集器(scan/cam/cos/advisor/audit_events…)经 `sign.py` 自动带
>   `X-TC-Token`,COS 域的 `cos_get` 也支持 `x-cos-security-token`,**无需逐个改脚本**。
>
> **如何让某账号走跨账号代查**:在它的 SSM secret `cloudops/tencent/<CODE>` 里加 role 字段:
>
> | secret JSON 字段 | 必填 | 说明 |
> |---|---|---|
> | `role_arn` | ✅(启用时) | 目标账号角色 ARN,如 `qcs::cam::uin/100099999999:roleName/InspectorReader` |
> | `assume_from` | 推荐 | 调用方**主账号 AK** 所在 secret 的项目码(如 `PARENT`);主账号 AK 只存一份、轮换只改一处 |
> | `role_external_id` | ❌ | 角色信任策略的 ExternalId(如有) |
> | `role_duration` | ❌ | 临时凭证有效期(秒),默认 3600 |
>
> 不写 `assume_from` 时,用本 secret 内 `secret_id/secret_key`(须为能 assume 目标角色的主账号 AK)当调用方。
>
> **并发安全**:临时凭证只活在该进程栈变量 / 子进程私有 env,绝不写共享 `os.environ`;一账号一进程,fan-out 各自 assume 各自的,互不串号。AssumeRole 失败 → `resolve()` 退出码 6,编排脚本原文输出 stderr 并停止。
>
> ⚠️ 控制台「角色 SwitchRole」≠ STS AssumeRole(API);跨账号巡检**必须**先在目标账号建角色并授信调用方主账号。

### 派生身份(UIN / 账号 / AppId 自动获取)

凭证就绪后(若启用了 AssumeRole,则已是临时凭证),本 Skill 在执行任何采集前调用 `cam.GetUserAppId` 一次,把返回的 `OwnerUin` / `Uin` / `AppId` 写入进程环境变量,后续脚本/报告直接消费,**禁止**让 LLM 凭印象写死 UIN。

```python
import os, sys
# 复用 scripts/lib/sign.py 直签 HTTP,不用 tccli
# 路径基于插件目录自动解析,无需环境变量
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_PLUGIN_DIR, 'scripts/lib'))
from sign import call_api

resp = call_api(
    host="cam.tencentcloudapi.com", service="cam",
    action="GetUserAppId", version="2019-01-16", payload={},
    region="ap-guangzhou",
    secret_id=os.environ["TENCENTCLOUD_SECRET_ID"],
    secret_key=os.environ["TENCENTCLOUD_SECRET_KEY"],
)  # 注:AssumeRole 模式下,call_api 经 sign.py 的 _default_token() 自动带 TENCENTCLOUD_TOKEN
uin = str(resp.get("OwnerUin") or resp.get("Uin") or "").strip()
app_id = str(resp.get("AppId") or "").strip()
assert uin, f"GetUserAppId 未返回 UIN,凭证可能权限不足: {resp}"

os.environ["INSPECTION_UIN"] = uin
os.environ["INSPECTION_APP_ID"] = app_id
# 账号显示名:用户在 Prompt 里给出的项目名(如"TSP账号");没给就回落到 UIN
os.environ.setdefault("INSPECTION_ACCOUNT", os.environ.get("INSPECTION_ACCOUNT") or uin)
print(f"[OK] UIN={uin} AppId={app_id} Account={os.environ['INSPECTION_ACCOUNT']}", flush=True)
```

派生后供下游脚本 / 报告头使用的环境变量:
- `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` —— 腾讯云 SDK 标准字段(用户提供)
- `INSPECTION_UIN` —— 主账号 UIN(本 Skill 自动派生,**禁止**手填覆盖)
- `INSPECTION_APP_ID` —— AppId(本 Skill 自动派生)
- `INSPECTION_ACCOUNT` —— 报告头部展示名,优先用用户消息里出现的项目名(如"TSP账号""一中心管控账户"),缺省回落到 UIN

> `GetUserAppId` 调用失败时(SignatureFailure/AuthFailure)直接停止本次执行并按下面的「环境未就绪」模板提示用户重新配置 AK/SK,**不**尝试本地推断 UIN。

### 凭证缺失:环境未就绪指引(原样输出)

当强制核查代码报告 SSM 凭证后端不可用(缺少 runner key)时,**停止执行**,向客户输出以下指引(模板,逐字使用):

```markdown
环境检测显示 **凭证后端未就绪**,无法调用腾讯云 API 查询您账号下的实际云资源风险。

⚠️ **环境未就绪**

本专家团以 **SSM + KMS 作为唯一凭证源**:各云账号的 AK/SK 以 KMS 加密后存放在腾讯云凭据管理服务(SSM)里,专家运行时通过一把 **runner key** 在线读取并由 SSM 服务端解密。您**不需要**、也**不应该**在终端 export 业务账号的明文 AK/SK——只需配置这把 runner key。

要查询您云资源的真实风险数据,需要先完成以下配置:

**1. 配置 runner key(SSM 访问密钥)**

请到 [腾讯云 CAM 控制台](https://console.cloud.tencent.com/cam/capi) 获取一把子账号 SecretId/SecretKey(仅需 SSM 只读权限 `ssm:GetSecretValue`/`ListSecrets`/`ListSecretVersionIds`,**无需任何 kms 权限**——解密在 SSM 服务端由服务角色完成),然后在终端执行（按你的操作系统选一种即可）:

**Linux / macOS（bash / zsh）**

​```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="your-runner-key-id"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="your-runner-key-secret"
​```

**Windows（PowerShell，仅当前会话生效）**

​```powershell
$env:TENCENTCLOUD_BOOTSTRAP_SECRET_ID="your-runner-key-id"
$env:TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="your-runner-key-secret"
​```

**Windows（CMD，仅当前会话生效）**

​```cmd
set TENCENTCLOUD_BOOTSTRAP_SECRET_ID=your-runner-key-id
set TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=your-runner-key-secret
​```

> ✅ 配好这两行 runner key 即自动启用 SSM,无需 `CLOUDOPS_SSM_ENABLED=true`(它只是个可选急停开关,设为 false/0/no/off 才会关闭 SSM)。
> ⚠️ 必须在**启动 WorkBuddy 的终端**里设置;若是从应用图标启动,把变量写入持久化配置后**重启 WorkBuddy**：
> - macOS / Linux：写入 `~/.zshrc`（zsh）或 `~/.bashrc`（bash）
> - Windows：用 `setx TENCENTCLOUD_BOOTSTRAP_SECRET_ID "your-runner-key-id"`（写入用户环境变量，新终端生效），或在「系统属性 → 高级 → 环境变量」里图形化添加
> ❌ **不要**把业务账号 AK/SK 贴在对话框里——账号凭证只存于 SSM,runner key 也只能由您自己在终端设置,我不会代您写入任何配置文件。

**2. 一次配齐:云运维专家团权限地图**(推荐做法)

为了让账单 / 审计 / 巡检 / 降本 4 个专家都能正常工作,推荐给同一个**只读子账号**一次性授予以下策略,避免来回换密钥:

| 专家 | 关键 CAM 策略 | 说明 |
|---|---|---|
| 🛡️ **云巡检** | `QcloudAdvisorFullAccess` + `ReadOnlyAccess` | 智能顾问 + 全量资源只读 |
| 💰 **云账单** | `QcloudFinanceFullAccess` + `QcloudCamReadOnlyAccess` | 账单只读 + UIN 自动派生 |
| 📉 **云降本** | `QcloudFinanceFullAccess` + `QcloudMonitorReadOnlyAccess` + 各产品 `ReadOnly` | 账单驱动 + 30 天监控 |
| 🔍 **云审计** | `QcloudCloudAuditReadOnlyAccess` 或 `ReadOnlyAccess` | 审计日志只读 |

**最简方案**:授予子账号 `ReadOnlyAccess`(一条策略覆盖几乎所有"看"的能力)+ `QcloudFinanceFullAccess`(账单需要单独授权)即可让 4 个专家全部跑通。

**可选环境变量**(不配也能跑):
​```bash
# 云账单/降本:配置后报告头部展示预算消耗率
export TENCENTCLOUD_MONTHLY_BUDGET="50000"
# 云降本:关键资源保护标签前缀,命中即跳过判定
export TENCENTCLOUD_FINOPS_CRITICAL_TAG="finops-critical"
​```

> UIN / AppId / 账户昵称由本 Skill 调用 `cam.GetUserAppId` 自动获取,**不需要您手填**。

---

**完成配置后,云巡检会做什么**

针对"云资源有哪些风险",从以下 5 大维度全面巡检:

| 风险维度 | 巡检内容 |
|---|---|
| 🔒 **安全合规** | 安全组 0.0.0.0/0 放通、公网暴露、密钥泄露、CAM 弱权限、SSL 证书到期 |
| ⚡ **可靠性** | 单 AZ 部署、缺备份、未启用容灾、SLA 风险 |
| 🚀 **性能** | CVM/CBS 性能瓶颈、数据库慢查询、网络延迟 |
| 💰 **成本** | 闲置 CVM/CBS/EIP、利用率低资源 |
| 🚫 **服务限制** | 配额接近上限、API 限频风险 |

输出形式:架构图风险列表 → 高/中/低风险分级 → 控制台直达修复页面,可生成可视化巡检报告。

---

**请确认下一步:**
1. 是否现在配置 AK/SK?(配置完告诉我"配好了",我立即启动巡检)
2. 还是先看一份样例巡检报告了解输出格式?

🔗 [前往腾讯云控制台](https://console.cloud.tencent.com/cam/capi)(配置 CAM 密钥)
```

**行为规则:**
1. 输出上述指引后**停止本次执行**,等待用户在终端 `export` AK/SK 后再次触发巡检
2. **禁止**通过对话直接接收用户粘贴的 AK/SK 后写入文件 —— 凭证必须由用户在终端 `export`
3. **禁止**输出"贴过来我帮你 export"、"密钥不会落盘""我帮你配置进会话"等任何形式的诱导式凭证收集话术
4. 用户重新发指令后,从「凭证查找流程」重新开始

### 更新 runner key

❌ **Skill 不得**修改用户 shell 环境或写入任何持久化凭证文件
✅ 更新流程:用户自己在终端重新设置 runner key（Linux/macOS 用 `export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`，Windows PowerShell 用 `$env:TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`，CMD 用 `set TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`），然后重新触发巡检;同一进程内的下次执行自动读到新值。业务账号 AK/SK 的增改在 SSM 控制台维护,不经终端

---

## 使用方式

> 路由总则:LLM **在收到用户消息的第一回合**根据 prompt 语义自行判定 scope,然后选 §下面对应的子节执行。**不要**问用户"是要全量还是单域",**不要**假设需要参数/配置。判定完后**必须先 `export REPORT_SCOPE=full|narrow|narrow_forensic|skipped`** 给 Step 4 grep 自检用(skipped 用于 sla_query,跳过 grep)。

### 全量巡检(scope=full)

用户说"巡检 {账户名}"、"帮我扫一下 XX 账户"、"给 YYY 账户出一份巡检报告"、"出本周巡检报告"、"全面检查一下配置合不合规"等覆盖整账户/多类资源的表述时,**先 `export REPORT_SCOPE=full`**,然后走下面 §巡检执行流程的固定五段:

1. `run_inspection_collectors.py` 一条命令完成 Step 0/1/2/2.6:加载凭证、拉地域、并发执行 scan/advisor/infra/CAM/COS/audit_events/标签扫描,落 `collect_summary.json`。
2. `generate_inspector_report.py` 一条命令生成基础 `report.md` / `report_doc.md` / `summary.json`。
3. `generate_inspector_customer_report.py` 一条命令生成**客户版 8 页结构** `inspector_doc_customer.md`:巡检概览 / 云安全 / 云可靠 / 云性能 / 云限制 / 资产标签治理 / 数据完整性与复核项 / 整改推进计划（**P8 整改推进计划固定 6 个 H3 子节：整体思路 / 阶段计划 / 时限分发 / 责任协同 / 复核指标 / 复核范围**；时限分发下保留 🔴≤2h / 🟠≤24h / 🟡≤7d 三个 H4 bullets，由 `build_action_list` 注入实际数据）。
4. `preflight_inspector_report.py` 硬校验客户版报告；失败即停止,禁止发布。

🚨 **性能红线**:full 巡检禁止 LLM 现场写 `generate_report.py`、禁止 heredoc 拼整篇报告、禁止逐 reference 手写报告。2026-05-25 实测现场写报告会把正式巡检从 ~1 分钟采集拖到 10 分钟级；固定脚本路径本地 smoke test 生成 86KB 文档 + 预检为秒级。

### 单域查询(scope=narrow)

用户说"看看 YYY 的 SSL 证书有没有快到期"、"检查一下 XX 账户安全组对不对"、"看下 COS 桶里有没有公有读"、"列一下闲置 AK"等【单一资源类型 + 是否…/有没有…/列一下…】表述时,**先 `export REPORT_SCOPE=narrow`**,然后按下面流程:

1. **只加载凭证 + 只跑该域所需的脚本/API**(常见映射):

   | 用户语义 | 该跑的脚本 / API | 落盘文件(若已存在则 Read 复用) |
   |---|---|---|
   | SSL 证书到期 | 直接调 `DescribeCertificates`(`ssl.tencentcloudapi.com` / ap-shanghai) | `inspect_ssl.json` |
   | 安全组对不对 / SG 端口 | 直接调 `DescribeSecurityGroups` + `DescribeSecurityGroupPolicies` | `inspect_sg_rules.json` |
   | COS 公有读 / 桶清单 | `inspect_cos.py` | `inspect_cos.json` |
   | CWP / Agent / 暴破 | 直接调 `cwp.DescribeMachines` + `DescribeBruteAttacks` | `inspect_cwp.json` |
   | CAM / IAM / AK / MFA | `inspect_cam.py` | `inspect_cam.json` |
   | 标签合规 | Step 2.6 prompt(billing API) | `inspect_resource_tags.json` |
   | CloudAudit 跟踪集 | 直接调 `cloudaudit.DescribeAuditTracks` | `inspect_cloudaudit.json` |

   > 实操:用户只问 SSL 时**不要**跑整个 `inspect_scan.py`(其他 4 块白扫一轮 SG/VPC/CWP/CloudAudit ~15s);LLM **直接用 Step 0 的 sign.py 模板调一次目标 API**,把结果存盘 `$OUT_DIR/inspect_<域>.json` 即可。**当且仅当**该域 inspect_*.json 已落盘且 ≤30 分钟,才直接 Read 复用。

2. 按 [`references/15-narrow-query-paths.md`](references/15-narrow-query-paths.md) 对应章节(§1 SSL / §2 SG / §4 CWP / …)的轻量模板写报告,落盘 `$OUT_DIR/report.md` —— **不**走 13 模板的 4 大类×动态产品全骨架。
3. **默认不创建在线文档**;LLM 直接把 markdown 内容贴回对话给用户。
4. 用户**显式**说"出报告/创腾讯文档/归档"才走 §4.2 的在线文档创建。
5. 报告 §扫描范围与边界一节**必出**,显式列出"未覆盖的 11 个域,如需全面巡检请说'巡检 {账户名}'"——避免用户误判"扫完了"。

### 应急取证窄查询(scope=narrow_forensic)

用户说"这周有没有人删 COS 桶"、"最近有没有 AK 被新建"、"上周谁改了 IAM 策略"、"销毁过 CVM 没"等【单一事件类型 + 时间窗 + 是否发生过】表述时,**先 `export REPORT_SCOPE=narrow_forensic`**,然后按下面流程:

1. **不要跑 `inspect_audit_events.py`**(默认扫 43 EventName 全量,窄查只关心 1-2 个,把 43 扫一遍是 ~30s 浪费 + LLM context 噪声)。
2. **直接用 Step 0 的 sign.py 模板调** `cloudaudit.tencentcloudapi.com / LookUpEvents`,关键参数:
   - `LookupAttributes=[{"AttributeKey":"EventName","AttributeValue":"<EventName>"}]`
   - `StartTime` / `EndTime` Unix 时间戳(默认最近 7 天;用户说"上周/这个月/最近 X 天"再换算)
   - `MaxResults=50`,有 `NextToken` 翻一两次即可
   - `Region=ap-guangzhou`(CloudAudit 全局服务,任意 region 一致)
3. 按 [`references/15-narrow-query-paths.md`](references/15-narrow-query-paths.md) §3(COS 删除取证)/ §4(CWP 入侵取证)/ §5(targeted forensics 通用骨架)写报告:**结论 + 明细 + 扫描可信度**三段,不套 4 大类骨架。
4. 默认行为同 scope=narrow:**不**创建在线文档,markdown 贴回对话(用户显式触发才创)。
5. 常见 EventName 速查(避免 LLM 拼错):

   | 用户语义 | EventName | 类别 |
   |---|---|---|
   | 删 COS 桶 | `DeleteBucket` | 资源毁灭 |
   | 销毁 CVM | `TerminateInstances` | 资源毁灭 |
   | 新建 AK | `CreateAccessKey` | AK 生命周期 |
   | 删 AK | `DeleteAccessKey` | AK 生命周期 |
   | 改 IAM 策略 / 授权 | `AttachUserPolicy` / `AttachRolePolicy` / `CreatePolicy` | 权限授予 |
   | 解绑 MFA | `UnbindMFADevice` | MFA 变更 |
   | 删审计跟踪集 | `DeleteAuditTrack` / `StopLogging` | 审计反取证 |

   完整 43 EventName 清单见 `scripts/inspect_audit_events.py`,窄查只取其中 1-2 个,**不要**全扫。

### 查询某项标准(scope=sla_query,不触发任何巡检)

用户问"🔴 严重漏洞 SOP 要求多久内修复"、"AK 闲置阈值是多少天"、"安全组高危端口判定标准是什么"等纯问询时,**先 `export REPORT_SCOPE=skipped`**(跳过 Step 4 grep),然后:

1. 直接读对应 [`references/XX-xxx.md`](references/) 文件中的「判定标准」/「响应时限」表
2. 把答案直接写回对话,**不要**加载凭证、**不要**跑任何脚本、**不要**生成 markdown 报告

### 报告输出形态分流(汇总)

| scope | 报告形态 | 落盘 | 在线文档 |
|---|---|---|---|
| full | 4 大类×动态产品完整报告(13 模板硬骨架) | `$OUT_DIR/report.md` + `report_doc.md` | 用户显式说才创 |
| narrow | 单域轻量模板(15 模板 §1-§4) | `$OUT_DIR/report.md` | 用户显式说才创 |
| narrow_forensic | 结论+明细+可信度三段(15 模板 §3/§4/§5) | `$OUT_DIR/report.md` | 用户显式说才创 |
| sla_query | 直接对话回答 | (无) | ❌ |

---

## 巡检执行流程

> 当用户说"巡检 {账户名}"时,按 Step 0 → Step 4 流程跑完一轮。

### 三条性能底线

> 下面三条是冷启动 / 串行 / context 膨胀三个瓶颈的硬约束,违反任何一条都会触发 900s 网关超时(2026-05-11 历史教训)。

1. **`sign.py` 直签 HTTP,不用 `tccli` 子进程**(每条 tccli 冷启动 0.5-1s × 几百次累计几分钟)。复用方式:在脚本目录下 `sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts/lib'))` + `from sign import call_api`。**(注:inspector 插件自带 `scripts/lib/sign.py`)**
2. **`ThreadPoolExecutor(max_workers=8)` 并发,不要 `for region in regions:` 串行**。8 路是 20 QPS 主账号上限的安全值,调高只触发限流。
3. **大结果落盘 `$OUT_DIR/inspect_<key>.json`,stdout 只 print summary**。明细全量 print 回 context 直接拖死 LLM 推理时间。
4. **❗ 账户隔离:落盘走 `/tmp/inspect_$INSPECTION_UIN/`,禁止裸写 `/tmp/`**(2026-05-11 session `f2b042fd` 双账户并发互相覆盖 → 900s 超时)。各 `inspect_*.py` 已用 `$INSPECT_OUT_DIR` / `$INSPECTION_UIN` 自动解析子目录,**不要手写 `/tmp/inspect_xxx.json`**。

### exec 超时阈值

所有 `python3 scripts/inspect_*.py` 的 exec 调用必须显式 **`timeout: 300`** 秒。WorkBuddy 默认 timeout 短,正常 17s 完成的脚本会被 auto-background,LLM `process poll` 每次浪费 120s。脚本本身都不会超过 300s。

### Step 0-2.6 · 固定采集入口(一条 terminal 命令,`timeout: 600`)

> 2026-05-25 性能收敛:full 巡检不再让 LLM 分多轮编排 Step0/1/2/2.6。统一调用固定入口,脚本内部完成凭证加载、地域探测、7 个采集器并发、标签账单扫描和耗时汇总。

> **路径约定**:以下命令中的脚本路径使用本插件内相对路径。LLM 执行时应根据实际插件安装位置解析:
> - `INSPECTOR_SCRIPTS` = 本 skill 的 `scripts/` 目录(即 SKILL.md 同级的 `scripts/`)
> - `INSPECTOR_REPORTS` = `/tmp/inspect_$INSPECTION_UIN/reports`(账户隔离的输出根目录)

```bash
python3 $INSPECTOR_SCRIPTS/run_inspection_collectors.py \
  --cloud tencent --project <CODE> \   # 必传：进程内 resolve(cloud, project) 从 SSM 取该账号凭证(KMS 服务端解密)，规避竞态
  --account-name "{用户输入的账户名}" \
  --out-dir "$INSPECT_OUT_DIR" \
  --timeout 300
# 凭证唯一来源是 SSM，宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY)；--project 必传，无"省略读裸名"回退
```

**输入约定**:
- `--cloud` / `--project`:并发隔离键 + SSM 定位键。`--project` 非空 → 进程内 `resolve(cloud, project)` 从 **SSM** 取该账号专属凭证(KMS 服务端解密)，注入给采集子进程的私有 env 副本（多账号并发时各 worker 互不覆盖）。由主理人下发的 `[Account context]` 透传。**必传**——SSM 按 `cloud/project` 定位密钥,不存在"省略 → 读裸名"的回退路径。
- `--account-name`:用户在 Prompt 里给出的项目/账号显示名(如"TSP账号""一中心管控账户"),仅用于报告头展示;**不**做 ALIAS 反查、**不**用来定位凭证。
- `--out-dir`:建议使用账户隔离目录,如 `/tmp/inspect_<uin>`。
- 凭证：`resolve(cloud, project)` 从 **SSM** 进程内解析(KMS 服务端解密,不污染共享 os.environ);凭证经子进程私有 env 注入采集器。宿主侧只需 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`(仅 SSM 只读权限,无需 kms)。UIN 由本脚本调用 `cam.GetUserAppId` 自动获取并写入 `INSPECTION_UIN`;脚本不打印 AK/SK。

**内部并发采集器**:

| 采集项 | 原脚本/逻辑 | 产物 |
|---|---|---|
| 地域/元数据 | `DescribeRegions` | `inspect_meta.json` |
| 护城河自扫 | `inspect_scan.py` | `inspect_sg_rules.json` / `inspect_cwp.json` / `inspect_ssl.json` / `inspect_cloudaudit.json` / `inspect_vpc_cross.json` |
| 云端策略 | `inspect_advisor.py` | `inspect_advisor.json` / `inspect_advisor_digest.json` |
| 基线/网络/安全产品/配置审计 | `inspect_infra.py` | `inspect_infra.json` |
| CAM | `inspect_cam.py` | `inspect_cam.json` |
| COS | `inspect_cos.py` | `inspect_cos.json` |
| 高危事件流 | `inspect_audit_events.py` | `inspect_audit_events.json` |
| 资产标签治理 | billing `DescribeBillResourceSummary` × 3 | `inspect_resource_tags.json` |

**输出**:
- `collect_summary.json`:每个采集器 `status/elapsed_s/stdout_tail/stderr_tail`,用于定位慢点。
- stdout 只输出简短 JSON summary,不要把明细 print 回 LLM context。

**失败策略**:
- `collect_summary.status=ok`:继续报告生成。
- `partial`:允许继续生成,但报告降级说明必须出现；若核心文件 `inspect_meta.json` 或 `inspect_advisor.json` 缺失则停止。
- 凭证缺失/账号别名缺失:结构化报错,停止。

### Step 3 · 可选窄补查(默认跳过)

full 巡检默认不做现场补查。只有用户明确要求跨数据集交叉核实时才跑,且必须把结果落盘为新的 `inspect_<domain>.json`。禁止为了修报告字段临时扫腾讯云 API；正式报告字段缺失走降级/复核项。

### Step 4 · 固定报告生成 + 客户版硬预检 + 发布

> 🚨 full 巡检报告生成必须调用固定脚本,禁止 LLM 现场写 `generate_report.py`。正式交付必须生成客户版报告,不能直接发布基础版 `report_doc.md`。

```bash
# ⚠️ 必须传 --lang（zh 或 en）；英文报告时 --lang en + export OUTPUT_LANG=en
export OUTPUT_LANG=en   # 英文时设置；中文时省略或 export OUTPUT_LANG=zh

python3 $INSPECTOR_SCRIPTS/generate_inspector_report.py \
  --input-dir "$INSPECT_OUT_DIR" \
  --archive-dir "$INSPECTOR_REPORTS/{账户名}/{YYYY-MM-DD}" \
  --date "{YYYY-MM-DD}" \
  --lang "$OUTPUT_LANG"

python3 $INSPECTOR_SCRIPTS/generate_inspector_customer_report.py \
  --input-dir "$INSPECT_OUT_DIR" \
  --output "$INSPECTOR_REPORTS/{账户名}/{YYYY-MM-DD}/inspector_doc_customer.md" \
  --date "{YYYY-MM-DD}" \
  --lang "$OUTPUT_LANG"
```

**⚠️ 英文报告 i18n 自动补全流程（当 `--lang en` 时必须执行）:**

报告生成后，静态字典无法覆盖每个客户账号的动态数据（advisor 规则名/整改建议/产品名等会因账号而异）。
`lib_report_translate.py` 会在翻译后自动提取残留中文到 `~/.cache/cloudops-inspector/untranslated.json`。
Agent 必须执行以下补全流程：

```bash
# Step 1：检查是否有未翻译的残留中文
python3 $INSPECTOR_SCRIPTS/update_translate_cache.py --show-untranslated

# 如果输出 "Translatable: 0"，说明翻译完整，跳到预检。
# 如果输出 "Translatable: N"，N > 0，继续 Step 2。

# Step 2：Agent LLM 读取 untranslated.json，翻译所有 skip=false 的条目
# （skip=true 的是用户自定义资源名，如实例名/安全组名/标签值，不应翻译）
# Agent 生成一个 JSON 文件 {中文: English}，例如：
# {
#   "容器服务（TKE）托管集群资源对象数过多": "TKE managed cluster has too many resource objects",
#   "建议开启日志投递服务": "Enable log delivery service"
# }
# 然后写入缓存：

python3 $INSPECTOR_SCRIPTS/update_translate_cache.py --from-file /path/to/agent_translations.json

# Step 3：用更新后的缓存重新生成报告（缓存会自动应用）
python3 $INSPECTOR_SCRIPTS/generate_inspector_report.py \
  --input-dir "$INSPECT_OUT_DIR" \
  --archive-dir "$INSPECTOR_REPORTS/{账户名}/{YYYY-MM-DD}" \
  --date "{YYYY-MM-DD}" \
  --lang "$OUTPUT_LANG"

python3 $INSPECTOR_SCRIPTS/generate_inspector_customer_report.py \
  --input-dir "$INSPECT_OUT_DIR" \
  --output "$INSPECTOR_REPORTS/{账户名}/{YYYY-MM-DD}/inspector_doc_customer.md" \
  --date "{YYYY-MM-DD}" \
  --lang "$OUTPUT_LANG"

# Step 4：再次检查——通常缓存补全后残留中文应为 0（或仅剩用户自定义名）
python3 $INSPECTOR_SCRIPTS/update_translate_cache.py --show-untranslated
```

**缓存机制说明**：
- 翻译缓存持久化在 `~/.cache/cloudops-inspector/translate_cache.json`
- 跨账号复用：TMD 规则名和整改建议在不同账号间高度重叠，第一个账号的翻译会自动惠及后续账号
- 缓存与静态字典分层：字典优先（术语一致性），缓存兜底（覆盖面）
- 新账号首次运行可能需要一轮 LLM 补全（~1分钟），后续运行直接命中缓存，零延迟

固定产物:
- `$INSPECT_OUT_DIR/report.md`:基础完整原始报告；
- `$INSPECT_OUT_DIR/report_doc.md`:基础报告,仅供 customer generator 输入,**不得直接发布**；
- `$INSPECT_OUT_DIR/summary.json`:结构化交付元数据；
- `inspector_doc_customer.md`:**正式客户版报告内容源**。

随后必须对客户版执行硬预检:

```bash
python3 $INSPECTOR_SCRIPTS/preflight_inspector_report.py \
  "$INSPECTOR_REPORTS/{账户名}/{YYYY-MM-DD}/inspector_doc_customer.md"
```

预检 fail 时**禁止创建在线文档或 HTML 报告**,先修脚本或数据源,不得绕过。当前拦截项:
- AK/SK/Secret/Token 明文或字段名；
- 内部词 `advisor/strategy/costopt/narrow/智能顾问`；
- Markdown 表格行间空行；
- 明细截断标记 `... / … / 另 N 个 / 前 N 项 / Top N`；
- `资源 ID/实例 ID/安全组 ID/证书 ID/桶名/UIN` 等定位列为空、`—`、`-`、`N/A`；
- 云成本章节误入；
- 云审计如 `inspect_audit_events.json.total_events > 0`,必须输出「高危事件流总览」和「高危事件明细」两块,明细至少包含时间/类别/EventName/用户/来源 IP/地域/资源/错误码/RequestId；只给汇总视为失败；
- 云端策略/CSC/CWPP/CFW 类明细的可定位字段不只看 `InstanceId/ResourceId`,还必须识别 `PriId/pri_id`、`Vip/vip`、桶名、域名/IP 等策略侧定位字段；不能因字段名不同误判为“未返回明细”；
- 只要智能顾问/云端策略存在公网/外网/互联网/未开启防护类命中,云安全章节必须输出「公网云资源清单」,清单主体必须来自 `DescribeTaskStrategyRisks` 返回的风险资源明细（`PriId/pri_id`、域名/IP、资源 ID 等）,并展示 EIP/CLB/CVM/数据库/Redis/MongoDB/ES/EMR/域名/IP 等云资源本身；安全组只能作为证据或辅助章节,严禁把 `sg-*` 当作公网云资源；不得用资源名/标签名启发式猜测公网暴露作为主清单来源。
  - 客户正文必须使用客户可读表头: `风险/分类/资源 ID/名称/地域/公网入口/风险项/发现来源/建议动作`；不得出现“证据来源”“策略接口”“不把 sg-* 当作公网云资源”等工程解释。
  - CFW/CSC 只能作为「发现来源」或「风险项」,不能作为公网资源「分类」；`cfw_lb-xxx_IP` 必须拆为 `资源 ID=lb-xxx`、`公网入口=IP`、`分类=负载均衡 CLB / 公网入口`; `cfw_ins-xxx_IP` 必须拆为 `资源 ID=ins-xxx`、`公网入口=IP`、`分类=云服务器 CVM / 公网主机`。
- 客户版内容源不得包含 raw `scan_status`、`项目详情` 等内部/填充口径；
- 数据完整性复核项必须聚合,不得出现同一表格行重复刷屏。

### Step 4.2 · 报告发布

🚨 **交付闭环硬规则**:full 巡检完成后必须交付 HTML 报告。

**产物 A — HTML 报告(深色玻璃拟态风格)**:
- 将 `inspector_doc_customer.md` 完整内容转为 HTML 网页
- 输出路径: `reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/inspector-{cloud}-{project}-{uin}.html`（`{cloud}`/`{project}` 取自 `CLOUDOPS_ACTIVE_CLOUD/PROJECT`，`{uin}` 取自 `cam.GetUserAppId`）

> **文件名约定（v4 自描述文件名）**：报告落盘到 `reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/inspector-{cloud}-{project}-{uin}.html`，文件名自带 cloud/project/uin 三元组，下载后无需依赖目录结构即可识别归属账号。
>
> - `{cloud}`：云厂商码（`tencent`），取 `CLOUDOPS_ACTIVE_CLOUD`
> - `{project}`：项目码，取 `CLOUDOPS_ACTIVE_PROJECT`（由 `run_inspection_collectors.py --project <CODE>` 注入）
> - `{uin}`：账号 UIN，取自 `cam.GetUserAppId` 返回值
> - **未传 `--project`（单账号旧入口）时**：退化为 `reports/{uin}/{YYYY-MM-DD}/inspector-{uin}.html`（不带 cloud/project 分桶）
> - **不再用** `{display_name}` 昵称或 `uin{uin}` 前缀命名；**不再用**裸名 `inspector.html` / `inspector_report.html`（下载后无法区分账号）
- 🎨 **配色与视觉必须与「腾讯云账单分析师」(tencent-billing-analyst) 看板完全一致** — 深色玻璃拟态(dark glassmorphism),让用户在两个专家间切换时观感统一。**严禁**沿用旧的浅色/白底/纯蓝 Header 风格。

  **① CSS 变量(原样照搬,禁止改值):**
  ```css
  :root {
    --bg-0: #0b1020; --bg-1: #111933; --line: rgba(120,144,200,0.16);
    --text-0: #e7ecff; --text-1: #aab4dc; --text-2: #6f7aa8;
    --accent: #5b8cff; --accent-2: #8b5cff;
    --good: #36d399; --warn: #f5b455; --bad: #ef4f6f;
    --shadow: 0 8px 32px rgba(0,0,0,0.32);
  }
  ```

  **② 页面底色(原样照搬):**
  ```css
  html, body { margin: 0; padding: 0; background:
    radial-gradient(1200px 600px at 10% -10%, rgba(91,140,255,0.18), transparent 60%),
    radial-gradient(900px 500px at 110% 10%, rgba(139,92,255,0.16), transparent 60%),
    var(--bg-0); color: var(--text-0);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei",
      "Helvetica Neue", Arial, sans-serif; font-size: 14px; line-height: 1.55; }
  .wrap { max-width: 1320px; margin: 0 auto; padding: 24px 28px 80px; }
  ```

  **③ 结构与组件(套用账单分析师的类与样式):**
  - **报告头(`.hero`)**:替代旧"渐变蓝 Header"。半透明渐变玻璃卡 `linear-gradient(135deg, rgba(91,140,255,0.12), rgba(139,92,255,0.10))` + `border:1px solid var(--line)` + `border-radius:16px` + `backdrop-filter:blur(8px)` + `box-shadow:var(--shadow)`;内含账户名(`h1` 22px/700)、UIN/日期/地域/采集耗时(`.sub` 13px/`--text-1`),右上角可用 `.tag` 胶囊(`border-radius:999px`,`rgba(91,140,255,0.18)`/文字 `#b8caff`)。
  - **四宫格统计卡(`.kpis` + `.kpi`)**:替代旧 `.stats-grid`。`grid-template-columns:repeat(4,1fr)`;卡片 `background:rgba(255,255,255,0.04)` + `border:1px solid var(--line)` + `border-radius:12px`,右上角 `::after` 径向高光;🔴严重→ `--bad`、🟠高→ `--warn`、🟡中→ `--accent`、📋标签治理→ `--good` 着色数值。`.kpi .value` 26px/700。
  - **🚨 网格目录(`.toc`)— 强制必须包含,缺一律视为残缺,不得发布**:
    - 位置:`.hero` 之后、`.kpis` 之前(若同页有 KPI),或 `.hero` 之后第一块。
    - 样式:`position:sticky;top:0;z-index:10` + grid + 玻璃卡(`rgba(255,255,255,0.04)` + `--line` 描边),hover 浮起 `translateY(-2px)` + `box-shadow:0 6px 20px rgba(91,140,255,0.15)`,点击平滑跳转。
    - **目录维度必须是「客户版 8 章 SOP 维度」,严禁改成「按云产品的细分目录」**(如 IAM / 审计 / 网络 / CVM / COS / 数据库 / CLB / TKE / 中间件 / SSL …)。产品维度归到「云安全」内部,**不**升级到顶层目录。
    - **目录条目固定 8 项,顺序固定,文案固定**(不允许增删/换序/改文案):
      1. 📋 巡检概览 → `#巡检概览`
      2. 🔒 云安全 → `#云安全`
      3. ⚡ 云可靠 → `#云可靠`
      4. 🚀 云性能 → `#云性能`
      5. 🚫 云限制 → `#云限制`
      6. 🏷️ 资产标签治理 → `#资产标签治理`
      7. 🔍 数据完整性与复核项 → `#数据完整性与复核项`
      8. 🧭 整改推进计划 → `#整改推进计划`
    - 锚点 id 必须与上述 8 个章节 H2 的 `id` **逐字对应**(用中文 id,不要换成 `#sec-1` 这种数字代号 — 数字代号不可读、复制链接不友好;`section`/`div.card` 容器都行,但 id 必须挂在容器上,不要漏)。
    - **现成 HTML 片段(原样复用,不要自创变体)**:

      ```html
      <!-- CSS 片段 -->
      <style>
      .toc { position:sticky; top:0; z-index:10; display:grid;
        grid-template-columns:repeat(8,1fr); gap:8px;
        padding:12px; margin:16px 0 18px;
        background:rgba(11,16,32,0.85); backdrop-filter:blur(10px);
        border:1px solid var(--line); border-radius:12px; }
      .toc a { display:block; text-align:center; padding:8px 4px;
        background:rgba(255,255,255,0.04); border:1px solid var(--line);
        border-radius:8px; text-decoration:none; color:var(--text-1);
        font-size:12px; transition:all .15s ease; }
      .toc a:hover { transform:translateY(-2px);
        background:rgba(91,140,255,0.12); color:var(--text-0);
        box-shadow:0 6px 20px rgba(91,140,255,0.15); }
      @media (max-width:980px) { .toc { grid-template-columns:repeat(4,1fr); } }
      @media (max-width:600px) { .toc { grid-template-columns:repeat(2,1fr); } }
      html { scroll-behavior:smooth; }
      </style>

      <!-- HTML 片段 -->
      <nav class="toc">
        <a href="#巡检概览">📋 巡检概览</a>
        <a href="#云安全">🔒 云安全</a>
        <a href="#云可靠">⚡ 云可靠</a>
        <a href="#云性能">🚀 云性能</a>
        <a href="#云限制">🚫 云限制</a>
        <a href="#资产标签治理">🏷️ 资产标签治理</a>
        <a href="#数据完整性与复核项">🔍 数据完整性与复核项</a>
        <a href="#整改推进计划">🧭 整改推进计划</a>
      </nav>

      <!-- 每个章节卡的 id 必须挂上,这是锚点能跳的前提 -->
      <section class="card" id="巡检概览"><h2>巡检概览</h2>...</section>
      <section class="card" id="云安全"><h2>云安全</h2>...</section>
      <!-- 以下 6 节同理,id 严格用上述中文 -->
      ```
    - **🚫 严禁**:
      - ❌ 不要再生成名为 `inspector_dashboard.html` / `inspector_overview.html` 的"看板版"附加文件(2026-06-09 实测出现过把目录改成 16 项产品维度的偏离),全量交付物只允许 1 个 `inspector-{cloud}-{project}-{uin}.html`,目录就是 SOP 8 章。
      - ❌ 不要把目录条目换成 `#sec-1`、`#section-1` 这类数字代号(过去 Step 4.2 实测出现过,虽能跳但不可读)。
      - ❌ 不要把云安全展开成「IAM / 审计 / 网络 / CVM / COS / 数据库 / CLB / TKE …」放到顶层目录(那是 Step 4.1 §二.1 章节内部的 H3/H4 层级,不是顶层 TOC 维度)。
      - ❌ 不要省略目录块(2026-06 实测残缺,直接判残);也不要把目录写成 `<ul>/<li>` 普通列表(必须是 `.toc` 玻璃卡 grid)。
  - **章节卡(每个 H2 → `.card`)**:`background:rgba(255,255,255,0.04)` + `border:1px solid var(--line)` + `border-radius:14px` + `padding:18px 20px` + `box-shadow:var(--shadow)`;标题 `h2` 15px/前置 `::before` 3px×14px `--accent` 竖条。
  - **表格**:`border-collapse:collapse`;表头 `th` `--text-2`/12px/`position:sticky;top:0`/`background:rgba(255,255,255,0.02)`;`tbody tr:hover { background:rgba(91,140,255,0.06) }`;长表用 `.scroll-y`(`max-height:460px;overflow:auto`)。
  - **风险着色(沿用类名,改为深色令牌)**:`risk-critical`→ `--bad(#ef4f6f)`、`risk-high`→ `--warn(#f5b455)`、`risk-medium`→ `--accent(#5b8cff)`、`risk-pass`→ `--good(#36d399)`、`risk-warn`→ `--warn`;胶囊标签参考 `.pill`(`border-radius:999px` + 同色 16% 底 + 32% 描边)。
  - **引用块(blockquote)**:左边框改 `--accent` 3px,底色 `rgba(91,140,255,0.06)`。
  - **页脚(`footer`)**:`--text-2`/11.5px/居中,含生成时间 + SOP 版本 + 下次复查日期。
  - **响应式**:`@media (max-width:980px)` 时 `.kpis` 与目录 grid 降为 2 列/1 列。

- Markdown → HTML 转换: H2 开新 `.card`, 表格包 `.scroll-y`, blockquote `--accent` 左边框, 风险符号(🔴🟠🟡✅⚠️)自动映射到上述 risk-* 类着色。
- 交付顺序：**先**在回复框输出 Markdown 智能概览（见下方产物 B），**再**用 `present_files` 打开 HTML 报告，确保概览不被预览面板遮挡

**🚦 HTML 报告发布前自检清单(任一不通过必须就地修补,不允许"先发后改"):**
1. `<nav class="toc">` 块存在,且包含 8 个 `<a href="#锚点">`(见上方目录强制规则)。
2. 8 个 `href="#X"` 的 X 都能在文档里找到对应的 `id="X"`(用 grep `id="巡检概览"` 等逐个核)。
3. 文件名是 `inspector-{cloud}-{project}-{uin}.html`,**不**是 `inspector_dashboard.html` / `inspector_overview.html` / `inspector_report.html` / `inspector.html` 这种无法区分账号的派生名。
4. 不存在第二个 HTML 副本(过去出现过同时落 `inspector_report.html` 与 `inspector_dashboard.html` 的情况,目录维度还不一致,直接误导用户)。

**HTML 报告交付后才算巡检闭环**。

**产物 B — 回复框 AIOps 智能概览(必交,与 HTML 报告并列)**:

🚨 **硬约束（交付顺序：先概览、后 HTML）**:同一轮回复中,**必须先**在文字回复区(回复框)输出一段 Markdown 智能概览,**再**通过 `present_files` 打开 HTML 看板,确保概览不被预览面板遮挡、让用户不点开 HTML 也能掌握关键发现。**严禁**只贴 HTML 链接就收尾、严禁"详见看板"一句话打发，也**严禁**先弹 HTML 预览把概览文字挤到下方/遮挡。

📐 **格式规范**:
- 长度 **≤600 字**(参考账单/降本概览口径,严控冗长)
- 以 `## 🛡️ 巡检智能概览` 开头(emoji + 二级标题,与账单 `## 🔍`、审计开头风格一致)
- 五段式结构(段落顺序固定):

  ① **巡检窗口与评分** — 账户名 + UIN + 巡检日期 + 4 大类总扫描产品数 + 安全评分(若有)/ 总 finding 数;
  ② **🔴 严重 / 🟠 高危 TOP 发现** — 列 3-5 条最关键 finding,每条一行(域 / 资源 ID / 一句话风险描述 / 处置时限),不展开长表;
  ③ **域级健康度** — 4 大类(IAM / 网络与安全 / 资源配置 / 日志合规)各一行结论(✅ / 🟡 / 🟠 / 🔴 + 命中 N 项 + 一句话定性),覆盖护城河 CAM / 云审计 / 标签合规;
  ④ **快速判断信号** — 1-3 条关键风险信号(如:Root AK 仍在用 / MFA 未全员开 / 公网暴露资源 N 个 / 跟踪集子账号身份未授权 等),用 ⚠️ 引导;
  ⑤ **行动建议** — 表格列出优先级(🔴/🟠/🟡)+ 行动项 + 处置时限,3-5 行,不超 5 行。

🚫 **回复框严禁项**:
- 严禁贴大段 Markdown 表格(明细全部在 HTML 看板里)
- 严禁输出任何 `AccessKeyId / SecretId / SecretKey`(SOUL §五 L70,即使脱敏/截断也不行)
- 严禁"详见看板/详见报告"一句话打发,概览本身必须自洽可读
- 严禁堆砌"巡检完成"等空话,直接进数据/结论

✅ **回复框文字概览模板示例**:
```
## 🛡️ 巡检智能概览

**账户**: {账户名}-{UIN} | **巡检日期**: {YYYY-MM-DD} | **覆盖产品**: 12 域 / N 产品
当前安全评分 **{score}/100**,命中 finding 共 {N} 项(🔴 严重 X / 🟠 高 Y / 🟡 中 Z)

### 🔴 TOP 发现
| 域 | 资源 | 风险 | 时限 |
|---|---|---|---|
| IAM | Root AK | 仍在使用,3 天内未轮换 | 24h |
| 网络 | sg-xxx | 0.0.0.0/0 全开 22/3389 | 24h |
| 日志 | CloudAudit | 跟踪集子账号身份未授权 | 7d |

### 🩺 域级健康度
- 🔴 IAM/CAM: 命中 3 项(Root AK / MFA / 离职账号)
- 🟠 网络与安全: 命中 5 项(SG 高危 / 公网 EIP)
- 🟡 资源配置: 命中 2 项(SSL 临期)
- ✅ 标签合规: 全量覆盖

### ⚠️ 快速判断信号
- Root AK 仍在使用且 30 天未轮换
- 公网暴露 EIP {N} 个,其中 {M} 个未挂 CFW
- CloudAudit 跟踪集 scan_status=caller_subuser,需主账号复核

### 🧭 建议执行节奏
| 优先级 | 行动 | 时限 |
|---|---|---|
| 🔴 高 | 立即禁用 Root AK | 24h |
| 🟠 中 | SG 高危端口收敛 | 7d |
| 🟡 观察 | SSL 临期续签 | 30d |

📊 完整明细见 HTML 看板。
```

#### 4.3 五条硬约束

- ❗ **不得现场生成报告程序**:full 巡检只允许调用 `generate_inspector_report.py`,禁止 LLM 写/patch `generate_report.py`、禁止 heredoc 拼报告。
- ❗ **不得调任何腾讯云 API 补查字段**。采集阶段全量落盘,报告阶段只读 `inspect_*.json`;缺字段走"这块我这次没拉到数据"或数据完整性复核项。
- ❗ **不得绕过硬预检**。`preflight_inspector_report.py` fail 时禁止发布,必须修脚本或数据源后重跑。
- ❗ **不得在 worker 进程里改生成器源码**(2026-05-29 新增,模式 G):worker 发现 preflight 报格式问题(截断符 `…` / `rule_short` 截断 / 矩阵未合并 / 折叠引用块等)时,**禁止** patch `generate_inspector_report.py` / `generate_inspector_customer_report.py` / `preflight_inspector_report.py` 中任何一个,**禁止** 反复重跑生成器调试。worker 唯一允许的动作:把 preflight 报错条目原样写进报告降级说明收尾,把"修生成器"留给后续人工/独立 PR。
- ❗ **iteration ≥ 50 强制收尾兜底**(2026-05-29 新增):worker 跑到 iteration 50 时,如果 preflight 仍未通过 / HTML 报告仍未发布,**必须**立即停止任何"再修一轮就好"的尝试,走 fallback 收尾路径:把当前 preflight 残留 / 未发布原因写进报告降级说明,以当前状态退出。**禁止**继续耗到 iteration 60 然后静默退出 — 那是最坏结局(用户拿不到任何反馈)。

#### 4.4 SOUL §五 报告侧合规

> 完整规则见下方 §SOUL §五 合规硬约束(报告侧)。Step 4 生成 `report_doc.md` 后**必须**跑 `preflight_inspector_report.py`;该脚本会检查 `AKID|AccessKeyId|SecretId|SecretKey|Token`、内部词、截断标记、资源 ID 空值和表格空行。

---

## 📋 判定准则:看 references/,不看本文件

**本 SKILL.md 不维护"严重度速查表"**。所有 finding 的触发条件、严重度、文案模板、SOP 依据,**唯一权威**在护城河四处 `references/01-iam-cam.md` / `references/10-logging-monitoring.md` / `references/11-compliance-tagging.md` / `references/17-infra-baseline.md` 各域文档「判定标准」表格 + `references/14-advisor-coverage.md` §1/§2 域路由+严重度。

> 历史教训:把判定阈值同时写在 SKILL.md 和 references 两处,2026-05 三轮优化期间反复出现"改了一边忘了另一边"的飘移。

LLM 在 Step 4 对**护城河四处**(CAM/CloudAudit/标签合规/基线-网络-安全-合规):**先 Read references/<域>.md 取判定表,再 Read inspect_*.json 取数据,按 13-report-template.md §xx 格式产出**;其他动态产品小节直接渲染 `inspect_advisor.json` 的 finding,不读 reference。reference 没列的字段一律不要 LLM 自由发挥。

### SOUL §五 合规硬约束(报告侧)

- ❌ **不得**输出任何 `AccessKeyId / SecretId / SecretKey`,即使是脱敏/截断形式(SOUL §五 L70 原文「**绝不输出,即使是占位/截断形式**」)
- ✅ Root AK 明细只列 `创建时间 / 最近使用 / 状态`(三字段足以唯一定位)
- ✅ 子账号 AK 明细只列 `子账号名 / 最后使用日期`(子账号名是定位线索,SOUL §五允许)
- ✅ UIN 在文档头部出现一次,正文不重复
- ✅ 公网 IP / 安全组允许段 / 邮箱 / 子账号名 / 域名:**完整保留**(运维需要)

### CAM 章节降级硬规则(2026-05-13 更新)

❗ **写报告前必读 `inspect_cam.json.caller_identity`**:

- `is_root_caller=true` → 正常出 `#### CAM 账户安全` 全部判定
- `is_root_caller=false` → **不再无条件降级**。必须先尝试调用 API，根据 API 实际返回结果决定：
  - API 返回正常数据 → **正常出报告**，与主账号时一样
  - API 返回错误（AuthFailure / AccessDenied / OperationDenied）→ 该项降级，按下表降级模板写

**(HARD) 禁止以下行为（已过时，2026-05-13 起废止）**：
- ❌ 看到 `is_root_caller=false` 就直接跳过检查项不调接口
- ❌ 把“子账号身份”作为唯一降级条件
- ❌ 不调接口直接写“子账号身份无法采集”

**原因**：子账号如果被授予了 `cam-read` 等 CAM 只读策略（如 `cam:Get*/Describe*/List*`），上述接口可正常返回数据。2026-05-13 实测确认：
- `GetUser --Name yujundeng` → RecentlyLoginTime=2026-04-01 ✅
- `ListGroupsForUser --SubUin 100045437701` → [开发测试组] ✅
- `ListAccessKeys --TargetUin 100045437701` → 0 个 AK ✅

**降级模板**（仅在 API 实际返回错误时使用）：

| 字段 | 降级模板(替代"正常"判定的措辞) |
|---|---|
| `root_aks_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` 主账号 AK 行写:`扫描凭证为子账号 {caller_uin}(OwnerUin={owner_uin}),无权列主账号 AK → 状态未采集,需主账号本人复核`,严重度标 ⚠️ 不可知,**不得**写"主账号持有 N 个活跃 AK" |
| `principals[].aks_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` 子用户 AK 表格里此行写 `(子账号身份不可见)`,不要写"无 AK"。**例外**:`aks_scan_status=ok` 的那一行就是扫描凭证自己,正常出 |
| `principals[].mfa_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` MFA 行写 `MFA 状态未采集(子账号身份)`,**禁止**给出"X/Y 已开启 MFA"的统计 |
| `principals[].last_login_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` 登录时间表格此行写 `(未采集)`;"疑似离职"和"从未登录"两项整体改写为 `子账号身份无法采集 RecentlyLoginTime,需主账号复核` |
| `principals[].groups_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` 用户组归属表格此行写 `(未采集)`,不要给"X 个未归属用户组"的统计 |
| `audit_cross_check.scan_status=skipped_subuser_caller` | `#### CAM 账户安全` CloudAudit 交叉校验段落整体不输出 |
| `collaborators_scan_status=skipped_subuser_caller` | `#### CAM 账户安全` 协助者统计行追加注脚 `(子账号身份下 ListCollaborators 返回空,可能为权限受限而非真无协助者)` |
| `admin_grants` / `custom_admin_policies` | **正常出**,这些 API 子账号身份默认有权 |
| `policies`(自定义策略) | **正常出**,ListPolicies 子账号有权 |

降级模式下 §一 巡检概览矩阵的 CAM 行摘要改写为:`⚠️ 子账号身份扫描,CAM 7 项中 5 项未采集` 之类,**不要**写"主账号AK+协助者AK"这种基于失真数据的判定。

> Why:2026-05-12 一中心管控账户事件--脚本 `ListAccessKeys` 不传 TargetUin 会返回"调用者自己"的 AK,被错标成主账号 AK 写进报告。同理子账号身份调 `ListAccessKeys(他人)` / `GetSafeAuthFlagColl(他人)` / `GetUser(他人)` / `ListGroupsForUser(他人)` / CloudAudit 全量都被默拒,采到空值会被下游误判为"无 AK / 未开 MFA / 从未登录"。inspect_cam.py 已加 `caller_identity` 探测和 `*_scan_status` 标记,报告侧必须配合。

### 云审计写报告骨架(`#### 云审计` 跟踪集子段 + 事件流子段 + 应急响应取证)

> 跟踪集(配置层 🟡)+ 高危事件流(行为层 🔴/🟠)双子节硬骨架 + 9 大事件类别判定阈值 + 7 条 HARD 规则(服务账号聚合 / 真高危必明细 / 0 事件 ≠ 审计未启用 / 跟踪集 vs 事件流不可混淆)见 [`references/10-logging-monitoring.md`](references/10-logging-monitoring.md) 云审计写报告骨架段。Step 4 渲染时严格按其总览矩阵 / 高危明细 / 服务账号聚合三张表。

---

### advisor 数据源使用硬骨架(2026-05-21 advisor 单源 + 自扫补丁重构 / F34 对外措辞)

> 数据源 `inspect_advisor.json` + `inspect_advisor_strategies.json`(Step 1b 并行 exec)。**主源域 / 自扫补丁域 / 护城河域** 三档分流、**scan_status 三态降级**、**repair_url 渲染规则**(HARD,空就空不补救)、**风险逐行表 schema** 见 [`references/14-advisor-coverage.md`](references/14-advisor-coverage.md) 第 7-11 节。F34 对外严禁出现 `advisor` / `智能顾问` / `strategy` / `costopt` 字眼,详见 `references/13-report-template.md` HARD #10。

---


### 报告章节硬骨架(权威定义在 `references/13-report-template.md`)

**(HARD) 以下骨架适用于完整报告**。

**顶层结构必须按下面顺序输出,缺一节判残**;骨架与各子节表格 schema(文档头 / §一 4 列巡检概览 / §二 4 大类项目详情 / §三 立即处置清单 / §四 扫描降级说明 / 文档尾)以 [`references/13-report-template.md`](references/13-report-template.md) 为准,本文件不重复维护。

**写完后必跑 grep 自检**(按 `$REPORT_SCOPE` 分流;任何一条不过 = 残):

```bash
report=$OUT_DIR/report.md

# SOUL §五 是全 scope 通用红线,任何情况都必须为 0
[ $(grep -cE 'AKID|AccessKeyId|SecretId|SecretKey' $report) -eq 0 ]

case "$REPORT_SCOPE" in
  full)
    # 4 大类松校验:命中大类必出标题(3 级,挂在 §二项目详情下),空大类允许整段省略;合规独立大类必出
    [ $(grep -cE '^### 云(安全|可靠|性能|限制)|^### 合规与资产标签' $report) -ge 1 ]
    # 护城河强制:CAM/云审计/标签三个小节必出(无论命中,4 级)
    [ $(grep -cE '^#### CAM 账户安全' $report) -ge 1 ]
    [ $(grep -cE '^#### 云审计' $report) -ge 1 ]
    [ $(grep -cE '^#### 标签合规' $report) -ge 1 ]
    # 「云成本」大类禁出现(用户决策:由独立的成本优化 skill 承载)
    [ $(grep -cE '^### 云成本' $report) -eq 0 ]
    [ $(grep -c '立即处置清单' $report) -ge 1 ]                  # §三必有
    [ $(grep -c '巡检概览' $report) -ge 1 ]                      # §一必有
    # 修复列措辞校验(改动 1:repair markdown 全文渲染)
    [ $(grep -cE '\| 修复建议 \|' $report) -ge 1 ]
    [ $(grep -c '\[文档\](https://' $report) -le 5 ]            # 单链接仅 §三处置清单使用,应 ≤5
    ;;
  narrow)
    # 单域:1 张表 + ≤3 条处置即可,不强求 4 大类 / 巡检概览 / 立即处置清单
    [ $(grep -cE '^\|.*\|.*\|' $report) -ge 1 ]                  # 至少一张 markdown 表
    ;;
  narrow_forensic)
    # 应急取证:时间窗 + 事件流水 + 结论
    [ $(grep -cE '时间窗|EventName|EventTime|UserName' $report) -ge 2 ]
    ;;
  skipped|"")
    # sla_query 等纯问询,本步骤跳过(不写 report.md)
    : ;;
esac
```

> `$REPORT_SCOPE` 由 §使用方式 首回合 export(`full|narrow|narrow_forensic|skipped`)。漏 export 时按 `""` 走 skipped 分支,Step 4 grep 不强校验——但凭 SOUL §五 行仍会跑,AK/SK 泄露红线绝不允许漏过。

### finding 数据完整性 + 风险等级 SLA

- 每条 finding 必须能链回原始资源 ID(sg-id / ins-id / bucket / cert-id);只写"N 个"不带 ID 会被 Step 3 验收时打回。资源 ID 多于 10 条显示前 10 + 总数,>30 条用单独表格。没问题的 SOP 域**也要在「巡检概览」里显示 ✅**(让运维知道是没问题不是没扫)。
- 等级响应时限速查:🔴 严重 ≤1h(生产≤2h / 测试≤24h)| 🟠 高 ≤1h(生产≤24h / 测试≤72h)| 🟡 中 ≤24h(生产≤72h / 测试≤7d)| 🟢 低 ≤48h(生产≤15d / 测试≤30d)。完整 SLA 表见 [`references/13-report-template.md`](references/13-report-template.md) §SLA。

---

---

---

## PPT 导出（按需触发）

### 触发条件

仅当**全量巡检（scope=full）报告**已生成（HTML/MD 报告 + 概览文字），且用户明确说**"导出 PPT / 转成 PPT / 给老板看的版本 / 汇报材料 / 出一份 ppt"**时触发。**默认不生成 PPT**；narrow / sla_query / narrow_forensic 不支持 PPT 导出（数据形态不适合）。

### 严禁行为

- ❌ 用户没明确要 PPT 时主动生成
- ❌ 在 PPT 里编造任何已生成报告没有的数据
- ❌ 用 PPT 替代 HTML/MD 报告（主交付物不变）

### 工作流

复用 `inspector_doc_customer.md` 解析后的章节数据 + 概览文字，按 `cloud-report-pptx-pro/SKILL.md` 的 JSON Schema 拼输入：

```python
pptx_input = {
    "report_type": "inspect",
    "title": "云安全巡检报告",
    "subtitle": f"4 大类 × {len(products_hit)} 个产品 · 主账号 {uin}",
    "uin": uin,
    "date": today,
    "summary_md": inspect_summary_md,   # 来自巡检概览章节
    "highlights": [
        {"title": "总发现项", "value": str(findings['total']),
         "delta": f"🔴{findings['p0']} 🟠{findings['p1']} 🟡{findings['p2']}", "note": ""},
        {"title": "需 ≤2h 处置", "value": str(findings['p0']),
         "delta": "P0 严重", "note": findings['top_p0_brief']},
        {"title": "覆盖类目", "value": "4 大类",
         "delta": "云安全/云可靠/云性能/云限制", "note": f"命中 {len(products_hit)} 个产品"},
    ],
    "sections": [
        {"heading": "4 大类发现概览",
         "chart": {"type":"bar", "categories":["云安全","云可靠","云性能","云限制"],
                   "series":[{"name":"发现项数","values": findings['by_group']}]}},
        {"heading": "P0/P1 高危项明细",
         "table": {"headers":["等级","产品","风险点","建议处置时限"],
                   "rows": findings['high_risk_rows'][:20]}},
        {"heading": "整改时限分布",
         "table": {"headers":["时限","数量","示例"],
                   "rows": findings['sla_rows']}},
    ],
    "actions": [{"priority": pri, "text": text} for pri, text in findings['action_items']],
}
```

调用：
```bash
/usr/bin/python3 ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input "$INPUT_PATH" \
  --output "$WORKBUDDY_WORKSPACE/reports/${CLOUDOPS_ACTIVE_CLOUD:-tencent}/${CLOUDOPS_ACTIVE_PROJECT}/$INSPECTION_UIN/$(date +%Y-%m-%d)/inspector.pptx" \
  --lang <output_lang>   # zh|en，跟随 [Output language]，与 HTML/概览同语种，缺省 zh
```

落盘后通过 `open_result_view` 工具触发预览，回复框追加（**语种跟随 `output_lang`**）：

> zh：📊 巡检 PPT 已生成在 `reports/{cloud}/{project}/{uin}/{date}/inspector.pptx`，覆盖 4 大类 + P0/P1 明细 + 整改时限。HTML/MD 报告仍在原位置。
> en: 📊 The inspection PPT has been generated at `reports/{cloud}/{project}/{uin}/{date}/inspector.pptx`, covering 4 categories + P0/P1 details + remediation SLA. The HTML/MD report remains in place.
