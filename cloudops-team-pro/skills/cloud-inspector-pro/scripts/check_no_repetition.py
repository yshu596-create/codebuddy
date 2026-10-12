#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户版云巡检报告 "共因复述" 防回退校验器。

用法：
    python check_no_repetition.py /path/to/inspector_doc_customer.md
    python check_no_repetition.py /path/to/dir/  # 自动扫所有 inspector_doc*.md

判定规则：
- 任何 8 字以上完全相同的"句子"在同一文档中出现 >=3 次 → FAIL（共因冗余回退）
- 句子定义：以中文标点（。；！？）或换行切分后，去除前导 markdown 前缀（- / | / > / # / * / 数字.）
- 引用块（以 > 开头的行）单独统计，不计入主体重复，避免误伤合规折叠详单

退出码：
    0  = 通过
    1  = 检测到回退（需修生成器）
    2  = 文件不存在或参数错误
"""
import re
import sys
import json
from collections import Counter
from pathlib import Path

REPEAT_THRESHOLD = 3
MIN_SENTENCE_LEN = 8

# 句子提取：先按中文/英文标点和换行切，再过滤
_SENT_SPLIT = re.compile(r"[。；！？\n]+")
_PREFIX_STRIP = re.compile(r"^[\s>\-\*\#\|]+|^\d+[\.\)、]\s*")


def extract_sentences(text: str, *, include_quote: bool = False):
    """从 markdown 文本提取去前缀的"句子"，返回 list[(sentence, line_no)]"""
    out = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # 引用块默认不参与主体重复检测（合规折叠详单常用 > 缩进）
        if (not include_quote) and line.lstrip().startswith(">"):
            continue
        # 表格分隔行跳过
        if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
            continue
        # 表格行：取每个单元格内容
        if "|" in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            for cell in cells:
                if cell:
                    for s in _SENT_SPLIT.split(cell):
                        s = _PREFIX_STRIP.sub("", s).strip()
                        if len(s) >= MIN_SENTENCE_LEN:
                            out.append((s, line_no))
            continue
        # 普通段落
        for s in _SENT_SPLIT.split(line):
            s = _PREFIX_STRIP.sub("", s).strip()
            if len(s) >= MIN_SENTENCE_LEN:
                out.append((s, line_no))
    return out


def analyze(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    sentences = extract_sentences(text)
    counter = Counter(s for s, _ in sentences)
    # 按出现次数倒序
    repeats = [(s, n) for s, n in counter.items() if n >= REPEAT_THRESHOLD]
    repeats.sort(key=lambda x: -x[1])
    # 命中行号
    hits = []
    for sent, n in repeats:
        line_nos = sorted({ln for s, ln in sentences if s == sent})
        hits.append({
            "sentence": sent[:120] + ("…" if len(sent) > 120 else ""),
            "count": n,
            "line_nos": line_nos[:10] + (["…"] if len(line_nos) > 10 else []),
        })
    return {
        "file": str(path),
        "total_sentences": len(sentences),
        "unique_sentences": len(counter),
        "repeat_threshold": REPEAT_THRESHOLD,
        "violations": hits,
        "passed": len(hits) == 0,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    target = Path(sys.argv[1]).expanduser()
    if not target.exists():
        print(f"ERROR: 路径不存在: {target}", file=sys.stderr)
        sys.exit(2)
    targets = []
    if target.is_dir():
        for pat in ["inspector_doc.md", "inspector_doc_customer.md"]:
            for p in target.rglob(pat):
                targets.append(p)
    else:
        targets.append(target)
    if not targets:
        print(f"ERROR: 在 {target} 下没找到 inspector_doc*.md", file=sys.stderr)
        sys.exit(2)
    overall_pass = True
    results = []
    for p in targets:
        r = analyze(p)
        results.append(r)
        if not r["passed"]:
            overall_pass = False
    print(json.dumps({"passed": overall_pass, "results": results}, ensure_ascii=False, indent=2))
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
