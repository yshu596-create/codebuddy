#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_resource_html.py — 把 query_resources.py 的 resources.json 渲染为单文件 HTML 资源看板。

输入:
  --data    /path/to/resources.json          # query_resources.py 的输出
  --output  /path/to/resource-{cloud}-{project}-{uin}.html  # 落盘路径(不指定则打印到 stdout)
  --account "<账户昵称>"                       # 报告头展示(可选, 默认回落到 UIN)
  --lang    zh|en                             # output_lang 铁律: 决定全部 UI 文案语言
  --offline /path/to/chart.umd.min.js         # 内嵌 Chart.js(离线场景)

输出:
  单文件 HTML(深色玻璃拟态看板 + Chart.js 地域分布图 + 全量资源明细)
  · 默认通过 jsdelivr CDN 引入 Chart.js
  · AK/SK 绝不出现(上游脚本不落盘明文凭证)

设计原则(对齐 cloud-auditor-pro / cloud-billing-pro):
  · 全量展示: CVM / GPU / TKE / COS / 子账户明细全数列出, 长表 max-height 滚动
  · 计数型资源(CBS/CLB/VPC/EIP/SG/CDB/Redis/Lighthouse)聚合成概览表
  · 智能洞察卡(规模/GPU/闲置EIP/地域分散/降级)由规则引擎生成
  · 单文件无外链(除 Chart.js CDN 一行 script)
  · output_lang: --lang 决定 UI 文案; 地域/产品枚举名走共享 i18n.tr()

字段契约(来自 query_resources.py):
  status: "ok" / "need_credentials" / "auth_failed"
  account: { cloud, project, uin, app_id, region_default }
  scanned_regions: [str]
  elapsed_s: float
  subaccounts: { status, sub_users:[...], collaborators:[...], error }
  cos: { status, buckets:[{name, region, create_date}], error }
  resources: {
    cvm: {total, running, gpu_total, by_region, gpu_instances:[...], instances:[...], errors},
    tke: {cluster_total, node_total, node_specs:{spec:n}, clusters:[...], errors},
    cbs/clb/vpc/eip/sg/cdb/redis/lighthouse: {total, by_region, errors, ...}
  }
