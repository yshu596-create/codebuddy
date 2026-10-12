---
name: cloud-billing-huawei-pro
description: 华为云账单分析专家。当用户提到"华为云账单""华为云费用""华为月度账单""华为云成本"等关键词或要求分析华为云账单时触发。输出**单文件 HTML 数据看板**（深色 + Chart.js + 全量资源展示），落盘工作区由 WorkBuddy 客户端预览。基于华为云 BSS 客户运营 API（ShowCustomerMonthlySum + ListCustomerselfResourceRecords）、SDK-HMAC-SHA256 签名 + AK/SK 直连。
---

# 华为云账单分析

## 目录

```
cloud-billing-huawei-pro/
├── SKILL.md                    # 本文件：流程 + 凭证 + 约束
├── scripts/                    # Python 工具脚本
│   ├── sign.py                 # SDK-HMAC-SHA256 签名 + GET 请求封装
│   ├── prepare_billing_data.py # 一键数据准备（含凭证解析 + IAM 派生 domain_id）
│   ├── extract_step3_data.py   # prep_output → step3_data 扁平化提取
│   └── render_billing_html.py  # step3_data → 单文件 HTML 看板
└── references/
    └── api-endpoints.md        # 华为云 BSS API 速查
```

> **凭证源 + 签名边界**：SSM + KMS 是唯一凭证来源，只负责安全存放华为云 AK/SK（可含 `bss_endpoint`、临时凭证 `securitytoken`、可选 `domain_id`）；脚本经 `resolve('huawei', project)` 在线读取、服务端解密。真正访问华为云 API 时，`scripts/sign.py` 必须按 SDK-HMAC-SHA256 生成 `Authorization` / `X-Sdk-Date` / `Host`，并把临时凭证的 `X-Security-Token` 纳入 SignedHeaders。宿主只需配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`（仅 SSM 只读权限，无需 kms）。可选 `HUAWEICLOUD_MONTHLY_BUDGET`（元，非机密，仍从 env 读，报告头部展示消耗率）。`domain_id` / 账号名称优先读 SSM，未提供时由 IAM `ListAuthDomains` 自动派生。

> **路径占位符**：`<SKILL_BASE>` = 本 skill 所在目录绝对路径；`<WORKSPACE>` = WorkBuddy 工作区根目录（环境变量 `WORKBUDDY_WORKSPACE` 或 `os.getcwd()`）。缓存目录 = `<WORKSPACE>/cache/billing-huawei/{domain_id}/`，归档目录 = `<WORKSPACE>/reports/{cloud}/{project}/{domain_id}/{YYYY-MM-DD}/billing-huawei-{project}-{domain_id}.html`。
>
> **文件名约定（v3，2026-06-13）**：HTML 文件名用稳定 PROJECT 码 + `{domain_id}`：`billing-huawei-{project}-{domain_id}.html`（en 版加 `-en` 后缀），避免多账号并发时用波动域名导致同名覆盖。例如：`billing-huawei-HWPROD-123456.html`。未传 `--project` 时退化为 `billing-huawei-{domain_id}.html`。

---

## 🔴 全局红线（最高优先级，违反即视为事故）

### 零、output_lang 一致性（最常见 bug）
- ⚠️ **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问时 `output_lang=en`，概览必须全英文；中文提问时 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。这在中文运行环境下极易发生。
- ✅ 正确做法：在写第一个字之前，默念「`output_lang` 是什么？」，然后用该语言写。
- ✅ `--lang` 参数透传给渲染脚本，确保 HTML 看板界面骨架语言一致。
- ✅ 枚举型中文名当 `output_lang=en` 时必须翻译为英文，禁止保留中文原文。

### 一、数据真实性
- ❌ 禁止编造任何数字。费用、占比、环比差额、资源数量等数值**必须来自华为云 BSS API 返回值**。
- ❌ 禁止在没有调用 API 的情况下输出数据；禁止对 API 数值做"合理推测"或"估算"。
- ✅ API 失败 → 标注"数据获取失败"；某产品某月无费用 → 标注"无费用"或"-"，不用 0 占位。

### 二、凭证安全（零流转）
- ❌ 禁止在任何输出（HTML 看板/对话/归档/调试日志）中展示 SK / AK / domain_id 明文。
- ❌ 禁止在报告里提及"环境变量""HUAWEICLOUD_SDK_AK"等技术细节。
- ❌ 禁止通过对话向用户索取 AK/SK；禁止把对话中出现的 AK/SK 写入任何环境变量或配置文件。
- ❌ 禁止 skill / agent 写入 `.env` 文件（无论用户是否说"帮我保存"）。
- ✅ 凭证唯一来源是 SSM + KMS；脚本只在进程内读取并用于实时签名，skill 只读不写。
- ✅ 缓存目录 `<WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/` 只写 API 原始返回值，不夹带 AK/SK / prompt / 调试日志。

### 三、HTML 看板渲染（最高优先级输出契约）
- ✅ 单文件 HTML：所有数据内联，仅通过 CDN 加载 Chart.js 一行 `<script>`，不依赖其他外部资源。
- ✅ 视觉风格：深色玻璃拟态 + KPI 四宫格 + 横向柱图 + 全量资源表 + 智能洞察卡片。
- ✅ 全量展示：所有云服务、所有企业项目、四类资源变化（增长/降低/新增/下线）必须全部渲染。
- ❌ 禁止生成 `.md` 报告作为最终交付物。
- ❌ 禁止创建在线文档、Bot 推送或其他在线协作产物——只输出 HTML 看板。

### 四、执行纪律
- ❌ 禁止跳过 Step：流程中的 Step 0 → 1 → 2 → 3 必须按顺序，不得省略。
- ❌ 禁止因为产品太多只输出部分；账单返回的**每个云服务类型**都要出现在「按云服务拆解」表中。
- ✅ 某产品某 API 报错 → 降级处理（标注"权限不足/API 错误"），**不**中止整体流程。
- ✅ 月份未到出账日 → 标注"未出账""部分出账"，**不**用上月数值替代。

### 五、账单数值精度
- ✅ 费用展示**整数**（`¥12,580`），**不带小数点**。
- ✅ 占比保留 1 位小数（`56.9%`）；中间计算保留 4 位小数防浮点误差，展示层 round 到整数。
- ✅ 环比差额 = 分析月 API 值 - 对照月 API 值（直接相减，**不**二次重算）。
- ✅ 国际站币种为 USD 时统一用 `$`；中国站为 CNY 时用 `¥`。**禁止**在中国站上用美元符号或反之。

### 六、月份语义
- ✅ 报告头部**必须**标注 `分析窗口: {YYYY-MM} vs {prev_YYYY-MM}`。
- ✅ 默认月份语义（用户不指定时）：**T-1 月**（上月）。
- ✅ 用户说"本月/当月" → 当月 YYYY-MM，**且报告头部加一句**：`⚠️ 当月账单尚未出账，以下数据为当前已生成部分，可能不完整`。
- ✅ 用户说"X 月份"年份未明确 → 默认当前年；跨年边界（12 月用户说"1 月份"）→ 反问。
- ❌ 禁止用"当前/目前/现在"描述账单数据状态（账单有出账延迟）。

### 七、同环比口径
- ✅ **环比** = 分析月 vs 上月（默认）；**同比** = vs 去年同月（用户明示才触发）。
- ✅ **新增资源** = 分析月有费用、对照月无；**已下线** = 反之；**增长** = 两月都有且分析月 > 对照月；**降低** = 反之。
- ❌ 禁止把"环比"写成"同比"。

### 八、不做承诺
- ✅ 账单分析输出**已发生的事实**，不输出"可节省 ¥X"等预测——预测是 costopt 的事。
- ❌ 禁止在账单报告里出现"建议关停 / 建议降配 / 立即整改"等动作型语句。
- ✅ 异常消费可以**标注**，但**不**给修复建议。

### 九、执行过程静默
- ❌ 禁止输出过渡语（"好，先来看看凭证情况"）、执行状态（"分析月份: 5 月"）、技术术语（"Step 1 完成"）、文件路径、rule_id。
- ✅ 用户触发后，模型的第一条可见输出必须是**凭证缺失指引**（若需要）或**AIOps 智能概览 + HTML 看板预览**（概览在前、present_files 打开 HTML 在后，确保概览不被遮挡）。

### 十、执行效率（HARD）
- **F-EXEC-COST**：Python sandbox 启动有 ~3.2s 固定开销。同一阶段计算必须合并到一条 exec，禁止逐文件/逐产品分多次 exec。
- **F-LARGE-FILE**：禁止直接 Read/cat 任何超过 500KB 的 JSON 文件进 context。
- **F-NO-BYPASS**：禁止自写脚本绕过 skill 提供的官方工具（`prepare_billing_data.py` / `extract_step3_data.py` / `render_billing_html.py`）。

---

## 一、凭证（单账号模式）

环境变量速查：

| 变量 | 必填 | 说明 |
|---|---|---|
| `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` | ✅ | runner key SecretId（宿主侧，仅 SSM 只读权限，无需 kms） |
| `TENCENTCLOUD_BOOTSTRAP_SECRET_KEY` | ✅ | runner key SecretKey |
| `TENCENTCLOUD_BOOTSTRAP_REGION` | ❌ | SSM 所在地域，默认 `ap-guangzhou` |
| `HUAWEICLOUD_MONTHLY_BUDGET` | ❌ | 月度预算（元，非机密），用于报告头部消耗率 |

> 华为云账号 AK/SK 与 `bss_endpoint` 存于 SSM 密文（secret 名 `cloudops/huawei/{project}`），脚本按 `--project <CODE>` 取，**不**经环境变量。配好 runner key 即自动启用 SSM，无需 `CLOUDOPS_SSM_ENABLED=true`。如使用临时凭证，SSM SecretString 还可包含 `securitytoken` / `security_token` / `token`，脚本会作为 `X-Security-Token` 参与签名。

**所需权限**：`BSS Administrator`（或自定义只读策略 `billing:bill:view`）+ `IAM ReadOnlyAccess`（用于 `ListAuthDomains` 派生 domain_id；如果 SSM 已提供 `domain_id`，可跳过该 IAM 派生调用）。

`domain_id` / 账号名优先读取 SSM；未填写时由 `prepare_billing_data.py::resolve_credentials` 调用 IAM `/v3/auth/domains` 自动获取，禁止在对话中索取 AK/SK。

**凭证缺失**：脚本返回 `status: "need_credentials"` + `prompt` 字段（双语友好引导）。**Agent 必须原文返回 `prompt` 给用户**，不裁剪、不改写、不补充自己的引导。

> ❌ **【红线·诱导式凭证收集禁令】禁止任何形式的"贴 AK/SK 给我我帮你 export 进会话"**：
> - 禁止说"想跳过准备阶段？直接贴过来，我帮你 export 进会话再跑"
> - 禁止说"密钥仅在本次会话内存中使用，不会落盘"
> - 凭证只能由用户自己在终端 `export`，绝无例外

---

## 二、分析流程（Step 0~3）

### Step 0：解析月份 + 缓存控制

```
账号解析：单账号模式，凭证由 `--project <CODE>` 从 SSM 取得，无须用户传账户名
月份判定：未指定 → 上月(T-1) | "本月" → 当月（标注 ⚠️ 未出账） | 指定月份 → 按要求

