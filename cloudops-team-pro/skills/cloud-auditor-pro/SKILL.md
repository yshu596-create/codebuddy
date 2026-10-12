---
name: cloud-auditor-pro
description: 云审计日志分析。扫描 CloudAudit 高危事件流(9大类43个EventName),支持全量事件扫描、定向取证查询和跟踪集合规检查。凭证从环境变量加载。
---

# cloud-auditor-pro

云审计日志分析专用技能。覆盖审计反取证/AK生命周期/权限授予/资源毁灭/KMS/MFA/用户CRUD/角色CRUD/安全组改动 9 大类,自动降噪服务账号,精准输出真高危事件。

## ⚠️ output_lang 一致性（最常见 bug · 写概览前必检）
- **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问时 `output_lang=en`，概览必须全英文；中文提问时 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。这在中文运行环境下极易发生。
- ✅ 正确做法：在写第一个字之前，默念「`output_lang` 是什么？」，然后用该语言写。
- ✅ `--lang` 参数透传给渲染脚本，确保 HTML 看板界面骨架语言一致。
- ✅ 枚举型中文名（事件类别名、风险等级、操作者类型）当 `output_lang=en` 时必须翻译为英文，禁止保留中文原文。

## 凭证管理（v3 进程内隔离 · 多账号并发安全）

> **凭证源 = SSM + KMS(唯一来源)**:各云账号 AK/SK 以 KMS 加密存放在腾讯云凭据管理服务(SSM),secret 名 `cloudops/tencent/{CODE}`(`{CODE}` 为项目码如 `ADC`/`TSP`/`YX`)。`inspect_audit_events.py` 接收 `--cloud tencent --project <CODE>`,内部调 `build_credential_env.resolve("tencent", "<CODE>")` 在**进程内**从 SSM 在线读取(KMS 解密在 SSM 服务端由服务角色完成,调用方无需任何 kms 权限),凭证仅存活于脚本局部变量,**绝不读裸名 env、也不 `eval` 导出到共享 shell 环境**,从根上消除多实例并发时的 `os.environ` 末位覆盖竞态。
>
> | 环境变量 | 必填 | 说明 |
> |---|---|---|
> | `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` | ✅ | runner key 的 SecretId(仅需 SSM 只读权限,无需 kms) |
> | `TENCENTCLOUD_BOOTSTRAP_SECRET_KEY` | ✅ | runner key 的 SecretKey |
> | `TENCENTCLOUD_BOOTSTRAP_REGION` | ❌ | SSM 所在地域,默认 `ap-guangzhou` |
> | `INSPECTION_UIN` | ❌ | 主账号 UIN,留空可由 `cam.GetUserAppId` 自动派生 |
>
> **自动启用**:配好上述 runner key 即自动启用 SSM,**无需**额外设 `CLOUDOPS_SSM_ENABLED=true`;仅当其值 ∈ {false,0,no,off} 时才关闭 SSM。`--project` 必传——SSM 按 `cloud/project` 定位密钥,**不存在"省略 → 读裸名"的回退路径**(旧 env-prefix 模型已废弃)。
>
> **单账号模式 / 多账号并发**:每个 `(cloud, project)` 一个脚本进程,各自从 SSM 取自己那把凭证,互不污染。主理人 fan-out 多个审计实例时天然隔离,每次产出当前账号一份报告。

### 跨账号代查:STS AssumeRole(已收敛进 SSM,自动透明)

