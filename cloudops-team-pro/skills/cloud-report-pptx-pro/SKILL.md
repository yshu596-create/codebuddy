---
name: cloud-report-pptx-pro
description: 云运维报告 PPT 导出技能。当用户在账单/审计/巡检/降本任意一个专家产出 HTML 看板 + AIOps 智能概览之后，明确说"导出 PPT""出一份汇报材料""转成 PPT""做成幻灯片""给老板看的版本"等关键词时触发。把已有的概览要点 + HTML 中的图表/表格数据，按 VCIC 模板（深空蓝 + 翠绿，16:9）渲染成统一风格的演示稿，落盘到 `reports/{uin}/{YYYY-MM-DD}/<report-type>.pptx`。**仅在用户明确要求时触发，不替代默认的 HTML+概览交付。** (cloudops-team@my-experts)
---

# Cloud Report PPT — 云运维报告 PPT 导出技能

> ⚠️ **使用前提**：本技能仅在 **HTML 看板 + AIOps 智能概览已经产出之后** 被触发。你不会从零跑账单/审计/巡检/降本，也不会自己调任何云 API。**所有数据来源于上一步专家已落盘的 `concise.json` / 概览文字 / HTML 数据**。如果上一步还没跑过，先告诉用户"得先做 XX 分析才能导出 PPT"，然后让主理人去调对应专家。

## 一、何时触发（路由匹配）

主理人 / 上游专家观察到以下任一类型问法时，调用本技能：

| 用户问法关键词 | 含义 |
|---|---|
| 导出 PPT、转成 PPT、做成幻灯片、转成 ppt | 把当前/上一份报告转成 PPT |
| 给老板看的版本、汇报材料、汇报版、复盘材料 | 同上，更口语 |
| 出一份 PPT 一起发、再来一份 ppt、加一份 ppt | 同时要 HTML + PPT |
| 帮我做个 PPT、生成 PPT、PPT 模版、套个模板 | 直接索取 PPT |

**不触发的情况**：
- 用户只说"看一下账单 / 巡检一下 / 检查高危操作"等常规分析诉求 → 走对应专家默认 HTML+概览路径，**不**触发 PPT
- 用户问"PPT 模板长啥样""支持导出 PPT 吗" → 这是能力咨询，主理人直接答即可，不触发

## 二、能干什么

把以下四类报告任一种，导出为统一风格的 .pptx：

| 报告类型 | 来源 | 输入字段 | 章节结构 |
|---|---|---|---|
| `billing` | cloud-billing-analyst-pro | 账单概要、按产品 TOP、按标签、四类资源变化、智能概览 | 封面 → 目录 → 总览 → 产品成本 TOP → 标签维度 → 资源变化 → 行动建议 |
| `audit` | cloud-auditor-pro | 9 大类高危事件矩阵、操作人 TOP、来源 IP TOP、真高危明细、智能概览 | 封面 → 目录 → 扫描概览 → 9 类事件矩阵 → 操作人/IP TOP → 真高危复核 → 处置建议 |
| `inspect` | cloud-inspector-pro | 风险分级、4 大类（云安全/云可靠/云性能/云限制）发现、整改时限分布、智能概览 | 封面 → 目录 → 巡检总览 → 四大类发现 → 高危项详情 → 整改时限计划 |
| `costopt` | cloud-cost-optimizer-pro | 闲置资源分类、可节省金额、风险分级建议、智能概览 | 封面 → 目录 → 降本总览 → 闲置资源分布 → 可节省 TOP 资源 → 处置策略 |

**风格规范**：
- 使用 `templates/cloud-report-template.pptx`（VCIC 模板，16:9，主色 Deep Space Blue + Vivid Green）
- 页眉页脚保留模板原样（不替换 Department K-OE 等示例文字，让模板原作者标识自然存在）
- 字体使用模板默认（The Group Head Light / The Group Text Regular）
- 配色不修改，章节封面用模板内置深蓝渐变页

## 三、工作流（标准三步）

### Step 1 · 准备数据（由调用方传入）

调用方（即主理人或上游专家）必须先把上一步的产出整理成一个统一的 JSON 输入文件，落盘到 `<WORKSPACE>/cache/pptx_input_<uuid>.json`，结构如下：

