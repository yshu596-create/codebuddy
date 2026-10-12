#!/usr/bin/env python3
"""query_billing.py — 提示词扩展查询入口

用法：
  python3 query_billing.py --month 202604 "andyyfang 这个月哪些资源涨了"
  python3 query_billing.py --month 202604 "谁这个月花钱最多"
  python3 query_billing.py --month 202604 --cache-base /path/to/cache "本月最该警惕的 3 件事"

工作流：
  1. 解析凭证（复用 prepare_billing_data.py 的 resolve_credentials），
     拿到 cache_base = <WORKSPACE>/cache/billing/{uin}
  2. BillingDataLoader 加载 6 月缓存
  3. 用 SlotDicts 构建槽位字典
  4. router.route() 把 query 路由到 intent_id
  5. handlers.HANDLERS[intent_id] 渲染输出
  6. 未命中 → 返回提示，建议改用月度全量分析

输出：纯文本三段式 markdown（直接打印到 stdout）。
"""
from __future__ import annotations

import argparse
import os
import sys

# 让脚本可以独立运行
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from query.billing_data_loader import BillingDataLoader
from query.handlers import HANDLERS
from query.router import route
from query.slot_dicts import SlotDicts


def _resolve_cache_base(args) -> tuple[str, str]:
    """返回 (cache_base, display_name)。优先使用 --cache-base，其次解析凭证。"""
    if args.cache_base:
        return args.cache_base, args.account_name or ""

    # 复用 prepare_billing_data.py 的凭证解析
    sys.path.insert(0, SCRIPT_DIR)
    try:
        from prepare_billing_data import resolve_credentials  # type: ignore
    except Exception as e:
        print(f"[ERR] 无法加载凭证模块：{e}", file=sys.stderr)
        sys.exit(2)

    creds = resolve_credentials(account_name=args.account_name or None,
                                uin=args.uin or None)
    if creds is None or creds.get("status") != "ok":
        prompt = (creds or {}).get("prompt", "缺少 AK/SK 配置")
        print(prompt)
        sys.exit(0)

    uin = creds["uin"]
    # 缓存路径：<WORKSPACE>/cache/billing/{uin}
    base = os.path.join(
        os.environ.get("WORKBUDDY_WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "cache", "billing", uin,
    )
    return base, creds.get("display_name", "")


def main():
    parser = argparse.ArgumentParser(description="账单提示词扩展查询")
    parser.add_argument("query", help="自然语言查询，如 'andyyfang 这个月哪些资源涨了'")
    parser.add_argument("--month", required=True, help="分析月，YYYYMM")
    parser.add_argument("--cache-base", default="",
                        help="缓存根目录，覆盖默认凭证路径")
    parser.add_argument("--account-name", default="", help="账户中文名")
    parser.add_argument("--uin", default="", help="UIN")
    parser.add_argument("--lookback", type=int, default=6, help="回看月数（含分析月）")
    parser.add_argument("--debug", action="store_true", help="打印路由调试信息")
    args = parser.parse_args()

    cache_base, _ = _resolve_cache_base(args)
    if not os.path.isdir(cache_base):
        print(f"⚠️ 缓存目录不存在：`{cache_base}`\n请先运行月度分析建立缓存。")
        sys.exit(0)

    loader = BillingDataLoader(cache_base, args.month, lookback=args.lookback)
    months = loader.months_available()
    if not months:
        print(f"⚠️ {args.month} 起回看 {args.lookback} 月均无缓存数据。")
        sys.exit(0)

    dicts = SlotDicts(loader)
    result = route(args.query, dicts)

    if args.debug:
        print(f"[debug] available months: {months}", file=sys.stderr)
        print(f"[debug] slots(heads={len(dicts.heads)}, business={len(dicts.businesses)}, "
              f"products={len(dicts.products)})", file=sys.stderr)
        if result:
            print(f"[debug] route={result.intent_id} score={result.confidence} "
                  f"slots={result.slots} candidates={result.candidates}", file=sys.stderr)
        else:
            print("[debug] route=None", file=sys.stderr)

    if not result:
        print(_fallback_message(args.query, dicts))
        sys.exit(0)

    handler = HANDLERS.get(result.intent_id)
    if not handler:
        print(f"⚠️ 路由命中 {result.intent_id}，但 handler 未实现（属于 P1+ 范围）。")
        sys.exit(0)

    out = handler(loader, result.slots, dicts)
    print(out)


def _fallback_message(query: str, dicts: SlotDicts) -> str:
    return (
        f"**【未识别意图】**\n"
        f"暂无法把 '{query}' 路由到现有触发器。\n\n"
        f"**【提示】**\n"
        f"已知 head：{', '.join(sorted(dicts.heads)[:8])}\n"
        f"已知业务：{', '.join(sorted(dicts.businesses)[:8])}\n\n"
        f"**【可尝试的提问形式】**\n"
        f"- `{{head}} 这个月哪些资源涨了`（H-01）\n"
        f"- `谁这个月花钱最多`（H-06）\n"
        f"- `各业务线成本拆解`（B-04）\n"
        f"- `本月异常飙升的资源`（A-03）\n"
        f"- `本月最该警惕的 3 件事`（I-04）\n"
    )


if __name__ == "__main__":
    main()
