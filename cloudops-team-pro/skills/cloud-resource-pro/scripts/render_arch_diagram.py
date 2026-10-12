#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_arch_diagram.py — 把 query_resources.py 产出的 resources.json 渲染成
单文件 HTML 云架构拓扑图（深色玻璃拟态 + 内联 SVG 分层布局）。

设计要点：
  · 纯渲染，不碰云 API、不碰凭证——输入只有 resources.json（resource 清点结果）。
  · 分层拓扑：账号层 → 地域层 → 网络层(VPC/子网/安全组) → 计算层(CVM/Lighthouse/TKE)
    → 数据层(CDB/Redis) → 存储层(CBS/COS) → 接入层(CLB/EIP)，按真实资源数量动态出层。
  · 自包含：所有 CSS/SVG 内联，单文件可直接预览，无外链依赖。
  · 双语：--lang zh|en 控制界面骨架文案；地域码/资源 ID 等数据值不翻译。

用法：
  python3 render_arch_diagram.py --in <resources.json> --out <arch.html> --lang zh \
      [--cloud tencent] [--project YX] [--uin 100012345678] \
      [--assessment <assessment.json>]   # 可选：agent 产出的架构评估，注入「架构评估」章节
退出码：0 成功；2 输入缺失/解析失败。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import sys

# ───────────────────────── i18n ─────────────────────────

I18N = {
    "zh": {
        "title": "云架构拓扑图",
        "subtitle": "基于资源清点的当前架构视图（只读盘点 · 非实时控制台快照）",
        "account": "账号",
        "uin": "主账号 UIN",
        "scanned_regions": "已扫描地域",
        "generated_at": "生成时间",
        "kpi_regions": "活跃地域",
        "kpi_compute": "计算实例",
        "kpi_db": "数据库实例",
        "kpi_storage": "存储桶/盘",
        "kpi_network": "网络对象",
        "layer_account": "账号 / 身份",
        "layer_access": "接入层",
        "layer_compute": "计算层",
        "layer_data": "数据层",
        "layer_storage": "存储层",
        "layer_network": "网络层",
        "region_block": "地域",
        "no_data": "未发现资源",
        "footer": "数据来源：云平台只读 API 资源清点；本图为架构概览，不含实时运行状态。",
        "legend": "图例",
        "sub_users": "子账户",
        "collaborators": "协作者",
        "cos_buckets": "COS 桶",
        "disclaimer_title": "说明",
        "disclaimer": "本架构图由资源清点数据自动生成，按资源类型分层呈现拓扑关系；架构评估章节基于当前清点快照做定性分析，仅供架构演进参考，不构成实时运行状态判断。跨账号架构对比、IaC 反向生成等能力正在建设中。",
        "disclaimer_topo": "本架构图由资源清点数据自动生成，按资源类型分层呈现拓扑关系，仅供架构梳理参考，不构成实时运行状态判断。架构评估、跨账号架构对比、IaC 反向生成等能力正在建设中。",
        "regions_detail": "地域资源分布",
        "region_col": "地域",
        "count_col": "资源数",
        "assess_title": "架构评估",
        "assess_score": "架构健康度",
        "assess_grade": "评级",
        "assess_summary": "评估摘要",
        "assess_dims": "维度评估",
        "assess_dim_col": "评估维度",
        "assess_dim_status": "状态",
        "assess_dim_note": "说明",
        "assess_risks": "风险与隐患",
        "assess_recos": "优化建议",
        "assess_level_good": "良好",
        "assess_level_warn": "需关注",
        "assess_level_risk": "风险",
        "assess_no_risk": "✅ 未发现明显架构风险项",
        "assess_no_reco": "✅ 当前架构无紧急优化项",
        "assess_disclaimer": "评估基于只读清点快照与通用架构最佳实践做定性分析，具体改造请结合业务 SLA 与成本评估。",
    },
    "en": {
        "title": "Cloud Architecture Topology",
        "subtitle": "Current architecture view from resource inventory (read-only · not a live console snapshot)",
        "account": "Account",
        "uin": "Root UIN",
        "scanned_regions": "Scanned regions",
        "generated_at": "Generated at",
        "kpi_regions": "Active regions",
        "kpi_compute": "Compute instances",
        "kpi_db": "Database instances",
        "kpi_storage": "Buckets / disks",
        "kpi_network": "Network objects",
        "layer_account": "Account / Identity",
        "layer_access": "Access layer",
        "layer_compute": "Compute layer",
        "layer_data": "Data layer",
        "layer_storage": "Storage layer",
        "layer_network": "Network layer",
        "region_block": "Region",
        "no_data": "No resources found",
        "footer": "Source: read-only cloud API resource inventory; this is an architecture overview, not live runtime status.",
        "legend": "Legend",
        "sub_users": "Sub-users",
        "collaborators": "Collaborators",
        "cos_buckets": "COS buckets",
        "disclaimer_title": "Note",
        "disclaimer": "This diagram is auto-generated from resource inventory data, laying out topology by resource layer; the assessment section is a qualitative analysis based on the current inventory snapshot, for architecture-evolution reference only, not a live runtime judgment. Cross-account architecture diff and IaC reverse generation are under construction.",
        "disclaimer_topo": "This diagram is auto-generated from resource inventory data, laying out topology by resource layer, for architecture-review reference only, not a live runtime judgment. Architecture assessment, cross-account architecture diff and IaC reverse generation are under construction.",
        "regions_detail": "Resource distribution by region",
        "region_col": "Region",
        "count_col": "Resources",
        "assess_title": "Architecture Assessment",
        "assess_score": "Architecture health",
        "assess_grade": "Grade",
        "assess_summary": "Assessment summary",
        "assess_dims": "Dimension assessment",
        "assess_dim_col": "Dimension",
        "assess_dim_status": "Status",
        "assess_dim_note": "Note",
        "assess_risks": "Risks & weak spots",
        "assess_recos": "Optimization advice",
        "assess_level_good": "Good",
        "assess_level_warn": "Watch",
        "assess_level_risk": "Risk",
        "assess_no_risk": "✅ No notable architecture risks found",
        "assess_no_reco": "✅ No urgent optimization items at present",
        "assess_disclaimer": "Assessment is a qualitative analysis from a read-only inventory snapshot against common architecture best practices; validate any change against your business SLA and cost.",
    },
}

