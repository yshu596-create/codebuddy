#!/usr/bin/env python3
"""
ssm_backend.py — Tencent Cloud SSM (Secrets Manager) backend for cloudops-accounts-pro.

Pulls credential payloads from Tencent Cloud SSM at runtime, so that the host only
needs ONE bootstrap key (the runner key) instead of N sets of plaintext business AK/SK
scattered across `~/.zshrc`.

Design:
  - Zero third-party deps. Hand-rolled TC3-HMAC-SHA256 signing (same style as the
    5 specialists' sign.py), so it runs on macOS managed python without lxml/sdk conflicts.
  - The runner (bootstrap) key is read from TENCENTCLOUD_BOOTSTRAP_SECRET_ID/KEY
    (optionally _REGION). This key needs only SSM read perms (GetSecretValue / ListSecrets).
  - Business credentials live in SSM as secrets named `cloudops/{cloud}/{project}`,
    whose SecretString is a JSON blob, e.g.:
        {"secret_id": "...", "secret_key": "...", "uin": "100012345678", "region": "ap-guangzhou"}

Two public helpers:
  - fetch_secret(cloud, project) -> dict | None   # GetSecretValue + json.loads
  - list_secret_names() -> list[str]              # ListSecrets (names only, no decrypt)

Exit / error policy:
  - These functions NEVER call sys.exit. They return None / [] on any error and let the
    caller (build_credential_env.resolve / list_accounts.scan_ssm) decide how to degrade.
  - Bootstrap key absence is treated as "SSM not available" → return None / [].
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import urllib.request
from datetime import timezone
from typing import Any

# Secret naming convention inside SSM. {cloud} ∈ {tencent,huawei,aliyun}, {project} ∈ project code.
# Hyphen-delimited form: cloudops-{cloud}-{project}-credential (e.g. cloudops-tencent-sdc-credential).
# {project} is stored lower-case in the secret name; callers normalize to upper-case for display.
SECRET_NAME_TMPL = "cloudops-{cloud}-{project}-credential"
SECRET_NAME_PREFIX = "cloudops-"
SECRET_NAME_SUFFIX = "-credential"

# SSM service endpoint + version (public cloud).
_SSM_HOST = "ssm.tencentcloudapi.com"
_SSM_SERVICE = "ssm"
_SSM_VERSION = "2019-09-23"

# SSM is a region-scoped service, so credentials only show up when you query the region
# they were actually created in. Instead of hard-pinning one region (which breaks the
# moment a secret is created elsewhere), we PROBE a candidate list and lock onto the first
# region that actually contains cloudops-* secrets, then cache it for the process lifetime.
# ap-shanghai is listed first because that's the current known location → probe #1 hits,
# zero extra round-trips in the common case. Region is intentionally NOT read from any env
# var (per design decision 2026-06-15) — the runner key only needs SecretId/Key.
_CANDIDATE_REGIONS = [
    "ap-shanghai", "ap-guangzhou", "ap-beijing", "ap-chengdu",
    "ap-nanjing", "ap-hongkong", "ap-singapore",
]
_DEFAULT_REGION = _CANDIDATE_REGIONS[0]

# Process-level cache of the discovered region. None = not yet probed.
# Lets list_secret_names / fetch_secret share a single scan instead of re-probing each call.
_discovered_region: str | None = None


# ---------------------------------------------------------------------------
# Bootstrap (runner) key resolution
# ---------------------------------------------------------------------------

def _bootstrap_key() -> tuple[str, str, str] | None:
    """Return (secret_id, secret_key, region) of the runner key, or None if absent.

    Only the reserved BOOTSTRAP prefix is honored — never the business bare names,
    so a business account can never accidentally act as the SSM runner.

    The region is auto-discovered (see _discover_region) rather than hard-pinned, and
    intentionally NOT read from TENCENTCLOUD_BOOTSTRAP_REGION — the candidate scan finds
    whichever region actually holds the cloudops-* secrets, with the result cached for the
    process lifetime so the probe runs at most once.
    """
    sid = os.environ.get("TENCENTCLOUD_BOOTSTRAP_SECRET_ID")
    sk = os.environ.get("TENCENTCLOUD_BOOTSTRAP_SECRET_KEY")
    if not (sid and sk):
        return None
    return sid, sk, _discover_region(sid, sk)


def _region_has_secrets(sid: str, sk: str, region: str) -> bool:
    """True iff ListSecrets in `region` returns at least one cloudops-* secret.

    A single page is enough to decide — we only care whether the region holds ANY of our
    secrets, not how many. Network/permission errors yield False so we move to the next
    candidate instead of raising.
    """
    resp = _tc3_call(
        "ListSecrets",
        {"Offset": 0, "Limit": 100, "OrderType": 0, "State": 1},
        sid, sk, region,
    )
    if "Error" in resp:
        return False
    for m in resp.get("SecretMetadatas") or []:
        if str(m.get("SecretName", "")).startswith(SECRET_NAME_PREFIX):
            return True
    return False


def _discover_region(sid: str, sk: str) -> str:
    """Probe _CANDIDATE_REGIONS and return the first region holding cloudops-* secrets.

    Result is cached in the module-global _discovered_region so the scan runs at most once
    per process (list_secret_names + multiple fetch_secret calls all share it). If no
    candidate region contains our secrets (e.g. nothing created yet, or created in an
    exotic region), fall back to _DEFAULT_REGION so behavior stays deterministic.
    """
    global _discovered_region
    if _discovered_region is not None:
        return _discovered_region
    for region in _CANDIDATE_REGIONS:
        if _region_has_secrets(sid, sk, region):
            _discovered_region = region
            return region
    # Nothing found anywhere — pin the default and stop probing for this process.
    _discovered_region = _DEFAULT_REGION
    return _discovered_region


def is_ssm_available() -> bool:
    """True iff the bootstrap key is present and SSM is not explicitly disabled.

    SSM is the sole credential source, so the presence of a runner key is itself the
    signal to use SSM — you only need to set TENCENTCLOUD_BOOTSTRAP_SECRET_ID/_KEY.

    CLOUDOPS_SSM_ENABLED is an OPTIONAL emergency kill-switch: set it to one of
    false/0/no/off to force SSM off WITHOUT deleting the runner key (handy for
    troubleshooting). Any other value (including unset) leaves SSM enabled.
    """
    flag = os.environ.get("CLOUDOPS_SSM_ENABLED", "").strip().lower()
    if flag in ("false", "0", "no", "off"):
        return False
    # Check key presence ONLY — do not trigger the region probe here, an availability
    # check shouldn't cost a network round-trip. The probe runs lazily on first fetch/list.
    return bool(
        os.environ.get("TENCENTCLOUD_BOOTSTRAP_SECRET_ID")
        and os.environ.get("TENCENTCLOUD_BOOTSTRAP_SECRET_KEY")
    )


# ---------------------------------------------------------------------------
# TC3-HMAC-SHA256 signing (self-contained, mirrors sign.py)
# ---------------------------------------------------------------------------

def _tc3_call(action: str, payload: dict[str, Any],
              secret_id: str, secret_key: str, region: str) -> dict[str, Any]:
    """Sign and POST one SSM request. Returns the `Response` dict (or {"Error":...})."""
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(datetime.datetime.now(tz=timezone.utc).timestamp())
    date = datetime.datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    content_type = "application/json; charset=utf-8"
    payload_bytes = json.dumps(payload).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{_SSM_HOST}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    credential_scope = f"{date}/{_SSM_SERVICE}/tc3_request"
    str_to_sign = (
        f"{algorithm}\n{timestamp}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    signing_key = _hmac(
        _hmac(_hmac(("TC3" + secret_key).encode("utf-8"), date), _SSM_SERVICE),
        "tc3_request",
    )
    signature = hmac.new(signing_key, str_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": _SSM_HOST,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": _SSM_VERSION,
        "X-TC-Region": region,
    }

    req = urllib.request.Request(
        f"https://{_SSM_HOST}/", data=payload_bytes, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()).get("Response", {})
    except Exception as e:  # noqa: BLE001 — never raise into caller; degrade gracefully
        return {"Error": {"Code": "NetworkError", "Message": str(e)}}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _resolve_version_id(name: str, sid: str, sk: str, region: str) -> str:
    """Return a usable VersionId for a secret.

    Secrets created via the console "credential value" flow expose the `SSM_Current`
    alias, but secrets written with an explicit custom VersionId (e.g. `v1`) do NOT —
    GetSecretValue then fails with ResourceNotFound. So we look up the real version id
    via ListSecretVersionIds and fall back to SSM_Current only when none is found.
    """
    resp = _tc3_call("ListSecretVersionIds", {"SecretName": name}, sid, sk, region)
    versions = resp.get("Versions") or []
    if versions:
        # Prefer the most recently created version.
        versions.sort(key=lambda v: v.get("CreateTime", 0), reverse=True)
        vid = versions[0].get("VersionId")
        if vid:
            return vid
    return "SSM_Current"


def fetch_secret(cloud: str, project: str) -> dict[str, Any] | None:
    """GetSecretValue for `cloudops-{cloud}-{project}-credential` and json.loads the SecretString.

    Returns the parsed dict (lower-case keys by convention), or None if:
      - SSM is not available (no bootstrap key / not enabled), or
      - the secret does not exist / access denied / malformed JSON.
    """
    bk = _bootstrap_key()
    if bk is None:
        return None
    sid, sk, region = bk

    name = SECRET_NAME_TMPL.format(cloud=cloud.lower(), project=project.lower())
    version_id = _resolve_version_id(name, sid, sk, region)
    resp = _tc3_call(
        "GetSecretValue",
        {"SecretName": name, "VersionId": version_id},
        sid, sk, region,
    )
    if "Error" in resp:
        return None

    raw = resp.get("SecretString")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    # Normalize keys to lower-case so callers can rely on payload.get("secret_id") etc.
    return {str(k).lower(): v for k, v in data.items()}


def list_secret_names() -> list[str]:
    """ListSecrets and return the subset of names under the `cloudops/` prefix.

    Names only — NO GetSecretValue, so this NEVER decrypts business credentials.
    Returns [] if SSM is unavailable or on any error.
    """
    bk = _bootstrap_key()
    if bk is None:
        return []
    sid, sk, region = bk

    names: list[str] = []
    offset = 0
    limit = 100
    for _ in range(20):  # hard page cap: 2000 secrets is plenty
        resp = _tc3_call(
            "ListSecrets",
            {"Offset": offset, "Limit": limit, "OrderType": 0, "State": 1},
            sid, sk, region,
        )
        if "Error" in resp:
            break
        meta = resp.get("SecretMetadatas") or []
        if not meta:
            break
        for m in meta:
            nm = m.get("SecretName", "")
            if nm.startswith(SECRET_NAME_PREFIX):
                names.append(nm)
        total = resp.get("TotalCount", 0)
        offset += limit
        if offset >= total:
            break
    return names


def parse_secret_name(name: str) -> tuple[str, str] | None:
    """`cloudops-tencent-sdc-credential` -> ("tencent", "SDC").

    Hyphen-delimited convention: cloudops-{cloud}-{project}-credential.
    {project} may itself contain hyphens; everything between the cloud segment and the
    trailing `-credential` suffix is treated as the project code (upper-cased for display).
    Returns None if not the expected shape.
    """
    if not name.startswith(SECRET_NAME_PREFIX) or not name.endswith(SECRET_NAME_SUFFIX):
        return None
    # Strip prefix and suffix, leaving "{cloud}-{project}".
    middle = name[len(SECRET_NAME_PREFIX):-len(SECRET_NAME_SUFFIX)]
    parts = middle.split("-", 1)
    if len(parts) != 2:
        return None
    cloud, project = parts
    if not cloud or not project:
        return None
    return cloud.lower(), project.upper()
