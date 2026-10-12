#!/usr/bin/env python3
"""
render_costopt_html.py — Step 3 闲置巡检看板渲染（单文件 HTML，Chart.js + 深色玻璃拟态）

用法:
  python3 render_costopt_html.py \\
      --data /tmp/costopt_primary/step3_results.json \\
      --output <WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/costopt-{cloud}-{project}-{uin}.html \\
      [--account-name "云降本医生测试账户"] \\
      [--offline /path/to/chart.umd.min.js]

输入 JSON 契约（judge_costopt.py 输出 step3_results.json）：
  {
    "account_name": str,
    "uin": str,
    "bill_month": "YYYY-MM",
    "monitor_window": {"start": "...", "end": "..."},
    "totals": {
      "total_bill_cost": float,
      "estimated_saving": float,
      "finding_count": int,
      "protected_count": int,
      "exempted_count": int
    },
    "findings": [
      {
        "severity": "critical"|"high"|"medium"|"low",
        "product": str,
        "resource_id": str,
        "resource_name": str,
        "region": str,
        "spec": str,
        "issue": str,
        "baseline": float,
        "saving": float,
        "saving_ratio": float,   # 0-100
        "reversible": "low"|"mid"|"high",
        "timing": str,           # "立即可执行" / "下个维护窗口" 等
        "owner": str,
        "advice": str,
        "console_url": str,
      },
      ...
    ],
    "protected": [
      {"product": str, "resource_id": str, "reason": str, "spec": str, "region": str},
      ...
    ],
    "exempted": [
      {"product": str, "resource_id": str, "tag": str, "reason": str},
      ...
    ],
    "insights": [
      {"level": "info"|"warn"|"danger", "text": str}
    ]
  }
"""

import argparse
import json
import os
import sys
import pathlib
from html import escape

# 团队共享 zh→en 枚举字典（产品名/地域/规格/计费模式/账户前缀/洞察话术）。
# 路径锚定到 __file__，因各 skill 以各自 cwd 调用，不能依赖 cwd/PYTHONPATH。
# parents[2]: scripts → cloud-costopt-pro → skills 根，再下钻 cloudops-accounts-pro/scripts/lib。
_LIB = pathlib.Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from i18n import tr, set_llm_hook, create_llm_hook, _flush_llm_cache  # shared team dictionary + LLM fallback

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"

SEVERITY_META = {
    "critical":     {"color": "#ff6b6b", "order": 0},
    "high":         {"color": "#fbbf24", "order": 1},
    "medium_high":  {"color": "#fb9224", "order": 2},
    "medium":       {"color": "#facc15", "order": 3},
    "low":          {"color": "#4ade80", "order": 4},
}

# ───────────── i18n ─────────────
I18N = {
    "zh": {
        # 文档/页头
        "html_lang": "zh-CN",
        "doc_title_suffix": "闲置资源巡检看板",
        "header_title_suffix": "闲置资源巡检看板",
        "billing_month": "账单月份",
        "monitor_window": "监控窗口",
        "window_30d": "（30 天）",
        # 卡片标题
        "card_kpi": "关键指标",
        "card_risk": "风险结构",
        "card_findings": "优化机会全量清单",
        "badge_items": "条",
        "card_products": "按产品聚合",
        "card_insights": "智能洞察",
        # KPI
        "kpi_bill_total": "月度账单总额",
        "kpi_bill_month": "账单月",
        "kpi_est_saving": "预计月度节省",
        "kpi_of_total_bill": "占总账单",
        "kpi_findings": "发现优化资源",
        "kpi_incl": "含",
        "kpi_critical": "严重",
        "kpi_high": "高优",
        "kpi_medium": "中等",
        "kpi_low": "低风险",
        "kpi_scanned": "扫描覆盖资源",
        "kpi_flagged": "优化",
        "kpi_protected": "保护",
        "kpi_exempted": "豁免",
        # 图表
        "chart_severity": "风险等级分布",
        "chart_products": "Top 产品预计月省",
        # 筛选器
        "filter_select_all": "全选",
        "filter_reset": "重置",
        "col_severity": "等级",
        "col_product": "产品",
        "col_optimize": "优化路径",
        "col_owner": "负责人",
        # findings 表
        "findings_empty": "本次扫描未发现可优化资源。",
        "th_resource": "资源",
        "th_region": "地域",
        "th_spec": "规格",
        "th_advice": "建议",
        "th_target_spec": "建议规格",
        "th_baseline": "baseline",
        "th_est_saving": "预计月省",
        # 产品聚合表
        "products_empty": "无产品级聚合数据。",
        "th_product": "产品",
        "th_flagged_count": "问题资源数",
        "th_share": "占比",
        # 保护表
        "protected_empty": "本次未发现需保护的关键资源。",
        "th_protect_reason": "保护原因",
        # 豁免表
        "exempted_empty": "本次无标签豁免资源。",
        "th_exempt_tag": "豁免标签",
        "th_note": "说明",
        # 洞察
        "insights_empty": "本次无额外洞察。",
        "level_info": "提示",
        "level_warn": "关注",
        "level_danger": "高危",
        # 严重度标签（风险四档）
        "sev_critical": "🔴 严重浪费",
        "sev_high": "🟠 高优化项",
        "sev_medium_high": "🟠 建议处理",
        "sev_medium": "🟡 中等优化",
        "sev_low": "🟢 低风险优化",
        # 前端 JS 可见词
        "js_tooltip_mid": " 条 · 预计省 ¥",
        "js_prod_label": "预计月省 (¥)",
        "js_showing_prefix": "显示 ",
        "js_showing_suffix": " 条",
    },
    "en": {
        "html_lang": "en",
        "doc_title_suffix": "Idle Resource Inspection Dashboard",
        "header_title_suffix": "Idle Resource Inspection Dashboard",
        "billing_month": "Billing Month",
        "monitor_window": "Monitoring Window",
        "window_30d": " (30 days)",
        "card_kpi": "Key Metrics",
        "card_risk": "Risk Breakdown",
        "card_findings": "All Optimization Opportunities",
        "badge_items": "items",
        "card_products": "By Product",
        "card_insights": "Insights",
        "kpi_bill_total": "Monthly Bill Total",
        "kpi_bill_month": "Billing Month",
        "kpi_est_saving": "Est. Monthly Savings",
        "kpi_of_total_bill": "of Total Bill",
        "kpi_findings": "Optimization Findings",
        "kpi_incl": "incl.",
        "kpi_critical": "critical",
        "kpi_high": "high",
        "kpi_medium": "medium",
        "kpi_low": "low risk",
        "kpi_scanned": "Resources Scanned",
        "kpi_flagged": "flagged",
        "kpi_protected": "protected",
        "kpi_exempted": "exempted",
        "chart_severity": "Severity Distribution",
        "chart_products": "Top Products by Est. Savings",
        "filter_select_all": "Select All",
        "filter_reset": "Reset",
        "col_severity": "Severity",
        "col_product": "Product",
        "col_optimize": "Action",
        "col_owner": "Owner",
        "findings_empty": "No optimization opportunities found in this scan.",
        "th_resource": "Resource",
        "th_region": "Region",
        "th_spec": "Spec",
        "th_advice": "Recommendation",
        "th_target_spec": "Target Spec",
        "th_baseline": "Baseline",
        "th_est_saving": "Est. Monthly Savings",
        "products_empty": "No product-level aggregation data.",
        "th_product": "Product",
        "th_flagged_count": "Flagged Resources",
        "th_share": "Share",
        "protected_empty": "No critical resources requiring protection.",
        "th_protect_reason": "Protection Reason",
        "exempted_empty": "No tag-exempted resources.",
        "th_exempt_tag": "Exemption Tag",
        "th_note": "Note",
        "insights_empty": "No additional insights.",
        "level_info": "INFO",
        "level_warn": "NOTICE",
        "level_danger": "CRITICAL",
        "sev_critical": "🔴 Critical Waste",
        "sev_high": "🟠 High Priority",
        "sev_medium_high": "🟠 Recommended",
        "sev_medium": "🟡 Medium Priority",
        "sev_low": "🟢 Low Risk",
        "js_tooltip_mid": " items · est. ¥",
        "js_prod_label": "Est. Monthly Savings (¥)",
        "js_showing_prefix": "Showing ",
        "js_showing_suffix": "",
    },
}