# 资源类型 → (emoji 图标, 颜色)
NODE_STYLE = {
    "cvm": ("🖥️", "#5b8def"),
    "lighthouse": ("💡", "#5b8def"),
    "tke": ("☸️", "#3fb6c9"),
    "cdb": ("🗄️", "#e0a458"),
    "redis": ("⚡", "#e0a458"),
    "cbs": ("💾", "#9b8cf0"),
    "cos": ("🪣", "#9b8cf0"),
    "clb": ("⚖️", "#52c happy"),  # placeholder, fixed below
    "eip": ("🌐", "#46c98b"),
    "vpc": ("🔗", "#8aa0b8"),
    "sg": ("🛡️", "#8aa0b8"),
}
NODE_STYLE["clb"] = ("⚖️", "#46c98b")


def _t(lang: str, key: str) -> str:
    return I18N.get(lang, I18N["zh"]).get(key, key)


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


# ───────────────────────── 节点构造 ─────────────────────────

def build_nodes(res: dict, lang: str) -> dict:
    """从 resources 聚合块抽出每层的节点卡片列表。返回 {layer: [card,...]}。"""
    r = res.get("resources", {})
    cos = res.get("cos", {})
    sub = res.get("subaccounts", {})

    def card(icon, color, label, count, sub=""):
        return {"icon": icon, "color": color, "label": label, "count": count, "sub": sub}

    layers = {"network": [], "compute": [], "data": [], "storage": [], "access": []}

    # 网络层
    vpc = r.get("vpc", {})
    if vpc.get("total", 0):
        i, c = NODE_STYLE["vpc"]
        layers["network"].append(card(i, c, "VPC", vpc["total"],
                                      f"{vpc.get('subnet_total', 0)} subnets"))
    sg = r.get("sg", {})
    if sg.get("total", 0):
        i, c = NODE_STYLE["sg"]
        layers["network"].append(card(i, c, "Security Group", sg["total"]))

    # 计算层
    cvm = r.get("cvm", {})
    if cvm.get("total", 0):
        i, c = NODE_STYLE["cvm"]
        gpu = cvm.get("gpu_total", 0)
        s = f"{cvm.get('running', 0)} running" + (f" · {gpu} GPU" if gpu else "")
        layers["compute"].append(card(i, c, "CVM", cvm["total"], s))
    lh = r.get("lighthouse", {})
    if lh.get("total", 0):
        i, c = NODE_STYLE["lighthouse"]
        layers["compute"].append(card(i, c, "Lighthouse", lh["total"]))
    tke = r.get("tke", {})
    if tke.get("cluster_total", 0):
        i, c = NODE_STYLE["tke"]
        layers["compute"].append(card(i, c, "TKE", tke["cluster_total"],
                                      f"{tke.get('node_total', 0)} nodes"))

    # 数据层
    cdb = r.get("cdb", {})
    if cdb.get("total", 0):
        i, c = NODE_STYLE["cdb"]
        layers["data"].append(card(i, c, "MySQL (CDB)", cdb["total"]))
    redis = r.get("redis", {})
    if redis.get("total", 0):
        i, c = NODE_STYLE["redis"]
        layers["data"].append(card(i, c, "Redis", redis["total"]))

    # 存储层
    cbs = r.get("cbs", {})
    if cbs.get("total", 0):
        i, c = NODE_STYLE["cbs"]
        layers["storage"].append(card(i, c, "CBS", cbs["total"],
                                      f"{cbs.get('total_gb', 0)} GB"))
    buckets = cos.get("buckets", [])
    if buckets:
        i, c = NODE_STYLE["cos"]
        layers["storage"].append(card(i, c, "COS", len(buckets)))

    # 接入层
    clb = r.get("clb", {})
    if clb.get("total", 0):
        i, c = NODE_STYLE["clb"]
        layers["access"].append(card(i, c, "CLB", clb["total"]))
    eip = r.get("eip", {})
    if eip.get("total", 0):
        i, c = NODE_STYLE["eip"]
        unb = eip.get("unbound", 0)
        layers["access"].append(card(i, c, "EIP", eip["total"],
                                     f"{unb} unbound" if unb else ""))
    return layers


