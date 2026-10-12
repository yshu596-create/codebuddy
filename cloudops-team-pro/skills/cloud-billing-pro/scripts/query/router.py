"""路由层 — 自然语言 → (intent_id, slots) 提取。

策略：
  1. **槽位优先**：先扫一遍 query，把 head / business / product / 数字阈值 全部抽出。
  2. **关键词命中**：每个 intent_id 维护一组关键词列表（必须命中 ANY），同时
     可以要求/禁止某些槽位存在。
  3. **优先级**：精确度高（关键词长 + 槽位多）的 intent 优先；同分按列表顺序。
  4. **未命中**：返回 None，由 LLM 兜底（暂用 fallback）。

只覆盖第一期 16 个 P0 ID：
  H-01/02/03/04/06/08/10、B-01/02/04、T-01/03、A-03/05、C-05、I-04
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .slot_dicts import SlotDicts


# ─────────────── 槽位提取 ───────────────


_NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*([%％]|\u5143|\u5757)?")
_TOP_N_PATTERN = re.compile(r"top\s*[\u7684]?\s*(\d+)|前\s*(\d+)|Top\s*(\d+)", re.I)
_THRESHOLD_AMOUNT = re.compile(r"超过\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(块|元)?")
_THRESHOLD_PCT = re.compile(r"超过\s*(\d+(?:\.\d+)?)\s*[%％]")
_CONSEC_MONTHS = re.compile(r"连续\s*(\d+)\s*[个]?\s*月")


@dataclass
class Slots:
    head: Optional[str] = None
    business: Optional[str] = None
    product: Optional[str] = None
    top_n: Optional[int] = None
    amount_threshold: Optional[float] = None
    pct_threshold: Optional[float] = None
    consec_months: Optional[int] = None
    raw: str = ""


def extract_slots(query: str, dicts: SlotDicts) -> Slots:
    s = Slots(raw=query)
    s.head = dicts.find_head_in(query)
    s.business = dicts.find_business_in(query)
    s.product = dicts.find_product_in(query)

    m = _TOP_N_PATTERN.search(query)
    if m:
        for g in m.groups():
            if g:
                s.top_n = int(g)
                break

    m = _THRESHOLD_AMOUNT.search(query)
    if m:
        s.amount_threshold = float(m.group(1))

    m = _THRESHOLD_PCT.search(query)
    if m:
        s.pct_threshold = float(m.group(1))

    m = _CONSEC_MONTHS.search(query)
    if m:
        s.consec_months = int(m.group(1))

    return s


# ─────────────── 触发器表 ───────────────


@dataclass
class Trigger:
    intent_id: str
    keywords_any: list[list[str]]  # 外层 AND，内层 OR；必须每个外层组都至少命中一个
    require_slots: tuple[str, ...] = ()
    forbid_slots: tuple[str, ...] = ()
    score: int = 0  # 静态权重，配合命中长度排序


def _hits(query: str, words: list[str]) -> int:
    """返回命中关键词的总长度（命中越长，置信度越高）。"""
    total = 0
    for w in words:
        if w.lower() in query.lower():
            total += len(w)
    return total


# 关键词分组语义（按 PRD §3）：
# H 维度
TRIGGERS: list[Trigger] = [
    # H 维度 ─────────────────────────────────
    Trigger(
        "H-01",
        keywords_any=[
            ["涨", "增长", "上涨", "涨了", "涨得"],
            ["资源", "实例", "服务"],
        ],
        require_slots=("head",),
        score=10,
    ),
    Trigger(
        "H-02",
        keywords_any=[
            ["降低", "降了", "下降", "降"],
            ["资源", "实例"],
        ],
        require_slots=("head",),
        score=10,
    ),
    Trigger(
        "H-03",
        keywords_any=[
            ["新增", "新加", "新开"],
            ["资源", "实例"],
        ],
        require_slots=("head",),
        score=10,
    ),
    Trigger(
        "H-04",
        keywords_any=[
            ["花了多少", "花费", "总成本", "总费用", "本月花了", "成本"],
        ],
        require_slots=("head",),
        forbid_slots=("product", "business"),
        score=8,
    ),
    Trigger(
        "H-06",
        keywords_any=[
            ["谁花", "谁这个月花", "谁本月花", "花钱最多", "成本最高的人", "Top", "排行", "排名"],
            # 必须有"head/负责人/人/谁"上下文，避免和 P-* 冲撞
            ["谁", "head", "负责人", "人"],
        ],
        forbid_slots=("head", "business", "product"),
        score=12,
    ),
    Trigger(
        "H-08",
        keywords_any=[
            ["没有 head", "未认领", "无负责人", "无人认领", "没人认领", "无 head", "没有负责人"],
        ],
        score=15,
    ),
    Trigger(
        "H-10",
        keywords_any=[
            ["每个 head", "按 head", "按负责人", "head 拆解", "负责人拆解", "head 透视"],
        ],
        forbid_slots=("head",),
        score=12,
    ),
    # B 维度 ─────────────────────────────────
    Trigger(
        "B-01",
        keywords_any=[
            ["花了多少", "花费", "总成本", "总费用", "成本"],
        ],
        require_slots=("business",),
        forbid_slots=("head",),
        score=10,
    ),
    Trigger(
        "B-02",
        keywords_any=[
            ["涨", "增长", "上涨"],
            ["资源", "实例"],
        ],
        require_slots=("business",),
        forbid_slots=("head",),
        score=10,
    ),
    Trigger(
        "B-04",
        keywords_any=[
            ["各业务", "按业务", "业务拆解", "business 拆解", "业务透视"],
        ],
        forbid_slots=("business",),
        score=12,
    ),
    # T 维度 ─────────────────────────────────
    Trigger(
        "T-01",
        keywords_any=[
            ["三个月", "近 3 月", "近三月", "3 月", "3个月"],
            ["增长", "涨", "涨得"],
        ],
        forbid_slots=("head", "business", "product"),
        score=15,
    ),
    Trigger(
        "T-03",
        keywords_any=[
            ["连续", "持续", "一直在涨", "持续上涨", "持续增长"],
        ],
        score=12,
    ),
    # A 维度 ─────────────────────────────────
    Trigger(
        "A-03",
        keywords_any=[
            ["异常", "飙升", "暴涨", "异常飙升", "异常增长"],
        ],
        score=15,
    ),
    Trigger(
        "A-05",
        keywords_any=[
            ["新增", "新开"],
            ["最贵", "最多", "花钱最多", "Top"],
        ],
        score=15,
    ),
    # C 维度 ─────────────────────────────────
    Trigger(
        "C-05",
        keywords_any=[
            ["增长贡献", "贡献度", "增长来源", "涨幅来源", "贡献 Top", "贡献最多"],
        ],
        score=15,
    ),
    # I 维度 ─────────────────────────────────
    Trigger(
        "I-04",
        keywords_any=[
            ["警惕", "需要警惕", "需关注", "重点关注", "提醒", "注意点"],
        ],
        score=15,
    ),
]


@dataclass
class RouteResult:
    intent_id: str
    slots: Slots
    confidence: int  # 命中权重，仅用于调试
    candidates: list[str]  # 备选 intent，便于在歧义场景给用户提示


def _trigger_match(t: Trigger, query: str, slots: Slots) -> int:
    # 槽位约束
    for r in t.require_slots:
        if not getattr(slots, r):
            return -1
    for f in t.forbid_slots:
        if getattr(slots, f):
            return -1
    # 关键词组：外层 AND，内层 OR
    total = 0
    for group in t.keywords_any:
        hit = _hits(query, group)
        if hit == 0:
            return -1
        total += hit
    return total + t.score


def route(query: str, dicts: SlotDicts) -> Optional[RouteResult]:
    slots = extract_slots(query, dicts)
    scored: list[tuple[int, Trigger]] = []
    for t in TRIGGERS:
        s = _trigger_match(t, query, slots)
        if s > 0:
            scored.append((s, t))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top = scored[0]
    candidates = [t.intent_id for _, t in scored[1:4]]
    return RouteResult(top.intent_id, slots, top_score, candidates)


__all__ = ["Slots", "extract_slots", "route", "RouteResult", "TRIGGERS"]