> 跨账号代查现已**完全收敛到 `build_credential_env.resolve()` 内部**——审计脚本本身**零感知**。
> 你照常 `--project <CODE>` 运行,`resolve()` 取到该 secret 后会自动判断:
> - secret 里**没有** `role_arn` → 直连本账号(路径 1)。
> - secret 里**有** `role_arn` → `resolve()` 在**进程内**用主账号 AK 调一次 `sts.AssumeRole`,
>   把换出的临时 `TmpSecretId/TmpSecretKey/Token` 直接作为返回凭证;`inspect_audit_events.py`
>   拿到后把 token 喂给 `sign.set_process_token()`,后续所有 `DescribeEvents` 自动带 `X-TC-Token`。
>
> **如何让某账号走跨账号代查**:在它的 SSM secret `cloudops/tencent/<CODE>` 里加 role 字段:
>
> | secret JSON 字段 | 必填 | 说明 |
> |---|---|---|
> | `role_arn` | ✅(启用跨账号时) | 目标账号角色 ARN,如 `qcs::cam::uin/100099999999:roleName/AuditReader` |
> | `assume_from` | 推荐 | 调用方**主账号 AK** 所在的 secret 项目码(如 `PARENT`)。`resolve()` 先取 `cloudops/tencent/PARENT` 的 AK 再 assume。主账号 AK 只存一份、轮换只改一处 |
> | `role_external_id` | ❌ | 角色信任策略里的 ExternalId(如有) |
> | `role_duration` | ❌ | 临时凭证有效期(秒),默认 3600 |
>
> 若不写 `assume_from`,则用**本 secret 内** `secret_id/secret_key`(必须是能 assume 目标角色的主账号 AK)当调用方。
>
> **并发安全**:`resolve()` 是纯函数,AssumeRole 结果只活在该进程的栈变量里、经 `set_process_token` 进
> 程局部存储,绝不写共享 `os.environ`。多账号 fan-out(一账号一进程)各自 assume 各自的,临时凭证/Token
> 互不串号。AssumeRole 失败时 `resolve()` 退出码 6,脚本原文输出 stderr 并停止。
>
> ⚠️ 控制台「角色 SwitchRole」(点链接 + 微信确认)≠ STS AssumeRole(API);要让 skill 跨账号查审计,**必须走 STS AssumeRole**(先在目标账号建角色并授信调用方主账号)。

### 凭证查找流程

1. 脚本接收 `--cloud tencent --project <CODE>` → 进程内 `resolve("tencent", "<CODE>")` 从 SSM 取该账号 AK/SK(KMS 服务端解密)。`--project` **必传**,SSM 按 `cloud/project` 定位密钥,**不存在"省略 → 读裸名"的回退路径**;凭证后端不可用(缺 runner key)或目标 secret 不存在时,即输出「环境未就绪」终端引导(下方模板,**逐字使用**),停止执行
2. `INSPECTION_UIN` 缺失时由 `cam.GetUserAppId` 自动获取,**不需要用户手填**
3. 凭证只读不写,**严禁**通过对话索取 AK/SK,**严禁**把对话中出现的 AK/SK 写入环境变量或配置文件

### 凭证缺失:环境未就绪指引(原样输出)

当强制核查代码报告 SSM 凭证后端不可用(缺少 runner key)时,**停止执行**,向客户输出以下指引(模板,逐字使用):