def _kpis(res: dict) -> dict:
    r = res.get("resources", {})
    compute = (r.get("cvm", {}).get("total", 0)
               + r.get("lighthouse", {}).get("total", 0)
               + r.get("tke", {}).get("node_total", 0))
    db = r.get("cdb", {}).get("total", 0) + r.get("redis", {}).get("total", 0)
    storage = r.get("cbs", {}).get("total", 0) + len(res.get("cos", {}).get("buckets", []))
    network = (r.get("vpc", {}).get("total", 0) + r.get("sg", {}).get("total", 0)
               + r.get("eip", {}).get("total", 0) + r.get("clb", {}).get("total", 0))
    return {
        "regions": len(res.get("scanned_regions", [])),
        "compute": compute, "db": db, "storage": storage, "network": network,
    }


# ───────────────────────── HTML 渲染 ─────────────────────────

def render_card(card) -> str:
    sub = f'<div class="node-sub">{_esc(card["sub"])}</div>' if card["sub"] else ""
    return f'''<div class="node" style="--nc:{card['color']}">
      <div class="node-ico">{card['icon']}</div>
      <div class="node-body">
        <div class="node-label">{_esc(card['label'])}</div>
        <div class="node-count">{_esc(card['count'])}</div>{sub}
      </div>
    </div>'''


def render_layer(title: str, cards: list, lang: str) -> str:
    if not cards:
        return ""
    inner = "".join(render_card(c) for c in cards)
    return f'''<div class="layer">
      <div class="layer-title">{_esc(title)}</div>
      <div class="layer-nodes">{inner}</div>
    </div>'''


