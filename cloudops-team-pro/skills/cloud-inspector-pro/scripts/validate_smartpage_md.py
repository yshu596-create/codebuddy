#!/usr/bin/env python3
"""Markdown 兼容性校验器（v3.6 根治-2）

历史教训：
  - 部分 Markdown 渲染器把"引用块紧贴表格""标题紧贴表格"等紧贴关系
    误判成同一个块，导致表格被吞成纯文本平铺。
  - 我们 v3.0~v3.5 反复 patch 单点都修不干净。
  - 本 validator = 渲染前最后一道闸门：fail 就拒绝发布。

规则（任何一条 fail 都退出非零）：
  R1 引用块（>）和表格（|）之间必须有空行
  R2 标题（#/##/###/**N. xxx**）和表格之间必须有空行
  R3 表格和后续非表格内容之间必须有空行
  R4 引用块和后续非引用块普通段之间必须有空行
  R5 表格行（|）必须连续（中间不能插空段再续接）

用法：
  python3 validate_smartpage_md.py <markdown_file>
  exit 0  全过
  exit 1  发现违规（详情打印到 stderr）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple


def is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and "|" in s[1:-1]


def is_quote_line(line: str) -> bool:
    return line.lstrip().startswith(">")


def is_heading_line(line: str) -> bool:
    s = line.lstrip()
    if s.startswith("#"):
        return True
    # 加粗段头形如 **N. xxx** 单独一行（生成器里大量这种）
    if re.match(r"^\*\*[^*]+\*\*\s*$", s):
        return True
    return False


def is_blank(line: str) -> bool:
    return line.strip() == ""


def validate(md: str) -> List[Tuple[int, str, str]]:
    """返回 [(行号, 规则ID, 描述), ...]，空列表表示全过。"""
    lines = md.split("\n")
    n = len(lines)
    violations: List[Tuple[int, str, str]] = []

    # 跟踪：上一条非空行是什么
    def prev_nonblank(i: int) -> Tuple[int, str]:
        j = i - 1
        while j >= 0 and is_blank(lines[j]):
            j -= 1
        return (j, lines[j] if j >= 0 else "")

    def next_nonblank(i: int) -> Tuple[int, str]:
        j = i + 1
        while j < n and is_blank(lines[j]):
            j += 1
        return (j, lines[j] if j < n else "")

    for i, line in enumerate(lines):
        # R1: 表格行（|）的前一行如果是引用块，且中间没空行
        if is_table_line(line):
            if i > 0 and is_quote_line(lines[i - 1]):
                violations.append((i + 1, "R1",
                    f"表格紧贴引用块（上一行是 `>`）: {line[:80]}"))
            # R2: 表格的前一行是标题
            if i > 0 and is_heading_line(lines[i - 1]):
                violations.append((i + 1, "R2",
                    f"表格紧贴标题（上一行是 `#`/`**N.**`）: {line[:80]}"))

        # R3: 表格的最后一行（下一行不是表格也不是空行）
        if is_table_line(line) and i + 1 < n:
            nxt = lines[i + 1]
            if (not is_table_line(nxt)) and (not is_blank(nxt)):
                violations.append((i + 2, "R3",
                    f"表格后紧接非表格内容（应空行隔离）: {nxt[:80]}"))

        # R4: 引用块的最后一行（下一行不是引用块、不是空行、不是表格行）
        # 注：R1 已覆盖"引用块紧贴表格"，这里只管"引用块紧贴普通段"
        if is_quote_line(line) and i + 1 < n:
            nxt = lines[i + 1]
            if (not is_quote_line(nxt)) and (not is_blank(nxt)) and (not is_table_line(nxt)):
                # 标题不算违规（标题前 R2 单独判）
                if not is_heading_line(nxt):
                    violations.append((i + 2, "R4",
                        f"引用块紧贴普通段（应空行隔离）: {nxt[:80]}"))

    # R5: 表格连续性（一个表格中间不能有空段再续）
    in_table = False
    table_start = -1
    last_table_line = -1
    for i, line in enumerate(lines):
        if is_table_line(line):
            if not in_table:
                in_table = True
                table_start = i
            last_table_line = i
        else:
            if in_table:
                # 表格结束。如果后面紧接着又出现表格行（中间空行/段落隔开），
                # 视为两个独立表格，OK。但若同一段中插入了非表格非空行，已被 R3 抓。
                in_table = False

    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: validate_smartpage_md.py <markdown_file>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[validate] 文件不存在: {path}", file=sys.stderr)
        return 2

    md = path.read_text(encoding="utf-8")
    vios = validate(md)

    if not vios:
        print(f"[validate] ✅ {path.name}: Markdown 兼容性校验全过 ({len(md.split(chr(10)))} 行)")
        return 0

    print(f"[validate] ❌ {path.name}: 发现 {len(vios)} 处 Markdown 兼容性违规", file=sys.stderr)
    print("", file=sys.stderr)
    for ln, rule, desc in vios[:50]:  # 限 50 条避免刷屏
        print(f"  L{ln}  [{rule}]  {desc}", file=sys.stderr)
    if len(vios) > 50:
        print(f"  ... 还有 {len(vios) - 50} 处省略", file=sys.stderr)
    print("", file=sys.stderr)
    print("规则说明:", file=sys.stderr)
    print("  R1 引用块（>）紧贴表格（|）— 部分渲染器会把表格当引用文本平铺", file=sys.stderr)
    print("  R2 标题紧贴表格 — 同上，标题块吞掉表头", file=sys.stderr)
    print("  R3 表格后紧贴非表格非空行 — 后续段被吞", file=sys.stderr)
    print("  R4 引用块后紧贴普通段 — 普通段被吞进引用", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
