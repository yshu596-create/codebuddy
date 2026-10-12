#!/usr/bin/env python3
"""Fast preflight for cloud-inspector-pro generated reports.

This script is intentionally deterministic and cheap. It blocks the common
quality regressions that previously forced LLM/manual report repair.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SECRET_RE = re.compile(r"AKID[A-Za-z0-9]+|AccessKeyId|SecretId|SecretKey|TENCENTCLOUD_SECRET|password\s*=|token\s*=", re.I)
FORBIDDEN_RE = re.compile(r"\b(advisor|strategy|costopt)\b|智能顾问|重派|dispatcher|kanban|narrow", re.I)
TRUNC_RE = re.compile(r"\.\.\.|…|另\s*\d+\s*个|(?<!当)前\s*\d+\s*项|(?<![A-Za-z])top\s+\d+|more\)", re.I)
TABLE_BLANK_RE = re.compile(r"\|[^\n]*\|\n\s*\n\s*\|")
RAW_STATUS_RE = re.compile(r"\b(scan_status|caller_subuser|skipped_subuser|skipped_subuser_caller|partial_error)\b", re.I)
FILLER_HEADING_RE = re.compile(r"^#{2,4}\s*(项目详情|扫描元数据|原始数据|云安全扩展发现)\s*$", re.M)
COUNT_ONLY_CONTRADICTION_RE = re.compile(r"涉及\s*\d+\s*个(?:风险实例|资源)[\s\S]{0,80}(?:没拉到|未返回)可定位资源", re.I)

RESOURCE_ID_HEADERS = {"资源 ID", "资源ID", "实例 ID", "实例ID", "安全组 ID", "证书 ID", "桶名", "UIN"}
EMPTY_TOKENS = {"", "-", "—", "--", "N/A", "n/a", "None", "null", "未返回", "未知"}


def split_md_row(line: str):
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return []
    return [c.strip() for c in line.strip("|").split("|")]


def is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def resource_id_issues(lines):
    issues = []
    header = None
    id_indexes = []
    for idx, line in enumerate(lines, 1):
        cells = split_md_row(line)
        if not cells:
            header = None
            id_indexes = []
            continue
        if idx < len(lines):
            nxt = split_md_row(lines[idx])
            if is_separator(nxt):
                header = cells
                id_indexes = [i for i, h in enumerate(header) if h in RESOURCE_ID_HEADERS or ("资源" in h and "ID" in h.upper()) or ("实例" in h and "ID" in h.upper())]
                continue
        if is_separator(cells) or not id_indexes:
            continue
        for i in id_indexes:
            if i >= len(cells):
                issues.append({"line": idx, "header": header[i] if header and i < len(header) else "资源 ID", "value": "<missing-cell>"})
                continue
            v = cells[i].strip()
            if v in EMPTY_TOKENS:
                issues.append({"line": idx, "header": header[i] if header and i < len(header) else "资源 ID", "value": v})
    return issues


def repeated_table_rows(lines, threshold=5):
    counts = {}
    for line in lines:
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        if is_separator(split_md_row(s)):
            continue
        # Header rows may repeat across product sections; data rows repeated many times are almost always a report bug.
        if any(h in s for h in ("资源 ID", "检查项", "严重度", "报告域", "数据源", "业务 |", "产品 |", "业务方向", "地域 | 数量")):
            continue
        counts[s] = counts.get(s, 0) + 1
    return [{"row": row[:160], "count": n} for row, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= threshold]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="report markdown path")
    ap.add_argument("--json", action="store_true", help="print JSON result only")
    ap.add_argument("--allow-resource-id-placeholders", action="store_true", help="do not fail on explicit platform placeholder values")
    args = ap.parse_args()

    path = Path(args.report)
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    issues = []
    if SECRET_RE.search(text):
        issues.append({"code": "secret_terms", "message": "report contains secret/key/token markers"})
    bad_terms = sorted(set(m.group(0) for m in FORBIDDEN_RE.finditer(text)))
    if bad_terms:
        issues.append({"code": "internal_terms", "terms": bad_terms[:20]})
    if TABLE_BLANK_RE.search(text):
        issues.append({"code": "table_blank_lines", "message": "markdown table contains blank lines"})
    trunc = sorted(set(m.group(0) for m in TRUNC_RE.finditer(text)))
    if trunc:
        issues.append({"code": "truncation_markers", "markers": trunc[:20]})
    if re.search(r"^###\s*云成本", text, re.M):
        issues.append({"code": "cost_heading", "message": "cloud cost section must not appear in inspector report"})
    raw_status = sorted(set(m.group(0) for m in RAW_STATUS_RE.finditer(text)))
    if raw_status:
        issues.append({"code": "raw_status_terms", "terms": raw_status})
    audit_sec = ""
    m = re.search(r"^###\s*云审计\s*$([\s\S]*?)(?=^###\s|^##\s|\Z)", text, re.M)
    if m:
        audit_sec = m.group(1)
        if re.search(r"\|\s*[^|]+\s*\|\s*[1-9]\d*\s*\|", audit_sec) and "高危事件明细" not in audit_sec:
            issues.append({"code": "missing_audit_event_details", "message": "CloudAudit hit events but 高危事件明细 is missing"})
    filler = FILLER_HEADING_RE.search(text)
    if filler:
        issues.append({"code": "filler_heading", "message": "customer report contains filler/internal headings such as 项目详情/扫描元数据/云安全扩展发现"})
    if COUNT_ONLY_CONTRADICTION_RE.search(text):
        issues.append({"code": "count_only_resource_claim", "message": "count-only findings must not be described as locatable resource instances"})
    if "高危端口规则" in text and "公网云资源清单" not in text:
        issues.append({"code": "missing_public_cloud_resource_inventory", "message": "安全组存在高危端口但缺少公网云资源清单"})
    m_pub = re.search(r"^###\s*公网云资源清单\s*$([\s\S]*?)(?=^###\s|^##\s|\Z)", text, re.M)
    if m_pub and re.search(r"\|\s*sg-[a-z0-9]+\s*\|", m_pub.group(1), re.I):
        issues.append({"code": "security_group_as_public_resource", "message": "公网云资源清单不能把安全组 ID 当作公网云资源"})
    if m_pub:
        pub_sec = m_pub.group(1)
        bad_pub_terms = [t for t in ["证据来源", "策略接口", "不把 sg-* 当作公网云资源", "不把 sg-*"] if t in pub_sec]
        if bad_pub_terms:
            issues.append({"code": "public_inventory_internal_wording", "terms": bad_pub_terms})
        if "| 风险 | 分类 | 资源 ID | 名称 | 地域 | 公网入口 | 风险项 | 发现来源 | 建议动作 |" not in pub_sec:
            issues.append({"code": "public_inventory_schema", "message": "公网云资源清单表头必须拆分公网入口/风险项/发现来源"})
        source_as_class = False
        for line in pub_sec.splitlines():
            if not line.startswith("|") or line.startswith("|------") or "| 风险 |" in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and re.search(r"^(云防火墙|CFW|云安全中心|CSC)", cells[1]):
                source_as_class = True
                break
        if source_as_class:
            issues.append({"code": "public_inventory_source_as_class", "message": "CFW/CSC 不能作为公网资源分类，只能作为发现来源或风险项"})
        if re.search(r"\|\s*cfw_(?:lb|ins)-", pub_sec):
            issues.append({"code": "public_inventory_compound_id_not_split", "message": "cfw_lb/cfw_ins 复合 ID 必须拆为真实资源 ID + 公网入口"})

    required = ["巡检概览", "云安全"]
    missing = [x for x in required if x not in text]

    if "立即处置清单" not in text and "整改推进计划" not in text:
        missing.append("立即处置清单或整改推进计划")
    if missing:
        issues.append({"code": "missing_required_sections", "sections": missing})

    rid = resource_id_issues(lines)
    if rid and not args.allow_resource_id_placeholders:
        issues.append({"code": "empty_resource_id", "count": len(rid), "samples": rid[:20]})
    repeated = repeated_table_rows(lines)
    if repeated:
        issues.append({"code": "repeated_table_rows", "count": len(repeated), "samples": repeated[:10]})

    result = {
        "status": "ok" if not issues else "fail",
        "path": str(path),
        "bytes": len(text.encode("utf-8")),
        "issues": issues,
        "counts": {
            "bad_terms": len(bad_terms),
            "resource_id_issues": len(rid),
            "table_blank_lines": 1 if TABLE_BLANK_RE.search(text) else 0,
            "truncation_markers": len(trunc),
            "raw_status_terms": len(raw_status),
            "repeated_table_rows": len(repeated),
        },
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] == "ok":
            print("PREFLIGHT OK")
            print(json.dumps(result["counts"], ensure_ascii=False))
        else:
            print("PREFLIGHT FAIL", file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
    return 0 if result["status"] == "ok" else 2

if __name__ == "__main__":
    raise SystemExit(main())
