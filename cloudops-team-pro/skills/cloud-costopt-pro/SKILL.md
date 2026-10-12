---
name: cloud-costopt-pro
description: 云闲置资源巡检与降本专家。当用户提到"降本""省钱""闲置资源""资源优化""规格降配""预留实例""浪费""资源利用率"或指定云账户做降本分析时触发。基于账单驱动扫描，采集 30 天云监控数据，按风险等级给出可执行降本建议，输出**单文件 HTML 数据看板**（深色 + Chart.js + 全量资源展示）+ 回复框 Markdown 概览（≤600 字五段式）。
---

# 云闲置资源巡检与降本专家

## ⚠️ output_lang 一致性（最常见 bug · 写概览前必检）
- **自检门**：写概览前先确认 `[Output language] output_lang=?`——用户英文提问时 `output_lang=en`，概览必须全英文；中文提问时 `output_lang=zh`，概览必须全中文。
- ❌ **最常见的泄漏**：用户英文提问，但概览/回复写成了中文。这在中文运行环境下极易发生。
- ✅ 正确做法：在写第一个字之前，默念「`output_lang` 是什么？」，然后用该语言写。
- ✅ `--lang` 参数透传给渲染脚本，确保 HTML 看板界面骨架语言一致。
- ✅ 枚举型中文名（产品名、风险等级、优化建议类型）当 `output_lang=en` 时必须翻译为英文，禁止保留中文原文。

## 目录结构

```
cloud-costopt-pro/
├── SKILL.md                  # 本文件：流程骨架 + 凭证管理 + 专属约束
├── references/
│   ├── api-constraints.md    # API 约束、产品码映射表（含通用兜底）、地域映射
│   ├── api-resource.md       # 各产品资源列表 API（纯调用信息，无判定规则）
│   ├── api-monitor.md        # 云监控 Namespace/指标/维度（纯参数参考，无判定规则）
│   ├── analysis-rules.md     # ⭐ 唯一权威判定文件：通用原则 + 全产品判定 + 专属策略矩阵 + 降本计划排期
│   ├── output-templates.md   # 历史报告格式（已废弃，保留备查）
│   ├── html-template.md      # ⭐ HTML 看板渲染契约（章节顺序、字段映射、视觉规范）
│   └── pricing-rules.md      # 预计月省计算口径、比例系数
└── scripts/
    ├── sign.py                  # TC3-HMAC-SHA256 签名 + API 调用（支持 STS X-TC-Token）
    ├── summarize_monitor.py     # 监控数据压缩摘要
    ├── collect_monitor.py       # ⭐ Step 2 监控采集主脚本（含 advisor 跳过）
    ├── scan_advisor.py          # advisor 成本组扫描
    ├── prepare_costopt_data.py  # ⭐ Step 0.3/0.4+Step 1 账单拉取+资源发现 + 凭证（禁止重写）
    ├── judge_costopt.py         # ⭐ Step 3 全产品闲置判定 + 自动渲染 HTML（禁止重写）
    └── render_costopt_html.py   # ⭐ Step 3 单文件 HTML 看板渲染（深色玻璃拟态 + Chart.js）
```

> **凭证源**: SSM + KMS（唯一来源）。各云账号 AK/SK 以 KMS 加密存于 SSM，脚本经 `resolve(cloud, project)` 在线读取、服务端解密；宿主只需配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`（仅 SSM 只读权限，无需 kms）。跨账号代查由目标 secret 内 `role_arn`(+`assume_from`)字段驱动，`resolve()` 自动 AssumeRole（详见 §一）。

> **路径约定**：`{SKILL_ROOT}` 指本 skill 所在目录（即 `skills/cloud-costopt-pro/`），LLM 执行时应从专家插件路径解析为绝对路径。

### references 懒加载规则（HARD）

❌ [HARD] **禁止开局一次性读完所有 references/**。昨晚实测开局读 6 份 references 花 43s,Step 3 又重复读一次 output-templates 花 51s,累计 ~1min30s 是纯浪费。

✅ [HARD] **严格按 Step 对应关系读,读过的不要重复读**:

| 阶段 | 允许读的 references | 禁止读 |
|------|---------------|--------|
| Step 0 账单 API | 脚本内置，**无需读 references** | 全部 |
| Step 1 资源发现 | 脚本内置，**无需读 references** | 全部 |
| Step 2 监控采集 | `api-monitor.md` | analysis-rules / html-template / pricing-rules |
| Step 3 判定+渲染 | `analysis-rules.md`、`html-template.md`、`pricing-rules.md` | 上面 3 份在 Step 3 **最多读一次**,已读就别再读 |

`scripts/sign.py` 开局读一次即可,后续 exec 用 `sys.path.insert(0,'...scripts'); from sign import call_api` 复用。

---

## 专属约束（闲置巡检）

> 数据真实性/安全/执行纪律/节省金额估算口径/变更谨慎/数据时效/不做承诺 等通用约束直接定义在本 SKILL.md Pre-Output Check 清单中。
> 以下为本 Skill 专属约束。核心判定规则全部权威定义于 `references/analysis-rules.md` 零章，本节仅列指针。

### 核心判定规则（指针）

| 主题 | 权威源 |
|------|--------|
| **F7 关键资源保护**（MySQL 灾备/只读、Redis 副本、MongoDB SECONDARY、PostgreSQL readonly/guard、finops-critical 标签） | `references/analysis-rules.md` §0.5 |
| **F8 监控覆盖率校验**（≥80%正常 / 50~80%提示 / <50%不判定；多指标取最小；30天窗口对齐） | `references/analysis-rules.md` §0.3 |
| **F9 资源豁免机制**（finops-skip 5 种语义：true/downgrade/relax/分产品/带过期；判定流程） | `references/analysis-rules.md` §0.1 |
| **F13 风险等级扩展**（`[等级] 可逆性 · 执行时机` 完整格式） | `references/analysis-rules.md` §0.2 |
| **F6 预计月省计算**（baseline × ratio%，禁止凭空估算） | `references/pricing-rules.md` |

### 通用产品兜底

账单中出现 `references/api-resource.md` 无扫描 API 的产品 → 归入"其他产品"节，只报费用不做闲置判定。

**❌ 严禁将以下产品归入"其他产品"**（均有完整 API + 判定规则，账单有费用必须执行资源级扫描）：

| 产品 | 判定规则 |
|:-----|:--------|
| COS 对象存储 | 低频访问 bucket 降档建议 |
| TDMQ 消息队列 | 空集群/低吞吐 topic |
| TKE 容器服务 | 空集群/低利用率集群 |
| EKS Serverless 容器 | 无 Pod 空集群 |
| 向量数据库 VectorDB | 闲置实例 |
| TCHouse-D 数据仓库 | 低利用率集群 |
| ES Elasticsearch | 闲置/低利用率 |
| CKafka 消息队列 | 闲置实例 |
| NAT 网关 | 低利用率 |
| CLB 负载均衡 | 无后端绑定 |
| EIP 弹性公网IP | 未绑定 EIP |
| CFS 文件存储 | 近无IO |
| CBS 云硬盘 | 未挂载 |

允许归入"其他产品"的唯一条件：账单费用=0 / API返回 unauthorized / 上表确实无对应条目的产品

⚠️ **不允许因 token 限制、复杂度、超时风险等主观原因将上述产品归入"其他产品"**

### F25-no-degradation-speech（HARD）

❌ [HARD] 禁止输出"由于文档内容较大""已写入摘要版本""完整明细数据太大"等降级话术；文档最终内容必须全量（≤50KB 单次写 / >50KB 分两次覆写到全量，见 `references/output-templates.md` §5.2.1）。

### shared/cache/ 禁用（HARD）

❌ [HARD] **本 skill 不得读写任何缓存目录**。监控指标(CPU/内存/流量)需要当下实时值,资源状态(运行中/已释放/配置变更)随时在变,任何缓存都会让降本建议过期。

---

## 一、凭证获取与安全处理

> ✅ **2026-06-08 简化为环境变量单一来源 + STS AssumeRole 双路径**：用户在 shell 中 `export` 后即可使用，**不再支持** `.env` 文件、`env_passthrough`、`ACCOUNT_{ALIAS}_*` 多账号别名映射。

凭证持久化禁用 / 静默约束等通用规则见本 SKILL.md Pre-Output Check 清单。

### 1.1 凭证读取（两条路径，单账号）

| 路径 | 适用场景 | 必填 | 可选 |
|------|---------|---------|---------|
| **路径 1：runner key + SSM** | 本账号自查（最简单） | 宿主配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` / `TENCENTCLOUD_BOOTSTRAP_SECRET_KEY`（仅 SSM 只读） | `TENCENTCLOUD_BOOTSTRAP_REGION`（SSM 地域，默认 ap-guangzhou）、`TENCENTCLOUD_FINOPS_CRITICAL_TAG`（保护标签前缀） |
| **路径 2：STS AssumeRole 跨账号代查** | 主账号委派、最小权限收敛 | 目标账号 SSM secret 里加 `role_arn` 字段（+ 推荐 `assume_from` 指向存主账号 AK 的 secret） | secret 内 `role_external_id`、`role_duration`（默认 3600s） |

