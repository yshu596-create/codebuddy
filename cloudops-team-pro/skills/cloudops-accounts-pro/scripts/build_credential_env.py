#!/usr/bin/env python3
"""
build_credential_env.py — Resolve business credentials from Tencent Cloud SSM (KMS-backed).

SSM IS THE SINGLE SOURCE OF TRUTH. There is NO env-prefix path and NO bare-name fallback.
Business AK/SK never live in the shell environment — they are stored as KMS-encrypted
secrets in Tencent Cloud SSM and pulled at runtime using the runner (bootstrap) key.

Given (cloud, project), this fetches the secret `cloudops/{cloud}/{project}` via SSM
GetSecretValue (SSM decrypts it with KMS server-side), maps the JSON fields onto
SDK-standard env names, and returns them.

Cross-account (STS AssumeRole) is built IN here — the ONE place
------------------------------------------------------------------
If the resolved secret carries a `role_arn`, resolve() performs the AssumeRole
IN-PROCESS and returns the *temporary* TmpSecretId/TmpSecretKey/Token instead of the
caller's AK. The caller (main-account) AK that signs AssumeRole is itself sourced from
SSM — either inline in the same secret, or (preferred) from a separate parent secret
referenced via the `assume_from` field (e.g. {"role_arn": "...", "assume_from": "PARENT"}).

This makes resolve() the SINGLE cross-account entry point. Downstream specialists
(billing/costopt/auditor/inspector) need ZERO assume logic: they just consume the
returned dict — TENCENTCLOUD_SECRET_ID/_KEY are already the temp creds, and
TENCENTCLOUD_TOKEN is the STS token to pass to set_process_token()/X-TC-Token.
ROLE_ARN is intentionally NOT re-exported in the result, so a downstream that still
has legacy `if role_arn: assume` code will NOT double-assume.

Concurrency: resolve() is a pure function. The AssumeRole result lives ONLY in
process-local vars and is returned as a plain dict — never written to os.environ.
Under multi-account fan-out (one process per (cloud, project)), each process assumes
its own role independently; nothing is shared, so no bleed.

PRIMARY (and intended) use is in-process, concurrency-safe — downstream collectors
`import build_credential_env` and call `resolve(cloud, project)` to get the SDK env
mapping as a plain dict, held only in process-local vars (never written back to the
shared os.environ). This is the only safe pattern under multi-account fan-out:

  import build_credential_env as bce
  env = bce.resolve("tencent", "YX")     # raises SystemExit on missing/invalid
  sid = env["TENCENTCLOUD_SECRET_ID"]     # stays local to this process

The CLI (--format json|shell) is a debugging/inspection aid only. Business
credentials must NEVER be `eval`-ed/exported into a shared shell — that is race-prone
under concurrent fan-out (last-writer-wins → account bleed) and leaks secrets into
shell history. Use resolve() in-process.

Prerequisites (host must have, and ONLY this, in the environment):
  TENCENTCLOUD_BOOTSTRAP_SECRET_ID / _SECRET_KEY   (runner key: ssm:GetSecretValue/ListSecrets only)
  TENCENTCLOUD_BOOTSTRAP_REGION                    (optional, default ap-guangzhou)
  CLOUDOPS_SSM_ENABLED                             (optional kill-switch; set false/0/no/off to
                                                    disable SSM without deleting the runner key)

Note: the runner key needs NO kms:* permission. SSM decrypts the secret server-side
under its own service role (SSM_QCSRole, authorized once when SSM is activated) and
returns plaintext over TLS — unlike AWS, the caller's identity is not the KMS principal.

Modes:
  --shell  (default) shell export lines for `eval`
  --json   JSON dict of env vars (for python subprocess env=... callers)

Exit codes:
  0  success
  3  invalid arguments (unknown cloud, malformed/reserved project code)
  4  secret not found in SSM, or missing required field inside the secret payload
  5  SSM not available (mode disabled or bootstrap runner key absent)
  6  cross-account AssumeRole failed (role_arn present but STS call/trust failed)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import shlex
import sys
import time
import urllib.request
from datetime import timezone
from typing import Any

# SSM backend is MANDATORY. If the lib import fails the script cannot resolve anything.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    import ssm_backend as _ssm  # type: ignore
except Exception:  # noqa: BLE001
    _ssm = None  # type: ignore

PROJECT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$|^DEFAULT$")

# Reserved project code: the SSM runner (bootstrap) key lives under TENCENTCLOUD_BOOTSTRAP_*.
# It must NEVER be selectable as a business account, or it would (a) show up as a ghost
# project in the picker and (b) let the runner key sign business APIs directly.
RESERVED_PROJECT_CODES = {"BOOTSTRAP"}

CLOUD_TO_VENDOR = {
    "tencent": "TENCENTCLOUD",
    "huawei": "HUAWEICLOUD",
    "aliyun": "ALIYUNCLOUD",
}

# (ssm_json_key, sdk_env_name, required) — the SSM secret payload is a JSON blob with
# lower-case keys (e.g. {"secret_id":..., "secret_key":..., "uin":..., "region":...}).
# ssm_backend.fetch_secret() already lower-cases every key, so we look up by lower-case here.
# Required fields are marked; a missing required field inside the payload → exit 4.
TENCENT_MAP: list[tuple[str, str, bool]] = [
    ("secret_id", "TENCENTCLOUD_SECRET_ID", True),
    ("secret_key", "TENCENTCLOUD_SECRET_KEY", True),
    ("region", "TENCENTCLOUD_REGION", False),
    ("uin", "INSPECTION_UIN", False),
    ("role_arn", "TENCENTCLOUD_ROLE_ARN", False),
    ("role_external_id", "TENCENTCLOUD_ROLE_EXTERNAL_ID", False),
    ("role_duration", "TENCENTCLOUD_ROLE_DURATION", False),
    # STS / SSM 临时凭证 Token：动态凭证场景透传，普通 AK/SK 直连留空即可。
    # 两个 SDK 命名都收：sign.py 的 _default_token() 会按 TOKEN > SECURITY_TOKEN 优先消费。
    ("token", "TENCENTCLOUD_TOKEN", False),
    ("security_token", "TENCENTCLOUD_SECURITY_TOKEN", False),
]

HUAWEI_MAP: list[tuple[str, str, bool]] = [
    ("ak", "HUAWEICLOUD_SDK_AK", True),
    ("sk", "HUAWEICLOUD_SDK_SK", True),
    ("project_id", "HUAWEICLOUD_SDK_PROJECT_ID", False),
    ("domain_id", "HUAWEICLOUD_SDK_DOMAIN_ID", False),
    ("domain_name", "HUAWEICLOUD_SDK_DOMAIN_NAME", False),
    ("bss_endpoint", "HUAWEICLOUD_BSS_ENDPOINT", False),
    # Temporary-credential compatibility. Huawei returns credential.securitytoken
    # from some federation/agency flows; request signing must send it as
    # X-Security-Token and include that header in SignedHeaders.
    ("security_token", "HUAWEICLOUD_SECURITY_TOKEN", False),
    ("securitytoken", "HUAWEICLOUD_SECURITY_TOKEN", False),
    ("token", "HUAWEICLOUD_SECURITY_TOKEN", False),
    ("expires_at", "HUAWEICLOUD_CREDENTIAL_EXPIRES_AT", False),
]

ALIYUN_MAP: list[tuple[str, str, bool]] = [
    ("access_key_id", "ALIBABA_CLOUD_ACCESS_KEY_ID", True),
    ("access_key_secret", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", True),
    ("region_id", "ALIBABA_CLOUD_REGION_ID", False),
]

VENDOR_MAP = {
    "TENCENTCLOUD": TENCENT_MAP,
    "HUAWEICLOUD": HUAWEI_MAP,
    "ALIYUNCLOUD": ALIYUN_MAP,
}


# ===========================================================================
# Cross-account STS AssumeRole — self-contained TC3-HMAC-SHA256 signer.
# Folded in here (from the now-removed sts_assume_role.py) so resolve() is the
# ONE place that does cross-account. Zero third-party deps; never touches os.environ.
# ===========================================================================

def _tc3_call(host: str, service: str, action: str, version: str,
              payload: dict[str, Any], region: str,
              secret_id: str, secret_key: str, token: str = "",
              timeout: int = 20) -> dict[str, Any]:
    """Minimal TC3 signed POST. Returns the parsed `Response` dict (or {"Error":...})."""
    algorithm = "TC3-HMAC-SHA256"
    ts = int(time.time())
    date = datetime.datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    body = json.dumps(payload).encode("utf-8")
    payload_hash = hashlib.sha256(body).hexdigest()

    canonical_headers = (
        f"content-type:{ct}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

    scope = f"{date}/{service}/tc3_request"
    str_to_sign = (
        f"{algorithm}\n{ts}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    signing_key = _hmac(
        _hmac(_hmac(("TC3" + secret_key).encode("utf-8"), date), service),
        "tc3_request",
    )
    signature = hmac.new(signing_key, str_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(ts),
        "X-TC-Version": version,
    }
    if region:
        headers["X-TC-Region"] = region
    if token:
        headers["X-TC-Token"] = token

    req = urllib.request.Request(f"https://{host}/", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()).get("Response", {})
    except Exception as e:  # noqa: BLE001
        return {"Error": {"Code": "NetworkError", "Message": str(e)}}


def _assume_role(caller_sid: str, caller_sk: str, role_arn: str,
                 external_id: str = "", duration: int = 3600,
                 session_name: str = "cloudops-resolve") -> dict[str, Any]:
    """sts.AssumeRole with the caller's main-account AK. Returns
    {ok, secret_id, secret_key, token, expired_time} | {ok:False, error}."""
    payload: dict[str, Any] = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
        "DurationSeconds": int(duration) if duration else 3600,
    }
    if external_id:
        payload["ExternalId"] = external_id

    resp = _tc3_call(
        host="sts.tencentcloudapi.com", service="sts", action="AssumeRole",
        version="2018-08-13", payload=payload, region="",
        secret_id=caller_sid, secret_key=caller_sk,
    )
    err = resp.get("Error") if isinstance(resp, dict) else None
    if err:
        return {"ok": False, "error": f"{err.get('Code', 'APIError')}: {err.get('Message', '')}"}

    creds = resp.get("Credentials") or {}
    tmp_sid = str(creds.get("TmpSecretId") or "").strip()
    tmp_sk = str(creds.get("TmpSecretKey") or "").strip()
    token = str(creds.get("Token") or "").strip()
    if not tmp_sid or not tmp_sk or not token:
        return {"ok": False, "error": "AssumeRole returned no TmpSecretId/TmpSecretKey/Token"}

    return {
        "ok": True,
        "secret_id": tmp_sid,
        "secret_key": tmp_sk,
        "token": token,
        "expired_time": resp.get("ExpiredTime"),
    }


def _maybe_assume_cross_account(cloud_lc: str, project: str,
                                payload: dict[str, Any],
                                out: dict[str, str]) -> dict[str, str]:
    """If the secret carries a role_arn, AssumeRole IN-PROCESS and overwrite the
    returned creds with temporary ones. The caller (main-account) AK is sourced from:

      - `assume_from: <PARENT>`  → fetch secret cloudops/{cloud}/{PARENT}, use ITS
        secret_id/secret_key as the caller AK (preferred: main AK stored once);
      - otherwise                → the inline secret_id/secret_key in THIS secret
        (must be the main-account AK able to AssumeRole the target role).

    On success: out[SECRET_ID/KEY] become temp creds, out[TOKEN] is set, and
    ROLE_ARN/EXTERNAL_ID/DURATION are popped so downstream cannot double-assume.
    On failure: sys.exit(6).
    """
    role_arn = (payload.get("role_arn") or "").strip()
    if not role_arn:
        return out  # direct-connect: no cross-account, return creds untouched

    external_id = (payload.get("role_external_id") or "").strip()
    duration = payload.get("role_duration") or 3600
    assume_from = (payload.get("assume_from") or "").strip()

    # Resolve the caller (main-account) AK that signs AssumeRole.
    if assume_from:
        if assume_from in RESERVED_PROJECT_CODES or not PROJECT_CODE_RE.match(assume_from):
            sys.stderr.write(
                f"error: invalid assume_from='{assume_from}' in secret "
                f"cloudops/{cloud_lc}/{project} (must be a valid project code).\n"
            )
            sys.exit(6)
        parent = _ssm.fetch_secret(cloud_lc, assume_from)
        if parent is None:
            sys.stderr.write(
                f"error: assume_from points to 'cloudops/{cloud_lc}/{assume_from}' "
                f"but that parent secret was not found in SSM.\n"
            )
            sys.exit(6)
        caller_sid = str(parent.get("secret_id") or "").strip()
        caller_sk = str(parent.get("secret_key") or "").strip()
        # ExternalId/duration may also be overridden by the parent if not set on the target.
        external_id = external_id or str(parent.get("role_external_id") or "").strip()
        if not external_id:
            external_id = ""
    else:
        caller_sid = out.get("TENCENTCLOUD_SECRET_ID", "").strip()
        caller_sk = out.get("TENCENTCLOUD_SECRET_KEY", "").strip()

    if not caller_sid or not caller_sk:
        sys.stderr.write(
            f"error: secret cloudops/{cloud_lc}/{project} has role_arn but no usable "
            f"main-account AK to AssumeRole with "
            f"({'parent ' + assume_from + ' is missing secret_id/secret_key' if assume_from else 'inline secret_id/secret_key missing'}).\n"
        )
        sys.exit(6)

    sts = _assume_role(caller_sid, caller_sk, role_arn,
                       external_id=external_id, duration=duration)
    if not sts.get("ok"):
        sys.stderr.write(
            f"error: AssumeRole failed for cloudops/{cloud_lc}/{project}: {sts.get('error')}\n"
            f"  Check role_arn, the target role's trust policy (caller main account "
            f"must be trusted), and ExternalId.\n"
        )
        sys.exit(6)

    # Overwrite with temporary creds; set the STS token; strip role fields so no
    # downstream re-assumes (prevents double-assume).
    out["TENCENTCLOUD_SECRET_ID"] = sts["secret_id"]
    out["TENCENTCLOUD_SECRET_KEY"] = sts["secret_key"]
    out["TENCENTCLOUD_TOKEN"] = sts["token"]
    out["TENCENTCLOUD_SECURITY_TOKEN"] = sts["token"]  # SDK-naming compat
    out["CLOUDOPS_CRED_SOURCE"] = "sts_assume_role"
    out.pop("TENCENTCLOUD_ROLE_ARN", None)
    out.pop("TENCENTCLOUD_ROLE_EXTERNAL_ID", None)
    out.pop("TENCENTCLOUD_ROLE_DURATION", None)
    return out


def resolve(cloud: str, project: str) -> dict[str, str]:
    """Return SDK-standard env dict, sourced EXCLUSIVELY from KMS-backed SSM.

    Raise SystemExit on error:
      3  unknown cloud / malformed / reserved project code
      4  secret not found in SSM, or a required field missing inside the payload
      5  SSM not available (mode disabled, runner key absent, or lib import failed)
    """
    cloud_lc = cloud.lower()
    if cloud_lc not in CLOUD_TO_VENDOR:
        sys.stderr.write(f"error: unknown cloud '{cloud}'. Expected: tencent, huawei, aliyun.\n")
        sys.exit(3)

    if project in RESERVED_PROJECT_CODES:
        sys.stderr.write(
            f"error: '{project}' is a reserved code (the SSM runner key). "
            f"It can never be selected as a business account.\n"
        )
        sys.exit(3)

    if not PROJECT_CODE_RE.match(project):
        sys.stderr.write(
            f"error: invalid project code '{project}'. "
            f"Must match ^[A-Z][A-Z0-9]{{1,9}}$ (e.g. YX, TSP, GW) or be 'DEFAULT'.\n"
        )
        sys.exit(3)

    # --- SSM is the ONLY source. No env-prefix path, no bare-name fallback. ---
    if _ssm is None or not _ssm.is_ssm_available():
        sys.stderr.write(
            "error: SSM credential backend is not available.\n"
            "This expert team requires SSM+KMS as the sole credential source. Ensure:\n"
            "  export TENCENTCLOUD_BOOTSTRAP_SECRET_ID=<runner key id>\n"
            "  export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY=<runner key secret>\n"
            "Having the runner key is enough to enable SSM (no CLOUDOPS_SSM_ENABLED needed).\n"
            "If SSM seems off, make sure CLOUDOPS_SSM_ENABLED is NOT set to false/0/no/off.\n"
            "The runner key needs only SSM read perms (GetSecretValue/ListSecrets); "
            "no kms:* permission is required (SSM decrypts server-side).\n"
        )
        sys.exit(5)

    payload = _ssm.fetch_secret(cloud_lc, project)
    if payload is None:
        sys.stderr.write(
            f"error: secret 'cloudops-{cloud_lc}-{project.lower()}-credential' not found in SSM "
            f"(or access denied / malformed).\n"
            f"Fix: create the KMS-encrypted secret in Tencent Cloud SSM (ap-shanghai), then retry.\n"
        )
        sys.exit(4)

    vendor = CLOUD_TO_VENDOR[cloud_lc]
    field_map = VENDOR_MAP[vendor]
    out: dict[str, str] = {}
    missing: list[str] = []

    # In 方案-b cross-account mode the target secret carries only role_arn + assume_from
    # (no inline AK — the caller AK lives in the parent secret). In that case the
    # secret_id/secret_key on THIS secret are not required: they'll be replaced by the
    # AssumeRole temp creds anyway. Detect it so the required-field check won't reject.
    _has_role = bool((payload.get("role_arn") or "").strip())
    _has_assume_from = bool((payload.get("assume_from") or "").strip())
    _ak_optional = vendor == "TENCENTCLOUD" and _has_role and _has_assume_from

    for ssm_key, sdk_name, required in field_map:
        value = payload.get(ssm_key)
        if value is None or value == "":
            if required and not (_ak_optional and ssm_key in ("secret_id", "secret_key")):
                missing.append(f"{ssm_key} (in secret cloudops/{cloud_lc}/{project})")
            continue
        out[sdk_name] = str(value)

    if missing:
        sys.stderr.write(
            f"error: SSM secret for cloud={cloud_lc} project={project} is missing required field(s):\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\nFix: update the secret JSON in SSM and retry.\n"
        )
        sys.exit(4)

    # Cross-account: if role_arn is present, AssumeRole IN-PROCESS and replace creds
    # with temporary ones (sets TENCENTCLOUD_TOKEN, strips ROLE_ARN). No-op otherwise.
    if vendor == "TENCENTCLOUD":
        out = _maybe_assume_cross_account(cloud_lc, project, payload, out)

    # Pin active context for report headers
    out["CLOUDOPS_ACTIVE_CLOUD"] = cloud_lc
    out["CLOUDOPS_ACTIVE_PROJECT"] = project
    return out


def emit_shell(env: dict[str, str]) -> str:
    lines = []
    for k, v in env.items():
        # shlex.quote handles values with spaces, quotes, $ etc.
        lines.append(f"export {k}={shlex.quote(v)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--cloud", required=True, help="tencent | huawei | aliyun")
    ap.add_argument("--project", required=True, help="project code (e.g. YX, TSP, DEFAULT)")
    ap.add_argument(
        "--format", choices=["shell", "json"], default="shell",
        help="output format: shell (default, for `eval`) or json"
    )
    args = ap.parse_args()

    env = resolve(args.cloud, args.project)

    if args.format == "json":
        json.dump(env, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(emit_shell(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
