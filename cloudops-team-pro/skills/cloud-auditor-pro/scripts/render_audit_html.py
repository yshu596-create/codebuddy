#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_audit_html.py — 把 inspect_audit_events.py 的 JSON 渲染为单文件 HTML 审计看板

输入:
  --data /path/to/inspect_audit_events.json   # inspect_audit_events.py 的输出
  --output /path/to/audit-{cloud}-{project}-{uin}.html  # 落盘路径(不指定则打印到 stdout)
  --account "<账户昵称>"                        # 账户昵称(报告头展示,可选,默认回落到 UIN)
  --uin "$INSPECTION_UIN"                      # 主账号 UIN(报告头展示,可选,通常由 cam.GetUserAppId 自动派生)
  --offline /path/to/chart.umd.min.js          # 内嵌 Chart.js(离线场景)

输出:
  单文件 HTML(深色玻璃拟态看板 + Chart.js + 全量事件展示)
  · 默认通过 jsdelivr CDN 引入 Chart.js
  · --offline 选项内嵌 Chart.js(约 +250KB,离线场景用)

设计原则(对齐 tencent-billing-analyst):
  · 全量展示:真高危事件明细全数出,长表 max-height 滚动;不做 Top N 截断
  · 服务账号聚合一行,不逐条展开(降噪)
  · 不输出 Markdown;单文件无外链资源(除 Chart.js CDN 一行 script)
  · AK/SK 绝不出现(脚本上游已脱敏 secret_id_masked)

字段契约(来自 inspect_audit_events.py):
  time_window: { start, end, days, start_iso, end_iso }
  scan_status: "ok" / "denied"
  events_by_category: { <category>: [ {event_name, event_name_cn, event_time,
                        event_source, username, source_ip, region, error_code,
                        resource_name, resource_type, secret_id_masked,
                        request_id}, ... ] }
  summary_by_principal: { <username>: {total, events:{en:cnt}, is_service} }
  service_account_set: [ <username>, ... ]
  category_risk: { <category>: "critical"/"high"/"medium" }
  total_events: int
  total_categories_hit: int