缓存控制：
  若用户原话含「重新分析 / 刷新 / 不走缓存 / refresh」→ 加 --refresh 参数
  分析月 == 当前月 或 今天 1-4 号 + 分析月 == T-1 → 自动跳过缓存（脚本判定）
```

### Step 1~2：一键数据准备（prepare_billing_data.py）

**唯一合法的数据准备方式**：

```bash
python3 <SKILL_BASE>/scripts/prepare_billing_data.py \
  --cloud huawei --project <CODE> \   # 必传：进程内 resolve('huawei', project) 从 SSM 取该账号凭证（KMS 服务端解密），规避竞态
  --month 202605 --lang <output_lang>
# --lang zh|en：跟随主理人 [Output language]；凭证缺失时据此返回对应语种引导，缺省 zh
# 凭证唯一来源是 SSM，宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY)；--project 必传，无"省略读裸名"回退
# 加 --refresh 可清空缓存重拉
```

脚本内部行为：
1. `resolve_credentials()` 读环境变量 + 调用 IAM 派生 `domain_id`/`domain_name`
2. 并发拉取分析月 + 对照月的：
   - `ShowCustomerMonthlySum`（按云服务类型汇总）
   - `ListCustomerselfResourceRecords`（资源消费记录）
3. 聚合：按云服务、企业项目、地域三个维度聚合
4. 计算：环比差额、ratio、resource_diff（growth/decline/new/decommissioned）
5. 输出 `prep_output.json` 到 `<WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/`，并 stdout 打印一份完整 JSON（含 `report_ready` 字段）

**输出 JSON 关键字段**：
- `status`：`ok` / `need_credentials` / `error`
- `credentials`：domain_id / domain_name / budget（不含 AK/SK）
- `report_ready.available`：True 时下游可直接渲染
- `report_ready.totals` / `by_service` / `by_enterprise_project` / `by_region` / `top_resources` / `resource_diff`

### Step 3：渲染 HTML 看板

**前置条件（HARD）**：必须验证 `report_ready.available == True` 才进入 Step 3。

```bash
# Step 3-1：从 prep_output.json 提取扁平 step3_data.json
python3 <SKILL_BASE>/scripts/extract_step3_data.py \
  --input <WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/prep_output.json \
  > <WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/step3_data.json