# 维度/风险 状态等级 → (颜色, i18n key)
_LEVEL_STYLE = {
    "good": ("#46c98b", "assess_level_good"),
    "warn": ("#e0a458", "assess_level_warn"),
    "risk": ("#ef6f6f", "assess_level_risk"),
}


def _grade_color(grade: str) -> str:
    g = (grade or "").strip().upper()[:1]
    return {"A": "#46c98b", "B": "#7ec98b", "C": "#e0a458",
            "D": "#ef9a6f", "E": "#ef6f6f", "F": "#ef6f6f"}.get(g, "#5b8def")


def render_assessment(assessment: dict, lang: str) -> str:
    """把 agent 产出的评估 JSON 渲染成深色玻璃拟态「架构评估」章节。

    assessment schema（全部可选，缺字段则该块不渲染）：
      score: int 0-100        架构健康度评分
      grade: str "A".."F"     评级
      summary: str            评估摘要（≤300 字，语种由调用方保证与 lang 一致）
      dimensions: [{name, status: good|warn|risk, note}]   维度评估
      risks: [str]            风险与隐患
      recommendations: [str]  优化建议
    """
    if not assessment:
        return ""

    score = assessment.get("score")
    grade = assessment.get("grade", "")
    summary = assessment.get("summary", "")
    dims = assessment.get("dimensions", []) or []
    risks = assessment.get("risks", []) or []
    recos = assessment.get("recommendations", []) or []

    # 评分 + 评级头
    head_bits = []
    if isinstance(score, (int, float)):
        head_bits.append(
            f'<div class="assess-score"><span class="assess-score-val">{int(score)}</span>'
            f'<span class="assess-score-max">/100</span>'
            f'<div class="assess-score-lab">{_t(lang,"assess_score")}</div></div>')
    if grade:
        gc = _grade_color(grade)
        head_bits.append(
            f'<div class="assess-grade" style="--gc:{gc}">'
            f'<span class="assess-grade-val">{_esc(grade)}</span>'
            f'<div class="assess-score-lab">{_t(lang,"assess_grade")}</div></div>')
    summary_html = (f'<div class="assess-summary">{_esc(summary)}</div>'
                    if summary else "")
    head = (f'<div class="assess-head">{"".join(head_bits)}{summary_html}</div>'
            if (head_bits or summary_html) else "")

    # 维度表
    dim_html = ""
    if dims:
        rows = []
        for d in dims:
            st = (d.get("status") or "good").lower()
            color, lvl_key = _LEVEL_STYLE.get(st, _LEVEL_STYLE["good"])
            rows.append(
                f'<tr><td>{_esc(d.get("name",""))}</td>'
                f'<td><span class="lvl" style="--lc:{color}">{_t(lang,lvl_key)}</span></td>'
                f'<td>{_esc(d.get("note",""))}</td></tr>')
        dim_html = f'''<div class="assess-sub">{_t(lang,"assess_dims")}</div>
        <table><thead><tr>
          <th>{_t(lang,"assess_dim_col")}</th>
          <th>{_t(lang,"assess_dim_status")}</th>
          <th>{_t(lang,"assess_dim_note")}</th>
        </tr></thead><tbody>{"".join(rows)}</tbody></table>'''

    def _li_block(title_key, items, empty_key, marker):
        body = ("".join(f'<li>{_esc(x)}</li>' for x in items)
                if items else f'<li class="ok">{_t(lang, empty_key)}</li>')
        return (f'<div class="assess-sub">{_t(lang,title_key)}</div>'
                f'<ul class="assess-list {marker}">{body}</ul>')

    risks_html = _li_block("assess_risks", risks, "assess_no_risk", "risk-list")
    recos_html = _li_block("assess_recos", recos, "assess_no_reco", "reco-list")

    return f'''<div class="glass assess">
    <div class="section-h">🧭 {_t(lang, "assess_title")}</div>
    {head}
    {dim_html}
    <div class="grid2 assess-grid">
      <div>{risks_html}</div>
      <div>{recos_html}</div>
    </div>
    <div class="assess-foot">{_t(lang, "assess_disclaimer")}</div>
  </div>'''


