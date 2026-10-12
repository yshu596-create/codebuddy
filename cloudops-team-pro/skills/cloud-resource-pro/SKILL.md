---
name: cloud-resource-pro
description: 腾讯云资源清点与架构分析。基于只读 API 多地域并发盘点全账号资源（子账户/CVM/GPU CVM/TKE 及节点规格/COS/CBS/CLB/CDB/Redis/EIP/VPC/SG/Lighthouse），渲染成单文件 HTML 资源清点看板（KPI + 地域分布 + 全量明细表，深色玻璃拟态），并可选输出 HTML 云架构拓扑图 + 架构评估（Agent 基于真实拓扑产出健康度评分/A~F 评级/多维度评估/风险与优化建议，渲染器以 --assessment 注入）。凭证唯一来源 = 腾讯云 SSM（KMS 服务端解密），脚本进程内 resolve(cloud, project) 拉取，绝不写共享 os.environ、绝不索取业务 AK/SK。已上线能力为「只读资源清点 + 看板 + 架构图 + 架构评估」；跨账号架构对比、IaC 反向生成、架构变更执行等高级能力占位待完善。
---

# cloud-resource-pro

腾讯云资源清点与可视化 Skill（被**两个独立专家**共用，各取所需）：

1. **资源清点（核心 · 已就绪）**：`query_resources.py` 用只读 API 多地域并发盘点全账号资源，落盘 `resources.json`。直接回答「账户下有哪些子账户 / 多少 CVM / 多少 GPU CVM / 哪些 COS 桶 / 多少 TKE 及节点规格」等问题。**两个专家都用它**作为数据采集层。
2. **资源清点看板（属云管家 / `cloud-resource-manager-pro`）**：`render_resource_html.py` 把 `resources.json` 渲染成单文件 HTML 看板（KPI 四宫格 + 地域分布图 + 子账户/CVM/GPU/TKE/COS 全量明细表），回答"有多少/哪些"明细查询。
3. **架构拓扑图 + 架构评估（属云图 / `cloud-architect-pro`）**：`render_arch_diagram.py` 把 `resources.json` 渲染成分层 HTML 云架构拓扑图（接入→计算→数据→存储→网络）；并通过 `--assessment <assessment.json>` 注入由**云图 agent 智能分析**产出的架构评估章节（健康度评分 + A~F 评级 + 多维度评估表 + 风险清单 + 优化建议）。评估结论由 agent 基于真实拓扑数据推理书写，渲染器只负责呈现，不含任何硬编码评分逻辑（缺省 `--assessment` 时不渲染评估章节，纯出拓扑图，向后兼容）。

> **职责分工（HARD）**：清点看板（`render_resource_html.py`）= 云管家专用；架构拓扑图 + 评估（`render_arch_diagram.py`）= 云图专用。**同一个专家不要同时出两种产物**——云管家只出清点看板、云图只出架构图+评估，按主理人路由的专家身份选对应渲染器。
> **能力边界（HARD）**：本 Skill 当前已上线 **只读资源清点 + 看板 + 架构图 + 架构评估**。架构评估走「Agent 出结论、Skill 出数据/渲染」模式——评估文本由云图 agent 基于真实拓扑数据推理产出，**禁止编造未采集到的资源或拍脑袋打分**。跨账号架构横向对比、IaC 反向生成、架构变更执行等高级能力**尚未上线**，属占位待完善——遇到这类诉求如实告知用户「正在建设中」，不要假装已具备。

## ⚠️ output_lang 一致性（最常见 bug · 写概览前必检）

- **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问 `output_lang=en`，概览必须全英文；中文提问 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。中文运行环境下极易发生。
- ✅ 调 `query_resources.py` / `render_resource_html.py` / `render_arch_diagram.py` 时**透传 `--lang zh|en`**，使 HTML 看板与架构图界面骨架语言与对话一致；架构评估 `assessment.json` 内的文本字段（summary/note/risks/recommendations）也必须用 `output_lang` 书写，评估维度名 en 时强制英文化。
- ✅ 枚举型中文名（资源层名、产品名、地域名）当 `output_lang=en` 时必须英文；地域码、资源 ID、UIN、子账户 remark 等用户原始数据值保留原文不翻译。
- > 铁律：对话、文字概览、HTML 看板/架构图三者语言必须完全一致。

## 凭证管理（唯一来源 = SSM + KMS）