def L(lang, key):
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


def _sev_label(lang, sev):
    return L(lang, "sev_" + sev)


# ───────────── costopt 自有 finops 词汇（共享枚举字典未覆盖的部分） ─────────────
# 这些是本脚本自己生成的「优化动作 / 占位符」词，不来自上游 API、也不属于标准云产品
# 枚举，因此放在 costopt 本地、不污染团队共享字典（避免漂移）。仅 en 生效。
# ❌ 同样禁止用于翻译 owner（负责人）等业务/人名值。
_CO_LOCAL_TERMS = {
    # —— 优化路径动作词（_OPTIMIZE_PATH_MAP / fallback 产出） ——
    "退订释放": "Terminate & Release",
    "确认停用": "Confirm Decommission",
    "确认合并": "Confirm Consolidation",
    "计费优化": "Billing Optimization",
    "定时关机": "Scheduled Shutdown",
    "类型降级": "Type Downgrade",
    "存储降级": "Storage Tier-down",
    "缩减带宽": "Reduce Bandwidth",
    "释放": "Release",
    "降配": "Downsize",
    "优化": "Optimize",
    # —— 占位/默认值 ——
    "其他": "Other",
    "规格待确认": "Spec TBD",
    # —— suggestion 模板（judge_costopt.py 产出的完整建议文案） ——
    "释放实例(创建快照备份)": "Release instance (create snapshot backup first)",
    "释放实例": "Release instance",
    "释放磁盘": "Release disk",
    "释放EIP": "Release EIP",
    "转包年包月（正常使用中）": "Convert to monthly subscription (instance in active use)",
    "转包年包月（节省约40%）": "Convert to monthly subscription (est. ~40% savings)",
    "转包年包月": "Convert to monthly subscription",
    "配置定时关机策略": "Configure scheduled shutdown policy",
    "建议配置定时关机（夜间/周末停机，预计省57%）": "Configure scheduled shutdown (stop during nights/weekends, est. 57% savings)",
    "转竞价实例（SPOTPAID）": "Convert to spot instance (SPOTPAID)",
    "降配或释放": "Downsize or release",
    "降配或释放，请人工核查内存使用": "Downsize or release; verify memory usage manually",
    "降配至更小规格": "Downsize to smaller spec",
    "关停或释放（请评审）": "Shut down or release (please review)",
    "快照备份后删除": "Delete after snapshot backup",
    "确认后释放（未配置任何监听器，纯浪费）": "Release after confirmation (no listeners configured, pure waste)",
    "确认后释放（有监听器但无 RS 绑定，纯浪费）": "Release after confirmation (has listeners but no RS binding, pure waste)",
    "确认业务是否停用后释放": "Confirm if business is decommissioned then release",
    "确认业务是否可合并至其他 CLB": "Confirm if traffic can be consolidated to another CLB",
    "确认后释放": "Release after confirmation",
    "确认后销毁集群": "Destroy cluster after confirmation",
    "销毁集群": "Destroy cluster",
    "释放回收": "Release and reclaim",
    "降带宽配额（长期合同，仅供参考）": "Reduce bandwidth quota (long-term contract, for reference only)",
    "降配或缩节点": "Downsize or reduce nodes",
    "缩减节点池规模 / 启用 ClusterAutoscaler": "Scale down node pool / enable ClusterAutoscaler",
    "缩减节点池 / 启用 CA": "Scale down node pool / enable CA",
    "转标准型/Turbo标准型": "Convert to Standard/Turbo Standard type",
    "释放或降配": "Release or downsize",
    "释放或降配（长期合同，仅供参考）": "Release or downsize (long-term contract, for reference only)",
    "释放或降规格（请评审）": "Release or downsize spec (please review)",
    "退订或释放（包年包月到期前仍在计费）": "Unsubscribe or release (still billing until subscription expires)",
    "确认业务后释放或降配（长期合同，仅供参考）": "Confirm with business then release or downsize (long-term contract, for reference only)",
    "联系业务确认是否可缩减（长期合同，仅供参考）": "Contact business to confirm if reduction is possible (long-term contract, for reference only)",
    "配置生命周期转归档存储（省72%）": "Configure lifecycle rule to archive storage (est. 72% savings)",
    "配置生命周期转低频存储（省32%）": "Configure lifecycle rule to infrequent access storage (est. 32% savings)",
    "切换为关机不计费模式，或创建快照备份后释放实例": "Switch to no-charge-when-stopped mode, or release instance after snapshot backup",
    "创建快照备份后释放实例（仅磁盘继续计费，如不再使用请释放）": "Release instance after creating snapshot backup (disk continues to bill; release disk too if unused)",
    "建议转竞价实例（非生产环境，可承受中断）": "Consider converting to spot instance (non-prod environment, can tolerate interruption)",
    # —— risk 标签 ——
    "[严重] 不可逆 · 即时": "Critical · Irreversible · Immediate",
    "[严重] 可逆 · 即时": "Critical · Reversible · Immediate",
    "[严重] 建议立即处理": "Critical · Act now",
    "[高] 不可逆 · 维护窗口": "High · Irreversible · Maintenance window",
    "[高] 可逆 · 业务低峰": "High · Reversible · Off-peak",
    "[高] 可逆 · 即时": "High · Reversible · Immediate",
    "[高] 请评审后执行": "High · Review before executing",
    "[中] 不可逆 · 维护窗口": "Medium · Irreversible · Maintenance window",
    "[中] 可逆 · 业务低峰": "Medium · Reversible · Off-peak",
    "[中] 可逆 · 即时": "Medium · Reversible · Immediate",
    "[中] 关注观察": "Medium · Monitor",
    "[中] 建议处理": "Medium · Recommended action",
    "[低] 可逆 · 即时": "Low · Reversible · Immediate",
    "[低] 不可逆 · 即时": "Low · Irreversible · Immediate",
    "[低]": "Low",
    # —— coverage_note ——
    "[数据不足，仅供参考]": "[Insufficient data, for reference only]",
    "[监控数据严重不足，无法判定]": "[Severely insufficient monitoring data, cannot judge]",
    "[内存数据缺失]": "[Memory data missing]",
    "（ClusterConn 数据缺失，仅 CPU+QPS 判定）": "(ClusterConn data missing, judgment based on CPU+QPS only)",
    " 挂载但无 IO，疑似低活跃": " mounted but no IO, suspected low activity",
    # —— target_spec 模板（非规格格式部分） ——
    "已达最小规格": "Already at minimum spec",
    "已达最小规格，建议评估是否释放": "Already at minimum spec, consider releasing if unused",
    "确认停用后释放": "Release after confirming decommission",
    # —— spec 相关碎片 ——
    "降至 ": "Downscale to ",
}


