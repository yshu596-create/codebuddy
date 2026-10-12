# 11 合规与资产标签巡检

> SOP: TC-SEC-SOP-001 | 章节 11  
> 操作路径：标签 > 资源标签 → https://console.cloud.tencent.com/tag

## 11.1 资源标签合规(billing 维度,2026-05-18 B 方案)

**风险等级:🔴 严重 / 🟠 高**(按缺失 key 分级,见下方判定规则)

### 必需标签 + 判定规则(2026-05-18 更新)

| 标签 Key | 说明 | 强制性 |
|---|---|---|
| `head` | 业务负责人(RTX 账号本名,后续告警闭环靠它定位人) | **必填** |
| `business` | 业务标识 | **business OR customer 二选一必填** |
| `customer` | 客户标识 | **business OR customer 二选一必填** |

**判定逻辑**(资源不合规当满足**任一**条件):

- 🔴 `head` 缺失或为空 → 严重(无法追责)
- 🟠 `business` 和 `customer` **同时**缺失或都为空 → 高(无法定位业务归属)

> 二者皆缺 → 同时 🔴 + 🟠,报告里在同一条目里标两个 missing_keys。

### 推荐标签(不纳入硬扫)

| 标签 Key | 说明 | 示例值 |
|---|---|---|
| `env` | 环境 | `prod`、`test`、`dev` |
| `cost-center` | 成本中心 | `BG-001` |
| `owner-backup` | 备份负责人 | `janedoe` |

### 数据源:billing API(不再扫全量资源)

**为什么改走 billing 维度**(2026-05-18 决策):

- 上一版 Phase 1+2 走"扫全量资源 + 读 Tags 字段"路线,需为每个产品在 `inspect_scan.py` SPECS 加行 + `inspect_resource_tags.py` TAG_SPEC 加行,**每加一个产品要写代码**,与极简主义原则相悖。
- 腾讯云 billing 提供 `DescribeBillResourceSummary`,按 `TagKey` + `TagValue=""` 直接返回**未设置该标签**的资源清单,**自动覆盖所有计费产品**,新上线产品零改动。
- 缺点:**只覆盖上月有消费的资源**;无消费的空闲/赠送额度内资源不在范围。FinOps 视角下零费用资源权重本身就低,可接受。

### 自动化执行(纯 SKILL.md prompt 驱动,无 Python 脚本)

LLM 在 Step 2.6 阶段判断:用户 prompt 含「合规」「标签」「上月」字眼时触发,直接执行下面 §11.1.5 Bash 块,产出落 `$OUT_DIR/inspect_resource_tags.json`。

### §11.1.5 完整执行块(LLM 1 条 exec,`timeout: 300`)

> **2026-05-18 B 方案前提**:Phase 1 / Phase 2 全部下线。本步骤**不调用任何 Python 脚本**,LLM 直接用 sign.py 直签 HTTP 拉 billing API。Step 1 / Step 2 跑完才能开始(依赖 `inspect_meta.json`)。
>
> 触发条件:Step 0 已加载凭证 + `$OUT_DIR/inspect_meta.json` 存在,用户没有显式 `--skip-tags` 时默认跑;月份默认 = **上月**(YYYY-MM,例如今天是 2026-05-18 就用 `2026-04`)。当月数据可能不完整,只有用户明确要"扫本月"才用当前月。

**API 契约**:
- 接口:`billing.tencentcloudapi.com / DescribeBillResourceSummary`(2018-07-09)
- 关键参数: `Month`(必填,YYYY-MM) / `Offset` / `Limit`(必填,Limit ≤ 1000) / `TagKey`(可选)+ `TagValue`(可选,**空字符串 = "该 TagKey 下未设置标签值的记录"**,腾讯云官方语义)
- 返回字段(每条 `ResourceSummarySet` 元素):`ResourceId / ResourceName / BusinessCode / BusinessCodeName / ProductCode / ProductCodeName / RegionId / RegionName / Tags / TotalCost / RealTotalCost / PayModeName / ...`
- 数据起点:2021-01,跨月数据无问题