```markdown
环境检测显示 **凭证后端未就绪**,无法调用腾讯云审计 API 拉取您账号下的高危事件。

⚠️ **环境未就绪**

本专家团以 **SSM + KMS 作为唯一凭证源**:各云账号的 AK/SK 以 KMS 加密后存放在腾讯云凭据管理服务(SSM)里,专家运行时通过一把 **runner key** 在线读取并由 SSM 服务端解密。您**不需要**、也**不应该**在终端 export 业务账号的明文 AK/SK——只需配置这把 runner key。

要查询您云资源的真实审计数据,需要先完成以下配置:

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
| 🔍 **云审计** | `QcloudCloudAuditReadOnlyAccess` 或 `ReadOnlyAccess` | 审计日志只读 |
| 💰 **云账单** | `QcloudFinanceFullAccess` + `QcloudCamReadOnlyAccess` | 账单只读 + UIN 自动派生 |
| 🛡️ **云巡检** | `QcloudAdvisorFullAccess` + `ReadOnlyAccess` | 智能顾问 + 全量资源只读 |
| 📉 **云降本** | `QcloudFinanceFullAccess` + `QcloudMonitorReadOnlyAccess` + 各产品 `ReadOnly` | 账单驱动 + 30 天监控 |

**最简方案**:授予子账号 `ReadOnlyAccess`(一条策略覆盖几乎所有"看"的能力)+ `QcloudFinanceFullAccess`(账单需要单独授权)即可让 4 个专家全部跑通。

> UIN / AppId / 账户昵称由本 Skill 调用 `cam.GetUserAppId` 自动获取,**不需要您手填**。

---

**完成配置后,云审计会做什么**

针对"账号下有没有高危操作",从以下 9 大类高危事件流为您扫描:

| 风险维度 | 巡检内容 |
|---|---|
| 🔒 **审计反取证** | 关闭/修改跟踪集、删除日志投递目标 |
| 🔑 **AK 生命周期** | 创建 / 删除 / 禁用主账号 AK,异常 IP 调用 |
| 🛡️ **权限授予** | 高危策略附加(AdministratorAccess / `*:*:*`)、跨账号角色信任变更 |
| 💥 **资源毁灭** | 批量删除 CVM / COS Bucket / CDB / 存储桶生命周期清空 |
| 🔐 **KMS / MFA / 用户 CRUD / 角色 CRUD / 安全组改动** | 关键安全配置异动取证 |

输出形式: 9 大类高危事件矩阵 + 操作人 / 来源 IP 排行 + 真高危明细 + AIOps 智能概览,自动降噪服务账号,精准定位需复核的真人操作。

---

**请确认下一步:**
1. 是否现在配置 runner key?(配置完告诉我"配好了",我立即启动审计扫描)
2. 还是先看一份样例审计报告了解输出格式?

🔗 [前往腾讯云控制台](https://console.cloud.tencent.com/cam/capi)(获取 runner key 的 SecretId/SecretKey)
```

**行为规则:**
1. 输出上述指引后**停止本次执行**,等待用户在终端设置好 runner key 后再次触发审计
2. **禁止**通过对话直接接收用户粘贴的 AK/SK 后写入文件 —— runner key 必须由用户自己在终端设置（Linux/macOS `export` / Windows PowerShell `$env:` / Windows CMD `set`）,业务账号 AK/SK 只存于 SSM
3. **禁止**输出"贴过来我帮你 export"、"密钥不会落盘""我帮你配置进会话"等任何形式的诱导式凭证收集话术
4. 用户重新发指令后,从「凭证查找流程」重新开始

### 更新 runner key

❌ **Skill 不得**修改用户 shell 环境或写入任何持久化凭证文件
✅ 更新流程:用户自己在终端重新设置 runner key（Linux/macOS 用 `export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`，Windows PowerShell 用 `$env:TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`，CMD 用 `set TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...`），然后重新触发审计;同一进程内的下次执行自动读到新值。业务账号 AK/SK 的增改在 SSM 控制台维护,不经终端

## 使用方式

### 全量事件扫描(scope=event_scan)

用户说"检查最近有没有高危操作"、"扫一下审计日志"、"看看这周有什么异常"时:

**Step 1 · 扫描事件流**

> 凭证**不再** export 到 shell——把项目码作为参数透传给脚本,脚本进程内 `resolve("tencent", "<CODE>")` 从 **SSM** 取该账号密钥(KMS 服务端解密),与并发的其他账号实例天然隔离。`<CODE>` 来自主理人 `[Account context]`(如 `ADC`/`TSP`/`YX`)。

```bash
# 进程内取凭证(并发安全),无需 export 业务账号 AK/SK
export INSPECT_OUT_DIR="/tmp/inspect_audit_<CODE>"   # 可选,缺省自动派生
python3 scripts/inspect_audit_events.py --cloud tencent --project <CODE>

# 注:凭证唯一来源是 SSM,宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY);
# --project 必传(SSM 按 cloud/project 定位密钥),不存在"省略 --project 读裸名"的回退路径。
```

