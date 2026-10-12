# 审计看板 HTML 渲染规范

> 本文档规定 `render_audit_html.py` 输出的单文件 HTML 审计看板的**渲染契约**:
> 必有章节、字段映射、视觉约束、降噪与全量展示规则。
>
> 模型不需要手写 HTML——所有渲染逻辑都在 `render_audit_html.py` 内实现。
> 本文档的作用是:让维护脚本的人 / 校对看板效果的人 / 排查渲染问题的人,
> 在不读源码的前提下知道"这张看板长什么样、字段从哪来、改动哪里要注意什么"。

---

## 一、最终交付物

```
<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/audit-{cloud}-{project}-{uin}.html
```

- **单文件**:所有数据内联进同一个 `.html`,无外部 CSS/JS/图片依赖
- **唯一外部依赖**:Chart.js,默认通过 jsdelivr CDN 一行 `<script>` 引入;离线场景用 `--offline /path/to/chart.umd.min.js` 把 Chart.js 内嵌
- **可双击打开**:本地 file:// 直接渲染;在 WorkBuddy 客户端通过预览工具直接展示

---

## 二、视觉风格契约(深色玻璃拟态,与 billing 看板同源)

| 用途 | 取色 |
|:------|:------|
| 页面背景 | 径向渐变叠加 `#0b1020` 基底 |
| 卡片背景 | `rgba(255,255,255,0.04)` + `backdrop-filter: blur(8px)` |
| 描边 | `rgba(120,144,200,0.16)` |
| 主前景文字 | `#e7ecff` |
| 次级文字 | `#aab4dc` / 弱化 `#6f7aa8` |
| 严重(critical) | `#ef4f6f`(红) |
| 高(high) | `#f5b455`(橙) |
| 中(medium) | `#5b8cff`(蓝) |
| 正向/成功 | `#36d399`(绿) |

- 报告主标题:22px / 700;KPI 数字:26px / 700;章节标题:15px / 600
- 字体主体 `-apple-system, "PingFang SC", ...`;代码/IP 列用等宽 `"SF Mono", Menlo, monospace`

---

## 三、必有章节(顺序固定)

| 顺序 | 章节 | 渲染元素 | 数据来源 |
|:------|:------|:------|:------|
| 1 | 报告头 | 标题 + 扫描窗口副标题 + UIN + 扫描状态徽标 | `time_window` / `scan_status` |
| 2 | KPI 四宫格 | 事件总量 / 命中类别(N/9) / 真高危事件 / 严重+高 | `total_events` / `total_categories_hit` / 派生 |
| 3 | 高危类别分布环图 | Chart.js doughnut + 可滚动图例(按风险配色) | `events_by_category`(非零类别) |
| 4 | 操作人高危排行 | Chart.js 横向 bar(真高危 Top10) | 真高危事件按 username 聚合 |
| 5 | 9 大类总览矩阵 | 全类别表(含 0 命中) | `events_by_category` + `category_risk` |
| 6 | 真高危事件明细 | 全量表 + 滚动(风险→时间倒序) | 非服务账号事件 |
| 7 | 服务账号聚合 | 聚合表(一账号一行) | `service_account_set` + `summary_by_principal` |
| 8 | 智能安全洞察 | AIOps 卡片 + level 统计条 | `derive_insights()` 派生 |

❌ 禁止省略任何章节;❌ 章节为空时渲染"暂无数据/未命中"占位,不静默丢弃。

---

## 四、降噪与全量展示约束(HARD)

- **真高危事件全量列出**:非服务账号的高危事件不做 Top N 截断,长表 `max-height: 520px; overflow-y: auto` 滚动
- **服务账号聚合降噪**:`service_account_set` 内的账号,其事件**不进**真高危明细,只在"服务账号聚合表"一行展示(总数 + 主要操作 Top3)
- **9 大类矩阵全列**:即使命中数为 0 也要列出该类别(灰色 0),让用户看到"哪些类别没事"
- 表头 `position: sticky; top: 0` 长表滚动时常驻
- ❌ 禁止以"行数过多"删行;只能在视觉层加滚动

---

## 五、Chart.js 引入策略

- **默认 CDN**:`https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js`
- **离线 fallback**:`--offline /path/to/chart.umd.min.js` 内联进 `<script>`(HTML +约 250KB)
- Chart.js 加载失败时,图表区域降级为"暂无数据"占位,表格数据不丢

---