```bash
python3 <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.', 'scripts/lib'))
from sign import call_api
from concurrent.futures import ThreadPoolExecutor

SID = os.environ['TENCENTCLOUD_SECRET_ID']
SKEY = os.environ['TENCENTCLOUD_SECRET_KEY']
OUT = os.environ['OUT_DIR']
# 上月,固定取调用日的上月
import datetime
today = datetime.date.today()
first_of_this_month = today.replace(day=1)
last_month = (first_of_this_month - datetime.timedelta(days=1)).strftime('%Y-%m')
MONTH = os.environ.get('TAG_SCAN_MONTH', last_month)

REQUIRED_KEYS = ['head', 'business', 'customer']
errors = []

def pull_one_key(tag_key):
    """拉 TagKey=<key> 且 TagValue='' 的全部资源(分页直到拉空)"""
    out = []
    offset = 0
    while True:
        try:
            r = call_api(
                host='billing.tencentcloudapi.com', service='billing',
                action='DescribeBillResourceSummary', version='2018-07-09',
                payload={'Month': MONTH, 'Offset': offset, 'Limit': 1000,
                         'TagKey': tag_key, 'TagValue': ''},
                region='ap-shanghai',
                secret_id=SID, secret_key=SKEY)
        except Exception as e:
            errors.append({'tag_key': tag_key, 'offset': offset, 'error': str(e)[:200]})
            return out, 'error'
        batch = r.get('ResourceSummarySet', [])
        out.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return out, 'ok'

# 3 个 key 并发拉
status_per_key = {}
raw_per_key = {}
with ThreadPoolExecutor(max_workers=3) as ex:
    futures = {ex.submit(pull_one_key, k): k for k in REQUIRED_KEYS}
    for f in futures:
        k = futures[f]
        raw_per_key[k], status_per_key[k] = f.result()

# scan_status 三态:全 ok → ok;部分失败 → partial;全失败 → denied/error
ok_cnt = sum(1 for s in status_per_key.values() if s == 'ok')
if ok_cnt == 3:
    scan_status = 'ok'
elif ok_cnt == 0:
    scan_status = 'denied'  # 通常是子账号无 finance:DescribeBillResourceSummary
else:
    scan_status = 'partial'

# 合并去重:同 ResourceId 多次出现 → 累加 missing_keys
by_rid = {}
for tk, items in raw_per_key.items():
    for it in items:
        rid = it.get('ResourceId') or f"__noid_{tk}_{it.get('ResourceName','')}"
        if rid not in by_rid:
            by_rid[rid] = {
                'biz_code': it.get('BusinessCode', ''),
                'biz_name': it.get('BusinessCodeName', ''),
                'product_code': it.get('ProductCode', ''),
                'product_name': it.get('ProductCodeName', ''),
                'region': it.get('RegionName') or it.get('RegionId') or '',
                'resource_id': rid,
                'resource_name': it.get('ResourceName', ''),
                # 用 RealTotalCost(实际支付) 比 TotalCost(原价) 更接近真实成本
                'total_cost': float(it.get('RealTotalCost') or it.get('TotalCost') or 0),
                'missing_keys': [],
            }
        if tk not in by_rid[rid]['missing_keys']:
            by_rid[rid]['missing_keys'].append(tk)

untagged_details = sorted(by_rid.values(),
                          key=lambda x: (-x['total_cost'], x['product_code'], x['resource_id']))

# ⚠️ 关键过滤步骤:对 3 个 TagKey 各自的 untagged 列表取并集得到 by_rid 后,
# 必须按真实合规规则 `head AND (business OR customer)` 过滤,只保留真不合规的
# 资源。否则会把"只缺 business 但有 customer"这种实际合规的资源也算进来
# (一中心 2026-04 实测:未过滤 340 个,过滤后只有 48 个真不合规)。
def is_noncompliant(missing):
    mk = set(missing)
    return ('head' in mk) or ('business' in mk and 'customer' in mk)

# 同时把每个真不合规资源的 verdict 写出来便于下游报告分级
def verdict(missing):
    mk = set(missing)
    has_head = 'head' in mk
    bc_both = 'business' in mk and 'customer' in mk
    if has_head and bc_both: return '🔴🟠'  # 同时违反两条
    if has_head: return '🔴'                  # head 缺 → 严重
    return '🟠'                               # business+customer 同时缺 → 高

untagged_details = [
    {**d, 'verdict': verdict(d['missing_keys'])}
    for d in untagged_details if is_noncompliant(d['missing_keys'])
]

by_product = {}
for d in untagged_details:
    bc = d['biz_code'] or '__unknown__'
    if bc not in by_product:
        by_product[bc] = {'biz_name': d['biz_name'], 'count': 0, 'cost_total': 0.0}
    by_product[bc]['count'] += 1
    by_product[bc]['cost_total'] = round(by_product[bc]['cost_total'] + d['total_cost'], 4)

result = {
    'required_logic': 'head AND (business OR customer)',
    'month': MONTH,
    'scan_status': scan_status,
    'total_untagged_resources': len(untagged_details),
    'by_product': by_product,
    'untagged_details': untagged_details,
    'errors': errors,
}

with open(os.path.join(OUT, 'inspect_resource_tags.json'), 'w') as f:
    json.dump(result, f, ensure_ascii=False)

# stdout summary(回 LLM 只看这段)
print(json.dumps({
    'month': MONTH,
    'scan_status': scan_status,
    'total_untagged': len(untagged_details),
    'by_key_counts': {k: len(v) for k, v in raw_per_key.items()},
    'top_biz': sorted([(bc, v['count']) for bc, v in by_product.items()],
                      key=lambda x: -x[1])[:5],
    'errors': len(errors),
}, ensure_ascii=False))
PY
```

### §11.1.6 产出 schema(写入 `$OUT_DIR/inspect_resource_tags.json`)

