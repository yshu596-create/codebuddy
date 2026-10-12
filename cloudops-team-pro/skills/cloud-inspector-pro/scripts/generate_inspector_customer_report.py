#!/usr/bin/env python3
"""Build the customer-facing 8-section inspector report.

Consumes the deterministic `report_doc.md` and `summary.json` produced by
`generate_inspector_report.py`, then rewrites into the latest customer template:
巡检概览 / 云安全 / 云可靠 / 云性能 / 云限制 / 资产标签治理 / 数据完整性与复核项 / 整改推进计划.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
import re

# 输出层翻译引擎（与 base 报告同一套词典/frames）。路径锚定导入 + 优雅降级。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from lib_report_translate import translate_report  # type: ignore
except Exception:  # pragma: no cover - 兜底：缺库时 EN 退化为不翻译
    def translate_report(text: str, lang: str = "zh") -> str:  # type: ignore
        return text

# 输出语言（zh 默认；en 时全部 UI 骨架走英文，section 键用英文，且对取出的
# base 正文片段不再二次翻译——base 在 EN 模式已自带英文正文）。
# 模块加载时先读环境变量，main()/build() 可再覆盖。
_LANG = (os.environ.get("OUTPUT_LANG") or "zh").lower()
if _LANG not in ("zh", "en"):
    _LANG = "zh"

# ── base report 的 H2 section 标题：中英映射（与 lib_report_translate._ENUMS 对齐）。
#    split_h2 在 EN 模式下需用英文键命中 base 的英文 H2。──
_H2_KEYS = {
    "巡检概览": "Inspection Overview",
    "云安全": "Cloud Security",
    "云安全扩展发现": "Cloud Security Extended Findings",
    "云可靠": "Cloud Reliability",
    "云性能": "Cloud Performance",
    "云限制": "Cloud Limits",
    "资产标签治理": "Asset Tag Governance",
    "数据完整性与扫描降级说明": "Data Integrity & Scan-Degradation Notes",
    "数据完整性与复核项": "Data Integrity & Review Items",
    "立即处置清单": "Immediate Action List",
    "整改推进计划": "Remediation Plan",
}


def _hk(zh_key: str) -> str:
    """按当前语言返回 base 报告里实际的 H2 标题键。"""
    return _H2_KEYS.get(zh_key, zh_key) if _LANG == "en" else zh_key


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def split_h2(text: str):
    ms = list(re.finditer(r"^##\s+(.+)$", text, re.M))
    sections = {}
    for i, m in enumerate(ms):
        start = m.start(); end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        sections[m.group(1).strip()] = text[start:end].strip()
    return sections


def without_h2(sec: str) -> str:
    return re.sub(r"^##\s+[^\n]+\n?", "", sec.strip(), count=1, flags=re.M).strip()


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["------"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(x).replace("\n", " ").replace("|", "\\|") for x in row) + " |")
    return "\n".join(out)


def cleanup_customer_text(text: str) -> str:
    if _LANG == "en":
        repl = {
            "scan_status": "Collection Status",
            "caller_subuser": "caller is a sub-account; review by primary account or add read-only permission",
            "skipped_subuser_caller": "caller is a sub-account; this item is skipped",
            "skipped_subuser": "caller is a sub-account; this item is skipped",
            "root_aks_scan_status": "Primary-Account Key Collection Status",
            "云安全扩展发现": "Cloud Security",
            "Cloud Security Extended Findings": "Cloud Security",
            "advisor": "cloud policy",
            "strategy": "risk rule",
            "costopt": "cost optimization",
        }
    else:
        repl = {
            "scan_status": "采集状态",
            "caller_subuser": "当前调用身份为子账号，需主账号或补充只读权限复核",
            "skipped_subuser_caller": "当前调用身份为子账号，本项跳过",
            "skipped_subuser": "当前调用身份为子账号，本项跳过",
            "root_aks_scan_status": "主账号密钥采集状态",
            "云安全扩展发现": "云安全",
            "advisor": "云端策略",
            "strategy": "风险规则",
            "costopt": "成本优化",
        }
    for a, b in repl.items():
        # EN：英文 key 加词边界，避免吃掉 advisories/strategyName 等长词（同 base sanitize）
        if _LANG == "en" and not re.search(r"[\u4e00-\u9fff]", a):
            text = re.sub(rf"\b{re.escape(a)}\b", b, text, flags=re.I)
        else:
            text = re.sub(re.escape(a), b, text, flags=re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def build(input_dir: Path, output: Path, *, account: str = "", uin: str = "", report_date: str = ""):
    src = input_dir / "report_doc.md"
    if not src.exists():
        raise SystemExit(f"missing report_doc.md: {src}")
    summary = load_json(input_dir / "summary.json", {})
    collect = load_json(input_dir / "collect_summary.json", {})
    text = src.read_text(encoding="utf-8", errors="ignore")
    sections = split_h2(text)
    counts = summary.get("counts", {})
    en = _LANG == "en"
    account = account or summary.get("account") or ("Inspection Account" if en else "巡检账户")
    uin = uin or summary.get("uin") or ("not returned" if en else "未返回")
    report_date = report_date or summary.get("report_date") or dt.date.today().isoformat()
    headline = summary.get("headline") or (
        f"{account} inspection complete; see each section for detailed risks." if en
        else f"{account} 巡检完成，详细风险见各章节。")
    elapsed = collect.get("elapsed_s") or ""
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    # "no data" 占位句
    nd = (lambda name: f"No data collected for {name} this time.") if en else (lambda name: f"{name}这块我这次没拉到数据。")

    security_body = "\n\n".join(x for x in [without_h2(sections.get(_hk("云安全"), "")), without_h2(sections.get(_hk("云安全扩展发现"), ""))] if x)
    action_body = without_h2(sections.get(_hk("立即处置清单"), "")) or without_h2(sections.get(_hk("整改推进计划"), ""))

    parts = []
    # v3.4：元信息（巡检账户、生成时间）合并到「巡检概览」章节内，不再独占头部 / 分页
    if en:
        parts.append("## Inspection Overview\n")
        parts.append(f"Inspection account: {account} ({uin})  ")
        parts.append(f"Generated: {generated} GMT+8\n")
        parts.append("### Management Summary\n")
        parts.append("This edition follows the customer cloud-inspection template: Cloud Security, Cloud Reliability, Cloud Performance, Cloud Limits, Asset-Tag Governance, Data Integrity & Review Items, and Remediation Plan are delivered as separate sections; internal collection fields are not shown; items where the platform returned no locatable resource ID are listed separately for review; full details are not abbreviated.")
        parts.append("\n" + headline)
        parts.append("\n### Risk Distribution\n")
        parts.append(md_table(["Risk Area", "Key Findings", "Recommendation"], [
            ["Cloud Security", "Security groups, public exposure, identity & permissions, host security, certificates, and COS risks — see the Cloud Security section", "Prioritize converging the public access surface, review high-privilege accounts, and handle host and storage access risks"],
            ["Cloud Reliability", "Cloud-policy reliability risks across multiple core cloud products", "Confirm impact scope by business criticality; prioritize ingress, database, message-queue, and cluster-stability risks"],
            ["Cloud Performance", "Performance-side risks on CVM, database/cache, ES, TKE, etc.", "Assess capacity and scale out / throttle based on peak, average, spec limits, and business-peak windows"],
            ["Cloud Limits", "Service-limit or quota-related risks", "Confirm impact on scaling, releases, or business growth; raise quotas in advance if needed"],
            ["Asset-Tag Governance", "Missing tags on billed resources", "Complete head/business/customer accountability tags to support cost allocation and remediation closure"],
            ["Data Integrity", f"{counts.get('review_items', 'N/A')} review items where the platform returned no locatable resource ID", "Review by rule name in the console; add read-only permission and re-scan if needed"],
        ]))
        parts.append("\n### Suggested Priorities\n")
        parts.append(md_table(["Priority", "Item", "Recommendation"], [
            ["Immediate", f"{counts.get('red_actions', 'N/A')} red remediation items", "First handle direct exposure surfaces, authn/authz, public access, expired certs, and confirmed high-risk issues"],
            ["Near-term", f"{counts.get('orange_actions', 'N/A')} orange remediation items", "Assign owners by business-system ownership; complete stability, performance, and config-risk remediation"],
            ["Continuous", f"{counts.get('yellow_actions', 'N/A')} yellow remediation items plus tag-governance items", "Bring into routine governance; establish long-term baselines for tags, accounts, permissions, security groups, backup, and audit"],
        ]))
    else:
        parts.append("## 巡检概览\n")
        parts.append(f"巡检账户：{account}（{uin}）  ")
        parts.append(f"生成时间：{generated} GMT+8\n")
        parts.append("### 管理摘要\n")
        parts.append("本版按客户版云巡检模板生成：云安全、云可靠、云性能、云限制、资产标签治理、数据完整性与复核项、整改推进计划分章节交付；不展示内部采集字段；平台未返回可定位资源 ID 的项目单列复核；全量明细不做省略。")
        parts.append("\n" + headline)
        parts.append("\n### 风险分布\n")
        parts.append(md_table(["风险领域", "主要发现", "处理建议"], [
            ["云安全", "安全组、公网暴露、身份权限、主机安全、证书与对象存储风险详见云安全章节", "优先收敛公网访问面、复核高权限账号、处理主机与存储访问风险"],
            ["云可靠", "云端策略命中可靠性风险，涉及多类核心云产品", "按业务关键程度确认影响范围，优先处理入口、数据库、消息队列和集群稳定性风险"],
            ["云性能", "云服务器、数据库/缓存、ES、TKE 等存在性能侧风险", "结合峰值、均值、规格上限和业务高峰窗口进行容量评估与扩容/限流"],
            ["云限制", "存在服务限制或配额相关风险", "确认是否影响扩容、发布或业务增长，必要时提前提额"],
            ["资产标签治理", "已产生消费资源中存在标签缺失", "补齐 head/business/customer 等责任归属标签，支撑费用分摊和整改闭环"],
            ["数据完整性", f"平台未返回可定位资源 ID 的复核项 {counts.get('review_items', '未统计')} 类", "在控制台按规则名复核，必要时补充只读权限后复扫"],
        ]))
        parts.append("\n### 建议优先处理事项\n")
        parts.append(md_table(["优先顺序", "事项", "建议"], [
            ["立即处理", f"红色整改项 {counts.get('red_actions', '未统计')} 项", "先处理直接暴露面、认证授权、公开访问、证书过期和已确认高危风险"],
            ["近期处理", f"橙色整改项 {counts.get('orange_actions', '未统计')} 项", "按业务系统归属分派负责人，完成稳定性、性能和配置风险整改"],
            ["持续治理", f"黄色整改项 {counts.get('yellow_actions', '未统计')} 项及标签治理项", "纳入例行治理，建立标签、账号、权限、安全组、备份和审计的长期基线"],
        ]))

    # v3.8：从 base 的「巡检概览」章节抠出「重点资源 TOP N」H3 子节，原样并入客户版概览
    overview_body = sections.get(_hk("巡检概览"), "")
    if overview_body:
        if en:
            m = re.search(r"(### Key Resource Summary.*?)(?=\n### |\n## |\Z)", overview_body, re.S)
        else:
            m = re.search(r"(### 重点资源汇总\(共 \d+ 条\).*?)(?=\n### |\n## |\Z)", overview_body, re.S)
        if m:
            parts.append("\n" + m.group(1).rstrip() + "\n")
    if en:
        parts.append("\n### Remediation Cadence\n")
        parts.append(md_table(["Time Window", "Target"], [
            ["Within 24h", "Confirm red risks and apply emergency containment to prevent public exposure, privilege abuse, or data-disclosure risks from expanding"],
            ["Within 3 days", "Assign owners for orange risks and set remediation plans, clarifying business impact, remediation actions, and re-check time"],
            ["Within 7 days", "Complete the first round of batch governance for tags, permissions, security groups, capacity, and backup"],
            ["Before next inspection", "Re-scan to confirm risk-count reduction; form a remediation closure and an exception list"],
        ]))
    else:
        parts.append("\n### 整改节奏建议\n")
        parts.append(md_table(["时间窗口", "建议目标"], [
            ["24 小时内", "完成红色风险确认和应急收敛，避免公网暴露、权限滥用或数据公开风险扩大"],
            ["3 天内", "完成橙色风险责任人分派和整改计划，明确影响业务、整改动作和复查时间"],
            ["7 天内", "完成标签、权限、安全组、容量与备份类批量治理项第一轮处理"],
            ["下次巡检前", "复扫确认风险数量下降，形成整改闭环和例外清单"],
        ]))

    if en:
        chapters = [
            ("Cloud Security", security_body or nd("Cloud Security")),
            ("Cloud Reliability", without_h2(sections.get(_hk("云可靠"), "")) or nd("Cloud Reliability")),
            ("Cloud Performance", without_h2(sections.get(_hk("云性能"), "")) or nd("Cloud Performance")),
            ("Cloud Limits", without_h2(sections.get(_hk("云限制"), "")) or nd("Cloud Limits")),
            ("Asset Tag Governance", without_h2(sections.get(_hk("资产标签治理"), "")) or nd("Asset Tag Governance")),
            ("Data Integrity & Review Items", without_h2(sections.get(_hk("数据完整性与扫描降级说明"), "")) or without_h2(sections.get(_hk("数据完整性与复核项"), "")) or "No scan degradation or platform-unlocatable review items were found this time."),
            ("Remediation Plan", action_body or "No remediation items were generated this time."),
        ]
    else:
        chapters = [
            ("云安全", security_body or "云安全这块我这次没拉到数据。"),
            ("云可靠", without_h2(sections.get("云可靠", "")) or "云可靠这块我这次没拉到数据。"),
            ("云性能", without_h2(sections.get("云性能", "")) or "云性能这块我这次没拉到数据。"),
            ("云限制", without_h2(sections.get("云限制", "")) or "云限制这块我这次没拉到数据。"),
            ("资产标签治理", without_h2(sections.get("资产标签治理", "")) or "资产标签治理这块我这次没拉到数据。"),
            ("数据完整性与复核项", without_h2(sections.get("数据完整性与扫描降级说明", "")) or without_h2(sections.get("数据完整性与复核项", "")) or "本次未发现扫描降级或平台未返回可定位资源 ID 的复核项。"),
            ("整改推进计划", action_body or "本次未生成整改推进项。"),
        ]
    for title, body in chapters:
        parts.append(f"\n## {title}\n")
        parts.append(body)

    if en:
        next_date = (dt.datetime.strptime(report_date, "%Y-%m-%d").date() + dt.timedelta(days=7)).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date) else "before next inspection"
        parts.append(f"\n\nReport generated: {generated} GMT+8 | Next review: {next_date}")
    else:
        next_date = (dt.datetime.strptime(report_date, "%Y-%m-%d").date() + dt.timedelta(days=7)).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date) else "下次巡检前"
        parts.append(f"\n\n报告生成：{generated} GMT+8 | 下次复查：{next_date}")
    out = cleanup_customer_text("\n\n".join(parts))
    # 输出层最终兜底：ZH 透传（字节不变），EN 仅清扫从 base 正文/数据片段漏出的中文。
    out = translate_report(out, _LANG)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(out, encoding="utf-8")
    return {"path": str(output), "bytes": len(out.encode("utf-8")), "sections": list(split_h2(out).keys())}


def _run_smartpage_validator(md_path: str) -> None:
    """v3.6 根治-2：customer 产物末尾的 Markdown 兼容性校验闸门。"""
    import subprocess as _sp
    from pathlib import Path as _P
    import sys as _sys
    script = _P(__file__).parent / "validate_smartpage_md.py"
    if not script.exists() or not _P(md_path).exists():
        return
    r = _sp.run(["python3", str(script), str(md_path)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=_sys.stderr)
        print(f"\n[validate] ❌ {md_path} 未通过 Markdown 兼容性校验，拒绝输出。",
              file=_sys.stderr)
        raise SystemExit(2)
    print(r.stdout.strip(), file=_sys.stderr)


def main():
    global _LANG
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--account")
    ap.add_argument("--uin")
    ap.add_argument("--date")
    ap.add_argument("--lang", choices=["zh", "en"], default=os.environ.get("OUTPUT_LANG", "zh"),
                    help="输出语言：zh（默认）或 en。须与 base 报告的 --lang 一致。")
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args()
    _LANG = args.lang if args.lang in ("zh", "en") else "zh"
    summary = build(Path(args.input_dir), Path(args.output), account=args.account or "", uin=args.uin or "", report_date=args.date or "")
    if not args.skip_validate:
        _run_smartpage_validator(args.output)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