输出 `$INSPECT_OUT_DIR/inspect_audit_events.json`,包含:
- `time_window`: 扫描时间窗
- `scan_status`: ok/denied
- `events_by_category`: 9 大类事件明细
- `summary_by_principal`: 按操作人聚合
- `service_account_set`: 已识别的服务账号
- `category_risk`: 类别风险等级映射
- `total_events`: 总事件数
- `total_categories_hit`: 命中类别数

**Step 2 · 渲染 HTML 看板**

> 🚨 **渲染硬规则**：HTML 报告**必须且只能**由 `scripts/render_audit_html.py` 生成，**严禁** LLM 手写 HTML/CSS/Chart.js 代码。违反此规则会导致多账号报告风格不统一、章节缺失、数据口径不一致。
>
> 违反示例（已出现过，禁止重犯）：
> - ❌ LLM 自行手拼 `<style>` + `<script>` + `<table>` 生成 audit.html
> - ❌ 不调 render_audit_html.py，直接 heredoc/cat 写 HTML
> - ✅ 唯一正确路径：`python3 scripts/render_audit_html.py --data ... --output ...`

```bash
python3 scripts/render_audit_html.py \
  --data "$INSPECT_OUT_DIR/inspect_audit_events.json" \
  --account "<账户昵称>" --uin "$INSPECTION_UIN" \
  --output "$WORKBUDDY_WORKSPACE/reports/${CLOUDOPS_ACTIVE_CLOUD:-tencent}/${CLOUDOPS_ACTIVE_PROJECT}/$INSPECTION_UIN/$(date +%Y-%m-%d)/audit-${CLOUDOPS_ACTIVE_CLOUD:-tencent}-${CLOUDOPS_ACTIVE_PROJECT}-$INSPECTION_UIN.html" \
  --lang <output_lang>   # zh|en，跟随主理人下发的 [Output language]，缺省 zh
# 注：$WORKBUDDY_WORKSPACE 确保报告落盘到工作区（产物面板可见）；文件名格式 audit-{cloud}-{project}-{uin}.html，与 billing/costopt 命名统一；CLOUDOPS_ACTIVE_CLOUD/PROJECT 由 inspect_audit_events.py 在 JSON 中携带；未传 --project（单账号旧入口）时退化为 audit-{cloud}-{uin}.html
```

生成单文件深色玻璃拟态看板(KPI 四宫格 + 类别环图 + 操作人排行 + 9 大类矩阵 + 真高危明细全量表 + 服务账号聚合 + 智能安全洞察)。`--lang en` 时类别名/风险等级/表头/洞察等界面骨架渲染为英文(EventName、用户名、IP、资源 ID 等数据值保留原文);须与对话/概览语言一致。渲染契约见 `references/html-template.md`。Chart.js 默认走 CDN,离线场景加 `--offline /path/to/chart.umd.min.js`。

**Step 3 · 文字概览 + 预览（先概览、后 HTML）**

落盘成功后，**先**在回复框输出一段 AIOps 智能概览(≤500 字),覆盖:扫描窗口与状态、真高危事件数与最高风险类别、需优先复核的操作人/来源 IP、服务账号降噪说明。**概览输出完毕后，再**通过 present_files 打开 `audit-{cloud}-{project}-{uin}.html`，确保概览不被预览面板遮挡。**不再输出大段 Markdown 表格**——明细已在 HTML 看板里。

### 定向取证(scope=forensic)

用户说"这周有没有人删COS桶"、"上周谁改了IAM策略"等时:

直接用 `scripts/lib/sign.py` 的 `call_api` 调用:
- Service: `cloudaudit`
- Action: `DescribeEvents`
- Region: `ap-guangzhou`
- 参数: `LookupAttributes`, `StartTime`, `EndTime`, `MaxResults=50`

### 跟踪集检查(scope=config_check)

用户说"审计跟踪集配了吗"时:
- 调用 `cloudaudit.DescribeAuditTracks` + `cloudaudit.ListAudits`
- 检查投递目标(CLS/COS)和保留期(≥90天)

### 标准问询(scope=sla_query)

纯问询直接回答,不加载凭证不跑脚本。

## 9 大事件类别

