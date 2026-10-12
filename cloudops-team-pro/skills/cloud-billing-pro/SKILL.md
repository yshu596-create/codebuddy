---
name: cloud-billing-pro
description: 云账单分析专家。当用户提到"账单分析""费用分析""月度账单""账单环比""成本趋势"或要求做云账单相关操作时触发。**两种工作模式**：(1) 月度全量报告——输出**单文件 HTML 数据看板**（深色 + Chart.js + 全量资源展示），落盘工作区由 WorkBuddy 客户端预览；(2) 提示词扩展查询——支持自然语言追问，仅返回三段式纯文本，不渲染看板。
---

# 云账单分析

## 目录

```
cloud-billing-pro/
├── SKILL.md                    # 本文件:流程 + 凭证 + 约束
├── scripts/                    # Python 工具脚本
│   ├── sign.py                 # TC3-HMAC-SHA256 签名 + API 调用
│   ├── fetch_bill_detail.py    # DescribeBillDetail 批量并发拉取
│   ├── fetch_billing_apis.py   # 并发拉取所有汇总 API（被 --auto-fetch 调用）
│   ├── summarize_bill.py       # 资源数据压缩摘要
│   ├── prepare_billing_data.py # 一键数据准备（月度全量分析用，Step 0~2 合并）
│   ├── extract_step3_data.py   # 一次性提取 Step 3 报告渲染数据 JSON
│   ├── render_billing_html.py  # 把渲染数据 JSON 渲染为单文件 HTML 看板
│   ├── query_billing.py        # 提示词扩展查询入口（自然语言 → 三段式回复）
│   └── query/                  # 提示词扩展查询模块
│       ├── billing_data_loader.py  # 多月数据加载与对齐（默认回看 6 月）
│       ├── slot_dicts.py            # head/business/product 自动发现
│       ├── router.py                # 关键词 + 槽位提取 → intent_id
│       ├── handlers.py              # 16 个 P0 handler（H/B/T/A/C/I 维度）
│       └── renderers.py             # 三段式 markdown 渲染
└── references/                 # 参考文档
    ├── api-constraints.md      # API 参数约束、地域映射、高频错误
    ├── html-template.md        # HTML 看板渲染规范
    └── contracts.md            # Stage 1→2→3 数据契约（字段命名/结构/HARD 约束）
```

> **凭证源**：SSM + KMS（唯一来源）。各云账号 AK/SK 以 KMS 加密存于 SSM，脚本经 `resolve(cloud, project)` 在线读取、服务端解密。宿主只需配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`(仅 SSM 只读权限,无需 kms)。跨账号代查由目标 secret 内的 `role_arn`(+ `assume_from`)字段驱动，`resolve()` 自动 AssumeRole（详见 §1.2）。UIN / AppId / 账户昵称由 `cam.GetUserAppId` 自动获取，**不需要用户手填**。详见 §一。

> **单账号模式**：本 skill 一次只分析一个云账号——即环境变量中当前生效的那一组凭证对应的账号。多账号 `ACCOUNT_*_` 兼容路径已于 2026-06-08 移除。如需分析多个账号，请用户切换环境变量后重复触发，每次只产出当前账号的报告。

> **路径占位符约定**：本 SKILL.md 中所有命令使用以下占位符，**禁止**让调用方/agent 自己拼写绝对路径前缀：
> - `<SKILL_BASE>` = 本 skill 所在目录的绝对路径（WorkBuddy 加载 skill 时通过 `Base directory for this skill: <abs path>` 注入）。脚本调用一律写 `python3 <SKILL_BASE>/scripts/xxx.py`。
> - `<WORKSPACE>` = 当前 WorkBuddy 工作区根目录（默认 `os.getcwd()` 或环境变量 `WORKBUDDY_WORKSPACE`）。缓存目录 = `<WORKSPACE>/cache/billing/{uin}/`，归档目录 = `<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html`。
> - **文件名约定（v3 PROJECT 码）**：HTML / PPT 文件名格式 `billing-{cloud}-{project}-{uin}.html`，用**稳定的项目码 `{project}`**（如 `ADC`/`TSP`/`YX`，取自 `credentials.active_project`）而非波动的账号昵称 `{display_name}`，避免多账号 / 多云同名覆盖且命名稳定。例如：`billing-tencent-ADC-100039081678.html`、`billing-tencent-TSP-100031104408.html`。**不再用** `{display_name}` 或 `uin{uin}` 前缀。en 版加 `-en` 后缀。

---

## 🔴 全局红线（最高优先级，违反即视为事故）

### 零、output_lang 一致性（最常见 bug）
- ⚠️ **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问时 `output_lang=en`，概览必须全英文；中文提问时 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。这在中文运行环境下极易发生。
- ✅ 正确做法：在写第一个字之前，默念「`output_lang` 是什么？」，然后用该语言写。
- ✅ `--lang` 参数透传给 `render_billing_html.py` / `prepare_billing_data.py`，确保 HTML 看板界面骨架语言一致。
- ✅ 枚举型中文名（产品名、地域名、标签维度）当 `output_lang=en` 时必须翻译为英文，禁止保留中文原文。

### 一、数据真实性
- ❌ 禁止编造任何数字。费用、占比、环比差额、资源数量等数值**必须来自账单 API 返回值**。
- ❌ 禁止在没有调用 API 的情况下输出数据；禁止对 API 数值做"合理推测"或"估算"。
- ✅ API 失败 → 标注"数据获取失败"；某产品某月无费用 → 标注"✅ 无费用"或"-"，不用 0 占位。

### 二、凭证安全（零流转）
- ❌ 禁止在任何输出（HTML 看板/对话/归档/调试日志）中展示 SecretKey/SecretId/UIN 明文。
- ❌ 禁止在报告里提及"环境变量""SecretId""SecretKey"等技术细节。
- ❌ 禁止通过对话向用户索取 AK/SK；禁止把对话中出现的 AK/SK 写入任何环境变量或配置文件。
- ❌ 禁止 skill / agent 写入 `.env` 文件（无论用户是否说"帮我保存"）。
- ✅ 凭证唯一来源是环境变量，由用户自行配置；skill 只读不写。
- ✅ 缓存目录 `<WORKSPACE>/cache/billing/{uin}/{YYYYMM}/` 仅写 API 原始 JSON 返回值；写入前核对：不含 `secret_id`/`secret_key`/`credentials` 字段，不夹带 prompt / 调试日志。

### 三、HTML 看板渲染（最高优先级输出契约）
- ✅ 单文件 HTML：所有数据内联在一个 `.html` 中，仅通过 CDN 加载 Chart.js 一行 `<script>`，不依赖其他外部资源。
- ✅ 视觉风格：深色玻璃拟态 + KPI 四宫格 + 趋势/环形/对比柱图 + 数据表 + 智能洞察卡片，由 `render_billing_html.py` 统一负责。
- ✅ 全量展示：所有产品、所有标签条线、四类资源变化（增长/降低/新增/下线）必须全部渲染到 HTML，长表通过 `max-height + overflow-y: auto` 滚动呈现；**禁止 Top N 截断**。
- ❌ 禁止生成 `.md` 报告作为最终交付物（中间数据 JSON 可保留在 cache 中）。
- ❌ 禁止创建在线文档、Bot 推送或其他在线协作产物——只输出 HTML 看板。

### 四、执行纪律
- ❌ 禁止跳过 Step：流程中的 Step 0 → 1 → 2 → 3 必须按顺序，不得省略。
- ❌ 禁止因为产品太多只输出部分；账单返回的**每个产品**都要出现在「按产品拆解」表中。
- ✅ 某产品某 API 报错 → 降级处理（标注"权限不足/API 错误"），**不**中止整体流程。
- ✅ 月份未到出账日 → 标注"未出账""部分出账"，**不**用上月数值替代。

### 五、账单数值精度
- ✅ 费用展示**整数**（`¥12,580`），**不带小数点**——0.01 精度是视觉噪音。
- ✅ 占比保留 1 位小数（`56.9%`）；万元以上可同时展示万元（`¥1.26万`），表格内保留整数。
- ✅ 中间计算保留 4 位小数防浮点误差，展示层 round 到整数。
- ✅ 环比差额 = 分析月 API 值 - 对照月 API 值（直接相减，**不**二次重算）。

### 六、月份语义
- ✅ 报告头部**必须**标注 `分析窗口: {YYYY-MM} vs {prev_YYYY-MM}`。
- ✅ 默认月份语义（用户不指定时）：**T-1 月**（上月）。
- ✅ 用户说"本月/当月" → 当月 YYYY-MM，**且报告头部加一句**：`⚠️ 当月账单尚未出账，以下数据为当前已生成部分，可能不完整`。
- ✅ 用户说"X 月份"年份未明确 → 默认当前年；跨年边界（12 月用户说"1 月份"）→ 反问。
- ❌ 禁止用"当前/目前/现在"描述账单数据状态（账单有出账延迟）。

### 七、同环比口径
- ✅ **环比** = 分析月 vs 上月（默认）；**同比** = vs 去年同月（用户明示才触发）。
- ✅ **新增资源** = 分析月有费用、对照月无；**已下线** = 反之；**增长** = 两月都有且分析月 > 对照月；**降低** = 反之。
- ❌ 禁止把"环比"写成"同比"；禁止用"3 个月平均值"等其他基准计算增长/降低。

### 八、不做承诺
- ✅ 账单分析输出**已发生的事实**，不输出"可节省 ¥X"等预测——预测是 costopt 的事。
- ❌ 禁止在账单报告里出现"建议关停 / 建议降配 / 立即整改"等动作型语句。
- ✅ 异常消费可以**标注**（"某 CVM 月费 ¥10万，远高于同类资源平均值"），但**不**给修复建议。

### 九、执行过程静默
- ❌ 禁止输出过渡语（"好，先来看看凭证情况"）、执行状态（"分析月份: 3月"）、技术术语（"Step 1 完成"）、文件路径、rule_id。
- ✅ 用户触发后，模型的第一条可见输出必须是**凭证缺失指引**（若需要）或**AIOps 智能概览 + HTML 看板预览**（概览在前、预览在后）。
- 允许的输出仅以下 4 类：(1) 凭证缺失指引；(2) AIOps 智能概览（≤600字 Markdown，五段式）+ HTML 看板预览（**先输出概览文字，再通过 present_files 打开 HTML**，确保概览不被预览面板遮挡）；(3) 错误/降级提示；(4) 用户后续追问的简答。

### 十、输出完整性（HARD）
- ❌ **资源变化四张表必须列出全部 N 行，不得截断**：「成本增长资源」「成本降低资源」「新增资源」「已下线资源」四张表，标题写"共 N 个" → 数据行数必须等于 N。
- ❌ **按产品拆解、按标签维度也必须全量列出**——HTML 看板已提供滚动条承载长表，不需要数据层裁剪。
- ❌ 禁止预判超限而主动砍内容；HTML 看板的容量没有 Markdown 那样的字节限制。

### 十一、执行效率（HARD）
- **F-EXEC-COST**：Python sandbox 启动有 ~3.2s 固定开销。同一阶段计算必须合并到一条 exec，禁止逐文件/逐产品分多次 exec。
- **F-LARGE-FILE**：禁止直接 Read/cat 任何超过 **500KB** 的 JSON 文件进 context（账单明细、资源汇总等）。大文件必须通过脚本处理后取摘要（< 100KB）再进 context。
- **F-NO-BYPASS**：禁止自写脚本绕过 skill 提供的官方工具（如 `prepare_billing_data.py`）。自写脚本不含 `report_ready` 等关键字段，会触发探索循环。

---

## 工作模式分流（先判断走哪条路径）

> **关键原则**：单点提问走轻量路径，月度全量分析走重路径。**两条路径的输出形态、缓存依赖、执行步骤完全不同**，开始动手前必须先判定。

### 模式 A：月度全量账单巡检（重路径）

触发词：**"月度账单 / 月报 / 账单分析 / 上月账单 / 环比 / 成本趋势 / 整体账单"** 等"全量巡检"语义。

输出形态：**单文件 HTML 数据看板**（`<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html`）；通过 WorkBuddy 客户端预览工具展示。

执行流程：见下方 §一/二（Step 0~3，调 `prepare_billing_data.py` + `extract_step3_data.py` + `render_billing_html.py`）。

### 模式 B：提示词扩展查询（轻量路径，2026-06-05 新增）

触发词：**含具体追问的句式**，例如：
- 谁这个月花钱最多 / 按 head 拆解 / xxx 哪些资源涨了
- 各业务线成本拆解 / xxx 业务花了多少
- 异常飙升 / 增长贡献度 / 警惕 3 件事
- 没有 head 的资源 / 连续 N 月在涨 / 三个月增长最快
- 等等（PRD §3 全部 50+ 触发器，第一期落地 16 个 P0）

输出形态：**纯文本三段式**（一句话结论 + 数据明细表 + 建议/洞察），≤ 20 行；**绝不渲染 HTML 看板，绝不写盘**。

执行流程（一行调用即可）：
```bash
python3 <SKILL_BASE>/scripts/query_billing.py \
  --month 202605 \
  "andyyfang 这个月哪些资源涨了"
