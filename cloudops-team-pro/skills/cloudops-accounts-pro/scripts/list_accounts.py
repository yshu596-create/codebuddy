#!/usr/bin/env python3
"""
list_accounts.py — List available multi-cloud / multi-project accounts from SSM.

SSM IS THE SINGLE SOURCE OF TRUTH. Accounts are discovered via SSM ListSecrets
(names only — NO GetSecretValue, so business credentials are NEVER decrypted here).
There is NO env-variable scan and NO bare-name fallback.

A secret named `cloudops/{cloud}/{project}` registers one account, e.g.
  cloudops/tencent/YX      -> cloud=tencent project=YX
  cloudops/huawei/TSP      -> cloud=huawei  project=TSP

Outputs:
  --json   (default) machine-readable JSON
  --human  human-readable table for direct shell preview

Exit codes:
  0  success (even if no accounts found — caller checks `total`)
  2  unexpected internal error
  5  SSM not available (mode disabled or bootstrap runner key absent)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any

# SSM backend is MANDATORY. Without it there is no way to list accounts.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    import ssm_backend as _ssm  # type: ignore
except Exception:  # noqa: BLE001
    _ssm = None  # type: ignore

# Reserved project code — the runner key, never a business account.
RESERVED_PROJECT_CODES = {"BOOTSTRAP"}


def scan() -> list[dict[str, Any]]:
    """List accounts from SSM secret names. Names only — no decryption."""
    names = _ssm.list_secret_names()  # type: ignore[union-attr]
    seen: set[tuple[str, str]] = set()
    accounts: list[dict[str, Any]] = []

    for name in names:
        parsed = _ssm.parse_secret_name(name)  # type: ignore[union-attr]
        if parsed is None:
            continue
        cloud, project = parsed
        if project in RESERVED_PROJECT_CODES:
            continue
        if (cloud, project) in seen:
            continue
        seen.add((cloud, project))
        accounts.append({
            "cloud": cloud,
            "project": project,
            "secret_name": name,
        })

    # Stable sort: cloud then project (DEFAULT last for human readability)
    accounts.sort(key=lambda r: (r["cloud"], r["project"] == "DEFAULT", r["project"]))
    return accounts


def build_tree(accounts: list[dict[str, Any]]) -> dict[str, list[str]]:
    tree: dict[str, list[str]] = defaultdict(list)
    for r in accounts:
        tree[r["cloud"]].append(r["project"])
    return dict(tree)


def render_human(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return (
            "No cloud accounts found in SSM.\n\n"
            "This expert team uses SSM+KMS as the sole credential source.\n"
            "Store each account as a KMS-encrypted secret named cloudops/{cloud}/{project}\n"
            "whose SecretString is a JSON blob, e.g. for cloudops/tencent/YX:\n"
            '  {"secret_id":"...","secret_key":"...","uin":"100012345678","region":"ap-guangzhou"}\n\n'
            "Huawei secret JSON keys: ak, sk, project_id, domain_id, bss_endpoint\n"
            "Aliyun secret JSON keys: access_key_id, access_key_secret, region_id\n\n"
            "The host only needs the runner (bootstrap) key in its environment:\n"
            "  export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=...\n"
            "  export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=...\n"
            "(having the runner key enables SSM automatically; CLOUDOPS_SSM_ENABLED is an\n"
            " optional kill-switch — set it to false/0/no/off only to disable SSM.)\n"
        )

    rows = []
    rows.append(f"{'Cloud':<10} {'Project':<14} {'Secret name':<32}")
    rows.append("-" * 56)
    for r in accounts:
        rows.append(f"{r['cloud']:<10} {r['project']:<14} {r['secret_name']:<32}")
    rows.append("")
    rows.append("(identifiers like UIN/region are inside the KMS-encrypted payload "
                "and are only decrypted at use time, never during listing)")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--human", action="store_true", help="render as table instead of JSON")
    ap.add_argument("--json", action="store_true", help="force JSON output (default)")
    ap.add_argument("--cloud", help="filter by cloud (tencent/huawei/aliyun)")
    args = ap.parse_args()

    # Hard gate: SSM must be available, or there is nothing to list.
    if _ssm is None or not _ssm.is_ssm_available():
        msg = (
            "SSM credential backend is not available. This expert team requires SSM+KMS "
            "as the sole credential source. Ensure the runner key "
            "TENCENTCLOUD_BOOTSTRAP_SECRET_ID/_SECRET_KEY are set (ssm:GetSecretValue/ListSecrets "
            "perms only; no kms:* needed). CLOUDOPS_SSM_ENABLED must not be false/0/no/off."
        )
        if args.human:
            print(msg)
        else:
            json.dump({"error": "ssm_unavailable", "message": msg},
                      sys.stderr, ensure_ascii=False)
            sys.stderr.write("\n")
        return 5

    try:
        accounts = scan()
        if args.cloud:
            accounts = [r for r in accounts if r["cloud"] == args.cloud.lower()]

        if args.human:
            print(render_human(accounts))
            return 0

        payload = {
            "accounts": accounts,
            "tree": build_tree(accounts),
            "total": len(accounts),
            "source": "ssm",
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as e:
        json.dump(
            {"error": "internal_error", "message": str(e)},
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