def tr_co(s, lang="zh"):
    """costopt 数据值翻译：先查本地 finops 词汇（整串精确匹配），再走团队共享 tr()。

    仅翻译标准枚举/动作词；owner 等业务值绝不传入本函数。
    lang != 'en' 或空值原样返回。
    """
    if lang != "en" or not s:
        return s
    if s in _CO_LOCAL_TERMS:
        return _CO_LOCAL_TERMS[s]
    return tr(s, lang)

REGION_MAP = {
    "1": "ap-guangzhou", "4": "ap-shanghai", "5": "ap-chongqing",
    "6": "ap-shanghai-fsi", "7": "ap-beijing", "8": "ap-chengdu",
    "9": "ap-nanjing", "11": "ap-singapore", "15": "ap-hongkong",
    "16": "ap-mumbai", "17": "ap-jakarta", "19": "ap-bangkok",
    "21": "ap-seoul", "22": "ap-tokyo", "23": "eu-frankfurt",
    "24": "eu-moscow", "25": "us-siliconvalley", "26": "us-ashburn",
    "33": "ap-shanghai-adc",
}


def _infer_region(f):
    """尝试从 finding 各字段推断地域（judge_costopt.py 不一定输出 region 字段）。"""
    # 直接字段
    for k in ("region", "Region", "Placement"):
        v = f.get(k)
        if v and isinstance(v, str) and v.startswith("ap-"):
            return v
    # billing_lookup 格式: RegionId 数字 → 地域码
    rid_val = f.get("rid") or f.get("resource_id") or ""
    region_id = f.get("_billing_region_id") or ""
    if region_id and region_id in REGION_MAP:
        return REGION_MAP[region_id]
    # 从 rid 中推断（某些资源 ID 含地域标记）
    for part in rid_val.split("-"):
        if part.startswith("ap-"):
            return part
    return ""


REVERSIBLE_LABEL = {"low": "低", "mid": "中", "high": "高"}

# ─── 优化路径兜底映射（与 judge_costopt.py 保持一致）─────────
_OPTIMIZE_PATH_MAP = {
    "停机按量计费": "释放", "疑似闲置": "释放", "疑似闲置（内存数据缺失）": "释放",
    "低利用率": "降配", "按量长期运行": "计费优化", "可中断工作负载": "计费优化",
    "非生产可定时关机": "定时关机", "未挂载": "释放", "SSD低IO可降级": "类型降级",
    "闲置": "释放", "低利用率可降配": "降配", "内存低可降配": "降配",
    "无监听器": "释放", "无后端绑定": "释放", "有后端但无连接": "确认停用",
    "连接数极低": "确认合并", "未绑定": "释放", "无客户端连接": "释放",
    "近无IO": "释放", "性能型低IO可降级": "类型降级", "空集群": "释放",
    "极低访问频率": "存储降级", "低访问频率": "存储降级",
    "已终止仍计费": "退订释放", "带宽利用率低": "缩减带宽",
    "云服务器（CVM）实例利用率不足": "降配", "云数据库（MySQL）利用率不足": "降配",
}

def _issue_to_optimize_path_fallback(issue_type):
    if issue_type in _OPTIMIZE_PATH_MAP:
        return _OPTIMIZE_PATH_MAP[issue_type]
    it = (issue_type or "")
    if not it:
        return "优化"
    if "停机" in it or "闲置" in it:
        return "释放"
    if any(k in it for k in ("未挂载", "未绑定", "空集群", "无监听器", "无后端绑定", "无客户端连接", "近无IO", "已终止")):
        return "释放"
    if any(k in it for k in ("按量长期", "可中断")):
        return "计费优化"
    if "定时关机" in it:
        return "定时关机"
    if "降级" in it:
        return "类型降级"
    if any(k in it for k in ("访问频率", "归档", "低频")):
        return "存储降级"
    if any(k in it for k in ("低利用率", "降配")):
        return "降配"
    if "带宽" in it:
        return "缩减带宽"
    return it


# ───────────── 工具 ─────────────

def fmt_money(v):
    if v is None:
        return "-"
    try:
        return f"¥{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "-"


def fmt_pct(v, sign=False):
    if v is None:
        return "-"
    try:
        f = float(v)
        return f"{f:+.1f}%" if sign else f"{f:.1f}%"
    except (TypeError, ValueError):
        return "-"