```

约束：
- 直接打印 stdout，不写盘、不渲染 HTML 看板
- 依赖现成的月度缓存（`<WORKSPACE>/cache/billing/{uin}/{YYYYMM}/`），不会自动拉取数据
- 缓存月数不足时自动降级（如近 3 月不全 → A-03 异常检测、T-01/T-03 趋势分析会提示数据不足）
- 路由未命中 → 输出 fallback 提示，让用户改换提法或切到模式 A

支持的 16 个 P0 触发器（首期）：
| ID | 触发示例 |
|---|---|
| H-01 | `{head} 这个月哪些资源涨了` |
| H-02 | `{head} 哪些资源降了` |
| H-03 | `{head} 这个月新增了哪些资源` |
| H-04 | `{head} 本月花了多少` |
| H-06 | `谁这个月花钱最多` |
| H-08 | `没有 head 的资源` |
| H-10 | `按负责人拆解 / 每个 head 的成本拆解` |
| B-01 | `{business} 业务花了多少` |
| B-02 | `{business} 哪些资源涨了` |
| B-04 | `各业务线成本拆解` |
| T-01 | `这三个月什么资源增长最快` |
| T-03 | `连续 N 个月在涨的资源` |
| A-03 | `本月异常飙升的资源` |
| A-05 | `本月新增资源中花钱最多的` |
| C-05 | `本月增长贡献度 Top N` |
| I-04 | `本月最该警惕的 3 件事` |

### ❌ [HARD] F-WRONG-MODE：不要把模式 B 当模式 A 跑

- 用户问"andyyfang 哪些资源涨了"是 **轻量查询**，**禁止** 跑 `prepare_billing_data.py` + 渲染 HTML 看板
- 一旦走错模式，会浪费数十秒执行时间且产生不必要的看板文件
- 判定标准：用户语句里如果**含 head/business 名 + 具体动作（涨/降/新增/花了多少）**，或者命中上面 16 个触发器示例中的句式，**必须走模式 B**
- 模式 B 失败（路由未命中 / 数据不足）时，**先告知用户**，再询问是否切到模式 A，**不要静默切换**

---

## 专属约束

> 全局约束见本文件顶部「全局红线」章节。

- 汇总 API 用 `BeginTime/EndTime`("YYYY-MM-DD HH:MM:SS"),禁止用 Month
- 资源明细 API 用 `Month`("YYYY-MM"),禁止用 BeginTime/EndTime
- HTML 看板的月份列头用 `YYYY-MM`（如 `2026-04`），禁止用"本月/上月"等相对表达
- 禁止展示优惠/激励抵扣、现金支付等费用细项
- 禁止展示 proj 标签
- 禁止输出降级话术（"文档内容较大""已写入摘要版本"等）
- 执行过程静默：从凭证获取到看板生成，禁止输出中间状态。用户输入后下一条可见输出直接是 AIOps 智能概览（≤600字 Markdown，五段式）+ HTML 预览（概览在前、present_files 打开 HTML 在后）
- **API 结果落盘缓存**：历史月账单不变，每次都重拉纯属浪费。规则见下方缓存策略；本 skill 自己查文件/写文件，不额外引 Python 工具：
  - 跳过缓存（不读不写）的唯二情况：**查询当前月** 或 **本月 1-4 号查 T-1 月**（关账期数据仍在变，5 号起 T-1 月视为已稳定）
  - 其他历史月：`test -f {cache_path}` 命中 → `cat` 读；未命中 → 调 API，拿到 JSON 后写盘
  - 用户说 "重新分析 / 刷新 / 不走缓存 / refresh / 忽略缓存" → 先 `rm -rf <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/`,再走正常流程
  - 写盘内容**只能**是 API 原始 JSON 返回值,禁止夹带 SK / prompt / session label / 调试日志

---

## ⚡ 执行效率 HARD 约束（2026-06-01，每次违反都会导致超时）

> 下列约束是历史多次超时/context overflow 的根因修复。**优先级高于一切探索习惯**。
> 通用原则（碎片 exec / 大文件 / 自写脚本）见顶部「全局红线 §十一 执行效率」；本节只记录 billing 专属细节。

### ❌ [HARD] F-EXEC-FRAG：billing 数据准备必须用 prepare_billing_data.py

> 通用原则见顶部「全局红线 §十一 F-EXEC-COST」。billing 专属规则：

✅ **唯一合法的数据准备方式**（Step 0~2 全部数据）：
```bash
python3 <SKILL_BASE>/scripts/prepare_billing_data.py \
  --cloud tencent --project <CODE> \   # 必传：进程内 resolve(cloud, project) 从 SSM 取凭证(KMS 服务端解密)，规避并发竞态
  --month 202605 \
  --lang <output_lang>   # zh|en，跟随主理人 [Output language]；凭证缺失时据此返回对应语种引导，缺省 zh
