# 14 智能顾问 advisor 数据覆盖映射

> 数据源:`scripts/inspect_advisor.py` 产出的 `inspect_advisor.json` + `inspect_advisor_strategies.json`。
> 路由表与覆盖数据基于 2026-05-20 三账户(一中心管控/营销/TSP)实测,DescribeStrategies 返回 450 strategy / 73 product / 5 GroupName,**跨账户元数据零差异**。

## 0. 数据源说明

- **DescribeStrategies** (advisor.tencentcloudapi.com, version=2020-07-21)
  - 一次性返回全 450 strategy 元数据,无分页,~1-2s
  - 字段:`StrategyId / Name / Desc / Product / ProductDesc / GroupId / GroupName / Repair (markdown) / Conditions:[{ConditionId, Level, LevelDesc, Desc}]`
  - **没有顶层 `RepairUrl` 字段** — URL 嵌在 `Repair` markdown 文本里(形如 `[创建账号](https://cloud.tencent.com/document/product/xxx)`),`inspect_advisor.py` 用正则抽第一个 `[文档](url)` 作为 `repair_url`
- **DescribeTaskStrategyRisks(StrategyId)**
  - 单 strategy 命中 Risks + ResourceCount + RiskTotalCount
  - 单次 ~0.5-1s,8 路并发 ~30s 拉完 450
  - **`Risks` 字段是 JSON-string-encoded**,必须 `json.loads` 二次解码;**`Level` 是 string '3' 不是 int 3**,统一转 int
- **跨账户零差异**:三账户 strategies.json 等同 → strategies.json 可以单文件全租户共享(本轮存 `$OUT_DIR/inspect_advisor_strategies.json`,不做 `shared/cache/advisor/`)
- **子账号身份**:advisor 三账户子账号(ReadOnly 策略组)全通,无需 caller_identity 三态降级

## 1. SOP 域 × advisor 覆盖映射(2026-05-20 实测)

| SOP 域 | advisor strategy 数 | 数据走向 | 代表 strategy_id |
|---|---|---|---|
| §01 IAM/CAM | 1 | **本地硬扫主**(advisor 覆盖几乎为 0,inspect_cam.py 才是源)| 128(CAM 账号保护未开启)|
| §02 网络与安全组 | 74 | **双源**(本地硬扫 VPC/SG + advisor 补 WAF/CFW/EIP/NAT/VPN/DC/CCN)| 170(SG 全开),171(网络 ACL 暴露),1064(WAF 弹性计费),4068/4069(WAF 拦截/开关),300145(CFW 未开启)|
| §03 CVM/CWP | 34 | **双源**(本地硬扫 CVM/CWP/CBS + advisor 补 CWP 客户端/快照/隐患机器)| 89(磁盘高使用率),90(本地盘风险),140(CBS 未快照),300120(CWP 客户端未装),300225(CVM 系统盘未快照),300226(CVM 隐患机器)|
| §04 COS | 18 | **advisor 主**(advisor 比 inspect_cos.py 覆盖广,本地只做 ACL/Lifecycle 兜底)| 19(子账号无限制),42(公有读),58(无 CORS),119(无 Referer),300202(Policy 过大)|
| §05 数据库 | 90 | **advisor 主(强项)** — MySQL/Redis/PostgreSQL/MongoDB/MariaDB/TDSQL/DCDB/CynosDB/SQLServer 一站搞定 | 3(MySQL root 安全),4(MySQL 高危命令),24(Redis 高危命令),67(MySQL 公网策略),4004(TDSQL 高危命令)|
| §06 CLB/CDN | 73 | **advisor 主** — CLB/CDN/EdgeOne/Live 边缘产品 | 94(CDN IP 限频),5000(CDN 无 HTTPS),5001(CDN 无 URL 鉴权),7100(EdgeOne 无 HTTPS),30078(EO 兼容 TLS1.0/1.1)|
| §07 TKE | 32 | **advisor 主** — TKE 安全/集群可靠性/性能 | 7501(TKE 公网 SG 不合理),7603(TKE 匿名访问),300136-300140(级联删除系列 5 个),7515(节点负载过高)|
| §08 中间件 | 123 | **advisor 主** — ES/CKafka/CMQ/TDMQ/RocketMQ/EMR/TCHouse/RabbitMQ/SCF/TSF/APIGW/VOD 等 | 5270(ES 集群未开 opack),300200(EMR 外网强制开),300210(TCHouse-C CK default 无密码),4066(CKafka Topic 单副本),5190(ES Lucene 子句过多)|
| §09 KMS/AK | 0 | **本地硬扫(护城河)** | — |
| §10 CloudAudit 事件流 | 0 | **本地硬扫(护城河)** — `inspect_audit_events.py` 是唯一源 | — |
| §11 标签合规 | 0 | **本地硬扫(护城河)** — `inspect_scan.py` Tags 字段已落盘 | — |
| §12 SSL 到期 | 1 | **双源**(本地硬扫主 — 有到期天数;advisor 仅 1 个粗粒度,补漏)| 1000(SSL 过期风险)|
| §17 基线/网络/安全产品/合规 | 0(配置层) | **本地硬扫(inspect_infra.py)** — 组织/联系人/消息订阅/VPC+ACL+NAT+EIP+堡垒机/WAF+DDoS+CFW/配置审计合规率 | — |