def safe_json_dumps(obj):
    """嵌入 <script> 时避免 </script> 注入"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ───────────── 数据加工 ─────────────

def normalize_findings(raw_findings, lang="zh"):
    """归一化 findings 列表，回填默认字段，按严重度排序。

    枚举型数据值（product/region/spec/target_spec/advice/optimize_path）在此处统一过
    tr_co()，确保下游表格单元格、列筛选下拉、data-* 属性、图表 label 用的是同一份
    译文，不会因「只在渲染处翻译」导致筛选/图表与显示值不一致。
    ❌ owner（负责人/人名）保持原样，绝不翻译。
    """
    out = []
    for f in raw_findings or []:
        sev = (f.get("severity") or "low").lower()
        if sev not in SEVERITY_META:
            sev = "low"
        raw_path = f.get("optimize_path") or _issue_to_optimize_path_fallback(f.get("issue_type") or f.get("issue") or "")
        item = {
            "severity": sev,
            "product": tr_co((f.get("product") or "其他").strip(), lang),
            "resource_id": f.get("resource_id") or f.get("ResourceId") or f.get("rid") or "-",
            "resource_name": f.get("resource_name") or f.get("name") or "",
            "region": tr_co(f.get("region") or _infer_region(f) or "-", lang),
            "spec": tr_co(f.get("spec") or "规格待确认", lang),
            "issue": f.get("issue") or f.get("issue_type") or "-",
            "optimize_path": tr_co(raw_path, lang),
            "baseline": float(f.get("baseline") or 0),
            "saving": float(f.get("saving") or 0),
            "saving_ratio": float(f.get("saving_ratio") or 0),
            "reversible": (f.get("reversible") or "mid").lower(),
            "timing": f.get("timing") or "-",
            "owner": f.get("owner") or "-",
            "advice": tr_co(f.get("advice") or f.get("suggestion") or "-", lang),
            "target_spec": tr_co(f.get("target_spec") or "-", lang),
            "console_url": f.get("console_url") or "",
        }
        out.append(item)
    out.sort(key=lambda x: (SEVERITY_META[x["severity"]]["order"], -x["saving"]))
    return out


def aggregate_by_product(findings):
    """按产品聚合：资源数 + 预计月省。"""
    bucket = {}
    for f in findings:
        p = f["product"]
        b = bucket.setdefault(p, {"product": p, "count": 0, "saving": 0.0})
        b["count"] += 1
        b["saving"] += f["saving"]
    rows = sorted(bucket.values(), key=lambda r: -r["saving"])
    return rows


def severity_breakdown(findings):
    """风险等级分布。"""
    counts = {k: 0 for k in SEVERITY_META}
    savings = {k: 0.0 for k in SEVERITY_META}
    for f in findings:
        counts[f["severity"]] += 1
        savings[f["severity"]] += f["saving"]
    return counts, savings


def build_data(raw, lang="zh"):
    """规整化输入数据，回填默认值。枚举型数据值在归一化阶段统一翻译（en 生效）。"""
    # 激活 API 动态注入层：把 step3 JSON 中的 product_code → 中文名 反查 _BIZCODE_EN_MAP，
    # 让 tr() 能自动翻译产品名，无需在静态字典里穷举。
    if lang == "en":
        product_code_to_name = raw.get("product_code_to_name")
        if product_code_to_name:
            inject_bizcode_map(product_code_to_name)
    findings = normalize_findings(raw.get("findings"), lang)
    totals = raw.get("totals") or {}
    counts, savings = severity_breakdown(findings)
    products = aggregate_by_product(findings)
    # 兼容 judge_costopt.py 字段命名（findings_count / total_saving）
    finding_count = totals.get("finding_count")
    if finding_count is None:
        finding_count = totals.get("findings_count")
    if finding_count is None:
        finding_count = len(findings)
    estimated_saving = totals.get("estimated_saving")
    if estimated_saving is None:
        estimated_saving = totals.get("total_saving")
    if estimated_saving is None:
        estimated_saving = sum(savings.values())
    total_bill = totals.get("total_bill_cost") or totals.get("total_cost") or 0
    protected_count = int(totals.get("protected_count")
                           or len(raw.get("protected") or []))
    exempted_count = int(totals.get("exempted_count")
                          or len(raw.get("exempted") or []))
    return {
        "account_name": raw.get("account_name") or "腾讯云账户",
        "uin": str(raw.get("uin") or ""),
        "bill_month": raw.get("bill_month") or "",
        "monitor_window": raw.get("monitor_window") or {},
        "totals": {
            "total_bill_cost": float(total_bill or 0),
            "estimated_saving": float(estimated_saving or 0),
            "finding_count": int(finding_count or 0),
            "protected_count": protected_count,
            "exempted_count": exempted_count,
            "scanned_count": int(finding_count or 0) + protected_count + exempted_count,
        },
        "severity_counts": counts,
        "severity_savings": savings,
        "products": products,
        "findings": findings,
        "protected": raw.get("protected") or [],
        "exempted": raw.get("exempted") or [],
        "insights": raw.get("insights") or [],
    }


# ───────────── HTML 模板 ─────────────

BASE_STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", "Microsoft YaHei", sans-serif;
  background: linear-gradient(135deg, #0b1020 0%, #131933 100%);
  color: #f5f6fb;
  min-height: 100vh;
  padding: 40px 32px;
  line-height: 1.6;
}
.container { max-width: 1280px; margin: 0 auto; }
header { margin-bottom: 32px; }
h1 { font-size: 32px; font-weight: 700; margin: 0 0 8px 0; letter-spacing: 0.5px; }
.subtitle { color: rgba(245,246,251,0.65); font-size: 14px; }
.subtitle span { color: rgba(245,246,251,0.45); margin-left: 12px; }

.card {
  background: rgba(255,255,255,0.04);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 14px;
  padding: 24px;
  margin-bottom: 32px;
}
.card-title { font-size: 18px; font-weight: 600; margin: 0 0 18px 0; display: flex; align-items: center; gap: 8px; }
.card-title .badge { font-size: 12px; font-weight: 500; padding: 2px 10px; border-radius: 999px; background: rgba(122,162,255,0.18); color: #7aa2ff; }

.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.kpi {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px; padding: 20px 22px;
}
.kpi-label { color: rgba(245,246,251,0.6); font-size: 13px; margin-bottom: 8px; }
.kpi-value { font-size: 28px; font-weight: 700; font-family: "SF Mono", "JetBrains Mono", Consolas, Menlo, monospace; }
.kpi-sub { color: rgba(245,246,251,0.55); font-size: 12px; margin-top: 6px; }
.kpi-value.saving { color: #4ade80; }
.kpi-value.risk { color: #ff6b6b; }
.kpi-value.shield { color: #7aa2ff; }

.charts-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 24px; }
.chart-box { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 18px; }
.chart-title { font-size: 14px; font-weight: 600; color: rgba(245,246,251,0.85); margin-bottom: 12px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); }
th { color: rgba(245,246,251,0.65); font-weight: 600; font-size: 12px; background: rgba(255,255,255,0.03); position: sticky; top: 0; z-index: 1; white-space: nowrap; }
td.num { font-family: "SF Mono", "JetBrains Mono", Consolas, Menlo, monospace; text-align: right; }
.scroll-table { max-height: 460px; overflow-y: auto; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }

.sev { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.sev-critical { background: rgba(255,107,107,0.18); color: #ff6b6b; }
.sev-high { background: rgba(251,191,36,0.18); color: #fbbf24; }
.sev-medium_high { background: rgba(251,146,36,0.18); color: #fb9224; }
.sev-medium { background: rgba(250,204,21,0.16); color: #facc15; }
.sev-low { background: rgba(74,222,128,0.16); color: #4ade80; }

.saving-cell { color: #4ade80; font-weight: 600; }
.tag-cell { display: inline-block; padding: 1px 8px; margin-right: 4px; background: rgba(122,162,255,0.15); color: #7aa2ff; font-size: 12px; border-radius: 6px; }
.tag-cell.warn { background: rgba(251,191,36,0.18); color: #fbbf24; }
.muted { color: rgba(245,246,251,0.45); }

.insight-card { background: rgba(255,255,255,0.03); border-left: 3px solid #7aa2ff; padding: 10px 14px; margin-bottom: 10px; border-radius: 6px; }
.insight-card.warn { border-left-color: #fbbf24; }
.insight-card.danger { border-left-color: #ff6b6b; }
.insight-card .level { display: inline-block; font-size: 12px; padding: 0 8px; border-radius: 4px; margin-right: 8px; background: rgba(122,162,255,0.18); color: #7aa2ff; }
.insight-card.warn .level { background: rgba(251,191,36,0.18); color: #fbbf24; }
.insight-card.danger .level { background: rgba(255,107,107,0.18); color: #ff6b6b; }

.section-heading { display: flex; align-items: center; gap: 10px; margin: 0 0 14px 0; font-size: 15px; font-weight: 600; }
.section-heading .count { color: rgba(245,246,251,0.55); font-size: 13px; font-weight: 500; }

/* 列筛选器 — 集成在表头 */
.col-filter-wrap { display: flex; align-items: center; justify-content: space-between; gap: 4px; }
.col-filter-btn {
  background: none; border: none; color: rgba(245,246,251,0.4); font-size: 10px; cursor: pointer;
  padding: 2px 4px; border-radius: 4px; line-height: 1;
}
.col-filter-btn:hover { color: #7aa2ff; background: rgba(122,162,255,0.1); }
.col-filter-btn.active { color: #7aa2ff; }
.col-filter-dropdown {
  position: absolute; top: calc(100% + 2px); left: 0; min-width: 180px; max-width: 260px;
  background: #1a1f35; border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
  padding: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); z-index: 100;
  display: none; max-height: 280px; overflow-y: auto; text-align: left;
}
.col-filter-dropdown.open { display: block; }
.col-filter-dropdown label {
  display: flex; align-items: center; gap: 8px; padding: 4px 6px;
  font-size: 13px; color: rgba(245,246,251,0.8); cursor: pointer; border-radius: 6px;
  white-space: nowrap;
}
.col-filter-dropdown label:hover { background: rgba(255,255,255,0.06); }
.col-filter-dropdown input[type="checkbox"] { accent-color: #7aa2ff; }
.col-filter-dropdown .filter-actions {
  display: flex; gap: 8px; padding: 8px 6px 2px; border-top: 1px solid rgba(255,255,255,0.08); margin-top: 4px;
}
.col-filter-dropdown .filter-actions button {
  flex: 1; background: rgba(122,162,255,0.15); border: none; border-radius: 6px;
  padding: 4px 0; color: #7aa2ff; font-size: 12px; cursor: pointer;
}
.col-filter-dropdown .filter-actions button:last-child { background: rgba(255,255,255,0.06); color: rgba(245,246,251,0.6); }
.col-filter-input {
  background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
  border-radius: 6px; padding: 3px 8px; color: rgba(245,246,251,0.85);
  font-size: 11px; width: 100%; outline: none; margin-top: 4px;
}
.col-filter-input::placeholder { color: rgba(245,246,251,0.4); }
.filter-result-count { color: rgba(245,246,251,0.55); font-size: 12px; margin-left: auto; }
"""