# Step 3-2：渲染单文件 HTML
python3 <SKILL_BASE>/scripts/render_billing_html.py \
  --data <WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/step3_data.json \
  --output <WORKSPACE>/reports/{cloud}/{project}/{domain_id}/{YYYY-MM-DD}/billing-huawei-{project}-{domain_id}.html \
  --lang <output_lang>   # zh|en，跟随主理人下发的 [Output language]，缺省 zh
```

约束：
- **看板语言**：`--lang en` 渲染英文界面骨架，`zh`（默认）中文；币种自适应（CNY ¥ / USD $）由数据决定，不随语言变。须与对话/概览语言一致。
- **单文件 HTML**：所有数据内联；Chart.js 通过 jsdelivr CDN 引入。
- **全量展示**：所有云服务、企业项目、地域、四类资源变化全部出，长表用 `max-height + overflow-y: auto` 滚动。
- **币种自适应**：根据 `currency` 字段选 `¥` / `$` 符号。
- ❌ 禁止在 LLM 层手写表格；❌ 禁止在 JSON 数据层做 Top N 截断后再喂入。

---

## 三、输出契约（落盘 + 直发）

### 3.1 落盘（HARD）

唯一最终交付文件：
```
<WORKSPACE>/reports/{cloud}/{project}/{domain_id}/{YYYY-MM-DD}/billing-huawei-{project}-{domain_id}.html
```

中间数据 JSON 允许保留在缓存目录用于调试 / 复用：
```
<WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/prep_output.json
<WORKSPACE>/cache/billing-huawei/{domain_id}/{YYYYMM}/step3_data.json
```

❌ 禁止再写任何 `.md` 报告文件作为最终交付物。
❌ 禁止把 HTML 落到 `/tmp/`、用户主目录或工作区之外。

### 3.2 用户可见输出

按红线 §九"执行过程静默"，用户触发后模型的可见输出为：

按红线 §九"执行过程静默"，用户触发后模型的可见输出为（**顺序固定：先概览、后预览**）：

1. **AIOps 智能概览**（≤ 600 字 Markdown，五段式），结构与腾讯云账单专家保持一致，**先在回复框输出**：
2. **打开 HTML 看板**：概览输出完毕后，**再**通过 `present_files` 打开 `billing-huawei-{project}-{domain_id}.html`，确保概览不被预览面板遮挡
3. **PPT 导出引导（语种跟随 `output_lang`，禁止语种错配）**：概览末尾追加一句引导语——zh：`💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。`；en：`💡 Want a presentation-ready PPT? Reply "export PPT" to generate a 16:9 deck.`

   ```markdown
   ## 🔍 2026-05 华为云账单智能概览

   ### 💰 费用总览
   当月 **¥28,431** | 环比 ↑10.8%（+¥2,761）| 上月 ¥25,670
   集中度：Top 3 云服务占比 **77.4%**（ECS + RDS + EVS）

   ### 📦 云服务洞察
   | 云服务 | 当月费用 | 环比 | 占比 |
   |---|---|---|---|
   | 弹性云服务器ECS | ¥12,000 | +9.1% | 42.2% |
   | 云数据库RDS | ¥5,731 | +13.0% | 20.2% |
   | 云硬盘EVS | ¥5,000 | +11.1% | 17.6% |
   | ⚠️ 对象存储OBS | ¥3,500 | **+16.7%** | 12.3% |

   ### 🏢 企业项目分账
   default ¥18,000（63.3%）| biz-aiops ¥10,431（36.7%）

   ### 🌏 地域分布
   华北-北京四 **77.4%** → 单地域集中风险 | 华东-上海一 22.6%

   ### 🔎 资源变化 & 风险提示
   📈 增长 1 / 📉 降低 1 / 🆕 新增 1 / ⬇️ 下线 1
   - 增长最猛：ecs-prod-1 +¥1,000（ECS / default）
   - 🟡 单地域集中 77.4% + ECS 占比超 40%

   📊 完整看板见上方 HTML 预览 ↑

   💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。
   ```
   > 上为 zh 示例；`output_lang=en` 时末行改用英文引导语（见上方第 3 点）。

3. ❌ 禁止把 HTML 内文复制到对话；❌ 禁止重复展示完整数据表；❌ 禁止"附录"二次渲染。

### 3.3 终止规则（HARD）

1. **HTML 已落盘 = 立即终止**：`billing-huawei-*.html` 写盘成功 → AIOps 智能概览（先在回复框输出）→ present_files 打开 HTML（后）→ 结束。
2. **重跑 `prepare_billing_data.py` 上限 1 次**：`report_ready.available != True` 时允许重跑一次。
3. **已落盘禁止重跑**：`billing-huawei-*.html` 已存在且 > 5000 字节 → Step 3 已完成 → 禁止再跑 prepare。

### 3.4 缓存复用规则

可复用：历史月 API 结果（按 domain_id+月份做 cache key）。

禁止复用：
- 当前月账单（出账中数值会变）
- 本月 1-4 号查 T-1 月（关账期数据仍在变）

异常（读/写/JSON 损坏）→ 软失败回源，不写入用户可见错误。

---

## 四、报告归档

落盘路径：

```
<WORKSPACE>/reports/{cloud}/{project}/{domain_id}/{YYYY-MM-DD}/billing-huawei-{project}-{domain_id}.html
```

- `{cloud}`：固定 `huawei`，取 `credentials.active_cloud`
- `{project}`：账号代号（`--project` 值），取 `credentials.active_project`；用稳定 PROJECT 码而非波动域名，多账号并发不重名
  - **未传 `--project` 时**：省略 `{project}` 段，退化为 `reports/{domain_id}/{YYYY-MM-DD}/billing-huawei-{domain_id}.html`（不要留双连字符）
- `{domain_id}`：由 IAM `ListAuthDomains` 自动获取
- `{YYYY-MM-DD}`：**落盘当天**的日期（不是分析的月份），本地时区
- 同账号同天重复触发 → **覆盖**

落盘内容仅 `render_billing_html.py` 渲染产物完整原文，**禁止**夹带 prompt / 调试日志 / AK/SK / domain_id 明文。

写入异常（权限不足 / 磁盘满）→ 软失败，不向用户展示"归档失败"。

---

## 五、API 接口速查

详见 `references/api-endpoints.md`。核心接口：

| 接口 | 方法 + 路径 | 用途 |
|---|---|---|
| ListAuthDomains | `GET /v3/auth/domains` (iam.myhuaweicloud.com) | 派生 domain_id |
| ShowCustomerMonthlySum | `GET /v2/bills/customer-bills/monthly-sum` (bss) | 按云服务类型汇总账单 |
| ListCustomerselfResourceRecords | `GET /v2/bills/customer-bills/res-fee-records` (bss) | 资源消费记录 |

签名算法：SDK-HMAC-SHA256（华为云 API Gateway v3 签名规范），由 `scripts/sign.py` 实现。