"""

import argparse
import json
import os
import sys
import time
from html import escape

# ── shared team i18n dictionary (single source of truth) ──
# Anchor by file path (parents[2]: scripts -> skill -> skills root) so import is
# robust regardless of the cwd each skill is invoked from.
from pathlib import Path as _Path
_LIB = _Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from i18n import tr, scan_residual_chinese, get_translation_report, set_llm_hook, create_llm_hook, _flush_llm_cache  # shared zh->en enum translator + residual scanner + audit report + LLM fallback
except Exception:
    def tr(s, lang="zh"):  # graceful fallback if shared module is unavailable
        return s
    scan_residual_chinese = lambda html, lang="zh": []
    get_translation_report = lambda: {"miss_count": 0, "misses": [], "suggested_entries": []}
    set_llm_hook = lambda hook: None
    create_llm_hook = lambda: None
    _flush_llm_cache = lambda: None

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"

# ───────────── i18n(中英双语 UI 文案) ─────────────
# 仅覆盖用户可见文案;API 数据值(EventName/用户名/IP/资源ID/时间戳)与技术标识不翻译。

I18N = {
    "zh": {
        # 类别名(9 大类)
        "cat_audit_tamper": "审计反取证",
        "cat_ak_lifecycle": "AK 生命周期",
        "cat_permission_grant": "权限授予",
        "cat_resource_destroy": "资源毁灭",
        "cat_kms_tamper": "KMS 主密钥",
        "cat_mfa_change": "MFA 变更",
        "cat_user_lifecycle": "用户 CRUD",
        "cat_role_change": "角色 CRUD",
        "cat_sg_change": "安全组改动",
        # 风险等级
        "risk_critical": "严重",
        "risk_high": "高",
        "risk_medium": "中",
        "risk_low": "低",
        # 通用
        "default_account": "腾讯云账户",
        "empty_value": "(空)",
        "days_unit": "天",
        # 洞察 - 汇总条
        "ins_sum_bad": "严重项",
        "ins_sum_warn": "关注项",
        "ins_sum_info": "提示项",
        "ins_sum_good": "正向项",
        # 洞察 - 扫描受限
        "ins_denied_title": "扫描权限受限",
        "ins_denied_badge": "需复核",
        "ins_denied_desc": "当前凭证调用 DescribeEvents 全量失败,可能子账号缺少 audit:DescribeEvents 权限。",
        "ins_denied_action": "为巡检子账号补充 ReadOnlyAccess 或 audit 只读权限后重跑。",
        # 洞察 - 审计反取证
        "ins_tamper_title": "审计反取证动作",
        "ins_tamper_badge": "严重",
        "ins_tamper_metric_hit": "命中事件",
        "ins_tamper_desc": "检测到关停/删除/篡改审计跟踪集的动作,这是攻击者反取证的典型标志。",
        "ins_tamper_action": "立即核实操作人与来源 IP 是否授权,确认审计日志投递是否被中断。",
        # 洞察 - 真高危盘点
        "ins_real_title": "真高危事件盘点",
        "ins_real_badge_dense": "高危密集",
        "ins_real_badge_exist": "存在高危",
        "ins_real_badge_mid": "均为中危",
        "ins_real_badge_clean": "整洁",
        "ins_metric_real_high": "真高危事件",
        "ins_metric_crit_high": "严重/高",
        "ins_metric_window": "时间窗",
        "ins_real_desc": "扣除服务账号噪声后,近 {days} 天共 {total} 条需关注的人为高危操作。",
        "ins_real_action": "优先核对「严重/高」事件的操作人、来源 IP 与资源是否在授权范围内。",
        "ins_real_desc_clean": "近 {days} 天未发现非服务账号的高危操作,事件流整洁。",
        "ins_real_action_clean": "保持周巡检节奏即可,可将窗口设为 1 天配合实时告警。",
        # 洞察 - 操作人集中
        "ins_conc_title": "操作人高危集中",
        "ins_conc_badge": "重点关注",
        "ins_metric_operator": "操作人",
        "ins_metric_high_events": "高危事件",
        "ins_conc_desc": "{user} 贡献了 {cnt} 条高危操作,集中度较高。",
        "ins_conc_action": "核对该身份近期是否被盗用或越权,必要时轮换其 AK 并启用 MFA。",
        # 洞察 - 来源 IP 分散
        "ins_ip_title": "来源 IP 分散",
        "ins_ip_badge": "多来源",
        "ins_metric_distinct_ip": "不同来源 IP",
        "ins_ip_desc": "高危操作来自 {n} 个不同 IP,需确认是否均为可信办公/跳板网络。",
        "ins_ip_action": "结合 CAM 登录保护与 IP 白名单,收敛高危操作的可信来源范围。",
        # 洞察 - 服务账号降噪
        "ins_svc_title": "服务账号已降噪",
        "ins_svc_badge": "{n} 个",
        "ins_metric_svc_acct": "服务账号",
        "ins_metric_agg_events": "聚合事件",
        "ins_svc_desc": "已识别 {n} 个服务/自动化账号,其 {total} 条事件聚合展示,不计入真高危明细。",
        "ins_svc_action": "如需逐条审计某服务账号,可单独定向取证(scope=forensic)。",
        # HTML - 报告头/状态
        "html_lang": "zh-CN",
        "title_suffix": "云审计高危事件分析",
        "status_denied": "⚠ 扫描受限",
        "status_ok": "✓ 扫描完成",
        "scan_window": "扫描窗口",
        "near_days": "(近 {days} 天)",
        "datasource": "数据源 · 腾讯云 CloudAudit 事件流",
        # HTML - KPI
        "kpi_total": "事件总量",
        "kpi_total_delta": "含服务账号噪声",
        "kpi_cats": "命中类别",
        "kpi_cats_delta": "9 大高危类别",
        "kpi_real": "真高危事件",
        "kpi_real_delta": "已扣除服务账号",
        "kpi_crit": "严重 / 高",
        "kpi_crit_delta": "需优先复核",
        # HTML - 卡片标题与表头
        "card_cat_dist": "高危类别分布",
        "card_cat_dist_meta": "命中类别事件占比",
        "card_principal": "操作人高危排行",
        "card_principal_meta": "真高危 Top 10",
        "card_matrix": "9 大类高危事件总览矩阵",
        "card_matrix_meta": "全类别列出",
        "th_category": "类别",
        "th_hits": "命中数",
        "th_risk": "风险",
        "th_top3": "主体分布 Top3",
        "card_real": "真高危事件明细",
        "card_real_count": "(共 {n} 条,全量列出)",
        "card_real_meta": "风险等级 → 时间倒序",
        "th_time": "时间",
        "th_operator": "操作人",
        "th_source_ip": "来源 IP",
        "th_region": "地域",
        "th_resource": "资源",
        "th_result": "结果",
        "card_svc": "服务账号自动化操作汇总",
        "card_svc_count": "(共 {n} 个,已降噪聚合)",
        "card_svc_meta": "不计入真高危明细",
        "th_svc_acct": "服务账号",
        "th_total_events": "事件总数",
        "th_main_ops": "主要操作 Top3",
        "card_insights": "智能安全洞察",
        "card_insights_meta": "基于规则引擎自动生成 · 共 {n} 条",
        "footer": "渲染 by cloud-auditor-pro@workbuddy · CloudAudit 事件流分析",
        # 前端 JS 可见词
        "js_success": "成功",
        "js_chart_high_events": "高危事件数",
        "js_empty_real": "✅ 未发现非服务账号的高危操作",
        "js_empty_svc": "未识别到服务账号",
        "js_empty_cat": "✅ 9 大高危类别均无命中",
        "js_empty_principal": "暂无真高危操作人数据",
    },
    "en": {
        # Category names (9 categories)
        "cat_audit_tamper": "Audit Anti-Forensics",
        "cat_ak_lifecycle": "AK Lifecycle",
        "cat_permission_grant": "Permission Grant",
        "cat_resource_destroy": "Resource Destruction",
        "cat_kms_tamper": "KMS Master Key",
        "cat_mfa_change": "MFA Change",
        "cat_user_lifecycle": "User CRUD",
        "cat_role_change": "Role CRUD",
        "cat_sg_change": "Security Group Change",
        # Risk levels
        "risk_critical": "Critical",
        "risk_high": "High",
        "risk_medium": "Medium",
        "risk_low": "Low",
        # Generic
        "default_account": "Tencent Cloud Account",
        "empty_value": "(empty)",
        "days_unit": "d",
        # Insights - summary bar
        "ins_sum_bad": "Critical",
        "ins_sum_warn": "Attention",
        "ins_sum_info": "Notice",
        "ins_sum_good": "Healthy",
        # Insights - scan denied
        "ins_denied_title": "Scan Permission Restricted",
        "ins_denied_badge": "Review Needed",
        "ins_denied_desc": "DescribeEvents failed entirely with the current credential; the sub-account likely lacks the audit:DescribeEvents permission.",
        "ins_denied_action": "Grant ReadOnlyAccess or audit read-only permission to the inspection sub-account, then re-run.",
        # Insights - audit anti-forensics
        "ins_tamper_title": "Audit Anti-Forensics Activity",
        "ins_tamper_badge": "Critical",
        "ins_tamper_metric_hit": "Matched Events",
        "ins_tamper_desc": "Detected actions disabling/deleting/tampering with the audit tracking set — a hallmark of attacker anti-forensics.",
        "ins_tamper_action": "Immediately verify whether the operator and source IP are authorized, and confirm audit log delivery was not interrupted.",
        # Insights - genuine high-risk review
        "ins_real_title": "Genuine High-Risk Review",
        "ins_real_badge_dense": "High-Risk Cluster",
        "ins_real_badge_exist": "High-Risk Present",
        "ins_real_badge_mid": "All Medium-Risk",
        "ins_real_badge_clean": "Clean",
        "ins_metric_real_high": "Genuine High-Risk",
        "ins_metric_crit_high": "Critical/High",
        "ins_metric_window": "Time Window",
        "ins_real_desc": "After filtering service-account noise, {total} human-driven high-risk operations require attention over the last {days} days.",
        "ins_real_action": "Prioritize verifying whether the operator, source IP and resource of \"Critical/High\" events fall within the authorized scope.",
        "ins_real_desc_clean": "No high-risk operations from non-service accounts in the last {days} days; the event stream is clean.",
        "ins_real_action_clean": "Maintain the weekly inspection cadence; consider a 1-day window paired with real-time alerting.",
        # Insights - operator concentration
        "ins_conc_title": "Operator High-Risk Concentration",
        "ins_conc_badge": "Key Focus",
        "ins_metric_operator": "Operator",
        "ins_metric_high_events": "High-Risk Events",
        "ins_conc_desc": "{user} contributed {cnt} high-risk operations — a notably high concentration.",
        "ins_conc_action": "Verify whether this identity was recently compromised or over-privileged; rotate its AK and enable MFA if needed.",
        # Insights - source IP dispersion
        "ins_ip_title": "Source IP Dispersion",
        "ins_ip_badge": "Multi-Source",
        "ins_metric_distinct_ip": "Distinct Source IPs",
        "ins_ip_desc": "High-risk operations originate from {n} distinct IPs; confirm all are trusted office/bastion networks.",
        "ins_ip_action": "Combine CAM login protection with IP allowlists to narrow the trusted source range for high-risk operations.",
        # Insights - service-account denoising
        "ins_svc_title": "Service Accounts Denoised",
        "ins_svc_badge": "{n}",
        "ins_metric_svc_acct": "Service Accounts",
        "ins_metric_agg_events": "Aggregated Events",
        "ins_svc_desc": "Identified {n} service/automation accounts; their {total} events are shown in aggregate and excluded from genuine high-risk details.",
        "ins_svc_action": "To audit a specific service account event-by-event, run a dedicated forensic scan (scope=forensic).",
        # HTML - header/status
        "html_lang": "en",
        "title_suffix": "CloudAudit High-Risk Event Analysis",
        "status_denied": "⚠ Scan Restricted",
        "status_ok": "✓ Scan Complete",
        "scan_window": "Scan window",
        "near_days": "(last {days} days)",
        "datasource": "Data source · Tencent Cloud CloudAudit event stream",
        # HTML - KPI
        "kpi_total": "Total Events",
        "kpi_total_delta": "Incl. service-account noise",
        "kpi_cats": "Categories Hit",
        "kpi_cats_delta": "9 high-risk categories",
        "kpi_real": "Genuine High-Risk",
        "kpi_real_delta": "Service accounts excluded",
        "kpi_crit": "Critical / High",
        "kpi_crit_delta": "Priority review needed",
        # HTML - card titles & table headers
        "card_cat_dist": "High-Risk Category Distribution",
        "card_cat_dist_meta": "Share of matched-category events",
        "card_principal": "Operator High-Risk Ranking",
        "card_principal_meta": "Genuine high-risk Top 10",
        "card_matrix": "9-Category High-Risk Overview Matrix",
        "card_matrix_meta": "All categories listed",
        "th_category": "Category",
        "th_hits": "Hits",
        "th_risk": "Risk",
        "th_top3": "Top 3 Principals",
        "card_real": "Genuine High-Risk Event Details",
        "card_real_count": "({n} total, fully listed)",
        "card_real_meta": "Risk level → time desc",
        "th_time": "Time",
        "th_operator": "Operator",
        "th_source_ip": "Source IP",
        "th_region": "Region",
        "th_resource": "Resource",
        "th_result": "Result",
        "card_svc": "Service Account Automation Summary",
        "card_svc_count": "({n} total, denoised & aggregated)",
        "card_svc_meta": "Excluded from genuine high-risk details",
        "th_svc_acct": "Service Account",
        "th_total_events": "Total Events",
        "th_main_ops": "Top 3 Operations",
        "card_insights": "AI Security Insights",
        "card_insights_meta": "Rule-engine generated · {n} items",
        "footer": "Rendered by cloud-auditor-pro@workbuddy · CloudAudit event-stream analysis",
        # Front-end JS visible strings
        "js_success": "Success",
        "js_chart_high_events": "High-Risk Events",
        "js_empty_real": "✅ No high-risk operations from non-service accounts",
        "js_empty_svc": "No service accounts identified",
        "js_empty_cat": "✅ No hits across the 9 high-risk categories",
        "js_empty_principal": "No genuine high-risk operator data",
    },
}


def L(lang, key):
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


# ───────────── 类别元数据 ─────────────

# label_key 指向 I18N key,在使用处经 L(lang, key) 解析为对应语言文案
CATEGORY_META = {
    "audit_tamper":     {"label_key": "cat_audit_tamper",     "icon": "🛑", "order": 0},
    "ak_lifecycle":     {"label_key": "cat_ak_lifecycle",     "icon": "🔑", "order": 1},
    "permission_grant": {"label_key": "cat_permission_grant", "icon": "🛡️", "order": 2},
    "resource_destroy": {"label_key": "cat_resource_destroy", "icon": "💥", "order": 3},
    "kms_tamper":       {"label_key": "cat_kms_tamper",       "icon": "🔐", "order": 4},
    "mfa_change":       {"label_key": "cat_mfa_change",       "icon": "📱", "order": 5},
    "user_lifecycle":   {"label_key": "cat_user_lifecycle",   "icon": "👤", "order": 6},
    "role_change":      {"label_key": "cat_role_change",      "icon": "🎭", "order": 7},
    "sg_change":        {"label_key": "cat_sg_change",        "icon": "🌐", "order": 8},
}

RISK_META = {
    "critical": {"label_key": "risk_critical", "dot": "🔴", "level": "bad",  "rank": 0},
    "high":     {"label_key": "risk_high",     "dot": "🟠", "level": "warn", "rank": 1},
    "medium":   {"label_key": "risk_medium",   "dot": "🟡", "level": "info", "rank": 2},
}


# ───────────── 工具 ─────────────

def safe_json_dumps(obj):
    """嵌入 <script> 时避免 </script> 注入"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def fmt_ts(ts):
    """Unix 秒 → 'MM-DD HH:MM'(本地时区);非法值返回 '-'"""
    try:
        t = int(ts)
        if t <= 0:
            return "-"
        return time.strftime("%m-%d %H:%M", time.localtime(t))
    except (TypeError, ValueError):
        return "-"