def render_kpis(d, lang="zh"):
    t = d["totals"]
    return f"""
    <div class="kpi-grid">
      <div class="kpi">
        <div class="kpi-label">{L(lang,'kpi_bill_total')}</div>
        <div class="kpi-value">{fmt_money(t['total_bill_cost'])}</div>
        <div class="kpi-sub">{L(lang,'kpi_bill_month')} {escape(d['bill_month'] or '-')}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">{L(lang,'kpi_est_saving')}</div>
        <div class="kpi-value saving">{fmt_money(t['estimated_saving'])}</div>
        <div class="kpi-sub">{L(lang,'kpi_of_total_bill')} {fmt_pct(t['estimated_saving'] / t['total_bill_cost'] * 100 if t['total_bill_cost'] else 0)}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">{L(lang,'kpi_findings')}</div>
        <div class="kpi-value risk">{t['finding_count']}</div>
        <div class="kpi-sub">{L(lang,'kpi_incl')} {d['severity_counts'].get('critical',0)} {L(lang,'kpi_critical')} / {d['severity_counts'].get('high',0)} {L(lang,'kpi_high')} / {d['severity_counts'].get('medium_high',0) + d['severity_counts'].get('medium',0)} {L(lang,'kpi_medium')} / {d['severity_counts'].get('low',0)} {L(lang,'kpi_low')}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">{L(lang,'kpi_scanned')}</div>
        <div class="kpi-value shield">{t['scanned_count']}</div>
        <div class="kpi-sub">{d['severity_counts'].get('critical',0) + d['severity_counts'].get('high',0) + d['severity_counts'].get('medium_high',0) + d['severity_counts'].get('medium',0) + d['severity_counts'].get('low',0)} {L(lang,'kpi_flagged')} / {t['protected_count']} {L(lang,'kpi_protected')} / {t['exempted_count']} {L(lang,'kpi_exempted')}</div>
      </div>
    </div>
    """