**合计**:advisor 覆盖 7 个 SOP 域(§02/§03/§04/§05/§06/§07/§08/§12 + 微弱 §01),共 446 strategy 走 advisor 数据通路;4 个 strategy(RocketMQ 系列 Product 字段空)落 §08 兜底,1 个 §12 SSL 落双源,1 个 §01 CAM 落本地硬扫。§17 基线/网络/安全产品/合规由 inspect_infra.py 本地硬扫,advisor 不覆盖配置层。

## 2. 严重度判定(脚本不判,LLM 在 Step 4 按下表)

| advisor `Level` | `RiskDays` | 严重度 | 含义 |
|---|---|---|---|
| **3**(高)| 任意 | 🔴 严重 | LevelDesc=高风险,advisor 已标红 → SOP 🔴(≤2h)|
| **2**(中)| > 30 | 🟠 高 | 中风险存量超月 → 升级 🟠(≤24h)|
| **2**(中)| ≤ 30 | 🟡 中 | 中风险新增 → SOP 🟡(≤7d)|
| **1**(低)| 任意 | 🟢 提示 | 提示性 strategy,不进 §三处置清单 |
| **0**(未知)| — | — | 极少见,按本地硬扫严重度兜底 |

**HARD**:严重度判定必须按此表,不得 LLM 即兴写;`level_max` 是 finding 内所有 risks.level 的最大值,LLM 用 `finding.level_max` 而非主表逐条 level 推算。

## 3. cloudq 7 类能力 → advisor 替代映射

| cloudq 能力 | advisor 替代方式 | inspector 落点 |
|---|---|---|
| **巡检** | 全 450 strategy 覆盖 73 product / 5 GroupName | §一概览矩阵 + §二全章 |
| **安全合规** | GroupName=安全(66 strategy)+ 安全相关 Conditions | §02-§08 主表 + advisor 子表格 |
| **处置方案** | `strategy.Repair` (markdown) + `repair_url` (从 Repair 抽出来) | §三立即处置清单条目末尾 `处置参考: [文档](url)` |
| **最佳实践** | GroupName=可靠(173)+ 性能(107) | §02-§08 advisor 子表格 |
| **AI 智能巡检** | 全 450 strategy 自动检测,无需用户指定 | inspector Step 1b 自动跑 |
| **主动预警** | level=3 的 finding | §一 🔴 段 + §三处置清单(level=3 必入)|
| **架构健康度** | `level_max` + `risk_total` 跨域加权 | §一概览矩阵摘要行 + 概览说明 |

## 4. cloudq 永久不替代项(选项 C 归零)