```json
{
  "report_type": "billing",            // billing | audit | inspect | costopt
  "title": "云账单分析报告",
  "subtitle": "2026年5月 · 主账号 100012345678",
  "uin": "100012345678",
  "date": "2026-06-11",
  "summary_md": "≤600 字 AIOps 智能概览的纯 Markdown 文本（来自专家回复框）",
  "highlights": [                       // 3-5 条核心亮点，用作三栏要点页
    {"title": "本月总支出", "value": "¥123,456", "delta": "+12.3%", "note": "环比上涨主要由 CVM 拉动"},
    {"title": "TOP 产品", "value": "CVM", "delta": "占 38%", "note": "..."}
  ],
  "sections": [                         // 内容章节，每个生成 1-N 张幻灯片
    {
      "heading": "按产品拆解",
      "subheading": "TOP 10 高消费产品",
      "table": {                        // 二选一：要么 table 要么 chart
        "headers": ["产品", "金额", "占比", "环比"],
        "rows": [
          ["CVM", "¥45,678", "38%", "+12%"],
          ["COS", "¥23,456", "19%", "+5%"]
        ]
      }
    },
    {
      "heading": "按标签维度",
      "chart": {
        "type": "bar",                  // bar | line | pie
        "categories": ["业务A","业务B","业务C"],
        "series": [{"name":"金额","values":[45678,23456,12345]}]
      }
    }
  ],
  "actions": [                          // 行动建议页内容（最后一页）
    {"priority": "P0", "text": "..."},
    {"priority": "P1", "text": "..."}
  ]
}
```

**规则**：
- 字段缺失就跳过对应章节（脚本必须容错）
- `table` 行数超过 12 行时自动分页
- 一个 section 同时给 table 和 chart 时，优先用 chart，table 作为下一页

### Step 2 · 调用 build_pptx.py 渲染

```bash
/usr/bin/python3 \
  ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input <WORKSPACE>/cache/pptx_input_<uuid>.json \
  --output <WORKSPACE>/reports/<uin>/<YYYY-MM-DD>/<report_type>.pptx \
  --lang <output_lang>   # zh|en，跟随主理人下发的 [Output language]，缺省 zh
```

> 路径默认与对应专家的 HTML 落盘目录同级，方便用户对照查看。
> **`--lang` 语义**：仅控制 PPT 的**固定骨架文案**（报告类型标题、目录页、章节封面、行动建议页等）的语言；从输入 JSON 取出的业务内容（标题、概览正文、表格数据）由上游专家按 `output_lang` 生成，原样渲染。两者须为同一语言。

**Python 依赖**：仅 `python-pptx`（系统 `/usr/bin/python3` 已有；如缺失，先 `/usr/bin/python3 -m pip install --user python-pptx`，**禁用** managed Python 因 lxml 签名冲突）。

### Step 3 · 触发预览 + 文字回包

落盘成功后：
1. 通过 `open_result_view` 工具触发 PPT 预览（或在回复中直接附 attachment）
2. 在回复框追加一句导航：
   > 📊 PPT 已生成在 `reports/<uin>/<date>/<type>.pptx`，按 VCIC 模板（16:9，深空蓝主色）渲染。HTML 看板和概览仍在原位置。

**禁止**：
- ❌ 凭空补充数据（PPT 内容必须 100% 来自传入 JSON 的字段，不允许 LLM 自由发挥）
- ❌ 修改模板的页眉页脚（保留原样，含 VW/CSD-class 占位）
- ❌ 把 HTML 截图嵌入 PPT（数据用结构化字段重画，避免位图模糊）
- ❌ 在 PPT 里展示 SecretId/SecretKey/任何凭证

## 四、目录结构

```
cloud-report-pptx-pro/
├── SKILL.md                      # 本文件
├── templates/
│   └── cloud-report-template.pptx  # ⭐ VCIC 模板（请勿覆盖）
├── scripts/
│   └── build_pptx.py             # ⭐ PPT 生成器（接 JSON 输入，吐 .pptx）
├── references/
│   ├── template-layouts.md       # 模板内 46 个 layout 的速查表
│   └── input-schema.md           # JSON 输入结构详细规范
└── examples/
    └── billing-sample.json       # 示例输入（可直接 `--input` 试跑）
```

## 五、调用方对接清单（写给上游专家看）

| 你需要做的 | 怎么做 |
|---|---|
| 把概览文字传给我 | `summary_md` 字段，原样塞 ≤600 字 Markdown |
| 把图表数据传给我 | 每个 section 的 `chart` 字段，结构同上 |
| 把表格数据传给我 | 每个 section 的 `table` 字段（headers + rows） |
| 设置封面标题/副标题 | `title` + `subtitle` |
| 设置落盘路径 | 命令行 `--output` 参数，建议放在 `reports/{uin}/{date}/` |

