"""槽位字典 — 从历史账单自动发现 head / business / product 列表。

**自动发现策略**（用户选择 q-2: 全自动）：
  1. 扫描 lookback 月 resources 中所有出现过的 head / business 标签值
  2. 产品名直接从 summary_product 拿
  3. 不写盘、不维护额外文件——每次运行时即时构建

后续如需别名映射（如『邓总』→『Tony.deng』），可在 ~/.tencent-billing/dicts/
下放 head_alias.json 等文件，本模块自动读取并合并；当前默认无别名。
"""
from __future__ import annotations

import json
import os
from typing import Iterable

from .billing_data_loader import BillingDataLoader, ResourceView


_USER_DICT_DIR = os.path.expanduser("~/.tencent-billing/dicts")


def _read_alias_dict(name: str) -> dict[str, str]:
    """读取 ~/.tencent-billing/dicts/{name}_alias.json 别名映射。

    返回 {alias: canonical} 形式。文件缺失或解析失败时返回 {}。
    """
    path = os.path.join(_USER_DICT_DIR, f"{name}_alias.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


class SlotDicts:
    """槽位字典：head / business / product 的可识别集合 + 别名。"""

    def __init__(self, loader: BillingDataLoader):
        self.loader = loader
        self.heads: set[str] = set()
        self.businesses: set[str] = set()
        self.products: set[str] = set()
        self._build()

        self.head_alias = _read_alias_dict("head")
        self.business_alias = _read_alias_dict("business")
        self.product_alias = _read_alias_dict("product")

    def _build(self):
        for r in self.loader.resources():
            h = r.head()
            b = r.business()
            if h:
                self.heads.add(h)
            if b:
                self.businesses.add(b)
            if r.product:
                self.products.add(r.product)
        # 也从 summary_product 收集产品名
        for m in self.loader.months_available():
            for p in self.loader.products(m):
                if p.get("ProductName"):
                    self.products.add(p["ProductName"])

    # ── 公共 API ─────────────────────────────────────────

    def resolve_head(self, text: str) -> str | None:
        """返回 head 名（精确 / 大小写不敏感 / 别名）；未找到返回 None。"""
        return _resolve(text, self.heads, self.head_alias)

    def resolve_business(self, text: str) -> str | None:
        return _resolve(text, self.businesses, self.business_alias)

    def resolve_product(self, text: str) -> str | None:
        return _resolve(text, self.products, self.product_alias)

    def find_head_in(self, query: str) -> str | None:
        """在自然语言 query 中找出第一个匹配的 head。"""
        return _find_in_text(query, self.heads, self.head_alias)

    def find_business_in(self, query: str) -> str | None:
        return _find_in_text(query, self.businesses, self.business_alias)

    def find_product_in(self, query: str) -> str | None:
        return _find_in_text(query, self.products, self.product_alias)


def _resolve(text: str, names: Iterable[str], aliases: dict[str, str]) -> str | None:
    if not text:
        return None
    text = text.strip()
    if text in names:
        return text
    # 大小写不敏感
    lower = text.lower()
    for n in names:
        if n.lower() == lower:
            return n
    # 别名
    if text in aliases and aliases[text] in names:
        return aliases[text]
    return None


def _find_in_text(query: str, names: Iterable[str], aliases: dict[str, str]) -> str | None:
    """在长文本里"含包含"匹配，返回最长命中的名字（避免短前缀冲撞）。"""
    if not query:
        return None
    candidates: list[tuple[int, str]] = []
    for n in names:
        if not n:
            continue
        if n in query:
            candidates.append((len(n), n))
        elif n.lower() in query.lower():
            candidates.append((len(n), n))
    # 别名（命中后映射回 canonical）
    for alias, canonical in aliases.items():
        if alias in query and canonical in set(names):
            candidates.append((len(alias), canonical))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


__all__ = ["SlotDicts"]
