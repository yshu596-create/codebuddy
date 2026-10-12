# 巡检报告输出规范

> ⚠️ 当前输出形态为 **单文件 HTML 看板 + 回复框 Markdown 概览**。HTML 看板由 `render_costopt_html.py` 脚本渲染（契约见 `html-template.md`），LLM 不手写 HTML/CSS/Chart.js。
>
> 以下仅保留判定逻辑层面的规则（不受输出形态影响），以及 Markdown 概览的格式约束。

---

## 一、数值展示规则

- **金额整数化(F20)**：所有金额展示为整数，不带小数（HTML KPI / 表格统一整数 + 千分号）
- **占比 1 位小数**：百分比类字段保留 1 位小数（如 `92.1%`）
- **中间计算 4 位**：内部分析计算保留 4 位小数，最终展示再取整
- **监控覆盖率保留整数**：如 `92%` 而非 `92.15%`

---

## 二、报告结构规则

### 2.1 节顺序（动态排列原则）

1. 🔴（立即处理）节统一排在前，🟡（建议优化）节统一排在后
2. 同优先级内按**账单费用降序**排列
3. 同一产品有多种问题类型时（如 CVM 同时有停机/闲置/低利用率），按严重程度拆分为独立节
4. 无问题的产品不生成节
5. 账单中有消费但无扫描 API 的产品归入"其他产品"节

❌ [HARD] **禁止用"Top N 优先处置"汇总表替代分产品节**。必须按产品分节展示全量清单，不得截断。

❌ [HARD] **禁止用"🔴 高优先级"/"🟡 关注"等大节包裹所有产品节**。每个产品直接作为独立节，节标题自带 🔴/🟡 emoji。

### 2.2 节标题格式

- 不加序号
- 格式：`### 🔴/🟡 {产品} {描述}({数量}{单位})`
- 描述行：普通文本

### 2.3 关键资源保护汇总(F7)

❌ [HARD] 保护资源**禁止出现在问题资源表中**(F7 联动)

- 无保护资源时整节不展示

### 2.4 其他产品节

账单中有消费但 api-resource.md 无扫描 API 的产品：

- 只报费用不做闲置判定
- 按量标记 + 费用阈值兜底判定

---

## 三、字段写法规范

### 3.1 「负责人」列写法规范（F38）

- ✅ 从资源 Tags 中提取 `head` 标签值填写（如 `andyyfang`）
- 无 head tag → 写 `-`
- ❌ [HARD] 有 head tag 的资源不得写 `-`

### 3.2 「问题类型」列写法规范（F42）

❌ [HARD] **凡涉及"利用率低"的问题类型，必须在文字中写出当前具体数值**，禁止只写"利用率低"等无数值的泛化描述。

- ✅ 正确：`CPU 使用率仅 1.2%，内存使用率仅 7.8%`、`磁盘未挂载`
- ❌ 错误：`CPU 利用率低`（无数值）、`实例低利用率`（无数值）
- advisor 独家命中（无 monitor 数据）：写 `使用率不足（云端策略命中，具体指标见控制台）`

### 3.3 「规格」列写法规范（F36）

❌ [HARD] **所有资源「规格」列禁止留空或写"-"**；规格来源优先级：①Step 1 资源发现 API 返回 → ②billing_lookup.json ResourceName → ③写 `规格待确认`

### 3.4 「预计月省」列写法规范

- `¥1,050` — 统一展示金额，整数(F20)
- `需评审` — 当双路都无可用金额时，**不写 `¥0`**
- 详见 `analysis-rules.md §7.3`

### 3.5 关机 CVM 特殊说明（F39）

- `STOP_CHARGING` → CVM 本身月省 ¥0，但挂载磁盘仍在计费
  - 预计月省 = **挂载 CBS 的 billing_lookup baseline 之和**（或 CBS 刊例价兜底）
  - ❌ **禁止只写 ¥0**
  - ❌ **禁止用 CVM 整机 baseline**（含运行期间计算费用，会高估）
- `KEEP_CHARGING` → 月省 = billing_lookup baseline

### 3.6 定时关机计算口径（F43，2026-06-11 新增）

- 按量付费 CVM 定时关机后，**计算费用停收、CBS 磁盘费用照收**
- 预计月省 = **仅计算部分** × (关机时长 / 24小时)
- 简化公式：`saving = baseline × 0.57`（详见 pricing-rules.md §3.3）
- ❌ **禁止使用旧公式 `baseline × 65%`**（把磁盘也算进节省，高估约 14%）

---

## 四、Pre-Output Check 清单（模型内部执行）

> 发送报告前执行以下检查。与 SKILL.md Pre-Output Check 清单互补。

### 4.1 HARD 级检查

| rule_id | 约束 | 检查点 |
|---------|------|--------|
| F7-critical-protect | MySQL 主备/灾备、Redis 副本跳过闲置判定 | 问题资源表无保护类资源 |
| F20-integer-amount | 金额整数展示 | 全文正则无 `¥[0-9,]+\.[0-9]+` |
| F36-spec-no-dash | 所有资源「规格」列不留空/不写"-" | 规格列无 `\| - \|` 或空值 |
| F38-head-tag-owner | 负责人列从资源 Tags[head] 填写 | 有 head tag 的资源不写 `-` |
| F39-stop-charging-mode | 关机 CVM StopChargingMode 区分计费 | `STOP_CHARGING` 月省不为 ¥0，不用 CVM 整机 baseline |
| F41-cbs-list-price | CBS 月省用刊例价兜底 | CBS 资源月省无"需评审" |
| F42-utilization-value | 利用率类问题必须写出具体数值 | 问题类型含"利用率"/"使用率"时，同行必须含百分比 |
| F43-timer-shutdown-compute-only | 定时关机月省仅计计算费用 | 禁用 `baseline×65%`，改用 `baseline×0.57` |
| F34-no-source-leak | 用户可见输出禁止暴露内部判定来源/系统代号 | 全文 grep -i 不含 `advisor` / `智能顾问` / `strategy` / `costopt` |
| no-secret-exposure | 禁止输出 SecretKey 明文 | 凭证约束 |
| F23-silent-execution | 输出中无中间执行状态/Pre-Output Check 结果/rule_id | 全文无"Pre-Output Check""凭证就绪""Step N""F1/F7"等模式 |
| F-HTML-COMPLETE | HTML 看板全量列出 findings / protected / exempted | 禁止 Top N 截断 |
| F-MARKDOWN-LIMIT | 回复框 Markdown 概览 ≤600 字 | 五段式 |
| F-DELIVERY-ONLY | 最终交付物只有 HTML 看板 + 回复框 Markdown 概览 | 禁止生成其他格式报告 |

### 4.2 MUST 级检查

| rule_id | 约束 | 检查点 |
|---------|------|--------|
| F6-saving-formula | 每条建议附「预计月省」计算说明（baseline × ratio%） | - |
| F8-coverage-min | 多指标产品覆盖率取最小值 | - |
| F9-skip-layered | 豁免标签支持分维度/分产品/带过期 | - |
| F13-risk-extended | 风险等级带可回滚性 + 执行时机 | - |
| risk-level-basic | 每条建议必须标注 [低/中/高] | - |
| CLB-priority | CLB 无后端标 🟡 | - |

---

## 五、降本计划报告格式

> 降本计划报告格式定义见 `analysis-rules.md` 第六章。直接以 Markdown 写在回复框。