def fmt_ts_full(ts):
    try:
        t = int(ts)
        if t <= 0:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    except (TypeError, ValueError):
        return "-"


# ───────────── 数据加工 ─────────────

def build_data(raw, account_name="", uin="", lang="zh"):
    """把 inspect_audit_events.py 的 JSON 处理成前端用的简洁结构"""
    tw = raw.get("time_window", {}) or {}
    scan_status = raw.get("scan_status", "ok")
    ebc = raw.get("events_by_category", {}) or {}
    category_risk = raw.get("category_risk", {}) or {}
    sbp = raw.get("summary_by_principal", {}) or {}
    service_set = set(raw.get("service_account_set", []) or [])

    # 1) 扁平化所有事件,并区分 真高危(非服务账号) vs 服务账号
    real_events = []      # 非服务账号事件(逐条展开)
    service_events = []   # 服务账号事件(聚合)
    for cat, evs in ebc.items():
        for e in evs:
            row = {
                "cat": cat,
                "cat_label": L(lang, CATEGORY_META.get(cat, {}).get("label_key", "")) if cat in CATEGORY_META else cat,
                "risk": category_risk.get(cat, "medium"),
                "event_name": e.get("event_name", ""),
                # EN mode: drop the Chinese gloss "(中文事件名)" — the English event_name is self-sufficient
                "event_name_cn": "" if lang == "en" else e.get("event_name_cn", ""),
                "event_time": e.get("event_time", 0),
                "time_str": fmt_ts(e.get("event_time", 0)),
                "username": e.get("username", ""),
                "source_ip": e.get("source_ip", ""),
                "region": tr(e.get("region", ""), lang),
                "error_code": str(e.get("error_code", "0")),
                "resource_name": e.get("resource_name", ""),
                "resource_type": tr(e.get("resource_type", ""), lang),
                "request_id": e.get("request_id", ""),
            }
            if row["username"] in service_set:
                service_events.append(row)
            else:
                real_events.append(row)

    # 真高危事件按 风险等级 → 时间倒序
    real_events.sort(
        key=lambda r: (RISK_META.get(r["risk"], {}).get("rank", 9),
                       -int(r["event_time"] or 0))
    )

    # 2) 类别总览矩阵(9 大类全列,含 0 命中)
    cat_overview = []
    for cat, meta in sorted(CATEGORY_META.items(), key=lambda kv: kv[1]["order"]):
        evs = ebc.get(cat, []) or []
        # 主体分布 Top3(去重计数)
        principal_cnt = {}
        for e in evs:
            u = e.get("username", "") or L(lang, "empty_value")
            principal_cnt[u] = principal_cnt.get(u, 0) + 1
        top3 = sorted(principal_cnt.items(), key=lambda x: -x[1])[:3]
        risk = category_risk.get(cat, "medium")
        cat_overview.append({
            "cat": cat,
            "label": L(lang, meta["label_key"]),
            "icon": meta["icon"],
            "count": len(evs),
            "risk": risk,
            "risk_label": L(lang, RISK_META.get(risk, {}).get("label_key", "risk_medium")),
            "risk_level": RISK_META.get(risk, {}).get("level", "info"),
            "top3": [{"name": n, "cnt": c} for n, c in top3],
        })

    # 3) 服务账号聚合表
    svc_agg = []
    for u in sorted(service_set):
        info = sbp.get(u, {}) or {}
        ev_map = info.get("events", {}) or {}
        main_ops = sorted(ev_map.items(), key=lambda x: -x[1])[:3]
        svc_agg.append({
            "username": u,
            "total": info.get("total", sum(ev_map.values()) if ev_map else 0),
            "main_ops": [{"name": n, "cnt": c} for n, c in main_ops],
        })
    svc_agg.sort(key=lambda x: -x["total"])

    # 4) KPI
    total_events = raw.get("total_events", len(real_events) + len(service_events))
    cats_hit = raw.get("total_categories_hit",
                       len([c for c in cat_overview if c["count"] > 0]))
    real_high = len(real_events)
    # 严重/高 真高危计数
    critical_high = len([r for r in real_events if r["risk"] in ("critical", "high")])

    # 5) 图表数据:类别分布(非零) + 风险等级分布
    cat_chart = [
        {"label": c["label"], "count": c["count"], "risk": c["risk"]}
        for c in cat_overview if c["count"] > 0
    ]
    # 主体柱图:真高危事件按操作人聚合 Top10
    principal_real = {}
    for r in real_events:
        u = r["username"] or L(lang, "empty_value")
        principal_real[u] = principal_real.get(u, 0) + 1
    principal_chart = sorted(
        [{"name": n, "count": c} for n, c in principal_real.items()],
        key=lambda x: -x["count"]
    )[:10]

    # 6) 智能洞察
    insights = derive_insights(
        scan_status=scan_status, cat_overview=cat_overview,
        real_events=real_events, svc_agg=svc_agg, tw=tw,
        critical_high=critical_high, lang=lang,
    )

    return {
        "meta": {
            "account_name": account_name or L(lang, "default_account"),
            "uin": uin or "-",
            "scan_status": scan_status,
            "window_days": tw.get("days", "-"),
            "start_iso": tw.get("start_iso", "-"),
            "end_iso": tw.get("end_iso", "-"),
            "total_events": total_events,
            "cats_hit": cats_hit,
            "real_high": real_high,
            "critical_high": critical_high,
            "service_count": len(service_set),
        },
        "cat_overview": cat_overview,
        "real_events": real_events,
        "svc_agg": svc_agg,
        "cat_chart": cat_chart,
        "principal_chart": principal_chart,
        "insights": insights,
    }


