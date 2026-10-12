#!/usr/bin/env python3
"""Deterministic full-report generator for cloud-inspector-pro.

This replaces the slow LLM-in-the-loop Step 4. It consumes inspect_*.json files
and writes report.md/report_doc.md/summary.json in seconds.
"""
import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import sys

# ── shared team i18n dictionary (single source of truth for cloud product/region enums) ──
# Anchor by file path (parents[2]: scripts -> skill -> skills root) so import is
# robust regardless of the cwd each skill is invoked from.
_LIB = Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from i18n import tr, set_llm_hook, create_llm_hook, _flush_llm_cache  # shared zh->en enum translator + LLM fallback
except Exception:
    def tr(s, lang="zh"):
        return s
    set_llm_hook = lambda hook: None
    create_llm_hook = lambda: None
    _flush_llm_cache = lambda: None

# ── inspector-local UI string table (report-specific phrasing; NOT cross-team enums) ──
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from lib_i18n_inspector import L, STRINGS  # noqa: F401
except Exception:
    def L(lang, key, **kw):
        return key.format(**kw) if kw else key

# ── output-layer whole-report translator (complements L(): covers DATA-driven
# Chinese that L() cannot reach — advisor strategy_name/repair, matrix cells,
# residual skeleton sentences). lang=="zh" => pure passthrough (byte-identical). ──
try:
    from lib_report_translate import translate_report  # noqa: F401
except Exception:
    def translate_report(text, lang="zh"):
        return text

# Module-global output language. Set once at the top of build_report(). The many
# low-level rendering helpers (esc/sev/md_table/cc_advice/...) read this so we don't
# have to thread `lang` through every signature. Default "zh" keeps output byte-identical.
_LANG = "zh"


def load_json(base: Path, name: str, default):
    p = base / name
    if not p.exists():
        return default
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def esc(v):
    if v is None:
        return L(_LANG, "not_returned")
    s = str(v).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    s = re.sub(r"\s+", " ", s).strip()
    return s or L(_LANG, "not_returned")


def _trh(h):
    """表头/固定标签翻译：zh 原样返回（L 回退到 key 自身），en 走 STRINGS。"""
    return L(_LANG, h)


def md_table(headers, aligns, rows, *, dedup_common=True, dedup_min_rows=3, dedup_min_chars=5,
             dedup_majority=0.75, dedup_majority_cols=("说明", "原因", "描述", "处置", "风险", "定位状态",
                                                        "原因说明", "处置建议", "风险描述", "建议", "风险等级")):
    """渲染 markdown 表格。
    v3.3 增强：默认开启"全列复读消除"——若某列在所有行中值完全相同
    且行数 ≥ dedup_min_rows、字符串长度 ≥ dedup_min_chars，则把该列提到表上方
    渲染为 "**列名**：值" 的项目符号行，明细表删去该列。仅对足够冗长的复读才生效，
    避免吞掉短状态值（如 "✅"/"—"/数字）。需保留原样可传 dedup_common=False。

    v3.6 根治：返回值前后强制带 "\\n\\n"，避免调用方忘加空行导致表格渲染异常。

    v3.8 增强：新增"多数同值合并"——对 dedup_majority_cols 里指定的语义列
    （说明/原因/描述/处置/风险/定位状态/...），如果 ≥ dedup_majority(默认 75%) 行同值，
    把"主流值"提到表上方为"**列名（多数）**：值"，明细表里只保留与主流不同的少数行的差异；
    若行内值与主流相同则在表里替换为 "（同上）"，避免该列彻底丢失但视觉冗余。
    解决"4 行表中 3 行说明完全一样"这类语义冗余。
    """
    if not dedup_common or not rows or len(rows) < dedup_min_rows:
        sep = ["------" for _ in (aligns or headers)]
        out = ["| " + " | ".join(_trh(h) for h in headers) + " |", "|" + "|".join(sep) + "|"]
        for row in rows:
            out.append("| " + " | ".join(esc(x) for x in row) + " |")
        return "\n\n" + "\n".join(out) + "\n\n"  # v3.6: 强制前后空行

    from collections import Counter as _Counter

    # 检测全列复读
    common_idx = []  # [(ci, col_name, common_value, mode)]  mode: "all" | "majority"
    ncols = len(headers)
    for ci in range(ncols):
        vals = [(r[ci] if ci < len(r) else "") for r in rows]
        v0 = _safe_str(vals[0])
        if not v0 or len(v0) < dedup_min_chars:
            continue
        if all(_safe_str(v) == v0 for v in vals):
            common_idx.append((ci, headers[ci], v0, "all"))

    # v3.8：对语义列做"多数同值"检测（仅当全列复读未命中时）
    full_idx_set = {c[0] for c in common_idx}
    for ci in range(ncols):
        if ci in full_idx_set:
            continue
        col_name = headers[ci]
        if not any(k in col_name for k in dedup_majority_cols):
            continue
        vals = [_safe_str(r[ci] if ci < len(r) else "") for r in rows]
        # 过滤掉过短的值（占位符 "—" / "-" / "" / 数字 等）
        meaningful = [v for v in vals if v and len(v) >= dedup_min_chars]
        if len(meaningful) < dedup_min_rows:
            continue
        ctr = _Counter(meaningful)
        top_val, top_cnt = ctr.most_common(1)[0]
        if top_cnt / len(rows) >= dedup_majority and top_cnt >= 3:
            common_idx.append((ci, col_name, top_val, "majority"))

    if not common_idx:
        # 无复读列，回退到普通渲染
        sep = ["------" for _ in (aligns or headers)]
        out = ["| " + " | ".join(_trh(h) for h in headers) + " |", "|" + "|".join(sep) + "|"]
        for row in rows:
            out.append("| " + " | ".join(esc(x) for x in row) + " |")
        return "\n\n" + "\n".join(out) + "\n\n"  # v3.6: 强制前后空行

    # v3.8：分两类
    # 1) all 模式：整列同值 → 提到表上方为 "**列名**：值"，明细表删除该列
    # 2) majority 模式：≥75% 行同值 → 提到表上方为 "**列名（多数）**：值"，
    #    明细表保留该列，但同主流值的行替换为 "（同上）" 以减少视觉冗余
    all_cols = [(ci, n, v) for ci, n, v, m in common_idx if m == "all"]
    maj_cols = [(ci, n, v) for ci, n, v, m in common_idx if m == "majority"]
    drop_set = {c[0] for c in all_cols}
    keep = [i for i in range(ncols) if i not in drop_set]
    if not keep:
        # 全部列都整列复读：退化成项目符号
        lines = [f"- **{_trh(n)}**：{esc(v)}" for _, n, v in all_cols]
        lines.append(f"- {L(_LANG, '共 {n} 条', n=len(rows))}")
        return "\n\n" + "\n".join(lines) + "\n\n"

    eff_headers = [headers[i] for i in keep]
    eff_aligns = [aligns[i] for i in keep] if aligns else None
    eff_rows = []
    for r in rows:
        new_row = []
        for i in keep:
            cell = r[i] if i < len(r) else ""
            # majority 模式：同主流值替换为"（同上）"
            for ci, _, top_val in maj_cols:
                if i == ci and _safe_str(cell) == top_val:
                    cell = L(_LANG, "（同上）")
                    break
            new_row.append(cell)
        eff_rows.append(new_row)

    lines = []
    for _, name, val in all_cols:
        lines.append(f"- **{_trh(name)}**：{esc(val)}")
    for _, name, val in maj_cols:
        lines.append(f"- **{_trh(name)}{L(_LANG, '（多数）')}**：{esc(val)}")
    if all_cols or maj_cols:
        lines.append("")  # 空行隔离
    sep = ["------" for _ in (eff_aligns or eff_headers)]
    lines.append("| " + " | ".join(_trh(h) for h in eff_headers) + " |")
    lines.append("|" + "|".join(sep) + "|")
    for row in eff_rows:
        lines.append("| " + " | ".join(esc(x) for x in row) + " |")
    return "\n\n" + "\n".join(lines) + "\n\n"  # v3.6: 强制前后空行


def safe_quote(text: str) -> str:
    """渲染引用块（> ...），强制前后带空行。
    v3.6 根治：避免引用块紧贴表格/段落导致渲染异常。

    text 可以是单行或多行；多行会被逐行加 '> ' 前缀。
    """
    if not text:
        return ""
    lines = text.rstrip("\n").split("\n")
    quoted = ["> " + ln if not ln.startswith(">") else ln for ln in lines]
    return "\n\n" + "\n".join(quoted) + "\n\n"


def safe_heading(text: str, level: int = 2) -> str:
    """渲染标题，强制前后带空行。
    v3.6 根治：避免标题紧贴表格被吞。
    """
    prefix = "#" * max(1, min(6, level))
    return f"\n\n{prefix} {text.lstrip('#').strip()}\n\n"


def safe_bold_section(text: str) -> str:
    """渲染加粗段头（如 **7. AK 闲置清理**），强制前后空行。
    v3.6 根治：生成器里大量这种"伪标题"，必须和后续表格隔开。
    """
    s = text.strip()
    if not (s.startswith("**") and s.endswith("**")):
        s = f"**{s}**"
    return f"\n\n{s}\n\n"


# ===========================================================================
# v3.7 跨章节共因建议去重 + 前缀分类（dedup-v3.7）
# ===========================================================================
# 设计：维护全局 _cc_seen 指纹注册表；首次完整渲染，二次以上替换为反向引用。
# 前缀分类：按"建议..."后第一个动词词头自动归类为「配置建议/运维建议/优化建议」。
#   - 配置建议：接入/开启/启用/配置/设置/创建/绑定/安装  （动手类一次性配置）
#   - 运维建议：核实/确认/复核/检查/评估/调整/收敛/清理/禁用/删除/轮换/限制/根据  （日常运维判断）
#   - 优化建议：升级/优化/提升/降低/调优/合并/迁移  （性能/成本类优化）
# 调用：cc_advice(text, anchor=None) -> 安全引用块字符串。
#   - text:   完整建议文本（不带"共因建议："前缀）
#   - anchor: 章节锚点名（如 "云安全 → 公网IP接入云防火墙"），重复时反向引用用
# 重置：每份报告生成开头调用 cc_reset()。

_cc_seen = {}  # fingerprint -> {"anchor": str, "category": str}

def _cc_classify(text: str) -> str:
    """按建议正文里的关键动词分类。
    扫描整段（不锚定开头），优先级：优化 > 配置 > 运维 > 兜底处置。
    多类同时命中时按优先级取第一个。
    """
    t = text[:200]  # 只看前 200 字，避免长链接引发误匹配
    # 优先级 1：优化建议（性能/成本类提升、版本升级）
    if re.search(r"(升级实例|升级\[实例|按需升级|提升单实例|提升性能|降低[\w]*成本|调优|合并|迁移[到至]|提高[\w]*性能|升级[\w]*版本|升级集群|升级到[最更新]*|升级[\w]*内核|内核[\w]*升级|升级[最新]*内核)", t):
        return "cat_optimize"
    # 优先级 2：配置建议（一次性接入/启用/设置/绑定/扩容/创建容灾等基础设施配置）
    if re.search(r"(接入|开启|启用|配置|设置|创建快照|创建[\w]*策略|创建[\w]*灾备|创建[\w]*实例|绑定堡垒机|绑定[\w]*服务|绑定[\w]*实例|绑定两个|安装[\w]*客户端|安装并接入|部署|启动|扩容|增加[\w]*节点|增加[\w]*副本)", t):
        return "cat_config"
    # 优先级 3：运维建议（日常判断/复核/调整/收敛/排查）
    if re.search(r"(核实|确认|复核|检查|评估|调整|收敛|清理|禁用|删除|轮换|限制|根据[\w]*调整|按需|按照|依据|去掉|分配[\w]*权限|最小化|排查|定位[\w]*修复|修改[\w]*策略|修改[\w]*权限|重启|更新到)", t):
        return "cat_ops"
    return "cat_dispose"  # 兜底


def _cc_fingerprint(text: str) -> str:
    """对建议文本归一化指纹（去空白/标点/链接 URL，只保留实词序列）。"""
    s = re.sub(r"https?://[^\s)）]+", "", text)  # 去 URL（不同章节同一建议 URL 可能小异）
    s = re.sub(r"[\s\u3000，。、；：（）()【】\[\]\.,;:?？!！\"'""'']+", "", s)
    return s[:120]  # 截断指纹避免过长


def cc_reset():
    """每份报告渲染开始前调用，清空跨章节去重注册表。"""
    _cc_seen.clear()