> 业务账号 AK/SK 不进环境变量，由管理员加密存入 SSM；脚本按 `--project <CODE>` 从 SSM 取该账号密钥（KMS 服务端解密）。跨账号代查由目标 secret 内 `role_arn`(+`assume_from`)字段驱动，`resolve()` 自动 AssumeRole——宿主**不**再 export `TENCENTCLOUD_ROLE_ARN`。配好 runner key 即自动启用 SSM，无需 `CLOUDOPS_SSM_ENABLED=true`（可选急停开关，设 false/0/no/off 才关）。

UIN / AppId / 账户昵称统一由脚本调用 `cam.GetUserAppId` 反查（路径 2 用换出来的临时凭证调）。

❌ [HARD] **业务账号凭证只接受 SSM**：不读 `.env`、不读 `ACCOUNT_*`、不读任何配置文件、不让用户在终端 export 明文业务 AK/SK / ROLE_ARN、不通过对话向用户索取 AK/SK / UIN / RoleArn。宿主侧只接受 runner key；跨账号的 role_arn / 主账号 AK 全部存 SSM secret。缺失态必须输出 §1.3 的"环境未就绪"友好引导话术（已内置于 `prepare_costopt_data.py`，stdout 返回的 JSON 含 `prompt` 字段）。

❌ [HARD] **禁止把 AK/SK / RoleArn / TmpSecretKey 写入任何文件**（包括 `.env`、`config.json`、报告、日志等）。

❌ [HARD] **控制台「角色 SwitchRole」≠ STS AssumeRole**：控制台切换是 cookie 级身份，脚本拿不到；要让 skill 跨账号扫资源，必须在目标 SSM secret 配置 `role_arn` 走 STS API。

### 1.2 凭证获取流程（脚本侧已实现，LLM 不需重写）

`scripts/prepare_costopt_data.py` 内置 `resolve_credentials()` 完整链路（跨账号已收敛进 `build_credential_env.resolve()`）：

```
1. resolve(cloud, project) 从 SSM 取该账号 secret（KMS 服务端解密；缺 runner key 或 SSM 无此账号 → need_credentials + prompt）
   · 若 secret 带 role_arn → resolve() 在进程内用主账号 AK（assume_from 指向的 / 本 secret 内的）调
     sts.AssumeRole 换临时 AK/SK + Token 返回，并剔除 role_arn（防 double-assume）；失败 → 退出码 6
2. resolve_credentials 直接消费返回的 (sid, sk, token)，不再自己 assume
3. 用最终 AK/SK（+Token）调 cam.GetUserAppId 反查 Uin/AppId/Nickname
4. 把 Token 经 sign.set_process_token() 注入进程局部 holder（不写共享 os.environ），call_api 自动透传 X-TC-Token
5. 返回 {status: "ok", credentials: {display_name, uin, app_id, source, has_sts_token}}
```

LLM 调用骨架（推荐，零 boilerplate）：

```python
import json, subprocess, sys
proc = subprocess.run([
    sys.executable,
    "{SKILL_ROOT}/scripts/prepare_costopt_data.py",
    "--work-dir", "/tmp/costopt_primary",
] + (["--account-name", account_name] if account_name else []),
    capture_output=True, text=True, timeout=600)
result = json.loads(proc.stdout.strip().splitlines()[-1])

if result.get("status") == "need_credentials":
    print(result["prompt"])   # §1.3 话术原样转发
    sys.exit(0)

# status == "ok"
display_name = result["credentials"]["display_name"]
uin = result["credentials"]["uin"]
source = result["credentials"]["source"]   # primary_env / sts_assume_role
```

### 1.3 凭证缺失指引话术（固定文本，禁止改写）

脚本输出的 `prompt` 字段含 SSM+KMS 凭证模型的完整指引（runner key 配置 + 路径 2 STS AssumeRole 跨账号代查），并附上"控制台 SwitchRole ≠ STS AssumeRole"的关键警告。**LLM 必须原样转发**，禁止：

- 改写"凭证未就绪：SSM 里没有对应密钥"标题或调整 emoji
- 把 `export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...` 代码块拆掉或合并
- 自行追加"请问您的 UIN 是多少 / 角色名叫什么"等索取语
- 删掉路径 2 STS AssumeRole 步骤（即便用户场景看起来是单账号）
- 改写「控制台 SwitchRole ≠ AssumeRole」警告（保留原文）
- 把 [前往腾讯云控制台] 链接的 URL 改成别的（必须保留 `https://console.cloud.tencent.com/cam/capi`、`https://console.cloud.tencent.com/cam/role`）

### 1.4 单账号约束

本 skill 一次扫描**只服务一个账户**。如需切换：

- 路径 1：业务账号 AK/SK 存于 SSM，切换账号即用不同的 `--project <CODE>` 触发扫描；runner key（宿主 `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`）保持不变。
- 路径 2：切换目标账号即换 `--project <CODE>`；跨账号的 `role_arn`(+`assume_from`)写在各目标账号自己的 SSM secret 里，宿主 runner key 不变。

**不再支持** "扫描所有账号 / 逐个扫描" 等多账号批量语义。

---

## 二、闲置资源巡检流程

### Step 0：账单驱动 — 确定扫描范围（必须第一步，不得跳过）

#### 0.1 解析账号范围

本 skill **只支持单账号扫描**。`accounts` 列表恒为长度 1，凭证读取走 §一 的 SSM 唯一路径（`resolve(cloud, project)` 从 SSM 取密钥 + `cam.GetUserAppId` 反查）：

```
- 默认场景
    → resolve(cloud, project) 从 SSM 取该账号 AK/SK；UIN/AppId/账户昵称 由 prepare_costopt_data.py 内部反查
    → 凭证缺失（缺 runner key / SSM 无此账号）→ 输出脚本返回的 prompt 文本（§1.3 友好引导），**停止本次执行**

- 用户指定了账户别名（如"扫描 一中心管控账户 闲置资源"）
    → 把 account_name 作为展示别名透传给脚本（写入报告标题）
    → 凭证仍只从 SSM 取；不再做 ACCOUNT_*_DISPLAY_NAME 匹配
    → 主路径无凭证 → 输出 prompt（§1.3 友好引导），**停止本次执行**

- "所有账号/全部账号/逐个扫描" 类多账号语义
    → 本 skill 不再支持，引导用户用不同 --project 逐个触发单账号扫描

# 单账号下不需要 for 循环
执行 0.2 月份判定
执行 0.3 账单 API 调用
执行 Step 1（资源发现）
执行 Step 2（监控数据采集）
执行 Step 3（判定 → 自动渲染 HTML 看板 → 先输出 Markdown 概览 → 再 present_files 打开 HTML）
```

#### 0.2 月份判定（在 API 调用前执行）

闲置巡检基于**上月已出账账单**确定扫描范围（与 billing Skill 的月份判定逻辑一致）：

| 场景 | 账单月份 | 说明 |
|------|---------|------|
| 默认（未指定） | **上月**（T-1月） | 当月账单未出账，用上月确定产品/地域范围 |
| 用户明确说"用本月" | 当月 | 尊重用户意图，但报告头部标注 `⚠️ 当月账单尚未出账，扫描范围可能不完整` |

#### 0.3 账单 API 调用

#### ✅ [HARD] 必须使用 prepare_costopt_data.py（禁止自行编写账单拉取+资源发现脚本）