def derive_insights(*, scan_status, cat_overview, real_events, svc_agg, tw,
                    critical_high, lang="zh"):
    """基于扫描结果衍生 AIOps 风格安全洞察。

    字段(每条):
      title / desc / level(bad/warn/info/good) / icon / badge
      metrics: [{label, value, mode}]
      action: 一句应急/复核建议
    """
    out = []

    def m(label, value, mode="none"):
        return {"label": label, "value": value, "mode": mode}

    days = tw.get("days", "-")
    days_unit = L(lang, "days_unit")

    # 0) 扫描受限
    if scan_status == "denied":
        out.append({
            "title": L(lang, "ins_denied_title"), "level": "warn", "icon": "🚫",
            "badge": L(lang, "ins_denied_badge"),
            "metrics": [m("scan_status", "denied")],
            "desc": L(lang, "ins_denied_desc"),
            "action": L(lang, "ins_denied_action"),
        })

    # 1) 审计反取证(出现即最高优先级)
    tamper = next((c for c in cat_overview if c["cat"] == "audit_tamper"), None)
    if tamper and tamper["count"] > 0:
        out.append({
            "title": L(lang, "ins_tamper_title"), "level": "bad", "icon": "🛑",
            "badge": L(lang, "ins_tamper_badge"),
            "metrics": [m(L(lang, "ins_tamper_metric_hit"), str(tamper["count"]), "up")],
            "desc": L(lang, "ins_tamper_desc"),
            "action": L(lang, "ins_tamper_action"),
        })

    # 2) 真高危事件总览
    total_real = len(real_events)
    if total_real > 0:
        if critical_high >= 10:
            lvl, badge = "bad", L(lang, "ins_real_badge_dense")
        elif critical_high >= 1:
            lvl, badge = "warn", L(lang, "ins_real_badge_exist")
        else:
            lvl, badge = "info", L(lang, "ins_real_badge_mid")
        out.append({
            "title": L(lang, "ins_real_title"), "level": lvl, "icon": "🔍", "badge": badge,
            "metrics": [
                m(L(lang, "ins_metric_real_high"), str(total_real)),
                m(L(lang, "ins_metric_crit_high"), str(critical_high), "up" if critical_high else "flat"),
                m(L(lang, "ins_metric_window"), f"{days} {days_unit}"),
            ],
            "desc": L(lang, "ins_real_desc").format(days=days, total=total_real),
            "action": L(lang, "ins_real_action"),
        })
    else:
        out.append({
            "title": L(lang, "ins_real_title"), "level": "good", "icon": "✅",
            "badge": L(lang, "ins_real_badge_clean"),
            "metrics": [m(L(lang, "ins_metric_real_high"), "0"),
                        m(L(lang, "ins_metric_window"), f"{days} {days_unit}")],
            "desc": L(lang, "ins_real_desc_clean").format(days=days),
            "action": L(lang, "ins_real_action_clean"),
        })

    # 3) 单操作人高危集中度
    if real_events:
        p_cnt = {}
        for r in real_events:
            u = r["username"] or L(lang, "empty_value")
            p_cnt[u] = p_cnt.get(u, 0) + 1
        top_u, top_c = max(p_cnt.items(), key=lambda x: x[1])
        if top_c >= 3:
            out.append({
                "title": L(lang, "ins_conc_title"), "level": "warn", "icon": "👤",
                "badge": L(lang, "ins_conc_badge"),
                "metrics": [m(L(lang, "ins_metric_operator"), top_u),
                            m(L(lang, "ins_metric_high_events"), str(top_c), "up")],
                "desc": L(lang, "ins_conc_desc").format(user=top_u, cnt=top_c),
                "action": L(lang, "ins_conc_action"),
            })

    # 4) 来源 IP 异常(非内网/多 IP)
    if real_events:
        ip_set = set(r["source_ip"] for r in real_events if r["source_ip"])
        if len(ip_set) >= 5:
            out.append({
                "title": L(lang, "ins_ip_title"), "level": "info", "icon": "🌍",
                "badge": L(lang, "ins_ip_badge"),
                "metrics": [m(L(lang, "ins_metric_distinct_ip"), str(len(ip_set)))],
                "desc": L(lang, "ins_ip_desc").format(n=len(ip_set)),
                "action": L(lang, "ins_ip_action"),
            })

    # 5) 服务账号降噪说明
    if svc_agg:
        svc_total = sum(s["total"] for s in svc_agg)
        out.append({
            "title": L(lang, "ins_svc_title"), "level": "info", "icon": "🤖",
            "badge": L(lang, "ins_svc_badge").format(n=len(svc_agg)),
            "metrics": [
                m(L(lang, "ins_metric_svc_acct"), str(len(svc_agg))),
                m(L(lang, "ins_metric_agg_events"), str(svc_total)),
            ],
            "desc": L(lang, "ins_svc_desc").format(n=len(svc_agg), total=svc_total),
            "action": L(lang, "ins_svc_action"),
        })

    return out


def _build_insights_summary(insights, lang="zh"):
    """洞察顶部统计条 HTML(按 level 分组计数)"""
    counter = {"bad": 0, "warn": 0, "info": 0, "good": 0}
    for i in insights or []:
        lv = i.get("level", "info")
        if lv not in counter:
            lv = "info"
        counter[lv] += 1
    badge_label = [("bad", L(lang, "ins_sum_bad")), ("warn", L(lang, "ins_sum_warn")),
                   ("info", L(lang, "ins_sum_info")), ("good", L(lang, "ins_sum_good"))]
    parts = []
    for k, label in badge_label:
        if counter[k] > 0:
            parts.append(f'<div class="stat"><span class="dot {k}"></span>{label} <b>{counter[k]}</b></div>')
    if not parts:
        return ""
    return '<div class="ins-summary">' + ''.join(parts) + '</div>'