def render_html(res: dict, lang: str, cloud: str, project: str, uin: str,
                assessment: dict | None = None) -> str:
    acct = res.get("account", {})
    cloud = cloud or acct.get("cloud", "tencent")
    project = project or acct.get("project", "")
    uin = uin or acct.get("uin", "")
    regions = res.get("scanned_regions", [])
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    layers = build_nodes(res, lang)
    kp = _kpis(res)
    badge = f"{cloud} · {project} · UIN {uin}" if uin else f"{cloud} · {project}"

    # 分层（从上到下：接入 → 计算 → 数据 → 存储；网络作为底座单列）
    layer_html = (
        render_layer(_t(lang, "layer_access"), layers["access"], lang)
        + render_layer(_t(lang, "layer_compute"), layers["compute"], lang)
        + render_layer(_t(lang, "layer_data"), layers["data"], lang)
        + render_layer(_t(lang, "layer_storage"), layers["storage"], lang)
        + render_layer(_t(lang, "layer_network"), layers["network"], lang)
    )
    if not layer_html.strip():
        layer_html = f'<div class="empty">{_t(lang, "no_data")}</div>'

    # 地域分布表（CVM by_region 为主）
    by_region = res.get("resources", {}).get("cvm", {}).get("by_region", {})
    region_rows = "".join(
        f"<tr><td>{_esc(rg)}</td><td>{_esc(n)}</td></tr>"
        for rg, n in sorted(by_region.items(), key=lambda x: -x[1])
    ) or f'<tr><td colspan="2" style="text-align:center;opacity:.6">{_t(lang, "no_data")}</td></tr>'

    # 账号层标识卡
    sub = res.get("subaccounts", {})
    id_chips = (
        f'<span class="chip">{_t(lang,"sub_users")}: {len(sub.get("sub_users", []))}</span>'
        f'<span class="chip">{_t(lang,"collaborators")}: {len(sub.get("collaborators", []))}</span>'
        f'<span class="chip">{_t(lang,"cos_buckets")}: {len(res.get("cos", {}).get("buckets", []))}</span>'
    )

    def kpi(label, val, color):
        return f'''<div class="kpi"><div class="kpi-val" style="color:{color}">{val}</div>
        <div class="kpi-label">{_esc(label)}</div></div>'''

    kpis_html = (
        kpi(_t(lang, "kpi_regions"), kp["regions"], "#5b8def")
        + kpi(_t(lang, "kpi_compute"), kp["compute"], "#46c98b")
        + kpi(_t(lang, "kpi_db"), kp["db"], "#e0a458")
        + kpi(_t(lang, "kpi_storage"), kp["storage"], "#9b8cf0")
        + kpi(_t(lang, "kpi_network"), kp["network"], "#3fb6c9")
    )

    return f'''<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_t(lang, "title")} · {_esc(badge)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
    background: radial-gradient(1200px 600px at 20% -10%, #1b2a4a 0%, #0e1726 55%, #0a0f1a 100%);
    color: #e7eefc; min-height: 100vh; padding: 28px 22px 40px;
  }}
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
  header {{ margin-bottom: 20px; }}
  h1 {{ font-size: 24px; font-weight: 700; letter-spacing: .5px; }}
  .sub {{ color: #9fb2d0; font-size: 13px; margin-top: 6px; }}
  .badge {{ display:inline-block; margin-top:10px; padding:5px 12px; border-radius:8px;
    background: rgba(91,141,239,.15); border:1px solid rgba(91,141,239,.4);
    color:#bcd0f5; font-size:12.5px; font-weight:600; letter-spacing:.3px; }}
  .meta {{ color:#7f93b3; font-size:12px; margin-top:8px; }}
  .glass {{ background: rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08);
    border-radius:16px; padding:18px 20px; backdrop-filter: blur(8px);
    box-shadow: 0 8px 30px rgba(0,0,0,.25); margin-bottom:18px; }}
  .kpis {{ display:grid; grid-template-columns: repeat(5,1fr); gap:12px; }}
  .kpi {{ text-align:center; padding:14px 8px; border-radius:12px;
    background: rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.06); }}
  .kpi-val {{ font-size:28px; font-weight:800; line-height:1; }}
  .kpi-label {{ font-size:12px; color:#9fb2d0; margin-top:7px; }}
  .id-chips {{ margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; }}
  .chip {{ font-size:12px; padding:4px 10px; border-radius:20px;
    background: rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1); color:#c5d4ee; }}
  .section-h {{ font-size:15px; font-weight:700; margin:4px 0 14px; color:#dbe6fb;
    display:flex; align-items:center; gap:8px; }}
  .section-h::before {{ content:""; width:4px; height:15px; border-radius:3px;
    background: linear-gradient(180deg,#5b8def,#46c98b); display:inline-block; }}
  .layer {{ margin-bottom:14px; position:relative; }}
  .layer:not(:last-child)::after {{ content:"▾"; position:absolute; left:50%; bottom:-13px;
    transform:translateX(-50%); color:#3a4d70; font-size:13px; }}
  .layer-title {{ font-size:12px; color:#8aa0c4; text-transform:uppercase;
    letter-spacing:1px; margin-bottom:9px; font-weight:600; }}
  .layer-nodes {{ display:flex; gap:12px; flex-wrap:wrap; }}
  .node {{ display:flex; align-items:center; gap:11px; min-width:150px;
    padding:12px 15px; border-radius:12px; background: rgba(255,255,255,.05);
    border:1px solid color-mix(in srgb, var(--nc) 45%, transparent);
    border-left:3px solid var(--nc); transition:transform .15s; }}
  .node:hover {{ transform:translateY(-2px); }}
  .node-ico {{ font-size:22px; width:30px; text-align:center; }}
  .node-label {{ font-size:13.5px; font-weight:700; color:#eaf1ff; }}
  .node-count {{ font-size:21px; font-weight:800; color:var(--nc); line-height:1.1; }}
  .node-sub {{ font-size:11px; color:#90a4c4; margin-top:2px; }}
  .empty {{ text-align:center; padding:40px; color:#7f93b3; font-size:14px; }}
  .grid2 {{ display:grid; grid-template-columns: 1.5fr 1fr; gap:18px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:9px 12px; text-align:left; border-bottom:1px solid rgba(255,255,255,.07); }}
  th {{ color:#9fb2d0; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
  td {{ color:#dbe6fb; }}
  .disclaimer {{ font-size:12.5px; color:#9fb2d0; line-height:1.7; }}
  .disclaimer b {{ color:#cdd9f0; }}
  .assess-head {{ display:flex; align-items:center; gap:18px; flex-wrap:wrap; margin-bottom:16px; }}
  .assess-score, .assess-grade {{ text-align:center; padding:10px 16px; border-radius:14px;
    background: rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.09); min-width:96px; }}
  .assess-score-val {{ font-size:34px; font-weight:800; color:#5b8def; line-height:1; }}
  .assess-score-max {{ font-size:14px; font-weight:700; color:#7f93b3; }}
  .assess-grade-val {{ font-size:34px; font-weight:800; color:var(--gc); line-height:1; }}
  .assess-score-lab {{ font-size:11.5px; color:#9fb2d0; margin-top:6px; }}
  .assess-summary {{ flex:1; min-width:240px; font-size:13.5px; color:#dbe6fb; line-height:1.7;
    padding:10px 14px; border-left:3px solid #5b8def; background:rgba(91,141,239,.07);
    border-radius:0 10px 10px 0; }}
  .assess-sub {{ font-size:13px; font-weight:700; color:#bcd0f5; margin:14px 0 8px; }}
  .lvl {{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:700;
    color:var(--lc); background: color-mix(in srgb, var(--lc) 16%, transparent);
    border:1px solid color-mix(in srgb, var(--lc) 45%, transparent); }}
  .assess-grid {{ margin-top:4px; }}
  .assess-list {{ list-style:none; margin:0; padding:0; }}
  .assess-list li {{ font-size:13px; color:#dbe6fb; line-height:1.6; padding:7px 10px 7px 26px;
    position:relative; border-bottom:1px solid rgba(255,255,255,.05); }}
  .assess-list li::before {{ position:absolute; left:7px; top:7px; font-size:12px; }}
  .risk-list li::before {{ content:"⚠"; color:#e0a458; }}
  .reco-list li::before {{ content:"➜"; color:#46c98b; }}
  .assess-list li.ok {{ color:#46c98b; }}
  .assess-list li.ok::before {{ content:""; }}
  .assess-foot {{ font-size:12px; color:#7f93b3; margin-top:14px; line-height:1.6;
    border-top:1px solid rgba(255,255,255,.07); padding-top:12px; }}
  footer {{ text-align:center; color:#5f7193; font-size:11.5px; margin-top:24px; }}
  @media (max-width:760px) {{ .kpis{{grid-template-columns:repeat(2,1fr)}} .grid2{{grid-template-columns:1fr}} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🏗️ {_t(lang, "title")}</h1>
    <div class="sub">{_t(lang, "subtitle")}</div>
    <div class="badge">{_t(lang, "account")}: {_esc(badge)}</div>
    <div class="meta">{_t(lang,"scanned_regions")}: {len(regions)} · {_t(lang,"generated_at")}: {now}</div>
  </header>

  <div class="glass">
    <div class="kpis">{kpis_html}</div>
    <div class="id-chips">{id_chips}</div>
  </div>

  <div class="glass">
    <div class="section-h">{_t(lang, "title")}</div>
    {layer_html}
  </div>

  {render_assessment(assessment, lang)}

  <div class="grid2">
    <div class="glass">
      <div class="section-h">{_t(lang, "regions_detail")}</div>
      <table>
        <thead><tr><th>{_t(lang,"region_col")}</th><th>{_t(lang,"count_col")} (CVM)</th></tr></thead>
        <tbody>{region_rows}</tbody>
      </table>
    </div>
    <div class="glass">
      <div class="section-h">{_t(lang, "disclaimer_title")}</div>
      <div class="disclaimer">{_t(lang, "disclaimer" if assessment else "disclaimer_topo")}</div>
    </div>
  </div>

  <footer>{_t(lang, "footer")}</footer>
</div>
</body>
</html>'''