`scripts/prepare_costopt_data.py` 已内置 Step 0.3/0.4（账单汇总+资源级账单预取）+ Step 1（资源发现）全部逻辑：

```bash
python3 {SKILL_ROOT}/scripts/prepare_costopt_data.py \
  --cloud tencent --project <CODE> \   # 必传：进程内 resolve(cloud, project) 从 SSM 取该账号凭证(KMS 服务端解密)，规避竞态
  --account-name "账户名" \
  --work-dir /tmp/costopt_<CODE> \
  --lang <output_lang> \
  [--bill-month YYYY-MM]
# --lang zh|en：跟随主理人 [Output language]；凭证缺失时据此返回对应语种引导，缺省 zh
# 凭证唯一来源是 SSM，宿主只需配 runner key(TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY)；--project 必传，无"省略读裸名"回退
```

输出落盘（同一条 exec，无需手写任何账单/资源 API 代码）：
- `billing_lookup.json` — 资源级账单索引（ResourceId → cost/region/charge）
- `bill_summary.json` — 账单汇总（products/region_ids/total_cost/bill_month）
- `resource_discovery.json` — 所有产品资源发现结果

stdout 输出 scan_ready JSON（用于 Step 2 参数传入）：
```json
{"status": "ok", "scan_ready": {"products_count": N, "resources_count": N, "bill_month": "YYYY-MM", "regions": [...]}, "credentials": {...}, "work_dir": "..."}
```

❌ **禁止自行编写账单拉取或资源发现脚本**（不得写 step0_billing.py、step1_scan.py 等任何替代脚本）

---

❌ [HARD] **Step 0.3 + 0.4 必须合并到同一条 exec 内完成**，禁止为"拿产品列表"、"拿地域列表"、"拉资源级账单"分别发独立 exec。2026-06-01 实测：碎片化 exec 导致 Step 0 耗时 36 分钟（正常应 2-5 分钟），原因是每条 exec 有 3-5s LLM 往返开销，十几条累计超过 1 分钟，加上 context 压缩和 skill 重加载雪上加霜。**凡是 Step 0 内的操作，全部写进下方骨架的同一条 exec。**

并行调用两个账单 API，产品和地域均由账单决定，不硬编码：

```
billing / DescribeBillSummaryByProduct  → 当前有消费的产品列表
  {BeginTime: "{账单月}月1日 00:00:00", EndTime: "{账单月}月末日 23:59:59"}
billing / DescribeBillSummaryByRegion   → 当前有消费的地域列表
  {BeginTime: 同上, EndTime: 同上}
过滤: RealTotalCost > 0
```

✅ [HARD] **地域换码必须用 RegionId(数字)查 references/api-constraints.md §4.1 表,不得用 RegionName(中文)**:账单 API 返 `RegionId='4'` + `RegionName='华东地区（上海）'`(全角括号),用 RegionName 字符串匹配会因全/半角括号差异全部 miss,导致 region 传成中文名让所有资源 API 返 0 条(2026-05-09 实测踩坑)。

> API 参数格式（`"YYYY-MM-DD HH:MM:SS"`）、产品码→扫描模块映射、RegionId 数字→API地域码映射 见 `references/api-constraints.md`

#### 0.4 资源级账单预取（HARD · 并发·防止 Step 3 绑价现查现用）

✅ [HARD] **在 Step 0.3 拿到产品列表后,立即并发拉 `DescribeBillResourceSummary` 全量**,按 `ResourceId` 建索引存到 `/tmp/billing_lookup.json`。Step 3 判定"预计月省"时**只读字典,不发 API**。

❌ [HARD] **Step 3 判定时禁止发 `DescribeBillResourceSummary`**。`baseline` 金额从 `/tmp/billing_lookup.json` 按 ResourceId 读取。若索引缺失(新资源) → 按 `references/pricing-rules.md` §2 的刊例价兜底估算,**不得补发 API**(2026-05-09 实测 4 条绑价补扫 exec 浪费 62s)。

**Step 0.3 + 0.4 合并骨架（一条 exec 完成账单汇总 + 资源级预取 + 落盘）**：

```python
import json, os, sys, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, '{SKILL_ROOT}/scripts')
from sign import call_api

# ---- 凭证 & 月份（0.1/0.2 已在上文 LLM 推断，此处直接使用变量）----
# 推荐：直接调用 prepare_costopt_data.py 一键完成 Step 0.3 + 0.4 + Step 1，
# 它会自动处理凭证（resolve(cloud, project) 从 SSM 取密钥 + GetUserAppId 反查）。
# 若必须手写裸 API 调用（不推荐），凭证读取参考下方（从 SSM，不读裸名 env）：
import build_credential_env as _bce
_cred = _bce.resolve('tencent', '<CODE>')   # 从 SSM 取(KMS 服务端解密)；宿主需配 runner key
sid = _cred['TENCENTCLOUD_SECRET_ID']
sk  = _cred['TENCENTCLOUD_SECRET_KEY']
assert sid and sk, '凭证未就绪，请按 §1.3 友好引导话术提示用户配置 runner key'
bill_month = ...  # 0.2 月份判定结果，格式 "YYYY-MM"
begin_time = f"{bill_month}-01 00:00:00"
# 月末日：用下月1日-1天计算
import calendar
y, m = map(int, bill_month.split('-'))
last_day = calendar.monthrange(y, m)[1]
end_time = f"{bill_month}-{last_day:02d} 23:59:59"

# ---- 0.3 并发拉账单汇总（产品 + 地域）----
def _fetch_by_product():
    return call_api(host='billing.tencentcloudapi.com', service='billing',
                    action='DescribeBillSummaryByProduct', version='2018-07-09',
                    payload={'BeginTime': begin_time, 'EndTime': end_time},
                    region='', secret_id=sid, secret_key=sk)

def _fetch_by_region():
    return call_api(host='billing.tencentcloudapi.com', service='billing',
                    action='DescribeBillSummaryByRegion', version='2018-07-09',
                    payload={'BeginTime': begin_time, 'EndTime': end_time},
                    region='', secret_id=sid, secret_key=sk)

with ThreadPoolExecutor(max_workers=2) as ex:
    f_prod = ex.submit(_fetch_by_product)
    f_reg  = ex.submit(_fetch_by_region)
    prod_resp = f_prod.result()
    reg_resp  = f_reg.result()

products = [i['BusinessCode'] for i in prod_resp.get('SummaryOverview', [])
            if float(i.get('RealTotalCost', 0)) > 0]
region_ids = list({i['RegionId'] for i in reg_resp.get('SummaryOverview', [])
                   if float(i.get('RealTotalCost', 0)) > 0})
total_cost = sum(float(i.get('RealTotalCost', 0))
                 for i in prod_resp.get('SummaryOverview', []))

# ---- 0.4 并发拉资源级账单（按产品码）----
def fetch_bill_resource(product_code):
    offset, limit, out = 0, 100, {}
    while True:
        resp = call_api(host='billing.tencentcloudapi.com', service='billing',
                        action='DescribeBillResourceSummary', version='2018-07-09',
                        payload={'Month': bill_month, 'Offset': offset, 'Limit': limit,
                                 'BusinessCode': product_code, 'NeedRecordNum': 1},
                        region='', secret_id=sid, secret_key=sk)
        items = resp.get('ResourceSummarySet', [])
        for it in items:
            rid = it.get('ResourceId') or ''
            if rid:
                out[rid] = {
                    'cost': float(it.get('RealTotalCost', 0)),
                    'product': product_code,
                    'charge': it.get('PayModeName', ''),
                    'region': it.get('RegionId', ''),
                    'name': it.get('ResourceName', ''),
                }
        if not items or offset + limit >= int(resp.get('Total', 0)):
            break
        offset += limit
    return out

billing_lookup = {}
with ThreadPoolExecutor(max_workers=3) as ex:  # billing API 限速较严，3路
    futs = [ex.submit(fetch_bill_resource, code) for code in products]
    for f in as_completed(futs):
        billing_lookup.update(f.result())

# ---- 落盘（同一条 exec 内完成，不再发额外 exec）----
out_dir = f'/tmp/costopt_{uin}'
os.makedirs(out_dir, exist_ok=True)
with open(f'{out_dir}/billing_lookup.json', 'w') as fp:
    json.dump(billing_lookup, fp, ensure_ascii=False)
with open(f'{out_dir}/bill_summary.json', 'w') as fp:
    json.dump({'products': products, 'region_ids': region_ids,
               'total_cost': total_cost, 'bill_month': bill_month}, fp)
print(json.dumps({'products': len(products), 'regions': len(region_ids),
                  'lookup_entries': len(billing_lookup), 'total_cost': total_cost}))
```