# 一次调用，输出包含 report_ready 字段，不塞 context
# 凭证唯一来源是 SSM，宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY)；--project 必传，无"省略读裸名"回退
```

**唯一允许单独 exec 的场景**：Step 2c 标签查询（`fetch_tags_for_resources`）、Step 2d 计费明细（`fetch_bill_detail.py`）。其他任何场景禁止。

### ❌ [HARD] F-NO-CUSTOM-SCRIPT：禁止自写脚本绕过 prepare_billing_data.py（2026-06-01 新增）

> 通用原则见顶部「全局红线 §十一 F-NO-BYPASS」。billing 专属根因：

**违规案例（t_f28b336e）**：agent 自写 `fetch_202605.py`、`build_report.py`，自写脚本输出**没有 `report_ready` 字段** → agent 感知"数据不够"继续探索 → `bill_details.json` 22MB 进 context → compacting 循环 → 报告写到 `/tmp/` → `protocol_violation`。

❌ **禁止**：自写任何 `fetch_*.py` / `build_*.py` / `prep_*.py` 替代 `prepare_billing_data.py`，原因：自写脚本没有 `report_ready` 字段，会触发探索循环。

### ❌ [HARD] F-EXPLORE-LOOP：禁止在快速路径下"探索式摸底"

当 `report_ready.available == True` 时，所有数据已由 `prepare_billing_data.py` 预计算完毕。此时**禁止**：
- 再次 cat / Read / exec 任何缓存文件
- 自己 grep / summarize_bill.py 做二次计算
- 手写 `refetch_tag.py`、`fetch_*.py` 等新脚本重拉已有数据

> **根因**：agent 不信任 `report_ready` 输出，自己从零探索，导致 14 次碎片 exec，context 被压缩 5 次，任务耗时超过 1 小时。

✅ 快速路径下：`report_ready.*` 字段直接拼报告，不要二次验证。如果数据看起来空或不对，先检查 `cache_inventory` 是否缺文件，缺了就补调 API，而不是绕过 `prepare_billing_data.py` 自己读文件。

### ❌ [HARD] F-TAG-REFETCH：summary_tag 缺失时禁止手写脚本重拉

**正确做法**（缺 summary_tag 缓存时）：
```bash
# 1. 检查缺哪些月份
ls <WORKSPACE>/cache/billing/{uin}/

# 2. 按 SKILL.md §Step 1 缓存读写规则补调 API（Bash + sign.py）
# 3. 写入 summary_tag.json 后，重新运行 prepare_billing_data.py
```

**禁止做法**：
- 手写 `refetch_tag.py` 脚本（重复造轮子，且不更新缓存）
- exec 里直接调 sign_and_call 读 summary_tag（碎片化）
- 绕开 prepare_billing_data.py 自己加载和合并标签数据

---

## 一、凭证（单账号模式，双路径）

> 凭证从**环境变量**获取（用户在 shell rc / IDE / WorkBuddy 工作区配置中导出）。
> 多账号 `ACCOUNT_*_SECRET_ID/KEY/UIN/DISPLAY_NAME` 兼容路径已于 2026-06-08 移除——一次只分析一个账号。

### 1.1 路径 1：配置 runner key（推荐，本账号自查）

各云账号 AK/SK 以 KMS 加密存放在 SSM；脚本进程内 `resolve(cloud, project)` 从 SSM 在线读取并由服务端解密。宿主只需配一把 runner key：

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` | ✅ | runner key 的 SecretId（仅需 SSM 只读权限，无需 kms） |
| `TENCENTCLOUD_BOOTSTRAP_SECRET_KEY` | ✅ | runner key 的 SecretKey |
| `TENCENTCLOUD_MONTHLY_BUDGET` | ❌ | 月度预算（元），配置后报告头部展示消耗率 |

> 配好 runner key 即自动启用 SSM，无需 `CLOUDOPS_SSM_ENABLED=true`(可选急停开关，设 false/0/no/off 才关)。业务账号 AK/SK 只存于 SSM，不在终端 export 明文。

**自动获取的字段**（由 `prepare_billing_data.py` 调用 `cam.GetUserAppId` 拉取）:
- `uin` / `app_id` / `display_name`(Nickname)

**所需权限**:
- runner key（宿主侧）：仅 SSM 只读 `ssm:GetSecretValue`/`ListSecrets`/`ListSecretVersionIds`，无需 kms。
- 业务账号密钥（存于 SSM）：授 `QcloudFinanceFullAccess`(账单只读)即可，`GetUserAppId` 不需要额外授权。

### 1.2 路径 2：STS AssumeRole（跨账号代查 / 最小权限收敛）

跨账号代查已**收敛进 `build_credential_env.resolve()`**：脚本不再读任何 `TENCENTCLOUD_ROLE_ARN` 环境变量，而是看目标账号的 **SSM secret** 里有没有 `role_arn` 字段。配置全部落在 SSM secret JSON 内：

| secret JSON 字段（`cloudops/tencent/<CODE>`） | 必填 | 说明 |
|---|---|---|
| `role_arn` | ✅（启用跨账号时） | 目标账号角色 ARN，如 `qcs::cam::uin/100099999999:roleName/BillingReader` |
| `assume_from` | 推荐 | 调用方**主账号 AK** 所在 secret 的项目码（如 `PARENT`）。`resolve()` 先取 `cloudops/tencent/PARENT` 的 `secret_id/secret_key` 当调用方 AK 再 assume。主账号 AK 只存一份、轮换只改一处 |
| `secret_id` / `secret_key` | 视情况 | 不写 `assume_from` 时，本字段须为能 assume 目标角色的**调用方主账号 AK**；写了 `assume_from` 则本 secret 可只放 `role_arn` |
| `role_external_id` | ❌ | 角色绑定的 ExternalID（建角色时勾选了才必填） |
| `role_duration` | ❌ | 临时凭证有效期（秒），默认 3600 |

