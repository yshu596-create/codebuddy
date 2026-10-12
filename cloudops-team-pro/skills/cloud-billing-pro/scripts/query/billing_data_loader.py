"""BillingDataLoader — 多月账单数据加载与对齐。

职责：
  1. 读取 cache_base/{YYYYMM}/{resource_summary,summary_product,summary_tag}.json
     —— 路径维持现有 cache/billing/{uin}/{YYYYMM}/ 约定（PRD §2.2 路径只作为参考，
     不切换文件位置；PRD 数据语义已在 summarize_bill.py 中实现）。
  2. 把多月数据对齐成统一的"资源视图"——即每个 ResourceId 在每月的 cost / tags。
  3. 返回的 ResourceView 列表是 router/handlers 所有查询逻辑的唯一数据源。

ResourceView dataclass：
  - resource_id, resource_name, product, region
  - tags: dict[tag_key, tag_value]    # head/business/customer 等
  - costs: dict[YYYYMM, float]        # 月度费用
  - statuses: dict[YYYYMM, str]       # 新增 / 增长 / 降低 / 持平 / 已下线

加载顺序（最近到最远）：[T-0, T-1, T-2, T-3, T-4, T-5]，T-0 为分析月。
缺失月份返回空 dict，不抛错；handlers 自行判断数据是否充足。
"""
from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass, field
from typing import Any


# 默认回看窗口：分析月在内共 6 个月（T-0 ~ T-5）
DEFAULT_LOOKBACK_MONTHS = 6


@dataclass
class ResourceView:
    """跨月份对齐的单资源视图。"""

    resource_id: str
    resource_name: str = ""
    product: str = ""
    region: str = ""
    pay_mode: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    costs: dict[str, float] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)

    # ── 便利属性 ──────────────────────────────────
    def cost(self, yyyymm: str) -> float:
        return float(self.costs.get(yyyymm, 0))

    def head(self) -> str:
        return self.tags.get("head", "")

    def business(self) -> str:
        return self.tags.get("business", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "product": self.product,
            "region": self.region,
            "pay_mode": self.pay_mode,
            "tags": dict(self.tags),
            "costs": dict(self.costs),
            "statuses": dict(self.statuses),
        }


# ── 月份工具 ──────────────────────────────────────────────────

def prev_month_str(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    if m == 1:
        return f"{y - 1:04d}12"
    return f"{y:04d}{m - 1:02d}"


def lookback_months(analysis_month: str, n: int = DEFAULT_LOOKBACK_MONTHS) -> list[str]:
    """返回最近 N 个月（含 analysis_month），按 [T-0, T-1, T-2, ...] 顺序。"""
    out = [analysis_month]
    cur = analysis_month
    for _ in range(n - 1):
        cur = prev_month_str(cur)
        out.append(cur)
    return out


# ── 原始 JSON 解析 ─────────────────────────────────────────────

def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _extract_tags(raw_tags: list[dict]) -> dict[str, str]:
    """与 summarize_bill.py 中 _extract_tags 对齐：剔除系统标签 + proj/test。"""
    sys_prefixes = ("tencentcloud:", "tke-", "qcloud:")
    out: dict[str, str] = {}
    for t in raw_tags or []:
        k = t.get("TagKey", "")
        v = t.get("TagValue", "")
        if not k:
            continue
        if any(k.startswith(p) for p in sys_prefixes):
            continue
        if k in ("test", "proj"):
            continue
        out[k] = v
    return out


def _fmt_amount(v: Any) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_resource_summary(data: Any) -> list[dict]:
    """把 resource_summary.json 解析成 dict 列表（每条含全部字段）。"""
    if data is None:
        return []
    if isinstance(data, dict):
        resp = data.get("Response", data)
        raw = resp.get("ResourceSummarySet", resp.get("ResourceSummaryList", []))
    else:
        raw = data
    out = []
    for r in raw or []:
        out.append({
            "ResourceId": r.get("ResourceId", ""),
            "ResourceName": r.get("ResourceName", ""),
            "ProductCode": r.get("ProductCode", ""),
            "ProductName": r.get("ProductName", ""),
            "RegionName": r.get("RegionName", ""),
            "PayModeName": r.get("PayModeName", ""),
            "RealTotalCost": _fmt_amount(r.get("RealTotalCost", 0)),
            "Tags": _extract_tags(r.get("Tags", [])),
        })
    return out


def _parse_summary_product(data: Any) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, dict):
        resp = data.get("Response", data)
        raw = resp.get("SummaryOverview", resp.get("SummaryTotal", []))
    else:
        raw = data
    out = []
    for r in raw or []:
        out.append({
            "ProductCode": r.get("ProductCode", r.get("BusinessCode", "")),
            "ProductName": r.get("ProductName", "") or r.get("BusinessCodeName", ""),
            "RealTotalCost": _fmt_amount(r.get("RealTotalCost", 0)),
            "TotalCost": _fmt_amount(r.get("TotalCost", 0)),
            "RealTotalCostRatio": r.get("RealTotalCostRatio", ""),
            "PayMode": r.get("PayMode", ""),
        })
    return out