Step 3 绑价查字典用法:

```python
lookup = json.load(open(f'/tmp/costopt_{uin}/billing_lookup.json'))
baseline = lookup.get(resource_id, {}).get('cost') or <刊例价兜底>
saving = baseline * ratio_pct / 100
```

### Step 1：资源发现

读取 `references/api-resource.md`，按 Step 0 的**产品列表 × 地域列表**调用资源 API。

- 地域完全来自账单，不使用任何硬编码列表
- 每次 100 条分页，循环取完
- 权限不足 → 跳过并在报告标注；无资源 → 标注"✅ 无资源"
- **豁免检查**：每个资源检查 `finops-skip=true` 标签，有则跳过判定
- **未注册兜底**：账单出现的产品码在 `references/api-resource.md` 的 `ProductCode` 字段里**找不到** → 归入"其他产品"节,只列账单金额 + 按量标记。**严禁**发独立 exec 补扫;若你判断该产品确实有 API(只是 docs 没注册),**就地 Edit references/api-resource.md 加节 + ProductCode 字段**,由下一次扫描自然覆盖

#### 表驱动扫描（HARD · 强制要求 · 消灭长尾漏扫）

❌ [HARD] **禁止每个产品发独立 exec**。每条 exec 需要 10-18s LLM 来回,11 个产品串行会累计 3 分钟以上。

✅ [HARD] **必须把所有产品的资源发现合并到一条 exec 里**,用 `concurrent.futures.ThreadPoolExecutor` 并发调用。

✅ [HARD · 表驱动] **扫描清单以 `references/api-resource.md` 为唯一权威源**,不得在 SKILL.md 里维护第二份产品硬编码清单。流程如下:
1. 同一条 exec 起始,Read `references/api-resource.md` 整文,用正则解析每一节代码块,抽出 `ProductCode` / `API(host+action)` / `Version` / `ResponseListKey` / `Special` 五个字段,构建 `PRODUCT_REGISTRY[p_code] = {host, service, action, version, list_key, special}`(一个节可能挂多个 p_code,逗号分隔)。
2. Step 0.3 账单产品列表逐个查 registry:命中 → 入任务队列;**未命中 → 不得发独立 exec 补扫**,直接记入 `results['其他产品']` 节,报告里用账单金额展示,判定仅做"按量付费+费用阈值"兜底。
3. 当你发现某个实际账单产品在 docs 里有节却没 `ProductCode` 字段(文档遗漏),**在同一条 exec 里调 Edit 给 docs 补字段后重解析**,不得以"文档不全"为由发独立 exec 绕过。

❌ [HARD · Special 路径不得绕过]:
- `Special: two-stage-targets`(CLB) → 主列表后,**同一 scan 调用内串行** `DescribeTargets(LoadBalancerId=单数!)`,把 `Targets` 挂回每个 LB。禁止 Step 3 再补发(2026-05-09 实测浪费 16s)。
- `Special: tdmq-three-flavors` → 依次调 `DescribeClusters` / `DescribeRocketMQClusters` / `DescribePulsarProInstances`,返回三子类型字典。
- `Special: bill-resource-summary`(COS) → 不走资源 API,直接从 Step 0.4 预取的 `/tmp/billing_lookup.json` 里抽 `product==p_cos` 的 ResourceId 作为 bucket 清单。

❌ [HARD] **exec 必须含实际 API 调用或文件 I/O**。禁止发出"纯 LLM 计算 / join / reshape"的 exec(2026-05-09 实测 2 条纯 join exec 浪费 63s)。合并到有 API 的 exec 尾部直接 `json.dump`。

```python
# 推荐骨架(一条 exec 完成"解析注册表 + 产品 × 地域并发扫描 + 落盘"):
import json, re, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '{SKILL_ROOT}/scripts')
from sign import call_api

sid, sk = ...  # Step 0.1 已从环境变量读取
regions = [...]  # Step 0.3 账单地域列表
products = [...]  # Step 0.3 账单产品列表

# ---- ① 解析 references/api-resource.md → PRODUCT_REGISTRY ----
doc = Path('{SKILL_ROOT}/references/api-resource.md').read_text()
REGISTRY = {}
# 分节:兼容 ##/### 两级标题(VectorDB/TCHouse-D 用 ##,其余 ###)
for section in re.split(r'\n##+ ', '\n' + doc)[1:]:
    title = section.split('\n', 1)[0].strip()
    block_match = re.search(r'```(.*?)```', section, re.S)
    if not block_match: continue
    block = block_match.group(1)
    def _get(key, first=True):
        # 允许 "API:" 或 "API 1:"(TDMQ 等多 API 小节)
        ms = list(re.finditer(rf'^{key}(?:\s*\d+)?:\s*(.+?)$', block, re.M))
        if not ms: return ''
        return ms[0].group(1).split('#')[0].strip()
    codes = [c.strip() for c in _get('ProductCode').split(',') if c.strip()]
    if not codes: continue
    api_line = _get('API')   # 多 API 行只取第一行作为主 action
    host, action = (api_line.split('/', 1) + [''])[:2] if '/' in api_line else (api_line, '')
    service = host.split('.')[0]
    spec = {
        'title': title, 'host': host, 'service': service, 'action': action,
        'version': _get('Version'), 'list_key': _get('ResponseListKey') or 'InstanceSet',
        'special': _get('Special'),
    }
    for code in codes:
        REGISTRY[code] = spec

# ---- ② 通用扫描器 ----
def _paginated(host, service, action, version, payload, region, list_key):
    out, offset, limit = [], 0, 100
    while True:
        resp = call_api(host=host, service=service, action=action, version=version,
                        payload={**payload, 'Offset': offset, 'Limit': limit},
                        region=region, secret_id=sid, secret_key=sk)
        # list_key 可能是嵌套路径,如 "Result.InstanceList"
        node = resp
        for k in list_key.split('.'):
            node = (node or {}).get(k, []) if isinstance(node, dict) else []
        out.extend(node or [])
        if not node or len(node) < limit: break
        offset += limit
    return out

def scan_generic(spec, region):
    return _paginated(spec['host'], spec['service'], spec['action'], spec['version'],
                      {}, region, spec['list_key'])

def scan_clb_two_stage(spec, region):
    lbs = scan_generic(spec, region)
    for lb in lbs:
        resp = call_api(host='clb.tencentcloudapi.com', service='clb',
                        action='DescribeTargets', version=spec['version'],
                        payload={'LoadBalancerId': lb['LoadBalancerId']},
                        region=region, secret_id=sid, secret_key=sk)
        lb['Targets'] = resp.get('Listeners', [])
    return lbs

def scan_tdmq_three(spec, region):
    kw = dict(host='tdmq.tencentcloudapi.com', service='tdmq', version=spec['version'],
              payload={}, region=region, secret_id=sid, secret_key=sk)
    return {
        'pulsar':     call_api(action='DescribeClusters', **kw).get('ClusterSet', []),
        'rocketmq':   call_api(action='DescribeRocketMQClusters', **kw).get('ClusterList', []),
        'pulsar_pro': call_api(action='DescribePulsarProInstances', **kw).get('Instances', []),
    }

DISPATCH = {
    'two-stage-targets':    scan_clb_two_stage,
    'tdmq-three-flavors':   scan_tdmq_three,
    'bill-resource-summary': None,  # COS 由 billing_lookup 驱动,不扫
    '':                     scan_generic,
}

# ---- ③ 任务装配 + 并发执行 ----
tasks, unknown = [], []
for code in products:
    spec = REGISTRY.get(code)
    if not spec:
        unknown.append(code)           # 未注册 → 其他产品兜底,禁补扫 exec
        continue
    if spec['special'] == 'bill-resource-summary':
        continue                        # COS 走账单路径,Step 3 从 billing_lookup 直接消费
    fn = DISPATCH[spec['special']]
    for region in regions:
        tasks.append((spec['title'], region, fn, spec))

results, errors = {}, {}
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(fn, spec, region): (name, region) for name, region, fn, spec in tasks}
    for f in as_completed(futs):
        name, region = futs[f]
        try:
            results.setdefault(name, {})[region] = f.result()
        except Exception as e:
            errors.setdefault(name, {})[region] = str(e)   # UnauthorizedOperation 等都在这

# ---- ④ 落盘 ----
with open('/tmp/resource_discovery.json', 'w') as fp:
    json.dump({'results': results, 'errors': errors, 'unknown_products': unknown}, fp, default=str)
print(json.dumps({
    'scanned': {k: {r: (len(v) if isinstance(v, list) else 'dict') for r, v in rs.items()} for k, rs in results.items()},
    'unknown': unknown, 'error_count': sum(len(v) for v in errors.values())
}, default=str))
```