"""

import argparse
import json
import os
import sys
from html import escape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 本地 UI 字符串表
from lib_i18n_resource import L, region_name  # noqa: E402

# 共享团队 i18n(zh->en 枚举翻译: 地域名 / 产品名)
_LIB = SCRIPT_DIR.parents[1] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from i18n import tr  # noqa: E402
except Exception:
    def tr(s, lang="zh"):
        return s

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"


def esc(v):
    return escape(str(v if v is not None else ""))


def _num(v):
    try:
        return int(v)
    except Exception:
        return 0


# ───────────────────────── 表格构建 ─────────────────────────

def _table(headers, rows, *, empty_msg="", num_cols=None):
    """生成 HTML 表格。num_cols: 右对齐数字列下标集合。"""
    num_cols = num_cols or set()
    if not rows:
        return f'<div class="empty">{esc(empty_msg)}</div>'
    th = "".join(f"<th{' class=\"num\"' if i in num_cols else ''}>{esc(h)}</th>"
                 for i, h in enumerate(headers))
    trs = []
    for row in rows:
        tds = "".join(
            f"<td{' class=\"num\"' if i in num_cols else ''}>{cell}</td>"
            for i, cell in enumerate(row)
        )
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def _pill(text, kind="info"):
    return f'<span class="pill {kind}">{esc(text)}</span>'


def _state_pill(state):
    s = (state or "").upper()
    if s == "RUNNING":
        return _pill(state, "ok")
    if s in ("STOPPED", "EXPIRED", "ISOLATED"):
        return _pill(state, "warn")
    return _pill(state or "-", "info")


# ───────────────────────── 智能洞察(规则引擎) ─────────────────────────

def build_insights(data, lang):
    res = data.get("resources", {})
    cvm = res.get("cvm", {})
    tke = res.get("tke", {})
    eip = res.get("eip", {})
    cos = data.get("cos", {})
    regions = data.get("scanned_regions", [])

    insights = []  # (kind, icon, title, badge, metrics[(label,value,cls)], desc, action)

    # 1. 规模总览(info)
    insights.append((
        "info", "📊",
        L(lang, "ins_scale_title"),
        L(lang, "ins_scale_badge", n=_count_resource_types(res, cos)),
        [(L(lang, "ins_scale_m_cvm"), _num(cvm.get("total")), ""),
         (L(lang, "ins_scale_m_tke"), _num(tke.get("node_total")), ""),
         (L(lang, "ins_scale_m_cos"), len(cos.get("buckets", [])), "")],
        L(lang, "ins_scale_desc", cvm=_num(cvm.get("total")), tke=_num(tke.get("node_total")),
          cos=len(cos.get("buckets", [])), regions=len(regions)),
        L(lang, "ins_scale_action"),
    ))

    # 2. GPU 算力(warn if >0 else good)
    gpu_n = _num(cvm.get("gpu_total"))
    gpu_running = sum(1 for g in cvm.get("gpu_instances", []) if g.get("state") == "RUNNING")
    if gpu_n > 0:
        insights.append((
            "warn", "🎮",
            L(lang, "ins_gpu_title"), L(lang, "ins_gpu_badge", n=gpu_n),
            [(L(lang, "ins_gpu_m_total"), gpu_n, "up"),
             (L(lang, "ins_gpu_m_running"), gpu_running, "")],
            L(lang, "ins_gpu_desc", n=gpu_n), L(lang, "ins_gpu_action"),
        ))
    else:
        insights.append((
            "good", "🎮",
            L(lang, "ins_gpu_title"), L(lang, "ins_gpu_badge", n=0),
            [(L(lang, "ins_gpu_m_total"), 0, "down")],
            L(lang, "ins_gpu_desc_none"), L(lang, "ins_gpu_action_none"),
        ))

    # 3. 闲置 EIP(bad if unbound>0)
    eip_idle = _num(eip.get("unbound"))
    if eip_idle > 0:
        insights.append((
            "bad", "🌐",
            L(lang, "ins_eip_title"), L(lang, "ins_eip_badge", n=eip_idle),
            [(L(lang, "ins_eip_m_total"), _num(eip.get("total")), ""),
             (L(lang, "ins_eip_m_idle"), eip_idle, "up")],
            L(lang, "ins_eip_desc", n=eip_idle), L(lang, "ins_eip_action"),
        ))

    # 4. 地域分散(info)
    by_region = cvm.get("by_region", {})
    if by_region:
        top_region = max(by_region, key=by_region.get)
        top_cnt = by_region[top_region]
        total_cnt = sum(by_region.values()) or 1
        pct = round(top_cnt / total_cnt * 100)
        insights.append((
            "info", "🗺️",
            L(lang, "ins_region_title"), L(lang, "ins_region_badge", n=len(by_region)),
            [(L(lang, "ins_region_m_regions"), len(by_region), ""),
             (L(lang, "ins_region_m_top"), region_name(top_region, lang), "")],
            L(lang, "ins_region_desc", n=len(by_region), top=region_name(top_region, lang), pct=pct),
            L(lang, "ins_region_action"),
        ))

    # 5. 降级提示(warn)
    # 注：SSL 到期预警分级（bad 级洞察）已移除——风险分级与续签处置归云巡检，
    # 云管家只做纯清点陈列，不在看板里对证书到期下风险判定。
    degraded = collect_degraded(data)
    if degraded:
        insights.append((
            "warn", "⚠️",
            L(lang, "ins_degraded_title"), L(lang, "ins_degraded_badge", n=len(degraded)),
            [(L(lang, "ins_degraded_m_count"), len(degraded), "up")],
            L(lang, "ins_degraded_desc", n=len(degraded)), L(lang, "ins_degraded_action"),
        ))

    return insights


def _count_resource_types(res, cos):
    n = 0
    for k in ("cvm", "tke", "cbs", "clb", "vpc", "eip", "sg", "cdb", "redis", "lighthouse"):
        v = res.get(k, {})
        if _num(v.get("total")) or _num(v.get("cluster_total")) or _num(v.get("node_total")):
            n += 1
    if cos.get("buckets"):
        n += 1
    return n


def collect_degraded(data):
    """汇总所有采集失败项: (service, region, error)。"""
    out = []
    res = data.get("resources", {})
    for key, blk in res.items():
        for e in blk.get("errors", []) or []:
            if e.get("error"):
                out.append((key.upper(), e.get("region", "?"), e.get("error")))
    sub = data.get("subaccounts", {})
    if sub.get("status") == "error" and sub.get("error"):
        out.append(("CAM", "global", sub["error"]))
    cos = data.get("cos", {})
    if cos.get("status") == "error" and cos.get("error"):
        out.append(("COS", "global", cos["error"]))
    ssl = data.get("ssl_certs", {})
    if ssl.get("status") == "error" and ssl.get("error"):
        out.append(("SSL", "global", ssl["error"]))
    return out


def render_insights_html(insights, lang):
    if not insights:
        return ""
    # 汇总条
    counter = {"bad": 0, "warn": 0, "info": 0, "good": 0}
    for ins in insights:
        counter[ins[0]] = counter.get(ins[0], 0) + 1
    sum_labels = [("bad", L(lang, "ins_sum_bad")), ("warn", L(lang, "ins_sum_warn")),
                  ("info", L(lang, "ins_sum_info")), ("good", L(lang, "ins_sum_good"))]
    stats = "".join(
        f'<div class="stat"><span class="dot {k}"></span>{lbl} <b>{counter[k]}</b></div>'
        for k, lbl in sum_labels if counter[k]
    )
    cards = []
    for kind, icon, title, badge, metrics, desc, action in insights:
        m_html = "".join(
            f'<div class="m"><span class="m-label">{esc(lbl)}</span>'
            f'<span class="m-value {cls}">{esc(val)}</span></div>'
            for lbl, val, cls in metrics
        )
        cards.append(
            f'<div class="ins-card {kind}">'
            f'<div class="ins-head"><div class="ins-icon">{icon}</div>'
            f'<div class="ins-title">{esc(title)}</div>'
            f'<div class="ins-badge">{esc(badge)}</div></div>'
            f'<div class="ins-metric">{m_html}</div>'
            f'<div class="ins-desc">{esc(desc)}</div>'
            f'<div class="ins-action">{esc(action)}</div></div>'
        )
    return (f'<div class="ins-summary">{stats}</div>'
            f'<div class="insights-wrap">{"".join(cards)}</div>')


# ───────────────────────── 各章节表格 ─────────────────────────

def render_subaccounts(data, lang):
    sub = data.get("subaccounts", {})
    users = sub.get("sub_users", [])
    rows = []
    for u in users:
        rows.append([
            esc(u.get("uin")), esc(u.get("name")),
            _pill(L(lang, "yes"), "ok") if str(u.get("console_login")) in ("1", "True", "true")
            else _pill(L(lang, "no"), "info"),
            esc(u.get("last_login") or "-"), esc(u.get("remark") or "-"),
        ])
    headers = [L(lang, "th_uin"), L(lang, "th_login_user"), L(lang, "th_console"),
               L(lang, "th_last_login"), L(lang, "th_remark")]
    return _table(headers, rows, empty_msg=L(lang, "empty_sub"))


def render_cvm(data, lang, gpu_only=False):
    cvm = data.get("resources", {}).get("cvm", {})
    src = cvm.get("gpu_instances", []) if gpu_only else cvm.get("instances", [])
    rows = []
    for i in src:
        rows.append([
            f'<span class="code">{esc(i.get("instance_id"))}</span>',
            esc(i.get("name") or "-"),
            f'<span class="code">{esc(i.get("instance_type"))}</span>',
            f'{_num(i.get("cpu"))}C / {_num(i.get("memory"))}G',
            _state_pill(i.get("state")),
            esc(i.get("charge_type") or "-"),
            region_name(i.get("region", ""), lang),
            esc(i.get("private_ip") or "-"),
            esc(i.get("public_ip") or "-"),
        ])
    headers = [L(lang, "th_instance_id"), L(lang, "th_name"), L(lang, "th_type"),
               L(lang, "th_cpu_mem"), L(lang, "th_state"), L(lang, "th_charge"),
               L(lang, "th_region"), L(lang, "th_private_ip"), L(lang, "th_public_ip")]
    empty = L(lang, "empty_gpu") if gpu_only else L(lang, "empty_cvm")
    return _table(headers, rows, empty_msg=empty)


def render_tke(data, lang):
    tke = data.get("resources", {}).get("tke", {})
    rows = []
    for c in tke.get("clusters", []):
        specs = c.get("node_specs", {}) or {}
        spec_str = " · ".join(f"{esc(k)}×{v}" for k, v in sorted(specs.items(), key=lambda x: -x[1])) or "-"
        rows.append([
            f'<span class="code">{esc(c.get("cluster_id"))}</span>',
            esc(c.get("name") or "-"),
            esc(c.get("k8s_version") or "-"),
            region_name(c.get("region", ""), lang),
            _num(c.get("node_total")),
            spec_str,
        ])
    headers = [L(lang, "th_cluster_id"), L(lang, "th_cluster_name"), L(lang, "th_k8s"),
               L(lang, "th_region"), L(lang, "th_node_total"), L(lang, "th_node_specs")]
    return _table(headers, rows, empty_msg=L(lang, "empty_tke"), num_cols={4})


def render_cos(data, lang):
    buckets = data.get("cos", {}).get("buckets", [])
    rows = []
    for b in buckets:
        rows.append([
            f'<span class="code">{esc(b.get("name"))}</span>',
            region_name(b.get("region", ""), lang) if b.get("region") else "-",
            esc(b.get("create_date") or "-"),
        ])
    headers = [L(lang, "th_bucket"), L(lang, "th_region"), L(lang, "th_create_date")]
    return _table(headers, rows, empty_msg=L(lang, "empty_cos"))


def render_network(data, lang):
    res = data.get("resources", {})
    vpc = res.get("vpc", {})
    eip = res.get("eip", {})
    rows = [
        [L(lang, "res_vpc"), _num(vpc.get("total")), ""],
        [L(lang, "res_subnet"), _num(vpc.get("subnet_total")), ""],
        [L(lang, "res_clb"), _num(res.get("clb", {}).get("total")), ""],
        [L(lang, "res_eip"), _num(eip.get("total")),
         L(lang, "unbound_eip", n=_num(eip.get("unbound"))) if _num(eip.get("unbound")) else ""],
        [L(lang, "res_sg"), _num(res.get("sg", {}).get("total")), ""],
    ]
    headers = [L(lang, "th_resource"), L(lang, "th_total"), L(lang, "th_detail")]
    body = [[esc(r[0]), r[1], esc(r[2])] for r in rows]
    return _table(headers, body, num_cols={1})


def render_db_storage(data, lang):
    """云数据库全栈：MySQL/Redis/Mongo/PG/TDSQL-C/Kafka/ES + 块存储/轻量。"""
    res = data.get("resources", {})
    cbs = res.get("cbs", {})
    rows = [
        [L(lang, "res_cdb"), _num(res.get("cdb", {}).get("total")), ""],
        [L(lang, "res_redis"), _num(res.get("redis", {}).get("total")), ""],
        [L(lang, "res_mongodb"), _num(res.get("mongodb", {}).get("total")), ""],
        [L(lang, "res_postgres"), _num(res.get("postgres", {}).get("total")), ""],
        [L(lang, "res_cynosdb"), _num(res.get("cynosdb", {}).get("total")), ""],
        [L(lang, "res_ckafka"), _num(res.get("ckafka", {}).get("total")), ""],
        [L(lang, "res_es"), _num(res.get("es", {}).get("total")), ""],
        [L(lang, "res_cbs"), _num(cbs.get("total")),
         L(lang, "total_gb", gb=_num(cbs.get("total_gb")))],
        [L(lang, "res_cfs"), _num(res.get("cfs", {}).get("total")), ""],
        [L(lang, "res_lighthouse"), _num(res.get("lighthouse", {}).get("total")), ""],
    ]
    headers = [L(lang, "th_resource"), L(lang, "th_total"), L(lang, "th_detail")]
    body = [[esc(r[0]), r[1], esc(r[2])] for r in rows]
    return _table(headers, body, num_cols={1})


def render_serverless(data, lang):
    """Serverless 与容器服务：SCF / TCR / TKE 节点。"""
    res = data.get("resources", {})
    tke = res.get("tke", {})
    rows = [
        [L(lang, "res_scf"), _num(res.get("scf", {}).get("total")), ""],
        [L(lang, "res_tcr"), _num(res.get("tcr", {}).get("total")), ""],
        [L(lang, "res_tke_cluster"), _num(tke.get("cluster_total")), ""],
        [L(lang, "res_tke_node"), _num(tke.get("node_total")), ""],
    ]
    headers = [L(lang, "th_resource"), L(lang, "th_total"), L(lang, "th_detail")]
    body = [[esc(r[0]), r[1], esc(r[2])] for r in rows]
    return _table(headers, body, num_cols={1})


def render_extnet(data, lang):
    """扩展网络与中间件：NAT / VPN / API 网关 / CFS。"""
    res = data.get("resources", {})
    rows = [
        [L(lang, "res_nat"), _num(res.get("nat", {}).get("total")), ""],
        [L(lang, "res_vpn"), _num(res.get("vpn", {}).get("total")), ""],
        [L(lang, "res_apigw"), _num(res.get("apigw", {}).get("total")), ""],
        [L(lang, "res_cfs"), _num(res.get("cfs", {}).get("total")), ""],
    ]
    headers = [L(lang, "th_resource"), L(lang, "th_total"), L(lang, "th_detail")]
    body = [[esc(r[0]), r[1], esc(r[2])] for r in rows]
    return _table(headers, body, num_cols={1})


def render_ssl(data, lang):
    """SSL 证书清单（纯清点视角）。

    只陈列证书域名/别名/状态/到期日/剩余天数——剩余天数是中性事实数字，
    **不做 🔴🟠 风险分级染色**（分级与续签处置归云巡检 §12 SSL）。
    """
    ssl = data.get("ssl_certs", {})
    rows = []
    for c in ssl.get("certs", []):
        dl = c.get("days_left")
        days_cell = L(lang, "days_unit", n=dl) if isinstance(dl, int) else "-"
        rows.append([
            f'<span class="code">{esc(c.get("domain") or "-")}</span>',
            esc(c.get("alias") or "-"),
            esc(c.get("status") or "-"),
            esc(c.get("end_time") or "-"),
            days_cell,
        ])
    headers = [L(lang, "th_domain"), L(lang, "th_alias"), L(lang, "th_cert_status"),
               L(lang, "th_expire"), L(lang, "th_days_left")]
    return _table(headers, rows, empty_msg=L(lang, "empty_ssl"))


def render_cvm_state(data, lang):
    """CVM 状态分布表。"""
    cvm = data.get("resources", {}).get("cvm", {})
    dist = cvm.get("state_dist", {}) or {}
    rows = [[_state_pill(st), n] for st, n in sorted(dist.items(), key=lambda x: -x[1])]
    headers = [L(lang, "th_state"), L(lang, "th_count")]
    return _table(headers, rows, empty_msg=L(lang, "empty_cvm"), num_cols={1})


def render_expiry_inventory(data, lang):
    """跨资源包年包月到期清单（纯事实陈列，按到期日升序）。

    汇总 CVM/CDB/Redis/CBS/Lighthouse/MongoDB/PostgreSQL/TDSQL-C 等所有包年包月资源的到期项，
    每行带「资源类型」列。到期日、剩余天数、续费方式都是中性事实——不做风险染色、不催续费。
    续费风险判定归云巡检与运维流程。
    """
    inv = data.get("resources", {}).get("expiry_inventory", {}) or {}
    items = inv.get("items", []) or []
    rows = []
    for c in items:
        dl = c.get("days_left")
        days_cell = L(lang, "days_unit", n=dl) if isinstance(dl, int) else "-"
        # 续费方式：auto/manual/off，中性标注，不打风险色。
        state = c.get("renew_state", "") or ("auto" if c.get("auto_renew") else "manual")
        if state == "auto":
            renew_cell = _pill(L(lang, "renew_auto"), "ok")
        elif state == "off":
            renew_cell = _pill(L(lang, "renew_off"), "info")
        elif state == "manual":
            renew_cell = _pill(L(lang, "renew_manual"), "info")
        else:
            renew_cell = _pill(L(lang, "renew_unknown"), "info")
        # 资源类型显示名（res_type_cvm / res_type_cdb ...）
        rtype = c.get("resource_type", "other")
        rtype_label = L(lang, f"res_type_{rtype}") if L(lang, f"res_type_{rtype}") != f"res_type_{rtype}" else rtype.upper()
        rows.append([
            f'<span class="pill pill-info">{esc(rtype_label)}</span>',
            f'<span class="code">{esc(c.get("instance_id") or "-")}</span>',
            esc(c.get("name") or "-"),
            region_name(c.get("region", ""), lang),
            esc(c.get("deadline") or "-"),
            days_cell,
            renew_cell,
        ])
    headers = [L(lang, "th_restype"), L(lang, "th_instance"), L(lang, "th_name"),
               L(lang, "th_region"), L(lang, "th_deadline"), L(lang, "th_days_left"),
               L(lang, "th_renew")]
    return _table(headers, rows, empty_msg=L(lang, "empty_prepaid"))


def render_tag_coverage(data, lang):
    """CVM 标签覆盖率（纯统计：扫描数 / 已打标 / 未打标 / 覆盖率）。

    不判 head/business 必填、不给风险——标签合规判定归云巡检。
    覆盖全量资源（含零费用），补云巡检 billing 维度"仅上月有消费资源"的盲区。
    """
    cvm = data.get("resources", {}).get("cvm", {})
    tc = cvm.get("tag_coverage", {}) or {}
    rate = tc.get("rate")
    rate_cell = f'{rate}%' if rate is not None else "-"
    rows = [
        [L(lang, "tag_scanned"), _num(tc.get("scanned"))],
        [L(lang, "tag_tagged"), _num(tc.get("tagged"))],
        [L(lang, "tag_untagged"), _num(tc.get("untagged"))],
        [L(lang, "tag_rate"), rate_cell],
    ]
    headers = [L(lang, "th_metric"), L(lang, "th_value")]
    return _table(headers, rows, num_cols={1})


def render_degraded(data, lang):
    degraded = collect_degraded(data)
    rows = [[esc(s), region_name(r, lang) if r not in ("global",) else r, f'<span class="code">{esc(e)}</span>']
            for s, r, e in degraded]
    headers = [L(lang, "th_service"), L(lang, "th_region"), L(lang, "th_error")]
    return _table(headers, rows, empty_msg=L(lang, "empty_degraded"))


# ───────────────────────── 主渲染 ─────────────────────────

def render_html(data, *, account_name="", lang="zh", offline_chartjs_path=None):
    acct = data.get("account", {})
    res = data.get("resources", {})
    cvm = res.get("cvm", {})
    tke = res.get("tke", {})
    cos = data.get("cos", {})
    sub = data.get("subaccounts", {})
    regions = data.get("scanned_regions", [])

    name = account_name or acct.get("project") or acct.get("uin") or L(lang, "default_account")
    uin = acct.get("uin", "")
    app_id = acct.get("app_id", "")
    elapsed = data.get("elapsed_s", 0)

    degraded = collect_degraded(data)
    status_tag = (f'<span class="tag warn">{L(lang, "status_partial")}</span>' if degraded
                  else f'<span class="tag ok">{L(lang, "status_ok")}</span>')

    insights = build_insights(data, lang)
    insights_html = render_insights_html(insights, lang)

    # 地域分布数据(CVM by_region) → Chart.js
    by_region = cvm.get("by_region", {})
    region_labels = [region_name(r, lang) for r in by_region.keys()]
    region_values = list(by_region.values())

    chartjs_tag = ""
    if offline_chartjs_path and os.path.exists(offline_chartjs_path):
        with open(offline_chartjs_path, encoding="utf-8") as f:
            chartjs_tag = f"<script>{f.read()}</script>"
    else:
        chartjs_tag = f'<script src="{CHART_JS_CDN}"></script>'

    # KPI 取值
    cvm_total = _num(cvm.get("total"))
    cvm_running = _num(cvm.get("running"))
    gpu_total = _num(cvm.get("gpu_total"))
    tke_clusters = _num(tke.get("cluster_total"))
    tke_nodes = _num(tke.get("node_total"))
    cos_total = len(cos.get("buckets", []))
    sub_total = len(sub.get("sub_users", []))
    collab_total = len(sub.get("collaborators", []))
    cdb_total = _num(res.get("cdb", {}).get("total"))
    redis_total = _num(res.get("redis", {}).get("total"))
    ssl_total = _num(data.get("ssl_certs", {}).get("total"))
    # 跨资源到期清单（CVM/CDB/Redis/CBS/Lighthouse/MongoDB/PG/CynosDB）
    _inv = res.get("expiry_inventory", {}) or {}
    prepaid_total = _num(_inv.get("total"))
    # 未开自动续费数：纯计数，meta 中性陈列（不打风险色）。0 则不显示。
    _prepaid_norenew = _num(_inv.get("no_autorenew"))
    prepaid_norenew_meta = (
        f" · {L(lang, 'meta_no_autorenew', n=_prepaid_norenew)}" if _prepaid_norenew > 0 else ""
    )
    _tag_rate = cvm.get("tag_coverage", {}).get("rate")
    tag_rate_label = f"{_tag_rate}%" if _tag_rate is not None else "-"

    title = f"{name} · {L(lang, 'title_suffix')}"

    return f"""<!DOCTYPE html>