# ───────────────────────── 主流程 ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="渲染云架构拓扑图 HTML")
    ap.add_argument("--in", dest="inp", required=True, help="resources.json 路径")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--cloud", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--uin", default="")
    ap.add_argument("--assessment", default="",
                    help="架构评估 JSON 路径（agent 产出，可选）；缺省则只出拓扑图不出评估章节")
    args = ap.parse_args()

    if not os.path.isfile(args.inp):
        print(json.dumps({"status": "error", "error": f"input not found: {args.inp}"},
                         ensure_ascii=False))
        return 2
    try:
        with open(args.inp, encoding="utf-8") as f:
            res = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"parse failed: {e}"},
                         ensure_ascii=False))
        return 2

    assessment = None
    if args.assessment:
        if not os.path.isfile(args.assessment):
            print(json.dumps({"status": "error",
                              "error": f"assessment not found: {args.assessment}"},
                             ensure_ascii=False))
            return 2
        try:
            with open(args.assessment, encoding="utf-8") as f:
                assessment = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"status": "error",
                              "error": f"assessment parse failed: {e}"},
                             ensure_ascii=False))
            return 2

    html_str = render_html(res, args.lang, args.cloud, args.project, args.uin,
                           assessment=assessment)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_str)

    acct = res.get("account", {})
    print(json.dumps({
        "status": "ok", "out_path": args.out, "lang": args.lang,
        "cloud": args.cloud or acct.get("cloud", ""),
        "project": args.project or acct.get("project", ""),
        "uin": args.uin or acct.get("uin", ""),
        "has_assessment": bool(assessment),
        "kpis": _kpis(res),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