**新产品上线流程(零 SKILL.md 改动)**:在 `references/api-resource.md` 新增一节 + 填 `ProductCode` / `API` / `Version` / `ResponseListKey` 即可,下次扫描自动覆盖。若该产品有两级 API 或多子类型,**必须同时扩 `DISPATCH` 映射 + `Special` 字段**,否则归为 generic 扫描。

**并发度约束**：

| 层级 | 上限 | 理由 |
|------|-----:|------|
| 产品级(不同 host) | **8** | 腾讯云账户 20 QPS,8 路不同 host 互不挤占 |
| 产品内分页(同 host) | **1** | 单 host 连续请求避免触发 RequestLimitExceeded |
| `billing/DescribeBillDetail` | **≤ 3** | 计费明细 API 历史上最严(见 api-constraints.md) |

**效果预期**:资源发现耗时从串行 ~3 分钟压到 30-50 秒;补扫 exec 归零。

### Step 1.5：advisor 成本巡检（与 Step 1 并行,吸收 cloudq 成本能力）

📌 **本节是 2026-05-21 新增**:把 cloudq skill 的 advisor 22 个 `GroupName="成本"` strategy 能力融合进 costopt;cloudq 不在本次删除,等 inspector(已完成) + costopt(本次) + billing(后续) 三方都收编完成后统一删除。

#### 1.5.1 调用方式

✅ [HARD] **Step 1.5 必须与 Step 1 在同一条 exec 内并发发起**(不再开第二条 exec);Step 1 用 ThreadPoolExecutor 跑资源发现的同时,主线程或额外 1 路线程后台运行 `scripts/scan_advisor.py`。

> ⚠️ **兜底机制(2026-06-01)**:`collect_monitor.py` 启动时若检测到 `advisor_cost.json` 不存在,会自动同步补跑 `scan_advisor.py`(超时 90s)。这是容错兜底,**不是替代并行执行的方案**——兜底会导致 Step 2 额外串行等待 10-15s,而正确并行执行时额外耗时为 0。因此 Step 1.5 仍必须并行。

```python
# Step 1 / Step 1.5 同一条 exec 推荐骨架:
import os, subprocess, threading
os.environ['INSPECTION_UIN'] = uin
os.environ['TENCENTCLOUD_SECRET_ID'] = sid
os.environ['TENCENTCLOUD_SECRET_KEY'] = sk

# 1.5 后台子进程,与 Step 1 ThreadPoolExecutor 并发
adv_proc = subprocess.Popen(
    ['python3', '{SKILL_ROOT}/scripts/scan_advisor.py'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
# ... 主线程跑 Step 1 资源发现 ThreadPoolExecutor ...
adv_stdout, adv_stderr = adv_proc.communicate(timeout=60)  # 预期 10-15s,超时 60s 兜底
adv_summary = json.loads(adv_stdout.decode()) if adv_proc.returncode == 0 else {'scan_status': 'denied'}
```

输出落盘 `/tmp/costopt_{uin}/advisor_cost.json`（**注意:`/` 分隔,不是下划线**;stdout 打的是摘要,完整 hits 只在落盘文件里）。

#### 1.5.2 失败语义(scan_status 三态)

详见 `references/15-advisor-cost-coverage.md §8`。

❌ [HARD] **scan_status=denied 时 Step 2/3 必须照常跑完**;最终报告头部加显式标注"⚠️ advisor 不可用,以下结论仅基于本地监控自判",**严禁静默降级**。

❌ [HARD] **advisor 元数据禁止落盘到任何缓存目录**:每次 scan_advisor.py 现拉 DescribeStrategies(~5s,跨账户零差异慢变数据)。理由:SKILL.md L89 `缓存禁用` HARD 不破例;现拉成本可接受;且元数据偶尔变化(advisor 平台更新策略),不落盘避免拿过期元数据做严重度判定。

#### 1.5.3 字段使用契约

判定规则、严重度映射、product 对齐表、repair_url/monthly_saving 抽取规则,统一在 `references/15-advisor-cost-coverage.md`;**SKILL.md 不重复定义判定逻辑**。

### Step 2：监控数据采集

**在开始采集监控数据前，必须先向用户发送以下提示（原文，不得修改）：**

> ⏳ 正在采集 30 天监控数据，涉及产品较多，预计需要 1-3 分钟，请耐心等待…

#### ✅ [HARD] 必须使用 collect_monitor.py 脚本（禁止自行编写监控采集代码）

`scripts/collect_monitor.py` 已内置：
- advisor 跳过名单（自动读取 `advisor_cost.json`，跳过 advisor 命中的资源）
- 并发采集（ThreadPoolExecutor，8 线程）
- 监控数据压缩（调用 summarize_monitor.py）
- 错误重试与退避

**禁止自行编写 step2_monitor.py 或类似脚本**，直接调用：

```bash
python3 {SKILL_ROOT}/scripts/collect_monitor.py \
  --work-dir /tmp/costopt_primary \
  [--uin <UIN>] [--appid <APPID>]
```

参数说明：
- `--work-dir`：工作目录，需含 `resource_discovery.json` 和 `billing_lookup.json`
- `--uin`（可选）：账户 UIN；不传则按 `bill_summary.json` → `cam.GetUserAppId` 顺序反查
- `--appid`（可选）：APPID（COS/TKE 监控需要，从 COS bucket 名后缀或账单中获取）
- 凭证统一经 `resolve(cloud, project)` 从 SSM 取得（KMS 服务端解密）；宿主只需配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`

输出：`{work-dir}/monitor_summary.json`

#### advisor 跳过机制说明

collect_monitor.py 自动执行以下逻辑（无需人工干预）：
1. 读取 `{work-dir}/advisor_cost.json` 或 `/tmp/costopt_{uin}/advisor_cost.json`
2. 从 `hits[].instance_id` 构建跳过名单
3. `scan_status=denied` 时跳过名单为空集，不跳过任何资源
4. 被跳过的资源在 Step 3 报告里仍要出现（走 advisor 结论路径）

❌ [HARD] **advisor 命中的资源在 Step 3 报告里仍要出现**（走 advisor 结论路径,不是消失），只是跳过本地监控数据采集。

#### 采集参数（已内置于脚本，仅供参考）

- 窗口：统一 30 天（Period=3600）
- CVM 同时采集 CPUUsage 和 MemUsage
- MongoDB 使用 `target`（全小写）作为 Dimensions.Name
- 并发：8 线程，单实例重试 3 次指数退避

### Step 3：判定 & 输出报告

#### ✅ [HARD] 必须使用 judge_costopt.py（禁止自行编写判定脚本）

`scripts/judge_costopt.py` 已内置全产品闲置判定 + F7/F8/F9/F38/F41/advisor 合并全部逻辑：

```bash
python3 {SKILL_ROOT}/scripts/judge_costopt.py \
  --cloud tencent --project <CODE> \   # 与 prepare 一致：决定落盘命名 costopt-{cloud}-{project}-{uin}.html
  --work-dir /tmp/costopt_<CODE> \
  --account-name "账户名" \
  --lang <output_lang>   # zh|en，跟随主理人下发的 [Output language]，缺省 zh；透传给内部 render_costopt_html.render_html