def render_severity_chart_box(d, lang="zh"):
    return f"""
    <div class="chart-box">
      <div class="chart-title">{L(lang,'chart_severity')}</div>
      <div style="position: relative; height: 280px;"><canvas id="sevChart"></canvas></div>
    </div>
    """


def render_product_chart_box(d, lang="zh"):
    return f"""
    <div class="chart-box">
      <div class="chart-title">{L(lang,'chart_products')}</div>
      <div style="position: relative; height: 280px;"><canvas id="productChart"></canvas></div>
    </div>
    """


def _render_col_filter(opts_html, filter_id, col_label, lang="zh", has_input=False, input_placeholder=""):
    """渲染集成在表头列里的筛选器。"""
    input_html = f'<input type="text" class="col-filter-input" id="{filter_id}-input" placeholder="{input_placeholder}" oninput="applyFilters()">' if has_input else ""
    return f"""
    <div class="col-filter-wrap">
      <span>{col_label}</span>
      <button class="col-filter-btn" onclick="toggleColFilter('{filter_id}', this)" id="{filter_id}-btn">▼</button>
    </div>
    <div class="col-filter-dropdown" id="{filter_id}-dropdown">
      {opts_html}
      <div class="filter-actions"><button onclick="selectAll('{filter_id}')">{L(lang,'filter_select_all')}</button><button onclick="clearFilter('{filter_id}')">{L(lang,'filter_reset')}</button></div>
      {input_html}
    </div>
    """


def render_findings_table(d, lang="zh"):
    if not d["findings"]:
        return f'<p class="muted">{L(lang,"findings_empty")}</p>'

    # 收集不重复值用于筛选下拉
    sev_vals = sorted({f["severity"] for f in d["findings"]}, key=lambda s: SEVERITY_META[s]["order"])
    prod_vals = sorted({f["product"] for f in d["findings"]})
    owner_vals = sorted({f["owner"] for f in d["findings"] if f["owner"] and f["owner"] != "-"})
    path_vals = sorted({f["optimize_path"] for f in d["findings"] if f.get("optimize_path") and f["optimize_path"] != "优化"})

    rows = []
    for f in d["findings"]:
        sev = f["severity"]
        sev_label = _sev_label(lang, sev)
        console = f["console_url"]
        rid_html = (
            f'<a href="{escape(console)}" target="_blank" style="color: #7aa2ff; text-decoration: none;">{escape(f["resource_id"])}</a>'
            if console else escape(f["resource_id"])
        )
        optimize_path = f.get("optimize_path") or "优化"
        advice = f.get("advice") or "-"
        target_spec = f.get("target_spec") or "-"
        rows.append(f"""
        <tr data-severity="{escape(sev)}" data-product="{escape(f['product'])}"
            data-owner="{escape(f['owner'])}" data-optimize-path="{escape(optimize_path)}">
          <td><span class="sev sev-{sev}">{sev_label}</span></td>
          <td>{escape(f["product"])}</td>
          <td>{rid_html}<div class="muted" style="font-size:12px;">{escape(f["resource_name"] or "")}</div></td>
          <td>{escape(f["region"])}</td>
          <td>{escape(f["spec"])}</td>
          <td><span class="tag-cell">{escape(optimize_path)}</span></td>
          <td>{escape(advice)}</td>
          <td>{escape(target_spec)}</td>
          <td class="num">{fmt_money(f["baseline"])}</td>
          <td class="num saving-cell">{fmt_money(f["saving"])}</td>
          <td>{escape(f["owner"])}</td>
        </tr>
        """)

    # 生成 checkbox 选项 HTML
    def _opts(vals, cls):
        return "".join(f'<label><input type="checkbox" class="{cls}" value="{escape(v)}">{escape(v)}</label>' for v in vals)

    sev_opts = "".join(
        f'<label><input type="checkbox" class="filter-sev" value="{escape(v)}">{_sev_label(lang, v)}</label>'
        for v in sev_vals
    )
    prod_opts = _opts(prod_vals, "filter-prod")
    owner_opts = _opts(owner_vals, "filter-owner")
    path_opts = _opts(path_vals, "filter-path")

    # 表头筛选器 HTML
    sev_filter = _render_col_filter(sev_opts, "sev", L(lang, "col_severity"), lang)
    prod_filter = _render_col_filter(prod_opts, "prod", L(lang, "col_product"), lang)
    path_filter = _render_col_filter(path_opts, "path", L(lang, "col_optimize"), lang)
    owner_filter = _render_col_filter(owner_opts, "owner", L(lang, "col_owner"), lang)

    return f"""
    <div class="scroll-table">
      <table id="findings-table">
        <thead>
          <tr>
            <th style="position:relative;">{sev_filter}</th>
            <th style="position:relative;">{prod_filter}</th>
            <th>{L(lang,'th_resource')}</th>
            <th>{L(lang,'th_region')}</th>
            <th>{L(lang,'th_spec')}</th>
            <th style="position:relative;">{path_filter}</th>
            <th>{L(lang,'th_advice')}</th>
            <th>{L(lang,'th_target_spec')}</th>
            <th>{L(lang,'th_baseline')}</th>
            <th>{L(lang,'th_est_saving')}</th>
            <th style="position:relative;">{owner_filter}</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    <div style="text-align:right; margin-top:8px;">
      <span class="filter-result-count" id="filter-count"></span>
    </div>
    """


