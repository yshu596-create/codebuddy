"""
腾讯云 TC3-HMAC-SHA256 签名 & 通用请求封装
用法：直接 import 或复制到脚本中使用

示例:
    from sign import call_api
    resp = call_api(
        host="cvm.tencentcloudapi.com", service="cvm",
        action="DescribeInstances", version="2017-03-12",
        payload={"Offset": 0, "Limit": 100},
        region="ap-shanghai",
        secret_id="...", secret_key="..."
    )

凭证模式：
    - 默认 AK/SK 直连（普通单账号巡检/审计），token 留空即可。
    - STS AssumeRole 跨账号代查：已收敛进 cloudops-accounts-pro/scripts/build_credential_env.resolve()
      —— 若 SSM secret 带 role_arn，resolve() 在进程内换好临时凭证 + Token 返回；编排脚本拿到后
      调 set_process_token() 或注入子进程私有 env，下方 token 透传机制即让所有调用点自动带 X-TC-Token。
      本 sign.py 无需改动。
    token 透传（优先级从高到低）：
    1. call_api(..., token="...") 显式传参（最高）
    2. set_process_token("...")  进程局部 Token（推荐：不污染共享 os.environ）
    3. os.environ["TENCENTCLOUD_TOKEN"] / ["TENCENTCLOUD_SECURITY_TOKEN"]
       （仅向后兼容 / 子进程私有 env 注入兜底；SSM-only 主链路不依赖共享 os.environ 传 token）
"""

import os, json, hashlib, hmac, time, datetime, urllib.request, urllib.parse, base64
from datetime import timezone

# 进程局部 Token holder：临时凭证存这里而非共享 os.environ，保证全程零污染。
_PROCESS_TOKEN = ""


def set_process_token(token):
    """注入本进程的默认 STS/SSM Token（不写 os.environ）。传空串可清空。"""
    global _PROCESS_TOKEN
    _PROCESS_TOKEN = (token or "").strip()


def _default_token():
    """读默认 Token：进程局部 holder 优先，再退化读环境变量（向后兼容）。"""
    return (_PROCESS_TOKEN
            or os.environ.get("TENCENTCLOUD_TOKEN", "")
            or os.environ.get("TENCENTCLOUD_SECURITY_TOKEN", "")
            or "").strip()


def sign_and_call(host, service, action, version, payload,
                  region=None, secret_id=None, secret_key=None, token=None):
    """TC3-HMAC-SHA256 签名并发起请求，返回 Response 字段内容。

    token: STS / SSM 临时凭证 Token（AssumeRole 后必填）；普通 AK/SK 直连不传。
    """
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    content_type = "application/json; charset=utf-8"
    payload_bytes = json.dumps(payload).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    credential_scope = f"{date}/{service}/tc3_request"
    str_to_sign = (
        f"{algorithm}\n{timestamp}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def hmac256(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    signing_key = hmac256(
        hmac256(hmac256(("TC3" + secret_key).encode("utf-8"), date), service),
        "tc3_request"
    )
    signature = hmac.new(signing_key, str_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
    }
    if region:
        headers["X-TC-Region"] = region
    # token 优先级：显式传参 > 环境变量（TENCENTCLOUD_TOKEN / TENCENTCLOUD_SECURITY_TOKEN）
    effective_token = (token or "").strip() or _default_token()
    if effective_token:
        headers["X-TC-Token"] = effective_token

    req = urllib.request.Request(
        f"https://{host}/", data=payload_bytes, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()).get("Response", {})
    except Exception as e:
        return {"Error": {"Code": "NetworkError", "Message": str(e)}}


# 简写别名，脚本中常用
call_api = sign_and_call


def get_monitor(namespace, metric, instance_id_name, instance_id_value,
                region, start_time, end_time, period=3600,
                secret_id=None, secret_key=None):
    """
    封装 GetMonitorData 调用，自动使用正确的 Instances 参数格式。

    参数:
        namespace:          如 "QCE/CVM"
        metric:             如 "CPUUsage"
        instance_id_name:   维度名，如 "InstanceId"、"instanceid"、"natId"
        instance_id_value:  实例 ID 值
        region:             如 "ap-shanghai"
        start_time:         "YYYY-MM-DD HH:MM:SS"
        end_time:           "YYYY-MM-DD HH:MM:SS"
        period:             聚合周期（秒），默认 3600
    """
    payload = {
        "Namespace": namespace,
        "MetricName": metric,
        "Instances": [{"Dimensions": [{"Name": instance_id_name, "Value": instance_id_value}]}],
        "StartTime": start_time,
        "EndTime": end_time,
        "Period": period,
    }
    return call_api(
        host="monitor.tencentcloudapi.com", service="monitor",
        action="GetMonitorData", version="2018-07-24",
        payload=payload, region=region,
        secret_id=secret_id, secret_key=secret_key
    )