# ───────────── 渲染 ─────────────

def render_html(data, *, offline_chartjs_path=None, lang="zh"):
    """生成单文件 HTML"""
    if offline_chartjs_path:
        with open(offline_chartjs_path, "r", encoding="utf-8") as f:
            chartjs = f.read()
        chartjs_tag = f"<script>{chartjs}</script>"
    else:
        chartjs_tag = f'<script src="{CHART_JS_CDN}"></script>'

    meta = data["meta"]
    title = f"{escape(meta['account_name'])} {L(lang, 'title_suffix')}"

    # 扫描状态徽标
    if meta["scan_status"] == "denied":
        status_tag = f'<span class="tag warn">{L(lang, "status_denied")}</span>'
    else:
        status_tag = f'<span class="tag ok">{L(lang, "status_ok")}</span>'

    payload = {
        "cat_chart": data["cat_chart"],
        "principal_chart": data["principal_chart"],
        "real_events": data["real_events"],
        "svc_agg": data["svc_agg"],
        "insights": data["insights"],
    }

    counts = {
        "real": len(data["real_events"]),
        "svc": len(data["svc_agg"]),
    }

    # 类别总览矩阵(服务端渲染,9 行全列)
    overview_rows = []
    for c in data["cat_overview"]:
        risk = RISK_META.get(c["risk"], {})
        dot = risk.get("dot", "🟡")
        top3 = " · ".join(
            f'{escape(t["name"])}({t["cnt"]})' for t in c["top3"]
        ) if c["top3"] else '<span class="muted">—</span>'
        cnt_cls = "hit" if c["count"] > 0 else "zero"
        overview_rows.append(
            f'<tr><td>{c["icon"]} {escape(c["label"])}</td>'
            f'<td class="num {cnt_cls}">{c["count"]}</td>'
            f'<td>{dot} {escape(c["risk_label"])}</td>'
            f'<td class="cell-ellipsis" title="{escape(top3)}">{top3}</td></tr>'
        )
    overview_html = "".join(overview_rows)

    # 本地化文案(注入 f-string)
    t_html_lang = L(lang, "html_lang")
    t_scan_window = L(lang, "scan_window")
    t_near_days = L(lang, "near_days").format(days=meta['window_days'])
    t_datasource = L(lang, "datasource")
    t_kpi_total = L(lang, "kpi_total")
    t_kpi_total_delta = L(lang, "kpi_total_delta")
    t_kpi_cats = L(lang, "kpi_cats")
    t_kpi_cats_delta = L(lang, "kpi_cats_delta")
    t_kpi_real = L(lang, "kpi_real")
    t_kpi_real_delta = L(lang, "kpi_real_delta")
    t_kpi_crit = L(lang, "kpi_crit")
    t_kpi_crit_delta = L(lang, "kpi_crit_delta")
    t_title_suffix = L(lang, "title_suffix")
    t_card_cat_dist = L(lang, "card_cat_dist")
    t_card_cat_dist_meta = L(lang, "card_cat_dist_meta")
    t_card_principal = L(lang, "card_principal")
    t_card_principal_meta = L(lang, "card_principal_meta")
    t_card_matrix = L(lang, "card_matrix")
    t_card_matrix_meta = L(lang, "card_matrix_meta")
    t_th_category = L(lang, "th_category")
    t_th_hits = L(lang, "th_hits")
    t_th_risk = L(lang, "th_risk")
    t_th_top3 = L(lang, "th_top3")
    t_card_real = L(lang, "card_real")
    t_card_real_count = L(lang, "card_real_count").format(n=counts['real'])
    t_card_real_meta = L(lang, "card_real_meta")
    t_th_time = L(lang, "th_time")
    t_th_operator = L(lang, "th_operator")
    t_th_source_ip = L(lang, "th_source_ip")
    t_th_region = L(lang, "th_region")
    t_th_resource = L(lang, "th_resource")
    t_th_result = L(lang, "th_result")
    t_card_svc = L(lang, "card_svc")
    t_card_svc_count = L(lang, "card_svc_count").format(n=counts['svc'])
    t_card_svc_meta = L(lang, "card_svc_meta")
    t_th_svc_acct = L(lang, "th_svc_acct")
    t_th_total_events = L(lang, "th_total_events")
    t_th_main_ops = L(lang, "th_main_ops")
    t_card_insights = L(lang, "card_insights")
    t_card_insights_meta = L(lang, "card_insights_meta").format(n=len(data['insights']))
    t_footer = L(lang, "footer")
    # 前端 JS 可见词(经 safe_json_dumps 注入,自动转义)
    t_js = safe_json_dumps({
        "risk_label": {
            "critical": L(lang, "risk_critical"),
            "high": L(lang, "risk_high"),
            "medium": L(lang, "risk_medium"),
        },
        "success": L(lang, "js_success"),
        "chart_high_events": L(lang, "js_chart_high_events"),
        "empty_real": L(lang, "js_empty_real"),
        "empty_svc": L(lang, "js_empty_svc"),
        "empty_cat": L(lang, "js_empty_cat"),
        "empty_principal": L(lang, "js_empty_principal"),
    })

    return f"""<!DOCTYPE html>
<html lang="{t_html_lang}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
{chartjs_tag}
<style>
:root {{
  --bg-0: #0b1020; --bg-1: #111933; --line: rgba(120,144,200,0.16);
  --text-0: #e7ecff; --text-1: #aab4dc; --text-2: #6f7aa8;
  --accent: #5b8cff; --accent-2: #8b5cff;
  --good: #36d399; --warn: #f5b455; --bad: #ef4f6f;
  --shadow: 0 8px 32px rgba(0,0,0,0.32);
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background:
  radial-gradient(1200px 600px at 10% -10%, rgba(91,140,255,0.18), transparent 60%),
  radial-gradient(900px 500px at 110% 10%, rgba(139,92,255,0.16), transparent 60%),
  var(--bg-0); color: var(--text-0); font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
  "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif; font-size: 14px; line-height: 1.55; }}
.wrap {{ max-width: 1320px; margin: 0 auto; padding: 24px 28px 80px; }}
.hero {{ background: linear-gradient(135deg, rgba(91,140,255,0.12), rgba(139,92,255,0.10));
  border: 1px solid var(--line); border-radius: 16px; padding: 24px 28px; margin-bottom: 20px;
  backdrop-filter: blur(8px); box-shadow: var(--shadow); }}
.hero-top {{ display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 16px; }}
.hero h1 {{ font-size: 22px; margin: 0 0 6px; }}
.hero .sub {{ color: var(--text-1); font-size: 13px; }}
.tag {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
  background: rgba(91,140,255,0.18); color: #b8caff; font-size: 12px;
  border: 1px solid rgba(91,140,255,0.32); margin-left: 8px; }}
.tag.warn {{ background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }}
.tag.ok {{ background: rgba(54,211,153,0.16); color: #9ee9c9; border-color: rgba(54,211,153,0.32); }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 20px; }}
.kpi {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 16px; position: relative; overflow: hidden; }}
.kpi::after {{ content: ""; position: absolute; right: -30px; top: -30px;
  width: 100px; height: 100px; border-radius: 50%;
  background: radial-gradient(circle, rgba(91,140,255,0.20), transparent 70%); }}
.kpi .label {{ color: var(--text-2); font-size: 12px; }}
.kpi .value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
.kpi .value.bad {{ color: var(--bad); }}
.kpi .value.good {{ color: var(--good); }}
.kpi .delta {{ font-size: 12px; margin-top: 4px; color: var(--text-1); }}
.card {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }}
.card h2 {{ font-size: 15px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }}
.card h2::before {{ content: ""; width: 3px; height: 14px; background: var(--accent); border-radius: 2px; }}
.card .meta {{ color: var(--text-2); font-size: 12px; font-weight: 400; margin-left: auto; }}
.row-2 {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }}
.row-2.eq {{ grid-template-columns: 1fr 1fr; }}
.chart-box {{ position: relative; height: 280px; }}
.chart-box.tall {{ height: 340px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }}
th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }}
th {{ color: var(--text-2); font-weight: 500; font-size: 12px; letter-spacing: 0.6px;
  background: rgba(255,255,255,0.02); position: sticky; top: 0; z-index: 1; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.num.hit {{ color: var(--bad); font-weight: 700; }}
td.num.zero {{ color: var(--text-2); }}
tbody tr:hover {{ background: rgba(91,140,255,0.06); }}
td.code {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; color: var(--text-1);
  max-width: 320px; overflow: hidden; text-overflow: ellipsis; }}
td.cell-ellipsis {{ max-width: 260px; overflow: hidden; text-overflow: ellipsis; }}
.pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; vertical-align: middle; }}
.pill.bad {{ background: rgba(239,79,111,0.16); color: var(--bad); border: 1px solid rgba(239,79,111,0.32); }}
.pill.warn {{ background: rgba(245,180,85,0.16); color: var(--warn); border: 1px solid rgba(245,180,85,0.32); }}
.pill.info {{ background: rgba(91,140,255,0.16); color: #b8caff; border: 1px solid rgba(91,140,255,0.32); }}
.pill.err {{ background: rgba(239,79,111,0.12); color: #ffb3c1; border: 1px solid rgba(239,79,111,0.28); }}
.pill.ok {{ background: rgba(54,211,153,0.12); color: #9ee9c9; border: 1px solid rgba(54,211,153,0.28); }}
.scroll-y {{ max-height: 520px; overflow: auto; border-radius: 8px; border: 1px solid var(--line); }}
.scroll-y::-webkit-scrollbar {{ width: 8px; height: 8px; }}
.scroll-y::-webkit-scrollbar-thumb {{ background: rgba(120,144,200,0.28); border-radius: 4px; }}
.scroll-y::-webkit-scrollbar-track {{ background: transparent; }}
.empty {{ padding: 40px 20px; text-align: center; color: var(--text-2); font-size: 13px; }}
.muted {{ color: var(--text-2); font-style: italic; }}

/* ── 环形图 + 自定义可滚动图例 ── */
.pie-wrap {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: center; height: 320px; }}
.pie-canvas {{ position: relative; height: 100%; }}
.pie-legend {{ max-height: 300px; overflow-y: auto; padding-right: 6px; font-size: 12px; }}
.pie-legend::-webkit-scrollbar {{ width: 6px; }}
.pie-legend::-webkit-scrollbar-thumb {{ background: rgba(120,144,200,0.30); border-radius: 3px; }}
.legend-item {{ display: grid; grid-template-columns: 12px 1fr auto; gap: 8px; align-items: center;
  padding: 5px 8px; border-radius: 6px; cursor: pointer; transition: background .15s; }}
.legend-item:hover {{ background: rgba(91,140,255,0.10); }}
.legend-item.dim {{ opacity: 0.35; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }}
.legend-name {{ color: var(--text-0); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.legend-pct {{ color: var(--text-2); font-variant-numeric: tabular-nums; font-size: 11.5px; }}

/* ── 智能洞察 - AIOps 风格 ── */
.insights-wrap {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
.ins-card {{ position: relative; background: linear-gradient(135deg, rgba(91,140,255,0.06), rgba(139,92,255,0.04));
  border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; overflow: hidden;
  transition: transform .15s, box-shadow .15s; }}
.ins-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(91,140,255,0.15); }}
.ins-card::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--accent); border-radius: 2px 0 0 2px; }}
.ins-card.warn::before {{ background: var(--warn); }}
.ins-card.bad::before {{ background: var(--bad); }}
.ins-card.good::before {{ background: var(--good); }}
.ins-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
.ins-icon {{ width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center;
  justify-content: center; font-size: 14px; flex-shrink: 0;
  background: rgba(91,140,255,0.18); color: var(--accent); }}
.ins-card.warn .ins-icon {{ background: rgba(245,180,85,0.18); color: var(--warn); }}
.ins-card.bad .ins-icon {{ background: rgba(239,79,111,0.18); color: var(--bad); }}
.ins-card.good .ins-icon {{ background: rgba(54,211,153,0.18); color: var(--good); }}
.ins-title {{ font-weight: 600; font-size: 13.5px; color: var(--text-0); flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.ins-badge {{ font-size: 10.5px; padding: 2px 8px; border-radius: 999px; font-weight: 600;
  background: rgba(91,140,255,0.16); color: #b8caff; border: 1px solid rgba(91,140,255,0.28); }}
.ins-card.warn .ins-badge {{ background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }}
.ins-card.bad .ins-badge {{ background: rgba(239,79,111,0.18); color: #ffb3c1; border-color: rgba(239,79,111,0.36); }}
.ins-card.good .ins-badge {{ background: rgba(54,211,153,0.16); color: #9ee9c9; border-color: rgba(54,211,153,0.32); }}
.ins-metric {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0 10px;
  padding: 8px 10px; background: rgba(0,0,0,0.20); border-radius: 8px;
  border: 1px solid rgba(120,144,200,0.10); }}
.ins-metric .m {{ display: flex; flex-direction: column; gap: 1px; }}
.ins-metric .m-label {{ color: var(--text-2); font-size: 10.5px; letter-spacing: 0.3px; }}
.ins-metric .m-value {{ color: var(--text-0); font-size: 14px; font-weight: 600;
  font-variant-numeric: tabular-nums; }}
.ins-metric .m-value.up {{ color: var(--bad); }}
.ins-metric .m-value.down {{ color: var(--good); }}
.ins-desc {{ color: var(--text-1); font-size: 12.5px; line-height: 1.55; }}
.ins-action {{ margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(120,144,200,0.14);
  color: var(--text-2); font-size: 11.5px; display: flex; align-items: center; gap: 6px; }}
.ins-action::before {{ content: "🛡️"; font-size: 12px; }}
.ins-summary {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px;
  padding: 10px 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--line);
  border-radius: 10px; font-size: 12px; }}
.ins-summary .stat {{ display: flex; align-items: center; gap: 6px; color: var(--text-1); }}
.ins-summary .stat b {{ color: var(--text-0); font-weight: 700; font-size: 14px;
  font-variant-numeric: tabular-nums; }}
.ins-summary .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
.ins-summary .dot.bad {{ background: var(--bad); }}
.ins-summary .dot.warn {{ background: var(--warn); }}
.ins-summary .dot.info {{ background: var(--accent); }}
.ins-summary .dot.good {{ background: var(--good); }}
footer {{ color: var(--text-2); font-size: 11.5px; text-align: center; margin-top: 24px; }}
@media (max-width: 980px) {{
  .kpis {{ grid-template-columns: repeat(2, 1fr); }}
  .row-2, .row-2.eq {{ grid-template-columns: 1fr; }}
  .pie-wrap {{ grid-template-columns: 1fr; height: auto; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <div class="hero-top">
      <div>
        <h1>🛡️ {escape(meta['account_name'])} · {t_title_suffix}</h1>
        <div class="sub">
          {t_scan_window} <b>{escape(str(meta['start_iso']))}</b> ~ <b>{escape(str(meta['end_iso']))}</b>{t_near_days}
          <span class="tag">UIN: {escape(str(meta['uin']))}</span>
          {status_tag}
        </div>
      </div>
      <div style="text-align: right; color: var(--text-1); font-size: 12px;">
        <div>{t_datasource}</div>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi">
        <div class="label">{t_kpi_total}</div>
        <div class="value">{meta['total_events']}</div>
        <div class="delta">{t_kpi_total_delta}</div>
      </div>
      <div class="kpi">
        <div class="label">{t_kpi_cats}</div>
        <div class="value">{meta['cats_hit']} / 9</div>
        <div class="delta">{t_kpi_cats_delta}</div>
      </div>
      <div class="kpi">
        <div class="label">{t_kpi_real}</div>
        <div class="value {'bad' if meta['real_high'] > 0 else 'good'}">{meta['real_high']}</div>
        <div class="delta">{t_kpi_real_delta}</div>
      </div>
      <div class="kpi">
        <div class="label">{t_kpi_crit}</div>
        <div class="value {'bad' if meta['critical_high'] > 0 else 'good'}">{meta['critical_high']}</div>
        <div class="delta">{t_kpi_crit_delta}</div>
      </div>
    </div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>{t_card_cat_dist} <span class="meta">{t_card_cat_dist_meta}</span></h2>
      <div class="pie-wrap">
        <div class="pie-canvas"><canvas id="catPie"></canvas></div>
        <div class="pie-legend" id="pieLegend"></div>
      </div>
    </div>
    <div class="card">
      <h2>{t_card_principal} <span class="meta">{t_card_principal_meta}</span></h2>
      <div class="chart-box tall"><canvas id="principalChart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <h2>{t_card_matrix} <span class="meta">{t_card_matrix_meta}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{t_th_category}</th><th class="num">{t_th_hits}</th><th>{t_th_risk}</th><th>{t_th_top3}</th>
        </tr></thead>
        <tbody>{overview_html}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{t_card_real}{t_card_real_count}<span class="meta">{t_card_real_meta}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{t_th_time}</th><th>{t_th_category}</th><th>EventName</th><th>{t_th_operator}</th>
          <th>{t_th_source_ip}</th><th>{t_th_region}</th><th>{t_th_resource}</th><th>{t_th_result}</th>
        </tr></thead>
        <tbody id="realTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{t_card_svc}{t_card_svc_count}<span class="meta">{t_card_svc_meta}</span></h2>
    <div class="scroll-y">
      <table>
        <thead><tr>
          <th>{t_th_svc_acct}</th><th class="num">{t_th_total_events}</th><th>{t_th_main_ops}</th>
        </tr></thead>
        <tbody id="svcTable"></tbody>
      </table>
    </div>
  </div>

  {(
    '<div class="card"><h2>🤖 ' + t_card_insights + ' <span class="meta">' + t_card_insights_meta + '</span></h2>'
    + _build_insights_summary(data['insights'], lang) +
    '<div class="insights-wrap" id="insightsWrap"></div></div>'
  ) if data['insights'] else ''}

  <footer>{t_footer}</footer>
</div>

<script>
const DATA = {safe_json_dumps(payload)};
const T = {t_js};
const RISK_COLOR = {{ critical: '#ef4f6f', high: '#f5b455', medium: '#5b8cff' }};
const RISK_PILL  = {{ critical: 'bad', high: 'warn', medium: 'info' }};
const RISK_LABEL = T.risk_label;
const COLORS = ['#ef4f6f','#f5b455','#5b8cff','#8b5cff','#36d399','#67c8f5',
                '#a8d36b','#ffb6c1','#9b8cff','#ffd166'];

function escapeHtml(s) {{
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}}

// ── 真高危事件表
const realTb = document.getElementById('realTable');
if (DATA.real_events.length) {{
  realTb.innerHTML = DATA.real_events.map(r => {{
    const pill = RISK_PILL[r.risk] || 'info';
    const errOk = (r.error_code === '0' || r.error_code === '' || r.error_code === 'None');
    const resTxt = [r.resource_name, r.resource_type].filter(Boolean).join(' / ') || '-';
    const en = escapeHtml(r.event_name) + (r.event_name_cn ? ' <span class="muted">(' + escapeHtml(r.event_name_cn) + ')</span>' : '');
    return '<tr>' +
      '<td title="' + escapeHtml(r.time_str) + '">' + escapeHtml(r.time_str) + '</td>' +
      '<td><span class="pill ' + pill + '">' + escapeHtml(r.cat_label) + '</span></td>' +
      '<td class="cell-ellipsis" title="' + escapeHtml(r.event_name) + '">' + en + '</td>' +
      '<td class="cell-ellipsis" title="' + escapeHtml(r.username) + '">' + (escapeHtml(r.username) || '<span class="muted">-</span>') + '</td>' +
      '<td class="code" title="' + escapeHtml(r.source_ip) + '">' + (escapeHtml(r.source_ip) || '-') + '</td>' +
      '<td>' + (escapeHtml(r.region) || '-') + '</td>' +
      '<td class="cell-ellipsis" title="' + escapeHtml(resTxt) + '">' + escapeHtml(resTxt) + '</td>' +
      '<td>' + (errOk ? '<span class="pill ok">' + escapeHtml(T.success) + '</span>' : '<span class="pill err">' + escapeHtml(r.error_code) + '</span>') + '</td>' +
    '</tr>';
  }}).join('');
}} else {{
  realTb.parentElement.parentElement.insertAdjacentHTML('afterend', '<div class="empty">' + escapeHtml(T.empty_real) + '</div>');
  realTb.parentElement.style.display = 'none';
}}

// ── 服务账号聚合表
const svcTb = document.getElementById('svcTable');
if (DATA.svc_agg.length) {{
  svcTb.innerHTML = DATA.svc_agg.map(s => {{
    const ops = s.main_ops.map(o => escapeHtml(o.name) + '(' + o.cnt + ')').join(' · ') || '-';
    return '<tr>' +
      '<td class="code" title="' + escapeHtml(s.username) + '">' + escapeHtml(s.username) + '</td>' +
      '<td class="num">' + s.total + '</td>' +
      '<td class="cell-ellipsis" title="' + escapeHtml(ops) + '">' + ops + '</td>' +
    '</tr>';
  }}).join('');
}} else {{
  svcTb.parentElement.parentElement.insertAdjacentHTML('afterend', '<div class="empty">' + escapeHtml(T.empty_svc) + '</div>');
  svcTb.parentElement.style.display = 'none';
}}

// ── 图表
if (typeof Chart !== 'undefined') {{
  Chart.defaults.color = '#aab4dc';
  Chart.defaults.borderColor = 'rgba(120,144,200,0.16)';
  Chart.defaults.font.family = '-apple-system, "PingFang SC", sans-serif';

  // 类别环图
  if (DATA.cat_chart.length) {{
    const totalCat = DATA.cat_chart.reduce((s, c) => s + c.count, 0);
    const pieColors = DATA.cat_chart.map(c => RISK_COLOR[c.risk] || '#5b8cff');
    const pieChart = new Chart(document.getElementById('catPie'), {{
      type: 'doughnut',
      data: {{
        labels: DATA.cat_chart.map(c => c.label),
        datasets: [{{ data: DATA.cat_chart.map(c => c.count), backgroundColor: pieColors, borderWidth: 0 }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false, cutout: '62%',
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: c => c.label + ': ' + c.parsed +
            ' (' + (totalCat > 0 ? (c.parsed/totalCat*100).toFixed(1) : '0.0') + '%)' }} }}
        }}
      }}
    }});
    const legendEl = document.getElementById('pieLegend');
    DATA.cat_chart.forEach((c, idx) => {{
      const pct = totalCat > 0 ? (c.count / totalCat * 100) : 0;
      const item = document.createElement('div');
      item.className = 'legend-item';
      item.innerHTML =
        '<span class="legend-dot" style="background:' + pieColors[idx] + '"></span>' +
        '<span class="legend-name" title="' + c.label + '">' + c.label + '</span>' +
        '<span class="legend-pct">' + c.count + ' · ' + pct.toFixed(0) + '%</span>';
      item.addEventListener('click', () => {{
        const m = pieChart.getDatasetMeta(0);
        m.data[idx].hidden = !m.data[idx].hidden;
        item.classList.toggle('dim', m.data[idx].hidden);
        pieChart.update();
      }});
      legendEl.appendChild(item);
    }});
  }} else {{
    document.getElementById('catPie').parentElement.parentElement.innerHTML =
      '<div class="empty" style="grid-column:1/-1;">' + escapeHtml(T.empty_cat) + '</div>';
  }}

  // 操作人柱图
  if (DATA.principal_chart.length) {{
    new Chart(document.getElementById('principalChart'), {{
      type: 'bar',
      data: {{
        labels: DATA.principal_chart.map(p => p.name),
        datasets: [{{ label: T.chart_high_events, data: DATA.principal_chart.map(p => p.count),
          backgroundColor: '#ef4f6f', borderRadius: 6 }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ precision: 0 }}, grid: {{ color: 'rgba(120,144,200,0.10)' }} }},
          y: {{ grid: {{ display: false }} }}
        }}
      }}
    }});
  }} else {{
    document.getElementById('principalChart').parentElement.innerHTML =
      '<div class="empty">' + escapeHtml(T.empty_principal) + '</div>';
  }}
}}

// ── 智能洞察卡片
const insWrap = document.getElementById('insightsWrap');
if (insWrap && DATA.insights && DATA.insights.length) {{
  const order = {{ bad: 0, warn: 1, info: 2, good: 3 }};
  const sorted = DATA.insights.slice().sort((a, b) => (order[a.level] || 9) - (order[b.level] || 9));
  insWrap.innerHTML = sorted.map(ins => {{
    const lvl = ['bad','warn','info','good'].includes(ins.level) ? ins.level : 'info';
    const icon = ins.icon || '🔍';
    const badge = ins.badge ? '<span class="ins-badge">' + escapeHtml(ins.badge) + '</span>' : '';
    let metricsHtml = '';
    if (Array.isArray(ins.metrics) && ins.metrics.length) {{
      metricsHtml = '<div class="ins-metric">' +
        ins.metrics.map(m => {{
          const mode = ['up','down','flat'].includes(m.mode) ? m.mode : '';
          return '<div class="m"><span class="m-label">' + escapeHtml(m.label) + '</span>' +
            '<span class="m-value ' + mode + '">' + escapeHtml(m.value) + '</span></div>';
        }}).join('') + '</div>';
    }}
    const action = ins.action ? '<div class="ins-action">' + escapeHtml(ins.action) + '</div>' : '';
    return '<div class="ins-card ' + lvl + '">' +
      '<div class="ins-head"><div class="ins-icon">' + icon + '</div>' +
        '<div class="ins-title">' + escapeHtml(ins.title) + '</div>' + badge + '</div>' +
      metricsHtml +
      '<div class="ins-desc">' + escapeHtml(ins.desc) + '</div>' + action +
    '</div>';
  }}).join('');
}}
</script>
</body>
</html>
"""