| 类别 | EventName | 风险等级 |
|------|-----------|----------|
| 审计反取证 | DeleteAuditTrack / StopLogging / UpdateAuditTrack | 🔴 严重 |
| AK 生命周期 | CreateAccessKey / DeleteAccessKey / UpdateAccessKey | 🔴 高 |
| 权限授予 | Attach/Detach User/Group/RolePolicy + Create/Update/DeletePolicy | 🔴 高 |
| 资源毁灭 | TerminateInstances / DeleteBucket / PutBucketAcl / Isolate+DeleteDBInstance | 🔴 高 |
| KMS 主密钥 | DisableKey / ScheduleKeyDeletion / CancelKeyDeletion | 🔴 高 |
| MFA 变更 | SetMfaFlag / UnbindMFADevice | 🟠 中 |
| 用户 CRUD | CreateUser/DeleteUser/UpdateUser + LoginProfile | 🟠 中 |
| 角色 CRUD | CreateRole / DeleteRole / UpdateRole / UpdateAssumeRolePolicy | 🟠 中 |
| 安全组改动 | Create/Modify/DeleteSecurityGroupPolicies / DeleteSecurityGroup | 🟡 中 |

## 服务账号降噪规则

满足任一即标为服务账号(报告聚合一行,不展开):
1. Username 含 `QcsRole` / `QCSLinkedRole`
2. 同 Username 单 EventName 出现 ≥ 5 次
3. Username 含 `_role` / `-role` / `-sg` 后缀且事件数 ≥ 3

## 报告输出格式

### 事件扫描报告(文字概览 + HTML 看板)

主交付物是 `render_audit_html.py` 生成的**单文件 HTML 看板**(落盘 `reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/audit-{cloud}-{project}-{uin}.html`,`{cloud}`/`{project}` 取自 `CLOUDOPS_ACTIVE_CLOUD/PROJECT`)。交付顺序固定为**先在回复框输出 AIOps 智能概览，再通过 present_files 打开 HTML 看板**（概览在前、预览在后，确保概览不被遮挡），**不再贴大段 Markdown 表格**。

> **文件名约定（v3 PROJECT 码）**：报告文件名格式 `audit-{cloud}-{project}-{uin}.html`，与 billing / costopt 命名统一（`billing-{cloud}-{project}-{uin}.html`、`costopt-{cloud}-{project}-{uin}.html`），用**稳定的项目码 `{project}`**（如 `ADC`/`TSP`/`YX`，取自脚本注入的 `CLOUDOPS_ACTIVE_PROJECT`）而非波动的账号昵称 `{display_name}`，多账号 / 多云不会同名覆盖且命名稳定。例如：`audit-tencent-ADC-100039081678.html`、`audit-tencent-TSP-100031104408.html`。
>
> - `{cloud}`：云厂商码（`tencent`），取 `CLOUDOPS_ACTIVE_CLOUD`
> - `{project}`：项目码，取 `CLOUDOPS_ACTIVE_PROJECT`（由 `inspect_audit_events.py --project <CODE>` 注入）；`{uin}` 由 `cam.GetUserAppId` 自动获取
> - **未传 `--project`（单账号旧入口）时**：退化为 `audit-{cloud}-{uin}.html`（省略 project 段，不留双连字符）
> - **不再用** `{display_name}` 昵称或 `uin{uin}` 前缀命名

看板必有章节(详见 `references/html-template.md`):
- KPI 四宫格:事件总量 / 命中类别(N/9) / 真高危事件 / 严重+高
- 高危类别分布环图(按风险配色) + 操作人高危排行柱图
- 9 大类总览矩阵(含 0 命中类别)
- 真高危事件明细全量表(风险→时间倒序,服务账号已剔除)
- 服务账号聚合表(一账号一行,降噪)
- 智能安全洞察卡片

回复框文字概览(≤500 字)结构示例:

