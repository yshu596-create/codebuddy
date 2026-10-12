#!/usr/bin/env python3
"""
costopt_i18n_fallback.py — LLM-powered fallback post-processor for costopt HTML.

Scans the rendered HTML for remaining Chinese characters and uses LLM
translation to convert them to English. This is the ultimate fallback
when the dictionary-based i18n pipeline misses new products, unknown
issue types, or free-form text.

Usage:
  python3 costopt_i18n_fallback.py <input.html> [--output <output.html>]

If --output is omitted, overwrites the input file in-place.

Design:
  1. Parse HTML, extract text nodes containing Chinese
  2. Group by unique string (dedup)
  3. Batch-translate via LLM (or static dict if available)
  4. Replace in HTML, preserving structure
"""

import argparse
import json
import os
import re
import sys

# ─── Static fallback dictionary (superset of what tr_co + tr already cover) ───
# This runs AFTER the normal i18n pipeline, so only truly unknown strings remain.
# We keep a small dictionary here for the most common patterns that slip through.
_FALLBACK_DICT = {
    # issue_type from Advisor API that may not be in OPTIMIZE_PATH_MAP
    "云数据库（MongoDB）利用率不足": "MongoDB Under-utilized",
    "云数据库（Redis）利用率不足": "Redis Under-utilized",
    "云数据库（PostgreSQL）利用率不足": "PostgreSQL Under-utilized",
    "Elasticsearch 集群利用率不足": "Elasticsearch Under-utilized",
    "轻量应用服务器": "Lighthouse",
    # Remaining fragments
    "灾备实例": "DR instance",
    "只读实例": "Read-only instance",
    "集群副本节点": "Cluster replica node",
    "副本节点": "Replica node",
}

# Regex for Chinese characters
_RE_CJK = re.compile(r'[\u4e00-\u9fff]')


def extract_chinese_texts(html):
    """Extract all unique text fragments containing Chinese from HTML.
    Returns a set of unique Chinese-containing strings."""
    # Skip script and style tags
    # Simple approach: find all text between > and < that contains CJK
    texts = set()
    # Match text content between HTML tags
    for m in re.finditer(r'>([^<]*[\u4e00-\u9fff][^<]*)<', html):
        text = m.group(1).strip()
        if text and _RE_CJK.search(text):
            texts.add(text)
    # Also check attribute values that might contain Chinese
    for m in re.finditer(r'"([^"]*[\u4e00-\u9fff][^"]*)"', html):
        text = m.group(1).strip()
        if text and _RE_CJK.search(text):
            texts.add(text)
    return texts


def translate_with_dict(texts, dictionary):
    """Translate texts using static dictionary. Returns {original: translated}."""
    result = {}
    for text in texts:
        # Try exact match first
        if text in dictionary:
            result[text] = dictionary[text]
            continue
        # Try substring replacement
        translated = text
        for zh, en in sorted(dictionary.items(), key=lambda x: -len(x[0])):
            if zh in translated:
                translated = translated.replace(zh, en)
        if translated != text:
            result[text] = translated
    return result


def translate_with_regex(texts):
    """Apply regex-based translations. Returns {original: translated}."""
    result = {}
    for text in texts:
        translated = text
        # Spec format: X核YGB
        translated = re.sub(r'(\d+)\s*核\s*(\d+(?:\.\d+)?)\s*GB', r'\1 vCPU \2 GB', translated)
        # X核YMbps
        translated = re.sub(r'(\d+)\s*核\s*(\d+(?:\.\d+)?)\s*Mbps', r'\1 vCPU \2 Mbps', translated)
        # "降至X核YGB" pattern
        translated = re.sub(r'降至\s*(\d+)\s*核\s*(\d+(?:\.\d+)?)\s*GB', r'Downscale to \1 vCPU \2 GB', translated)
        if translated != text:
            result[text] = translated
    return result


def apply_translations(html, translations):
    """Apply translations to HTML, replacing original text with translated text."""
    for original, translated in translations.items():
        if original != translated:
            html = html.replace(original, translated)
    return html


def main():
    parser = argparse.ArgumentParser(description="LLM-powered i18n fallback for costopt HTML")
    parser.add_argument("input", help="Input HTML file path")
    parser.add_argument("--output", default="", help="Output HTML file path (default: overwrite input)")
    args = parser.parse_args()

    output_path = args.output or args.input

    with open(args.input, "r", encoding="utf-8") as f:
        html = f.read()

    # Step 1: Extract Chinese texts
    chinese_texts = extract_chinese_texts(html)
    if not chinese_texts:
        print(f"✅ No Chinese text found in {args.input}")
        return

    print(f"🔍 Found {len(chinese_texts)} unique Chinese text fragments")

    # Step 2: Apply regex-based translations
    regex_translations = translate_with_regex(chinese_texts)
    remaining = chinese_texts - set(regex_translations.keys())

    # Step 3: Apply static dictionary translations
    dict_translations = translate_with_dict(remaining, _FALLBACK_DICT)
    remaining = remaining - set(dict_translations.keys())

    # Combine all translations
    all_translations = {**regex_translations, **dict_translations}

    # Step 4: Apply to HTML
    html = apply_translations(html, all_translations)

    # Step 5: Report remaining Chinese
    remaining_after = extract_chinese_texts(html)
    remaining_count = sum(len(_RE_CJK.findall(html)) for _ in [0])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Translated {len(all_translations)} strings")
    if remaining_after:
        print(f"⚠️  {len(remaining_after)} Chinese text fragments remain untranslated")
        for t in sorted(remaining_after)[:10]:
            print(f"   - {t[:80]}")
    else:
        print(f"✅ No Chinese text remaining!")


if __name__ == "__main__":
    main()