| cloudq 能力 | 处理方式 |
|---|---|
| **多云对比** | 永久归零(advisor 仅腾讯云)|
| **架构图生成** | 答复"暂不支持,可用控制台'网络扫描自动生图'功能",不模拟 |
| **闲置资源/成本能力** | 归 costopt skill(后续 TODO)— 用 advisor `GroupName=成本`(22 strategy)+ 本地监控指标 |
| **资源查询** | 归 billing skill(后续 TODO)— 用 DescribeResourceRedis/CVM 等独立接口 |
| **DescribeUserAuthorizationStatus** | 跟 cloudq skill 一起删,inspector/costopt/billing 均不实现 |

## 5. inspect_advisor.json 主字段说明

```
{
  scan_status: 'ok' | 'partial' | 'denied',
  total_strategies: 450,
  total_risks: int,
  total_resources: int,
  by_group: {安全: {checked, hit, risks_total}, 可靠: ..., ...},
  level3_count: int,
  repair_url_coverage: float (0-1, 实测 0.88-0.90),
  findings: [
    {
      strategy_id: int,
      strategy_name: str (← strategy.Name),
      product: str (← strategy.ProductDesc, 中文),
      group: str (← strategy.GroupName, 5 选 1),
      type: '' (advisor 元数据无独立 Type,LLM 用 group 路由),
      level_max: 0|1|2|3,
      risk_total: int (实例命中数),
      resource_total: int (扫描总数,DescribeTaskStrategyRisks.ResourceCount),
      repair_url: str (从 Repair markdown 抽的第一个 [文档](url),可能为空 ~10%),
      repair: str (Repair markdown 原文),
      risks: [
        {
          instance_id, instance_name, region, zone,
          level: int (已 string→int 转换),
          risk_days: int,
          tags: [{Key,Value}, ...] (原样保留),
          vip, vport, status, ignore_type, pri_id, condition_id
        }, ...
      ]
    }, ...
  ],
  errors: [{kind: 'fetch_risks' | 'fetch_strategies', strategy_id, strategy_name, error}, ...]
}
```

## 6. inspect_advisor_strategies.json 主字段说明

```
[
  {
    StrategyId: int,
    Name: str,
    Desc: str,
    Ignore: str (% 实例被忽略 的模板),
    Notice: str (% 实例存在 xx 风险 的模板),
    SubProduct: str,
    Product: str (产品 key,如 'mysql','cos'),
    ProductDesc: str (产品中文名),
    Repair: str (markdown,含修复链接),
    GroupId: int,
    GroupName: '安全'|'可靠'|'性能'|'成本'|'服务限制',
    IsSupportCustom: bool,
    Conditions: [{ConditionId, Level: int, LevelDesc: str, Desc: str}]
  }, ...
]
```

LLM 在 Step 4 反查时:
- 用 `StrategyId` 关联到 finding
- 用 `Name`/`ProductDesc` 写报告显示名
- 用 `Conditions[].LevelDesc` 取风险等级中文(高风险/中风险/低风险)
- `Repair` 字段如果 `repair_url` 为空且需要显示修复指引,可以从这里渲染前 ~200 字符

---

## 7. advisor 数据源使用硬骨架(LLM Step 4 渲染指引)

> ⚠️ **F34 全局原则**:本节是 LLM 内部技术指南,字段名/路径/脚本名保留 advisor。**对外输出**(报告归档 .md / HTML 报告)**严禁出现** `advisor` / `智能顾问` / `strategy` / `costopt` 字眼,详见 `references/13-report-template.md` HARD #10。

### 7.1 云端策略主源域(§02 网络与安全组 / §03 CVM/CWP / §04 COS / §05 数据库 / §06 CLB/CDN / §07 TKE / §08 中间件 / §12 SSL)

2026-05-21 起 **所有资源类数据**由云端策略单源覆盖,自扫不再做资源类发现/统计:

- LLM **直接基于** `inspect_advisor.json/findings` 写各域主表 — 覆盖广(450 strategy / 73 product),命中粒度细(单实例 + RiskDays + Tags + Vip:Vport)
- 风险描述列填 `{strategy_name}(持续 {risk_days} 天)`,**列标题用「风险描述」不要用「策略名」**
- 资源总数(如"共 20 个 CVM"):从 `findings[i].resource_total` 同产品取**最大值**(`max(f.resource_total for f in findings if f.product == '云服务器（CVM）')`),不要 sum/重复计

