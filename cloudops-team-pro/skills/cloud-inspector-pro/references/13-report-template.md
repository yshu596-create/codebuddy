# 13 巡检报告生成

> SOP: TC-SEC-SOP-001 | 报告生成模块
> 触发词:"生成巡检报告"、"输出报告"、"写报告"、"报告模板"

---

## 0. 调用模式

用户在本 workspace 直接对话触发(`scope=full|narrow|narrow_forensic|sla_query` 由 SKILL.md 路由判定)。**单账户驱动**:每次调用 1 个 `account_name`,通过环境变量 `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` 读取凭证。

- **关键约束**:
  - 顶层标题 `## 🔎 {账户名} 云巡检报告 {YYYY-MM-DD}`(放大镜柄,与 costopt `## 🔍` 区分)
  - 全量巡检完成后返回完整报告 + 交付产物
  - **完整报告**(概览 + §二项目详情 + 处置清单),写入 `report_doc.md`
  - **不**输出"如需进一步分析请告诉我"等互动话术
  - **不**使用祈使语气(用"建议/可考虑/需确认")

---

## 0.5 全局排版硬约束(HARD)

> **为什么要有这节**:2026-05-11 三账户(一中心管控 / 营销 / TSP)并发巡检后,三份报告在排版、表头命名、对齐符、加粗段位置上各写各的 — 数据都对但观感割裂。以下规则对**所有大类(`### 云安全/云可靠/云性能/云限制/合规与资产标签`)、所有动态产品/护城河/自扫补丁小节(`#### `)、所有账户、所有调用模式**生效,**个别小节的 reference 若与本节冲突以本节为准**。
>
> **(HARD · 命名)** 章节名称严格使用 4 大类 + 合规标签独立大类的 5 个一级名 + `#### {产品名}` 4 级子标题。**严禁**出现 `### 0X xxx`(如 `### 02 网络与安全组`)、`§0X` 域编号、`SOP 域 0X`、"12 个域"等旧 12 SOP 域骨架的痕迹 —— 这些命名属于内部数据源映射(`SOP 域键`),**禁止泄漏到用户可见的报告章节标题**。

1. **(HARD)** 每个 `#### {产品名/护城河名}` 4 级小节下的**第一段必须是"一句话引言"**(说明本小节扫描范围/总数,例如 `ap-shanghai + ap-shanghai-adc 共 46 个安全组` 或 `存储桶总数 24 个,其中 1 个公有读取`),**禁止**直接以加粗段 `**xxx**` 或表格起手。唯一例外:`#### CAM 账户安全`(其 "CAM 检测汇总表" 硬约束已在前文 CAM 小节模板写明)。

2. **(HARD)** 每个风险点必须用**表格**呈现,**禁止**用 `- bullet` 列举。bullet 仅在 `## 三、立即处置清单` 使用。

3. **(HARD)** 表格**风险/等级列必须右对齐**:写 `|-----:|` 而非 `|:-----|`,emoji 居右视觉更整齐。其他列左对齐 `|:-----|`,数字列右对齐 `|-----:|`。整份报告所有表格统一适用。

4. **(HARD)** 数据库 / TKE / 中间件 / SSL 证书等动态产品小节必须**逐实例/逐证书一行**,**禁止**汇总成一行(如 "15 个集群,1 个公网 API 已创建")。若实例数 > 10,主表保留**全部高风险行**(🔴/🟠)+ 前 5 行 ✅/🟡,尾行追加 `| ...另 N 个 | (合计 M 个 ✅) | | |`,**不得截断隐藏风险行**(违反 `feedback_billing_full_resource_lists`)。

5. **(HARD)** **统一风险明细表 schema**:无论挂在哪个大类下的动态产品小节,统一 7 列 `严重度 | 资源 ID | 名称 | 产品 | 地域 | 风险描述 | 修复建议`,不再为单产品保留特有字段。护城河/自扫补丁小节保留各自专属表头(CAM 7 项汇总 / 网络与安全组端口分桶 / CVM 四列巡检 / 标签合规明细),不进风险明细 schema。

   | 小节类型 | 必用表头 |
   |---|---|
   | 动态产品小节(advisor 命中) | `严重度 \| 资源 ID \| 名称 \| 产品 \| 地域 \| 风险描述 \| 修复建议` |
   | CAM 账户安全(护城河) | 7 项汇总表(详见 §CAM 小节模板) |
   | 云审计(护城河) | 跟踪集子段 + 高危事件流子段双子表(沿用旧模板) |
   | 网络与安全组(自扫补丁) | `安全组 \| 地域 \| 危险规则 \| 关联实例 \| 等级` |
   | CVM 主机安全(自扫补丁) | `巡检项 \| 期望 \| 实际 \| 风险` 四列 |
   | 标签合规(护城河) | `业务 \| 产品 \| 地域 \| 资源 ID \| 名称 \| 缺失标签 \| 等级 \| 月度费用` |
   | 网络基础设施(自扫补丁) | `巡检项 \| 当前状态 \| 数量 \| 风险等级` |
   | 安全产品覆盖(自扫补丁) | `安全产品 \| 启用状态 \| 详情 \| 风险等级` |
   | 基线配置检查(自扫补丁) | `检查项 \| 当前状态 \| 风险等级` |
   | 配置审计合规率(自扫补丁) | `框架 \| 规则数 \| 合规率 \| 风险等级` |

6. **(HARD)** 加粗段 `**xxx**` 作为"风险分类小标题"**仅允许在以下护城河/自扫补丁小节**使用,动态产品小节(纯 advisor 单源)**禁止**用加粗段分块,直接一张 7 列风险明细表起手:
   - `### CAM 账户安全`:7 项汇总表 + `**1. 账号统计**` 等编号详情
   - `### 网络与安全组`:`**🔴 全端口开放 0.0.0.0/0**` / `**🔴 高危端口对外(在用)**` / `**空安全组(建议清理)**`
   - `### CVM 主机安全`:四列巡检表 + `**密码破解成功明细**` / `**风险登录 Top 10**`
   - `### 云审计`:跟踪集子段 + 事件流子段双子表(`**A. 跟踪集状态**` / `**B. 高危事件明细**`)
   - `### 标签合规扫描`:`**11.2 按业务码分组**` / `**11.3 不合规资源明细**`
   - `### 网络基础设施`:`**VPC 概览**` / `**缺 ACL 子网**` / `**EIP 散落直连**` / `**堡垒机**`
   - `### 安全产品覆盖`:`**WAF**` / `**DDoS 高防**` / `**云防火墙(CFW)**` / `**数据库审计(advisor 推断)**`
   - `### 基线配置检查`:`**组织归属**` / `**安全联系人**` / `**消息订阅**`
   - `### 配置审计合规率`:`**合规率概览**` / `**规则框架覆盖**` / `**不合规规则明细**`

7. **(HARD)** `#### CVM 主机安全` 自扫补丁小节一律使用**"巡检项 × 期望 × 实际 × 风险"四列表**,列名固定为 `巡检项 | 期望结果 | 实际结果 | 风险`,**禁止**三份报告三种写法(散文收尾 / 加粗段分块 / 只汇总)。

