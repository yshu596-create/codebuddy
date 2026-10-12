#!/usr/bin/env python3
"""
从 prepare_billing_data.py 输出的 prep_output.json 提取看板渲染所需的扁平 JSON。

用法：
    python3 extract_step3_data.py <workspace_root>
    （读取 cache/billing-huawei/{domain_id}/{YYYYMM}/prep_output.json，stdout 输出 step3_data.json）

或直接：
    python3 extract_step3_data.py --input /path/to/prep_output.json
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _fmt_ym(yyyymm: str) -> str:
    """202605 → 2026-05（已带连字符则原样返回）。"""
    if not yyyymm:
        return ""
    if "-" in yyyymm:
        return yyyymm
    return "%s-%s" % (yyyymm[:4], yyyymm[4:6]) if len(yyyymm) == 6 else yyyymm


def _next_ym(yyyymm: str) -> str:
    """下一个月（YYYYMM）。"""
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    if m == 12:
        return "%d01" % (y + 1)
    return "%d%02d" % (y, m + 1)


def build_trend(prep: Dict[str, Any]) -> Dict[str, Any]:
    """读 prep.trend_months → {"months":[YYYY-MM...], "totals":[...], "raw_months":[YYYYMM...]}。

    仅保留 available=True 的月份（无数据月不画点，避免趋势断崖误导）。
    """
    tm = prep.get("trend_months") or []
    months, totals, raw = [], [], []
    for it in tm:
        if not it.get("available"):
            continue
        ym = it.get("month") or ""
        months.append(_fmt_ym(ym))
        totals.append(round(float(it.get("total") or 0.0), 4))
        raw.append(ym)
    return {"months": months, "totals": totals, "raw_months": raw}


def _ols_slope(xs: List[float], ys: List[float]) -> float:
    """最小二乘斜率：Σ(x-x̄)(y-ȳ)/Σ(x-x̄)²（手写，不依赖 numpy）。"""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return (num / den) if den else 0.0


def _wma3(seq: List[float]) -> float:
    """近3月加权移动平均，权重 0.2/0.3/0.5（最近月权重最高）。"""
    last3 = seq[-3:]
    if len(last3) < 3:
        return sum(last3) / len(last3) if last3 else 0.0
    return last3[0] * 0.2 + last3[1] * 0.3 + last3[2] * 0.5


def build_forecast(trend: Dict[str, Any], horizon: int = 3) -> Optional[Dict[str, Any]]:
    """基于 trend.totals 做可解释的【多步】区间预测（默认未来 3 个月）。

    point：滚动递归外推（先预测 M+1，把 point 当已知值再推 M+2/M+3）。
      历史≥4月：point = wma3 + 0.5×OLS斜率（method=wma+ols）；3月：仅 wma3（method=wma）。
    区间（对齐腾讯云 billing-cost-forecast 的 MAPE 相对误差带，避免 σ×√step 喇叭口）：
      band_pct = mape_frac × z × sqrt(step)，z=1.0；并对单步带宽设硬上限 cap=25%，
      多步累计带宽再封顶 35%（防止逐月翻倍发散）。lower/upper = point × (1 ∓ band_pct)。
      mape 缺失（<4月无法回测）→ 用固定 ±10% 兜底。
    回测 MAPE：用 t 之前 wma3 预测 totals[t]，取 |pred-actual|/actual 均值（存为百分数，如 12.21）。
    """
    totals = trend.get("totals") or []
    raw_months = trend.get("raw_months") or []
    n = len(totals)
    if n < 3:
        return None

    method = "wma+ols" if n >= 4 else "wma"

    # 先算回测 MAPE（小数形式，如 0.1221），既用于区间也用于展示
    errs = []
    for t in range(3, n):
        pred = totals[t - 3] * 0.2 + totals[t - 2] * 0.3 + totals[t - 1] * 0.5
        actual = totals[t]
        if actual > 0:
            errs.append(abs(pred - actual) / actual)
    mape_frac = (sum(errs) / len(errs)) if errs else None     # 0~1 小数
    # 区间基准误差率：有回测用 MAPE，否则兜底 10%
    base_pct = mape_frac if mape_frac is not None else 0.10

    Z = 1.0
    STEP_CAP = 0.15       # 单步带宽硬上限 ±15%（第5轮收紧，避免视觉夸张）
    CUMUL_CAP = 0.20      # 多步累计带宽封顶 ±20%（第5轮收紧）

    months: List[str] = []
    points: List[float] = []
    lowers: List[float] = []
    uppers: List[float] = []

    seq = list(totals)
    last_ym = raw_months[-1] if raw_months else ""
    for step in range(1, horizon + 1):
        last_ym = _next_ym(last_ym) if last_ym else ""
        if method == "wma+ols":
            wma = _wma3(seq)
            xs = list(range(len(seq)))
            slope = _ols_slope([float(x) for x in xs], seq)
            point = wma + 0.5 * slope
            if point < 0:
                point = max(0.0, wma)
        else:
            point = _wma3(seq)
        # MAPE 相对带宽：随步长缓增（×sqrt(step)），但双重封顶
        band_pct = base_pct * Z * math.sqrt(step)
        band_pct = min(band_pct, STEP_CAP)          # 单步上限
        band_pct = min(band_pct, CUMUL_CAP)         # 累计上限
        lo = point * (1 - band_pct)
        up = point * (1 + band_pct)
        months.append(_fmt_ym(last_ym))
        points.append(round(point, 4))
        lowers.append(round(max(0.0, lo), 4))
        uppers.append(round(up, 4))
        seq.append(point)

    mape = round(mape_frac * 100, 2) if mape_frac is not None else None  # 展示用百分数

    # 历史月环比绝对均值（衡量账号波动性）+ OLS 斜率方向
    mom_abs = []
    for i in range(1, n):
        if totals[i - 1] > 0:
            mom_abs.append(abs(totals[i] - totals[i - 1]) / totals[i - 1])
    vol_pct = round(sum(mom_abs) / len(mom_abs) * 100, 1) if mom_abs else None
    full_slope = _ols_slope([float(x) for x in range(n)], totals)
    # 趋势方向：斜率相对均值水平的占比，<2% 视为"无明显方向"
    mean_level = sum(totals) / n if n else 0
    slope_ratio = (abs(full_slope) / mean_level) if mean_level else 0
    no_trend = slope_ratio < 0.02
    volatile = (vol_pct is not None and vol_pct >= 15)

    return {
        "months": months,
        "point": points,
        "lower": lowers,
        "upper": uppers,
        "method": method,
        "mape_backtest": mape,        # 百分数（如 12.21），渲染层直接当 "%" 展示
        "next_month": months[0] if months else "",
        "volatility_pct": vol_pct,    # 历史环比绝对均值（如 22.4）
        "volatile": volatile,         # 是否波动型账号（≥15%）
        "no_trend": no_trend,         # 近月是否无明显趋势方向
        "slope": round(full_slope, 2),
    }


def build_insights(rr: Dict[str, Any], trend: Dict[str, Any],
                   forecast: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """加密洞察规则到接近腾讯云密度（目标 6~8 条）。"""
    insights: List[Dict[str, str]] = []
    by_service = rr.get("by_service") or []
    by_ep = rr.get("by_enterprise_project") or []
    by_region = rr.get("by_region") or []
    diff = rr.get("resource_diff") or {}
    totals = rr.get("totals") or {}
    a_total = totals.get("analysis") or 0
    c_total = totals.get("comparison") or 0

    # 1. 环比总额变化
    if c_total > 0:
        ratio = (a_total - c_total) / c_total * 100
        if ratio > 10:
            insights.append({
                "level": "warn",
                "title": "环比增长超过 10%",
                "desc": "分析月费用 ¥%s，环比上月 +%.1f%%（+¥%s）" % (
                    int(round(a_total)), ratio, int(round(a_total - c_total))),
            })
        elif ratio < -10:
            insights.append({
                "level": "good",
                "title": "环比降低超过 10%",
                "desc": "分析月费用 ¥%s，环比上月 %.1f%%（¥%s）" % (
                    int(round(a_total)), ratio, int(round(a_total - c_total))),
            })

    # 2. Top3 产品集中度
    if by_service and a_total > 0:
        top3_amount = sum(s["amount"] for s in by_service[:3])
        top3_ratio = top3_amount / a_total * 100
        lvl = "warn" if top3_ratio >= 70 else "info"
        insights.append({
            "level": lvl,
            "title": "Top 3 云服务集中度 %.1f%%" % top3_ratio,
            "desc": "Top 3 云服务合计占当月费用 %.1f%%（%s）%s" % (
                top3_ratio,
                " / ".join(s["service_name"] for s in by_service[:3]),
                "，集中度偏高" if top3_ratio >= 70 else ""),
        })

    # 3. 单地域集中度
    if by_region and a_total > 0:
        top_region = by_region[0]
        rg_ratio = top_region["amount"] / a_total * 100
        if rg_ratio >= 60:
            insights.append({
                "level": "warn",
                "title": "单地域集中 %.1f%%" % rg_ratio,
                "desc": "地域「%s」占当月费用 %.1f%%，资源高度集中于单一地域" % (
                    top_region.get("region_code") or "-", rg_ratio),
            })
        else:
            insights.append({
                "level": "info",
                "title": "地域分布 %d 个" % len(by_region),
                "desc": "费用分布于 %d 个地域，主地域「%s」占 %.1f%%" % (
                    len(by_region), top_region.get("region_code") or "-", rg_ratio),
            })

    # 4. 企业项目未归集占比
    if by_ep and a_total > 0:
        uncollected = 0.0
        for ep in by_ep:
            epid = ep.get("enterprise_project_id") or ""
            if epid in ("未归集", "", "0", "default"):
                uncollected += ep["amount"]
        if uncollected > 0:
            uc_ratio = uncollected / a_total * 100
            lvl = "warn" if uc_ratio >= 50 else "info"
            insights.append({
                "level": lvl,
                "title": "未归集项目占比 %.1f%%" % uc_ratio,
                "desc": "有 ¥%s（%.1f%%）费用归属 default/未归集企业项目，分账治理存在缺口" % (
                    int(round(uncollected)), uc_ratio),
            })

    # 5. 资源潮汐（新增 vs 下线）
    new_n = len(diff.get("new") or [])
    off_n = len(diff.get("decommissioned") or [])
    if new_n or off_n:
        net = new_n - off_n
        lvl = "info"
        if net > 0:
            tide = "净新增 %d 个资源（新增 %d / 下线 %d），资源规模扩张" % (net, new_n, off_n)
            lvl = "warn" if net >= 50 else "info"
        elif net < 0:
            tide = "净下线 %d 个资源（新增 %d / 下线 %d），资源规模收缩" % (-net, new_n, off_n)
        else:
            tide = "新增与下线持平（各 %d / %d 个）" % (new_n, off_n)
        insights.append({
            "level": lvl,
            "title": "资源潮汐",
            "desc": tide,
        })

    # 6. 增长最猛资源
    growth_items = diff.get("growth") or []
    if growth_items:
        top = growth_items[0]
        insights.append({
            "level": "warn",
            "title": "增长最猛资源",
            "desc": "%s（%s）环比 +¥%s" % (
                top.get("resource_name") or top.get("resource_id"),
                top.get("service_name") or "-",
                int(round(top.get("diff") or 0))),
        })

    # 7. 趋势偏离（本月 vs 近6月均值）
    totals_seq = trend.get("totals") or []
    if len(totals_seq) >= 3:
        mean = sum(totals_seq) / len(totals_seq)
        cur = totals_seq[-1]
        if mean > 0:
            dev = (cur - mean) / mean * 100
            if abs(dev) >= 15:
                insights.append({
                    "level": "warn" if dev > 0 else "good",
                    "title": "趋势偏离均值 %+.1f%%" % dev,
                    "desc": "当月 ¥%s 较近 %d 月均值 ¥%s %s %.1f%%" % (
                        int(round(cur)), len(totals_seq), int(round(mean)),
                        "高出" if dev > 0 else "低于", abs(dev)),
                })

    # 8. 下月预测（forecast.point 现为序列，取首月）
    if forecast and forecast.get("point"):
        pts = forecast["point"]
        los = forecast.get("lower") or []
        ups = forecast.get("upper") or []
        mons = forecast.get("months") or []
        p0 = pts[0] if pts else 0
        l0 = los[0] if los else p0
        u0 = ups[0] if ups else p0
        m0 = mons[0] if mons else "-"
        mape_txt = ""
        if forecast.get("mape_backtest") is not None:
            mape_txt = "，回测 MAPE %.1f%%" % forecast["mape_backtest"]
        insights.append({
            "level": "info",
            "title": "下月费用预测 ¥%s" % int(round(p0)),
            "desc": "预计 %s 费用约 ¥%s，区间 ¥%s ~ ¥%s（%s）%s" % (
                m0, int(round(p0)), int(round(l0)), int(round(u0)),
                forecast.get("method") or "-", mape_txt),
        })

    return insights


def extract(prep: Dict[str, Any]) -> Dict[str, Any]:
    rr = prep.get("report_ready") or {}
    if not rr.get("available"):
        return {"available": False, "reason": "report_ready.available != True"}

    by_service = rr.get("by_service") or []
    by_ep = rr.get("by_enterprise_project") or []
    by_region = rr.get("by_region") or []
    top_resources = rr.get("top_resources") or []
    diff = rr.get("resource_diff") or {}
    totals = rr.get("totals") or {}

    trend = build_trend(prep)
    forecast = build_forecast(trend)
    insights = build_insights(rr, trend, forecast)

    return {
        "available": True,
        "domain_id": rr.get("domain_id"),
        "domain_name": rr.get("domain_name"),
        "active_cloud": rr.get("active_cloud"),
        "active_project": rr.get("active_project"),
        "currency": rr.get("currency", "CNY"),
        "budget": rr.get("budget"),
        "analysis_month": rr.get("analysis_month"),
        "comparison_month": rr.get("comparison_month"),
        "totals": totals,
        "by_service": by_service,
        "by_enterprise_project": by_ep,
        "ep_prev_estimated": bool(rr.get("ep_prev_estimated")),
        "by_region": by_region,
        "top_resources": top_resources,
        "resource_diff": diff,
        "trend": trend,
        "forecast": forecast,
        "insights": insights,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", nargs="?", help="WorkBuddy workspace root")
    ap.add_argument("--input", help="prep_output.json 绝对路径（优先）")
    ap.add_argument("--month", help="YYYYMM；workspace 模式下必填")
    ap.add_argument("--domain-id", help="domain_id；workspace 模式下必填")
    args = ap.parse_args()

    if args.input:
        prep = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        if not (args.workspace and args.month and args.domain_id):
            print(json.dumps({"error": "missing workspace/month/domain-id"}))
            return 1
        path = (
            Path(args.workspace)
            / "cache" / "billing-huawei" / args.domain_id / args.month / "prep_output.json"
        )
        if not path.exists():
            print(json.dumps({"error": "prep_output not found: %s" % path}))
            return 1
        prep = json.loads(path.read_text(encoding="utf-8"))

    out = extract(prep)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