<html lang="{L(lang, 'html_lang')}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{esc(title)}</title>
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
.hero .sub b {{ color: var(--text-0); }}
.tag {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
  background: rgba(91,140,255,0.18); color: #b8caff; font-size: 12px;
  border: 1px solid rgba(91,140,255,0.32); }}
.tag.warn {{ background: rgba(245,180,85,0.16); color: #ffd49a; border-color: rgba(245,180,85,0.32); }}
.tag.ok {{ background: rgba(54,211,153,0.16); color: #9ee9c9; border-color: rgba(54,211,153,0.32); }}
.kpis {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-top: 20px; }}
.kpi {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 12px; padding: 14px 16px; position: relative; overflow: hidden; }}
.kpi::after {{ content: ""; position: absolute; right: -30px; top: -30px;
  width: 100px; height: 100px; border-radius: 50%;
  background: radial-gradient(circle, rgba(91,140,255,0.20), transparent 70%); }}
.kpi .label {{ color: var(--text-2); font-size: 12px; }}
.kpi .value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
.kpi .value.bad {{ color: var(--bad); }}
.kpi .value.good {{ color: var(--good); }}
.kpi .value.accent {{ color: var(--accent); }}
.kpi .delta {{ font-size: 11.5px; margin-top: 4px; color: var(--text-1); }}
.card {{ background: rgba(255,255,255,0.04); border: 1px solid var(--line);
  border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }}
.card h2 {{ font-size: 15px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }}
.card h2::before {{ content: ""; width: 3px; height: 14px; background: var(--accent); border-radius: 2px; }}
.card .meta {{ color: var(--text-2); font-size: 12px; font-weight: 400; margin-left: auto; }}
.row-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.chart-box {{ position: relative; height: 300px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }}
th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }}
th {{ color: var(--text-2); font-weight: 500; font-size: 12px; letter-spacing: 0.6px;
  background: rgba(255,255,255,0.02); position: sticky; top: 0; z-index: 1; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tbody tr:hover {{ background: rgba(91,140,255,0.06); }}
.code {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; color: var(--text-1); }}
.pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; vertical-align: middle; }}
.pill.bad {{ background: rgba(239,79,111,0.16); color: var(--bad); border: 1px solid rgba(239,79,111,0.32); }}
.pill.warn {{ background: rgba(245,180,85,0.16); color: var(--warn); border: 1px solid rgba(245,180,85,0.32); }}
.pill.info {{ background: rgba(91,140,255,0.16); color: #b8caff; border: 1px solid rgba(91,140,255,0.32); }}
.pill.ok {{ background: rgba(54,211,153,0.12); color: #9ee9c9; border: 1px solid rgba(54,211,153,0.28); }}
.scroll-y {{ max-height: 520px; overflow: auto; border-radius: 8px; border: 1px solid var(--line); }}
.scroll-y::-webkit-scrollbar {{ width: 8px; height: 8px; }}
.scroll-y::-webkit-scrollbar-thumb {{ background: rgba(120,144,200,0.28); border-radius: 4px; }}
.scroll-y::-webkit-scrollbar-track {{ background: transparent; }}
.empty {{ padding: 40px 20px; text-align: center; color: var(--text-2); font-size: 13px; }}
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
.ins-action::before {{ content: "💡"; font-size: 12px; }}
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
  .row-2 {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <div class="hero-top">
      <div>
        <h1>☁️ {esc(name)} · {L(lang, 'title_suffix')}</h1>
        <div class="sub">
          {L(lang, 'uin_label')} <b>{esc(uin)}</b> · {L(lang, 'appid_label')} <b>{esc(app_id)}</b><br/>
          {L(lang, 'scan_scope')} <b>{len(regions)}</b> {L(lang, 'regions_unit')} ·
          {L(lang, 'elapsed')} <b>{esc(elapsed)}</b> {L(lang, 'sec_unit')} · {L(lang, 'datasource')}
        </div>
      </div>
      <div>{status_tag}</div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="label">{L(lang, 'kpi_cvm')}</div>
        <div class="value accent">{cvm_total}</div>
        <div class="delta">{L(lang, 'kpi_cvm_delta', running=cvm_running, gpu=gpu_total)}</div></div>
      <div class="kpi"><div class="label">{L(lang, 'kpi_gpu')}</div>
        <div class="value {'warn' if gpu_total else 'good'}">{gpu_total}</div>
        <div class="delta">{L(lang, 'kpi_gpu_delta')}</div></div>
      <div class="kpi"><div class="label">{L(lang, 'kpi_tke')}</div>
        <div class="value accent">{tke_clusters}</div>
        <div class="delta">{L(lang, 'kpi_tke_delta', nodes=tke_nodes)}</div></div>
      <div class="kpi"><div class="label">{L(lang, 'kpi_cos')}</div>
        <div class="value accent">{cos_total}</div>
        <div class="delta">{L(lang, 'kpi_cos_delta')}</div></div>
      <div class="kpi"><div class="label">{L(lang, 'kpi_subuser')}</div>
        <div class="value">{sub_total}</div>
        <div class="delta">{L(lang, 'kpi_subuser_delta', collab=collab_total)}</div></div>
      <div class="kpi"><div class="label">{L(lang, 'kpi_db')}</div>
        <div class="value">{cdb_total + redis_total}</div>
        <div class="delta">{L(lang, 'kpi_db_delta', cdb=cdb_total, redis=redis_total)}</div></div>
    </div>
  </div>

  <div class="card">
    <h2>🤖 {L(lang, 'card_insights')} <span class="meta">{L(lang, 'card_insights_meta', n=len(insights))}</span></h2>
    {insights_html}
  </div>

  <div class="row-2">
    <div class="card">
      <h2>🗺️ {L(lang, 'card_region_dist')} <span class="meta">{L(lang, 'card_region_dist_meta')}</span></h2>
      <div class="chart-box"><canvas id="regionChart"></canvas></div>
    </div>
    <div class="card">
      <h2>🌐 {L(lang, 'card_network')} <span class="meta">{L(lang, 'card_network_meta')}</span></h2>
      {render_network(data, lang)}
    </div>
  </div>

  <div class="card">
    <h2>🖥️ {L(lang, 'card_cvm')} <span class="meta">{L(lang, 'card_cvm_meta')} · {cvm_total}</span></h2>
    <div class="scroll-y">{render_cvm(data, lang, gpu_only=False)}</div>
  </div>

  <div class="card">
    <h2>🎮 {L(lang, 'card_gpu')} <span class="meta">{L(lang, 'card_gpu_meta')} · {gpu_total}</span></h2>
    <div class="scroll-y">{render_cvm(data, lang, gpu_only=True)}</div>
  </div>

  <div class="card">
    <h2>⎈ {L(lang, 'card_tke')} <span class="meta">{L(lang, 'card_tke_meta')} · {tke_clusters} / {tke_nodes} {L(lang, 'nodes_unit')}</span></h2>
    <div class="scroll-y">{render_tke(data, lang)}</div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>🗄️ {L(lang, 'card_cos')} <span class="meta">{L(lang, 'card_cos_meta')} · {cos_total}</span></h2>
      <div class="scroll-y">{render_cos(data, lang)}</div>
    </div>
    <div class="card">
      <h2>📊 {L(lang, 'card_cvm_state')} <span class="meta">{L(lang, 'card_cvm_state_meta')}</span></h2>
      {render_cvm_state(data, lang)}
    </div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>🛢️ {L(lang, 'card_dbstack')} <span class="meta">{L(lang, 'card_dbstack_meta')}</span></h2>
      {render_db_storage(data, lang)}
    </div>
    <div class="card">
      <h2>⚡ {L(lang, 'card_serverless')} <span class="meta">{L(lang, 'card_serverless_meta')}</span></h2>
      {render_serverless(data, lang)}
    </div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>🔀 {L(lang, 'card_extnet')} <span class="meta">{L(lang, 'card_extnet_meta')}</span></h2>
      {render_extnet(data, lang)}
    </div>
    <div class="card">
      <h2>🔐 {L(lang, 'card_ssl')} <span class="meta">{L(lang, 'card_ssl_meta')} · {ssl_total}</span></h2>
      <div class="scroll-y">{render_ssl(data, lang)}</div>
    </div>
  </div>

  <div class="row-2">
    <div class="card">
      <h2>📅 {L(lang, 'card_deadline')} <span class="meta">{L(lang, 'card_deadline_meta')} · {prepaid_total}{prepaid_norenew_meta}</span></h2>
      <div class="scroll-y">{render_expiry_inventory(data, lang)}</div>
    </div>
    <div class="card">
      <h2>🏷️ {L(lang, 'card_tagcov')} <span class="meta">{L(lang, 'card_tagcov_meta')} · {tag_rate_label}</span></h2>
      {render_tag_coverage(data, lang)}
    </div>
  </div>

  <div class="card">
    <h2>👥 {L(lang, 'card_subaccounts')} <span class="meta">{L(lang, 'card_subaccounts_meta')} · {sub_total}</span></h2>
    <div class="scroll-y">{render_subaccounts(data, lang)}</div>
  </div>

  <div class="card">
    <h2>🩺 {L(lang, 'card_degraded')} <span class="meta">{L(lang, 'card_degraded_meta')} · {len(degraded)}</span></h2>
    <div class="scroll-y">{render_degraded(data, lang)}</div>
  </div>

  <footer>{L(lang, 'footer')}</footer>
</div>

<script>
(function() {{
  var labels = {json.dumps(region_labels, ensure_ascii=False)};
  var values = {json.dumps(region_values)};
  if (!labels.length || typeof Chart === 'undefined') return;
  var ctx = document.getElementById('regionChart');
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{ labels: labels, datasets: [{{
      label: 'CVM', data: values,
      backgroundColor: 'rgba(91,140,255,0.55)',
      borderColor: 'rgba(91,140,255,1)', borderWidth: 1, borderRadius: 5
    }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ color: '#aab4dc', font: {{ size: 11 }} }}, grid: {{ display: false }} }},
        y: {{ ticks: {{ color: '#6f7aa8' }}, grid: {{ color: 'rgba(120,144,200,0.12)' }}, beginAtZero: true }}
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="渲染腾讯云资源清点 HTML 看板")
    ap.add_argument("--data", required=True, help="query_resources.py 输出的 resources.json")
    ap.add_argument("--output", default="", help="HTML 落盘路径(不指定则打印 stdout)")
    ap.add_argument("--account", default="", help="账户昵称(报告头展示)")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--offline", default="", help="内嵌 Chart.js 文件路径")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)

    status = data.get("status")
    if status != "ok":
        # need_credentials / auth_failed 由上层 SKILL 工作流引导, 这里仅提示
        sys.stderr.write(f"[render] data status={status}, 无法渲染完整看板。\n")
        # 仍渲染一个最小占位(避免空文件)
        data.setdefault("account", {})
        data.setdefault("resources", {})
        data.setdefault("subaccounts", {})
        data.setdefault("cos", {})
        data.setdefault("ssl_certs", {})
        data.setdefault("scanned_regions", [])

    html = render_html(data, account_name=args.account, lang=args.lang,
                       offline_chartjs_path=args.offline or None)

    if args.output:
        # 落盘命名对齐账单看板规范: reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/resource-{cloud}-{project}-{uin}.html
        # 相对路径时统一加 WORKBUDDY_WORKSPACE 前缀, 确保 artifacts 面板可见性(与 render_billing_html.py 一致)
        out_path = args.output
        if not os.path.isabs(out_path):
            ws = os.environ.get("WORKBUDDY_WORKSPACE")
            if ws:
                out_path = os.path.join(ws, out_path)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 已生成 HTML 看板: {out_path}", file=sys.stderr)
        print(json.dumps({"status": "ok", "output": out_path, "bytes": len(html)}, ensure_ascii=False))
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
