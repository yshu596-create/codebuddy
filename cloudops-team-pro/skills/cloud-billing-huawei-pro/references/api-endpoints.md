# 华为云 BSS API 速查

## 通用约定

- 协议：HTTPS
- 方法：GET（账单查询接口全部为 GET）
- 域名：
  - 中国站（默认）：`bss.myhuaweicloud.com`
  - 国际站：`bss-intl.myhuaweicloud.com`（通过 SSM SecretString 的 `bss_endpoint` 切换）
- 鉴权：SDK-HMAC-SHA256（参考 `scripts/sign.py`）；临时凭证必须发送 `X-Security-Token` 且纳入 SignedHeaders
- 子账号 AK/SK 推荐授予 `BSS Administrator`（只读）+ `IAM ReadOnlyAccess`

## 1. IAM ListAuthDomains（派生 domain_id）

- Host: `iam.myhuaweicloud.com`
- 方法 + 路径：`GET /v3/auth/domains`
- 参数：无
- 返回（节选）：

```json
{
  "domains": [
    {"id": "08abc...", "name": "fangxing-huawei", "enabled": true}
  ]
}
```

`domains[0].id` 即 domain_id，对应华为云"账号 ID"概念，等价于腾讯云的 UIN。

## 2. ShowCustomerMonthlySum（按云服务类型汇总）

- Host: `bss.myhuaweicloud.com`
- 方法 + 路径：`GET /v2/bills/customer-bills/monthly-sum`
- 关键参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `bill_cycle` | ✅ | 账期，格式 `YYYY-MM`（东八区） |
| `service_type_code` | ❌ | 云服务类型编码，如 `hws.service.type.ec2` |
| `enterprise_project_id` | ❌ | 企业项目 ID（`0` 默认项目，`-1` 未归集） |
| `method` | ❌ | `oneself` / `sub_customer` / `all`，默认 `all` |
| `offset` / `limit` | ❌ | 分页，limit 1-1000，默认 10（脚本里默认 1000） |

- 返回（节选）：

```json
{
  "currency": "CNY",
  "total_count": 12,
  "bill_sums": [
    {
      "customer_id": "08abc...",
      "cloud_service_type_code": "hws.service.type.ec2",
      "cloud_service_type_name": "弹性云服务器",
      "resource_type_code": null,
      "consume_amount": 12000.5,
      "total_amount": 12000.5,
      "measure_id": 1
    }
  ]
}
```

注：`measure_id`：1 = 元/美元；3 = 分/美分。脚本目前按 `measure_id == 1` 处理，若实际为 3 需要除以 100（待补充）。

## 3. ListCustomerselfResourceRecords（资源消费记录）

- Host: `bss.myhuaweicloud.com`
- 方法 + 路径：`GET /v2/bills/customer-bills/res-fee-records`
- 关键参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `cycle` | ✅ | 账期，`YYYY-MM` |
| `charge_mode` | ❌ | 计费模式：1=包年/包月，3=按需，10=预留实例，11=节省计划 |
| `cloud_service_type` | ❌ | 云服务类型编码 |
| `region` | ❌ | 地域编码，如 `cn-north-4` |
| `bill_type` | ❌ | 账单类型（1=新购，3=变更，5=按时计费，等等） |
| `enterprise_project_id` | ❌ | 企业项目 |
| `resource_id` | ❌ | 资源 ID 精确过滤 |
| `method` | ❌ | 同上，默认 `all` |
| `offset` / `limit` | ❌ | 分页，limit 1-1000 |

- 返回核心字段：`resource_id` / `resource_name` / `cloud_service_type_code` / `cloud_service_type_name` / `region` / `enterprise_project_id` / `consume_amount` / `amount`。

## 常见错误码

| HTTP | error_code | 含义 | 应对 |
|---|---|---|---|
| 401 | APIGW.0301 | 鉴权失败（AK/SK 错或时间偏差） | 校验 AK/SK；同步系统时间 |
| 403 | CBC.0150 | 没有 BSS 权限 | 给账号授 `BSS Administrator` |
| 400 | CBC.99006005 | bill_cycle 格式错误 | 必须 `YYYY-MM`，且不超过 3 年 |
| 429 | APIGW.0308 | 限流 | 退避重试，并发收敛到 ≤ 5 |

## 国际站差异

- 域名：`bss-intl.myhuaweicloud.com`
- 默认币种：USD
- 部分接口响应字段命名相同；金额展示符号需切到 `$`

## 参考链接

- [华为云 BSS API 总览](https://support.huaweicloud.com/api-oce/)
- [SDK-HMAC-SHA256 签名规范](https://support.huaweicloud.com/devg-apisign/api-sign-algorithm.html)
- [华为云 SDK Python（huaweicloudsdkbss）](https://github.com/huaweicloud/huaweicloud-sdk-python-v3)