```

> **`--lang` 语义**：`en` 时 HTML 看板的 KPI/findings 表头/风险四档标签/洞察等界面骨架渲染为英文，`zh`（默认）为中文；币种 `¥` 与数据值不随语言变。须与对话/概览语言一致。

> **`--report-dir` 语义（避免 reports/reports 路径重复）**：默认**不传**，脚本自动落盘到 `$WORKBUDDY_WORKSPACE/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html`（传了 `--project` 时；未传则退化 `reports/{uin}/{YYYY-MM-DD}/costopt-{cloud}-uin{uin}.html`）。用稳定 PROJECT 码而非波动昵称，多账号并发不重名。若确需自定义路径，`--report-dir` 传入的是**最终落盘目录本身**（脚本直接 `os.makedirs` 后写入），**禁止**再传 `reports/{uin}/{date}` 这类已含层级的路径。

输入（从 `--work-dir` 读取）：
- `resource_discovery.json` — Step 1 产物
- `billing_lookup.json` — Step 0.4 产物
- `monitor_summary.json` — Step 2 产物
- `advisor_cost.json` — Step 1.5 产物

输出：
- `step3_results.json`（中间数据：findings / protected / exempted / totals）
- **`{WORKSPACE}/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html`**（最终交付物，单文件 HTML 看板，judge_costopt.py 内部已自动调 `render_costopt_html.render_html` 落盘，渲染规范见 `references/html-template.md`）

**LLM 完成以下交付（顺序固定：先概览、后预览）**：
1. **先**在回复框输出 **Markdown 概览**（≤600 字五段式，模板见 `references/html-template.md §六`）
2. 概览输出完毕后，**再**通过 `present_files` 打开 `costopt-{cloud}-{project}-{uin}.html`，确保概览不被预览面板遮挡
3. 调用 `deliver_attachments` 将 HTML 看板注册到产物面板（`deliver_attachments` 不是生成新格式报告，而是注册已有交付物到产物面板，F-DELIVERY-ONLY 不限制此操作）
4. 调用 `open_result_view` 展示 `$WORKBUDDY_WORKSPACE/.workbuddy/artifacts/overview.md`（概览文件必须写入此路径才能在产物面板可见，**不是**工作区根目录）

❌ **禁止自行编写判定脚本**（不得写 step3_judge.py、judge_idle.py 等任何替代脚本）。
❌ **禁止自行渲染 markdown 报告**；HTML 由 `render_costopt_html.py` 脚本统一生成，LLM 不手写 HTML/CSS/Chart.js。

---

> ❌ [HARD] **F-STEP3-FRAG：Step 3 报告渲染禁止碎片 exec（2026-06-01 新增）**
> 通用原则（3.2s 开销、大文件禁止）见本 SKILL.md Pre-Output Check 清单 `F23-silent-execution`。costopt 专属规则：
>
> ✅ **正确做法**：Step 3 只允许发 **1-2 条 exec**：
> 1. **一条 exec 完成全部判定**：加载所有 Step 0/1/2 产物，在同一 Python 进程内完成所有产品的闲置判定、月省计算、结果分类，输出 `step3_results.json` 落盘。
> 2. **LLM 直接读 `step3_results.json` 渲染报告**，不再 exec；报告 markdown 通过文件写入（`open().write()`）。
>
> ❌ **禁止**：
> - 逐产品发独立 exec（每次 `import json; data = json.load(...)` 读一个产品数据）
> - context compacting 后"从头来"重新逐步 exec（Step 3 数据必须预先落盘）
> - 把报告 markdown 正文当 exec 输出内容打印（应写文件）

> ❌ [HARD] **F-STEP3-PREREQ：进入 Step 3 前必须验证所有输入产物文件存在（2026-06-01 新增）**
>
> **在开始写任何判定脚本或报告内容之前**，必须先用一条 exec 验证 Step 0/1/2 的产物文件全部落盘：
> ```python
> import os, sys
> base = f"/tmp/costopt_{uin}/"
> required = ["resource_discovery.json", "billing_lookup.json", "monitor_summary.json", "advisor_cost.json"]
> missing = [f for f in required if not os.path.exists(base + f)]
> if missing:
>     print(f"PREREQ FAIL: 以下产物文件缺失，Step 3 不得开始：{missing}")
>     sys.exit(1)
> print("PREREQ OK: 所有产物文件就绪，可以开始 Step 3 判定")
> ```
>
> ❌ **若 PREREQ FAIL**：
> - 唯一合法动作是回到对应 Step（0/1/2）补跑缺失的采集
> - 禁止跳过缺失文件直接开始判定（会导致大量"待评审"或数据空洞）
> - 禁止在 Step 3 里重新调用 `DescribeBillResourceSummary` 或其他 API 补数据（F-Step 3 禁止发此 API，已有约束）
>
> ❌ **禁止在 Step 3 里读取超过 500KB 的 JSON 文件进 context**（`bill_details.json` / 原始 `billing_lookup.json` 超大时用脚本摘要输出，不得直接 Read/cat）

- **判定规则**：读取 `references/analysis-rules.md`（零章通用原则 + 一至五章产品规则 + 专属策略矩阵 + 第七章 advisor 双源融合规则）
- **advisor 双源合并(2026-05-21 新增)**:读 `/tmp/costopt_{uin}/advisor_cost.json`（⚠️ 路径含 `/` 分隔,不是下划线）,按 `references/15-advisor-cost-coverage.md §6` 和 `analysis-rules.md §7` 合并:
  - **优先级**:advisor 命中 > costopt 自判(advisor 是腾讯云官方权威结论)
  - **去重**:advisor `hits[].instance_id` ∩ costopt 自扫资源 → 报告里只展示一行,来源列写 `advisor+costopt`,严重度取最高
  - **月省金额口径**:实测 advisor 成本策略金额 100% 返回 0;**正确口径是 billing_lookup.json baseline × pricing-rules.md ratio%**;advisor 金额 >0 时才主显并括号附 costopt 计算值。偏差 >20% 且双源都 >0 时才加 ⚠️ 评审提示
  - **来源标注**:内部判定用 `advisor`/`costopt`/`advisor+costopt`,**用户可见报告禁止出现这些字眼**(F34,统一用"云端策略"/"云成本巡检")
  - **LH 独家**:strategy 7302(LH 轻量应用服务器)costopt 未直扫,完全采用 advisor 结论
  - **scan_status=denied**:报告头部必须加显式红框(用户可见文案见 analysis-rules.md §7.5,禁止用 advisor 字眼),严禁静默
  - **advisor 命中资源的规格补全(F36 · HARD)**:advisor hit 只带 instance_id,Step 3 必须主动补规格:
    1. 先查 `resource_discovery.json` 里对应产品的实例列表,按 instance_id 找规格字段(CVM→InstanceType, MySQL→Memory+CPU, Redis→Type+Size, ES→节点规格, MongoDB→规格字符串等)
    2. 次查 `billing_lookup.json`,按 instance_id 取 `name` 字段(含规格时截取)
    3. 两处均无 → 写 `规格待确认`
    4. ❌ [HARD] **严禁写 "-" 或空列**
- **负责人字段填充(F38 · HARD)**：写判定脚本时，每个 finding 的 `owner` 字段必须从 `resource_discovery.json` 中读取：
  ```python
  # 预处理：构建 rid → head tag 的映射
  head_map = {}
  for prod_key, regions in disc['results'].items():
      if not isinstance(regions, dict): continue
      for region, instances in regions.items():
          if not isinstance(instances, list): continue
          for inst in instances:
              rid = inst.get('InstanceId', inst.get('InstanceID', ''))
              tags = inst.get('Tags', [])
              head = next((t.get('tagValue', t.get('Value', '')) for t in tags
                           if t.get('tagKey') == 'head' or t.get('Key') == 'head'), None)
              if rid and head:
                  head_map[rid] = head
  # 写 finding 时：
  finding['owner'] = head_map.get(rid, '-')
  ```
  ❌ [HARD] **有 head tag 的资源不得写 `-`**；`head_map` 必须在 Step 3 判定脚本开头预构建，不得在渲染阶段临时查。
- **多路径判定**：每个产品按专属策略矩阵的多条路径逐一检查，不只是"闲置/不闲置"二元判定
- **绑价(baseline)三级查找，禁止跳级直接标"待评审"**（2026-06-01 修复，根因是 MongoDB/Redis/ES/CFS/NAT/LH 大量被错误标注）：

  ```
  级别1：资源级账单
    billing_lookup.json 按 ResourceId 查 → 命中则直接用
  
  级别2：产品级均摊（资源级查不到时必须走这一步，不得跳过）
    产品总费用 = Step 0.3 的 DescribeBillSummaryByProduct 对应产品 RealTotalCost
    该产品扫到的资源数 = len(resource_discovery[product])
    均摊 baseline = 产品总费用 / 资源数
    → 展示：¥X (=¥产品总费用 均摊，共N个资源)
    → 适用：MongoDB/Redis/ES/CFS/NAT/轻量应用服务器等账单按集群汇总、资源级 ID 对不上的产品
  
  级别3：刊例价估算（级别1/2都没有时）
    按 pricing-rules.md §2 的刊例价公式估算
    → 展示：¥X [基于刊例价估算]，不计入合计
  
  ❌ 禁止级别1查不到就直接标"待评审"——那是跳过了级别2
  ❌ 禁止标注"需评审后确认金额"代替计算金额——有均摊就能算，不能用"待评审"规避
  ```

  **禁止现场调用 `DescribeBillResourceSummary`**，上述三级都从已落盘的产物文件读取（Step 0.4 已全量预取）。
- **风险标注**：每条建议标注风险等级 `[低]` / `[中]` / `[高]`
- **看板渲染**：读取 `references/html-template.md`（字段契约 + 章节顺序 + 视觉规范）。`render_costopt_html.py` 内部已实现全部渲染逻辑，**LLM 不需要手写 HTML / CSS / Chart.js 配置**。
- **渲染前三项汇总**（KPI 四宫格必填；无数据不得留空）：
  - `月度账单总额` = Step 0.3 账单 API 所有产品 `RealTotalCost` 求和，整数展示（F20）
  - `发现优化资源` = 判定后 🔴+🟠+🟡+🟢 四档下所有资源条目计数（不含 🛡 已保护 / 🛡 已豁免）
  - `预计月度节省` = 所有建议的"预计月省"求和，整数展示（F20）；豁免 `downgrade/relax` 的按 `pricing-rules.md` 折算
  - `关键资源已保护` = 主备/只读自动豁免 + `finops-critical` 标签豁免数量之和
- **最终交付物**：仅一份**单文件 HTML 看板** `{WORKSPACE}/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html` + 回复框 Markdown 概览，由 `judge_costopt.py` 内部自动调 `render_costopt_html.render_html` 落盘。
  - ✅ LLM **先**在回复框输出 Markdown 概览（≤600 字，模板见 `html-template.md §六`），**再**通过 present_files 打开 `costopt-{cloud}-{project}-{uin}.html`（概览在前、预览在后，确保概览不被遮挡）
- **发送前**：执行下方 Pre-Output Check 清单作为模型内部自检（不可见于用户）：
  - 遍历所有 HARD 和 MUST 约束逐项 pass/fail
  - 若有 HARD fail → 禁止输出，内部修正后重跑自检（最多 2 次）
  - 若只有 MUST fail → 内部修正后直接输出

❌ [HARD] **执行过程静默（F23）**：从凭证读取到看板生成的整个流程中，**禁止向用户输出任何中间状态**（月份判定、凭证处理、API 进度、Pre-Output Check 结果、rule_id 编号、Step 编号等）。用户输入后，下一条可见输出**必须直接是 Markdown 概览（以 `## 🔍` 开头，先输出）+ HTML 看板预览（概览后再 present_files 打开）**。**例外**：①环境变量中匹配不到时输出 §1.3 友好引导话术；②Step 2 监控采集等待提示。