### 7.2 自扫补丁域(云端策略无同等粒度,§02/§03/§12 双源,§01/§08/§10/§11 自扫独有)

- **§02 SG 端口聚合**(`inspect_sg_rules.json`):云端策略 strategy 170 只给"开放公网/内网"1 个粗粒度,自扫给 4 类分桶(全端口/高危端口/空规则/规则级明细);两者按 instance_id 取并集,**自扫覆盖度更细优先**
- **§02 VPC 跨 UIN**(`inspect_vpc_cross.json`):云端策略只看本账号 VPC,自扫独家
- **§03 CWP 暴破成功明细**(`inspect_cwp.json`):云端策略只标"未防护",自扫给出 root@IP/SUCCESS 真入侵证据;`brute_success > 0` 直接 🔴 严重
- **§04 COS ACL 桶清单**(`inspect_cos.json`):云端策略给数字不给桶名,自扫独家给桶名 + 地域 + public_type
- **§10 CloudAudit 跟踪集**(`inspect_cloudaudit.json`):云端策略不审,自扫独家
- **§10 CloudAudit 事件流**(`inspect_audit_events.json`,**§10/§12 核心**):云端策略不审,自扫独家
- **§11 资源标签合规**(`inspect_resource_tags.json`):云端策略不知道公司内规 `head + (business OR customer)`,自扫独家
- **§12 SSL 过期分级**(`inspect_ssl.json`):云端策略 strategy 1000 只 1 个粗粒度,自扫给 days_to_expire 分级(已过期 / 30d 内到期)
- **§17 基线/网络/安全产品/合规**(`inspect_infra.json`):云端策略不审配置层(组织归属/联系人/消息订阅/VPC+ACL+NAT+EIP/堡垒机/WAF+DDoS+CFW/配置审计合规率),自扫独家。**数据库审计**:advisor 已有 MySQL/Redis/PostgreSQL 等 90 个 strategy 部分涉及审计配置,LLM 在 Step 4 从 `inspect_advisor_digest.json` 的数据库 finding 中查找审计关键词推断审计状态,不需要额外调 API

### 7.3 护城河域(§01 IAM/CAM / §09 KMS/AK)

- **不读** `inspect_advisor.json`,完全用 `inspect_cam.json`
- §一概览矩阵**禁止**追加任何来源标签(F34,原 `[本地]` 标签 2026-05-21 撤销)
- §01 CAM 例外:云端策略仅 1 个命中(128 账号保护未开启),但 inspect_cam.py 已覆盖等价检查,云端策略不进 §01 主表,LLM 不要混

## 8. scan_status 三态降级

| scan_status | LLM 处理 |
|---|---|
| `ok` | 正常基于 findings 渲染各域主表 + repair_url 链接;§一矩阵摘要**不加任何来源标签**(F34) |
| `partial` | 渲染能拉到的 finding,在 §五降级说明节列一行 `⚠️ 云端策略巡检部分失败({errors_count} 项)`;主表正常出,errors 详情附在 §五 |
| `denied` | §02-§08 各域主表**降级到只展示自扫覆盖的有限维度**(SG 端口/CWP 暴破/SSL 过期/CAM/COS ACL/审计事件流/标签合规),各域末尾追加一行 `⚠️ 云端策略巡检不可用,本域结论仅基于护城河自扫`;§三处置清单不出 [文档] 链接;§五降级说明节列一行,文案 `⚠️ 云端策略巡检不可用({Errors[0].Message}),建议给当前子账号附加 QcloudAdvisorReadOnlyAccess 策略后复跑`(`QcloudAdvisorReadOnlyAccess` 是腾讯云原生不可改字符串,F34 例外保留) |

## 9. 修复文档链接渲染规则(HARD)