```json
{
  "required_logic": "head AND (business OR customer)",
  "month": "2026-04",
  "scan_status": "ok",
  "total_untagged_resources": 47,
  "by_product": {
    "p_cvm":   {"biz_name": "云服务器 CVM", "count": 12, "cost_total": 3421.5},
    "p_cdb":   {"biz_name": "云数据库 MySQL", "count": 5, "cost_total": 890.2}
  },
  "untagged_details": [
    {"biz_code":"p_cvm","biz_name":"云服务器 CVM","product_code":"sp_cvm_s5","product_name":"标准型S5",
     "region":"广州","resource_id":"ins-xxxxxxxx","resource_name":"prd-web-1",
     "total_cost": 1024.5,"missing_keys":["head","business","customer"],"verdict":"🔴🟠"}
  ],
  "errors": []
}
```

### §11.1.7 `scan_status` 三态语义

| 值 | 含义 | 下游报告处理 |
|---|---|---|
| `ok` | 3 个 TagKey 全部拉取成功 | 正常出 §11 报告 |
| `partial` | 至少 1 个 TagKey 失败(errors 列详情) | 出报告 + §四扫描降级说明列出失败的 TagKey |
| `denied` | 3 个 TagKey 全部失败 | §11 行写"权限不足(billing:DescribeBillResourceSummary),需主账号本人或加 finance 只读后复跑" |

### §11.1.8 数据源边界(报告里**必须**写明)

- **只覆盖上月有消费的资源**。空闲/包年未起算/赠送额度内/CWP 主机维度等不在 billing 出账的资源**看不见**,不在本节范围
- 这是 B 方案的根本限制(权衡了"速度快 + 自动覆盖所有计费产品 + 带费用权重"换"看不见无消费资源"),不是 bug
- 实例已删但本月有消费的:照样在范围内(用户能从月度账单上看到 = 应该有 owner)

### §11.1.9 ⚠️ billing API 软限流(2026-05-18 实测)

- 短时间内对同 TagKey + TagValue="" 反复调用,billing 服务端会**静默返空** (`ResourceSummarySet=[]`,不抛 Error,`scan_status` 仍为 `ok`),冷却 ~10s 后恢复
- 实测一中心 2026-04 三连发:RUN-1 head=92/business=596/customer=65;RUN-2 business 变 0;RUN-3 三个 key 全 0;sleep 10s 后 business 恢复 596
- **防御**:每轮巡检 Step 2.6 **只跑一次**,LLM 不要"看着数据反常自己重跑"——重跑反而打到限流空响应
- 报告侧:如 `total_untagged_resources` 较上次断崖式下降(>80%),**优先怀疑限流而不是"用户突然打全标签"**,等 1 分钟再人工复跑确认

### 不在覆盖范围(显式声明)

- **无消费资源**:空闲 CVM/未起算包年/赠送额度内 → billing 不返回,本扫描看不见
- **CWP 主机维度**:主机不是租户资源单元,标签语义不适用
- **跨月长期资产**:某些包年包月跨月不出账的资源月份内可能漏

---

## 11.2 闲置资源清理

| 检查项 | 操作方法 | 判定标准 | 风险等级 |
|--------|---------|---------|---------|
| 闲置 CVM | CVM > 监控 | CPU < 5% 超 **30 天** | 🟡 中 |
| 闲置 DB | DB > 监控 | 连接数 = 0 超 **30 天** | 🟡 中 |
| 闲置 CLB | CLB > 监控 | 无流量超 **30 天** | 🟡 中 |
| 闲置 EIP | EIP > 绑定 | 未绑定实例 | 🟡 中 |
| 过期快照 | CBS > 快照 | 超保留策略 | 🟡 中 |
| 闲置 COS 桶 | COS > 存储桶 | 无对象且无访问超 30 天 | 🟡 中 |

### 巡检步骤

1. 通过云监控导出各资源近 30 天利用率数据
2. 筛选 CPU < 5% 的 CVM 实例，联系业务确认是否可下线
3. 检查数据库连接数为 0 的实例，评估是否可释放
4. 清理未绑定实例的 EIP（避免浪费和潜在暴露面）
5. 清理过期快照，降低存储成本

---

## 11.3 数据合规

| 检查项 | 操作方法 | 判定标准 | 风险等级 |
|--------|---------|---------|---------|
| 数据地域 | 各产品 > 地域 | 符合数据出境规定 | 🟠 高 |
| 敏感标识 | 数据安全 > 敏感数据 | 应有标识和分级 | 🟠 高 |
| 生命周期 | 数据管理 | 有保留和销毁策略 | 🟡 中 |
| 隐私合规 | 个人信息保护 | 符合《个人信息保护法》要求 | 🟠 高 |
| 数据分类 | 数据安全 | 完成数据分类分级 | 🟠 高 |

### 巡检步骤

1. 核查存储个人信息的资源（COS/数据库）所在地域，确认符合数据出境规定
2. 完成数据分类分级工作，对核心敏感数据（身份证/银行卡/手机号）做特殊标记
3. 确认各类数据有明确的保留和销毁策略（如日志 90 天、用户行为数据 1 年）
4. 对照《个人信息保护法》要求，检查用户授权、数据最小化等合规项