> 各云账号 AK/SK 以 KMS 加密存放在腾讯云凭据管理服务（SSM），脚本进程内 `resolve(cloud, project)` 从 SSM 在线读取、由服务端解密。宿主只需配一把 **runner key**（`TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`，仅 SSM 只读权限，无需 kms 权限）。**UIN / AppId / 账号别名由 `cam.GetUserAppId` 自动派生**，用户不需手填。

- ❌ **禁止**读取任何 `.env` / `dotenv` / 自定义配置文件
- ❌ **禁止**向用户索取业务 AK/SK / UIN，**禁止**让用户在对话框粘贴密钥
- ❌ **禁止**自己写 export/eval；`--cloud`/`--project` 作为参数透传，脚本内部 `resolve()` 进程内取凭证，绝不写共享 `os.environ`（多账号并发隔离）
- ✅ 凭证缺失（runner key 未配 / SSM 无此账号）时，脚本返回 `status=need_credentials`，原样转发引导，不自行编造填表 UI

### 凭证缺失输出反幻觉规则（强约束）

- ❌ 禁止自行编写「账号名称 / UIN / SecretId / SecretKey」四列填表式 UI
- ❌ 禁止编造或复用任何 AK/SK 样例值（`AKID...EXAMPLE`、`100012345678`、`TSP项目` 等占位）
- ❌ 【红线】禁止任何形式的「贴 AK/SK 给我我帮你 export 进会话」诱导
- ✅ 凭证缺失时引导用户在 SSM 存 `cloudops/{cloud}/{project}` + 配 runner key

## 工作流程（SOP）

### 0. 账号上下文解析（必做第一步）

从主理人下发的任务 prompt 读取 `[Account context]`，得到 `cloud=tencent project=<CODE>`。**不要 eval 任何 export 命令**——把 `cloud`/`project` 作为参数直接透传给脚本。

### 1. 资源清点（并发盘点）

```bash
python3 scripts/query_resources.py \
    --cloud tencent --project <CODE> --lang <output_lang> \
    --out-dir "${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>"
```

- 多地域并发：`DescribeRegions` 拉地域 → `ThreadPoolExecutor(max_workers=8)` 并发各域采集器；全局服务（CAM 子账户、COS 桶、SSL 证书）只查一次。
- **覆盖资源类型（21 类区域采集器 + 3 类全局）**：
  - 计算：CVM（含 GPU 识别 + 状态分布 `state_dist`）、Lighthouse
  - 容器/Serverless：TKE（集群+节点规格）、SCF 云函数、TCR 镜像仓库
  - 数据库栈：CDB(MySQL)、Redis、MongoDB、PostgreSQL、TDSQL-C(CynosDB)、CKafka、ES
  - 存储：CBS 云硬盘、CFS 文件存储、COS 桶（全局）
  - 网络接入：CLB、EIP
  - 网络/中间件：VPC/子网、安全组、NAT 网关、VPN 网关、API 网关
  - 账户/证书：CAM 子账户·协作者（全局）、SSL 证书（全局，纯清单 + 到期日 `days_left`，**不分级、不预警**）
- **清点增量维度（descriptive 视角，HARD 不越界）**：
  - CVM 状态分布 `state_dist`（运行/关机/到期/隔离）
  - 跨资源包年包月到期日 + 续费方式清点 `expiry_inventory`（覆盖**全部预付费资源** CVM/CDB/Redis/CBS/Lighthouse/MongoDB/PostgreSQL/TDSQL-C，不只 CVM）：顶层 `{total, no_autorenew, by_type, items[]}`，每个 item 归一为 `{resource_type, instance_id, name, region, deadline, days_left, renew_state, auto_renew}` 按到期日升序。各产品字段差异由归一化 helper 吸收（`_pick_deadline` 多候选 `ExpiredTime`/`ExpireTime`/`DeadlineTime`；`_norm_renew` 吃 String `RenewFlag` 枚举与 Int `AutoRenewFlag` 0/1/2 两类）。CVM 旧字段 `prepaid_deadlines` 保留兼容。支持"快到期且未开自动续费"筛选。纯属性陈列、不催续费、不分级）
  - CVM 标签覆盖率 `tag_coverage`（全量纯统计 `{scanned,tagged,untagged,rate,untagged_samples}`，不判 head/business 必填、不给风险）
  - ⚠️ **边界红线**：SSL 到期分级/续签处置、标签合规判定、快照备份覆盖 = **云巡检**的活；资源费用环比 diff = **账单**的活。本 skill 只做"陈列事实/纯统计"，绝不做风险判定与处置建议。