## 六、字段契约(`inspect_audit_events.py` 输出 JSON)

`render_audit_html.py` 的输入 JSON 必须包含以下字段(缺失时按对应策略降级):

### 6.1 顶层字段

| 字段 | 类型 | 用途 | 缺失策略 |
|:------|:------|:------|:------|
| `time_window` | object | 报告头扫描窗口 `{days, start_iso, end_iso}` | 显示 `-` |
| `scan_status` | string | `ok` / `denied`,决定状态徽标 | 默认 `ok` |
| `total_events` | int | KPI 事件总量 | 由明细计数兜底 |
| `total_categories_hit` | int | KPI 命中类别数 | 由非零类别计数兜底 |

### 6.2 事件主体

| 字段 | 类型 | 含义 |
|:------|:------|:------|
| `events_by_category` | object | key=9 大类别名,value=该类事件数组 |
| `events_by_category.*[]` | list[object] | 每条事件含 `event_name / event_name_cn / event_time / username / source_ip / region / error_code / resource_name / resource_type / request_id` |
| `category_risk` | object | key=类别,value=`critical/high/medium`,决定配色与排序 |
| `summary_by_principal` | object | key=username,value=`{total, events:{en:cnt}, is_service}` |
| `service_account_set` | list[string] | 已识别的服务账号 username 列表(降噪聚合依据) |

### 6.3 类别枚举(`CATEGORY_META`,9 类)

`audit_tamper`(审计反取证) / `ak_lifecycle`(AK 生命周期) / `permission_grant`(权限授予) /
`resource_destroy`(资源毁灭) / `kms_tamper`(KMS 主密钥) / `mfa_change`(MFA 变更) /
`user_lifecycle`(用户 CRUD) / `role_change`(角色 CRUD) / `sg_change`(安全组改动)

风险默认映射:`audit_tamper`=critical;`ak_lifecycle/permission_grant/resource_destroy/kms_tamper/mfa_change`=high;`user_lifecycle/role_change/sg_change`=medium。

---

## 七、智能洞察(`derive_insights()`)

脚本内基于扫描结果自动衍生安全洞察,每条字段:`title / desc / level(bad/warn/info/good) / icon / badge / metrics[] / action`。覆盖规则:

1. **扫描权限受限**(scan_status=denied → warn)
2. **审计反取证动作**(audit_tamper 命中 → bad,最高优先)
3. **真高危事件盘点**(严重/高计数分级:≥10 bad / ≥1 warn / 0 good)
4. **操作人高危集中**(单 username 高危 ≥3 → warn)
5. **来源 IP 分散**(不同 IP ≥5 → info)
6. **服务账号已降噪**(说明聚合了多少噪声 → info)

洞察卡片前端按 `bad > warn > info > good` 排序展示。

---

## 八、降级 / 异常渲染

| 场景 | 渲染 |
|:------|:------|
| `scan_status=denied` | 报告头标记"⚠ 扫描受限"徽标 + 洞察卡提示补权限 |
| 真高危事件为 0 | 明细表渲染"✅ 未发现非服务账号的高危操作"占位 |
| 9 大类全无命中 | 环图区渲染"✅ 9 大高危类别均无命中"占位 |
| 无服务账号 | 聚合表渲染"未识别到服务账号"占位 |
| Chart.js 加载失败 | 图表区降级占位,表格数据不丢 |

---

## 九、安全红线(HARD)

- ❌ HTML 看板中**禁止**出现 SecretId / SecretKey 明文(上游已脱敏为 `secret_id_masked`,且本看板不渲染该字段)
- ❌ 报告中禁止提及"环境变量""SecretId""SecretKey"等凭证技术细节
- ❌ 禁止往凭证目录 / `~/` 落盘
- ✅ 落盘路径仅限 `reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/audit-{cloud}-{project}-{uin}.html`

---

## 十、变更指引(给维护者)

1. **新增事件类别**:先改 `inspect_audit_events.py` 的 `HIGH_RISK_CATEGORIES` + `CATEGORY_RISK` → 再在 `render_audit_html.py` 的 `CATEGORY_META` 加映射 → 最后更新本文档"§六类别枚举"
2. **改样式**:只动 `render_audit_html.py` 的 `<style>` 块
3. **新增洞察规则**:在 `derive_insights()` 加分支 + 更新本文档"§七"
4. **CDN 版本升级**:同步改 `render_audit_html.py` 的 `CHART_JS_CDN` 与本文档"§五"