def render_products_table(d, lang="zh"):
    if not d["products"]:
        return f'<p class="muted">{L(lang,"products_empty")}</p>'
    total_saving = d["totals"]["estimated_saving"] or 1
    rows = []
    for p in d["products"]:
        share = p["saving"] / total_saving * 100 if total_saving else 0
        rows.append(f"""
        <tr>
          <td>{escape(p["product"])}</td>
          <td class="num">{p["count"]}</td>
          <td class="num saving-cell">{fmt_money(p["saving"])}</td>
          <td class="num">{fmt_pct(share)}</td>
        </tr>
        """)
    return f"""
    <div class="scroll-table">
      <table>
        <thead><tr><th>{L(lang,'th_product')}</th><th>{L(lang,'th_flagged_count')}</th><th>{L(lang,'th_est_saving')}</th><th>{L(lang,'th_share')}</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_protected_table(d, lang="zh"):
    items = d["protected"]
    if not items:
        return f'<p class="muted">{L(lang,"protected_empty")}</p>'
    rows = []
    for p in items:
        rows.append(f"""
        <tr>
          <td>{escape(p.get('product') or '-')}</td>
          <td>{escape(str(p.get('resource_id') or p.get('rid') or '-'))}</td>
          <td>{escape(p.get('spec') or '-')}</td>
          <td>{escape(p.get('region') or '-')}</td>
          <td>{escape(p.get('reason') or '-')}</td>
        </tr>
        """)
    return f"""
    <div class="scroll-table">
      <table>
        <thead><tr><th>{L(lang,'th_product')}</th><th>{L(lang,'th_resource')}</th><th>{L(lang,'th_spec')}</th><th>{L(lang,'th_region')}</th><th>{L(lang,'th_protect_reason')}</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_exempted_table(d, lang="zh"):
    items = d["exempted"]
    if not items:
        return f'<p class="muted">{L(lang,"exempted_empty")}</p>'
    rows = []
    for p in items:
        rows.append(f"""
        <tr>
          <td>{escape(p.get('product') or '-')}</td>
          <td>{escape(str(p.get('resource_id') or p.get('rid') or '-'))}</td>
          <td><span class="tag-cell warn">{escape(p.get('tag') or p.get('exemption_type') or 'finops-skip')}</span></td>
          <td>{escape(p.get('reason') or '-')}</td>
        </tr>
        """)
    return f"""
    <div class="scroll-table">
      <table>
        <thead><tr><th>{L(lang,'th_product')}</th><th>{L(lang,'th_resource')}</th><th>{L(lang,'th_exempt_tag')}</th><th>{L(lang,'th_note')}</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_insights(d, lang="zh"):
    items = d["insights"]
    if not items:
        return f'<p class="muted">{L(lang,"insights_empty")}</p>'
    blocks = []
    for it in items:
        level = (it.get("level") or "info").lower()
        cls = "" if level == "info" else level
        label = {"info": L(lang, "level_info"), "warn": L(lang, "level_warn"), "danger": L(lang, "level_danger")}.get(level, L(lang, "level_info"))
        blocks.append(f'<div class="insight-card {cls}"><span class="level">{label}</span>{escape(it.get("text") or "")}</div>')
    return "".join(blocks)


def render_html(data, *, offline_chartjs_path=None, lang="zh"):
    d = build_data(data, lang)
    mw = d["monitor_window"]
    win_str = ""
    if mw.get("start") and mw.get("end"):
        win_str = f"{L(lang,'monitor_window')} {escape(mw['start'])} ~ {escape(mw['end'])}{L(lang,'window_30d')}"

    chart_script = ""
    if offline_chartjs_path and os.path.exists(offline_chartjs_path):
        with open(offline_chartjs_path, "r", encoding="utf-8") as fp:
            chart_script = f"<script>{fp.read()}</script>"
    else:
        chart_script = f'<script src="{CHART_JS_CDN}"></script>'

    sev_counts = d["severity_counts"]
    sev_savings = d["severity_savings"]
    chart_data = {
        "severity": {
            "labels": [_sev_label(lang, k) for k in ("critical", "high", "medium_high", "medium", "low")],
            "counts": [sev_counts.get(k, 0) for k in ("critical", "high", "medium_high", "medium", "low")],
            "savings": [int(round(sev_savings.get(k, 0))) for k in ("critical", "high", "medium_high", "medium", "low")],
            "colors": [SEVERITY_META[k]["color"] for k in ("critical", "high", "medium_high", "medium", "low")],
        },
        "products": {
            "labels": [p["product"] for p in d["products"][:12]],
            "savings": [int(round(p["saving"])) for p in d["products"][:12]],
        },
    }

    js_i18n = {
        "tooltipMid": L(lang, "js_tooltip_mid"),
        "prodLabel": L(lang, "js_prod_label"),
        "showingPrefix": L(lang, "js_showing_prefix"),
        "showingSuffix": L(lang, "js_showing_suffix"),
    }

    html_parts = [
        "<!DOCTYPE html>",
        f'<html lang="{L(lang,"html_lang")}"><head>',
        '<meta charset="utf-8">',
        f'<title>{escape(d["account_name"])} {escape(d["bill_month"])} {L(lang,"doc_title_suffix")}</title>',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<style>{BASE_STYLE}</style>",
        chart_script,
        "</head><body>",
        '<div class="container">',
        "<header>",
        f'<h1>{escape(d["account_name"])} · {L(lang,"header_title_suffix")}</h1>',
        f'<div class="subtitle">{L(lang,"billing_month")} {escape(d["bill_month"] or "-")} <span>{win_str}</span></div>',
        "</header>",

        # KPI
        '<div class="card">',
        f'<h2 class="card-title">{L(lang,"card_kpi")} <span class="badge">KPI</span></h2>',
        render_kpis(d, lang),
        "</div>",

        # Charts
        '<div class="card">',
        f'<h2 class="card-title">{L(lang,"card_risk")}</h2>',
        '<div class="charts-grid">',
        render_severity_chart_box(d, lang),
        render_product_chart_box(d, lang),
        "</div></div>",

        # Findings 全量表
        '<div class="card">',
        f'<h2 class="card-title">{L(lang,"card_findings")} <span class="badge">{d["totals"]["finding_count"]} {L(lang,"badge_items")}</span></h2>',
        render_findings_table(d, lang),
        "</div>",

        # 产品聚合
        '<div class="card">',
        f'<h2 class="card-title">{L(lang,"card_products")}</h2>',
        render_products_table(d, lang),
        "</div>",

        # Insights
        '<div class="card">',
        f'<h2 class="card-title">{L(lang,"card_insights")}</h2>',
        render_insights(d, lang),
        "</div>",

        "</div>",
        "<script>",
        f"const COSTOPT_CHART_DATA = {safe_json_dumps(chart_data)};",
        f"const COSTOPT_I18N = {safe_json_dumps(js_i18n)};",
        """
        (function(){
          if (typeof Chart === 'undefined') return;
          Chart.defaults.color = 'rgba(245,246,251,0.7)';
          Chart.defaults.borderColor = 'rgba(255,255,255,0.08)';
          Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif';
          const sev = COSTOPT_CHART_DATA.severity;
          const prod = COSTOPT_CHART_DATA.products;
          new Chart(document.getElementById('sevChart'), {
            type: 'doughnut',
            data: {
              labels: sev.labels,
              datasets: [{ data: sev.counts, backgroundColor: sev.colors, borderColor: 'rgba(0,0,0,0)', borderWidth: 0 }]
            },
            options: {
              responsive: true, maintainAspectRatio: false, cutout: '60%',
              plugins: { legend: { position: 'right' }, tooltip: { callbacks: {
                label: function(ctx){
                  const i = ctx.dataIndex;
                  return ctx.label + ': ' + ctx.parsed + COSTOPT_I18N.tooltipMid + sev.savings[i].toLocaleString();
                }
              } } }
            }
          });
          new Chart(document.getElementById('productChart'), {
            type: 'bar',
            data: {
              labels: prod.labels,
              datasets: [{
                label: COSTOPT_I18N.prodLabel,
                data: prod.savings,
                backgroundColor: 'rgba(74,222,128,0.7)',
                borderColor: 'rgba(74,222,128,0.9)',
                borderWidth: 1, borderRadius: 4
              }]
            },
            options: {
              responsive: true, maintainAspectRatio: false,
              scales: {
                x: { grid: { display: false } },
                y: { ticks: { callback: v => '¥' + Number(v).toLocaleString() } }
              },
              plugins: { legend: { display: false } }
            }
          });
        })();

        // ─── 表格筛选逻辑 ───
        (function(){
          function getCheckedValues(cls) {
            return Array.from(document.querySelectorAll('.' + cls + ':checked')).map(cb => cb.value);
          }
          function setBtnActive(btnId, active) {
            const btn = document.getElementById(btnId);
            if (btn) btn.classList.toggle('active', active);
          }
          window.toggleColFilter = function(filterId, btn) {
            const dd = document.getElementById(filterId + '-dropdown');
            if (!dd) return;
            const wasOpen = dd.classList.contains('open');
            document.querySelectorAll('.col-filter-dropdown.open').forEach(d => d.classList.remove('open'));
            document.querySelectorAll('.col-filter-btn.active').forEach(b => b.classList.remove('active'));
            if (!wasOpen) {
              dd.classList.add('open');
              btn.classList.add('active');
            }
          };
          window.selectAll = function(prefix) {
            document.querySelectorAll('.filter-' + prefix).forEach(cb => cb.checked = true);
            applyFilters();
          };
          window.clearFilter = function(prefix) {
            document.querySelectorAll('.filter-' + prefix).forEach(cb => cb.checked = false);
            applyFilters();
          };
          window.applyFilters = function() {
            const sevChecked = getCheckedValues('filter-sev');
            const prodChecked = getCheckedValues('filter-prod');
            const ownerChecked = getCheckedValues('filter-owner');
            const pathChecked = getCheckedValues('filter-path');

            setBtnActive('sev-btn', sevChecked.length > 0);
            setBtnActive('prod-btn', prodChecked.length > 0);
            setBtnActive('owner-btn', ownerChecked.length > 0);
            setBtnActive('path-btn', pathChecked.length > 0);

            const rows = document.querySelectorAll('#findings-table tbody tr');
            let visible = 0;
            rows.forEach(row => {
              const sev = row.dataset.severity || '';
              const prod = row.dataset.product || '';
              const owner = row.dataset.owner || '';
              const path = row.dataset.optimizePath || '';

              let show = true;
              if (sevChecked.length > 0 && !sevChecked.includes(sev)) show = false;
              if (prodChecked.length > 0 && !prodChecked.includes(prod)) show = false;
              if (ownerChecked.length > 0 && !ownerChecked.includes(owner)) show = false;
              if (pathChecked.length > 0 && !pathChecked.includes(path)) show = false;

              row.style.display = show ? '' : 'none';
              if (show) visible++;
            });

            const total = rows.length;
            const cntEl = document.getElementById('filter-count');
            if (cntEl) cntEl.textContent = COSTOPT_I18N.showingPrefix + visible + ' / ' + total + COSTOPT_I18N.showingSuffix;
          };

          // 监听 checkbox 变化
          ['filter-sev', 'filter-prod', 'filter-owner', 'filter-path'].forEach(cls => {
            document.querySelectorAll('.' + cls).forEach(cb => {
              cb.addEventListener('change', applyFilters);
            });
          });

          // 点击外部关闭下拉
          document.addEventListener('click', function(e) {
            if (!e.target.closest('.col-filter-wrap') && !e.target.closest('.col-filter-dropdown')) {
              document.querySelectorAll('.col-filter-dropdown.open').forEach(d => d.classList.remove('open'));
              document.querySelectorAll('.col-filter-btn.active').forEach(b => b.classList.remove('active'));
            }
          });

          applyFilters();
        })();
        """,
        "</script>",
        "</body></html>",
    ]
    return "\n".join(html_parts)


# ───────────── CLI ─────────────

def main():
    ap = argparse.ArgumentParser(description="Render costopt JSON to single-file HTML dashboard")
    ap.add_argument("--data", required=True, help="step3_results.json 路径")
    ap.add_argument("--output", required=True, help="输出 HTML 路径")
    ap.add_argument("--account-name", default="", help="账户名（覆盖 JSON 字段）")
    ap.add_argument("--offline", default="", help="离线 Chart.js 路径（可选）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="报告语言（zh/en）")
    args = ap.parse_args()

    # 在任何 tr() 调用之前注册 LLM 兜底钩子
    if args.lang == "en":
        set_llm_hook(create_llm_hook())

    with open(args.data, "r", encoding="utf-8") as fp:
        raw = json.load(fp)
    if args.account_name:
        raw["account_name"] = args.account_name

    html = render_html(raw, offline_chartjs_path=args.offline or None, lang=args.lang)
    # Resolve output path: if relative, prefix with WORKBUDDY_WORKSPACE (ensures artifacts panel visibility)
    out_path = args.output
    if not os.path.isabs(out_path):
        ws = os.environ.get("WORKBUDDY_WORKSPACE")
        if ws:
            out_path = os.path.join(ws, out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(html)
    print(json.dumps({
        "status": "ok",
        "output": os.path.abspath(out_path),
        "size_bytes": len(html.encode("utf-8")),
        "finding_count": len(raw.get("findings") or []),
    }, ensure_ascii=False))

    # 持久化 LLM 翻译缓存
    if args.lang == "en":
        _flush_llm_cache()


if __name__ == "__main__":
    main()
