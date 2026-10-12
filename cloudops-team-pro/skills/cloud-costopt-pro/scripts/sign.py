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

STS AssumeRole 路径：调用方先把临时凭证 + Token 通过下面三种方式之一传递，
无需逐个调用点改签名（优先级从高到低）：
    1. call_api(..., token="...") 显式传参（最高优先级）
    2. set_process_token("...")  进程局部 Token（推荐：不污染共享 os.environ，
       并发 fan-out 下各进程互不干扰；STS/SSM 临时凭证场景的标准注入口）
    3. os.environ["TENCENTCLOUD_TOKEN"] / ["TENCENTCLOUD_SECURITY_TOKEN"]（向后兼容兜底）
"""

import os, json, hashlib, hmac, time, datetime, urllib.request
from datetime import timezone

# 进程局部 Token holder：STS 临时凭证存这里而非共享 os.environ，
# 保证「全程零污染」契约——同一父进程顺序处理多账户也绝不互相覆盖。
_PROCESS_TOKEN = ""


def set_process_token(token):
    """注入本进程的默认 STS Token（不写 os.environ）。传空串可清空。"""
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

    token: STS 临时凭证 Token（路径 2 AssumeRole 后必填）；普通 AK/SK 直连不传。
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