def avg_values(monitor_resp):
    """从 GetMonitorData 响应中提取均值，返回 (float|None, error_str|None)。"""
    if "Error" in monitor_resp:
        err = monitor_resp["Error"]
        return None, f"{err.get('Code')}: {err.get('Message')}"
    pts = monitor_resp.get("DataPoints", [])
    if not pts:
        return None, "no DataPoints"
    vals = [v for v in pts[0].get("Values", []) if v is not None]
    if not vals:
        return None, "all null values"
    return sum(vals) / len(vals), None


def max_values(monitor_resp):
    """从 GetMonitorData 响应中提取最大值，返回 float|None。"""
    pts = monitor_resp.get("DataPoints", [])
    if not pts:
        return None
    vals = [v for v in pts[0].get("Values", []) if v is not None]
    return max(vals) if vals else None


# ── COS XML API 签名 ──────────────────────────────────────────────
# COS 使用独立的 XML API 签名（HmacSHA1 / HmacSHA256），不走 TC3。
# 提供 cos_get() 封装 GET 请求，覆盖 list_buckets / get_bucket_acl / get_bucket_tagging 等只读操作。

def _cos_authorize(secret_id, secret_key, method, endpoint, path, params,
                    expire_seconds=600):
    """生成 COS XML API 临时签名 Key-Value 对（用于 URL 参数或 Header）。

    签名格式: q-sign-algorithm=sha1& q-ak=...& q-sign-time=...& q-key-time=...& q-header-list=...& q-url-param-list=...& q-signature=...
    """
    timestamp = int(time.time())
    key_time = f"{timestamp};{timestamp + expire_seconds}"
    sign_time = key_time

    # 1) SignKey = HMAC-SHA1(SecretKey, KeyTime)
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()

    # 2) HttpString = Method\nPath\nParams\nHeaders\n
    #    只签 host header（最小必要集）
    sorted_params = sorted(params.items()) if params else []
    param_str = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params)
    param_str_lower = param_str.lower()

    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    header_str = f"host={host.lower()}"
    http_string = f"{method.lower()}\n{path}\n{param_str_lower}\n{header_str}\n"

    # 3) StringToSign = Algorithm\nSignTime\nSHA1(HttpString)\n
    http_string_hash = hashlib.sha1(http_string.encode()).hexdigest()
    string_to_sign = f"sha1\n{sign_time}\n{http_string_hash}\n"

    # 4) Signature = HMAC-SHA1(SignKey, StringToSign)
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()

    auth = (
        f"q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={sign_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list=host"
        f"&q-url-param-list={';'.join(k.lower() for k, _ in sorted_params) if sorted_params else ''}"
        f"&q-signature={signature}"
    )
    return auth


def cos_get(endpoint, path, params=None, secret_id=None, secret_key=None,
            timeout=10, token=None):
    """COS XML API GET 请求封装。

    参数:
        endpoint:   如 "https://cos.ap-shanghai.myqcloud.com"
        path:       如 "/" 或 "/bucket-name"
        params:     dict, 查询参数
        secret_id/secret_key: 凭证
        timeout:    秒
        token:      STS 临时凭证 Token（AssumeRole 跨账号场景必填）；缺省自动从
                    环境变量 TENCENTCLOUD_TOKEN / TENCENTCLOUD_SECURITY_TOKEN 读取。
                    COS 规范：临时凭证须带 x-cos-security-token 头，且该头【不参与】
                    签名计算（与 TC3 的 X-TC-Token 一致），故只加 header 不进 _cos_authorize。

    返回: 解析后的 dict (XML→dict 自动转换)
    """
    import xml.etree.ElementTree as ET

    params = params or {}
    sid = secret_id or os.environ.get("TENCENTCLOUD_SECRET_ID", "")
    skey = secret_key or os.environ.get("TENCENTCLOUD_SECRET_KEY", "")
    tok = (token or "").strip() or _default_token()

    auth = _cos_authorize(sid, skey, "GET", endpoint, path, params)

    query = urllib.parse.urlencode(params) if params else ""
    url = f"{endpoint}{path}"
    if query:
        url += f"?{query}"

    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    headers = {
        "Authorization": auth,
        "Host": host,
    }
    if tok:
        # 临时凭证：COS 用 x-cos-security-token（不参与上面的 q-signature 计算）
        headers["x-cos-security-token"] = tok

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            # XML → dict
            root = ET.fromstring(body)
            return _xml_to_dict(root)
    except Exception as e:
        return {"Error": {"Code": "NetworkError", "Message": str(e)}}


def _xml_to_dict(element):
    """递归将 XML Element 转换为 dict。列表元素用 list 承接同名子节点。"""
    result = {}
    for child in element:
        children = list(child)
        if children:
            child_dict = _xml_to_dict(child)
            # 同名子节点合并为列表
            if child.tag in result:
                existing = result[child.tag]
                if not isinstance(existing, list):
                    result[child.tag] = [existing]
                result[child.tag].append(child_dict)
            else:
                result[child.tag] = child_dict
        else:
            result[child.tag] = child.text or ""
    return result