调用示例（主理人或专家在 Bash 里）：

```bash
# 1) 准备 JSON 输入（专家在生成 HTML 时顺手写一份 concise.json，主理人转发即可）
INPUT=$WORKSPACE/cache/pptx_input_$(uuidgen).json
# ... 填充 JSON ...

# 2) 渲染 PPT
OUT=$WORKSPACE/reports/$UIN/$(date +%Y-%m-%d)/billing-tencent-${DISPLAY_NAME}-uin${UIN}.pptx
mkdir -p "$(dirname $OUT)"
/usr/bin/python3 ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input "$INPUT" --output "$OUT" --lang "$OUTPUT_LANG"   # zh|en，跟随 [Output language]，缺省 zh

# 3) 通知用户
echo "PPT ready: $OUT"
```

## 六、依赖与降级

- **依赖**：`python-pptx` 1.0+（已知 macOS managed Python lxml 有签名冲突，**强制走 /usr/bin/python3**）
- **图表降级**：构造原生 PowerPoint Chart 复杂且模板里图表绑定关系易丢，统一用 **表格 + 柱形图占位形状（auto_shape 矩形条）** 兜底，看起来仍然像图表，不会因为底层 chart XML 出错失败
- **字段缺失**：JSON 任一字段缺失都跳过对应页，不报错
- **模板缺失**：脚本启动先校验模板存在；不存在直接报错退出

## 七、多项目场景（每项目独立 PPT）

当主理人通过账号选择器多选了 2-3 个项目时，每个项目已经各自产出了一份独立的 HTML 看板 + AIOps 概览。PPT 导出遵循**同一原则——每项目独立生成一份 PPT**，不做跨项目数据合并。

### 为什么不合 1

- 不同项目的账单口径、币种、标签体系可能不同，合并易误导
- 与 HTML 报告模型一致（一对一），实现简单、逻辑清晰
- 各 PPT 可独立分发，接收方只看自己的项目

### 工作流

主理人收到"导出 PPT"请求后：

1. **逐项目调用**：对每个已完成报告的项目，各准备一份 `pptx_input_<uuid>.json`，各调一次 `build_pptx.py`
2. **输出路径**：沿用各项目自己的 `reports/{cloud}/{project}/{uin}/{date}/` 分桶，不会覆盖
3. **汇总回填**：主理人在汇总目录表格的 `PPT` 列逐行填入链接（初始为 `—`，生成后回填）

示例（3 项目账单分析后导出 PPT）：

```bash
# 项目 1：YX
/usr/bin/python3 .../build_pptx.py \
  --input $WORKSPACE/cache/pptx_input_yx.json \
  --output $WORKSPACE/reports/tencent/YX/100012345678/2026-06-15/billing-tencent-YX-uin100012345678.pptx \
  --lang zh

# 项目 2：TSP
/usr/bin/python3 .../build_pptx.py \
  --input $WORKSPACE/cache/pptx_input_tsp.json \
  --output $WORKSPACE/reports/tencent/TSP/100098765432/2026-06-15/billing-tencent-TSP-uin100098765432.pptx \
  --lang zh

# 项目 3：GW（如果未失败）
/usr/bin/python3 .../build_pptx.py \
  --input $WORKSPACE/cache/pptx_input_gw.json \
  --output $WORKSPACE/reports/tencent/GW/.../billing-tencent-GW-....pptx \
  --lang zh
```

主理人回填汇总目录：

```
| Account | Status | Summary | Report | PPT |
|---|---|---|---|---|
| tencent · YX | ✅ | 总额 ¥120k，环比 +5% | [billing.html](...) | [billing.pptx](...) |
| tencent · TSP | ✅ | 总额 ¥80k，环比 -2% | [billing.html](...) | [billing.pptx](...) |
| tencent · GW | ❌ failed | — | — | — |
```

### 注意事项

- 失败的项目不生成 PPT，汇总目录里 PPT 列保持 `—`
- 各项目 PPT 独立调用 `build_pptx.py`，互不依赖，可串行执行
- 每个项目 PPT 的 `title`/`subtitle` 自然携带项目标识，不会混淆

## 八、版本记录

- v1.0（2026-06-11 · 初版）：支持 billing/audit/inspect/costopt 四类报告导出；模板基于 VCIC 16:9 精简版；字段驱动，**仅在用户明确要求时触发**
- v1.1（2026-06-15 · 多项目独立 PPT）：新增「多项目场景」章节——多选 2-3 项目时每项目独立生成 PPT，不做合并；主理人汇总目录增加 PPT 列