### 本 Skill Pre-Output Check 清单（最低必检项）

| rule_id | 约束 | 级别 | 来源 |
|---------|------|------|------|
| F7-critical-protect | MySQL 主备/灾备节点、Redis 副本必须跳过闲置判定 | HARD | analysis-rules.md |
| F20-integer-amount | 所有金额展示为整数，不带小数（HTML KPI / 表格统一整数 + 千分号） | HARD | 本 SKILL.md 专属约束 |
| F36-spec-no-dash | 所有资源「规格」列禁止留空或写"-"；advisor 命中资源规格从 resource_discovery.json 或 billing_lookup.json 补全 | HARD | analysis-rules.md |
| F38-head-tag-owner | 负责人列从资源 Tags[head] 取值；有 head tag 的资源不写 `-`。**自检方法**：抽查 3 条 finding，grep `head_map.get(rid)` 是否真正调用；若 HTML 看板中所有负责人列均为 `-` 且账户有 head tag 资源，则判定为 FAIL | HARD | judge_costopt.py §F38 |
| F39-stop-charging-mode | 关机 CVM 区分 STOP_CHARGING/KEEP_CHARGING；STOP_CHARGING 时月省 = 挂载 CBS baseline 之和（CVM 本身 ¥0），**禁止只写 ¥0** | HARD | analysis-rules.md §CVM |
| F41-cbs-list-price | CBS 月省用刊例价兜底（DiskSize × 单价/GB），不写"需评审" | HARD | analysis-rules.md §CBS |
| F42-utilization-value | 利用率类问题（含"利用率低""低利用率""使用率不足"等）必须在问题列写出具体数值（如 CPU 1.2%、内存 7.8%） | HARD | judge_costopt.py |
| F-HTML-COMPLETE | HTML 看板必须全量列出 findings / protected / exempted；禁止 Top N 截断、禁止"详情见 XX"截断话术 | HARD | html-template.md §四 |
| F-MARKDOWN-LIMIT | 回复框 Markdown 概览 ≤600 字，五段式（费用总览 / 风险结构 / 产品洞察 / 关键资源保护 / 建议执行节奏） | HARD | html-template.md §六 |
| F-DELIVERY-ONLY | 最终交付物只有 HTML 看板 + 回复框 Markdown 概览；禁止生成任何其他格式报告。**例外**：`deliver_attachments`（注册已有 HTML 到产物面板）和 `open_result_view`（展示 overview.md）不受此限制——它们是交付机制，不是新格式报告 | HARD | SKILL.md §三 |
| F6-saving-formula | 每条建议附「预计月省」计算说明（baseline × ratio%） | MUST | references/pricing-rules.md |
| F8-coverage-min | 多指标产品覆盖率取最小值 | MUST | analysis-rules.md |
| F9-skip-layered | 豁免标签支持分维度/分产品/带过期 | MUST | analysis-rules.md §0.1 |
| F13-risk-extended | 风险等级带可回滚性 + 执行时机 | MUST | analysis-rules.md |
| risk-level-basic | 每条建议必须标注 [低/中/高] | MUST | 本 SKILL.md 专属约束 |
| no-secret-exposure | 禁止在任何输出（HTML/对话/归档/日志）中展示 SecretKey 明文 | HARD | 本 SKILL.md 凭证约束 |
| F23-silent-execution | 禁止输出中间执行状态、Pre-Output Check 结果、技术调试信息 | HARD | 本 SKILL.md 专属约束 |
| F25-no-degradation-speech | 禁止输出"内容过大已省略""摘要版已覆盖"等降级话术 | HARD | SKILL.md 专属约束 |
| F-ARTIFACTS-PATH | overview.md 必须写入 `$WORKBUDDY_WORKSPACE/.workbuddy/artifacts/overview.md`，而非工作区根目录；HTML 看板路径不变（`reports/` 子目录） | HARD | SKILL.md §三 |

---

## 2.7 多账号并行编排约束（HARD · 主理人必读）

当用户请求分析多个账号（如 ADC / TSP / YX）时，主理人通过 Team/Agent spawn 多个 `cloud-cost-optimizer-pro` 子 agent 并行运行。以下为 **HARD 约束**，违反即视为事故：