# ───────────── CLI ─────────────

def main():
    ap = argparse.ArgumentParser(description="Render CloudAudit events JSON to single-file HTML dashboard")
    ap.add_argument("--data", required=True, help="inspect_audit_events.py 输出的 JSON 路径(或 - 表示 stdin)")
    ap.add_argument("--output", "-o", default="-", help="输出 HTML 路径,默认 stdout")
    ap.add_argument("--account", default="", help="账户昵称(报告头展示)")
    ap.add_argument("--uin", default="", help="主账号 UIN(报告头展示)")
    ap.add_argument("--offline", default="", help="内嵌 Chart.js(提供本地 chart.umd.min.js 路径,否则用 CDN)")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="UI language for the dashboard")
    args = ap.parse_args()

    # 在任何 tr() 调用之前注册 LLM 兜底钩子
    if args.lang == "en":
        set_llm_hook(create_llm_hook())

    if args.data == "-":
        raw = json.loads(sys.stdin.read())
    else:
        with open(args.data, "r", encoding="utf-8") as f:
            raw = json.load(f)

    data = build_data(raw, account_name=args.account, uin=args.uin, lang=args.lang)
    html = render_html(data, offline_chartjs_path=args.offline or None, lang=args.lang)

    if args.output == "-":
        sys.stdout.write(html)
    else:
        # Resolve output path: if relative, prefix with WORKBUDDY_WORKSPACE (ensures artifacts panel visibility)
        out_path = args.output
        if not os.path.isabs(out_path):
            ws = os.environ.get("WORKBUDDY_WORKSPACE")
            if ws:
                out_path = os.path.join(ws, out_path)
            # else: keep relative — will resolve against cwd
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 已生成审计 HTML 看板: {out_path}", file=sys.stderr)

    # ── i18n 残留扫描（en 模式） ──
    if args.lang == "en":
        # 1) tr() 调用层审计：哪些字段调了 tr() 但没翻干净
        tr_report = get_translation_report()
        # 2) HTML 渲染层扫描：哪些字段根本没过 tr()
        html_residuals = scan_residual_chinese(html, lang=args.lang)

        if tr_report["miss_count"] > 0 or html_residuals:
            combined = {
                "tr_layer_misses": tr_report["misses"],
                "tr_layer_suggested": tr_report["suggested_entries"],
                "html_residuals": html_residuals,
                "summary": {
                    "tr_miss_count": tr_report["miss_count"],
                    "html_residual_count": len(html_residuals),
                },
            }
            # 输出到 stderr
            print(f"⚠️ i18n 残留中文: tr()层 {tr_report['miss_count']} 条, HTML层 {len(html_residuals)} 条",
                  file=sys.stderr)
            for r in html_residuals[:10]:
                print(f"   [{r['category']}] {r['text']} (×{r['count']})", file=sys.stderr)

            # 写 .i18n-missing.json 旁路文件（与 HTML 同目录）
            if args.output != "-":
                miss_path = os.path.splitext(args.output)[0] + ".i18n-missing.json"
                with open(miss_path, "w", encoding="utf-8") as f:
                    json.dump(combined, f, ensure_ascii=False, indent=2)
                print(f"📋 i18n 缺失条目: {miss_path}", file=sys.stderr)
        else:
            print("✅ i18n 扫描通过: 无残留中文", file=sys.stderr)

    # 持久化 LLM 翻译缓存
    if args.lang == "en":
        _flush_llm_cache()


if __name__ == "__main__":
    main()