8. **(HARD · F34)** **报告章节由 4 大类×动态产品组织,数据源仍为三层架构**:
   - (a) **动态产品小节**(advisor 单源):每大类下 advisor `findings[]` 按 `product` 字段分桶,每 product 一个 `### {产品名}` 小节,逐 finding 一行 7 列风险明细表;`group` 字段(GroupName)决定挂哪个大类;**`group=成本` finding 整体丢弃**(用户决策:成本由 costopt skill 独家承载);**禁止**在动态小节里现场调云 API 补查
   - (b) **自扫补丁小节**(挂载到对应大类下的固定位置):`### 网络与安全组`(挂云安全)/ `### CVM 主机安全`(挂云安全,Agent/暴破/登录/漏洞 4 维)/ `### SSL 证书`(挂云安全)/ `### COS 桶清单`(挂云安全,ACL fast-path)。这些自扫覆盖 advisor 缺细粒度的维度
   - (c) **护城河小节**(advisor 完全不覆盖):`### CAM 账户安全`(挂云安全首位)/ `### 云审计`(挂云安全第二位)/ `### 标签合规扫描`(挂独立 `## 合规与资产标签`)
   - (d) `resource_id` union 去重:同 product 下自扫与 advisor 重叠时合并行,严重度取较高,严重度 emoji 取 `level_max`,**修复建议**列取 advisor `repair` markdown 全文(见 #9)
   - (e) **禁止**在小节里另起「📋 ... 命中详情」子表,全部并入 7 列风险明细表
   - (f) F34 全文 0 命中 `advisor / 智能顾问 / strategy / costopt`
   - (g) **禁止出现** `## 云成本` 大类(成本由 costopt skill 独家承载);advisor `group=成本` finding 在 Step 4 第一步过滤,不分配到任何大类

9. **(HARD)** **修复建议列直接渲染 advisor `repair` markdown 全文,表内竖线必须转义**:
   - 数据源:`inspect_advisor.json/findings[*].repair`(腾讯云官方写好的 SOP markdown,已 100% 非空,平均 119 字,67% 含内联 `[链接](url)`)。**禁止**继续用 `repair_url` 单链接形式(链接经常 404,文字本身就是可读 SOP)。
   - 渲染方式:动态产品小节 7 列表的「修复建议」列**直接放 `repair` 全文**,不要二次加 `[文档](...)` 包装。
   - **(HARD)** `repair` 文本中所有 `|` 字符必须转义为 `\|`,否则会被 markdown 解析为表格列分隔,排版崩坏。`repair` 含换行(`\n`)替换为空格或 `<br>`。
   - `repair_url` 字段保留(scripts/inspect_advisor.py 仍输出,costopt skill 还在消费),inspector 报告 §二**不再使用**;§三立即处置清单继续用 `repair_url` 单链接(那里只能放短链接,不能放 119 字 SOP)。
   - `repair == ""`(极少数)→ 修复建议列写 `—`,不加占位文字。

10. **(HARD · F34)** **对外输出禁止暴露内部判定来源/系统代号**:报告(在线文档 + 报告归档 .md)任何**用户可见位置**禁止出现以下字眼(不区分大小写):`advisor` / `智能顾问` / `strategy` / `costopt`。统一用业务化措辞替代:
    - 「云端策略」「云端策略巡检」← 替代 advisor / 智能顾问
    - 「风险描述」← 替代 strategy_name 列标题
    - 「云端策略巡检不可用 / 部分失败」← 替代 advisor scan_status=denied/partial 文案
    - 内部 docs(SKILL.md、本文件注释)、scripts(`inspect_advisor.py`)、JSON 字段名(`strategy_name`/`repair_url`)、`shared/cache/` 路径等**技术资产保留 advisor 命名**(F34 仅针对终端用户可见输出)。
    - Pre-Output Check 必须 `grep -i` 全文,4 个禁用词都为 0 命中才算通过。
    - 唯一例外:CAM 策略名 `QcloudAdvisorReadOnlyAccess` 等腾讯云控制台**原生不可改的字符串**,在"建议给子账号附加 xxx 策略"的运维提示中允许保留(因为用户必须用这个原文去搜)。
    - **反例库**(2026-05-21 fork-mode #1 实测发现 LLM 在解释性括号/降级文案/数据源说明里反射性偷塞内部代号,自检 Edit 6 处。下表给出可直接套用的替换 phrasing,从源头收敛):

      | ❌ 禁用写法(LLM 反射) | ✅ 业务化替换 | 触发场景 |
      |---|---|---|
      | "advisor 子账号视角未细分" | "子账号视角下未细分" | 子账号身份降级文案 |
      | "advisor 接口受限" / "调用 advisor API 受限" | "云端策略巡检接口受限" | scan_status=denied 文案 |
      | "advisor 命中 N 项" / "advisor findings" | "云端策略命中 N 项" / "云端策略巡检结果" | 概览矩阵 / 域内统计 |
      | "advisor 未给出 X" / "advisor 不覆盖 X" | "云端策略未覆盖 X" | 自扫补丁域为何独立扫的解释 |
      | "strategy 在此场景未命中" / "strategy 未触发" | "此场景未命中策略" / "未命中策略规则" | 域内"无风险"原因解释 |
      | "strategy_name 列" / "按 strategy 分组" | "风险描述列" / "按风险类型分组" | 表格列名 / 文案 |
      | "调用智能顾问 API" / "智能顾问扫描" | "调用云端策略接口" / "云端策略巡检" | 流程描述 |
      | "costopt 跳过名单" / "costopt 已处理" | "成本优化跳过名单" / "成本巡检已覆盖" | 跨 skill 协同说明 |
      | "advisor + 本地补丁" / "advisor 主源" | "云端策略 + 本地补丁" / "云端主源" | 数据源构成说明(§五降级节常见) |
      | "结合 advisor 与自扫" | "结合云端策略与本地巡检" | 方法论说明 |

      **Why**: HARD 反复强调"禁止"只能让 LLM 知道不能写,但当需要解释"这个字段为什么不细分 / 为什么这里走 fallback"时,自然语言反射会把内部数据源名顺手带出。给可替换 phrasing 让 LLM 有现成路径可走,稳定收敛。

      **How to apply**: 写完报告 Pre-Output Check 时,先 `grep -i 'advisor\|智能顾问\|strategy\|costopt'`;命中后**优先**按本表查 ❌→✅ 替换,不要为了删词而删掉整句解释(那会丢失对用户有用的"为什么这里降级"的语义)。本表外的新句式遵循同一原则:**保留语义,改写措辞**。

11. **(HARD · 章节命名负向兜底)** 用户可见的报告(在线文档 + 报告归档 .md)中,**严禁**出现以下旧 12 SOP 域骨架命名(2026-05-22 已重构为 4 大类 × 动态产品):
    - 章节标题层(`### `/`#### `):**禁止** `### 0X xxx` / `### 01 IAM/CAM` / `### 02 网络与安全组` / `### 12 漏洞处置` 等以两位数字编号开头的标题
    - 行内引用层:**禁止** `§0X` / `§02 网络` / `§03 CVM` / `§07 TKE` / `§12 应急` 等以 `§` + 两位数字编号引用章节
    - 描述层:**禁止** `0X 域` / `12 个 SOP 域` / `第 X 个域` / `SOP 域 0X` 等表述
    - **唯一允许例外**:`#### 云审计` 内部子段命名为 `跟踪集子段` / `事件流子段`,严禁写 `§10.1` / `§10.2`;`#### 标签合规扫描` 内部子小节编号 `##### 11.1 - 11.5` 是子小节内部编号(不带 `§` 前缀),允许保留
    - **内部技术资产保留**(对用户不可见):scripts/SKILL.md/reference 注释中的 `inspect_advisor.json/findings[]`(SOP 域键 `02`)等"SOP 域键"字串属于内部数据源映射,**允许保留**;但 LLM 渲染报告时**禁止**把这种映射键泄漏成用户可见的章节命名

    **Why**: 12 SOP 域是 advisor JSON 数据源内部的归类维度(2-12),报告章节是用户视角的 4 大类业务面;两者 1:N 多对多映射,若把 12 SOP 域当章节标题,等于把内部数据库 schema 倒灌到用户报告里,既丢失"4 大类"的产品视角,又让 LLM 在 0X 编号与 4 大类之间来回切换时频繁回退到旧骨架(2026-05-22 营销账号实测就是这个原因回退)。**How to apply**: 写完报告后 grep `'### 0[1-9] \|### 1[0-2] \|§0[1-9]\|§1[0-2]\|0X 域\|SOP 域 0'`,期望 0 命中(子小节内部编号 `11.1-11.5` 不带前缀,不会命中)。

12. **(HARD · 表格行无空行)** Markdown 表格的**任意两条数据行之间禁止插入空行**。表头分隔行 `|:----|` 之后,所有 `| 数据 |` 行必须**连续紧贴**直到表格结束。**禁止**为了"看起来整齐"在每行之间空一行。

    **❌ 错误**(LLM 常见自然倾向):
    ```markdown
    | 账号 | UIN | 权限 |
    |:-----|:----|:----|

    | mikegao | 100040317736 | Admin |

    | andyyfang | 100007803921 | Admin |
    ```

    **✅ 正确**:
    ```markdown
    | 账号 | UIN | 权限 |
    |:-----|:----|:----|
    | mikegao | 100040317736 | Admin |
    | andyyfang | 100007803921 | Admin |
    ```

    **Why**: Markdown 标准里,空行是段落终止符。表格行之间一旦有空行,渲染器会把表格切成多个独立"单行表"碎片,严格的渲染器会让用户看到一堆只有表头的零散表格,可读性归零。宽松的渲染器(GitHub / VSCode 预览)会容忍这种写法,因此 LLM 在终端里看自己生成的 markdown 觉得"挺整齐",实际在某些渲染环境下会散架。

    **How to apply**: 写完报告后,在落盘前用 Python/grep 跑一遍校验:`re.findall(r'\|[^\n]*\|\n\n+\|', report)` 期望返回空列表;命中即说明有"表格行 → 空行 → 表格行"模式,必须用 `re.sub(r'(\|[^\n]*\|)\n\n+(\|[^\n]*\|)', r'\1\n\2', text)` 反复替换到稳定再落盘。模板片段、动态拼接(尤其循环里 `f"\n\n| {row} |"` 写法)、子模板 string concat 是常见出 bug 点。

---

## 报告结构模板

### 文档头部

```markdown
# 腾讯公有云全量安全巡检报告

文档编号：TC-SEC-SOP-001
报告期：{YYYY-MM-DD}
巡检账户：{账户名}（{UIN}）
执行人：{当前用户}
生成时间：{YYYY-MM-DD HH:MM} GMT+8
```

> - `{当前用户}`: 从会话上下文获取 sender_id
> - `{YYYY-MM-DD HH:MM}`: 报告生成时的实际时间

---

### 一、巡检概览

按腾讯云控制台「巡检概览」横排呈现(**4 列,无成本列**),每列单元格用 `<br>` 软换行列「产品名 🔴/🟠/🟡 计数」,空列写 `✅`:

```markdown
| 安全 | 可靠 | 性能 | 限制 |
|:-----|:-----|:-----|:-----|
| CAM 🔴2<br>SG 🔴1 🟠3<br>CWP 🔴1<br>COS 🟠1 | CDB 🟠2<br>Redis 🔴1 🟠2<br>CLB 🟡5 | TKE 🟠1<br>CVM 🟡3 | EIP 🟡8<br>NAT 🟡1 |
```

**合规标签独立成行,放 4 列表下方**:

```markdown
**合规标签**:🔴N 🟠M(数据源 `inspect_resource_tags.json`,billing 维度 head + business/customer)
```

**(HARD)** 概览矩阵填表规则:
- 每列产品 = `inspect_advisor.json/findings[]` 按 `group` 字段过滤后,再按 `product` 分桶
- 计数 = 该 product 下 finding 数,按严重度分:`🔴N 🟠M 🟡K`(✅ 档不出现)
- 单元格内按 finding 总数倒序;空列(零命中)整格写 `✅`
- **`group=成本` 的 finding 全部丢弃**,不入任何列;**禁止出现「成本」列**
- **护城河 / 自扫补丁产品强制入云安全列**(无论 advisor 是否给出):
  - `CAM`(读 `inspect_cam.json`,无命中也出 ✅)
  - `云审计`(读 `inspect_audit_events.json` 高危事件 + `inspect_cloudaudit.json` 跟踪集)
  - `SG`(读 `inspect_sg_rules.json` 端口分桶 + `inspect_vpc_cross.json`)
  - `CVM`(读 `inspect_cwp.json` Agent/暴破/登录)
  - `SSL`(读 `inspect_ssl.json` 天数分级)
  - `COS`(读 `inspect_cos.json` 桶 ACL)
- **护城河 4 个固定字眼必出**:`CAM` / `云审计` / `SG` / `CVM` 在云安全列至少各占一行(零命中写 ✅ 不省)
- **标签合规独立列在合规大类**(不入安全列):放在 4 列表下方一行 `**合规标签**`(读 `inspect_resource_tags.json`)
- F34:不写 `advisor` / `strategy` / `costopt` / `[advisor]/[本地]/[双源]` 等字眼,所有数据源融合是实现细节,不暴露
- 单项目报告 4 列横排不变,只是文案口径只描述这一个项目

**单元格写法示例**:

| 安全列写法 | 含义 |
|---|---|
| `CAM 🔴2`<br>`SG 🔴1 🟠3`<br>`CWP 🔴1`<br>`COS 🟠1` | CAM 2 项红、SG 1 红 3 橙、CWP 1 红、COS 1 橙 |
| `CAM ✅`<br>`SG ✅`<br>`CVM ✅`<br>`云审计 ✅` | 护城河 4 项全清,云安全列只有 ✅ 摆设 |
| `CDB 🟠2`<br>`Redis 🔴1 🟠2`<br>`CLB 🟡5` | 云可靠列动态产品,按 finding 数倒序 |
| `✅` | 空大类(如云限制零命中),整格只一个 ✅ |

---

## 二、{项目名} 项目详情

> **(HARD · F34)** 章节按腾讯云控制台「巡检概览」**4 大类**组织(云成本由 costopt skill 独家承载,本报告不涉成本)。每大类下小节由 advisor 实际命中的 product 动态生成,无命中即整大类省略;**护城河小节例外**(CAM / 云审计 / 标签合规),无论命中数都强出。
>
> **(HARD · 标题层级)** `## 二、{项目名} 项目详情` 是 §二的外壳标题(2 级);**4 大类降为 3 级**`### 云安全 / ### 云可靠 / ### 云性能 / ### 云限制 / ### 合规与资产标签`;**护城河/动态产品小节降为 4 级** `#### CAM 账户安全 / #### 云审计 / #### 网络与安全组 / #### CVM 主机安全 / #### SSL 证书 / #### COS 对象存储 / #### {advisor 产品小节}`。**禁止**出现 `### 云成本`(用户决策)。
>
> **`### 云安全` 下小节固定顺序**(护城河首,自扫补丁次,动态产品末):
> 1. `#### CAM 账户安全`(护城河,必出,详见下方 CAM 7 项汇总模板)
> 2. `#### 云审计`(护城河,必出,详见下方双子表模板)
> 3. `#### 网络与安全组`(自扫补丁,有命中即出,详见下方 SG 端口分桶模板)
> 4. `#### CVM 主机安全`(自扫补丁,有命中即出,详见下方四列巡检模板)
> 5. `#### SSL 证书`(自扫补丁,有命中即出)
> 6. `#### COS 对象存储`(自扫补丁,有命中即出)
> 7. 其他 advisor `group=安全` 命中的 product 小节(动态,按 finding 数倒序,统一 7 列风险明细表)
>
> **`### 云可靠` / `### 云性能` / `### 云限制`**:每大类下**只出动态产品小节**(`#### {产品名}`),无护城河。骨架统一为 7 列风险明细表,无命中即整大类省略。
>
> **`### 合规与资产标签`**:独立大类,固定整份报告最后大类(在四大类之后、立即处置清单之前)。下挂 `#### 标签合规扫描`(护城河,详见下方标签合规模板)。

> **(HARD · 完整骨架样板,LLM 必须以此为准复制粘贴)** 一份合规报告的 §二项目详情**必须**呈现下面这 5 个 3 级大类的整体结构(0 命中的动态产品小节省略,但护城河 5 个小节无论命中数都必须出现)。**严禁出现 `### 0X xxx` / `### 12 漏洞处置` / `### 02 网络与安全组` 等旧 12 SOP 域骨架的标题**:
>
> ```markdown
> ## 二、营销账号 项目详情
>
> ### 云安全
>
> #### CAM 账户安全
> {7 项汇总表 + 详情子段;护城河必出}
>
> #### 云审计
> {跟踪集子段 + 事件流子段双子表;护城河必出}
>
> #### 网络与安全组
> {SG 端口分桶 + 跨 UIN 表;自扫补丁,有命中出}
>
> #### CVM 主机安全
> {四列巡检表 + Agent/暴破/登录/漏洞明细;自扫补丁,有命中出}
>
> #### SSL 证书
> {逐证书一行;自扫补丁,有命中出}
>
> #### COS 对象存储
> {存储桶清单 + 公有读 fast-path;自扫补丁,有命中出}
>
> #### CDB MySQL  ← 示例:advisor group=安全 命中的产品
> {7 列风险明细表}
>
> ### 云可靠
>
> #### Redis  ← 示例:advisor group=可靠性 命中的产品
> {7 列风险明细表}
>
> #### CLB
> {7 列风险明细表}
>
> ### 云性能
>
> #### CKafka  ← 示例:advisor group=性能 命中的产品
> {7 列风险明细表}
>
> ### 云限制
>
> #### CVM  ← 示例:advisor group=配额 命中的产品
> {7 列风险明细表}
>
> ### 合规与资产标签
>
> #### 标签合规扫描
> {11.1-11.5 五个子小节;护城河必出}
> ```
>
> 上面注释中的 `← 示例:advisor group=xxx` 仅做说明用,**实际报告里 LLM 严禁写这种来源注释**(F34 不暴露内部判定来源)。

---

#### 动态产品小节通用骨架(7 列风险明细表)

适用于所有非护城河、非自扫补丁的产品小节(即 advisor 单源命中的产品),标题层级 **4 级**(`#### `):

```markdown
#### {产品名}({product code})

{产品全名} 共 {N} 个实例,命中 {K} 项 `{group}` 类风险。

| 严重度 | 资源 ID | 名称 | 产品 | 地域 | 风险描述 | 修复建议 |
|-----:|:-------|:----|:----|:----|:--------|:--------|
| 🔴 | {resource_id} | {resource_name} | {product} | {region} | {strategy_name} | {repair markdown 全文,`\|` 已转义} |
| 🟠 | ... | ... | ... | ... | ... | ... |
```

**字段映射**(advisor finding schema):
- `严重度`:取 `level_max` 字段(advisor 实际只输出 `2`/`3` 两档,`1` 极少):`3 → 🔴` / `2 → 🟠` / `1 → 🟡` / `0 → 未命中`(应被 `risk_total > 0` 过滤掉)
- `资源 ID` / `名称` / `地域`:取 `risks[]` 数组逐元素;`risks[]` 为空(命中数=0 实例聚合)时 `资源 ID` 列写 `({risk_total} 个资源)`,其他列 `—`
- `产品`:取 finding 顶层 `product` 字段
- `风险描述`:取 finding 顶层 `strategy_name` 字段(F34:列名只写「风险描述」,禁止 `strategy_name`)
- `修复建议`:取 finding 顶层 `repair` markdown 全文,渲染前**必须** `repair.replace("|", "\\|").replace("\n", " ")`;空字符串写 `—`
- 同 product 下多 finding 按 `level_max` 倒序(🔴 → 🟠 → 🟡)
- `resource_total` 字段 = 该 strategy 扫描的资源总数;`risk_total` 字段 = 该 strategy 命中风险数;**只渲染 `risk_total > 0` 的 finding**,`level_max=0` 直接过滤掉

**何时省略本小节**:该 product 在本大类下 `risk_total > 0` 的 finding 数 = 0 时整段省略,**禁止**留空标题。

---

#### 护城河 / 自扫补丁小节填充模板

下面给出 7 个固定小节(`#### CAM 账户安全` / `#### 云审计` / `#### 网络与安全组` / `#### CVM 主机安全` / `#### COS 对象存储` / `#### SSL 证书 + 应急响应` / `#### 标签合规扫描`)的具体填充模板。其余产品(CDB/Redis/CLB/TKE/CKafka/ES 等)统一走上方「动态产品小节通用骨架」7 列风险明细表,不再为单产品维护专属表头。

---

#### CAM 账户安全(护城河)

> **变更 2026-05-11**: 新增 CAM 7 项检测汇总表(必须作为本节第一个输出),账号统计合并到此处。

**CAM 检测汇总表（必须首先输出,7 项固定检查项）**

```markdown
| 检查项 | 判定 | 等级 |
|--------|------|------|
| 账号总量统计 | {N} 个账号，结构清晰 / {描述} | ✅ 合规 / 🟡 中风险 |
| 离职/未活跃账号 | ✅ 无超过90天未登录 / {N} 个 > 90 天未登录 | ✅ 合规 / 🟠 高风险 |
| AK/SK 权限审查 | ✅ 无异常 / 主账号持有活跃 AK / 协助者持有 AK | ✅ 合规 / 🔴 严重 |
| Administrator 权限 | {N} 个全量权限账号 | ✅ 合规(≤5) / 🟡 中风险(≤5但需关注) / 🔴 严重(>5) |
| 用户组归属 | ✅ 全部已归属 / {N} 个协助者未归属 | ✅ 合规 / 🟡 中风险 |
| MFA 开启 | {已开启数}/{应开启数} 已开启 | ✅ 合规 / 🟠 高风险 |
| AK 闲置 | ✅ 无闲置 / {N} 个 > 90 天未用 | ✅ 合规 / 🟡 中风险 |
```

**等级判定规则:**
- 🔴 严重: 主账号活跃 AK、Admin 权限 >5 人、服务账号持有 Admin
- 🟠 高风险: 离职/未活跃账号(>90天)、MFA 未开启
- 🟡 中风险: Admin 权限 ≤5 但需关注(如服务账号)、用户组未归属、AK 闲置 >90天
- ✅ 合规: 无异常

> **关键约束**: 汇总表必须包含全部 7 项,即使该项结果为合规也要列出。这是报告的核心输出之一。

---

以下为各检查项的**详情展开**(汇总表之后输出):

**1. 账号统计**

```markdown
| 类别 | 数量 |
|------|------|
| 子账号（可登录控制台） | {N} |
| 子账号（禁止登录控制台） | {N} |
| 协助者 | {N} |
```

> 数据来源：`cam.ListUsers` + `cam.ListCollaborators`

**2. 离职/未活跃账号检测**

**(HARD)** 输出规则(严格按疑似不活跃数量 N 分档):

- **N = 0** → 一句话确认:
  ```markdown
  离职检测: ✅ 无超过90天未登录账号
  ```

- **1 ≤ N ≤ 4** → **必须**用下表,**逐行列出**全部 N 个账号:
  ```markdown
  | 账号 | UIN | 类型 | 最后登录 | 未登录天数 | 风险 |
  |:-----|:----|:-----|:---------|---------:|-----:|
  | {账号名} | {UIN} | 子账号/协助者 | {YYYY-MM-DD HH:MM} / 从未登录 | {N}天 / — | 🔴/🟠/⚠️ |
  ```

- **N ≥ 5** → **必须**用上表,**逐行列出全部**,**禁止**只给数字摘要(例如 `"21 个账号疑似未活跃,建议控制台核查"` 这种写法违反本 HARD)。若 N > 20,主表仍列全部高风险(🔴/🟠),从未登录(⚠️)按"未登录天数"倒序保留前 10 行,尾行追加 `| ...另 M 个 ⚠️ 从未登录 | | | | | |`,**不得截断隐藏 🔴/🟠 行**(对齐 `feedback_billing_full_resource_lists`)。

**数据取法(LLM 在 Step 4 直接从 `inspect_cam.json` 过滤)**:

```
.principals[]
| select(.console_login == 1 or .type == "collaborator")
| select(
    (.last_login_time == "" or .last_login_time == null)  # 从未登录
    or (.last_login_time < "{今天 - 90 天, YYYY-MM-DD}")     # >90 天未登录
  )
```

若 `audit_cross_check.suspect_count` 与上式过滤结果数量不一致,**以上式(principals 实时过滤)为准**,因为 `suspect_count` 可能用了不同阈值/口径。

> 数据来源:`inspect_cam.json` 的 `.principals[].last_login_time`(scan 侧已由 `GetUser.RecentlyLoginTime` + `DescribeSubAccounts.LastLoginTime` 合并填充)
> 判定标准:>200天 🔴 严重 | 90-200天 🟠 高 | 从未登录 ⚠️ 关注

**3. AK/SK 权限审查**

```markdown
| 检查项 | 结果 | 风险 |
|--------|------|------|
| 主账号持有 AK | {N} 个 {Active/Inactive} | 🔴 严重 / ✅ |
| 可登录子用户持有 AK | {N} 个 | ✅ / 🟠 高 |
| 协助者持有 AK | {账号名} {N}个 {Active/Inactive} | 🟠 高 / ✅ |
```

> 数据来源：对每个账号调用 `cam.ListAccessKeys`(`TargetUin`)
> 判定标准：主账号持有活跃 AK = 🔴 严重 | 协助者持有 AK = 🟠 高

**4. Administrator 权限**

简写(≤5人):
```markdown
Administrator 权限: {N}个账号（{账号列表}）✅ ≤5
```

或超标(>5人)时用表格:
```markdown
Administrator 权限: {N}个账号 🔴 超标(>5)

| 账号 | 权限来源 |
|------|---------|
| {账号名} | 直接关联 / Admin组 / {组名} |
```

> ⚠️ 服务账号直接关联 AdministratorAccess 需单独标注:
> `⚠️ 服务账号 {账号名} 直接关联 AdministratorAccess`

> 数据来源：`cam.ListEntitiesForPolicy`(`PolicyId=1`)
> 判定标准：服务账号持有 Admin 🔴 严重 | >5 个账号持有 Admin 🔴 超标 | ≤5 个 ✅

**5. 用户组归属检查**

```markdown
| 检查项 | 结果 | 风险 |
|--------|------|------|
| 用户组归属 | ✅ 全部已归属 / ⚠️ {N}个未归属：{列表} | — / 🟡 中风险 |
```

> 数据来源：`cam.ListGroupsForUser`(`SubUin`)
> 判定标准：未归属任何用户组 = 🟡 中风险

**6. MFA 开启检查**

```markdown
MFA: ✅ {已开启数}/{应开启数} 已开启（主账号 {MFA类型}）
```

或当存在异常时,按表格列出未开启的账号。

> 数据来源：`DescribeSafeAuthFlag`（主账号）+ `DescribeSafeAuthFlagColl --SubUin`（逐个查询）

**7. AK 闲置清理**

> 闲置 AK 明细在 `#### CAM 账户安全` 的 AK 闲置子条目中统一列出,此处仅统计数量。

> 全部 IAM 数据来源 API 汇总：
> `ListUsers` / `ListCollaborators` / `DescribeSubAccounts` / `DescribeSafeAuthFlag` / `DescribeSafeAuthFlagColl` / `ListAccessKeys` / `ListEntitiesForPolicy` / `ListGroupsForUser`

#### 网络与安全组(自扫补丁)

**安全组总览**

```markdown
{地域1}+{地域2}+...共 {N} 个安全组
```

**🔴 全端口开放 0.0.0.0/0（在用）**

```markdown
| 安全组 | 地域 | 危险规则 | 关联实例 | 等级 |
|--------|------|----------|---------|------|
| {安全组名} ({sg-id}) | {地域} | ALL:ALL 对 0.0.0.0/0 | {N} | 🔴 |
```

**🔴 高危端口对外（在用）**

```markdown
| 安全组 | 地域 | 危险规则 | 关联实例 | 等级 |
|--------|------|----------|---------|------|
| {安全组名} ({sg-id}) | {地域} | {端口}({服务}) 对 0.0.0.0/0 | {N} | 🔴 |
```

> 当同时暴露多个数据库端口时,补充风险提示:
> `⚠️ 同时暴露 {N} 个数据库端口到公网，关联 {N} 台生产实例`

**等级判定标准：**
- `ALL:ALL` 对 0.0.0.0/0 → 🔴 严重
- SSH(22)/RDP(3389)/ICMP 对 0.0.0.0/0 → 🔴 严重
- 数据库端口(3306/6379/27017/5432/9200) 对 0.0.0.0/0 → 🟠 高（生产环境 🔴）
- NodePort(30000-32768) 对 0.0.0.0/0 → 🟠 高
- 单个应用端口 对 0.0.0.0/0 → 🟡 中

> 数据来源：`vpc.DescribeSecurityGroups` + `DescribeSecurityGroupPolicies`

**空安全组明细（必须列出）：**

```markdown
空安全组: {N}个（建议清理）🟡

| 地域 | 安全组 ID | 名称 |
|------|----------|------|
| {region} | {sg-id} | {安全组名称} |
```

> 数据来源：`DescribeSecurityGroups` + `DescribeSecurityGroupAssociationStatistics`，筛选 `TotalCount=0` 的安全组
> 注意：必须遍历所有地域才能全量覆盖

**NAT/EIP**

```markdown
NAT/EIP: NAT{状态描述} {风险标记} | EIP {N}个{闲置状态} {风险标记}
```

当存在高危端口 DNAT 时,列出明细表格(同旧模板)。当存在闲置 EIP 时,列出明细表格。

> 数据来源：`vpc.DescribeNatGateways` + `DescribeNatGatewayDestinationIpPortTranslationNatRules`（NAT）/ `DescribeAddresses`（EIP）

**VPC 网络隔离（必须列出）：**

检查跨 UIN 云联网关联和对等连接(数据源: `inspect_vpc_cross.json`):

```markdown
| 检查项 | 结果 | 风险 |
|:-------|:----|-----:|
| CCN 云联网(本账号) | {ccn_total} 个 | — |
| 跨 UIN 云联网关联 | ✅ 无 / ⚠️ {ccn_cross_uin.length} 项 | — / 🟠 高 |
| 对等连接(本账号) | {peer_total} 个 | — |
| 跨 UIN 对等连接 | ✅ 无 / ⚠️ {peer_cross_uin.length} 项 | — / 🟠 高 |
```

**跨 UIN 云联网明细(ccn_cross_uin 非空时必出)**:

```markdown
| CCN ID | CCN 名称 | 关联实例 | 类型 | 地域 | 对端 UIN |
|:-------|:---------|:--------|:----|:-----|:--------|
| {ccn_id} | {ccn_name} | {inst_id} | {inst_type} | {inst_region} | {inst_uin} |
```

**跨 UIN 对等连接明细(peer_cross_uin 非空时必出)**:

```markdown
| 对等连接 ID | 名称 | 本端 VPC | 本端地域 | 对端 VPC | 对端地域 | 对端 UIN | 状态 |
|:-----------|:-----|:--------|:--------|:--------|:--------|:--------|:----|
| {peer_id} | {peer_name} | {src_vpc} | {src_region} | {dst_vpc} | {dst_region} | {dst_uin}(或 {src_uin}) | {state} |
```

**HARD 规则**:
- 数据源**固定为** `inspect_vpc_cross.json`(Step 1 `inspect_scan.py` 已并发落盘,2026-05-12 收编);**禁止**写"需手工核查"/"需登录控制台确认"等话术 —— 除非 `scan_status != 'ok'`
- `scan_status == 'denied'` 时改写为"⚠️ 子账号无 vpc 读权限,未能判定"并标 ⚠️(不要标 ✅ 无,避免假合规)
- **必须**在总览表下列出全量明细行(ccn_cross_uin + peer_cross_uin 各自一张表),即使只有 1 项 —— 跨 UIN 是高敏拓扑信息,所有项必须逐行暴露
- ccn_total=0 且 peer_total=0 时总览表仍需列 4 行(不要因为全 0 就省略整个表)

> 数据来源:
> - 列表: `DescribeCcns` + `DescribeCcnAttachedInstances`(云联网,全局 API 走 ap-guangzhou)
> - 对等连接: `DescribeVpcPeeringConnections`(地域 API 逐 region,顶层返回 `PeerConnectionSet`,跨 UIN 判定看 `SourceUin`/`DestinationUin` 是否不等于本账号 `OwnerUin`)

**📌 风险明细合并规则(LLM 内部参考,F34 不暴露来源)**

`#### 网络与安全组` 是**自扫补丁 + 云端策略融合小节**:
- **自扫主导**:SG 全端口/高危端口分桶(`inspect_sg_rules.json`)、空 SG、CCN 跨 UIN、Peer 跨 UIN —— 这 4 类**唯本地有**(云端策略只给"放通 0.0.0.0/0"一个粗粒度桶,不分端口/不给关联实例数/不给跨 UIN 拓扑)
- **云端补充**:`inspect_advisor.json/findings[]`(SOP 域键 `02`)中其他 SG/VPC/NAT/EIP 相关风险(如"安全组未关联任何实例"、"EIP 长期空闲"等),按 `resource_id` 与上述自扫表 union 去重;**重复实体保留自扫行**(本地语义更精确,合并行的"风险描述"列可叠加云端描述),**新增实体**追加至本域**「风险明细」统一表**,字段映射:

| 严重度 | 资源 ID | 资源名 | 地域 | 风险描述 | 修复 |
|-----:|:-------|:------|:-----|:--------|:----|
| 🔴 | {resource_id} | {resource_name} | {region} | {strategy_name}(持续 {risk_days} 天) | [文档]({repair_url}) |

> `scan_status != 'ok'` 时本节末尾追加一行 `⚠️ 云端策略巡检拉取 {scan_status},合并信号未能补充`,**禁止**写 advisor 字眼。

#### CVM 主机安全(自扫补丁)

> **数据源**:`inspect_cwp.json`(Agent/暴破/登录/漏洞自扫,2026-05-12 收编)+ `inspect_advisor.json`(CVM 资源面与公网 IP 命中,2026-05-21 二次重构:CVM 资源面交 advisor 主导,自扫只补 Agent/暴破/登录/漏洞 4 个维度)。**禁止**写"需手工核查"/"需登录控制台"/"因无 Agent 无法检测整项"(用子字段数据直接判定)。

**CVM 总数**

```markdown
CVM 总数:{MachineCnt} 台(防护中 {ProtectMachineCnt} / 未防护 {NotProtectMachineCnt} / 风险机器 {RiskMachineCnt})
```

**Agent 状态巡检表（必须列出）**

```markdown
| 巡检项 | 期望结果 | 实际结果 | 风险 |
|--------|---------|---------|------|
| Agent 在线状态 | 全部在线 | ✅/❌ 在线 {agent_online} / 离线 {agent_offline} / 未装 {agent_uninstalled} | ✅/🔴 |
| Agent 版本 | 统一最新版 | {版本分布 from machines_by_region[].AgentVersion} | ✅/🟡 |
| 付费版覆盖 | 全部专业/旗舰 | ✅/❌ 基础版 {N} / 专业版 {N} / 旗舰版 {N} | ✅/🟠 |
| 异常登录事件 | 无异常 | ⚠️ 近期风险登录 {login_total_all} 条(采样 {login_sampled} 条) | ✅/🟠 高 |
| 密码破解事件 | 无成功破解 | ⚠️ 破解尝试 {brute_total_all} 次,**成功 {brute_success} 次** | ✅/🔴 严重(brute_success>0)/🟠 高 |
| 应急漏洞通告 | 全部已修复 | ⚠️ {TotalCount} 个通告,受影响主机 {vul_host_count} 台,漏洞条数 {vul_total_count} | ✅/🟠 |
| 漏洞扫描时效 | 近期有扫描 | {scan_time} / ⚠️ 从未扫描(IfFirstScan=True) | ✅/🟠 |
```

**Agent 未安装明细表(`agent_uninstalled > 0` 必出)**

```markdown
| 地域 | 实例 ID | 机器名 | 内网 IP | 公网 IP | 操作系统 |
|:-----|:-------|:------|:-------|:-------|:--------|
| {region} | {InstanceId} | {MachineName} | {MachineIp} | {MachineWanIp or -} | {MachineOs} |
```
取自 `machines_by_region[region][*]` 中 `AgentStatus == "UNINSTALLED"` 的全部记录。

**密码破解成功明细(`brute_success > 0` 时必出,🔴 严重)**

```markdown
| 时间 | 实例 ID | 机器名 | 受攻击用户 | 源 IP | 地域/来源 | 协议:端口 | 尝试次数 |
|:-----|:-------|:------|:----------|:-----|:---------|:---------|:--------|
| {CreateTime} | {InstanceId} | {MachineName} | {UserName} | {SrcIp} | {Location 或 Country} | {Protocol}:{Port} | {Count} |
```
取自 `brute_attack` 中 `Status == "SUCCESS"` 的全部记录。**单条也必须逐行列**,不可汇总。

**风险登录 Top 10(`login_total_all > 0` 时,按 RiskLevel 倒序)**

```markdown
| 时间 | 实例 | 用户 | 源 IP | 归属地 | 风险维度 |
|:-----|:----|:----|:-----|:-------|:--------|
| {LoginTime} | {MachineName} | {UserName} | {SrcIp} | {Location} | {IsRiskArea?"异地 "}{IsRiskUser?"风险用户 "}{IsRiskTime?"异常时段 "}{IsRiskSrcIp?"风险IP"} |
```
取自 `host_login[:10]`。

**公网 CVM 明细(从 `inspect_advisor.json/findings[]` SOP 域键 `03` 中 strategy_name 含"公网"/"对外暴露"等关键词筛出)**

```markdown
| 实例 ID | 实例名 | 公网 IP | 地域 | 风险 |
|:-------|:------|:-------|:-----|:----|
| {resource_id} | {resource_name} | {findings.extra.public_ip or "-"} | {region} | 🟠 开公网 |
```

**HARD 规则**:
- 数据源**固定**为 `inspect_cwp.json`(Agent/Brute/Login/Vul 自扫) + `inspect_advisor.json`(CVM 资源面/公网 IP) —— **禁止**写"需手工核查"/"请查控制台"/"数据不可得"
- `scan_status != 'ok'` 时用"⚠️ 权限不足,无法判定"替换整表,但必须注明原始 scan_status 字段值(不能假合规写 ✅)
- `brute_success > 0` **必须 🔴 严重 + 逐条列明细表**,哪怕只有 1 条;**禁止**用"N 条,成功 N 条"一笔带过而不列明细
- `agent_uninstalled > 0` 必出未安装明细表(最少 5 列),哪怕上一轮报告已列过
- `login_sampled` 是**腾讯云侧风险登录列表的采样上限 100**,不代表"100 次异常登录";必须用 `login_total_all` 报告实际全量数
- `vul_if_first_scan == True`(TSP 账号典型症状)必须单独标 🟠 "从未扫描过,风险未知",不可和 `vul_total_count=0` 合并写成 ✅ 合规
- `machine_general.MachineCnt == 0` 时整个 `#### CVM 主机安全` 小节以 "CVM 总数 0 台,无需巡检" 一行结束,不再填其他表

> 数据来源:`cwp.DescribeMachines` + `cwp.DescribeVulHostCountScanTime`(自扫:Agent/Brute/Login/Vul);CVM 资源面与公网 IP 由 `inspect_advisor.json` 提供(2026-05-21 起 CVM 资源面交 advisor 主导)

**📌 风险明细合并规则(LLM 内部参考,F34 不暴露来源)**

`#### CVM 主机安全` 是**自扫补丁 + 云端策略融合小节**:
- **自扫主导**:Agent 安装/付费版/暴破成功/风险登录/漏洞修复 5 类细粒度运维信号 —— **唯本地有**(云端策略不给暴破成功明细、不给登录归属地、不给 Agent 在线分布)
- **云端补充**:`inspect_advisor.json/findings[]`(SOP 域键 `03`)中其他 CVM 风险(如开公网/版本停维/裸金属安全/低利用率等),按 `InstanceId` 与上述自扫表 union 去重;**重复实体保留自扫行**,**新增实体**追加至本域风险明细表,字段映射:

| 严重度 | 实例 ID | 机器名 | 地域 | 风险描述 | 修复 |
|-----:|:-------|:------|:-----|:--------|:----|
| 🔴 | {resource_id} | {resource_name} | {region} | {strategy_name}(持续 {risk_days} 天) | [文档]({repair_url}) |

> `scan_status != 'ok'` 时本节末尾追加一行 `⚠️ 云端策略巡检拉取 {scan_status},合并信号未能补充`,**禁止**写 advisor 字眼。

#### COS 对象存储(自扫补丁)

> ⚠️ **执行前必做**:在执行 COS 巡检前,必须重新执行 §SKILL.md「执行巡检前加载凭证」段落中的 `eval $(jq -r ...)` 模板重新 export 环境变量(按需传入 `ACCOUNT_NAME`)。
> COS 使用 Python SDK,从环境变量读取 AK/SK,如果前序巡检域修改了环境变量则会导致权限失败。
> ❗ 每个桶的 ACL 查询必须使用该桶所在地域初始化 CosS3Client,否则会报 AccessDenied 或 NoSuchBucket。

```markdown
存储桶总数 {N} 个，其中 {N} 个公开读取：
{桶名1} / {桶名2} / ... 🟠
```

> 如 SDK 返回 0 个桶(权限不足),标注:
> `⚠️ cos.ListBuckets 返回 0 个桶（子账号可能缺少 cos:GetService 权限），建议控制台确认`

> 数据来源：Python `qcloud_cos` SDK `list_buckets` + `get_bucket_acl`

**📌 风险明细合并规则(LLM 内部参考,F34 不暴露来源)**

`#### COS 对象存储` 是**自扫补丁 + 云端策略融合小节**:
- **自扫主导**:桶清单(桶名/地域/ACL 公有读 fast-path)—— **唯本地有**(云端策略 21 条 strategy 都不给桶名清单,只给桶维度命中信号)
- **云端补充**:`inspect_advisor.json/findings[]`(SOP 域键 `04`)中其他 COS 风险(版本控制/防盗链/HTTPS/生命周期/防误删等),按桶名(`resource_id`)与本地 ACL 表 union 去重;**重复桶保留自扫行**,**新增桶**追加至本域风险明细表,字段映射:

| 严重度 | 桶名 | 地域 | 风险描述 | 修复 |
|-----:|:----|:-----|:--------|:----|
| 🔴 | {resource_id} | {region} | {strategy_name}(持续 {risk_days} 天) | [文档]({repair_url}) |

> `scan_status != 'ok'` 时本节用降级文案"⚠️ 云端策略巡检不可用,以下结论仅基于本地 ACL 扫描",本地 ACL 表保留(双 fallback)。

#### 云审计(护城河)

> 跟踪集子段(配置层,合规摆设,降权 🟡)+ 高危事件流子段(行为层,核心运维信号,🔴/🟠)。腾讯云事件流默认采集 90 天,跟踪集只是把日志投递到 CLS/COS 长期持久化;真正运维价值在事件流里的高危操作,不是跟踪集配置。

##### 跟踪集配置(配置层 / 合规摆设)

```markdown
| 检查项 | 结果 | 风险 |
|--------|------|------|
| CloudAudit 跟踪集 | {按 scan_status 三态写,见下方 HARD} | ✅ / ⚠️ / 🟡 |
| CLS 日志主题 | {N}个 | ✅ |
```

> 数据来源:`inspect_cloudaudit.json`(2026-05-12 收编,双接口 ListAudits + DescribeAuditTracks + caller_identity)/ `inspect_cls.json`

**跟踪集子段 HARD 规则**:
- 数据源固定为 `inspect_cloudaudit.json`,**禁止**写"需手工核查"/"需登录控制台确认"/"cloudaudit.DescribeAudits"等老话术
- 必须**先读 `scan_status`** 再决定文案:
  - `scan_status='ok' && any_track_configured=False` → `🟡 未配置跟踪集(ListAudits=0,DescribeAuditTracks=0,主账号身份确认)。事件流默认采集 90 天,但未投递到 CLS/COS 长期持久化` —— **第 6 轮从 🟠 降为 🟡**(只影响 90 天外能否查,事件流仍在采集,不是无审计)
  - `scan_status='ok' && any_track_configured=True` → `✅ 已配置 N 个跟踪集(ListAudits={audits_total}, DescribeAuditTracks={tracks_total})`
  - `scan_status='caller_subuser'` → `⚠️ 扫描凭证为子账号 {caller_uin}(OwnerUin={owner_uin}),CloudAudit 跟踪集需主账号本人复核` —— **严禁**降级为 🟠/🟡,空返可能是默拒
  - `scan_status='denied'` → `⚠️ 权限不足(CamNoAuth),无法判定`
- **严禁**写"未配置审计 = 没记录":事件流永远在记录(腾讯云 90 天默认窗口),跟踪集只决定是否投递持久化

##### 高危事件流扫描(行为层 / 核心运维信号)

> 本节覆盖最近 {time_window.days} 天高危操作(从 `inspect_audit_events.json/time_window.days` 取,默认 7 天周巡检;月度合规传 `AUDIT_WINDOW_DAYS=30`)。窗口起止:`{time_window.start_iso} ~ {time_window.end_iso}`。

**A. 总览矩阵**(按类别统计,**必出**):

```markdown
| 类别 | 命中数 | 风险 | 主体分布(Top 3) |
|------|--------|------|-----------------|
| 审计反取证 audit_tamper | {N} | 🔴 严重 / ✅ 0 | {username:cnt,...} |
| AK 生命周期 ak_lifecycle | {N} | 🔴 高 / ✅ 0 | ... |
| 权限授予 permission_grant | {N} | 🔴 高 / ✅ 0 | ... |
| 资源毁灭 resource_destroy | {N} | 🔴 高 / ✅ 0 | ... |
| KMS 主密钥 kms_tamper | {N} | 🔴 高 / ✅ 0 | ... |
| MFA 变更 mfa_change | {N} | 🟠 中 / ✅ 0 | ... |
| 用户 CRUD user_lifecycle | {N} | 🟠 中 / ✅ 0 | ... |
| 角色 CRUD role_change | {N} | 🟠 中 / ✅ 0 | ... |
| 安全组改动 sg_change | {N} | 🟡 中 / ✅ 0 | ... |
```

**B. 高危事件明细表**(只出非服务账号 + 高危类别,**逐条列**):

```markdown
| 时间 | 类别 | EventName | 用户 | 来源 IP | 地域 | 资源 | 错误码 |
|------|------|-----------|------|---------|------|------|--------|
| 2026-05-12 13:24 | 资源毁灭 | DeleteBucket | aslinwang | 111.206.145.58 | ap-guangzhou | prefix//1325126984/prd-ai-1325126984/ | 0 |
| 2026-05-06 10:20 | 资源毁灭 | TerminateInstances | bazhshi | 116.211.195.12 | ap-shanghai | instance/ins-q75fwvwx | 0 |
```

**C. 服务账号自动化操作汇总**(`is_service=true` 的 Username,**单行聚合**):

```markdown
| 服务账号 | 本周事件总数 | 主要操作 |
|----------|--------------|----------|
| bh-add-white-list | 10 | CreateUser × 10 |
| cam-add_user | 4 | AttachUserPolicy × 2 / CreatePolicy × 2 |
| CVM_QCSLinkedRoleInClawPro | 1 | ModifySecurityGroupPolicies × 1 |
```

> 数据来源:`inspect_audit_events.json`(2026-05-12 第 6 轮收编,`scripts/inspect_audit_events.py` 产出)

**高危事件流 HARD 规则**:
1. **数据源固定**为 `inspect_audit_events.json`,**禁止**写"需手工核查事件流"/"需登录控制台审计页"等老话术 —— 已自动化采集
2. **窗口透明**:章节抬头必须写明"本节覆盖最近 N 天高危操作",N 从 `time_window.days` 取,不要默认值假设
3. **服务账号必须聚合**:`is_service=true` 的 Username 在 B 表里**严禁**逐条展开(否则一中心 33 条 CreateUser 会淹没真高危信号),只在 C 表单行汇总
4. **真高危必须明细到行**:非服务账号的 audit_tamper/ak_lifecycle/permission_grant/resource_destroy/kms_tamper 必须**逐条**列时间/用户/IP/地域/资源/错误码
5. **零事件**(`total_events==0`)→ 写 `✅ 本周无高危操作`,**禁止**写"无审计配置/审计未启用"
6. **失败事件**(`error_code != "0"`)→ 单独标"操作被拒",可能是攻击者权限不足探测或合法操作被策略拦截
7. **跟踪集子段与事件流子段不可混淆**:跟踪集是投递配置(合规),事件流是行为信号(运维),严禁写成"没建跟踪集 = 没审计"
8. **scan_status='denied'** → 整个事件流子段写"⚠️ 权限不足无法判定"(子账号 audit:DescribeEvents 默认有权,出现 denied 是异常)

#### 标签合规扫描(护城河)

> **数据源**:`$OUT_DIR/inspect_resource_tags.json`(2026-05-18 改走 B 方案 billing 维度,由 `SKILL.md §Step 2.6` 中的 prompt 驱动调 `DescribeBillResourceSummary`,无 Python 脚本)。
> **必需标签**:`head` **必填** + `business` / `customer` **二选一必填**(2026-05-18 决策)。
> **判定分级**:`head` 缺 → 🔴 严重;`business` 和 `customer` 同时缺 → 🟠 高;两者皆缺 → 同时双色。
> **覆盖范围**:**只覆盖上月有消费的资源**;无消费/包年未起算/赠送额度内资源不在范围(权衡 FinOps 视角,billing 自带费用权重 + 自动覆盖所有计费产品)。

##### 11.1 标签合规扫描总览

```markdown
| 必需标签 | 强制性 | 说明 |
|---------|--------|------|
| head | **必填** | 业务负责人 RTX,缺失 → 🔴 |
| business | business OR customer 二选一 | 业务标识,与 customer 同时缺 → 🟠 |
| customer | business OR customer 二选一 | 客户标识,与 business 同时缺 → 🟠 |
| 月份窗口 | — | inspect_resource_tags.json/month 字段(默认上月)|
```

##### 11.2 按业务码(biz_code)分组的未设置标签资源(按上月费用降序)

数据源:`inspect_resource_tags.json/by_product`(按 biz_code 聚合)+ `untagged_details`(逐行明细)。

```markdown
| biz_code | biz_name | 未设置标签的资源数 | 月度费用合计(元) |
|----------|----------|-----------------|----------------|
| {biz_code} | {biz_name} | {N} | {amount} |
| ... | ... | ... | ... |
| **合计** | — | {N} | {amount} |
```

> **HARD**:**逐 biz_code 列出全部,不许折叠**;`untagged_details` 为空时整张表写"✅ 上月所有有消费资源均已规范打标"。

##### 11.3 不合规资源明细(必须**逐行列出全部 untagged_details**,禁止截断)

数据源:`inspect_resource_tags.json/untagged_details`(Step 2.6 prompt 已在内部完成"`head AND (business OR customer)`"过滤,**列表里全部都是真不合规资源**,每条带 `verdict` 字段)。

```markdown
| 业务 | 产品 | 地域 | 资源 ID | 名称 | 缺失标签 | 等级 | 月度费用(元) |
|------|------|------|---------|------|---------|------|--------------|
| {biz_name} | {product_name} | {region} | {resource_id} | {resource_name} | head,business,customer | {verdict} | {amount} |
| ... | ... | ... | ... | ... | ... | ... | ... |
```

> **HARD**:
> - **`verdict` 字段直接显示在"等级"列**(`🔴` / `🟠` / `🔴🟠`),**不要二次推断**
> - **不许写"共 N 项"了事**,billing API 返回多少行就列多少行
> - 一条资源同时违反两条规则时,`missing_keys` 列写 `head, business, customer`,`verdict` 显示 `🔴🟠`,**不要拆成两行**

##### 11.4 数据源声明与降级

```markdown
| 项 | 说明 |
|----|------|
| 月份窗口 | inspect_resource_tags.json/month(YYYY-MM,默认上月) |
| 覆盖范围 | 仅覆盖该月**已产生消费**的资源,无消费资源未纳入 |
| 接口 | billing.DescribeBillResourceSummary × 3(TagKey=head/business/customer,TagValue="") |
| scan_status | ok / partial(部分 TagKey 拉取失败,errors 详情)/ denied(billing API 无权) |
```

> `scan_status != 'ok'` 时:`partial` 在本节追加 `errors` 字段内容、`denied` 写 `⚠️ billing API 权限不足,无法判定`(不要写 ✅ 合规)。

##### 11.5 标签合规扫描 HARD 规则(必须严格遵守)

1. **判定规则按 2026-05-18 新版**:`head` 必填 + (`business` 或 `customer`) 二选一必填。`head` 缺 → 🔴;`business`+`customer` 同时缺 → 🟠。
2. **数据范围只覆盖上月有消费的资源**,报告内必须明示"仅覆盖上月已产生消费的资源",**禁止**写"全量资源标签合规率 X%"——空闲/包年未起算/赠送额度内资源不在范围。
3. **不合规资源明细必须逐行列全**(billing API 返回多少行就列多少行),禁止写"共 N 项"。
4. **缺失标签列**用逗号分隔(`head, business`),不要写"全部缺失"。
5. **`untagged_details` 为空 = 合规**,写"✅ 上月所有有消费资源均已规范打标",**不要**写"无资源"——后者会引发歧义。
6. **数据源是 `inspect_resource_tags.json` 的 untagged_details / by_product**,**禁止**根据 inspect_<产品>.json 自己重算 Tags 字段(那是上一版 Phase 1 路线,2026-05-18 已废)。
7. **scan_status 必须先看**:不是 `ok` 时整章节用降级文案,**不要**直接渲染数据。

#### SSL 证书 + 应急响应(自扫补丁)

**漏洞扫描**

```markdown
| 指标 | 结果 |
|------|------|
| 受影响主机 | {N}台 {风险标记} |
| 最近扫描 | {YYYY-MM-DD HH:MM:SS} |
| 待修复漏洞 | {N} {风险标记} |
```

**SSL 证书**

```markdown
| 证书 ID | 域名 | 状态 | 风险 |
|---------|------|------|------|
| {ID} | {域名} | 已过期{N}天 / {N}天后到期 | 🔴 / 🟠 / 🔴 紧急 |
```

> SSL 风险等级:已过期 = 🔴 | ≤3天到期 = 🔴 紧急 | ≤30天到期 = 🟠

**云审计取证**(2026-05-12 第 6 轮重构,应急响应处的"云审计"段复用事件流子段明细做应急取证)

应急响应处的"云审计"段不要再单独写跟踪集状态(已在前面 `#### 云审计` 跟踪集子段出过),而是基于 `inspect_audit_events.json` 给出**应急取证视角**的事件清单:

```markdown
**A. 跟踪集投递状态**(与跟踪集子段一致,一行带过):
{按 inspect_cloudaudit.json/scan_status 三态写,文案与跟踪集子段完全相同,严禁矛盾}

**B. 本周高危行为取证**(从 inspect_audit_events.json 取,**按主体 + 时间倒序**):
| 时间 | 主体(Username/IP) | 行为 | 资源 | 应急判断 |
|------|--------------------|------|------|----------|
| 2026-05-12 13:24 | aslinwang / 111.206.145.58 | DeleteBucket | prd-ai-1325126984 | ⚠️ 是否正常业务删桶?核对发布单 |
| 2026-05-06 10:20 | bazhshi / 116.211.195.12 | TerminateInstances ×2 | ins-q75fwvwx, ins-azmi7uvp | ⚠️ 批量销毁,核对工单 |
```

> 数据来源:`inspect_audit_events.json` / `inspect_cloudaudit.json` / `inspect_cwp.json` / `inspect_ssl.json`
>
> **应急响应云审计取证 HARD 规则**(2026-05-12 第 6 轮):
> - **禁止**只写"跟踪集已配/未配"就算完事(那是合规层信息),应急响应必须出**行为层证据**
> - **禁止**复述事件流子段全部内容,本段视角是"如果出事从这些线索追溯",只挑非服务账号 + 高风险类别(audit_tamper/ak_lifecycle/permission_grant/resource_destroy/kms_tamper)
> - 每条事件后必须给"应急判断"(是否正常业务/是否需要核对工单/是否需要溯源 SrcIP),不要只列原始事件
> - 跟踪集状态(A 块)与跟踪集子段文案必须完全一致,scan_status 三态严禁矛盾

---

### 四(或最末章)、整改推进计划

> **(HARD)** 客户版报告 P8 章节标题固定为 `## 整改推进计划`(不再叫"立即处置清单";顶层 H2 唯一),下挂 6 个固定 H3 子节,顺序与命名严格不可变:`### 整体思路` / `### 阶段计划` / `### 时限分发` / `### 责任协同` / `### 复核指标` / `### 复核范围`。`build_action_list` 输出的 🔴/🟠/🟡 三档 bullets 整体收敛到 `### 时限分发` 之下,以 H4 形式呈现;不要把 bullets 直接挂在 H2 之下。

模板片段(generator 真实渲染顺序):

```markdown
## 整改推进计划

### 整体思路
基于本次扫描发现的高/中风险问题(高危公网暴露 {N1} 项、密码破解成功 {N2} 项、Administrator 授权 {N3} 个、未启用 MFA 子账号 {N4} 个、闲置 AK {N5} 个),按业务影响优先级和实施成本排序,形成 7/30/90 天三阶段闭环:先止血公网暴露与登录链路风险,再补齐 MFA / 最小权限 / AK 治理体系,最后进入配置审计与合规框架的持续运营。

### 阶段计划
| 阶段 | 时间窗 | 关键目标 | 主要负责人 |
|------|------|------|------|
| 紧急修复 | 0-7 天 | 高危公网暴露 / 密码破解 / 未防护主机收敛 | 安全 + 运维 |
| 体系完善 | 7-30 天 | MFA 全覆盖、最小权限、AK 治理 | CAM 管理员 |
| 持续运营 | 30-90 天 | 配置审计、合规框架、SOC 流程 | 安全治理 |

### 时限分发
#### 🔴 ≤ 2 小时
- {处置项1}
- {处置项2}

#### 🟠 ≤ 24 小时
- {处置项1}
- {处置项2}

#### 🟡 ≤ 7 天
- {处置项1}
- {处置项2}

### 责任协同
| 角色 | 关键动作 |
|------|------|
| 安全团队 | 平台策略复扫、漏洞优先级裁定、应急响应 |
| 运维团队 | 主机加固、CWP 客户端铺设、补丁推进 |
| CAM 管理员 | 离职清理、AK 治理、MFA 强制策略 |
| 业务团队 | 公网暴露下线评估、备份策略、变更窗口协调 |

### 复核指标
| 指标 | 目标 | 复核方式 |
|------|------|------|
| 高危公网命中数 | ≤ 5 | 复扫云端策略 (sg.all_port_risks + cos.acl_findings) |
| MFA 覆盖率 | 100% | 巡检 cam.principals.mfa_flags(当前未启用 {N4} 个) |
| CWP 客户端覆盖率 | ≥ 95% | 巡检 cwp.machine_general(当前纳管 {N6} 台) |
| 闲置 AK 数 | ≤ 3 | 巡检 cam idle_ak_90d(当前 {N5} 个) |
| Administrator 授权数 | ≤ 5 | 巡检 cam.admin_grants(当前 {N3} 个) |

### 复核范围
- **下次自动复扫**:N+7 天后再次完整扫描,对比本次报告差异项;
- **复核重点**:所有 🔴 项必须复核,🟠 抽查不少于 50%,🟡 纳入月度治理跟踪;
- **变更窗口**:建议在低峰期执行整改动作,生产环境变更走 CAB 流程;
- **本次范围**:子账号 {N7} 个、Administrator 授权 {N3} 个、闲置 AK {N5} 个、CWP 纳管主机 {N6} 台。
```

> **变量来源(generator 注入)**:N1=`len(all_port)+len(public_r)+len(public_rw)`、N2=`len(brute_success)`、N3=`len(admins)`、N4=`len(no_mfa)`、N5=`len(idle90)`(idle_ak_90d)、N6=`cwp.machine_general.MachinesAll`、N7=`cam.principals where type==user`。**禁止**让 LLM 现场写整改章节文本;静态文案以 generator 输出为唯一真源。

**处置项编写规则：**
- 包含具体资源 ID，如 "sg-7e1vuor3 限制数据库端口来源"
- SSL 证书即将到期的要单独列出并注明到期天数

**(HARD) 修复文档链接引用规则(F34)**:处置项**优先**追加官方修复文档链接,**严禁**自己编造 URL:
- 数据源(内部):`inspect_advisor.json/findings[*].repair_url`(已是腾讯云官方 `[文档](url)` 抽出的纯 URL)
- 写法:`- 营销 redis-xxx 关闭公网访问 ([文档](https://cloud.tencent.com/document/product/239/30912))`
- `repair_url` **为空字符串** → **不加链接,不写"暂无文档"占位**(留空即可)
- **禁止**根据资源类型猜测文档 URL —— ~10% 上游 markdown 本就缺修复文档,属结构性缺口
- 仅本地扫描独占的处置项(如 `#### CAM 账户安全` / `#### 标签合规扫描`)**不加链接**,处置语义已足够清晰
- 链接文案统一为「文档」,**禁止**用「advisor 文档」「智能顾问指引」等暗示来源的措辞

**(HARD)** 立即处置清单**用 `repair_url` 单链接形式**(短文案约束,bullet 项不能塞 119 字 SOP);§二项目详情各大类下「修复建议」列才渲染 `repair` markdown 全文。两者数据源不同(`repair_url` vs `repair`),**不要混用**:
- §二修复建议列 = `repair` 全文(竖线 `\|` 转义,换行替换为空格/`<br>`)
- §三处置清单链接 = `[文档](repair_url)`(单链接,允许空 → 不出链接)

---

### 文档尾部

```markdown
---

报告生成：{YYYY-MM-DD HH:MM} GMT+8 | SOP: TC-SEC-SOP-001 v1.0 | 执行人：{用户}
下次复查：{下周同一天日期}
```

---

### 五、扫描降级说明(scan_status 汇总,F34)

> **目的**:在报告末尾用一张表统一披露**本次巡检哪些数据源出现降级**,避免读者把"没数据"误读成"没风险"。任何 `scan_status != 'ok'` 的源**必须**在此节列一行。

```markdown
| 数据源 | scan_status | 影响域 | 文案 |
|:-------|:------------|:------|:----|
| 云端策略巡检 | denied | 云安全/云可靠/云性能/云限制 各动态产品小节 | ⚠️ 云端策略巡检不可用,本小节结论仅基于本地扫描数据(建议给当前子账号附加 `QcloudAdvisorReadOnlyAccess` 等只读策略后复跑) |
| 云端策略巡检 | partial | 云安全/云可靠/云性能/云限制 各动态产品小节 | ⚠️ 云端策略巡检部分失败({errors_count} 项),命中可能不全;主表本地数据未受影响 |
| CAM 本地巡检 | denied | `#### CAM 账户安全` | ⚠️ 子账号 cam 读权限不足 |
| ...其他 | ... | ... | ... |
```

❌ [HARD · F34] 本节文案**禁止**出现 `advisor` / `智能顾问` / `strategy` / `costopt` 字眼;统一用「云端策略巡检」描述外部命中源,**只在需要给用户提示开权限时**保留 CAM 策略名(如 `QcloudAdvisorReadOnlyAccess`,因为这是用户必须看到的腾讯云控制台原生字符串,无法替换)。

**(HARD) 云端策略巡检降级专用文案**:
- `inspect_advisor.json/scan_status == 'denied'` → 所有命中云端策略的动态产品小节末尾追加一行 `⚠️ 云端策略巡检不可用,本小节结论仅基于本地扫描`;**本地 inspect_*.json 主表保留**(双 fallback)
- `inspect_advisor.json/scan_status == 'partial'` → 各动态产品小节末尾追加 `⚠️ 云端策略巡检部分失败(失败 {errors_count} 项),命中可能不全`;errors 列表附在本节
- `findings == []` 且 `scan_status == 'ok'` → 不需要额外说明,本地主表正常出即可(避免噪音)

> 此节仅在**至少有一个 scan_status != 'ok'** 时出现;全 ok 时整节省略。

---

## 报告输出方式

### 输出为 HTML 报告

将 `inspector_doc_customer.md` 完整内容转为 HTML 网页,用 `preview_url` 工具展示给用户。具体样式见 SKILL.md §Step 4.2 产物 A。

### 报告标题命名规范

```
【{账户名}】云资源安全巡检报告 {YYYY-MM-DD}
```

---

## 完整流程

1. **加载凭证**:按 SKILL.md「执行巡检前加载凭证」段落从环境变量 `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` 读取并 export
2. **逐域巡检**:按 SKILL.md §巡检执行流程 Step 0-2.6 跑各 `inspect_*.py` / 直签 API 落盘 JSON
3. **汇总数据**:LLM 按本模板 4 大类×动态产品骨架组织 Markdown 内容(护城河 + 自扫补丁 + advisor 单源)
4. **交付报告**:生成 HTML 报告并展示给用户
5. **返回结果**:将 HTML 报告展示给用户

---

## 单域 / 应急取证报告模板

> 单域 / 应急取证轻量模板见 [15-narrow-query-paths.md](15-narrow-query-paths.md)

本节只做导航。完整 4 大类×动态产品月度巡检沿用本文上面所有章节；单域查询与应急取证另起轻量形态,详细骨架在 15-narrow-query-paths.md。

### 用户语义 → scope → 模板路由

| 用户语义示例 | scope | 报告形态 | 模板位置 |
|------|------|---------|---------|
| "生成巡检报告" / "云资源安全巡检" / "全量巡检" | `full` | 4 大类×动态产品完整报告(本文上面所有章节) | 本文件 §一/§二/§三/§四/§五 |
| "看下我的 SSL 证书 / 安全组 / CVM 状态" / "扫一下 §0X 这个域" | `narrow` | 单域轻量返回(单表 + ≤3 条处置) | 15-narrow-query-paths.md §1/§2 |
| "查一下 X 资源最近的操作" / "谁动了 Y" / "确认是不是被入侵" | `narrow_forensic` | 取证清单(命中事件流水 + 关联资源 + 1-2 条结论) | 15-narrow-query-paths.md §3/§4/§5 |

### narrow 报告头骨架

```
# 【{域名}】快速巡检 {YYYY-MM-DD HH:MM}
账户:{account_name}  范围:单域({domain_code})  扫描方式:advisor + 本地补丁
```

正文只出 1-2 张表,**不渲染**完整概览矩阵 / 处置清单 / 降级说明;明细表 schema 复用本文对应域 §0X 表头,**列数可裁剪**保留最关键 4-6 列。

### narrow_forensic 报告头骨架

```
# 【取证】{资源 ID 或 用户名} {YYYY-MM-DD HH:MM}
账户:{account_name}  范围:应急取证  时间窗:{N} 天  数据源:CloudAudit LookUpEvents
```

正文按时间倒序铺事件流水(EventName / EventTime / SourceIPAddress / UserName / RequestParameters 节选);末尾 1-2 条明确结论(命中真风险 / 未发现异常 / 需扩窗复扫)。**禁止**套用完整 4 大类模板填白。

---

## 变更日志

| 日期 | 变更内容 |
|------|---------|
| 2026-05-22(三次) | **skill-creator 第三轮死肉清理 — tccli 执行块清零 + 4 大类×动态产品措辞统一**:删除 `report-notes.md`(已被本文件吸收);`references/01-iam-cam.md` 827→335 行(-59%):移除 4 处 `**执行方法:**` + ```bash``` tccli 执行块(已被 `inspect_cam.py` SDK 收编),保留检测项 schema 但表头由 `tccli cam <action> --Foo {Y}` 改为 API 名格式 `cam.<action>(Foo)`;`02-network-security-group.md` 503→114 行(-77%),删 SG audit + NAT/EIP audit 两段大 bash 块;`03-cvm-host.md` 移除 `tccli cvm DescribeInstances` 执行块,表格列改 `cvm.DescribeInstances`;`10-logging-monitoring.md` `tccli cloudaudit` → `cloudaudit.*` 单行收口。本文件 `13-report-template.md` 8 处「数据来源 tccli xx YY」→ API 名格式;HARD 规则「禁止动态小节里现场 tccli 补查」→「禁止动态小节里现场调云 API 补查」(收口范围扩大);L883/L889/L909 三处「12 域月度巡检 / 12 域完整报告 / 完整 12 域模板填白」→「4 大类×动态产品 / 4 大类」。`15-narrow-query-paths.md` / `16-dispatcher-protocol.md` 同步把残留的 `12 SOP 域 / 12 域硬骨架 / 12 域矩阵 / 01-12 域表格明细` 等历史措辞统一为 `4 大类×动态产品 / 4 大类骨架 / 4 大类矩阵`。`SKILL.md` L73 `供 tccli / SDK 使用` → `供 SDK 使用`;L101/L124/L139/L166 四处 `12 SOP 域骨架 / 不走 13 模板的 12 SOP 域全骨架 / 不套 12 域骨架 / 12 SOP 域完整报告` 全部改为 4 大类×动态产品体系措辞;L177/L181 性能底线入口 prose 收掉 900s 历史归因长描述,**保留 L183 反向硬约束**「`sign.py` 直签 HTTP,不用 `tccli` 子进程」防 Step 4 LLM 动态补查时滑回 tccli;L305 `12 份 inspect_*.json` → `各份 inspect_*.json`(避免脚本数量增减触发文档漂移);L383/L387 references 路由从 `01-iam-cam.md ~ 12-incident-response.md` 改为「护城河三处」(`01-iam-cam.md` / `10-logging-monitoring.md` / `11-compliance-tagging.md`),Step 4 LLM 仅 Read 这三处,其他动态产品小节直接渲染 `inspect_advisor.json` 不读 reference。**保留**项:HARD 禁字眼里照引的 `tccli cloudaudit DescribeAudits 等老话术` / 12 域 changelog 历史 / Python 脚本注释「不阻塞 12 域主扫」(scope 外)。grep 自检三组断言全过:活动 .md 内 0 处 `tccli` 执行块、0 处非 changelog 的「12 SOP/12 域/12 大类/12 份/01-12」措辞、bash 块全部为合法 jq 凭证加载/python3 inspect_*.py 调用/Step 2.6 prompt 驱动 heredoc。 |
| 2026-05-22(二次) | **子模板物理移除 + 过渡话术清理**:删除 §05/§06/§07/§08/§09 五个旧 SOP 域子模板(已被「动态产品小节通用骨架」7 列风险明细表取代);剩余 §01/§02/§03/§04/§10/§11/§12 子节标题去掉"挂载到 ## ... > ### ..."括号注释,直陈护城河/自扫补丁小节名(`#### CAM 账户安全(护城河)` / `#### 网络与安全组(自扫补丁)` 等)。删除「模式 A/B 双模式说明」中模式 B 历史多项目合并/拆分章节(单账户驱动已是事实);文档头部模板从「双项目 / 单项目」简化为「巡检账户:{账户名}({UIN})」单账户单行;报告标题命名规范从合并/拆分两行改为统一 `【{账户名}】云资源安全巡检报告 {YYYY-MM-DD}`;完整流程第 1 步「确认范围:合并/拆分」删除。§三处置项编写规则去掉"以项目名开头(合并报告时)/两项目共同问题合并描述"两条。`(HARD · 2026-05-22)` / `(HARD · F34 · 2026-05-22 4 大类重构)` 等规则编号上的日期标全部清除(变更日志本节保留日期作为历史档案)。 |
| 2026-05-22 | **章节重构 — 4 大类×动态产品 + repair 全文渲染**:§二项目详情骨架由 12 SOP 域固定章节改为 4 大类(`## 云安全` / `## 云可靠` / `## 云性能` / `## 云限制`)+ 合规独立大类 `## 合规与资产标签`;advisor `GroupName` 字段路由大类、`product` 字段路由小节;护城河(CAM/云审计/标签)固定挂载点,自扫补丁(SG/CVM/SSL/COS)固定挂在云安全大类。**`group=成本` finding 整体丢弃**(用户决策:由 costopt skill 独家承载),**禁出 `## 云成本` 大类**。§一巡检概览矩阵从 12 行文字摘要改为 4 列产品矩阵表(对齐控制台「巡检概览」截图风格,无成本列),合规标签独立成行放表下方。修复列从 `[文档](url)` 单链接改为渲染 advisor `repair` markdown 全文(竖线 `\|` 转义、换行替换为空格/`<br>`)。`repair_url` 字段保留供 §三立即处置清单与 costopt skill 使用,§二不再使用。§0.5 HARD #5 表头改为统一 7 列风险明细 schema + 4 个护城河/自扫专属表头;#6 加粗段允许位置改为按护城河/自扫小节列举;#8 三层架构改写为「动态产品 / 自扫补丁 / 护城河」+ 新增 (g) 禁出 `## 云成本`;#9 改写为渲染 `repair` 全文 + 转义规则。空大类整段省略,护城河小节强出。SKILL.md grep 自检从 12 SOP 校验改为 4 大类松校验 + 3 个护城河必出 + 禁出 `## 云成本` + 单链接 ≤5 + `修复建议` 列名 ≥1。 |
| 2026-05-21(二次) | **架构重构 — advisor 单源 + 8 类自扫补丁**:实测 advisor 资源面覆盖度后,把 §05/§06/§07/§08 整域改为 advisor 单源(本地 DescribeInstances/DescribeClusters/CKafka VipList 全部下线),资源总数取 `max(resource_total)`,跨产品命中并入同一张风险明细表;CVM 资源面/公网 IP 同步交 advisor,本地 `inspect_cwp.json` 只留 Agent/暴破/登录/漏洞 4 维护城河信号。§0.5 #8 HARD 重写为"自扫补丁 + 云端策略融合"三层架构(护城河 §01/§09/§10/§11、自扫补丁 SG/VPC/CWP/SSL/CloudAudit/COS 名单/标签、advisor 单源 §02 EIP+§03 资源面+§05+§06+§07+§08);§0.5 #5 表头规则随之更新为统一风险明细 schema;§一概览矩阵新增数字取数路由 HARD 表;§02-§08 七处合并指引段改写区分自扫主导/云端补充各自字段映射。SKILL.md 同步重写 Step 1 节、Step 3 速查表、4 条硬红线、advisor 数据源使用骨架;`inspect_scan.py` 从 763 行精简到 ~400 行,落盘 5 个自扫键 JSON。**取舍**:CLB Listener/TKE Endpoint/CKafka VipList 等产品特有字段从报告里移除,统一用 strategy_name 文案表达,运维侧丢字段但报告排版一致性大幅提升。 |
| 2026-05-21 | **F34 对外脱敏 9 处修订**(对外不暴露内部判定来源/系统代号):§0.5 加 HARD #10 全局禁字眼条款(advisor/智能顾问/strategy/costopt 全文 0 命中);§0.5 #8 改"双源域明细合并为一张表"(原"📋 子表"撤销);§0.5 #9 改"链接文案统一为「文档」";§一矩阵后的 `[advisor]/[本地]/[双源]` 数据源标记 HARD 表整段移除;§02-§08 七处「📋 advisor 命中详情」子表改为「📌 云端策略命中合并指引/渲染指引」内部参考(LLM 必须合并并入本地主表,不独立成表);列名"策略名"统一改为"风险描述";§三 repair_url 引用规则改名「修复文档链接引用规则」,链接文案禁止暗示来源;§五降级说明节表头和文案全部脱敏为「云端策略巡检不可用/部分失败」。同步改 SKILL.md L739-789 段对外渲染指令。例外保留 CAM 控制台原生字符串 `QcloudAdvisorReadOnlyAccess`(用户必须照此搜索)。 |
| 2026-05-20 | **advisor 融合 5 处增量**:§0.5 加 HARD #8(双源域 instance_id union 去重,严重度取较高)/ #9(repair_url 直接读,空字符串留空,严禁编造)；§一概览矩阵后追加 `[advisor]/[本地]/[双源]` 数据源标记 HARD 表;§02-§08 每域末尾追加 `📋 advisor 命中详情` 子表 schema(双源域 union 优先本地;主拉域 advisor 为风险主源);§三处置清单加 `repair_url` 引用规则 HARD(优先追加官方文档链接,空则留空,严禁猜);新增 §五扫描降级说明(advisor/本地 scan_status 统一披露,denied 降级为单源、partial 子表保留 + 提示),仅在有降级时出现。 |
| 2026-05-13 | **(HARD) 企微版(`wecom_markdown`)从摘要版升级为完整报告**:用户反馈企微消息即是完整报告,不需要点开文档才看明细。从本日起 `wecom_markdown` 必须包含全部 4 段制内容(文档头 + 一、巡检概览 + 二、项目详情 01-12 域完整表格 + 三、处置清单 + 尾部),与腾讯文档版内容结构完全一致。原「企微版只出概览+处置清单」的做法废止。同步更新 SKILL.md §4.3 和本文件模式 A 节。 |
| 2026-05-12 | §10/§12 第 6 轮重构:§10 拆为 §10.1 跟踪集(配置层,降权 🟡 合规摆设)+ §10.2 高危事件流(行为层,**新核心** 🔴/🟠);§10.2 三张硬表(总览矩阵 + 高危明细 + 服务账号聚合);§12 云审计段从 4 行 scan_status 文案改为 A 跟踪集一行带过 + B 本周高危行为取证清单;§一概览矩阵 §10 行从"跟踪集 ✅/🟠"改为"高危事件 🔴/🟠/✅";新增 8 条 HARD(服务账号必聚合 / 真高危必明细到行 / 窗口透明 / 零事件 != 无审计 / 失败事件单独标 / 不可混淆配置层与行为层 / scan_status=denied 例外 / §10.1 文案与 §12 完全一致);数据源 `inspect_audit_events.json` 7 天默认 + `AUDIT_WINDOW_DAYS` 月度可调。 |
| 2026-05-12 | §10/§12 CloudAudit 跟踪集模板按 `scan_status` 三态重写,数据源切到 `inspect_cloudaudit.json`(双接口 ListAudits + DescribeAuditTracks + caller_identity 探测);**子账号身份(scan_status=caller_subuser)严禁写 🟠**,改为 ⚠️ 需主账号复核,杜绝每月给所有子账号扫描刷 🟠 噪声;§一 概览矩阵 §10 行扩为三态。 |
| 2026-05-12 | §03 CVM 主机安全从 reference Step 3 现场跑云 API 收编进 Step 1 `inspect_cwp.json`;巡检表从 5 行扩到 7 行(加付费版/漏洞扫描时效);新增"密码破解成功明细"强制表(brute_success>0 时单条也必出,实测发现营销 root@10.2.0.10 被破解 SUCCESS 此前永远漏报);新增"风险登录 Top 10"表;HARD 禁止写"需手工核查"/假合规;login_sampled vs login_total_all 语义区分强调;`vul_if_first_scan=True` 单独 🟠 不得和 `vul_total_count=0` 合并写 ✅。§一 概览矩阵 §03 示例更新。 |
| 2026-05-12 | §02 VPC 跨 UIN 从 Step 3 LLM 现场跑收编进 Step 1 `inspect_vpc_cross.json`;总览表扩至 4 行(本账号/跨 UIN 分开)+ 两张明细表(CCN 实例明细 + Peer 明细);HARD 禁止写"需手工核查",`scan_status=denied` 降级为 ⚠️ 权限不足。§08 CKafka 明细新增"VPC/内网接入点/公网接入"三列;数据源切到 `inspect_ckafka_attrs.json`;加 HARD 禁用 `PublicNetwork` 字段/禁写"需手工核查"/禁混 CKafka 与 Kibana。§一 概览矩阵 §08 示例从 `Kafka 关闭公网` 改为 `ES Kibana / CKafka 公网均关闭` |
| 2026-05-11 | 基于 2026-05-07 实际双项目报告全面对齐:取消独立"账号总量统计"章节(合并入 01 IAM);巡检概览改为文字摘要格式;01 IAM 新增协助者 AK 检查行和 Admin 权限来源表;02 安全组新增"关联实例"列;03 CVM 新增 Agent 巡检表+明细表;05 数据库改为表格+公网暴露明细;06 CLB 改为指标表格;07 TKE 简化为汇总行;08 中间件改为统一表格;09 密钥新增 KMS 行;10 日志改为表格;12 SSL 新增"紧急"等级;删除"五、与上次巡检对比"章节 |