# ── 主类 ──────────────────────────────────────────────────────


class BillingDataLoader:
    """统一加载多月账单数据。"""

    def __init__(self, cache_base: str, analysis_month: str,
                 lookback: int = DEFAULT_LOOKBACK_MONTHS):
        """
        Args:
          cache_base: 形如 <WORKSPACE>/cache/billing/{uin}
          analysis_month: 分析月，"YYYYMM"
          lookback: 回看月数（含分析月）
        """
        self.cache_base = cache_base
        self.analysis_month = analysis_month
        self.lookback = lookback
        self.months = lookback_months(analysis_month, lookback)

        # 解析后的原始数据缓存
        self._resources_per_month: dict[str, list[dict]] = {}
        self._products_per_month: dict[str, list[dict]] = {}

        # 对齐后的资源视图
        self._views: list[ResourceView] = []
        self._loaded = False

    # ── 公共 API ─────────────────────────────────────────────

    def months_available(self) -> list[str]:
        """返回缓存中**真实有数据**的月份列表（按时间倒序）。"""
        self._ensure_loaded()
        return [m for m in self.months if m in self._resources_per_month]

    def resources(self) -> list[ResourceView]:
        """对齐后的资源视图列表，含所有出现过的 ResourceId（任一月份有费用即收录）。"""
        self._ensure_loaded()
        return self._views

    def products(self, yyyymm: str) -> list[dict]:
        """某月 summary_product 数据（用于产品级查询）。"""
        self._ensure_loaded()
        return self._products_per_month.get(yyyymm, [])

    def comparison_month(self) -> str:
        return prev_month_str(self.analysis_month)

    # ── 内部 ─────────────────────────────────────────────────

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load_all()
        self._build_views()
        self._loaded = True

    def _load_all(self):
        for m in self.months:
            month_dir = os.path.join(self.cache_base, m)
            rs_path = os.path.join(month_dir, "resource_summary.json")
            sp_path = os.path.join(month_dir, "summary_product.json")
            res = _parse_resource_summary(_load_json(rs_path))
            if res:
                self._resources_per_month[m] = res
            prods = _parse_summary_product(_load_json(sp_path))
            if prods:
                self._products_per_month[m] = prods

    def _build_views(self):
        # 收集所有资源的元数据 + 每月费用
        meta: dict[str, dict] = {}
        for m, items in self._resources_per_month.items():
            for r in items:
                rid = r["ResourceId"]
                if not rid:
                    continue
                if rid not in meta:
                    meta[rid] = {
                        "resource_name": r.get("ResourceName", ""),
                        "product": r.get("ProductName", ""),
                        "region": r.get("RegionName", ""),
                        "pay_mode": r.get("PayModeName", ""),
                        "tags": dict(r.get("Tags", {})),
                        "costs": {},
                    }
                else:
                    # 合并补全字段：以最新月份的非空值为准
                    if r.get("ResourceName") and not meta[rid]["resource_name"]:
                        meta[rid]["resource_name"] = r.get("ResourceName")
                    new_tags = r.get("Tags") or {}
                    for k, v in new_tags.items():
                        meta[rid]["tags"].setdefault(k, v)
                meta[rid]["costs"][m] = r.get("RealTotalCost", 0)

        # 计算 status：相对于"上一相邻月份"
        analysis = self.analysis_month
        comp = prev_month_str(analysis)

        views: list[ResourceView] = []
        for rid, m in meta.items():
            v = ResourceView(
                resource_id=rid,
                resource_name=m["resource_name"],
                product=m["product"],
                region=m["region"],
                pay_mode=m["pay_mode"],
                tags=m["tags"],
                costs=m["costs"],
            )
            curr = v.cost(analysis)
            prev = v.cost(comp)
            if curr > 0 and prev == 0:
                v.statuses[analysis] = "新增"
            elif curr == 0 and prev > 0:
                v.statuses[analysis] = "已下线"
            elif curr > prev and (curr - prev) > 0.5:
                v.statuses[analysis] = "增长"
            elif curr < prev and (prev - curr) > 0.5:
                v.statuses[analysis] = "降低"
            else:
                v.statuses[analysis] = "持平"
            views.append(v)
        # 默认按当月费用降序
        views.sort(key=lambda x: x.cost(analysis), reverse=True)
        self._views = views


__all__ = [
    "BillingDataLoader",
    "ResourceView",
    "lookback_months",
    "prev_month_str",
    "DEFAULT_LOOKBACK_MONTHS",
]