- `repair_url` 必须从 `inspect_advisor.json/findings[].repair_url` **直接读**,**禁止** LLM 编造、用 strategy_id 拼接、用关键词搜索控制台 URL,**禁止**从 `repair` markdown 现场抽(脚本已抽好)
- 渲染位置:① §三立即处置清单每条 🔴/🟠 末尾追加 `处置参考: [文档](repair_url)`(若 url 非空);② §02-§08 各域风险逐行表的"修复"列;**链接文案统一为「文档」**,禁止写「advisor 文档」「策略修复指引」等暗示来源的措辞(F34)
- `repair_url` 为空字符串 → **处置项不加链接,不要写"暂无文档"占位**(实测 ~10% finding 上游缺 url,空就空,不补救;13-report-template.md §0.5 HARD #9 强制)
- 主 finding 表(如 §05 "Redis 公网暴露明细")**不加 [文档] 列** — 表格已经够窄,链接列会折行

## 10. 风险逐行表 schema(§02-§08 各域统一格式)

```markdown
| 严重度 | 资源 ID | 名称 | 地域 | 风险描述 | 修复 |
|-----:|:-------|:----|:-----|:--------|:----|
| 🔴/🟠/🟡 | {resource_id} | {resource_name} | {region} | {strategy_name}(持续 {risk_days} 天) | [文档]({repair_url}) / — |
```

各域具体列名/字段映射见 `references/13-report-template.md` §02-§08 「云端策略命中渲染指引」段。**2026-05-21 起统一格式,不再为 CLB/TKE/CKafka 等产品保留特有列(Listener/Endpoint/VipList)**;特有字段如有运维必看价值,改进 strategy_name 文案表达。

## 11. cloudq 替代说明(2026-05-20 起 advisor 替代 cloudq 7 类能力)

- 替代映射见本文档 §3:巡检 / 安全合规 / 处置方案 / 最佳实践 / AI 智能巡检 / 主动预警 / 架构健康度
- **永久不替代**(选项 C 归零):多云对比 / 架构图生成 / 闲置资源(归 costopt 后续 TODO)/ 资源查询(归 billing 后续 TODO)/ `DescribeUserAuthorizationStatus` 接口
- **cloudq skill 本轮一行不动**,等 inspector + costopt + billing 全部完工再统一删除

---

## 12. Step 3 / Step 4 产物 schema 速查表(避免反复 `print(keys)` 探查)

> 2026-05-11 真实 session 实测 LLM 在 Step 3 跑了 19 次 `python3 -c ...print(keys())` 探查 JSON 结构,耗时 2min+。以下结构 100% 确定,**不要再试探**:

| 文件 | 顶层类型 | 结构 |
|---|---|---|
| `inspect_advisor.json` | **dict** | `{scan_status, total_strategies(450), total_risks, total_resources, by_group:{安全:{checked,hit,risks_total},可靠:...,性能:...,成本:...,服务限制:...}, level3_count, repair_url_coverage(0-1,实测 0.88-0.90), findings:[{strategy_id, strategy_name, product(中文), group, type(空), level_max(0-3), risk_total, resource_total, repair_url(可能为空 ~10%), repair(markdown 原文), risks:[{instance_id, instance_name, region, zone, level(int), risk_days, tags:[{Key,Value}], vip, vport, status, ignore_type, pri_id, condition_id}]}], errors:[{kind:'fetch_strategies'/'fetch_risks', strategy_id?, error}]}` - **§02-§08 各域资源数据的主源**(2026-05-21 架构重构后)。**判定规则与 SOP 域路由见本文档 §1-§11**。`Level` 字段已在脚本里 string→int 转过,LLM 端直接 `f.level_max` 用,不要再次转换;`repair_url` 是从 `repair` markdown 抽的第一个 `[文档](url)`,可能为空 ~10%,**不要 LLM 编造或拼接**(13-report-template.md §0.5 HARD #9 强制约束)。**scan_status 三态**:ok 正常出云端策略各域主表 + repair_url;partial 出能拉到的部分,errors 段写明失败的 strategy;denied(全 450 strategy 拉取失败)→ LLM 在 §02-§08 各域降级到只展示 Step 1a/Step 2 自扫覆盖的有限维度(SG 端口/CWP 暴破/SSL 过期/CAM/COS ACL/审计事件流/标签合规),并在 §五追加降级声明 |
| `inspect_advisor_strategies.json` | **list** | 全 450 strategy 元数据缓存(LLM 反查 strategy_name/group/product/repair markdown 用)。元素字段:`{StrategyId, Name, Desc, Ignore(模板), Notice(模板), SubProduct, Product(key,如'mysql'), ProductDesc(中文名), Repair(markdown,含修复链接), GroupId, GroupName('安全'/'可靠'/'性能'/'成本'/'服务限制'), IsSupportCustom, Conditions:[{ConditionId, Level(int), LevelDesc(高/中/低风险), Desc}]}`。**跨账户元数据完全一致**,本轮不做 `shared/cache/advisor/`,统一落 `$OUT_DIR/`,每次跑都重拉 ~1-2s 可忽略 |
| `inspect_sg_rules.json` | **dict** | `{<region>: {total_sgs, all_port_risks:[{region,sg,name,proto,assoc}], hr_port_risks:[{region,sg,port,service,assoc}], empty_sgs:[{region,id,name}]}}` - Step 1a 自扫(SG 端口聚合,云端策略 strategy 170 只给粗粒度,运维需要 4 类明细) |
| `inspect_vpc_cross.json` | **dict** | `{own_uin, ccn_total, peer_total, scan_status, ccn_cross_uin:[{ccn_id,ccn_name,inst_type,inst_id,inst_region,inst_uin}], peer_cross_uin:[{region,peer_id,peer_name,src_vpc,src_uin,src_region,dst_vpc,dst_uin,dst_region,state}]}` - Step 1a 自扫(跨 UIN VPC,云端策略只看本账号)。**判定规则**:`ccn_cross_uin` 非空 → §02 跨UIN云联网 🟠 高,逐行列实例;`peer_cross_uin` 非空 → §02 跨UIN对等连接 🟠 高,逐行列。`scan_status='denied'` 时报告标"权限不足无法判定"而非"无关联" |
| `inspect_cwp.json` | **dict** | `{machine_general:{MachineCnt,ProtectMachineCnt,NotProtectMachineCnt,RiskMachineCnt,...}, machines_by_region:{region:[{MachineName,InstanceId,MachineIp,MachineWanIp,MachineOs,AgentStatus(ONLINE/OFFLINE/UNINSTALLED),AgentVersion,ProtectType,SecurityStatus,IsProVersion,MalwareNum,VulNum,BaselineNum,InvasionNum,CyberAttackNum}]}, brute_attack:[{MachineIp,MachineName,UserName,SrcIp,Status(FAILED/SUCCESS),CreateTime,Country,Protocol,Port,InstanceId,Count}], brute_total_all:int, host_login:[{MachineIp,MachineName,UserName,SrcIp,LoginTime,RiskLevel,IsRiskArea,IsRiskUser,IsRiskTime,IsRiskSrcIp,Location}], login_total_all:int, emergency_vul:{List:[{VulId,Level,VulName,CveId,Status,LastScanTime,HostCount}], ExistsRisk:bool, TotalCount:int}, vul_summary:{TotalVulCount,VulHostCount,ScanTime,LastFixTime,IfFirstScan:bool}, scan_status}` - Step 1a 自扫(暴破成功明细 root@IP/SUCCESS,云端策略只标"未防护"缺真入侵证据)。**关键判定**:`agent_uninstalled > 0` → §03 Agent 未装 🔴,列明细;`brute_success > 0` → §03 已被攻破 🔴 严重,逐条列;`vul_if_first_scan==True` → 🟠 从未扫描;`machine_general.ProtectMachineCnt==0` 且 `MachineCnt>0` → 🔴 未付费防护。**历史坑**:`DescribeNonlocalLoginPlaces` 已废弃改用 `DescribeHostLoginList`;`DescribeMachineList` 改用更全的 `DescribeMachines`。`scan_status='denied'` 时报告标"权限不足无法判定" |
| `inspect_cloudaudit.json` | **dict** | `{audits:[],tracks:[],audits_total_all:int,tracks_total_all:int,caller_uin,owner_uin,is_root_caller:bool,scan_status}` - Step 1a 自扫(跟踪集配置层,云端策略不审 CloudAudit)。双接口拉:`ListAudits`(旧版跟踪集)+ `DescribeAuditTracks`(新版跟踪集,必传 PageNumber/PageSize≤50);任一非空就算"已配置"。**判定规则**(必须先看 `scan_status`):`scan_status='ok'` 且 `audits_total_all==0 && tracks_total_all==0` → 真未配 🟡(2026-05-12 第 6 轮降权);`scan_status='caller_subuser'` → **不可断言** ⚠️,报告写"扫描凭证为子账号 {caller_uin}(OwnerUin={owner_uin}),CloudAudit 跟踪集配置需主账号本人确认";`scan_status='denied'` → ⚠️ 权限不足。**真正 §10/§12 核心信号在 `inspect_audit_events.json`(下方),跟踪集只是合规摆设** |
| `inspect_ssl.json` | **list** | `[{OwnerUin,ProductZhName,Domain,Status,CertEndTime,days_to_expire}]` - Step 1a 自扫(顶层就是 list,**不是** `{"certs":[...]}`)。云端策略 strategy 1000 仅 1 个粗粒度不给到期天数,自扫给出 days_to_expire 后报告 §12 SSL 按"已过期/30d 内到期"分级 |
| `inspect_cam.json` | **dict** | `{"caller_identity": {uin, owner_uin, is_root_caller, degraded_reason}, "principals": [{uin,name,type,console_login,aks,aks_scan_status,mfa_flags,mfa_scan_status,last_login_time,last_login_scan_status,groups,groups_scan_status,audit_last_login}], "policies": [...], "root_aks": [{AccessKeyId,Status,CreateTime,last_used_date}], "root_aks_scan_status": "ok|skipped_subuser_caller|denied|error", "admin_grants": [{uin,name,source}], "custom_admin_policies": [{policy_id,policy_name}], "collaborators_scan_status": "ok|skipped_subuser_caller|error", "audit_cross_check": {suspect_count, audit_verified_count, verified_uins, scan_status}}` - Step 2 独立脚本 `inspect_cam.py`(CAM 是护城河域,云端策略覆盖几乎为 0) |
| `inspect_cos.json` | **dict** | `{"buckets": [{Name,Location,BucketType,OFSType,Tags}], "acl_findings": {total,public_rw,public_r,skipped,errors,public_r_list:[{bucket,region,public_type}]}}` - Step 2 独立脚本 `inspect_cos.py`(ACL 桶名清单是自扫独有,云端策略给数字不给桶名)。`acl_findings` 是 **dict 不是 list** |
| `inspect_audit_events.json` | **dict** | `{time_window:{start,end,days,start_iso,end_iso}, scan_status, events_by_category:{ak_lifecycle:[],user_lifecycle:[],permission_grant:[],mfa_change:[],resource_destroy:[],kms_tamper:[],sg_change:[],audit_tamper:[],role_change:[]}, summary_by_principal:{username:{total,events:{EventName:cnt},is_service:bool}}, service_account_set:[], total_events, total_categories_hit, category_risk:{cat:critical/high/medium}, errors:[]}` - Step 1 独立脚本 `inspect_audit_events.py`(**§10/§12 核心数据源**,云端策略不覆盖事件流)。判定规则与 §10/§12 写报告硬骨架见 [`references/10-logging-monitoring.md`](10-logging-monitoring.md) |
| `inspect_resource_tags.json` | **dict** | `{required_logic:"head AND (business OR customer)", month:"YYYY-MM", scan_status, total_untagged_resources, by_product:{<biz_code>:{count, cost_total}}, untagged_details:[{biz_code,biz_name,product_code,product_name,region,resource_id,resource_name,total_cost,missing_keys:[],verdict:"🔴/🟠/🔴🟠"}], errors:[]}` - Step 2.6 prompt 驱动(标签合规公司内规,云端策略不知道)。`untagged_details` 里全部是真不合规资源,`verdict` 字段直接给分级,**不要再二次过滤或重算**。数据源限制:只覆盖**上月有消费的资源** |
| `inspect_infra.json` | **dict** | `{baseline:{scan_status,organization:{joined,org_id,org_name,member_count,dept_depth,scan_status},contacts:{configured,count,details},alarm_notices:{configured,count,details}}, network:{scan_status,vpc_summary:{total,by_region},subnets_no_acl:[{region,subnet_id,name,vpc_id,cidr}],nat_gateways:{total,by_region},eip_total,eip_scattered:[{region,eip_id,address,bound_resource,bound_type}],bastion_host:{deployed,count,instances}}, security:{scan_status,waf:{domains_total,domains_bound_lb,domains_unbound_lb,scan_status},ddos:{bgp_instances,instances,scan_status},cfw:{enabled,acl_count,scan_status},db_audit_note}, compliance:{scan_status,config_enabled,rules_total,rules_by_framework:{CIS,等保,other},compliance_rate,non_compliant_count,compliant_count,total_evaluated,key_missing_rules,evaluation_summary}}` - Step 1c 自扫(4 域配置层,云端策略不覆盖)。判定规则见 `references/17-infra-baseline.md`。各域独立 scan_status 三态(ok/partial/denied)。**数据库审计不在脚本里查**:advisor 已有数据库相关 strategy,LLM 从 digest 推断 |
| `inspect_errors.json` | **list** | scan 中的非致命错误,空 = 干净 |
| `inspect_meta.json` | **dict** | `{regions, account, uin, out_dir}` |

**通用访问模式**(直接复用,不要自己推敲):

```python
# 1) 资源类风险逐行(§02-§08 主表):从 inspect_advisor.json 取
adv = json.load(open(f"{OUT}/inspect_advisor.json"))
for f in adv['findings']:
    if f['product'] in ('云服务器（CVM）','云数据库（MySQL）',...):  # 域路由按本文档 §1
        for risk in f['risks']:
            # risk['instance_id'] / risk['instance_name'] / risk['region'] / risk['risk_days']
            ...
# 2) SG 端口聚合:从 inspect_sg_rules.json 取
sgs = json.load(open(f"{OUT}/inspect_sg_rules.json"))
all_port = [r for region_data in sgs.values() for r in region_data.get('all_port_risks',[])]
# 3) MFA 判定:mfa_flags 是 dict,{} 表示未开,{'Stoken':1} 表示开了
# ⚠️ 2026-05-12: 子账号身份下 mfa_scan_status=skipped_subuser_caller 时
# mfa_flags 字段值不可信(API 被拒返空 ≠ 未开 MFA),必须先过滤 scan_status
no_mfa = [p for p in principals if p.get('console_login')==1
          and p.get('mfa_scan_status')=='ok' and not p.get('mfa_flags')]
# 4) 闲置 AK:ak.last_used_date 是字符串 YYYY-MM-DD,字符串比较即可
```

❗ **四条硬红线:**

1. **❌ 不得 `python3 -c "...print(list(data.keys()))..."` 探查结构**。schema 在上表里,直接写分析逻辑。累计探查超过 **2 次**就说明你没读速查表,停下来重读。
2. **❌ 不得重跑判定速查表已覆盖的判定**(advisor finding level_max/risk_days、SG 端口聚合、CWP 暴破成功、SSL 过期天数、CloudAudit 事件流类别 等)。你是在补充,不是在复审。
3. **❌ 不得发任何腾讯云 API**。Step 1/2 已经全采,缺什么字段就在报告里写"需手工核查",**不**调 API 补查。下一轮把该字段加进 Step 1a/2 脚本才是正道。
4. **❌ 不得现场 `for region in regions` 批量扫资源**。资源类数据全部从 advisor findings 取,不要自己模仿 Step 1a 写代码。

**真的要补查的场景**(如某 strategy 想看 risks 之外的字段):

- 只查涉及的那一两个具体资源,不要 `for region in regions` 展开
- 补查结果落盘 `$OUT_DIR/inspect_<key>_detail.json`,保持 summary-only 的输出纪律
- 单次补查超过 **5 个资源**就说明 Step 1a/Step 1b 该扩字段了,长期办法是改对应脚本

❗ **COS 的 `skipped` 不等于失败**。`ap-*-adc` 专属集群 bucket 走不通公网 COS 接口,报告里必须明确标注"专属集群 N 个 bucket 未扫,需手工核查 ACL",但**不要**在这一步尝试换域名再扫——标准 SDK 没有公网路径。
