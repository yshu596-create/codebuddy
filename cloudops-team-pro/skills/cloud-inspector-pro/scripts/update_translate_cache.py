#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_translate_cache.py — 将翻译结果写入持久化缓存，供后续报告生成时自动复用。

用法：
  # 方式 1：从 JSON 文件读取翻译结果（Agent LLM 补全后推荐）
  python3 update_translate_cache.py --from-file /path/to/translations.json

  # 方式 2：从命令行直接传 key=value 对
  python3 update_translate_cache.py --kv "中文规则名=English rule name" --kv "另一个=Another one"

  # 方式 3：查看当前缓存内容
  python3 update_translate_cache.py --show

  # 方式 4：Agent LLM 自动补全模式（读取 untranslated.json → 输出 → 写缓存）
  # Agent 读取 ~/.cache/cloudops-inspector/untranslated.json
  # Agent 翻译后生成 JSON: {"中文1": "English1", ...}
  # Agent 调用: python3 update_translate_cache.py --from-file /path/to/agent_translations.json
"""
import argparse
import json
import sys
from pathlib import Path

# 复用 lib_report_translate 的缓存路径
sys.path.insert(0, str(Path(__file__).parent))
from lib_report_translate import (
    _TRANSLATE_CACHE_PATH,
    _UNTRANSLATED_PATH,
    _load_translate_cache,
    _save_translate_cache,
    update_cache_from_dict,
)


def show_cache():
    """显示当前缓存内容。"""
    cache = _load_translate_cache()
    print(f"Cache file: {_TRANSLATE_CACHE_PATH}")
    print(f"Entries: {len(cache)}")
    if cache:
        print("\n--- Cached translations ---")
        for zh, en in sorted(cache.items(), key=lambda kv: len(kv[0]), reverse=True):
            print(f"  {zh[:60]:60s} → {en[:60]}")


def show_untranslated():
    """显示未翻译串。"""
    if not _UNTRANSLATED_PATH.exists():
        print("No untranslated strings file found.")
        return
    with open(_UNTRANSLATED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    translatable = [it for it in items if not it.get("skip")]
    skipped = [it for it in items if it.get("skip")]
    print(f"Untranslated file: {_UNTRANSLATED_PATH}")
    print(f"Translatable: {len(translatable)}, Skipped (user-defined): {len(skipped)}")
    if translatable:
        print("\n--- Need translation ---")
        for it in translatable:
            print(f"  {it['text'][:80]}")
            if it.get("context"):
                print(f"    context: {it['context'][:80]}")
    if skipped:
        print(f"\n--- Skipped (user-defined, {len(skipped)} items) ---")
        for it in skipped[:10]:
            print(f"  {it['text'][:60]}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")


def from_file(path: str):
    """从 JSON 文件读取翻译结果并更新缓存。"""
    with open(path, "r", encoding="utf-8") as f:
        translations = json.load(f)
    if not isinstance(translations, dict):
        print("Error: JSON file must be a flat object {Chinese: English}", file=sys.stderr)
        sys.exit(1)
    added = update_cache_from_dict(translations)
    total = len(_load_translate_cache())
    print(f"Added {added} new translations. Cache now has {total} entries.")
    print(f"Cache file: {_TRANSLATE_CACHE_PATH}")


def from_kv(pairs: list[str]):
    """从 key=value 对读取翻译结果并更新缓存。"""
    translations = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"Warning: skipping malformed pair (no '='): {pair}", file=sys.stderr)
            continue
        zh, en = pair.split("=", 1)
        translations[zh.strip()] = en.strip()
    added = update_cache_from_dict(translations)
    total = len(_load_translate_cache())
    print(f"Added {added} new translations. Cache now has {total} entries.")


def main():
    parser = argparse.ArgumentParser(description="Update the inspector translation cache")
    parser.add_argument("--show", action="store_true", help="Show current cache contents")
    parser.add_argument("--show-untranslated", action="store_true", help="Show untranslated strings")
    parser.add_argument("--from-file", metavar="PATH", help="Read translations from a JSON file")
    parser.add_argument("--kv", action="append", help="Add a key=value translation pair")
    args = parser.parse_args()

    if args.show:
        show_cache()
    elif args.show_untranslated:
        show_untranslated()
    elif args.from_file:
        from_file(args.from_file)
    elif args.kv:
        from_kv(args.kv)
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  1. Run report generation with --lang en")
        print("  2. Check untranslated: python3 update_translate_cache.py --show-untranslated")
        print("  3. Translate them (manually or via Agent LLM)")
        print("  4. Update cache: python3 update_translate_cache.py --from-file translations.json")
        print("  5. Re-run report generation — cache will be applied automatically")


if __name__ == "__main__":
    main()