```
近 7 天云审计扫描完成。共采集 N 条事件,命中 X/9 类高危类别,扣除服务账号噪声后
真高危 M 条(其中严重/高 K 条)。
🔴 最需关注:{最高风险类别 + 一句话}
👤 重点操作人:{username}({高危事件数})
🌍 来源 IP:{是否分散/可信}
🤖 已降噪:{N} 个服务账号的 {X} 条自动化事件
详见看板。
```

### 定向取证报告(轻量 Markdown)

取证是即时单点查询,保留轻量 Markdown 即可:

```
## 结论
{有/无目标事件}

## 事件明细
| 时间 | 操作人 | 来源IP | 资源 | 错误码 | RequestId |
|------|--------|--------|------|--------|-----------|

## 扫描可信度
- 时间窗: ...
- EventName 覆盖: ...
```

---

## PPT 导出（按需触发）

### 触发条件

仅当审计报告（HTML 看板 + 概览）已经产出，且用户明确说**"导出 PPT / 转成 PPT / 给老板看的版本 / 汇报材料"**等关键词时触发。**默认不生成 PPT**。

### 严禁行为

- ❌ 用户没明确要 PPT 时主动生成
- ❌ PPT 替代 HTML 看板（HTML 是主交付物）
- ❌ 在 PPT 里编造 HTML 没有的数据

### 工作流

复用已生成的审计 HTML 数据（`/tmp/audit_$INSPECTION_UIN/inspect_audit_events.json` + 已生成的概览文字），按 `cloud-report-pptx-pro/SKILL.md` 的 JSON Schema 拼成输入：

```python
pptx_input = {
    "report_type": "audit",
    "title": "云审计风险报告",
    "subtitle": f"扫描窗口 {start} ~ {end} · 主账号 {uin}",
    "uin": uin,
    "date": today,
    "summary_md": audit_summary_md,    # 已生成的 ≤500 字概览
    "highlights": [
        {"title": "扫描事件总数", "value": str(events['total']), "delta": "", "note": f"窗口 {window_days} 天"},
        {"title": "真高危事件", "value": str(events['high_risk_count']),
         "delta": events['top_category'], "note": "降噪后真人操作"},
        {"title": "TOP 操作人", "value": events['top_actor'],
         "delta": f"{events['top_actor_count']} 次", "note": events['top_actor_ip']},
    ],
    "sections": [
        {"heading": "9 大类高危事件矩阵",
         "table": {"headers":["类别","事件数","真高危","降噪服务账号"],
                   "rows": events['matrix_rows']}},
        {"heading": "操作人 TOP / 来源 IP TOP",
         "chart": {"type":"bar", "categories": events['top_actors'],
                   "series": [{"name":"事件数","values":events['top_actor_counts']}]}},
        {"heading": "需复核真高危事件明细",
         "table": {"headers":["时间","操作人","EventName","资源","来源 IP"],
                   "rows": events['high_risk_rows'][:20]}},
    ],
    "actions": [{"priority":"P0","text":t} for t in events['actions']],
}
```

调用：
```bash
/usr/bin/python3 ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input "$INPUT_PATH" \
  --output "$WORKBUDDY_WORKSPACE/reports/${CLOUDOPS_ACTIVE_CLOUD:-tencent}/${CLOUDOPS_ACTIVE_PROJECT}/$INSPECTION_UIN/$(date +%Y-%m-%d)/audit-${CLOUDOPS_ACTIVE_CLOUD:-tencent}-${CLOUDOPS_ACTIVE_PROJECT}-$INSPECTION_UIN.pptx" \
  --lang <output_lang>   # zh|en，跟随 [Output language]，与 HTML/概览同语种，缺省 zh
```

落盘后通过 `open_result_view` 工具触发预览，回复框追加（**语种跟随 `output_lang`**）：

> zh：📊 审计 PPT 已生成在 `reports/{cloud}/{project}/{uin}/{date}/audit-{cloud}-{project}-{uin}.pptx`。HTML 看板和概览仍在原位置。
> en: 📊 The audit PPT has been generated at `reports/{cloud}/{project}/{uin}/{date}/audit-{cloud}-{project}-{uin}.pptx`. The HTML dashboard and overview remain in place.