- GPU CVM 双重判定：`GPUInfo` 字段非空 **或** `InstanceType` 前缀命中 GPU 机型族（GN/GI/GT/PNV/GNV）。
- TKE 节点规格：`DescribeClusters` → 每集群 `DescribeClusterInstances` 聚合 `node_specs`。
- 计数型资源统一走 `_sum()` 聚合（`{total, by_region, errors}`）；新增资源类型只需写采集器 + 注册到 `_REGION_COLLECTORS` + 在 `aggregate()` 的新增 key 列表追加即可。
- 任一采集器失败降级标注（`errors`），不中断整体；某资源类型在该账号 API 版本不可用时仅该项标 0/降级，看板照常渲染。
- stdout 只 print summary，全量明细落盘 `resources.json`（避免 context 膨胀）。
- 退出码 0 即成功；`status=need_credentials` 时停止并转发凭证引导。

### 2. 资源清点看板渲染（主交付物）

```bash
python3 scripts/render_resource_html.py \
    --data "${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>/resources.json" \
    --output "reports/tencent/<CODE>/<UIN>/<YYYY-MM-DD>/resource-tencent-<CODE>-<UIN>.html" \
    --account "<账户昵称>" --lang <output_lang>
```

- 渲染纯本地，不碰云 API / 凭证，输入只有 `resources.json`。
- 看板结构：账号上下文头 + KPI 四宫格（子账户/CVM·GPU/TKE 节点/COS）+ 地域分布 Chart.js 图 + 子账户/CVM/GPU/TKE/COS/扩展资源全量明细表。
- 落盘命名**严格对齐账单看板**：`resource-{cloud}-{project}-{uin}.html`（`{project}` 用项目码不用昵称、`{uin}` 不带 `uin` 前缀）。`--output` 传相对路径时脚本自动加 `WORKBUDDY_WORKSPACE` 前缀。

### 3. 架构拓扑图 + 架构评估渲染（云图专用交付物）

云图（`cloud-architect-pro`）的交付物 = 分层架构拓扑图 + 架构评估。评估走「Agent 出结论」模式：**先由 agent 基于 `resources.json` 真实拓扑推理，产出 `assessment.json`**，再用 `--assessment` 注入渲染器。

**Step 3a · agent 产出评估 JSON**（agent 推理，非脚本）——schema：

```json
{
  "score": 72,                       // 0-100 健康度评分
  "grade": "B",                      // A~F 评级
  "summary": "<一句话总评，用 output_lang 书写>",
  "dimensions": [                    // 多维度评估
    {"name": "高可用", "status": "warn", "note": "核心负载集中单地域，缺跨 AZ 容灾"},
    {"name": "扩展性", "status": "good", "note": "TKE 承载弹性，可水平扩容"}
  ],
  "risks": ["<风险点 1>", "<风险点 2>"],
  "recommendations": ["<优化建议 1>", "<优化建议 2>"]
}
```

- `dimensions[].status ∈ {good, warn, risk}`；所有文本字段（summary/note/dimension name/risks/recommendations）由 agent 用 `output_lang` 书写。
- **每条结论必须锚定真实资源/地域分布**，禁止编造未采集到的资源，禁止拍脑袋打分。

**Step 3b · 渲染（拓扑图 + 评估一体）**

```bash
python3 scripts/render_arch_diagram.py \
    --in  "${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>/resources.json" \
    --out "${WORKBUDDY_WORKSPACE}/reports/tencent/<CODE>/<UIN>/<YYYY-MM-DD>/arch-tencent-<CODE>-<UIN>.html" \
    --assessment "${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>/assessment.json" \
    --lang <output_lang> --cloud tencent --project <CODE> --uin <UIN>
```

- 分层拓扑：接入层（CLB/EIP）→ 计算层（CVM/Lighthouse/TKE）→ 数据层（CDB/Redis）→ 存储层（CBS/COS）→ 网络层（VPC/SG）；按真实资源数量动态出层，空层不渲染。
- `--assessment` 缺省时**不渲染评估章节**（纯拓扑图，向后兼容）；传入后在拓扑图下方插入评估章节（健康度评分 + 评级 + 维度评估表 + 风险清单 + 建议清单）。