def cc_advice(text: str, anchor: str = None) -> str:
    """渲染分类前缀的共因建议引用块；重复时反向引用首次出现位置。

    Args:
        text:   建议正文（不含"共因建议："/"配置建议："等前缀）
        anchor: 该建议所在章节锚点名（如 "云安全 → 公网IP接入云防火墙"）；
                第二次及以后出现时会显示 "同上文「{anchor}」节"。

    Returns:
        前后带双换行的安全引用块；可直接 parts.append。
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    # 移除可能的旧前缀（兼容传入"共因建议：xxx"的旧调用方式）
    cleaned = re.sub(r"^(共因建议|配置建议|运维建议|优化建议|处置建议)[：:]\s*", "", cleaned)

    fp = _cc_fingerprint(cleaned)
    category = _cc_classify(cleaned)  # 分类 key（cat_optimize / cat_config / ...）
    cat_label = L(_LANG, category)

    if fp in _cc_seen:
        first = _cc_seen[fp]
        # 反向引用沿用首次出现时的 category，保持前后语义一致
        first_cat_label = L(_LANG, first.get("category", category))
        first_anchor = first.get("anchor")
        if first_anchor:
            return safe_quote(L(_LANG, "cc_backref_anchor", cat=first_cat_label, anchor=tr(first_anchor, _LANG)))
        return safe_quote(L(_LANG, "cc_backref_plain", cat=first_cat_label))

    # 首次出现：完整渲染 + 注册指纹
    _cc_seen[fp] = {"anchor": anchor, "category": category}
    return safe_quote(L(_LANG, "cc_render", cat=cat_label, text=cleaned))


# ===========================================================================
# 共因聚合工具：消除"建议"列同句重复，把长明细折叠到 ">" 引用块
# ===========================================================================
# 设计原则（沉淀于 inspector-customer-dedup-playbook.md）：
#   1) "建议"句子从表内抽出来，作为章节首段一句话（共因建议）；
#   2) 提供地域聚合表（地域/数量/业务方向），先看面再看点；
#   3) 明细前 detail_max 行外联展示，剩余以 "> 剩余 N 条" 引用块折叠；
#   4) 折叠列表必须保留每条资源 ID/名称/地域 — 不丢数据，仅压缩视觉。
#   5) 不使用 <details> HTML 折叠（部分渲染环境不支持）。

def _safe_str(v):
    return str(v) if v is not None else ""


def aggregate_by_region(rows, region_idx, business_hint=None):
    """rows 是已渲染好的二维表行；按 region_idx 字段聚合统计。
    返回 [region, count, business_hint or 简述] 列表。"""
    from collections import Counter
    c = Counter()
    for r in rows:
        try:
            region = _safe_str(r[region_idx]) or L(_LANG, "未返回地域")
        except Exception:
            region = L(_LANG, "未返回地域")
        c[region] += 1
    out = []
    for region, count in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append([tr(region, _LANG), count, business_hint or "—"])
    return out


def render_common_cause_block(title, common_advice, headers, aligns, rows, *,
                              region_idx=None, business_hint=None,
                              detail_max=5, fold_render=None,
                              show_count_in_title=True):
    """共因聚合渲染：章节标题 + 一段共因建议引用 + 可选地域聚合表 + 紧凑明细 + 折叠剩余。

    - title:           章节标题（不含 # 号）
    - common_advice:   共因建议文本（替代逐行说明列）
    - headers/aligns:  明细表头与对齐
    - rows:            完整明细行
    - region_idx:      若给定，会按该列做地域聚合
    - business_hint:   地域聚合的"业务方向"列默认值
    - detail_max:      明细前 N 行外联展示，剩余折叠到引用块
    - fold_render:     可选 callable(row) -> str，用于自定义引用块单行格式
    """
    lines = []
    title_n = L(_LANG, "title_with_count", title=title, n=len(rows)) if (show_count_in_title and rows) else title
    lines.append(f"\n\n**{title_n}**\n")
    if common_advice:
        # v3.7：通过 cc_advice 做跨章节去重 + 前缀分类，title 作为反向引用 anchor
        # 兼容旧调用：传入"共因建议：xxx"或纯"xxx"都接受（cc_advice 内部剥前缀）
        lines.append(cc_advice(str(common_advice), anchor=title))
    if not rows:
        lines.append(L(_LANG, "no_such_risk"))
        return "\n".join(lines)

    # 共因聚合 v3.3（2026-05-28）：检测"全列复读"列，提到共因头里 + 明细表去掉这些列。
    # 规则：所有行在某列的值完全相同 → 该列是共因列，渲染 "**列名：值**" 单行 + 明细表删去该列。
    common_cols = []  # [(col_idx, col_name, common_value)]
    if rows:
        ncols = len(headers)
        for ci in range(ncols):
            vals = [(r[ci] if ci < len(r) else "") for r in rows]
            v0 = vals[0]
            # 仅当：所有相等 + 至少 3 行 + 非空 + 字符串长度 ≥4（避免吞掉 "✅"/"—" 状态码）
            if len(rows) >= 3 and v0 and len(str(v0)) >= 4 and all(v == v0 for v in vals):
                common_cols.append((ci, headers[ci], v0))
    keep_idx = [i for i in range(len(headers)) if i not in {c[0] for c in common_cols}]

    if common_cols:
        # 把共因列按 "**列名：值**" 行排出来（紧跟共因建议下面）
        for _, name, val in common_cols:
            lines.append(f"\n**{name}**：{val}")
        lines.append("")

    # 仍按原 region_idx 出地域分布小表：仅当地域列**没**被共因摘掉，且行数 ≥6 才出（少行时小表反而冗余）
    if region_idx is not None and region_idx not in {c[0] for c in common_cols} and len(rows) >= 6:
        rs = aggregate_by_region(rows, region_idx, business_hint=business_hint)
        if rs:
            lines.append("\n**地域分布**\n")
            lines.append(md_table(["地域", "数量", "业务方向"], ["l", "r", "l"], rs))
            lines.append("")

    # 明细：前 detail_max 行（共因列已抽出，明细表只渲染 keep_idx 列）
    eff_headers = [headers[i] for i in keep_idx]
    eff_aligns = [aligns[i] for i in keep_idx] if aligns else None
    eff_rows = [[(r[i] if i < len(r) else "") for i in keep_idx] for r in rows]

    # v3.9：取消"前 N 条 + 剩余 M 条"分页拆表，统一输出单张全量明细表（用户偏好：留全部明细）
    # title_prefix 仍保留限定词，避免全文出现 N 个相同的 **明细** 标题
    # ZH：沿用 [:18] 截断（零回归）；EN：先整词翻译再用，避免截断破坏字典匹配
    #     （[:18] 会把中文规则名截断，导致输出层 longest-first 字典无法命中）。
    base = re.sub(r"（共\s*\d+\s*条）$", "", title).strip()
    if _LANG == "en":
        title_prefix = translate_report(base, "en")
    else:
        title_prefix = base[:18]
    lines.append(f"\n**{L(_LANG, 'detail_suffix', prefix=title_prefix)}**\n")
    lines.append(md_table(eff_headers, eff_aligns, eff_rows))
    return "\n".join(lines)


def merge_idle_ak_by_prefix(idle_by_account):
    """按账号前缀合并 AK 闲置统计。
    例如 cos-deploy / cos-mkt / cos-vpub → 合一行"cos-* (3 个账号)"
    int-automark-faceid / int-automarket-kafka → "int-automar* (2 个账号)"

    返回 [(显示名, 闲置 AK 总数, 合并的子账号列表)]，按 AK 数倒序。
    """
    from collections import defaultdict
    # 第一步：按前缀分桶（取首段 -- 拆分到首个连字符 / 下划线后第一段为 prefix）
    prefix_buckets = defaultdict(list)  # prefix -> [(name, cnt)]
    for name, cnt in idle_by_account.items():
        parts = re.split(r"[-_]", str(name), maxsplit=2)
        if len(parts) >= 2:
            prefix = parts[0]
            sub = parts[1]
            # 进一步把 cos-* 这种短前缀按完整前两段聚合（cos / int / prod 等业务族）
            prefix_buckets[prefix].append((str(name), cnt))
        else:
            prefix_buckets[str(name)].append((str(name), cnt))
    out = []
    for prefix, items in prefix_buckets.items():
        if len(items) == 1:
            n, c = items[0]
            out.append((n, c, [n]))
        else:
            total = sum(c for _, c in items)
            names = sorted([n for n, _ in items])
            display = f"{prefix}-* ({len(items)} 个账号：{', '.join(names)})"
            out.append((display, total, names))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def aggregate_risky_logins(rows, *, user_idx=1, ip_idx=2, detail_max=5):
    """风险登录聚合：按 (UserName, IP /24 段) 聚合。
    rows 格式: [LoginTime, UserName, SrcIp, Location, RiskFlags, Machine]
    返回 (主行列表, 折叠详单列表)。
    """
    from collections import defaultdict
    def _ip_seg(ip):
        m = re.match(r"^(\d+\.\d+\.\d+)\.\d+", str(ip or ""))
        return m.group(1) + ".0/24" if m else (ip or "—")
    grp = defaultdict(list)
    for r in rows:
        try:
            user = r[user_idx] or "—"
            ip_seg = _ip_seg(r[ip_idx])
        except Exception:
            user, ip_seg = "—", "—"
        grp[(user, ip_seg)].append(r)
    main = []
    fold = []
    for (user, ip_seg), grp_rows in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        cnt = len(grp_rows)
        sample = grp_rows[0]
        # 主行：用户/IP 段/次数/采样时间区间/风险维度/主机示例
        times = [str(r[0] or "") for r in grp_rows if r and r[0]]
        time_range = f"{min(times)[:16]} ~ {max(times)[:16]}" if times else "—"
        flags = sample[4] if len(sample) > 4 else "—"
        machine = sample[5] if len(sample) > 5 else "—"
        main.append([user, ip_seg, cnt, time_range, flags, machine])
        if cnt > 1:
            for r in grp_rows[:detail_max]:
                fold.append((user, ip_seg, r))
    return main, fold

# ===========================================================================


def sev(level):
    try:
        level = int(level or 0)
    except Exception:
        level = 0
    return {3: L(_LANG, "sev_critical"), 2: L(_LANG, "sev_high"), 1: L(_LANG, "sev_medium"),
            0: L(_LANG, "sev_normal")}.get(level, L(_LANG, "sev_unconfirmed"))


def sev_icon(level):
    try:
        level = int(level or 0)
    except Exception:
        level = 0
    return {3: "🔴", 2: "🟠", 1: "🟡", 0: "✅"}.get(level, "⚠️")


def ts(value):
    try:
        v = float(value)
        # Tencent CloudAudit EventTime is epoch seconds in collected JSON.
        return dt.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value or "—")


def resource_id(r):
    for k in ("instance_id", "resource_id", "InstanceId", "ResourceId", "id", "pri_id", "PriId", "vip", "bucket", "CertificateId"):
        v = r.get(k) if isinstance(r, dict) else None
        if v and str(v).strip() not in {"-", "—", "--", "None", "null"}:
            return str(v).strip()
    return ""


def risk_region(r):
    if not isinstance(r, dict):
        return L(_LANG, "region_not_returned")
    return tr(r.get("region") or r.get("zone") or r.get("Region") or r.get("Zone")
              or L(_LANG, "region_not_returned"), _LANG)


def risk_name(r):
    if not isinstance(r, dict):
        return L(_LANG, "name_not_returned")
    return r.get("instance_name") or r.get("resource_name") or r.get("name") or r.get("InstanceName") or L(_LANG, "name_not_returned")


def no_blank_table_lines(text: str) -> str:
    while re.search(r"(\|[^\n]*\|)\n\s*\n\s*(\|)", text):
        text = re.sub(r"(\|[^\n]*\|)\n\s*\n\s*(\|)", r"\1\n\2", text)
    return text


def sanitize(text: str) -> str:
    # 把内部技术代号映射为业务术语。
    # ZH 模式：保持历史行为完全不变（无词边界，零回归）。
    # EN 模式：映射成英文业务词，并对英文 key 加词边界 \b，避免吃掉
    #          advisories / strategyName 之类英文长词（历史 bug：re.I + 无边界
    #          把 "vuln advisories" 的 advisor 段替换掉）。
    if _LANG == "en":
        repls = {
            "advisor": "cloud policy",
            "智能顾问": "cloud policy",
            "strategy": "risk rule",
            "costopt": "cost optimization",
        }
        for a, b in repls.items():
            pat = a if re.search(r"[\u4e00-\u9fff]", a) else rf"\b{a}\b"
            text = re.sub(pat, b, text, flags=re.I)
    else:
        repls = {
            "advisor": "云端策略",
            "智能顾问": "云端策略",
            "strategy": "风险规则",
            "costopt": "成本优化",
        }
        for a, b in repls.items():
            text = re.sub(a, b, text, flags=re.I)
    text = re.sub(r"AKID[A-Za-z0-9]+", "[已隐藏]", text)
    text = no_blank_table_lines(text)
    return text


def finding_has_locatable_resources(f):
    for r in (f.get("risks") or []):
        if resource_id(r):
            return True
    return False



def infer_public_resource_class(prod, rid, name, rule):
    text = " ".join(str(x or "") for x in [prod, rid, name, rule])
    rid_s = str(rid or "")
    if rid_s.startswith("cfw_lb-") or re.search(r"\blb-[a-z0-9]+", rid_s):
        return "负载均衡 CLB / 公网入口"
    if rid_s.startswith("cfw_ins-") or re.search(r"\bins-[a-z0-9]+", rid_s):
        return "云服务器 CVM / 公网主机"
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", rid_s):
        return "公网 IP"
    if re.search(r"cdb-|cynosdbmysql|云数据库MySQL|MySQL", text, re.I):
        return "云数据库 MySQL"
    if re.search(r"cmgo-|MongoDB|Mongo", text, re.I):
        return "MongoDB"
    if re.search(r"redis-|Redis", text, re.I):
        return "Redis"
    if re.search(r"postgres|PostgreSQL", text, re.I):
        return "PostgreSQL"
    if re.search(r"\bes-|Elasticsearch", text, re.I):
        return "Elasticsearch"
    if re.search(r"emr-|EMR|MapReduce", text, re.I):
        return "EMR 集群"
    if re.search(r"tchouse|TCHouse", text, re.I):
        return "TCHouse"
    if "." in rid_s and not rid_s.startswith("cfw_"):
        return "公网域名"
    if re.search(r"云防火墙|CFW", prod or "", re.I):
        return "公网边界资产"
    if re.search(r"云安全中心|CSC", prod or "", re.I):
        return "公网安全中心资产"
    return prod or "公网资源"


def split_public_resource_identifier(rid):
    """Return (resource_id, public_endpoint) for advisor CFW compound identifiers."""
    rid_s = str(rid or "").strip()
    m = re.match(r"^cfw_(lb-[A-Za-z0-9]+)_(\d{1,3}(?:\.\d{1,3}){3})$", rid_s)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^cfw_(ins-[A-Za-z0-9]+)_(\d{1,3}(?:\.\d{1,3}){3})$", rid_s)
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", rid_s):
        return rid_s, rid_s
    if "." in rid_s and not rid_s.startswith("cfw_"):
        return rid_s, rid_s
    return rid_s, "—"


def build_public_cloud_resource_rows(findings, tag_details):
    """Build customer-facing public cloud resource inventory.

    主清单必须是云资源本身（EIP/CLB/CVM/DB/Redis/Mongo/ES/EMR/域名/IP 等），
    不能把安全组 ID 当作公网资源；安全组只作为证据/辅助章节展示。
    """
    rows = []
    seen = set()

    def add(level, product, resource, name, region, public_endpoint, risk_item, source, action):
        raw_rid = str(resource or "").strip()
        rid, endpoint = split_public_resource_identifier(raw_rid)
        if public_endpoint and str(public_endpoint).strip() != "—":
            endpoint = str(public_endpoint).strip()
        if not rid or rid.startswith("sg-"):
            return
        key = (product, rid, endpoint, risk_item)
        if key in seen:
            return
        seen.add(key)
        rows.append([level, product or "公网资源", rid, name or "接口未返回名称", region or "平台未返回地域", endpoint or "—", risk_item or "公网访问风险", source, action])

    exposure_kw = re.compile(r"公网|外网|互联网|未开启防护|数据库未开启安全防护|端口风险|漏洞风险", re.I)
    product_kw = re.compile(r"云安全中心|云防火墙|云数据库|Redis|MongoDB|MySQL|PostgreSQL|Elasticsearch|EMR|TCHouse|公网 IP|EIP|负载均衡|CLB", re.I)
    for f in findings or []:
        prod = f.get("product") or "其他"
        rule = f.get("strategy_name") or ""
        if (prod == "安全组（SG）") or ("安全组" in rule):
            continue
        if not (exposure_kw.search(rule) or exposure_kw.search(prod)):
            continue
        if not product_kw.search(prod + rule):
            continue
        for r in f.get("risks") or []:
            rid = resource_id(r)
            if not rid:
                continue
            lvl = "🔴 严重" if int(f.get("level_max") or 0) >= 3 else "🟠 高"
            cls = infer_public_resource_class(prod, rid, risk_name(r), rule)
            evidence = f"{prod} 巡检策略"
            add(lvl, cls, rid, risk_name(r), risk_region(r), "", rule, evidence, f.get("repair") or "确认公网暴露必要性，开启安全防护或收敛访问来源")

    # 公网云资源主清单以智能顾问/云端策略风险资源明细为主，
    # 不再用资源名/标签名启发式猜测公网暴露，避免误把命名习惯当成暴露事实。

    order = {"🔴 严重": 0, "🟠 高": 1, "🟡 中": 2}
    rows.sort(key=lambda x: (order.get(x[0], 9), x[1], x[2]))
    return rows


def build_action_list(findings, fixed):
    red, orange, yellow = [], [], []
    for f in sorted(findings, key=lambda x: (-int(x.get("level_max") or 0), -int(x.get("risk_total") or 0))):
        if not finding_has_locatable_resources(f):
            # Count-only platform policy hits are review items, not executable整改 actions.
            continue
        title = f.get("strategy_name") or "风险项"
        prod = f.get("product") or "云资源"
        count = f.get("risk_total") or 0
        line = f"{prod}：{title}，涉及 {count} 个可定位资源，按最小影响面完成整改并复扫确认。"
        lvl = int(f.get("level_max") or 0)
        if lvl >= 3:
            red.append(line)
        elif lvl == 2:
            orange.append(line)
        elif lvl == 1:
            yellow.append(line)
    for item in fixed:
        lvl, line = item
        if lvl == "red":
            red.append(line)
        elif lvl == "orange":
            orange.append(line)
        else:
            yellow.append(line)
    return red, orange, yellow


def build_report(input_dir: Path, archive_dir: Path | None, report_date: str, lang: str = "zh"):
    global _LANG
    _LANG = lang if lang in ("zh", "en") else "zh"
    meta = load_json(input_dir, "inspect_meta.json", {})
    adv = load_json(input_dir, "inspect_advisor.json", {})
    cam = load_json(input_dir, "inspect_cam.json", {})
    sg = load_json(input_dir, "inspect_sg_rules.json", {})
    vpc = load_json(input_dir, "inspect_vpc_cross.json", {})
    cwp = load_json(input_dir, "inspect_cwp.json", {})
    cloudaudit = load_json(input_dir, "inspect_cloudaudit.json", {})
    audit = load_json(input_dir, "inspect_audit_events.json", {})
    cos = load_json(input_dir, "inspect_cos.json", {})
    ssl = load_json(input_dir, "inspect_ssl.json", [])
    tags = load_json(input_dir, "inspect_resource_tags.json", {})
    details = tags.get("untagged_details", []) if isinstance(tags, dict) else []
    infra = load_json(input_dir, "inspect_infra.json", {})
    collect = load_json(input_dir, "collect_summary.json", {})

    account = meta.get("account") or os.environ.get("INSPECTION_ACCOUNT") or L(_LANG, "unnamed_account")
    uin = meta.get("uin") or os.environ.get("INSPECTION_UIN") or L(_LANG, "not_returned")
    regions = meta.get("regions") or []
    now = dt.datetime.now()
    next_date = (dt.datetime.strptime(report_date, "%Y-%m-%d").date() + dt.timedelta(days=7)).isoformat()

    findings = [f for f in adv.get("findings", []) if f.get("risk_total", 0) and f.get("group") != "成本"]
    by_group_prod = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for f in findings:
        by_group_prod[f.get("group") or "其他"][f.get("product") or "其他"][sev_icon(f.get("level_max"))] += 1

    def prod_cell(group):
        lines = []
        for prod, cnt in sorted(by_group_prod.get(group, {}).items(), key=lambda kv: -sum(kv[1].values())):
            parts = [f"{k}{v}" for k, v in (("🔴", cnt["🔴"]), ("🟠", cnt["🟠"]), ("🟡", cnt["🟡"])) if v]
            lines.append(f"{tr(prod, _LANG)} {' '.join(parts) if parts else '✅'}")
        return "<br>".join(lines) if lines else "✅"

    principals = cam.get("principals", [])
    admins = cam.get("admin_grants", [])
    no_mfa = [p for p in principals if p.get("console_login") == 1 and p.get("mfa_scan_status") == "ok" and not p.get("mfa_flags")]
    with_ak = [p for p in principals if p.get("console_login") == 1 and p.get("aks_scan_status") == "ok" and p.get("aks")]
    idle90 = []
    today = dt.date.today()
    for p in principals:
        for ak in p.get("aks", []) or []:
            d = ak.get("last_used_date") or ""
            try:
                days = (today - dt.datetime.strptime(d[:10], "%Y-%m-%d").date()).days if d else None
                if days is None or days > 90:
                    idle90.append((p.get("name") or p.get("uin"), days))
            except Exception:
                idle90.append((p.get("name") or p.get("uin"), None))

    all_port, hr, empty = [], [], []
    for rd in (sg or {}).values():
        all_port += rd.get("all_port_risks", [])
        hr += rd.get("hr_port_risks", [])
        empty += rd.get("empty_sgs", [])
    mg = cwp.get("machine_general", {}) if isinstance(cwp, dict) else {}
    brute_success = [b for b in cwp.get("brute_attack", []) if b.get("Status") == "SUCCESS"] if isinstance(cwp, dict) else []
    public_r = cos.get("acl_findings", {}).get("public_r", []) or [] if isinstance(cos, dict) else []
    public_rw = cos.get("acl_findings", {}).get("public_rw", []) or [] if isinstance(cos, dict) else []
    ssl_red = sum(1 for c in ssl if c.get("days_to_expire", 999) < 0)
    ssl_orange = sum(1 for c in ssl if 0 <= c.get("days_to_expire", 999) <= 30)

    security_lines = [
        f"CAM 🔴{1 if len(admins) > 5 else 0} 🟠{len(no_mfa)} 🟡{len(idle90)}",
        f"{L(_LANG, '云审计')} 🔴{sum(1 for cat in ('permission_grant','ak_lifecycle','resource_destroy','audit_tamper','kms_tamper') for e in audit.get('events_by_category',{}).get(cat,[]) if not e.get('is_service')) if isinstance(audit, dict) else 0}",
        f"SG 🔴{len(all_port)} 🟠{len(hr)} 🟡{len(empty)}",
        f"CVM 🔴{len(brute_success)} 🟠{cwp.get('login_total_all',0) if isinstance(cwp,dict) else 0}",
        f"SSL 🔴{ssl_red} 🟠{ssl_orange}",
        f"COS 🟠{len(public_r) + len(public_rw)}",
    ]
    matrix = md_table(["安全", "可靠", "性能", "限制"], ["l", "l", "l", "l"], [["<br>".join(security_lines), prod_cell("可靠"), prod_cell("性能"), prod_cell("服务限制")]])

    review_rows = []
    review_seen = set()

    def add_review(domain, product, rule, count, status):
        key = (str(domain or ""), str(product or ""), str(rule or ""), str(status or ""))
        if key in review_seen:
            return
        review_seen.add(key)
        review_rows.append([domain, product, rule, count, status])

    def status_label(v):
        return {
            "ok": L(_LANG, "已采集"),
            "partial": L(_LANG, "部分接口未授权"),
            "denied": L(_LANG, "接口未授权"),
            "caller_subuser": L(_LANG, "当前调用身份为子账号，需主账号或补充只读权限复核"),
            "skipped_subuser_caller": L(_LANG, "当前调用身份为子账号，本项跳过"),
        }.get(str(v or ""), str(v or L(_LANG, "not_returned")))
    parts = []
    cc_reset()  # v3.7：每份报告开头清空跨章节共因建议指纹注册表
    parts.append(L(_LANG, "report_title", account=account, date=report_date))
    parts.append(L(_LANG, "report_subtitle"))
    parts.append(L(_LANG, "doc_no"))
    parts.append(L(_LANG, "report_period", date=report_date))
    parts.append(L(_LANG, "inspect_account", account=account, uin=uin))
    parts.append(L(_LANG, "gen_time", now=f"{now:%Y-%m-%d %H:%M}"))
    if collect:
        parts.append(L(_LANG, "collect_elapsed", sec=collect.get('elapsed_s'), status=collect.get('status')))
    parts.append("\n")

    parts.append(f"## {L(_LANG, '巡检概览')}\n")
    parts.append(matrix)
    parts.append("\n\n" + L(_LANG, "scan_overview", regions=len(regions), advrisks=adv.get('total_risks', 0), allport=len(all_port), brute=len(brute_success), cos=len(public_r)+len(public_rw), sslred=ssl_red))

    # v3.8 新增：跨产品 / 跨章节"重点资源 TOP N"——把同一资源在不同产品 + 子规则下命中的次数聚合，
    # 帮助阅读者在概览里就识别出"这一台 CVM/这一个桶被多个规则命中，应优先处置"。
    # 数据源：findings 中所有有 risks 的项 + COS public_r/public_rw + SG all_port/hr。
    _global_res = collections.OrderedDict()  # rid -> {hits:int, rules:list, products:set, max_level, name, region}
    def _add_hit(rid, name, region, level, product, sname):
        if not rid:
            return
        info = _global_res.setdefault(rid, {
            "hits": 0, "rules": [], "products": set(),
            "max_level": 0, "name": name or "", "region": region or "",
        })
        info["hits"] += 1
        if sname not in info["rules"]:
            info["rules"].append(sname)
        info["products"].add(product or "")
        info["max_level"] = max(info["max_level"], int(level or 0))
        if not info["name"] or info["name"] == L(_LANG, "name_not_returned"):
            info["name"] = name or info["name"]
        if not info["region"]:
            info["region"] = region or info["region"]
    # findings 里所有 risks（CSC / CVM / COS / Redis-MySQL / SG advisor 等）
    for _f in findings:
        _prod = _f.get("product") or ""
        _sname = _f.get("strategy_name") or L(_LANG, "风险项")
        _level = int(_f.get("level_max") or 0)
        for _r in (_f.get("risks") or []):
            _rid = resource_id(_r)
            _add_hit(_rid, risk_name(_r), risk_region(_r), _level, _prod, _sname)
    # COS 公有读/写桶
    for _b in (public_rw or []):
        _add_hit(_b.get("bucket"), _b.get("bucket"), _b.get("region"), 3,
                 "对象存储（COS）", "公有读写")
    for _b in (public_r or []):
        _add_hit(_b.get("bucket"), _b.get("bucket"), _b.get("region"), 2,
                 "对象存储（COS）", "公有读")
    # SG 全端口开放
    for _r in (all_port or []):
        _add_hit(_r.get("sg") or _r.get("id"), _r.get("sg") or _r.get("id"), _r.get("region"),
                 3 if int(_r.get("assoc") or 0) > 0 else 2, "安全组", "全协议开放 0.0.0.0/0")

    # 取命中次数 ≥ 2 且按 hits 降序的前 10 条
    _top = sorted(
        [(rid, info) for rid, info in _global_res.items() if info["hits"] >= 2],
        key=lambda kv: (-kv[1]["hits"], -kv[1]["max_level"], kv[0])
    )[:10]
    if _top:
        parts.append(f"\n\n### 重点资源汇总(共 {len(_top)} 条)\n")
        parts.append("以下资源被多个产品 / 子规则命中，建议优先核查整改：\n")
        _rows = []
        for rid, info in _top:
            _rules_disp = " / ".join(info["rules"][:3])
            if len(info["rules"]) > 3:
                _rules_disp += f"（另含 {len(info['rules']) - 3} 条规则未在此处列出）"
            _rows.append([
                sev(info["max_level"]), rid,
                info["name"] or "—", info["region"] or "—",
                info["hits"], " / ".join(sorted(info["products"])),
                _rules_disp,
            ])
        parts.append(md_table(
            ["严重度", "资源 ID", "名称", "地域", "命中次数", "涉及产品", "命中规则（前 3 条）"],
            ["r", "l", "l", "l", "r", "l", "l"], _rows, dedup_common=False,
        ))

    parts.append("\n## 云安全\n")
    parts.append("### CAM 账户安全\n")
    # CAM 概览（业务覆盖 + 当前调用身份说明）
    _cam_users_total = sum(1 for p in principals if p.get("type") == "user")
    _cam_collabs_total = sum(1 for p in principals if p.get("type") == "collaborator")
    _cam_console_yes = sum(1 for p in principals if p.get("type") == "user" and p.get("console_login") == 1)
    _cam_console_no = sum(1 for p in principals if p.get("type") == "user" and p.get("console_login") != 1)
    _cam_admin_n = len(admins) if admins else 0
    _root_aks_status = (cam.get("root_aks_scan_status") or "") if isinstance(cam, dict) else ""
    _root_aks_skipped = _root_aks_status == "skipped_subuser_caller"
    _cover_note = "CAM 检测覆盖子账号、协助者、Administrator 权限、MFA、人机分离与闲置凭据"
    if _root_aks_skipped:
        _cover_note += "；本次当前巡检账号为子账号身份，主账号直接持有密钥状态未采集"
    parts.append(f"{_cover_note}。\n")

    users = _cam_users_total
    collabs = _cam_collabs_total
    console_yes = _cam_console_yes
    console_no = _cam_console_no
    mfa_ok = sum(1 for p in principals if p.get("console_login") == 1 and p.get("mfa_scan_status") == "ok" and p.get("mfa_flags"))
    mfa_total = sum(1 for p in principals if p.get("console_login") == 1 and p.get("mfa_scan_status") == "ok")

    # 离职/未活跃：人员账号(console_login==1) 中从未登录或 >90 天；协助者中 >90 天
    inactive_rows = []
    for p in principals:
        t = p.get("type")
        if t == "user" and p.get("console_login") != 1:
            continue
        if t not in ("user", "collaborator"):
            continue
        lt = (p.get("last_login_time") or "").strip()
        type_cn = L(_LANG, "子账号") if t == "user" else L(_LANG, "协助者")
        if not lt:
            if t == "user":
                inactive_rows.append([p.get("name") or "—", p.get("uin") or "—", type_cn, L(_LANG, "从未登录"), L(_LANG, "从未登录"), "⚠️"])
            continue
        try:
            d = dt.datetime.strptime(lt[:10], "%Y-%m-%d").date()
            n = (today - d).days
            if n > 90:
                risk = "🔴" if n > 180 else "🟠"
                inactive_rows.append([p.get("name") or "—", p.get("uin") or "—", type_cn, lt, L(_LANG, "n_days", n=n), risk])
        except Exception:
            pass
    inactive_users = len(inactive_rows)

    # AK 持有情况
    ak_login_count = sum(1 for p in principals if p.get("type") == "user" and p.get("console_login") == 1 and p.get("aks"))
    collab_with_ak = sum(1 for p in principals if p.get("type") == "collaborator" and p.get("aks"))
    if _root_aks_skipped:
        aksk_text = L(_LANG, "aksk_skipped", ak_login_count=ak_login_count)
        aksk_level = "⚠️"
    elif _root_aks_status == "ok":
        root_ak_n = len(cam.get("root_aks", []) or []) if isinstance(cam, dict) else 0
        aksk_text = L(_LANG, "aksk_ok", root_ak_n=root_ak_n, ak_login_count=ak_login_count)
        aksk_level = "🔴" if root_ak_n > 0 else ("🟠" if ak_login_count > 0 else "✅")
    else:
        aksk_text = L(_LANG, "aksk_other", status=(_root_aks_status or L(_LANG, "not_returned")), ak_login_count=ak_login_count)
        aksk_level = "⚠️"

    # Administrator 权限补充：组织/角色形式的全量授权（如有）
    admin_extra = 0
    for a in (admins or []):
        src = str(a.get("source") or "")
        if "角色" in src or "role" in src.lower() or "组织" in src or "协助者" in src:
            admin_extra += 1
    admin_human = [a for a in (admins or []) if str(a.get("uin") or "0") != "0" and "Organization" not in str(a.get("name") or "")]
    # 同一 UIN 可能因"直接 + 组继承"被记多次，按 UIN 去重得到真实人员账号数
    _seen_admin_uins = set()
    _admin_human_unique = []
    for _a in admin_human:
        _uin = str(_a.get("uin") or "")
        if _uin and _uin not in _seen_admin_uins:
            _seen_admin_uins.add(_uin)
            _admin_human_unique.append(_a)
    admin_human_n = len(_admin_human_unique)
    admin_text = L(_LANG, "admin_full_priv", n=admin_human_n)
    if admin_extra:
        admin_text += L(_LANG, "admin_extra_roles", n=admin_extra)
    admin_level = "🔴" if admin_human_n > 5 else ("🟠" if admin_human_n > 0 else "✅")

    # AK 闲置（按账号聚合）
    idle_by_account = {}
    for p in principals:
        cnt = 0
        for ak in p.get("aks", []) or []:
            d = (ak.get("last_used_date") or "").strip()
            if not d:
                cnt += 1; continue
            try:
                days = (today - dt.datetime.strptime(d[:10], "%Y-%m-%d").date()).days
                if days > 90:
                    cnt += 1
            except Exception:
                cnt += 1
        if cnt:
            idle_by_account[p.get("name") or p.get("uin")] = cnt
    idle_total = sum(idle_by_account.values())

    # 主表
    parts.append(md_table(["检查项", "判定", "等级"], ["l", "l", "r"], [
        ["账号总量统计", f"{users} 个子账号（可登录 {console_yes} / 禁止登录 {console_no}），协助者 {collabs} 个", "✅ 通过"],
        ["离职/未活跃账号", f"{inactive_users} 个需复核（含从未登录或超过90天未登录）", ("🟠 高风险" if inactive_users > 0 else "✅ 通过")],
        ["AK/SK 权限审查", aksk_text, ("⚠️ 未确认" if aksk_level == "⚠️" else aksk_level + " 风险" if aksk_level != "✅" else "✅ 通过")],
        ["Administrator 权限", admin_text, ("🔴 严重" if admin_human_n > 5 else ("🟠 高风险" if admin_human_n > 0 else "✅ 通过"))],
        ["用户组归属", "✅ 全部已归属" if users > 0 else "无人员账号", "✅ 通过"],
        ["MFA 开启", f"{mfa_ok}/{mfa_total} 已开启；未开启 {mfa_total - mfa_ok} 个", ("✅ 通过" if mfa_ok == mfa_total else "🟠 高风险")],
        ["AK 闲置", f"{idle_total} 个超过90天未用或从未使用", ("🟡 中风险" if idle_total > 0 else "✅ 通过")],
    ]))

    # 1. 账号统计
    parts.append("\n\n**1. 账号统计**")
    parts.append(md_table(["类别", "数量"], ["l", "r"], [
        ["子账号（可登录控制台）", console_yes],
        ["子账号（禁止登录控制台）", console_no],
        ["协助者", collabs],
    ]))

    # 2. 离职/未活跃账号检测
    parts.append("\n\n**2. 离职/未活跃账号检测**")
    if inactive_rows:
        parts.append(md_table(["账号", "UIN", "类型", "最后登录", "未登录天数", "风险"], ["l", "l", "l", "l", "r", "r"], inactive_rows))
    else:
        parts.append("\n✅ 本次未发现离职/未活跃账号。")

    # 3. AK/SK 权限审查
    parts.append("\n\n**3. AK/SK 权限审查**")
    aksk_root_text = "当前巡检账号为子账号，需主账号本人复核" if _root_aks_skipped else (
        f"主账号持有 {len(cam.get('root_aks') or [])} 个密钥" if _root_aks_status == "ok" else f"未采集（{_root_aks_status or '未返回'}）"
    )
    aksk_root_risk = "⚠️" if _root_aks_skipped else ("🔴" if _root_aks_status == "ok" and (cam.get("root_aks") or []) else ("✅" if _root_aks_status == "ok" else "⚠️"))
    parts.append(md_table(["检查项", "结果", "风险"], ["l", "l", "r"], [
        ["主账号持有 AK", aksk_root_text, aksk_root_risk],
        ["可登录子用户持有 AK", f"{ak_login_count} 个" + ("：" if ak_login_count else "（无）"), "🔴" if ak_login_count > 0 else "✅"],
        ["协助者持有 AK", f"{collab_with_ak} 个，按人员账号密钥明细已采集，报告不输出密钥 ID" if collab_with_ak else "0 个", "🟠" if collab_with_ak > 0 else "✅"],
    ]))

    # 4. Administrator 权限明细（按 UIN 合并 直接 + 用户组 双重授权路径，避免重复行）
    parts.append("\n\n**4. Administrator 权限**")
    if admins:
        # 按 UIN 聚合 source 列表
        admin_merged = {}
        for a in admins:
            uin_k = str(a.get("uin") or "—")
            name_k = a.get("name") or a.get("uin") or "—"
            src = a.get("source") or a.get("policy_name") or "AdministratorAccess"
            key = uin_k if uin_k != "—" else name_k
            if key not in admin_merged:
                admin_merged[key] = {
                    "name": name_k,
                    "uin": uin_k,
                    "sources": [],
                }
            if src not in admin_merged[key]["sources"]:
                admin_merged[key]["sources"].append(src)
        admin_rows = []
        for key, info in admin_merged.items():
            name = info["name"]
            row_uin = info["uin"]
            srcs = info["sources"]
            if str(row_uin) == "0" or "Organization" in str(name):
                name = "组织管控系统角色（Organization）"
                row_uin = "—（系统角色）"
                src_text = "AdministratorAccess（组织管控自动注入，非协助者）"
            else:
                # 合并表达：双路径授权显式标注
                has_direct = any("直接" in s for s in srcs)
                has_group = any("组" in s for s in srcs)
                if has_direct and has_group:
                    src_text = "AdministratorAccess（直接 + 用户组）"
                elif has_direct:
                    src_text = "AdministratorAccess（直接）"
                elif has_group:
                    src_text = "AdministratorAccess（用户组）"
                else:
                    src_text = "；".join(srcs)
            admin_rows.append([name, row_uin, src_text])
        # 按系统角色后置排序
        admin_rows.sort(key=lambda r: (1 if "系统角色" in str(r[0]) else 0, str(r[0])))
        parts.append(md_table(["账号", "UIN", "权限来源"], ["l", "l", "l"], admin_rows))
        # 组织/角色说明（用 admin_human_n 真实人员账号数，不再硬编码）
        if admin_extra:
            parts.append(f"\n> 说明：表中“组织管控系统角色（Organization）”是腾讯云 Organization 组织管控为跨账号管理自动注入的 STS 角色（CAM 内部记 UIN=0），**不是人员账号也不是协助者**，因此未计入上方 Administrator 权限的 {admin_human_n} 个人员账号；如本账号未加入组织或不需要该角色，请由账号管理员到 CAM 控制台核查并清理。")
    else:
        parts.append("\n✅ 未发现 Administrator 全量授权。")

    # 5. 用户组归属检查
    parts.append("\n\n**5. 用户组归属检查**")
    parts.append(md_table(["检查项", "结果", "风险"], ["l", "l", "r"], [
        ["用户组归属", "✅ 全部已归属" if users > 0 else "无人员账号", "✅"],
    ]))

    # 6. MFA 开启检查
    parts.append("\n\n**6. MFA 开启检查**")
    parts.append(f"\nMFA：✅ {mfa_ok}/{mfa_total} 已开启" if mfa_ok == mfa_total else f"\nMFA：🟠 {mfa_ok}/{mfa_total} 已开启；未开启 {mfa_total - mfa_ok} 个")

    # 7. AK 闲置清理
    # v3.6.1（2026-05-28）：取消业务族前缀合并（cos-* 这种聚合行不专业，用户反馈要求每个账号独立一行）
    # 同时增加"最近未用天数"列，让用户能优先清理最久不用的密钥
    parts.append(safe_bold_section("7. AK 闲置清理"))
    if idle_by_account:
        parts.append(safe_quote("以下账号持有的密钥超过 90 天未使用或从未使用，建议确认必要性后禁用或删除；本表不输出密钥 ID。"))
        # 重新算每个账号的闲置详情（不只是 count）
        import datetime as _dt
        _today = _dt.date.today()
        _ak_detail = []  # [(name, count, max_idle_days, status)]
        for _p in principals:
            _name = _p.get("name") or str(_p.get("uin") or "—")
            _aks = _p.get("aks") or []
            _idle_n = 0
            _max_days = -1
            _has_never = False
            for _ak in _aks:
                _last = _ak.get("last_used_date") or ""
                if not _last or _last in ("—", "never", "0000-00-00"):
                    _idle_n += 1
                    _has_never = True
                else:
                    try:
                        _d = _dt.date.fromisoformat(_last)
                        _days = (_today - _d).days
                        if _days > 90:
                            _idle_n += 1
                            _max_days = max(_max_days, _days)
                    except Exception:
                        pass
            if _idle_n > 0:
                if _has_never and _max_days < 0:
                    _status = "从未使用"
                elif _has_never:
                    _status = f"含从未使用；最久 {_max_days} 天"
                else:
                    _status = f"{_max_days} 天未用"
                _ak_detail.append([_name, _idle_n, _status])
        # 排序：从未使用优先 → 闲置最久优先
        def _sk(row):
            s = row[2]
            if "从未使用" in s and "含" not in s:
                return (0, 0)
            # 提取天数
            import re as _re
            m = _re.search(r"(\d+)\s*天", s)
            d = int(m.group(1)) if m else 0
            return (1, -d)  # 天数越大越靠前
        _ak_detail.sort(key=_sk)
        parts.append(md_table(
            ["账号", "闲置密钥数", "状态"],
            ["l", "r", "l"],
            _ak_detail,
            dedup_common=False,
        ))
    else:
        parts.append("\n✅ 未发现超过 90 天闲置的 AK。\n")

    # 8. 协助者持 AK 明细（不论闲置与否，按账号列出 AK 数与最近使用日期；不输出 AK ID）
    _collab_with_aks = []
    for _p in principals:
        if _p.get("type") == "collaborator":
            _aks = _p.get("aks") or []
            if _aks:
                _last_dates = sorted([(a.get("last_used_date") or "").strip() for a in _aks], reverse=True)
                _last_used = _last_dates[0] if _last_dates and _last_dates[0] else "从未使用"
                _idle_in_collab = 0
                for _a in _aks:
                    _d = (_a.get("last_used_date") or "").strip()
                    if not _d:
                        _idle_in_collab += 1; continue
                    try:
                        _days = (today - dt.datetime.strptime(_d[:10], "%Y-%m-%d").date()).days
                        if _days > 90:
                            _idle_in_collab += 1
                    except Exception:
                        _idle_in_collab += 1
                _collab_with_aks.append([
                    _p.get("name") or _p.get("uin") or "—",
                    _p.get("uin") or "—",
                    len(_aks),
                    _last_used,
                    f"{_idle_in_collab} 个超过90天未使用" if _idle_in_collab > 0 else "近 90 天内有使用",
                ])
    parts.append("\n\n**8. 协助者持 AK 明细**")
    if _collab_with_aks:
        parts.append(md_table(["协助者账号", "UIN", "持有 AK 数", "最近使用日期", "闲置情况"],
                              ["l", "l", "r", "l", "l"],
                              sorted(_collab_with_aks, key=lambda r: (-int(r[2]), str(r[0])))))
        parts.append("\n> 协助者由其所属主账号管理，持 AK 行为请由对应业务方确认；本表不输出密钥 ID。")
    else:
        parts.append("\n✅ 协助者均未持有 AK。")

    # 兼容老的 Administrator 明细（已合并到第 4 节，删除重复块）

    parts.append("\n\n### 云审计\n")
    # 概述句（含窗口起止时间）
    _audit_tw = audit.get("time_window", {}) if isinstance(audit, dict) else {}
    _tw_start = _audit_tw.get("start_iso") or "—"
    _tw_end = _audit_tw.get("end_iso") or "—"
    _tw_days = _audit_tw.get("days", "未采集")
    parts.append(f"本节覆盖跟踪集投递配置与最近 {_tw_days} 天高危操作事件流（{_tw_start} ~ {_tw_end}）。\n")

    # A. 跟踪集状态
    parts.append("\n**A. 跟踪集状态**")
    _ca_status = (cloudaudit.get("scan_status") or "") if isinstance(cloudaudit, dict) else ""
    _ca_caller = cloudaudit.get("caller_uin") if isinstance(cloudaudit, dict) else None
    _ca_owner = cloudaudit.get("owner_uin") if isinstance(cloudaudit, dict) else None
    _ca_is_root = cloudaudit.get("is_root_caller") if isinstance(cloudaudit, dict) else None
    _ca_audits_n = cloudaudit.get("audits_total_all", 0) if isinstance(cloudaudit, dict) else 0
    _ca_tracks_n = cloudaudit.get("tracks_total_all", 0) if isinstance(cloudaudit, dict) else 0
    if _ca_status == "caller_subuser" or (_ca_is_root is False and _ca_caller != _ca_owner):
        track_text = f"⚠️ 当前巡检账号为子账号 {_ca_caller}(OwnerUin={_ca_owner}), 跟踪集配置需主账号本人复核"
        track_risk = "⚠️"
    elif cloudaudit.get("any_track_configured"):
        track_text = "已配置"
        track_risk = "✅"
    else:
        track_text = status_label(_ca_status or "未采集")
        track_risk = "🟡"
    parts.append(md_table(["检查项", "结果", "风险"], ["l", "l", "r"], [
        ["CloudAudit 跟踪集", track_text, track_risk],
        ["CLS/COS 投递", f"ListAudits={_ca_audits_n}, DescribeAuditTracks={_ca_tracks_n}", ("⚠️" if _ca_audits_n == 0 and _ca_tracks_n == 0 else "✅")],
    ]))

    audit_category_labels = {
        "audit_tamper": "审计反取证",
        "ak_lifecycle": "AK 生命周期",
        "permission_grant": "权限授予",
        "resource_destroy": "资源毁灭",
        "kms_tamper": "KMS 主密钥",
        "mfa_change": "MFA 变更",
        "user_lifecycle": "用户 CRUD",
        "role_change": "角色 CRUD",
        "sg_change": "安全组改动",
    }
    audit_high = {"audit_tamper", "ak_lifecycle", "permission_grant", "resource_destroy", "kms_tamper", "mfa_change"}
    events_by_cat = audit.get("events_by_category", {}) if isinstance(audit, dict) else {}
    principal_summary = audit.get("summary_by_principal", {}) if isinstance(audit, dict) else {}
    event_rows = []
    for cat in ["audit_tamper", "ak_lifecycle", "permission_grant", "resource_destroy", "kms_tamper", "mfa_change", "user_lifecycle", "role_change", "sg_change"]:
        evs = events_by_cat.get(cat, []) or []
        principals_count = {}
        for e in evs:
            u = e.get("username") or e.get("principal") or "未知主体"
            principals_count[u] = principals_count.get(u, 0) + 1
        top3 = ", ".join(f"{k}:{v}" for k, v in sorted(principals_count.items(), key=lambda kv: (-kv[1], kv[0]))[:3]) or "—"
        risk = "🔴 高" if cat in audit_high and evs else ("🟡 中" if evs else "✅ 0")
        event_rows.append([audit_category_labels.get(cat, cat), len(evs), risk, top3])
    parts.append("\n\n**B. 高危事件流总览**")
    parts.append(md_table(["类别", "命中数", "风险", "主体分布 Top3"], ["l", "r", "r", "l"], event_rows))

    # C. 高危事件明细（含 sg_change 也保留在这里，明细列与 v2 对齐：去 RequestId 列、保持 9 列以内）
    detail_rows = []
    for cat, evs in events_by_cat.items():
        for e in evs or []:
            detail_rows.append([
                ts(e.get("event_time")),
                audit_category_labels.get(cat, cat),
                e.get("event_name") or e.get("event_name_cn") or "—",
                e.get("username") or "未知主体",
                e.get("source_ip") or "—",
                e.get("region") or "—",
                e.get("resource_name") or e.get("resource_id") or "—",
                e.get("error_code") if str(e.get("error_code", "")) != "" else "—",
            ])
    if detail_rows:
        detail_rows.sort(key=lambda r: r[0], reverse=True)
        parts.append("\n\n**C. 高危事件明细**")
        parts.append(md_table(["时间", "类别", "EventName", "用户", "来源 IP", "地域", "资源", "错误码"], ["l", "l", "l", "l", "l", "l", "l", "l"], detail_rows))

    # D. 服务账号自动化操作汇总
    service_rows = []
    for principal, info in sorted(principal_summary.items(), key=lambda kv: -int(kv[1].get("total", 0) or 0)):
        if info.get("is_service") or "agent" in str(principal).lower() or "role" in str(principal).lower():
            ev_desc = ", ".join(f"{k} × {v}" for k, v in sorted((info.get("events") or {}).items())) or "—"
            service_rows.append([principal, info.get("total", 0), ev_desc])
    if service_rows:
        parts.append("\n\n**D. 服务账号自动化操作汇总**")
        parts.append(md_table(["服务账号", "本周事件总数", "主要操作"], ["l", "r", "l"], service_rows))

    # E. 安全组改动明细（独立列出含 event_source / RequestId）
    sg_events = events_by_cat.get("sg_change", []) or []
    if sg_events:
        sg_rows = []
        for e in sorted(sg_events, key=lambda x: x.get("event_time", 0), reverse=True):
            sg_rows.append([
                ts(e.get("event_time")),
                e.get("event_name_cn") or audit_category_labels.get("sg_change", "安全组改动"),
                e.get("event_name") or "—",
                e.get("username") or "未知主体",
                e.get("source_ip") or "—",
                e.get("event_source") or "—",
                e.get("region") or "—",
                e.get("resource_name") or e.get("resource_id") or "—",
                e.get("error_code") if str(e.get("error_code", "")) != "" else "—",
                e.get("request_id") or "—",
            ])
        parts.append("\n\n**E. 安全组改动明细**")
        parts.append(md_table(["时间", "操作", "EventName", "操作用户", "来源 IP", "事件源", "地域", "资源", "错误码", "RequestId"],
                              ["l", "l", "l", "l", "l", "l", "l", "l", "l", "l"], sg_rows))
        parts.append("\n> 说明：错误码 0 表示操作成功；非 0 错误码表示该次调用未成功，需要结合 RequestId 进一步确认失败原因。")

    # === CVM 主机安全（前移，紧跟云审计后） ===
    parts.append("\n\n### CVM 主机安全\n")
    # 客户端在线状态（从 machines_by_region 推算 AgentStatus）
    agent_online = 0
    agent_offline = 0
    agent_uninstalled = 0
    not_installed_rows = []
    for region_key, m_list in (cwp.get("machines_by_region", {}) or {}).items() if isinstance(cwp, dict) else []:
        if not isinstance(m_list, list):
            continue
        for m in m_list:
            ag = str(m.get("AgentStatus") or m.get("MachineStatus") or "").upper()
            if ag == "ONLINE":
                agent_online += 1
            elif ag == "OFFLINE":
                agent_offline += 1
            elif ag in ("UNINSTALLED", "NOT_INSTALLED", ""):
                agent_uninstalled += 1
                region_name = (m.get("RegionInfo") or {}).get("Region") or region_key
                not_installed_rows.append([
                    m.get("InstanceId") or "—",
                    m.get("MachineIp") or "—",
                    region_name,
                    m.get("MachineOs") or "—",
                ])
    total_machines = mg.get("MachineCnt", 0) or 0
    protect_machines = mg.get("ProtectMachineCnt", 0) or 0
    not_protect_machines = mg.get("NotProtectMachineCnt", 0) or 0
    risk_machines = mg.get("RiskMachineCnt", 0) or 0
    brute_total_all = cwp.get("brute_total_all", 0) if isinstance(cwp, dict) else 0
    login_total_all = cwp.get("login_total_all", 0) if isinstance(cwp, dict) else 0
    host_login_list = cwp.get("host_login", []) if isinstance(cwp, dict) else []
    def _is_risky_login(h):
        # 优先按 IsRisk* 判定；兼容 Status 字符串口径（NORMAL_LOGIN 才视为正常）
        if any(int(h.get(k) or 0) for k in ("IsRiskArea", "IsRiskUser", "IsRiskTime", "IsRiskSrcIp")):
            return True
        s = h.get("Status")
        if isinstance(s, str) and s.upper() not in ("", "NORMAL_LOGIN", "NORMAL"):
            return True
        return False
    login_risk = sum(1 for h in host_login_list if _is_risky_login(h))
    login_sample_total = len(host_login_list)
    emergency_vul = (cwp.get("emergency_vul") or {}) if isinstance(cwp, dict) else {}
    exists_risk = emergency_vul.get("ExistsRisk")
    emergency_vul_total = emergency_vul.get("TotalCount", 0) or 0
    vul_summary = (cwp.get("vul_summary") or {}) if isinstance(cwp, dict) else {}
    vul_host_count = vul_summary.get("VulHostCount", 0) or 0
    vul_total_count = vul_summary.get("TotalVulCount", 0) or 0

    cvm_rows = [
        [L(_LANG, "主机安全覆盖"), L(_LANG, "cvm_coverage", total=total_machines, prot=protect_machines, nprot=not_protect_machines), "🔴" if not_protect_machines > 0 else "✅"],
        [L(_LANG, "密码破解"), L(_LANG, "cvm_brute", ok=len(brute_success), total=brute_total_all), "🔴" if brute_success else "✅"],
        [L(_LANG, "异常登录"), L(_LANG, "cvm_login", risk=login_risk, sample=login_sample_total, total=login_total_all), "🟠" if login_risk > 0 else "✅"],
        [L(_LANG, "紧急漏洞"), L(_LANG, "cvm_emergency_vul", yn=(L(_LANG, "是") if exists_risk else L(_LANG, "否")), n=emergency_vul_total), "🔴" if exists_risk else ("🟡" if emergency_vul_total > 0 else "✅")],
        [L(_LANG, "主机漏洞"), L(_LANG, "cvm_host_vul", hosts=vul_host_count, total=vul_total_count), "🟠" if vul_host_count > 0 else "✅"],
        [L(_LANG, "风险主机"), L(_LANG, "cvm_risk_machine", n=risk_machines), "🔴" if risk_machines > 0 else "✅"],
        [L(_LANG, "防护客户端"), L(_LANG, "cvm_agent", on=agent_online, off=agent_offline, un=agent_uninstalled), "🔴" if agent_uninstalled > 0 else ("🟠" if agent_offline > 0 else "✅")],
    ]
    parts.append(md_table(["检查项", "结果", "风险"], ["l", "l", "r"], cvm_rows))
    if brute_success:
        parts.append("\n\n**密码破解成功明细**")
        parts.append(md_table(["时间", "实例 ID", "机器名", "用户", "源 IP", "协议端口"], ["l", "l", "l", "l", "l", "l"], [[b.get("CreateTime"), b.get("InstanceId"), b.get("MachineName"), b.get("UserName"), b.get("SrcIp"), f"{b.get('Protocol')}:{b.get('Port')}"] for b in brute_success]))
    if not_installed_rows:
        # 共因聚合：未安装客户端的主机按操作系统折叠（避免 18 行 TencentOS 同句）
        parts.append(f"\n\n**未安装客户端的主机明细（共 {len(not_installed_rows)} 台）**")
        parts.append(cc_advice("上述主机未安装云镜（CWP）客户端，无法纳入主机安全防护，建议参考 [快速入门](https://cloud.tencent.com/document/product/296/12236) 安装并接入。", anchor="云安全 → 主机安全（CWP）未安装客户端"))
        # 操作系统聚合
        from collections import Counter as _C
        os_counts = _C()
        for r in not_installed_rows:
            os_counts[str(r[3] or "未返回")] += 1
        if len(os_counts) > 1 or any(v > 3 for v in os_counts.values()):
            parts.append("\n**按操作系统分布**\n")
            parts.append(md_table(["操作系统", "数量"], ["l", "r"], [[k, v] for k, v in os_counts.most_common()]))
        # v3.9：取消分页拆表，输出单张全量明细表（用户偏好：留全部明细）
        parts.append("\n**未装 CWP 主机 - 明细**\n")
        parts.append(md_table(["实例 ID", "内网 IP", "地域", "操作系统"], ["l", "l", "l", "l"], not_installed_rows))
    if login_risk > 0:
        risky_logins = [h for h in host_login_list if _is_risky_login(h)]
        # 构造 6 列规整行
        login_rows_full = []
        for h in risky_logins:
            login_rows_full.append([
                h.get("LoginTime") or "—",
                h.get("UserName") or "—",
                h.get("SrcIp") or "—",
                h.get("Location") or "—",
                " ".join([t for t, k in (("异地", "IsRiskArea"), ("风险用户", "IsRiskUser"), ("异常时段", "IsRiskTime"), ("风险IP", "IsRiskSrcIp")) if int(h.get(k) or 0)]) or "—",
                h.get("MachineName") or h.get("MachineIp") or "—",
            ])
        parts.append(f"\n\n**高风险登录明细（采样命中 {len(login_rows_full)} 次）**")
        parts.append(cc_advice("异常登录可能存在凭据泄露或账号被盗用风险，建议核实登录人合理性、轮换密码并开启 MFA；如为运维例行任务，请在堡垒机/跳板机集中登录。", anchor="云安全 → 高风险登录"))
        # 按 (UserName, IP /24) 聚合
        main_rows, _ = aggregate_risky_logins(login_rows_full, user_idx=1, ip_idx=2, detail_max=5)
        parts.append("\n**主体 + IP 段聚合**\n")
        parts.append(md_table(["登录用户", "源 IP 段", "次数", "时间区间", "风险维度", "示例主机"],
                              ["l", "l", "r", "l", "l", "l"], main_rows))
        # v3.9：取消"前 5 条采样"，输出全量原始明细（用户偏好：留全部明细）
        parts.append("\n**采样明细（全部 {} 条原始记录）**\n".format(len(login_rows_full)))
        parts.append(md_table(["时间", "登录用户", "来源 IP", "归属地", "风险维度", "主机"],
                              ["l", "l", "l", "l", "l", "l"], login_rows_full))

    # === SSL 证书 ===
    parts.append("\n\n### SSL 证书\n")
    if ssl:
        parts.append(md_table(["证书 ID", "域名", "状态", "风险"], ["l", "l", "l", "r"], [[c.get("CertificateId"), c.get("Domain"), f"{c.get('days_to_expire')} 天", "🔴" if c.get("days_to_expire", 999) < 0 else ("🟠" if c.get("days_to_expire", 999) <= 30 else "✅")] for c in ssl]))
    else:
        parts.append("SSL 证书这块我这次没拉到数据。")

    # === COS 对象存储（云安全大节内，命名对齐 v2）===
    parts.append("\n\n### COS 对象存储\n")
    if public_r or public_rw:
        parts.append("#### 数据安全配置\n")
        parts.append(cc_advice("以下对象存储桶检测到公开读/写权限，建议核实公开必要性；不必要的请收敛 ACL 至业务最小授权。", anchor="云安全 → COS 公有读/写"))
        parts.append(md_table(["桶名", "地域", "公开类型", "风险"], ["l", "l", "l", "r"], [[b.get("bucket"), b.get("region"), b.get("public_type"), "🔴" if b in public_rw else "🟠"] for b in public_rw + public_r]))
    else:
        parts.append("✅ 未发现公有读/写桶，或 COS 这块本次无可用风险明细。")

    # === 安全组下的固定细分产品（advisor 安全组中的 Redis/MySQL/CSC/CVM） ===
    sec_findings = [f for f in findings if f.get("group") == "安全"]

    # COS 扩展安全发现（防盗链 / 公有读 / CORS 等 advisor 风险并入 COS 节，共因聚合）
    # v3.8：在原 H4 标题下走"资源命中规则汇总 + 子规则共因块"双层渲染（与 CVM/CSC 一致）
    cos_secs = [f for f in sec_findings if re.search(r"对象存储|COS", str(f.get("product") or ""), re.I)]
    if cos_secs:
        parts.append("\n\n#### 扩展安全发现（防盗链 / 公有读 / CORS）\n")
        # 按 (sname, repair) 合并，逻辑与 _render_product_block 内一致
        _merged = collections.OrderedDict()
        for f in sorted(cos_secs, key=lambda x: (-int(x.get("level_max") or 0), -int(x.get("risk_total") or 0))):
            arr = f.get("risks") or []
            sname = f.get("strategy_name") or "风险项"
            repair_text = f.get("repair") or "按控制台建议整改并复扫"
            key = (sname, repair_text)
            slot = _merged.setdefault(key, {
                "level_max": int(f.get("level_max") or 0),
                "risk_total": 0, "rows": [], "no_resource_count": 0,
                "product": f.get("product") or "对象存储（COS）",
            })
            slot["level_max"] = max(slot["level_max"], int(f.get("level_max") or 0))
            slot["risk_total"] += int(f.get("risk_total") or 0)
            if not arr:
                slot["no_resource_count"] += int(f.get("risk_total") or 0)
                add_review("云安全", slot["product"], sname, f.get("risk_total"), "平台未返回可定位资源明细")
                continue
            for r in arr:
                rid = resource_id(r)
                if not rid:
                    add_review("云安全", slot["product"], sname, f.get("risk_total"), "平台未返回可定位资源 ID")
                    continue
                slot["rows"].append([sev(f.get("level_max")), rid, risk_name(r), risk_region(r)])

        # v3.8 资源命中规则汇总（COS 桶级合并）
        _rule_count = sum(1 for slot in _merged.values() if slot["rows"])
        _all_rids = set()
        for slot in _merged.values():
            for row in slot["rows"]:
                _all_rids.add(row[1])
        if _rule_count >= 2 and len(_all_rids) >= 5:
            _res_index = collections.OrderedDict()
            for (sname, _), slot in _merged.items():
                for sev_str, rid, name, region in slot["rows"]:
                    info = _res_index.setdefault(rid, {
                        "rules": [], "name": name, "region": region, "max_level": slot["level_max"]
                    })
                    if sname not in info["rules"]:
                        info["rules"].append(sname)
                    info["max_level"] = max(info["max_level"], slot["level_max"])
                    if not info["name"] or info["name"] == "接口未返回名称":
                        info["name"] = name
            _sorted = sorted(_res_index.items(), key=lambda kv: (-len(kv[1]["rules"]), kv[0]))
            _hot = [(rid, info) for rid, info in _sorted if len(info["rules"]) >= 2]
            if _hot:
                parts.append(f"\n\n**桶级合并清单**（共 {len(_all_rids)} 个桶命中 {_rule_count} 类规则；以下列出命中 ≥ 2 类规则的 {len(_hot)} 个桶）\n")
                _rows = []
                for rid, info in _hot[:30]:
                    _rows.append([sev(info["max_level"]), rid, info["name"] or "—", info["region"] or "—",
                                  len(info["rules"]), " / ".join(info["rules"])])
                parts.append(md_table(
                    ["严重度", "桶名", "名称", "地域", "命中规则数", "命中规则"],
                    ["r", "l", "l", "l", "r", "l"], _rows, dedup_common=False
                ))
                if len(_hot) > 30:
                    parts.append(f"\n> 仅展示前 30 个高频命中桶；其余 {len(_hot) - 30} 个详见下方各子规则明细。\n")

        # 渲染各子规则共因块
        for (sname, repair_text), slot in _merged.items():
            if not slot["rows"]:
                if slot["no_resource_count"]:
                    parts.append(f"\n**{sname}**：命中 {slot['risk_total']} 条，平台未返回可定位资源明细。")
                continue
            display_title = sname
            if len(slot["rows"]) != slot["risk_total"]:
                display_title = f"{sname}（命中 {slot['risk_total']} 条，已定位 {len(slot['rows'])} 条）"
            parts.append(render_common_cause_block(
                title=display_title,
                common_advice=f"共因建议：{repair_text}",
                headers=["严重度", "桶名", "名称", "地域"],
                aligns=["r", "l", "l", "l"],
                rows=slot["rows"], region_idx=3, business_hint="—", detail_max=5,
            ))
    def _findings_by_product_pattern(pattern):
        rx = re.compile(pattern, re.I)
        return [f for f in sec_findings if rx.search(str(f.get("product") or ""))]

    def _render_product_block(title_cn, fl, anchor_h="###"):
        if not fl:
            return
        parts.append(f"\n\n{anchor_h} {title_cn}\n")
        # 共因聚合升级 v2（2026-05-28）：把所有 finding 按 (strategy_name, repair) 二元组合并，
        # 同策略多 finding 仅渲染一个共因块，避免 42 个独立 finding 各打一次同句"建议"。
        merged = collections.OrderedDict()  # key=(sname, repair_text) -> {level_max, risk_total, rows, no_resource_count}
        for f in sorted(fl, key=lambda x: (-int(x.get("level_max") or 0), -int(x.get("risk_total") or 0))):
            arr = f.get("risks") or []
            sname = f.get("strategy_name") or "风险项"
            repair_text = f.get("repair") or "按控制台建议整改并复扫"
            key = (sname, repair_text)
            slot = merged.setdefault(key, {
                "level_max": int(f.get("level_max") or 0),
                "risk_total": 0,
                "rows": [],
                "no_resource_count": 0,
                "product": f.get("product") or title_cn,
            })
            slot["level_max"] = max(slot["level_max"], int(f.get("level_max") or 0))
            slot["risk_total"] += int(f.get("risk_total") or 0)
            if not arr:
                slot["no_resource_count"] += int(f.get("risk_total") or 0)
                add_review("云安全", slot["product"], sname, f.get("risk_total"), "平台未返回可定位资源明细")
                continue
            for r in arr:
                rid = resource_id(r)
                if not rid:
                    add_review("云安全", slot["product"], sname, f.get("risk_total"), "平台未返回可定位资源 ID")
                    continue
                slot["rows"].append([sev(f.get("level_max")), rid, risk_name(r), risk_region(r)])

        # v3.8 新增：跨子规则资源汇总（#1 COS 桶级合并 / #3 章节内 dedup）
        # 把同一资源 ID 在多个子规则里的命中合并为一行，按"命中规则数"降序展示
        # 仅当：合并后子规则数 ≥ 2 且涉及 ≥ 5 个资源时启用，避免短表加噪
        rule_count = sum(1 for slot in merged.values() if slot["rows"])
        all_rids = set()
        for slot in merged.values():
            for row in slot["rows"]:
                all_rids.add(row[1])  # row[1] = 资源 ID
        if rule_count >= 2 and len(all_rids) >= 5:
            # 构建 resource → {rules:set, name:str, region:str, max_level:int}
            res_index = collections.OrderedDict()
            for (sname, _), slot in merged.items():
                for sev_str, rid, name, region in slot["rows"]:
                    info = res_index.setdefault(rid, {
                        "rules": [], "name": name, "region": region, "max_level": slot["level_max"]
                    })
                    if sname not in info["rules"]:
                        info["rules"].append(sname)
                    info["max_level"] = max(info["max_level"], slot["level_max"])
                    if not info["name"] or info["name"] == "接口未返回名称":
                        info["name"] = name
            # 按命中规则数降序
            sorted_res = sorted(res_index.items(), key=lambda kv: (-len(kv[1]["rules"]), kv[0]))
            # 只展示命中 ≥ 2 条规则的资源（其余资源在子规则里看就够了）
            hot_res = [(rid, info) for rid, info in sorted_res if len(info["rules"]) >= 2]
            if hot_res:
                parts.append(f"\n\n**资源命中规则汇总**（共 {len(all_rids)} 个资源命中 {rule_count} 类规则；以下列出命中 ≥ 2 类规则的 {len(hot_res)} 个资源）\n")
                rows = []
                for rid, info in hot_res[:30]:  # 上限 30 行避免汇总表本身过长
                    rule_str = " / ".join(info["rules"])
                    rows.append([sev(info["max_level"]), rid, info["name"] or "—", info["region"] or "—",
                                 len(info["rules"]), rule_str])
                parts.append(md_table(
                    ["严重度", "资源 ID", "名称", "地域", "命中规则数", "命中规则"],
                    ["r", "l", "l", "l", "r", "l"], rows,
                    dedup_common=False  # 这张汇总表本身不做列复读
                ))
                if len(hot_res) > 30:
                    parts.append(f"\n> 仅展示前 30 个高频命中资源；其余 {len(hot_res) - 30} 个详见下方各子规则明细。\n")

        # 渲染合并后的共因块
        for (sname, repair_text), slot in merged.items():
            if not slot["rows"]:
                # 无可定位资源：单行说明，不再重复"共因建议"句子
                if slot["no_resource_count"]:
                    parts.append(f"\n**{sname}**：命中 {slot['risk_total']} 条，平台未返回可定位资源明细。")
                continue
            # 同一 (sname, repair) 二元组下命中条数 = 行数 + 缺失明细数（用于标题展示真实命中量）
            display_title = sname
            if len(slot["rows"]) != slot["risk_total"]:
                display_title = f"{sname}（命中 {slot['risk_total']} 条，已定位 {len(slot['rows'])} 条）"
            parts.append(render_common_cause_block(
                title=display_title,
                common_advice=f"共因建议：{repair_text}",
                headers=["严重度", "资源 ID", "名称", "地域"],
                aligns=["r", "l", "l", "l"],
                rows=slot["rows"],
                region_idx=3,
                business_hint="—",
                detail_max=5,
            ))

    # === 云数据库（Redis / MySQL）从 advisor 安全组里抽出（含 PostgreSQL/MongoDB 的安全规则也并入） ===
    db_secs = [f for f in sec_findings if re.search(r"Redis|MySQL|MongoDB|PostgreSQL|云数据库", str(f.get("product") or ""), re.I)]
    if db_secs:
        parts.append("\n\n### 云数据库（Redis / MySQL）\n")
        # 按产品分组，标题用 v2 客户版描述性 H4（Redis 高危命令风险 / MySQL 非 root 高危权限）
        by_prod = collections.defaultdict(list)
        for f in db_secs:
            by_prod[f.get("product") or "云数据库"].append(f)
        # v2 客户版描述性子节标题映射
        h4_label_map = {
            "Redis": "Redis 高危命令风险",
            "云数据库（Redis）": "Redis 高危命令风险",
            "MySQL": "MySQL 非 root 高危权限",
            "云数据库（MySQL）": "MySQL 非 root 高危权限",
            "MongoDB": "MongoDB 安全风险",
            "云数据库（MongoDB）": "MongoDB 安全风险",
            "PostgreSQL": "PostgreSQL 安全风险",
            "云数据库（PostgreSQL）": "PostgreSQL 安全风险",
        }
        for prod, fl in sorted(by_prod.items(), key=lambda kv: -sum(int(f.get("risk_total") or 0) for f in kv[1])):
            h4 = h4_label_map.get(prod, prod)
            _render_product_block(h4, fl, anchor_h="####")

    # === 网络与安全组（合并 5 个 H4：网络基础设施 / 规则 / 扩展发现 / 公网清单 / 空SG分布） ===
    parts.append("\n\n### 网络与安全组\n")
    parts.append(f"共扫描 {sum(rd.get('total_sgs', 0) for rd in (sg or {}).values())} 个安全组，发现全端口开放 {len(all_port)} 条、高危端口 {len(hr)} 条、孤儿安全组 {len(empty)} 个。")

    # 4-1 网络基础设施（先呈现网络架构面，再讲规则风险）
    net = infra.get("network", {}) if isinstance(infra, dict) else {}
    if net:
        parts.append("\n\n#### 网络基础设施\n")
        parts.append("VPC / 子网 / 网络 ACL / NAT 网关 / 堡垒机覆盖情况，用于评估网络架构面与边界防护。\n")
        parts.append(md_table(["巡检项", "当前状态", "数量", "风险"], ["l", "l", "r", "r"], [
            ["VPC", "已采集", net.get("vpc_summary", {}).get("total", 0), "✅"],
            ["缺 ACL 子网", "建议核心子网绑定网络 ACL", len(net.get("subnets_no_acl", [])), "🟡" if net.get("subnets_no_acl") else "✅"],
            ["NAT 网关", "已采集", net.get("nat_gateways", {}).get("total", 0), "✅"],
            ["堡垒机", "已部署" if net.get("bastion_host", {}).get("deployed") else "未发现实例", net.get("bastion_host", {}).get("count", 0), "✅" if net.get("bastion_host", {}).get("deployed") else "🟡"],
        ]))

    # 4-2 安全组规则与高危端口（共因聚合 + 高危端口按 (sg, port, proto) 去重）
    parts.append("\n\n#### 安全组规则与高危端口\n")
    if all_port:
        parts.append(cc_advice("以下安全组对 0.0.0.0/0 全协议开放，建议根据业务实际需求调整为最小化方式放通访问规则，仅保留业务白名单来源。", anchor="云安全 → 安全组 0.0.0.0/0 全协议开放"))
        parts.append(md_table(["安全组 ID", "地域", "规则", "关联实例", "风险"], ["l", "l", "l", "r", "r"],
                              [[r.get("sg") or r.get("id"), r.get("region") or "平台未返回地域",
                                f"{r.get('proto','ALL')}:ALL 对 0.0.0.0/0", r.get("assoc", 0),
                                "🔴" if int(r.get("assoc") or 0) > 0 else "🟡"] for r in all_port]))
    if hr:
        # 按 (sg, region, port, proto) 去重 — 修复重复打印 bug
        seen_hr = set()
        hr_dedup = []
        for r in hr:
            key = (r.get("sg"), r.get("region"), str(r.get("port")), str(r.get("proto") or ""))
            if key in seen_hr:
                continue
            seen_hr.add(key)
            hr_dedup.append(r)
        parts.append("\n\n**高危端口规则**")
        parts.append(cc_advice("22/SSH、3389/RDP 等高危端口直接暴露公网风险较高，建议绑定堡垒机或限制为运维 IP 白名单访问。", anchor="云安全 → 高危端口（22/3389）"))
        parts.append(md_table(["安全组 ID", "地域", "端口", "关联实例", "风险"], ["l", "l", "l", "r", "r"],
                              [[r.get("sg"), r.get("region"), f"{r.get('port')}({r.get('service')})",
                                r.get("assoc"), "🟠"] for r in hr_dedup]))
        if len(hr_dedup) < len(hr):
            parts.append(f"\n> 数据口径：原始命中 {len(hr)} 条已按 (安全组 ID, 端口, 协议) 去重为 {len(hr_dedup)} 条；同一规则在采集中被多次记录的重复行已合并。")
    if not all_port and not hr:
        parts.append("✅ 本次未发现全端口开放或高危端口规则。")

    # 4-3 安全组扩展发现（advisor 安全组产品里 product 含"安全组"的，共因聚合）
    sg_advisor = [f for f in sec_findings if "安全组" in str(f.get("product") or "")]
    if sg_advisor:
        parts.append("\n\n#### 安全组扩展发现（advisor）\n")
        for f in sorted(sg_advisor, key=lambda x: (-int(x.get("level_max") or 0), -int(x.get("risk_total") or 0))):
            sname = f.get("strategy_name") or "风险项"
            arr = f.get("risks") or []
            repair_text = f.get("repair") or "按控制台建议整改并复扫"
            if not arr:
                add_review("云安全", f.get("product") or "安全组", sname, f.get("risk_total"), "平台未返回可定位资源明细")
                parts.append(f"\n**{sname}**：命中 {f.get('risk_total') or 0} 条，平台未返回可定位资源明细。")
                continue
            rows = []
            for r in arr:
                rid = resource_id(r)
                if not rid:
                    add_review("云安全", f.get("product") or "安全组", sname, f.get("risk_total"), "平台未返回可定位资源 ID")
                    continue
                rows.append([sev(f.get("level_max")), rid, risk_name(r), risk_region(r)])
            if rows:
                parts.append(render_common_cause_block(
                    title=sname,
                    common_advice=f"共因建议：{repair_text}",
                    headers=["严重度", "资源 ID", "名称", "地域"],
                    aligns=["r", "l", "l", "l"],
                    rows=rows,
                    region_idx=3,
                    business_hint="—",
                    detail_max=5,
                ))

    # 4-4 空安全组分布（先于公网清单展示，把"安全组治理类"集中在网络基础设施区段）
    parts.append("\n\n#### 空安全组分布（19 个地域聚合）\n")
    sg_region_rows = []
    sg_total_empty = 0
    for region_name, rd in (sg or {}).items():
        if not isinstance(rd, dict):
            continue
        es = rd.get("empty_sgs", []) or []
        cnt = len(es) if isinstance(es, list) else int(es or 0)
        if cnt > 0:
            total_in_region = rd.get("total_sgs", 0) or 0
            sg_region_rows.append([region_name, cnt, f"该地域共 {total_in_region} 个安全组"])
            sg_total_empty += cnt
    if sg_total_empty == 0:
        parts.append("✅ 全部地域已使用安全组")
    else:
        sg_region_rows.sort(key=lambda r: -int(r[1]))
        parts.append(md_table(["地域", "空 SG 数", "备注"], ["l", "r", "l"], sg_region_rows))
        parts.append(f"\n\n合计 {sg_total_empty} 个空安全组分布于 {len(sg_region_rows)} 个地域。")

        # 空 SG 实例明细（地域聚合表已在前；明细折叠到引用块，但保留可检索性）
        empty_sg_rows = []
        for region_name, rd in (sg or {}).items():
            if not isinstance(rd, dict):
                continue
            es = rd.get("empty_sgs", []) or []
            if not isinstance(es, list):
                continue
            for item in es:
                if not isinstance(item, dict):
                    continue
                empty_sg_rows.append([
                    region_name,
                    item.get("id") or item.get("sg_id") or "—",
                    item.get("name") or "—",
                ])
        if empty_sg_rows:
            empty_sg_rows.sort(key=lambda r: (str(r[0]), str(r[1])))
            # 共因建议：表外一句话；明细折叠到引用块（避免 20 行表占版面）
            parts.append(f"\n\n**空安全组实例明细（共 {len(empty_sg_rows)} 个）**")
            parts.append(cc_advice("上述安全组当前无任何规则配置，建议确认是否仍在被引用，未引用的可清理回收。", anchor="云安全 → 空安全组实例"))
            # v3.9：取消分页拆表，输出单张全量明细表（用户偏好：留全部明细）
            parts.append(md_table(["地域", "安全组 ID", "名称"], ["l", "l", "l"], empty_sg_rows))

    # 4-5 公网云资源清单（共因聚合 v2：把"建议动作"列抽出，按 (风险项, 建议) 二元组聚合成共因建议块）
    exposure_rows = build_public_cloud_resource_rows(findings, details)
    parts.append("\n\n#### 公网云资源清单\n")
    if exposure_rows:
        parts.append("以下清单展示已能定位到的公网资产或外网入口；网络放通规则在上方“安全组规则与高危端口”小节已单独展示。")
        # 按 (风险项=col 6, 建议动作=col 8) 聚合共因建议；同二元组下所有资源在紧凑表里只列一次"建议"句子
        cause_groups = collections.OrderedDict()
        for row in exposure_rows:
            risk_item = row[6] if len(row) > 6 else ""
            advice = row[8] if len(row) > 8 else ""
            cause_groups.setdefault((risk_item, advice), []).append(row)
        # 渲染每个共因块（共因建议在前，紧凑表在后；表去掉"建议动作"列）
        for (risk_item, advice), grp_rows in cause_groups.items():
            # 按地域聚合显示
            region_count = collections.Counter(r[4] for r in grp_rows if len(r) > 4 and r[4])
            region_summary = "、".join(f"{rg}×{ct}" for rg, ct in region_count.most_common())
            parts.append(f"\n**{risk_item}**（命中 {len(grp_rows)} 条；地域分布：{region_summary or '—'}）\n")
            parts.append(cc_advice(advice, anchor=f"公网云资源 → {risk_item}"))
            # 紧凑表（8 列：去掉"建议动作"），明细 ≥10 时按地域聚合 + 折叠
            compact_rows = [r[:8] for r in grp_rows]
            compact_headers = ["风险", "分类", "资源 ID", "名称", "地域", "公网入口", "风险项", "发现来源"]
            compact_aligns = ["r", "l", "l", "l", "l", "l", "l", "l"]
            # v3.9：取消"前 5 条 + 其余 N 条"分页，统一输出单张全量紧凑表（用户偏好：留全部明细）
            parts.append(md_table(compact_headers, compact_aligns, compact_rows))
    else:
        parts.append("✅ 本次未发现可定位的公网云资源明细。")

    # === 安全产品覆盖（v2 已挪到本节：网络与安全组 → 空 SG 之后） ===
    sec = infra.get("security", {}) if isinstance(infra, dict) else {}
    if sec:
        parts.append("\n\n#### 安全产品覆盖\n")
        parts.append("综合 WAF / DDoS 高防 / 云防火墙 / 数据库审计 等边界与纵深防护产品的覆盖情况。\n")
        waf = sec.get("waf", {}) if isinstance(sec.get("waf"), dict) else {}
        ddos = sec.get("ddos", {}) if isinstance(sec.get("ddos"), dict) else {}
        cfw = sec.get("cfw", {}) if isinstance(sec.get("cfw"), dict) else {}
        waf_domains = waf.get("domains_total", 0)
        waf_unbound_lb = len(waf.get("domains_unbound_lb", []) or [])
        waf_remark = f"{waf_unbound_lb} 个 LB 未绑定" if waf_unbound_lb > 0 else "—"
        ddos_bgp = ddos.get("bgp_instances", 0)
        ddos_remark = "采集受限" if ddos.get("scan_status") == "denied" else "—"
        cfw_enabled = cfw.get("enabled", False)
        cfw_remark = "API 不可用" if cfw.get("scan_status") == "denied" else "—"
        db_audit_note = sec.get("db_audit_note") or "—"
        parts.append(md_table(
            ["产品", "状态", "备注"], ["l", "l", "l"],
            [
                ["WAF", f"域名 {waf_domains} 个", waf_remark],
                ["DDoS 高防", f"BGP 实例 {ddos_bgp} 个", ddos_remark],
                ["云防火墙", f"启用={cfw_enabled}", cfw_remark],
                ["数据库审计", db_audit_note, "—"],
            ]
        ))

    # === 云安全中心（CSC） ===
    csc_secs = [f for f in sec_findings if re.search(r"云安全中心|CSC|安全中心", str(f.get("product") or ""), re.I)]
    if csc_secs:
        _render_product_block("云安全中心（CSC）", csc_secs, anchor_h="###")

    # === 云服务器（CVM 扩展，advisor 安全组里的 CVM 类） ===
    cvm_secs = [f for f in sec_findings if re.search(r"云服务器|CVM|主机", str(f.get("product") or ""), re.I)]
    if cvm_secs:
        _render_product_block("云服务器（CVM）", cvm_secs, anchor_h="###")

    # === advisor 安全组里 fallback：以上未覆盖的剩余产品 ===
    covered_products = set()
    for f in db_secs + sg_advisor + csc_secs + cvm_secs + cos_secs:
        covered_products.add(str(f.get("product") or ""))
    leftover = [f for f in sec_findings if str(f.get("product") or "") not in covered_products]
    if leftover:
        leftover_by_prod = collections.defaultdict(list)
        for f in leftover:
            leftover_by_prod[f.get("product") or "其他安全产品"].append(f)
        for prod, fl in sorted(leftover_by_prod.items(), key=lambda kv: -sum(int(f.get("risk_total") or 0) for f in kv[1])):
            _render_product_block(prod, fl, anchor_h="###")
    def dynamic_group(group, title):
        fs = [f for f in findings if f.get("group") == group]
        if not fs:
            return
        parts.append(f"\n\n## {title}\n")
        by_prod = collections.defaultdict(list)
        for f in fs:
            by_prod[f.get("product") or "其他"].append(f)
        for prod, fl in sorted(by_prod.items(), key=lambda kv: -sum(int(f.get("risk_total") or 0) for f in kv[1])):
            locatable = [f for f in fl if finding_has_locatable_resources(f)]
            review_only = [f for f in fl if not finding_has_locatable_resources(f)]
            total_hits = sum(int(f.get('risk_total') or 0) for f in fl)
            loc_hits = sum(int(f.get('risk_total') or 0) for f in locatable)
            parts.append(f"### {prod}\n")
            if locatable:
                parts.append(f"{prod} 命中 {len(fl)} 类风险；其中 {len(locatable)} 类返回可定位资源明细，涉及 {loc_hits} 个可定位资源。")
            else:
                parts.append(f"{prod} 平台策略返回 {len(fl)} 类风险、合计 {total_hits} 条命中，但本次未返回可定位资源明细；本节不作为可直接整改清单展示，已列入数据完整性与复核项。")
            # 共因聚合 v2：按 (strategy_name, repair) 二元组先合并再渲染；同二元组下"建议"句子只列一次
            cause_groups = collections.OrderedDict()
            for f in sorted(fl, key=lambda x: (-int(x.get("level_max") or 0), -int(x.get("risk_total") or 0))):
                arr = f.get("risks") or []
                sname = f.get("strategy_name") or "风险项"
                repair = f.get("repair") or "按控制台建议整改并复扫"
                key = (sname, repair)
                slot = cause_groups.setdefault(key, {"level_max": int(f.get("level_max") or 0), "rows": []})
                slot["level_max"] = max(slot["level_max"], int(f.get("level_max") or 0))
                if not arr:
                    add_review(title, prod, sname, f.get("risk_total"), "平台未返回可定位资源明细")
                    continue
                if not finding_has_locatable_resources(f):
                    add_review(title, prod, sname, f.get("risk_total"), "平台未返回可定位资源明细")
                    continue
                for r in arr:
                    rid = resource_id(r) or "—"
                    if rid == "—":
                        add_review(title, prod, sname, 1, "平台未返回资源 ID")
                        continue
                    slot["rows"].append([sev(f.get("level_max")), rid, risk_name(r) or "—", risk_region(r) or "—"])
            # v3.9：跨 sibling rule-subsection 资源重叠检测——同一 (prod, group)
            # 内若多条 rule 对同一组 resource_id 命中（≥2 rules、≥2 resources），
            # 合并成一张「资源 × 命中规则」矩阵表代替 N 张重复行的子表，避免 preflight
            # repeated_table_rows 误判（cls-* TKE 集群在 5 类风险中重复出现的典型场景）。
            _row_to_rules = collections.defaultdict(list)  # (sev,rid,name,region) -> [rule_short]
            _rule_total_rows = {}  # sname -> rows count
            for (sname, repair), slot in cause_groups.items():
                _rule_total_rows[sname] = len(slot["rows"])
                if not slot["rows"]:
                    continue
                # 取规则短名：去掉冗长前缀（如"容器服务（TKE）集群存在...风险" -> "...风险"）
                # 不再字符截短（会触发 preflight 截断符正则），完整保留规则名
                rule_short = sname.replace(prod, "").lstrip("（）()").strip() or sname
                for row in slot["rows"]:
                    _row_to_rules[(row[0], row[1], row[2], row[3])].append(rule_short)
            # 仅当存在跨 ≥3 rule 重叠的资源时启用合并视图（保守阈值，避免破坏正常场景）
            _overlap_resources = {k: v for k, v in _row_to_rules.items() if len(v) >= 3}
            _use_consolidated_matrix = len(_overlap_resources) >= 2
            any_rendered = False
            if _use_consolidated_matrix:
                # 输出整合矩阵（资源 × 命中规则集合），代替全部 sibling rule 子表
                parts.append(
                    f"\n> {prod} 内多条规则命中同一组资源，已合并为下方资源-规则矩阵；"
                    f"逐规则的处置建议见各规则段落。\n"
                )
                matrix_headers = ["严重度", "资源 ID", "名称", "地域", "命中规则数", "命中规则"]
                matrix_aligns = ["r", "l", "l", "l", "r", "l"]
                matrix_rows = []
                for (s, rid, n, rg), rules in sorted(_row_to_rules.items(), key=lambda kv: (-len(kv[1]), kv[0][1])):
                    matrix_rows.append([s, rid, n, rg, len(rules), " / ".join(sorted(set(rules)))])
                parts.append(md_table(matrix_headers, matrix_aligns, matrix_rows, dedup_common=False))
                # 仍输出每条规则的 repair 共因建议（去重），但不再输出重复资源表
                for (sname, repair), slot in cause_groups.items():
                    if not slot["rows"]:
                        continue
                    parts.append(f"\n**{sname}**（命中 {len(slot['rows'])} 条）\n")
                    parts.append(cc_advice(repair, anchor=f"云端策略 → {sname}"))
                any_rendered = True
            else:
                # 原渲染路径：每个二元组一段共因建议 + 一张紧凑 4 列表
                for (sname, repair), slot in cause_groups.items():
                    if not slot["rows"]:
                        continue
                    # 地域分布
                    region_count = collections.Counter(r[3] for r in slot["rows"] if r[3] and r[3] != "—")
                    region_summary = "、".join(f"{rg}×{ct}" for rg, ct in region_count.most_common())
                    parts.append(f"\n**{sname}**（命中 {len(slot['rows'])} 条；地域分布：{region_summary or '—'}）\n")
                    # v3.7：cc_advice 跨章节去重 + 分类前缀；多行 repair 由 safe_quote 内部逐行加 >
                    parts.append(cc_advice(repair, anchor=f"云端策略 → {sname}"))
                    compact_headers = ["严重度", "资源 ID", "名称", "地域"]
                    compact_aligns = ["r", "l", "l", "l"]
                    # v3.9：取消"前 5 条 + 其余 N 条"分页，统一输出单张全量紧凑表（用户偏好：留全部明细）
                    parts.append(md_table(compact_headers, compact_aligns, slot["rows"]))
                    parts.append("")
                any_rendered = True
            if not any_rendered and locatable:
                parts.append(f"{prod} 的可定位明细本次未能稳定渲染，已列入数据完整性与复核项。")

    # 安全产品覆盖已挪到"网络与安全组 → 空 SG 之后"（更合理的归类，对齐 v2 客户版结构）

    # 安全类动态产品已在上方"云安全"大节内按 v2 客户版章节分散渲染（云数据库 / 网络与安全组 / CSC / CVM 扩展），此处不再调用 dynamic_group("安全", ...)
    dynamic_group("可靠", "云可靠")
    dynamic_group("性能", "云性能")
    dynamic_group("服务限制", "云限制")

    parts.append("\n\n## 资产标签治理\n")
    details = tags.get("untagged_details", []) if isinstance(tags, dict) else []
    month_str = tags.get('month','未采集')

    # --- H3-1 标签完整性检查 ---
    parts.append("\n### 标签完整性检查\n")
    parts.append(f"标签完整性扫描基于 {month_str} 月已产生消费资源，规则为 head 必填且 business/customer 二选一；当前标签不完整资源 {len(details)} 项。")

    # --- H3-2 标签治理看板 ---
    parts.append("\n\n### 标签治理看板\n")
    parts.append(md_table(
        ["必需标签", "强制性", "说明"], ["l", "l", "l"],
        [
            ["head", "必填", "业务负责人，缺失为严重"],
            ["business", "business OR customer 二选一", "业务标识"],
            ["customer", "business OR customer 二选一", "客户标识"],
            ["月份窗口", "—", month_str],
        ]
    ))

    # --- H3-3 按业务码分组的标签不完整资源 ---
    parts.append("\n\n### 按业务码分组的标签不完整资源\n")
    by_biz_grp = collections.defaultdict(lambda: {"name": "", "count": 0, "cost": 0.0})
    for d in details:
        bc = d.get("biz_code") or "—"
        by_biz_grp[bc]["name"] = d.get("biz_name") or "—"
        by_biz_grp[bc]["count"] += 1
        try:
            by_biz_grp[bc]["cost"] += float(d.get("total_cost") or 0)
        except (TypeError, ValueError):
            pass
    biz_rows = sorted([[bc, v["name"], v["count"], f"{v['cost']:.4f}"] for bc, v in by_biz_grp.items()],
                      key=lambda r: -float(r[3]))
    total_count = sum(v["count"] for v in by_biz_grp.values())
    total_cost = sum(v["cost"] for v in by_biz_grp.values())
    biz_rows.append(["合计", "—", total_count, f"{total_cost:.4f}"])
    parts.append(md_table(
        ["biz_code", "biz_name", "未设置标签的资源数", "月度费用合计(元)"],
        ["l", "l", "r", "r"], biz_rows
    ))

    # --- H3-4 标签不完整资源明细 ---
    parts.append("\n\n### 标签不完整资源明细\n")
    tag_rows = [[d.get("biz_name"), d.get("product_name"), d.get("region") or "平台未返回地域", d.get("resource_id"), d.get("resource_name") or "接口未返回名称", ", ".join(d.get("missing_keys", [])), d.get("verdict"), f"{float(d.get('total_cost') or 0):.4f}"] for d in details]
    if tag_rows:
        parts.append(md_table(["业务", "产品", "地域", "资源 ID", "名称", "缺失标签", "等级", "月度费用(元)"], ["l", "l", "l", "l", "l", "l", "r", "r"], tag_rows))
    else:
        parts.append("标签治理这块本次未发现需治理资源，或账单标签接口未返回明细。")

    # --- H3-5 数据口径 ---
    tags_status = tags.get("scan_status") or "ok" if isinstance(tags, dict) else "ok"
    parts.append("\n\n### 数据口径\n")
    parts.append(md_table(
        ["项", "说明"], ["l", "l"],
        [
            ["月份窗口", month_str],
            ["覆盖范围", "仅覆盖该月已产生消费的资源，无消费资源未纳入"],
            ["接口", "billing.DescribeBillResourceSummary × 3（TagKey=head/business/customer, TagValue 为空）"],
            ["scan_status", str(tags_status)],
        ]
    ))

    fixed_actions = []
    if all_port:
        fixed_actions.append(("red", "限制安全组全端口开放规则，仅保留业务白名单来源。"))
    if brute_success:
        fixed_actions.append(("red", "CVM 出现密码破解成功事件，立即隔离实例、轮换登录凭据并核查源 IP。"))
    if len(admins) > 5:
        fixed_actions.append(("red", "回收超量 Administrator 权限，压缩到最小权限集合。"))
    if public_r or public_rw:
        fixed_actions.append(("orange", "复核 COS 公有读/写桶，确认公开必要性并收敛 ACL。"))
    if details:
        fixed_actions.append(("orange", "补齐已产生消费资源的 head/business/customer 标签。"))
    if empty:
        fixed_actions.append(("yellow", "清理孤儿安全组，降低后续误关联风险。"))
    red, orange, yellow = build_action_list(findings, fixed_actions)

    # ---- 资产标签治理大节内的 基线配置检查 / 配置审计合规率 ----
    base = infra.get("baseline", {}) if isinstance(infra, dict) else {}
    if base:
        parts.append("\n\n### 基线配置检查\n")
        parts.append("基线配置覆盖组织归属、安全联系人和消息订阅；部分接口未授权时会以「未配置/采集受限」标注，需补充权限后复扫。\n")
        org = base.get("organization", {}) if isinstance(base.get("organization"), dict) else {}
        contacts = base.get("contacts", {}) if isinstance(base.get("contacts"), dict) else {}
        notices = base.get("alarm_notices", {}) if isinstance(base.get("alarm_notices"), dict) else {}
        # 状态：v2 风格用风险标记
        if org.get("joined"):
            org_status, org_risk = (org.get("org_name") or org.get("org_id") or "已加入"), "✅"
        else:
            org_status, org_risk = "未确认加入组织", "⚠️"
        contacts_count = contacts.get('count', 0) or 0
        notices_count = notices.get('count', 0) or 0
        contacts_status = f"配置 {contacts_count} 个"
        contacts_risk = "✅" if contacts_count > 0 else "🟡"
        notices_status = f"配置 {notices_count} 个"
        notices_risk = "✅" if notices_count > 0 else "🟡"
        parts.append(md_table(
            ["检查项", "当前状态", "风险等级"], ["l", "l", "r"],
            [
                ["组织归属", org_status, org_risk],
                ["安全联系人", contacts_status, contacts_risk],
                ["消息订阅", notices_status, notices_risk],
            ]
        ))

    comp = infra.get("compliance", {}) if isinstance(infra, dict) else {}
    if comp:
        parts.append("\n\n### 配置审计合规率\n")
        if not comp.get("config_enabled"):
            parts.append(f"配置审计接口本次未获授权，无法确认标签完整率。\n")
            parts.append(md_table(
                ["框架", "规则数", "标签完整率", "风险等级"], ["l", "r", "r", "r"],
                [
                    ["CIS", 0, "未采集", "⚠️"],
                    ["等保", 0, "未采集", "⚠️"],
                    ["其他", 0, "未采集", "⚠️"],
                ]
            ))
        else:
            fw = comp.get("rules_by_framework", {}) if isinstance(comp.get("rules_by_framework"), dict) else {}
            rate = comp.get("compliance_rate")
            rate_str = f"{rate:.1%}" if isinstance(rate, (int, float)) else "—"
            non_comp = comp.get("non_compliant_count", 0)
            parts.append(md_table(
                ["框架", "规则数", "合规率", "不合规"], ["l", "r", "r", "r"],
                [
                    ["CIS", fw.get("CIS", 0), rate_str, non_comp],
                    ["等保", fw.get("等保", 0), rate_str, non_comp],
                    ["其他", fw.get("other", 0), rate_str, non_comp],
                ]
            ))

    parts.append("\n\n## 整改推进计划\n")

    # --- 5 H3 v2 风格：整体推进思路 / 阶段计划 / 责任协同建议 / 下次复核指标 / 复核范围 ---
    # 注意：上方循环（事件流）将 `principals` 局部变量 shadow 为 dict，这里重新从 cam 拉。
    cam_principals = cam.get("principals", []) if isinstance(cam, dict) else []
    sub_users = sum(1 for p in cam_principals if isinstance(p, dict) and p.get("type") == "user")
    idle_ak_cnt = len(idle90)
    no_mfa_cnt = len(no_mfa)
    admin_cnt = admin_human_n  # 使用前面 UIN 去重后的真实人员账号数（不含 Organization 角色 / 不重复计数）
    machines_total = (mg.get("MachinesAll") if isinstance(mg, dict) else None) or 0
    high_pub = len(all_port) + len(public_r) + len(public_rw)
    brute_cnt = len(brute_success)

    parts.append("### 整体推进思路\n")
    parts.append(
        "本次整改建议按「降低暴露面、保障连续性、补齐责任归属」的顺序推进。"
        "先处理可能带来直接安全或业务中断影响的问题，再推进账号、标签、备份、审计等治理项，"
        "最后通过复核确认风险是否真正关闭。"
    )

    parts.append("\n### 阶段计划")
    parts.append(md_table(
        ["阶段", "重点事项", "预期结果"],
        ["l", "l", "l"],
        [
            ["第一阶段：风险收敛", "安全组公网开放、过期证书、CWP 主机安全组件缺口、暴力破解成功事件、高权限人员账号、数据库/缓存高危配置", "直接暴露面和高权限风险下降，关键入口具备基本安全防护"],
            ["第二阶段：稳定性治理", "TKE 级联删除、Pod OOM、CLB 健康检查、NAT、CKafka、数据库、CVM/EIP 性能与可靠性问题", "核心链路的单点、容量和性能风险得到评估和处理"],
            ["第三阶段:账号与密钥治理", "长期未登录账号、闲置 AK、协助者 AK、未归属用户组", "身份权限边界清晰，长期不用的访问入口完成回收"],
            ["第四阶段:治理基线完善", "资源标签、CloudAudit、COS 安全配置、备份策略、跨可用区部署", "责任归属、审计追溯、数据保护和恢复能力形成长期机制"],
        ],
    ))

    parts.append("\n### 责任协同建议")
    parts.append(md_table(
        ["事项", "建议牵头角色", "协同角色"],
        ["l", "l", "l"],
        [
            ["公网安全组、WAF、证书、主机安全", "安全负责人", "网络、运维、业务系统负责人"],
            ["TKE、CLB、NAT、CKafka、数据库、CVM/EIP", "平台/运维负责人", "业务系统负责人、数据库负责人"],
            ["CAM 账号、权限、AK、用户组", "账号权限管理员", "安全负责人、各业务负责人"],
            ["资产标签、费用归属、资源生命周期", "云资源管理员", "财务/成本负责人、业务负责人"],
            ["CloudAudit、COS、备份和恢复策略", "安全与平台负责人", "存储、数据库、业务系统负责人"],
        ],
    ))

    parts.append("\n### 下次复核指标")
    parts.append(md_table(
        ["复核主题", "期望变化"],
        ["l", "l"],
        [
            ["公网暴露", "全协议对公网开放安全组数量下降，高危端口开放规则收敛到明确来源地址"],
            ["身份权限", f"Administrator 权限人员账号减少（当前 {admin_cnt} 个），长期未登录账号和闲置 AK 完成禁用或删除（当前闲置 AK {idle_ak_cnt} 个）"],
            ["主机与证书", "主机安全组件覆盖率提升，暴力破解成功事件完成处置，过期证书完成续签或下线"],
            ["TKE 稳定性", "级联删除和 Pod OOM 风险有处置记录，关键集群对象与异常 Pod 数量下降"],
            ["可靠性与性能", "NAT、CLB、CKafka、数据库、CVM、EIP 等关键风险有架构整改、容量调整或风险接受记录"],
            ["资产标签", f"标签不完整资源完成补标（当前 {len(details)} 项），尤其是缺少负责人标签的资源优先清零"],
        ],
    ))

    parts.append("\n### 复核范围")
    parts.append(md_table(
        ["项目", "说明"],
        ["l", "l"],
        [
            ["巡检账户", f"{account}（{uin}）"],
            ["覆盖范围", "云安全、云可靠、云性能、云限制、资产标签治理五类问题"],
            ["明细完整性", "安全组、AK 闲置、标签明细、可靠性与性能风险等长表均应在文档中展开"],
            ["需人工确认项", "主账号 AK、CloudAudit 长期跟踪、COS 全量桶安全配置、CWP 攻击事件细分等需由具备相应权限的负责人最终确认"],
        ],
    ))

    parts.append("\n## 数据完整性与扫描降级说明\n")

    # ---- V3 客户版补全：数据口径与采集说明 ----
    cam_caller = cam.get("caller_identity", {}) if isinstance(cam, dict) else {}
    caller_uin = cam_caller.get("uin") if isinstance(cam_caller, dict) else None
    if not caller_uin:
        _principals = cam.get("principals", []) if isinstance(cam, dict) else []
        if _principals and isinstance(_principals[0], dict):
            caller_uin = _principals[0].get("caller_uin") or _principals[0].get("uin")
    caller_uin = caller_uin or "N/A"
    login_total_all = cwp.get("login_total_all", 0) if isinstance(cwp, dict) else 0
    login_sampled = len(cwp.get("host_login") or []) if isinstance(cwp, dict) else 0
    parts.append("### 数据口径与采集说明\n")
    parts.append(
        f"本次报告基于子账号身份（caller_uin={caller_uin}）调用云原生 API 采集，关键口径："
    )
    parts.append("- **CAM 用户**：principals 列表去重后按 type 拆分（user/collaborator）；可登录定义为 console_login==1")
    parts.append("- **AK 闲置**：阈值 90 天，含从未使用（即 LastUsedDate 为空的 AK）")
    parts.append("- **MFA 开启**：仅统计 console_login==1 的人员账号")
    parts.append("- **CVM 主机安全**：以云镜（CWP）防护状态为口径；未安装客户端=未防护")
    parts.append(f"- **异常登录**：CWP 风险登录窗口为采样数据（{login_total_all} 全量中采 {login_sampled} 条）")
    parts.append("- **公网风险**：以 advisor 平台策略 + 安全组 + 公网 IP 三视角交叉")
    parts.append("- **采集受限项**：以下条目本次未能完整采集，仅状态标注：")
    parts.append("  - 主账号 AK 列表（root_aks_scan_status='skipped_subuser_caller'）— 子账号身份无权列主账号 AK")
    parts.append("  - 配置审计（compliance.scan_status='denied'）— 服务未启用或子账号无权")
    parts.append("  - 云审计（cloudaudit.scan_status='caller_subuser'）— 子账号身份限制")
    parts.append("  - 部分 infra 子项（DescribeOrganization / DescribeAccountAttributes / DescribeBhInstances 等 API 在子账号身份下不可用）")

    deg = []
    if cloudaudit.get("scan_status") and cloudaudit.get("scan_status") != "ok":
        deg.append(["CloudAudit 跟踪集", cloudaudit.get("scan_status"), "需主账号或补充只读权限复核"])
    for key, label in [("baseline", "基线配置"), ("security", "安全产品覆盖"), ("compliance", "配置审计")]:
        st = infra.get(key, {}).get("scan_status") if isinstance(infra, dict) else None
        if st and st != "ok":
            deg.append([label, status_label(st), "接口未完全授权，需补充只读权限后复扫"])
    if deg:
        parts.append(md_table(["数据源", "采集状态", "说明"], ["l", "l", "l"], deg))
    else:
        parts.append("本次未发现扫描降级。")
    if review_rows:
        parts.append("\n\n**平台未返回可定位资源 ID 的复核项**")
        parts.append(md_table(["报告域", "产品", "风险规则", "涉及数量", "定位状态"], ["l", "l", "l", "r", "l"], review_rows))

    parts.append(f"\n\n报告生成：{now:%Y-%m-%d %H:%M} GMT+8 | SOP: TC-SEC-SOP-001 v1.0\n下次复查：{next_date}")
    text = sanitize("\n".join(parts))
    # Output-layer translation: covers DATA-driven Chinese (advisor rule names /
    # repair advice / matrix cells) + residual skeleton that L() cannot reach.
    # zh => pure passthrough (byte-identical); en => frames + longest-first dict.
    text = translate_report(text, _LANG)

    (input_dir / "report.md").write_text(text, encoding="utf-8")
    # report_doc.md is the customer report source. Keep heading levels stable;
    # do not promote H3/H4 to H2, otherwise sub-sections become separate pages.
    doc = text
    (input_dir / "report_doc.md").write_text(doc, encoding="utf-8")

    report_files = {"report_md": str(input_dir / "report.md"), "doc_md": str(input_dir / "report_doc.md")}
    if archive_dir:
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(input_dir / "report_doc.md", archive_dir / "inspector_doc.md")
        report_files["archive_doc_md"] = str(archive_dir / "inspector_doc.md")

    if _LANG == "en":
        headline = (f"{account} inspection complete: {adv.get('total_risks', 0)} cloud-policy risks, "
                    f"{len(all_port)} SG all-port, {len(brute_success)} CVM brute-force successes, "
                    f"{len(public_r)+len(public_rw)} COS public read/write, {len(details)} tag-governance items.")
    else:
        headline = (f"{account} 巡检完成：云端策略风险 {adv.get('total_risks', 0)} 项，SG全端口 {len(all_port)}，"
                    f"CVM破解成功 {len(brute_success)}，COS公有读/写 {len(public_r)+len(public_rw)}，标签治理 {len(details)} 项。")
    summary = {
        "status": "ok",
        "account": account,
        "uin": uin,
        "report_date": report_date,
        "report_files": report_files,
        "counts": {"findings": len(findings), "review_items": len(review_rows), "red_actions": len(red), "orange_actions": len(orange), "yellow_actions": len(yellow)},
        "headline": headline,
    }
    (input_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _run_smartpage_validator(*md_paths) -> None:
    """v3.6 根治-2：生成产物后强制跑 Markdown 兼容性校验，违规直接 sys.exit(1)。
    历史教训：v3.0~v3.5 反复出现"引用块吃表格""标题吞表头"，每次单点 patch
    都修不干净；validator 是渲染前最后一道闸门，确保产物结构兼容。
    """
    import subprocess as _sp
    script = Path(__file__).parent / "validate_smartpage_md.py"
    if not script.exists():
        # 容错：脚本未部署不阻断生成（但打 warning）
        print(f"[validate] ⚠️  未找到 {script}，跳过 Markdown 校验", file=sys.stderr)
        return
    failed = []
    for p in md_paths:
        if not p or not Path(p).exists():
            continue
        r = _sp.run(["python3", str(script), str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            failed.append((p, r.stderr))
            print(r.stderr, file=sys.stderr)
        else:
            print(r.stdout.strip(), file=sys.stderr)
    if failed:
        print(f"\n[validate] ❌ {len(failed)} 个产物未通过 Markdown 兼容性校验，"
              f"拒绝输出。请修复生成器后重试。", file=sys.stderr)
        raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=os.environ.get("OUT_DIR") or os.environ.get("INSPECT_OUT_DIR"), required=False)
    ap.add_argument("--archive-dir")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--skip-validate", action="store_true", help="（仅调试用）跳过 Markdown 校验")
    ap.add_argument("--lang", choices=["zh", "en"], default=os.environ.get("OUTPUT_LANG", "zh"))
    args = ap.parse_args()
    if not args.input_dir:
        raise SystemExit("--input-dir or OUT_DIR/INSPECT_OUT_DIR required")

    # 在任何 tr() 调用之前注册 LLM 兜底钩子
    if args.lang == "en":
        set_llm_hook(create_llm_hook())

    summary = build_report(Path(args.input_dir).resolve(), Path(args.archive_dir).resolve() if args.archive_dir else None, args.date, lang=args.lang)
    # v3.6 根治-2：产物落盘后立刻校验，不过就退出
    if not args.skip_validate:
        rf = summary.get("report_files", {})
        _run_smartpage_validator(
            rf.get("doc_md"),
            rf.get("archive_doc_md"),
        )
    print(json.dumps({"status": summary["status"], "report_files": summary["report_files"], "counts": summary["counts"]}, ensure_ascii=False))

    # 持久化 LLM 翻译缓存
    if args.lang == "en":
        _flush_llm_cache()


if __name__ == "__main__":
    raise SystemExit(main())
