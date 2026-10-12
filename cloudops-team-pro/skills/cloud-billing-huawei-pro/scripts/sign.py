"""
Huawei Cloud SDK-HMAC-SHA256 signer (API Gateway Signing v3) and request helper.

This module intentionally follows the canonicalization pattern used by Huawei's
API signing examples:
- canonical URI is normalized, URI-encoded by segment, and ends with "/";
- query string is sorted and URI-encoded with RFC3986 safe chars;
- signed headers include content-type, host, x-sdk-date, plus any extra headers
  such as x-security-token for temporary credentials;
- request body hash is calculated from the exact bytes sent.

The Huawei billing collector mainly uses GET, but the lower-level sign_request()
keeps method/body support so failures can be debugged against other Huawei APIs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

ALGORITHM = "SDK-HMAC-SHA256"


def sha256_hex(data: bytes | str | None) -> str:
    if data is None:
        data = b""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hmac_sha256_hex(key: bytes | str, data: bytes | str) -> str:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def quote(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def normalize_path(path: str) -> str:
    """Normalize path for Huawei canonical request.

    Huawei API Gateway signing expects a canonical URI that starts with "/",
    encodes each path segment, and ends with "/". The actual request URL is not
    mutated here; this value is only used in the string-to-sign.
    """
    normalized = posixpath.normpath(path or "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    encoded = "/".join(quote(part) for part in normalized.split("/"))
    if not encoded.endswith("/"):
        encoded += "/"
    return encoded


def canonical_query(query_items: Iterable[Tuple[str, Any]]) -> str:
    pairs = []
    for key, value in query_items:
        if value is None:
            continue
        pairs.append((quote(key), quote("" if value is None else value)))
    pairs.sort(key=lambda item: (item[0], item[1]))
    return "&".join(f"{key}={value}" for key, value in pairs)


def _normalize_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key, value in headers.items():
        if value is None:
            continue
        normalized[str(key).strip().lower()] = str(value).strip()
    return normalized


def sign_request(
    method: str,
    url: str,
    ak: str,
    sk: str,
    body: bytes | str = b"",
    extra_headers: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], str]:
    """Return signed request headers and the canonical request string.

    The canonical request is returned for local debugging only. Callers must not
    print it in normal expert output because it may reveal request details.
    """
    if not ak or not sk:
        raise ValueError("Huawei Cloud AK/SK are required for request signing")

    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body or b""

    parsed = urllib.parse.urlsplit(url)
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    request_time = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    headers = {
        "content-type": "application/json",
        "host": parsed.netloc,
        "x-sdk-date": request_time,
    }
    headers.update(_normalize_headers(extra_headers or {}))

    sorted_header_names = sorted(headers)
    canonical_headers = "".join(
        f"{name}:{headers[name].strip()}\n" for name in sorted_header_names
    )
    signed_headers = ";".join(sorted_header_names)

    canonical_request = "\n".join(
        [
            method.upper(),
            normalize_path(parsed.path),
            canonical_query(query_items),
            canonical_headers,
            signed_headers,
            sha256_hex(body_bytes),
        ]
    )
    string_to_sign = "\n".join(
        [ALGORITHM, request_time, sha256_hex(canonical_request)]
    )
    signature = hmac_sha256_hex(sk, string_to_sign)
    headers["authorization"] = (
        f"{ALGORITHM} Access={ak}, SignedHeaders={signed_headers}, Signature={signature}"
    )

    # urllib accepts lowercase headers, but title-casing keeps diagnostics readable.
    request_headers = {"-".join(part.capitalize() for part in name.split("-")): value
                       for name, value in headers.items()}
    return request_headers, canonical_request


def _canonical_url(host: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    query = canonical_query((params or {}).items())
    url = f"https://{host}{path}"
    if query:
        url += "?" + query
    return url


def sign_and_call_get(
    host: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    ak: Optional[str] = None,
    sk: Optional[str] = None,
    extra_headers: Optional[Dict[str, Any]] = None,
    security_token: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """Sign and call a Huawei Cloud GET API.

    Returns the parsed JSON response, or {"_error": {...}} on HTTP/network
    errors. `security_token` is for temporary credentials and is included as
    X-Security-Token, which participates in the signature.
    """
    headers_extra = dict(extra_headers or {})
    if security_token:
        headers_extra.setdefault("X-Security-Token", security_token)

    url = _canonical_url(host, path, params or {})
    try:
        headers, _ = sign_request("GET", url, ak or "", sk or "", b"", headers_extra)
    except Exception as e:  # noqa: BLE001
        return {"_error": {"Code": "SignError", "Message": str(e)}}

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"_raw": body}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
        except Exception:
            err_json = {"raw": str(e)}
        return {
            "_error": {
                "Code": "HttpError-%s" % e.code,
                "Message": err_json.get("error_msg")
                or err_json.get("error_code")
                or str(err_json),
                "Raw": err_json,
            }
        }
    except Exception as e:  # noqa: BLE001
        return {"_error": {"Code": "NetworkError", "Message": str(e)}}


# Backward-compatible alias used by older docs/scripts.
call_get = sign_and_call_get


def get_domain_id(
    ak: str,
    sk: str,
    region: str = "cn-north-4",
    security_token: str = "",
) -> Dict[str, Any]:
    """Fetch domain_id/domain_name via IAM listAuthDomains."""
    _ = region  # kept for backward-compatible signature
    resp = sign_and_call_get(
        host="iam.myhuaweicloud.com",
        path="/v3/auth/domains",
        params={},
        ak=ak,
        sk=sk,
        security_token=security_token,
    )
    if "_error" in resp:
        return {"status": "error", "message": resp["_error"].get("Message", "")}
    domains = resp.get("domains") or []
    if not domains:
        return {"status": "error", "message": "no domains in IAM response"}
    d = domains[0]
    return {
        "status": "ok",
        "domain_id": d.get("id"),
        "domain_name": d.get("name"),
    }