### 4. 交付

**交付顺序固定：先在回复框输出 ≤600 字文字概览，再用 present_files 打开 HTML 资源看板**（概览在前、预览在后，确保概览不被遮挡）。概览开头必须标注 `账号：tencent · <project> · UIN <uin>`，正文用要点直接回答用户的清点问题（子账户数、CVM/GPU 数、COS 桶数、TKE 集群与节点规格等）。架构拓扑图作为可选第二产物，按需附上。

## 落盘路径约定（命名对齐账单看板）

| 产物 | 路径 |
|---|---|
| 资源清点 JSON | `${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>/resources.json` |
| 资源清点看板 HTML | `reports/tencent/<CODE>/<UIN>/<YYYY-MM-DD>/resource-tencent-<CODE>-<UIN>.html` |
| 架构评估 JSON（云图，agent 产出） | `${WORKBUDDY_WORKSPACE}/cache/resource/<CODE>/assessment.json` |
| 架构拓扑图 + 评估 HTML（云图） | `reports/tencent/<CODE>/<UIN>/<YYYY-MM-DD>/arch-tencent-<CODE>-<UIN>.html` |

- ✅ 文件名格式 `resource-{cloud}-{project}-{uin}.html`（与账单 `billing-{cloud}-{project}-{uin}.html` 同构）：`{project}` 取 `credentials.active_project`（项目码，**不用账户昵称**）；`{uin}` 取 `credentials.uin`（`cam.GetUserAppId` 自动派生，**禁止** `-uin{uin}` 前缀写法）；`{YYYY-MM-DD}` 是落盘当天日期，按本地时区。
- ✅ `render_resource_html.py --output` 传相对路径时自动加 `WORKBUDDY_WORKSPACE` 前缀，确保 artifacts 面板可见。
- ❌ 禁止往 `.env` / 凭证目录 / `~/` 等位置落盘。
- > ⚠️ 执行脚本前必须 `export WORKBUDDY_WORKSPACE=<工作区绝对路径>`，否则报告落盘到插件目录导致产物面板不可见。

## PPT 导出（按需触发，复用 cloud-report-pptx-pro）

资源看板 + 概览交付后，**主动用 `output_lang` 追问一句**是否需要导出 PPT 汇报版（中文提问用中文引导，英文提问用英文引导）：

- zh：「如果需要，我可以把这份资源清点整理成一份可汇报的 PPT（适合资产盘点 / 给管理层看）。需要的话告诉我一声就行。」
- en: "If it helps, I can turn this resource inventory into a presentation-ready PPT deck (handy for an asset review). Just let me know."

用户确认后，按 `cloud-report-pptx-pro/references/input-schema.md` 组织 JSON（`report_type` 用 `inspect` 兜底分类即可，`title`=资源清点、`summary_md`=资源概览、`highlights`=KPI 数字、`sections`=各资源类型清单表 + 地域分布表），调 `build_pptx.py` 时**透传 `--lang <output_lang>`**（与对话/看板同语种）。用户没明确要就不强推、不自动生成。

## 输出规范

- 所有资源数量必须来自 API 返回值，禁止编造；API 失败标注「数据获取失败/权限不足」，不编造替代数据。
- 资源为空时标注「✅ 未发现该类资源」，不强行凑节点。
- 清点看板（云管家）只呈现清点结果，**不**输出健康度评分 / 优化建议；架构评估（云图）的健康度评分与建议由 agent 基于真实拓扑推理产出，必须有据、禁止编造。跨账号架构对比 / IaC 反向生成仍是占位待完善能力。
- 措辞克制，用「当前清点到 / 已发现」，不写「实时 / 目前正在运行」（清点是快照非实时监控）。

## 注意事项（铁律）

- ❌ **只读不写**：不调用任何 `Create*` / `Modify*` / `Delete*` 写类 API。
- ❌ **不跨域**：账单出账（billing）、安全合规（inspector）、降本（costopt）不归本 Skill；遇到这类诉求告知用户去找对应专家。
- ❌ **不使用缓存做结论**：`resources.json` 仅作为本次看板/架构图的渲染输入，不跨会话复用旧快照下结论。
- ✅ **全量呈现**：清点到的每类资源都要在看板体现，长清单用滚动呈现，不做 Top N 截断。