**目标账号建角色步骤**:
1. 到[腾讯云 CAM 角色控制台](https://console.cloud.tencent.com/cam/role) 新建角色
2. 角色载体选「腾讯云账户」→ 输入**调用方主账号 UIN**
3. 授权:`QcloudFinanceFullAccess` + `QcloudCamReadOnlyAccess`(后者用于 `GetUserAppId`)
4. (可选)勾选「需要 ExternalID」加一道保险
5. 记下 `RoleArn`,把它写进目标账号 SSM secret 的 `role_arn` 字段（连同 `assume_from`）

**执行流**:脚本 `--project <CODE>` 运行 → `resolve()` 取 secret，发现 `role_arn` → 在**进程内**用（`assume_from` 指向的或本 secret 内的）主账号 AK 调 `sts.AssumeRole` 拿 `TmpSecretId` / `TmpSecretKey` / `Token` → 把临时凭证作为返回值，并**剔除 role_arn**（防下游 double-assume） → `resolve_credentials` 用临时凭证调 `cam.GetUserAppId` 派生目标账号 UIN → 后续所有账单 API 都用临时凭证 + `X-TC-Token` 头签名。**不会**用主账号 AK 直签目标账号 API。

> ⚠️ 控制台「角色 SwitchRole」(点链接 + 微信确认)≠ STS AssumeRole(API)。控制台切换是 cookie 级身份,脚本拿不到;要让 skill 跨账号查账单,**必须走 STS AssumeRole**。

> ⚠️ **禁止向用户索取 UIN / AppId / 账户昵称**。这些信息必须通过 API 自动获取。如果用户主动提供 UIN，仅作展示名校验，不影响主流程。

### 1.3 凭证查找流程

`prepare_billing_data.py::resolve_credentials` 解析顺序:

1. **读取凭证**（进程内隔离，凭证唯一来源 = SSM，跨账号已在 resolve 内完成）:
   - 传了 `--project <CODE>` → 调 `build_credential_env.resolve()` 从 **SSM** 取该项目专属凭证（KMS 服务端解密，并发场景唯一正确源头，绝不读裸名 env）
   - 若该 secret 带 `role_arn`，`resolve()` 已在进程内 `AssumeRole` 换好**临时凭证 + token**并返回（role_arn 被剔除）；本层直接消费，不再二次 assume
   - `--project` 必传——SSM 按 `cloud/project` 定位密钥，**不存在"省略 → 读裸名"的回退路径**（旧 env-prefix 模型已废弃）
   - 解析失败（缺 runner key / SSM 无此账号 / AssumeRole 失败，退出码 5/4/6）即返回 `need_credentials`
2. **判断路径**:`token` 非空 → 路径 2（resolve 已 assume 的跨账号临时凭证）；`token` 为空 → 路径 1（直连本账号 AK/SK）。本层**不再自己 assume**
3. **UIN 自动补全**:用当前生效凭证(主 or 临时) + 可能的 token 调 `cam.GetUserAppId`,填 `uin` / `app_id` / `nickname`
4. **预算可选**:读取 `TENCENTCLOUD_MONTHLY_BUDGET`,缺失时不展示消耗率
5. **缺失态**:任一步失败即返回 `status: "need_credentials"` + `prompt`(SKILL 渲染给用户的友好引导)

返回 dict 关键字段:`status` / `display_name` / `uin` / `app_id` / `secret_id` / `secret_key` / `token`(STS 才有) / `auth_path`(`direct`|`sts`) / `budget`。

### 1.4 凭证缺失指引（友好引导模式）

环境变量没配置时:脚本输出 `need_credentials` JSON,其中 `prompt` 字段固定包含双路径引导文本(agent 收到后**原文返回给用户**,禁止改写或裁剪)。**`prompt` 语种由 `--lang` 决定**(zh=中文 / en=English,跟随主理人 `[Output language]`)——英文提问传 `--lang en`,脚本返回经安全审校的确定性英文版,agent 原文返回即语种正确,**禁止自行翻译**(翻译会让"别贴 AK/SK""SwitchRole≠AssumeRole"等安全警告漂移)。完整模板见 `scripts/prepare_billing_data.py::_build_need_credentials_prompt`(中文) / `_need_credentials_prompt_en`(英文)。模板核心结构:

```markdown
环境检测仍然没有 AK/SK，账单分析没法跑起来。

## ⚠️ 环境未就绪：凭证后端未配置

本专家团以 **SSM + KMS 作为唯一凭证源**:各云账号 AK/SK 以 KMS 加密存放在腾讯云凭据管理服务(SSM),专家运行时通过一把 **runner key** 在线读取并由 SSM 服务端解密。要分析您账号的真实账单，请按场景选一种配置：

### 路径 1：配置 runner key（最简单，本账号自查）

到 [腾讯云 CAM 控制台](https://console.cloud.tencent.com/cam/capi) 获取一把子账号 SecretId/SecretKey 作为 runner key（仅需 SSM 只读权限 `ssm:GetSecretValue`/`ListSecrets`/`ListSecretVersionIds`，**无需任何 kms 权限**——解密在 SSM 服务端完成），然后按你的操作系统选一种：

**Linux / macOS（bash / zsh）**

​```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="your-runner-key-id"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="your-runner-key-secret"
# 可选：月度预算（元），配置后报告头部展示消耗率
export TENCENTCLOUD_MONTHLY_BUDGET="50000"
​```

**Windows（PowerShell，仅当前会话生效）**

​```powershell
$env:TENCENTCLOUD_BOOTSTRAP_SECRET_ID="your-runner-key-id"
$env:TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="your-runner-key-secret"
# 可选：月度预算（元）
$env:TENCENTCLOUD_MONTHLY_BUDGET="50000"
​```

**Windows（CMD，仅当前会话生效）**

​```cmd
set TENCENTCLOUD_BOOTSTRAP_SECRET_ID=your-runner-key-id
set TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=your-runner-key-secret
rem 可选：月度预算（元）
set TENCENTCLOUD_MONTHLY_BUDGET=50000
​```

> ✅ 配好这两行 runner key 即自动启用 SSM,无需 `CLOUDOPS_SSM_ENABLED=true`(它只是个可选急停开关,设为 false/0/no/off 才会关闭 SSM)。业务账号 AK/SK 只存于 SSM,**不要**在终端 export 明文 AK/SK。

UIN / AppId / 账户昵称会自动通过 `cam.GetUserAppId` 识别，不用手填。

### 路径 2：STS AssumeRole（跨账号代查 / 最小权限收敛）

跨账号配置全部落在 **SSM secret JSON**，不再用环境变量 export（旧 `export TENCENTCLOUD_ROLE_ARN` 模式已废弃——脚本只从 SSM 取，不读裸名 env）。

**步骤 A**：到腾讯云 CAM 角色控制台 新建角色
- 角色载体：「腾讯云账户」→ 输入调用方主账号 UIN
- 授权：QcloudFinanceFullAccess + QcloudCamReadOnlyAccess
- （可选）勾选「需要 ExternalID」加一道保险
- 记下 RoleArn：qcs::cam::uin/100099999999:roleName/BillingReader

**步骤 B**：把跨账号配置写进**目标账号的 SSM secret** `cloudops/tencent/<CODE>`（SecretString JSON）：

​```jsonc
{
  // 目标账号要 assume 的角色（必填，启用跨账号）
  "role_arn": "qcs::cam::uin/<目标账号UIN>:roleName/<角色名>",
  // 推荐：调用方主账号 AK 所在 secret 的项目码（主账号 AK 只存一份）
  "assume_from": "PARENT",
  // 可选
  "role_external_id": "...",
  "role_duration": "3600"
}
​```

调用方主账号 AK 单独存在 `cloudops/tencent/PARENT` 的 `secret_id`/`secret_key` 里（一份，轮换只改这一处）。

> 也可不用 `assume_from`，把调用方主账号 AK 直接写进目标 secret 的 `secret_id`/`secret_key` —— 但那样每个目标 secret 都重复存一份主账号 AK，轮换时要改 N 处，不推荐。

宿主机器仍只配 runner key（`TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`），**不**在终端 export 任何业务 AK/SK / ROLE_ARN。`resolve()` 看到 secret 里的 `role_arn` 会自动调 STS AssumeRole 换临时凭证再查目标账号账单，不会用主账号 AK 直签目标账号 API。

> ⚠️ 注意：控制台「角色 SwitchRole」（点链接 + 微信确认）≠ STS AssumeRole（API）。
> 控制台切换是 cookie 级身份，脚本拿不到；要让 skill 跨账号查账单，必须走 STS AssumeRole。

**一旦配置完成，账单分析专家会输出**

| 维度 | 内容 |
|---|---|
| 💰 总费用 | 月度总额 + 环比 + 预算消耗率 |
| 🌏 按地域 | 各地域费用 + 占比 |
| 📦 按产品 | Top 10 产品费用 + 环比 + 占比 |
| 📈 近 3 月趋势 | Top 10 产品 sparkline |
| 🏷️ 按标签分账 | head / business 维度分账 |
| 🔥 Top 10 资源 | 最贵资源 + 产品 + 环比 |
| ⚖️ 环比变化 | 增长 / 降低 / 新增 / 已下线四类完整列表 |
| 📝 总结洞察 | 涨幅最大、未享受折扣产品 |

之后您还可以直接追问：「谁这个月花钱最多」「andyyfang 哪些资源涨了」
「本月最该警惕的 3 件事」「连续 3 月在涨的资源」等 16 个 P0 触发器（轻量回复，不出文档）。

**请确认下一步走哪条：**
1. 路径 1（配置 runner key · 本账号自查）？— 配置完告诉我，立刻启动账单分析
2. 路径 2（AssumeRole 跨账号代查）？— 需要先在目标账号建角色，建完告诉我 RoleArn
3. 先看一份样例账单报告了解输出格式？
```

**铁律**：
- ❌ **禁止**通过对话向用户索取 AK/SK / UIN / AppId
- ❌ **禁止**主动让用户编辑 `.env`（除非用户明确询问"放在哪个文件"）
- ❌ **禁止**自行创建或修改 `.env` 文件
- ❌ **禁止**用主账号 AK 直签目标账号 API（跨账号必须走 STS）
- ✅ 凭证唯一来源是环境变量，由用户自行决定写入 shell rc / `.env` / IDE 配置等位置
- ✅ STS 临时凭证仅在脚本进程内存存活,不落盘、不进缓存 JSON

凭证缺失时输出 `prepare_billing_data.py` 返回的 `prompt` 字段（友好引导话术），原文返回给用户，**禁止**改写或裁剪。

---

## 二、分析流程

### Step 0~2 一键加载（prepare_billing_data.py）

**默认走 `--auto-fetch`，缓存空时自动并发拉取所有汇总 API**，无须 agent 拼装临时脚本：

```bash
# ⚠️ 并发隔离（2026-06-13）：prep 输出必须落盘到 per-project 路径，禁止共用裸 <WORKSPACE>/prep_output.json
#    project 在任务起点即已知且并发各 worker 互不相同 → 用它做隔离键，从根上规避第二层竞态
python3 <SKILL_BASE>/scripts/prepare_billing_data.py \
  --cloud tencent --project <CODE> \   # 必传：进程内 resolve(cloud, project) 从 SSM 取该项目凭证（KMS 服务端解密，并发隔离）
  --month 202604 \
  --auto-fetch \
  --lang <output_lang> \  # zh|en，跟随 [Output language]；凭证缺失引导话术据此出对应语种，缺省 zh
  > <WORKSPACE>/cache/billing/<CODE>/prep_output.json   # per-project 落盘，并发不冲突
# --cache-only --month 202604     # worker 已拿凭证、只走缓存的场景
# 凭证唯一来源是 SSM，宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY)；--project 必传，无"省略读裸名"回退
```

`--auto-fetch` 行为：
- 若 `<cache_base>/{analysis_month}/summary_product.json` 或 `<cache_base>/{comp_month}/summary_product.json` 缺失 → 自动调用 `fetch_billing_apis.py::fetch_all` 并发拉 product/region/tag/resource（含 T-3..T-1 趋势月），限频自动退避
- 已有缓存 → 跳过 fetch，直接走原逻辑
- fetch 结果记到输出 JSON 的 `auto_fetch_result` 字段（含 `ok` / `missed`），后续若有缺失自动重试

**输出 JSON 包含**（详见 `references/contracts.md`）：
- `credentials`: 账户凭证（uin/app_id/display_name/budget + **active_cloud/active_project**，后两者供落盘命名直接取用），缺失时返回 `need_credentials`
- `analysis_summary` / `comparison_summary`: 汇总数据（产品/地域/标签）
- `analysis_resources`: 分析月 Top 50 资源 + 统计
- `comparison_resources`: 对照月统计
- `resource_diff`: 预计算的环比分类（growth/decline/new/offline/stable，含 Diff/Ratio/Tags）
- `trend`: Top 10 产品近 3 月趋势
- `cache_inventory`: 各月缓存文件清单（帮助判断是否需要 API 拉取）
- `skip_cache`: 是否跳过缓存
- `report_ready`: **报告就绪 dict**（v2 新增,缓存齐全时自动可用）— 见下方 §Step 3a。

**使用策略**：
1. 先运行 `prepare_billing_data.py`，检查 `cache_inventory` 和 `skip_cache`
2. 若 `report_ready.available == True` **且** 不需要 Step 2c 资源标签覆盖 → **跳过任何 LLM 探索式分析**,直接用 `report_ready.*` 字段渲染报告
3. 若缓存缺失 → 按原 Step 1/2 逻辑调 API 补数据，再重新运行本脚本
4. Step 2c（标签查询）和 Step 2d（计费明细）不在本脚本范围内，需单独调用

### ⚡ Cache-Hit 快速路径

**触发条件**（全部满足）：
- `prepare_billing_data.py` 输出 `report_ready.available == True`
- `skip_cache == False`（分析月不是当前月，也不在月初 1-4 号）
- `cache_inventory[analysis_month]` 包含 summary_product / summary_region / summary_tag / resource_summary
- `cache_inventory[comparison_month]` 包含 summary_product

**快速路径执行**（目标 ≤ 60s LLM 时间）：

> ❌ [HARD] 违反本节任何一条 = 违反 F-EXEC-FRAG / F-EXPLORE-LOOP（见上方效率约束节）

1. **零探索**：不要再 cat / Read / exec 任何缓存文件做"摸底"，`report_ready` 已经把展示前的最后一公里算好了。**不信任 report_ready 自行探索是历次超时的根因**。
2. **直接进 Step 3**：调 `extract_step3_data.py` → 拿到渲染数据 JSON → 调 `render_billing_html.py` → 落盘 HTML → 用 preview 工具展示给用户。
3. **HTML 渲染由脚本统一负责**，LLM 不再手写表格、不再做 `ProductCode → ProductName` 反查、不再决定 Top N，全部交给 `render_billing_html.py`。
4. **总结/洞察文案** 直接用 `report_ready.summary_highlights` + `report_ready.insights`，**不要**自己再分析数据找"最贵资源/涨幅最大"。

### Step 0：解析月份 + 预算

```
账号解析：单账号模式，凭证由 `--project <CODE>` 从 SSM 取得，无须用户传 account_name
月份判定：未指定 → 上月(T-1) | "本月" → 当月（标注 ⚠️ 未出账） | 指定月份 → 按要求
预算读取：用户指定 > TENCENTCLOUD_MONTHLY_BUDGET 环境变量 > 不展示

缓存控制：
  若用户原话含「重新分析 / 刷新 / 不走缓存 / refresh / 忽略缓存」
    → rm -rf <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/
  记录 SKIP_CACHE = (YYYYMM == 当前月) or (今天 1-4 号 and YYYYMM == T-1 月)

执行流程：Step 1 → Step 2 → Step 3（单账号一次走完）
```

### Step 1:拉取汇总数据

**必须并行调用**（所有无依赖的 API 同时发起，不要串行等待）,参数详见 `references/api-constraints.md`:

```
分析月 + 对照月 + T-3月 + T-4月:DescribeBillSummaryByProduct(各一次)
分析月:DescribeBillSummaryByRegion
标签:DescribeTagKeys → 过滤系统/测试标签 → 逐个调 DescribeBillSummaryByTag 验证有账单数据 → 有效 TagKey **分析月+对照月各调一次** DescribeBillSummaryByTag
  ❌ 禁止只拉分析月不拉对照月(否则标签分账无法展示环比差额)
  ✅ 标签分账表必须包含 {prev_MM}月费用 列和环比差额列
  ❌ [HARD] 禁止硬编码标签键(如 customer/head)——不同账户有效标签键不同,必须从 DescribeTagKeys 动态发现
```

**缓存读写(每次 API 调用前后):**

```
对每个 (yyyymm, api_name) 组合:
  cache = <WORKSPACE>/cache/billing/{uin}/{yyyymm}/{api_name}.json
    api_name ∈ { summary_product, summary_region, summary_tag }

  若 SKIP_CACHE(Step 0 判定) → 跳过下列读写,直接调 API
  否则:
    test -f {cache} && 用 Read 读入 → 作为 API 结果,跳过本次实际调用
    未命中 → 调 API → 写入 {cache}(mkdir -p + 写 JSON 原文)

标签类(summary_tag)特殊:
  把"所有 TagKey × 分析月/对照月"合并成单个 dict 写入 summary_tag.json
  结构建议:{ "{TagKey}": { "{yyyymm}": <API 原始返回>, ... }, ... }
  任一 TagKey 的任一月命中缺失 → 重调缺失部分 → 合并写回
```

**API 顶层结构差异(踩过的坑,2026-05-22 发现并修复)**:

不同 Bill API 的 JSON 顶层结构**不一致**,`summarize_bill.py` 必须用 `data.get("Response", data)` 这种"两种结构都兼容"的写法读取,**不要直接 `data["Response"]`**:

| API | 缓存文件 | 顶层结构 |
|---|---|---|
| DescribeBillSummaryByProduct | `summary_product.json` | `{"Response": {"SummaryOverview": [...]}}` |
| DescribeBillSummaryByTag | `summary_tag.json` | `{"{TagKey}": {"Response": {"SummaryOverview": [...]}}}` |
| **DescribeBillSummaryByRegion** | **`summary_region.json`** | **`{"Ready": 1, "SummaryOverview": [...], "RequestId": "..."}`** ← 没有 `Response` 包装 |

**症状**: 如果 `summarize_bill.py` 把某个维度数据原样塞进 `summary_*_raw` 字段(而不是 `summarize_*` 处理后的扁平化结构),报告生成 LLM 大概率会因看不懂嵌套结构 + `_raw` 后缀的"未处理信号"而**整段忽略**,导致报告里某维度被错误标成"未细分/无数据"。

**修复**: 所有维度都要走 `summarize_*` 函数压成扁平化 `{"<维度>": [...], "count": N}` 结构,字段名统一不带 `_raw` 后缀。新增 API 也照此规约。


### Step 2:拉取资源明细

**分析月 + 对照月同时拉取**（两个月份并行调用） DescribeBillResourceSummary(不拉对照月会导致全部误判为新增)。

```
分页终止:ResourceSummarySet 为空(Total 可能为 None,不可用)
聚合:按 ResourceId 合并多行,求和 RealTotalCost
对齐:双向 full join → 分类(growth/decline/new/offline/stable)
阈值:差额 > ¥1 才算增长/降低;上月 < ¥1 的增长资源显示「近新增」
```

**资源数据加载流程（必须按此顺序执行，不得跳过任何步骤）：**

```
对每个 yyyymm(分析月 / 对照月):

  Step A — 缓存检查:
    cache = <WORKSPACE>/cache/billing/{uin}/{yyyymm}/resource_summary.json
    若 SKIP_CACHE → 跳过缓存直接 Step C
    否则: test -f {cache}
      命中 → 直接 Step B（禁止 cat/Read 读原文）
      未命中 → Step C

  Step B — summarize（缓存命中路径）:
    执行:
      python3 <SKILL_BASE>/scripts/summarize_bill.py \
        <WORKSPACE>/cache/billing/{uin}/{yyyymm} --top 50 --month {yyyymm}
    把脚本输出（JSON 摘要, < 100KB）作为该月资源数据进 context
    输出包含两个字段：
      - resources： top 50 资源（字段完整）——用于成本排行展示
      - all_slim：全量 596 条资源（仅 ResourceId + ProductName + RealTotalCost + Tags）——用于环比分析
    原始 resource_summary.json（800KB）❌ 禁止直接读入 context
    → 完成后跳到下一个 yyyymm，或进入 Step 2c

  Step C — API 拉取（缓存未命中路径）:
    分页调用 DescribeBillResourceSummary，循环直到 ResourceSummarySet 为空
    聚合完成后写盘 → cache（写原始列表，供后续 Step 2d 复用）
    然后执行 Step B（对刚写入的缓存 summarize）
```

> 为什么要 summarize：原始 resource_summary.json 达 800KB+，直接进 context 会消耗
> 大量 token 并显著拖慢后续所有推理步骤。summarize 后仅 ~3KB，信息损失 < 5%。

### Step 2c：资源标签（动态标签列）

```
tag.tencentcloudapi.com / v2018-08-13 / DescribeResourceTagsByResourceIds
  查询范围：所有出现在增长/降低/新增列表中的资源 ID（不能只查 Top N，必须全查）
  每批 ≤ 50 个 ResourceId,提取所有有效标签键(由 Step 1 DescribeTagKeys 发现)
  API 报错 → 降级为「未打标」,不中止
  若 DescribeTagKeys 返回空或全部标签无账单数据,从资源级标签数据聚合标签分账
  ❗ 每个资源都必须有所有有效标签键的值（查不到填「未打标」）
  ❌ [HARD] 禁止硬编码 customer/head——标签列由 Step 1 动态发现的标签键决定
```

### Step 2d:计费明细

```
billing / DescribeBillDetail {Month, ResourceId, Limit:100}
  必须按 ResourceId 逐个拉取,禁止按 BusinessCode 批量拉
  只拉增长额 Top 12 且 diff > ¥10 的资源
  并发度 ≤ 5
```

**缓存读写(skill 自己做文件级命中,脚本零改动):**

```
cache_dir = <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/detail/

若 SKIP_CACHE(分析月为当前月 或 本月 1-4 号查 T-1):
  miss_ids = 待查的全部 Top 12 resource_ids
  直接调 fetch_bill_detail.py,不读不写 cache_dir
  (即便脚本成功返回也不要落盘 — 统一由 Step 0 的 SKIP_CACHE 开关控制)

否则:
  mkdir -p {cache_dir}
  hit = {}; miss_ids = []
  for rid in top12_resource_ids:
    f = {cache_dir}/{rid}.json
    若 test -f {f} → 读入 hit[rid];否则 → miss_ids.append(rid)

  若 miss_ids 非空:
    result = fetch_bill_detail({YYYYMM}, miss_ids, sid, sk, concurrency=5)
    # 脚本签名不变:(month, resource_ids, secret_id, secret_key, concurrency)
    for rid, detail in result.items():
      写 {cache_dir}/{rid}.json(JSON 原文)
      hit[rid] = detail

  all_details = hit   # 最终用 hit 完整还原 Step 2d 产出
```

**⚠️ 脚本分发与调用规则:**
- `scripts/fetch_bill_detail.py` 随 skill 一起分发,**直接 import 调用**(签名:`fetch_bill_detail(month, resource_ids, secret_id, secret_key, concurrency=5) -> dict[resource_id, detail_list]`)
- 本脚本保留的唯一理由:**真正的并发 + 分页 + 指数退避重试**,agent 用 Bash/Read 做不出等价行为;其他拉取类操作必须走 SKILL.md prompt + Read/Bash,禁止再新建脚本
- 修改条件:仅当 API 参数约束变更(如新增字段)时才重写
- **脚本内部不关心缓存**:文件命中/回源/写盘由本 SKILL(agent)在调用前后用 Read/Bash 完成,脚本签名和职责保持纯粹
- **SK 只以函数参数形式传入,禁止经由 argv / 环境变量 / 临时文件**（凭证零流转红线延伸）

### Step 2e:数据核查

```
核查 1: 产品级 ≈ 资源级(Top 10 产品,差额 < ¥10)
核查 2: 分类全覆盖(新增+增长+降低+不变 == 全集)
核查 3: 新增 < 30%(否则疑似聚合 bug)
核查 4: 降低不为空(>50 资源时)
  核查结果不输出给用户
```

### Step 3：渲染单文件 HTML 看板

> 本 skill 的最终交付物是**单文件 HTML 数据看板**（`billing-{cloud}-{project}-{uin}.html`），由
> `extract_step3_data.py` 输出 JSON → `render_billing_html.py` 渲染 HTML 两步完成。
> ❌ 禁止生成任何 `.md` 报告作为最终交付物；❌ 禁止逐字段读 `prep_output.json` 自渲染。

#### Step 3-0：进入 Step 3 前的硬性前置条件

> ❌ [HARD] **F-STEP3-PREREQ：进入 Step 3 渲染前必须验证 `report_ready.available == True`**
>
> **违规根因（t_f28b336e）**：agent 绕过 `prepare_billing_data.py` 自写脚本，导致没有 `report_ready` →
> 进入 Step 3 反复探索数据 → 22MB 原始数据进 context → compacting 循环 → `protocol_violation`。

✅ 验证逻辑：
```python
assert prep_data.get('report_ready', {}).get('available') == True, \
    "report_ready.available != True，禁止进入 Step 3 渲染。唯一合法动作：重跑 prepare_billing_data.py"
```

❌ 若 `report_ready.available != True`，唯一合法动作：
```bash
python3 <SKILL_BASE>/scripts/prepare_billing_data.py --month YYYYMM
```

❌ 在 `report_ready` 未通过验证时，禁止：
- 进入 Step 3 渲染
- 自写任何替代脚本（`build_report.py` / `fetch_202605.py` 等）
- 直接 Read 任何 > 500KB 的 JSON 缓存文件

#### Step 3-1：一次性提取看板数据 JSON（MUST）

> ❌ [HARD] **F-STEP3-EXTRACT：必须用 `extract_step3_data.py` 获取看板数据，不得逐字段探索**
>
> **违规根因（t_e0618181）**：agent 在 Step 3 用 44 次 execute_code 逐字段读 `prep_output.json`
> （每次 3.2s sandbox 开销）→ 60 轮 max_turns 耗完 → 任务截断。

✅ 唯一合法的数据提取方式：
```bash
# 第 2 个位置参数 = prep_output 路径（并发隔离）：显式传 per-project prep 文件，
#   extract 不再默认读共享 <WORKSPACE>/prep_output.json，规避并发 fan-out 第二层竞态。
python3 <SKILL_BASE>/scripts/extract_step3_data.py \
  <WORKSPACE> \
  <WORKSPACE>/cache/billing/<CODE>/prep_output.json \
  > <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/step3_data.json
# 单账号旧入口可省略第 2 参数，脚本退化读 <WORKSPACE>/prep_output.json
```

`step3_data.json` 已包含看板渲染所需的全部字段：
- `analysis_month` / `account_name` / `totals`（KPI）
- `top_products[]`（全部产品，看板侧不截断）
- `product_trend.*` + `trend_months_ordered`（近 N 月趋势线）
- `top_regions[]`（地域分布环图）
- `summary_tag.{TagKey}[]` + `effective_tag_keys`（标签分账，全部条线）
- `resource_diff.{growth,decline,new,decommissioned}.items[]`（四类资源变化，含 `_tags` 字段）
- `top10_resources[]`（成本 Top 10 资源 + ProductName 反查）
- `no_discount_products[]`（未享受折扣）
- `insights[]`（智能洞察 bullets）
- `summary_highlights`（费用最高 / 涨幅最大 / 最贵资源）
- `product_code_to_name`（ProductCode → ProductName 映射）

❌ 禁止：用 execute_code 逐字段读 `prep_output.json` / `analysis_resources` / `resource_diff` / `summary_tag` / `summarize_*.json`。

#### Step 3-2：渲染 HTML 看板

```bash
python3 <SKILL_BASE>/scripts/render_billing_html.py \
  --data <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/step3_data.json \
  --output <WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html \
  --lang <output_lang>   # zh|en，跟随主理人下发的 [Output language]；缺省默认 zh
# {cloud}/{project}/{uin} 取自 prepare 返回的 credentials.active_cloud/active_project/uin
# 文件名禁止 -uin 前缀、禁止用账户昵称替代项目码；例 billing-tencent-TSP-100031104408.html
# 单账号旧入口（active_project 为空）退化为 reports/{cloud}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{uin}.html
```

约束：
- **看板语言**：`--lang en` 时 KPI/表头/洞察卡片/报告头等界面骨架渲染为英文，`--lang zh`（默认）为中文；币种 `¥` 不随语言变。务必与对话/概览语言保持一致。
- **单文件 HTML**：所有数据内联在一个 `.html` 中；Chart.js 默认通过 jsdelivr CDN 引入（一行 `<script>`）。极端离线场景可加 `--offline /path/to/chart.umd.min.js` 把 Chart.js 内嵌进 HTML。
- **全量展示**：`render_billing_html.py` 内部已实现 6 张全量资源/标签/产品表（产品 / 标签 / 增长 / 降低 / 新增 / 下线），长表通过 `max-height: 460px + overflow-y: auto` 滚动呈现；❌ 禁止在 JSON 数据层做 Top N 截断后再喂入。
- **渲染契约**：HTML 必须包含的章节、字段、视觉元素见 `references/html-template.md`。模型不再手写 HTML，渲染细节由 `render_billing_html.py` 统一负责。

#### Step 3-3：用户可见输出（先概览、后预览）

1. **AIOps 智能概览**（≤ 600 字 Markdown，五段式），格式见 §四 4.2。**先在回复框输出这段文字概览。**
2. 概览输出完毕后，再通过 WorkBuddy 客户端的 `present_files` 打开 `billing-{cloud}-{project}-{uin}.html`。**顺序固定为：先文字概览 → 再 present_files 打开 HTML**，确保概览不被预览面板遮挡。
3. **PPT 导出引导**：概览末尾追加一句引导语（**语种跟随 `output_lang`**，见下方 4.2-A 的中/英双语版），主动询问用户是否需要导出 PPT 汇报版。
4. ❌ 禁止把 HTML 内文复制到对话；❌ 禁止重复展示完整数据表；❌ 禁止"附录"一类二次渲染。

**Step 3 完成后立即终止**：HTML 落盘 → AIOps 智能概览（先）→ present_files 打开 HTML（后）→ 结束。详见 §四 输出契约。

---

## 三、环境变量配置（速查）

> 详细说明见 §一。本节仅作速查表。下方代码块以 Linux/macOS 语法为准；Windows 用户参考下面的"跨平台等价语法"小节。

**路径 1（配置 runner key · 本账号自查）**:

```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="<你的SecretId>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<你的SecretKey>"
# runner key 仅需 SSM 只读权限(GetSecretValue/ListSecrets)，无需 kms；业务账号 AK/SK 只存于 SSM
# 可选：月度预算（元），用于报告头部展示消耗率
export TENCENTCLOUD_MONTHLY_BUDGET="50000"
```

**路径 2（STS AssumeRole 跨账号）**:

跨账号配置不走环境变量，而是写进**目标账号 SSM secret** `cloudops/tencent/<CODE>` 的 JSON：

```jsonc
{
  "role_arn": "qcs::cam::uin/<目标账号UIN>:roleName/<角色名>",
  "assume_from": "PARENT",          // 调用方主账号 AK 所在 secret 项目码（推荐）
  "role_external_id": "...",         // 可选
  "role_duration": "3600"            // 可选
}
```

调用方主账号 AK 单独存 `cloudops/tencent/PARENT`。宿主机器仍只配 runner key，不 export 任何业务 AK/SK / ROLE_ARN。`resolve()` 见到 `role_arn` 自动 AssumeRole。

UIN / AppId / 账户昵称由 `prepare_billing_data.py` 自动调用 `cam.GetUserAppId` 获取（路径 2 走临时凭证调用），**用户不需要手填**。

### 跨平台等价语法

| 操作系统 / Shell | 设置当前会话变量 | 持久化 |
|---|---|---|
| Linux / macOS（bash/zsh） | `export KEY="value"` | 写入 `~/.bashrc` / `~/.zshrc` |
| Windows PowerShell | `$env:KEY="value"` | `[Environment]::SetEnvironmentVariable("KEY","value","User")` |
| Windows CMD | `set KEY=value` | `setx KEY "value"`（新终端生效） |

把上面速查表里所有 `export X="Y"` 按本表替换即可在 Windows 复用。

### 配置位置

环境变量可写入：
- macOS / Linux：用户 shell rc（`~/.zshrc` / `~/.bashrc`）
- Windows：`setx` 写入用户环境变量，或在「系统属性 → 高级 → 环境变量」里图形化添加；只想临时用就 `$env:` / `set`
- IDE / Workspace 配置（如 VS Code 的 `.env` + dotenv 插件）
- WorkBuddy 工作区启动时由用户在终端设置后再触发本 skill

> 凭证只走环境变量，本 skill 不读 `.env` 文件、不写 `.env` 文件。

---

## 四、输出契约（落盘 + 直发）

> 本 skill **直接面向用户**输出报告。

### 4.1 落盘（HARD）

Step 3 渲染完成后，**唯一落盘文件**：
```
<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html
```
其中 `{cloud}`/`{project}` 取自 `prepare` 返回的 `credentials.active_cloud`/`active_project`（项目码，非昵称）；`{uin}` 由 `cam.GetUserAppId` 自动获取。单账号旧入口（`active_project` 为空）退化为 `reports/{cloud}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{uin}.html`。

中间数据 JSON 允许保留在缓存目录用于调试 / 复用：
```
<WORKSPACE>/cache/billing/{uin}/{YYYYMM}/step3_data.json
```

❌ 禁止再写任何 `.md` 报告文件作为最终交付物（`billing_doc.md` 等历史路径已废弃）。
❌ 禁止把 HTML 落到 `/tmp/`、用户主目录或工作区之外的位置。

### 4.2 用户可见输出

按 §九"执行过程静默"：用户触发后，模型的可见输出为：

1. 通过预览工具触发 `billing-{cloud}-{project}-{uin}.html` 渲染（preview_url 或等价工具）
2. **AIOps 智能概览**（≤ 600 字，Markdown 格式），以 AIOps 产品专家视角，让用户在回复框即可看到关键结论与风险信号，无需打开 HTML。结构化五段式，每段必填：

   **格式规范**：
   ```markdown
   ## 🔍 2026-05 账单智能概览

   ### 💰 费用总览
   当月 **¥264,770** | 环比 ↑6.0%（+¥15,034）| 上月 ¥249,736
   集中度：Top 3 产品占比 **70.0%**（专线接入 + CVM + TDMQ）

   ### 📦 产品洞察
   | 产品 | 当月费用 | 环比 | 占比 |
   |---|---|---|---|
   | 专线接入 | ¥140,000 | — | 52.9% |
   | 云服务器CVM | ¥25,303 | -1.3% | 9.6% |
   | 消息队列TDMQ | ¥20,088 | +4.4% | 7.6% |
   | ⚠️ VPN网关 | ¥9,807 | **+313%** | 3.7% |
   | ⚠️ COS | ¥8,743 | **+107%** | 3.3% |

   ⚠️ 异常信号：VPN网关环比暴涨 313%（+¥7,434），COS 翻倍增长（+¥4,510），云联网CCN +297%

   ### 🌏 地域分布
   华东（上海自动驾驶云）**80.3%** → 单地域集中风险 | 华东（上海）14.1% | 华南（广州）3.0%

   ### 🏷️ 标签分账
   - **head**：andyyfang ¥78K | mikegao ¥72K | minhma ¥68K（↑10.3%）
   - **customer**：BYD ¥140K（52.9%）| 合规SDK ¥32K | sgm ¥23K（↑112%）
   - **proj**：自驾合规 ¥250K（94.3%）

   ### 🔎 资源变化 & 风险提示
   📈 增长 90 / 📉 降低 19 / 🆕 新增 49 / ⬇️ 下线 3
   - 新增最贵：VPN网关 vpngw-ewoe1le3 ¥8,207（minhma/自驾合规）
   - 增长最猛：云联网CCN ccn-itnz6umn +¥3,189（sevenzhu/自驾合规）
   - 🟡 单地域集中 80% + 专线接入零折扣 ¥140K/月

   📊 完整看板见上方 HTML 预览 ↑

   💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。
   ```
   > 上为 zh 示例；`output_lang=en` 时末行改用英文引导语（见 4.2-A）。

   **4.2-A · PPT 导出引导语（必须追加到概览末尾，跟随 `output_lang` 双语）**：
   - AIOps 智能概览最后一行必须追加一句 PPT 导出引导，**语种跟随 `--lang/output_lang`**（中文提问→中文引导，英文提问→英文引导，禁止语种错配）：
     - zh：`💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。`
     - en：`💡 Want a presentation-ready PPT? Reply "export PPT" to generate a 16:9 deck.`
   - 此引导语为**非侵入式提示**，不自动触发 PPT 生成，仅引导用户主动表达
   - 用户明确回复后按 §十二 PPT 导出流程执行

   **AIOps 概览编写规则**：
   - 费用总览段：总额 + 环比 + 差额 + 集中度（Top3占比）
   - 产品洞察段：表格展示 Top 5~8 产品，异常环比用 ⚠️ + 粗体标注，单独一行总结异常信号
   - 地域分布段：Top 3 地域 + 集中风险提示（单地域>60%时必须标注）
   - 标签分账段：每个标签维度列出 Top 3 + 关键环比变化（涨幅>10%的标注）
   - 资源变化段：四类统计 + Top 1~2 关键资源（含负责人/标签）+ 智能洞察合并
   - 智能洞察（insights）应融入各段，不再单独列出；风险/关注信号用 🟡🟠🔴 标注级别
3. ❌ 禁止把 HTML 内文复制到对话；❌ 禁止"附录"二次渲染；❌ 禁止重复列出全量数据表。

### 4.3 终止规则（HARD）

1. **HTML 已落盘 = 立即终止**：`billing-{cloud}-{project}-{uin}.html` 写盘成功 → AIOps 智能概览（先）→ present_files 打开 HTML（后）→ 结束。**禁止之后再重跑 `prepare_billing_data.py`、补调 API、二次验证**。
2. **重跑 `prepare_billing_data.py` 上限 1 次**：`report_ready.available != True` 时允许重跑一次；仍不可用 → 按 4.4 降级。
3. **已落盘禁止重跑 prep_data**：若 `billing-*.html` 已存在且 > 5000 字节，说明 Step 3 已完成过，**禁止**再次执行 `prepare_billing_data.py`。

### 4.4 降级 / 失败

- 内部 API 报错 → 按 §四"执行纪律"降级，降级标注嵌入看板正文（`render_billing_html.py` 在洞察卡片中标注）。
- 整体不可恢复（如汇总 API 全部 503）→ 输出 `❌ 账单数据获取失败：{人话原因}`，不输出空看板。

### 4.5 缓存复用规则

**可复用**（按 UIN+月份做 cache key）：
- 历史月 API 结果 — 由 `<WORKSPACE>/cache/billing/{uin}/{YYYYMM}/` 文件系统承担
- 本会话已查询过的任意月份 API 结果

**禁止复用 / 必须重拉**：
- 当前月账单（出账中数值会变）
- 本月 1-4 号查 T-1 月（关账期数据仍在变，5 号起视为已稳定）
- 资源标签快照仅历史月可命中缓存；当前月和本月 1-4 号的 T-1 月强制 SKIP_CACHE

异常（读/写/JSON 损坏）→ **软失败回源**，不写入用户可见错误。

### 4.6 多账号并行编排约束（HARD · 主理人必读）

当用户请求分析多个账号（如 ADC / TSP / YX）时，主理人通过 Team/Agent spawn 多个 `cloud-billing-analyst-pro` 子 agent 并行运行。以下为 **HARD 约束**，违反即视为事故：

1. **谁完成先发谁**：收到任一子 agent 的完成消息后，**立即**将该账号的 AIOps 概览（先）+ HTML 预览（后）展示给用户。❌ 禁止等所有账号都完成再统一汇总——用户不应该面对"全部跑完前的沉默"。
2. **每个账号必须包含三要素**：① AIOps 智能概览（≤600字，五段式，**先输出**） ② HTML 预览（**概览后再 present_files 打开**） ③ **主理人追加的** PPT 导出引导语（语种跟随 `output_lang`）。缺任何一项视为交付不完整。
3. **PPT 引导是主理人的职责（HARD）**：PPT 导出引导语由**主理人**在向用户交付时统一追加，**不是子 agent 的活**。子 agent 只负责交付 HTML 路径 + AIOps 概览。主理人在每次展示完一个账号的概览 + HTML 后，必须追加一句 PPT 引导语：
   - zh：「💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。」
   - en：「💡 Want a presentation-ready PPT? Reply "export PPT" to generate a 16:9 deck.」
3. **不重复 spawn**：每个 `(cloud, project)` 只 spawn 一个子 agent。❌ 禁止对同一账号 spawn 两个 agent（会导致重复 HTML + 资源浪费）。
4. **完成即交付**：子 agent 完成后，主理人的回复就是用户的最终交付。❌ 禁止"我先收着，后面一起补"——概览和 PPT 引导不是可选项，是交付契约的一部分。

---

## 五、报告归档

> Step 3 渲染完成后，单文件 HTML 看板必须落盘到本地归档目录，供对账 / 人工复查 / 离线审计 / WorkBuddy 客户端预览。

### 5.1 落盘路径

```
<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html
```

- `{cloud}`：取 `prepare` 返回的 `credentials.active_cloud`（如 `tencent`）
- `{project}`：取 `credentials.active_project`（项目码，如 `TSP`/`ADC`/`YX`）。**用稳定的项目码，不用账户昵称**——昵称飘忽、可能为空或含中文，项目码才是人类可读且多账号不撞名的标识
- `{uin}`：由 `cam.GetUserAppId` 自动获取（`credentials.uin`）
- `{YYYY-MM-DD}`：**落盘当天**的日期（不是分析的月份），本地时区
- 同账号同天重复触发 → **覆盖**（按天去重，与下游推送一致）
- **单账号旧入口兜底**（`active_project` 为空）：退化为 `<WORKSPACE>/reports/{cloud}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{uin}.html`（省略 project 段，不留双连字符）

### 5.2 落盘内容

- `billing-{cloud}-{project}-{uin}.html`：写入 `render_billing_html.py` 渲染产物**完整原文**（深色玻璃拟态 + KPI 四宫格 + Chart.js 图表 + 6 张全量资源表 + 智能洞察卡片）
- ❌ 不要夹带 prompt / 调试日志 / 入参 JSON / SecretId / SecretKey / UIN（凭证零流转红线延伸）
- ❌ 不要内嵌 API 原始响应或 22MB 级 `bill_details.json` 作为隐藏数据

### 5.3 写盘规则

落盘必须由本 skill 在 Step 3 结束后**立即**完成（用 `--output` 参数让 `render_billing_html.py` 直接写入目标路径），**不需要任何额外自检脚本**：

```bash
python3 <SKILL_BASE>/scripts/render_billing_html.py \
  --data <WORKSPACE>/cache/billing/{uin}/{YYYYMM}/step3_data.json \
  --output <WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/billing-{cloud}-{project}-{uin}.html \
  --lang <output_lang>   # zh|en，跟随 [Output language]，缺省 zh
```

写盘成功 → AIOps 智能概览（先在回复框输出）→ present_files 打开 `billing-{cloud}-{project}-{uin}.html`（后）→ 结束。

### 5.4 失败策略

- 写入异常（权限不足 / 磁盘满 / 目录不存在）→ **软失败**，`continue`
- ❌ 不要向用户提示"归档失败"，主流程不受影响
- ❌ 不要把失败信息带进 HTML 看板正文
- 排查时可人工 `touch <WORKSPACE>/reports/.probe` 验证写入权限


---

## 十二、PPT 导出（按需触发，不替代默认 HTML+概览交付）

### 触发条件（必须同时满足）

1. 已经完成账单分析，HTML 看板已落盘到 `reports/{cloud}/{project}/{uin}/{date}/billing-{cloud}-{project}-{uin}.html`
2. 用户在 HTML 已生成之后，明确说出以下任一类关键词：
   - "导出 PPT" / "转成 PPT" / "做成幻灯片" / "ppt 版本"
   - "给老板看的版本" / "汇报材料" / "汇报版"
   - "再来一份 ppt" / "加一份 ppt"

### 严禁行为

- ❌ 用户没明确要 PPT 时，**禁止主动生成 PPT**（默认交付物只有 HTML + 概览）
- ❌ 用 PPT 替代 HTML 看板（HTML 是主交付物，PPT 只是衍生物）
- ❌ 在 PPT 里编造任何 HTML 看板没有的数据；PPT 内容必须 100% 来自已有概览 + 渲染数据

### 工作流（3 步）

**Step A · 准备 JSON 输入**

复用 Step 3 渲染时已生成的 `billing_render.json`，从中抽取以下字段，写到 `<WORKSPACE>/cache/pptx_input_billing_<uuid>.json`：

```python
import json, uuid, os
from pathlib import Path

uin = os.environ['INSPECTION_UIN']  # 或 cam.GetUserAppId 派生
date = "2026-06-11"
ws = os.environ.get('WORKBUDDY_WORKSPACE', os.getcwd())

# 读已渲染数据（Step 3 落盘的）
with open(f"{ws}/cache/billing/{uin}/billing_render_{date}.json") as f:
    render = json.load(f)

pptx_input = {
    "report_type": "billing",
    "title": "云账单分析报告",
    "subtitle": f"{render['period_label']} · 主账号 {uin}",
    "uin": uin,
    "date": date,
    "summary_md": render['summary_md'],   # 已生成的 ≤600 字概览
    "highlights": [
        {"title": "本月总支出", "value": f"¥{render['total']:,.0f}",
         "delta": f"{render['mom_pct']:+.1f}%", "note": render['top_driver_note']},
        {"title": "TOP 1 产品", "value": render['top_product']['name'],
         "delta": f"占 {render['top_product']['ratio']:.0%}", "note": ""},
        {"title": "异常项", "value": f"{len(render['anomalies'])} 个",
         "delta": "需复核", "note": render['anomaly_brief']},
    ],
    "sections": [
        {"heading": "按产品拆解", "subheading": "TOP 10 高消费产品",
         "table": {"headers": ["产品","金额","占比","环比"],
                   "rows": render['product_top_rows'][:12]}},
        {"heading": "按业务标签", "subheading": "TOP 5 业务线",
         "chart": {"type": "bar", "categories": render['tag_top_names'][:5],
                   "series": [{"name":"金额(元)", "values": render['tag_top_values'][:5]}]}},
        {"heading": "资源变化", "subheading": "新增/下线/上涨/下降",
         "table": {"headers": ["类型","资源","变化"],
                   "rows": render['resource_changes_rows'][:20]}},
    ],
    "actions": render['actions_list'],   # [{priority, text}]
}

input_path = f"{ws}/cache/pptx_input_billing_{uuid.uuid4().hex[:8]}.json"
Path(input_path).parent.mkdir(parents=True, exist_ok=True)
with open(input_path, 'w', encoding='utf-8') as f:
    json.dump(pptx_input, f, ensure_ascii=False, indent=2)
print(input_path)
```

**Step B · 调用 cloud-report-pptx-pro**

```bash
PPTX_OUT="$WORKBUDDY_WORKSPACE/reports/$INSPECTION_UIN/$(date +%Y-%m-%d)/billing-tencent-${DISPLAY_NAME}-uin${INSPECTION_UIN}.pptx"
mkdir -p "$(dirname "$PPTX_OUT")"
/usr/bin/python3 \
  ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input "$INPUT_PATH" --output "$PPTX_OUT"
```

**Step C · 触发预览 + 文字回包**

落盘成功后通过 `open_result_view` 工具触发 PPT 预览，并在回复框追加一句导航：

> 📊 PPT 已生成在 `reports/{cloud}/{project}/{uin}/{date}/billing-{cloud}-{project}-{uin}.pptx`，按 VCIC 模板（16:9）渲染。HTML 看板和概览仍在原位置不变。

### 字段清单（要求 Step 3 一并产出）

为支持 PPT 导出，Step 3 渲染数据 JSON 必须包含以下字段（即使本次没要 PPT 也要落盘，方便用户后续追问"导一份 PPT"时直接用）：

| 字段 | 说明 |
|---|---|
| `summary_md` | ≤600 字概览（与回复框文字一致） |
| `total` / `mom_pct` / `top_product` / `top_driver_note` | 顶部三栏亮点用 |
| `anomalies` / `anomaly_brief` | 异常项 |
| `product_top_rows` | 产品 TOP 表格行 |
| `tag_top_names` / `tag_top_values` | 标签维度图表数据 |
| `resource_changes_rows` | 资源变化表 |
| `actions_list` | `[{priority:"P0/P1/P2", text:"..."}]` |