1. **谁完成先发谁**：收到任一子 agent 的完成消息后，**立即**将该账号的 AIOps 概览 + HTML 预览展示给用户。❌ 禁止等所有账号都完成再统一汇总——用户不应该面对"全部跑完前的沉默"。
2. **每个账号必须包含三要素**：① AIOps 智能概览（≤600字，五段式，**先输出**） ② HTML 预览（**概览后再 present_files 打开**） ③ **主理人追加的** PPT 导出引导语（语种跟随 `output_lang`）。缺任何一项视为交付不完整。
3. **`deliver_attachments` 是必做步骤**：每份 HTML 看板必须通过 `deliver_attachments` 注册到产物面板，否则用户在产物面板看不到报告。
4. **`overview.md` 必须写入正确路径**：`$WORKBUDDY_WORKSPACE/.workbuddy/artifacts/overview.md`，而非工作区根目录。写入后通过 `open_result_view` 展示。
5. **PPT 引导是主理人的职责（HARD）**：PPT 导出引导语由**主理人**在向用户交付时统一追加，**不是子 agent 的活**。子 agent 只负责交付 HTML 路径 + AIOps 概览。主理人在每次展示完一个账号的概览 + HTML 后，必须追加一句 PPT 引导语：
   - zh：「💡 需要导出 PPT 汇报版？回复「导出 PPT」即可生成 16:9 幻灯片。」
   - en：「💡 Want a presentation-ready PPT? Reply "export PPT" to generate a 16:9 deck.」
6. **不重复 spawn**：每个 `(cloud, project)` 只 spawn 一个子 agent。❌ 禁止对同一账号 spawn 两个 agent。
7. **完成即交付**：子 agent 完成后，主理人的回复就是用户的最终交付。❌ 禁止"我先收着，后面一起补"——概览和 PPT 引导不是可选项，是交付契约的一部分。

---

## 三、降本计划

> 降本计划在对话上下文中完成，不需要文件持久化。

### 3.1 生成降本计划

**触发词**：用户说"生成降本计划"/"降本排期"

**前置条件**：同一对话中已完成闲置巡检（上下文中有巡检结果）

**执行流程**：
1. 基于上下文中的巡检结果，按风险等级分周排期
2. 排期规则见 `references/analysis-rules.md` 第六章
3. 输出降本计划报告 — **直接以 Markdown 写在回复框**（沿用概览风格）

**无巡检结果时**：回复"请先执行闲置资源巡检（发送'扫描闲置'），然后再生成降本计划。"

### 3.2 查看降本计划

**触发词**：用户说"查看降本计划"

在同一对话上下文中回顾已生成的降本计划。

---

## 四、导出报告（预留）

**触发词**：用户说"导出报告"

**当前回复**：
```
完整看板已经通过 HTML 预览给您（深色玻璃拟态 + Chart.js 全量明细）。

如需 CSV/Excel 等其他格式，功能正在开发中，敬请期待。
```

---

## 五、账户配置格式

> 凭证唯一来源详见 **§一**：业务账号 AK/SK 以 KMS 加密存于 SSM，经 `resolve(cloud, project)` 在线读取；宿主只需配 runner key `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY`（路径 2 跨账号代查由目标 secret 内 `role_arn`+`assume_from` 字段驱动，`resolve()` 自动 AssumeRole）。UIN/AppId/账户名由脚本调用 `cam.GetUserAppId` 自动反查。不再支持 `.env` 文件、`env_passthrough`、`ACCOUNT_{ALIAS}_*` 多账号别名映射、裸名 env 兜底。

---

## 六、报告归档（WORKSPACE/reports）

Step 3 生成的 **单文件 HTML 看板** + 中间数据 JSON 都需要落盘到本地归档目录，供 WorkBuddy 客户端预览、对账、人工复查、离线审计。

### 6.1 落盘路径

```
{WORKSPACE}/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html   # 最终交付物：单文件 HTML 看板
{WORKSPACE}/cache/costopt/{uin}/{YYYYMM}/step3_results.json   # 中间数据，可选保留供调试 / 复用
```

- `{WORKSPACE}` = 当前 WorkBuddy 工作区根目录（环境变量 `WORKBUDDY_WORKSPACE`，缺失则回退 `os.getcwd()`）
- `{uin}` = `cam.GetUserAppId` 反查得到的 UIN；为路径稳定优先用 UIN 而非中文账户名
- `{YYYY-MM-DD}` = **落盘当天**的日期（不是扫描的月份），用本地时区
- 同账号同天重复触发 → **覆盖**（与 billing 模式一致）

### 6.2 落盘内容

- `costopt-{cloud}-{project}-{uin}.html` 写入 `render_costopt_html.render_html()` 的**完整 HTML 字符串**（深色玻璃拟态 + Chart.js + 全量明细，体积 50–200KB）
- `step3_results.json` 写入判定中间产物（findings / protected / exempted / totals），供二次分析或调试
- ❌ **禁止**在归档内容里夹带 prompt / 调试日志 / 入参 JSON（持久化禁用延伸要求）
- ❌ **禁止**把 SecretId / SecretKey / UIN 明文以外的敏感字段写入归档

### 6.3 谁来写

- **本 skill 自己写 HTML 文件**（`judge_costopt.py` 内部调 `render_costopt_html.render_html`，落盘到上述路径）
- 在返回的 stdout JSON 里给出 `report_html: <绝对路径>` 字段，LLM 用 preview_url 工具直接预览

### 6.4 失败策略

- 写入异常（权限不足 / 磁盘满 / 目录不存在）→ **软失败**，`continue` 走 stdout 提示 `report_html: ""`
- ❌ **不要**向用户提示"归档失败"，主流程不受影响
- 排查时可人工 `touch $WORKBUDDY_WORKSPACE/reports/.probe` 验证写入权限

---


---

## PPT 导出（按需触发）

### 触发条件

仅当降本报告（HTML 看板 + 概览）已经产出，且用户明确说**"导出 PPT / 转成 PPT / 给老板看的版本 / 汇报材料 / 再来一份 ppt"**等关键词时触发。**默认不生成 PPT**。

### 严禁行为

- ❌ 用户没明确要 PPT 时主动生成
- ❌ PPT 替代 HTML 看板（HTML 是主交付物）
- ❌ 在 PPT 里编造已生成数据外的内容（包括"可节省金额"等关键数字）

### 工作流

复用 Step 3 渲染时生成的 `costopt_render.json`（包含闲置资源分类、可节省金额、风险分级），按 `cloud-report-pptx-pro/SKILL.md` 的 JSON Schema 拼输入：

```python
pptx_input = {
    "report_type": "costopt",
    "title": "云降本优化报告",
    "subtitle": f"基于 {render['bill_period']} 账单 + 30 天监控 · 主账号 {uin}",
    "uin": uin,
    "date": today,
    "summary_md": render['summary_md'],   # 已生成的 ≤600 字概览
    "highlights": [
        {"title": "可节省金额", "value": f"¥{render['save_total']:,.0f}/月",
         "delta": f"{render['save_pct']:.0%}", "note": "按当前账单口径"},
        {"title": "闲置资源数", "value": str(render['idle_count']),
         "delta": f"占总量 {render['idle_ratio']:.0%}", "note": render['top_idle_type']},
        {"title": "高优建议", "value": str(render['p0_count']),
         "delta": "P0 立即处置", "note": render['p0_brief']},
    ],
    "sections": [
        {"heading": "闲置资源分布",
         "chart": {"type":"bar", "categories": render['idle_type_names'],
                   "series": [{"name":"资源数","values": render['idle_type_counts']}]}},
        {"heading": "可节省金额 TOP 资源",
         "table": {"headers":["资源","类型","当前/月","建议","可节省/月"],
                   "rows": render['save_top_rows'][:15]}},
        {"heading": "处置策略分级",
         "table": {"headers":["等级","数量","示例操作","风险提示"],
                   "rows": render['strategy_rows']}},
    ],
    "actions": render['actions_list'],
}
```

调用：
```bash
/usr/bin/python3 ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/skills/cloud-report-pptx-pro/scripts/build_pptx.py \
  --input "$INPUT_PATH" \
  --output "$WORKBUDDY_WORKSPACE/reports/{cloud}/{project}/$INSPECTION_UIN/$(date +%Y-%m-%d)/costopt-{cloud}-{project}-$INSPECTION_UIN.pptx"
```

落盘后通过 `open_result_view` 工具触发预览，回复框追加：

> 📊 降本 PPT 已生成在 `reports/{cloud}/{project}/{uin}/{date}/costopt-{cloud}-{project}-{uin}.pptx`，覆盖闲置分布 + 节省 TOP + 处置策略。HTML 看板和概览仍在原位置。
