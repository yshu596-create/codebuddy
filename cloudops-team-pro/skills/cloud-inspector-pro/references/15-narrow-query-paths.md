# 15 单域查询路径与应急取证轻量模板

> 配套 SKILL.md §使用方式 - 单域查询(`scope=narrow`)与应急取证窄查询(`scope=narrow_forensic`)。
> [`13-report-template.md`](13-report-template.md) 是 `scope=full` 的 4 大类×动态产品硬骨架;**本文件是 narrow / narrow_forensic 的轻量模板**。
> 与 13 模板的关系:13 是 §一概览(4 大类矩阵)+ §二项目详情(4 大类×动态产品骨架)+ §三处置清单 + §四扫描降级,严肃合规;15 是 §一概览(目标域单表)+ §二明细(目标域内分桶)+ §三处置 + §N扫描范围与边界,精简易读。

---

## §0 触发条件清单(LLM 怎么识别 scope=narrow / narrow_forensic / full)

把 SKILL.md description 的四分语义具象化,作为 LLM 路由的速查表:

| 用户原话样本 | 推断 scope | 走哪节模板 |
|---|---|---|
| "巡检一下营销账号,出本周的安全巡检报告" | full | 13.md 4 大类骨架 |
| "全面检查 XX 账户" / "扫一下 YYY 账户" / "生成本周巡检报告" | full | 13.md |
| "看看一中心的 SSL 证书有没有快到期" | narrow | 本文件 §1 |
| "帮我看下营销账号的安全组对不对" | narrow | 本文件 §2 |
| "列一下闲置 AK / 最近 90 天没用的 AK" | narrow | (CAM 单域,本文件 §6 差异表参考) |
| "TSP 账号这周有没有人删 COS 桶" | narrow_forensic | 本文件 §3 |
| "看看 XX 账户最近有没有被破解 / 暴破日志" | narrow_forensic | 本文件 §4 |
| "上周谁改了 IAM 策略" / "销毁了几台 CVM" | narrow_forensic | 本文件 §5 通用骨架 |
| "🔴 严重漏洞修复要多久" / "AK 闲置阈值多少天" | sla_query | 直接读 references,不出报告 |
| "看看 XX 账户怎么样" / "扫一下 YYY"(模糊) | **narrow + 询问澄清** | 默认 narrow,问用户"查哪类资源",不要默认拉成 full |

判定后 LLM 内部 `export REPORT_SCOPE=full|narrow|narrow_forensic|skipped` 给 Step 4 grep 自检用。

---

## §1 SSL 单域模板(`scope=narrow`)

**触发**:"看看 XX 的 SSL 证书有没有快到期 / 快过期 / 还有几天" / "列一下 SSL 证书"
**数据源**:`ssl.tencentcloudapi.com / DescribeCertificates` @ ap-shanghai(SSL 全局服务,任意 region 一致)
**落盘**:`$OUT_DIR/inspect_ssl.json`(若已存在 ≤30 min 则 Read 复用,否则即时调)

### 模板正文(直接抄)

```markdown
# {账户名} SSL 证书巡检 {YYYY-MM-DD}

> 范围:腾讯云 SSL 证书全量(`DescribeCertificates` Total={N})单域查询
> 报告期:{YYYY-MM-DD} | UIN:{uin} | 数据日期:{YYYY-MM-DD}

## 一、巡检概览

| 指标 | 数量 |
|---|---|
| 证书总数 | {total} |
| 🔴 已过期 | {expired} |
| 🔴 ≤3 天到期 | {urgent} |
| 🟠 ≤30 天到期 | {warn} |
| ✅ 正常 | {normal} |
| ⚠️ 时间未知 | {unknown} |

**关键判断**:{一句话,说明本次最迫切的处置项,如"8 张已过期,其中 6 张 *.map.qq.com 业务证书需立即续签"}

## 二、证书明细

### 2.1 🔴 已过期({expired} 张)
| 域名 | 别名 | 状态码 | 过期天数 | 业务标签 |
|---|---|---|---|---|

### 2.2 🔴 ≤3 天到期({urgent} 张)
(同列)

### 2.3 🟠 ≤30 天到期({warn} 张)
(同列)

### 2.4 ✅ 正常({normal} 张)
(可折叠,默认只列 Top 5;数量大时给"完整清单见 `inspect_ssl.json`")

## 三、立即处置清单

| 级别 | 域名 / 别名 | 操作建议 | SLA |
|---|---|---|---|
| 🔴 ≤2h | (逐张过期/即将过期证书) | 续签或停用 | (按 12-incident-response §12.1) |
| 🟠 ≤24h | (≤30 天到期) | 计划续签 | |
| 🟡 ≤7d | (业务下线但证书未注销) | 注销冗余证书 | |

## 四、扫描范围与边界

- **范围**:仅 SSL 证书清单(`DescribeCertificates`),**未覆盖**其余 11 个 SOP 域(IAM/SG/CVM/COS/数据库/CLB/TKE/中间件/密钥/日志/合规/CWP/事件流),如需全面巡检请说"巡检 {账户名}"。
- **数据源**:`ssl.tencentcloudapi.com 2019-12-05 / DescribeCertificates` @ ap-shanghai,分页 `Limit=100` 翻完 Total。
- **天数计算**:`CertEndTime - 当前北京时间`,基准时区 GMT+8。
- **分级规则**:`Status` 字段为优先(STATUS_EXPIRED 直接 🔴);其次按剩余天数 ≤3 / ≤30 / 其他。
- **scan_status**:`ok` / `error: <reason>` / `degraded: <reason>`。

报告生成 {YYYY-MM-DD HH:MM} CST | 数据源 `DescribeCertificates` @ ap-shanghai
```

### HARD 约束

1. §四数据采集说明**必出**,显式说明分级规则与 scan_status,否则用户无从判断"是否扫全/有没有漏"
2. 已过期/≤3 天 的证书逐张列(不可折叠),≤30 天逐张列,正常证书允许折叠 Top 5
3. F34:报告里禁出现 advisor/智能顾问/strategy/costopt 字眼

---

## §2 SG 单域模板(`scope=narrow`)

**触发**:"检查 XX 安全组对不对" / "看下 SG 端口" / "SG 有没有 0.0.0.0 开放"
**数据源**:`vpc.tencentcloudapi.com / DescribeSecurityGroups` + `DescribeSecurityGroupPolicies` + `DescribeSecurityGroupAssociationStatistics`,扫所有 region
**落盘**:`$OUT_DIR/inspect_sg_rules.json`(若已存在 ≤30 min 则 Read 复用)

### 模板正文

```markdown
# {账户名} 安全组规则巡检 {YYYY-MM-DD}

> 范围:腾讯云 VPC 安全组全量({N} region 扫描)单域查询
> 报告期:{YYYY-MM-DD} | UIN:{uin} | 数据日期:{YYYY-MM-DD}

## 一、巡检概览

| 指标 | 数量 |
|---|---|
| SG 总数 | {sg_total} |
| 🔴 全端口 0.0.0.0/0:ALL | {full_open_total}({with_instance} 有实例关联) |
| 🔴 高危端口对外(22/3389/3306/6379/27017/...) | {high_risk_total}({with_instance} 有实例关联) |
| 🟡 空安全组(无任何资源关联) | {empty_total} |
| 扫描耗时 | {seconds} 秒 |
| 失败地域 | {failed_regions or "无"} |

**整体判断**:{一句话,说明最严峻的真风险,如"4 个 SG 全端口开放且均关联实例,1 个 SG 同时开 SSH+RDP 公网 + 3 CVM,需立即收敛"}

## 二、明细

### 2.1 桶 1:全端口 0.0.0.0/0:ALL(🔴 严重,{full_open_total} 项)

| 严重度 | 地域 | SG ID | SG 名称 | 协议 | 关联实例数 |
|---|---|---|---|---|---|

(无项时显式写 "**无**",**不要省略整个子节**)

### 2.2 桶 2:高危端口对外(🔴/🟡)

**🔴 有实例关联({high_with_instance} 项)**:

| 地域 | SG ID | SG 名称 | 端口 | 协议 | 来源 | 关联实例数 |
|---|---|---|---|---|---|---|

**🟡 无实例关联({high_without_instance} 项)**:

(同列,可折叠)

### 2.3 桶 3:空安全组(🟡 中,{empty_total} 项,建议清理)

| 地域 | SG ID | SG 名称 |
|---|---|---|

(可折叠,数量多时给"完整清单见 `inspect_sg_rules.json`")

## 三、立即处置清单

| 级别 | 资源 | 操作建议 |
|---|---|---|
| 🔴 ≤2h | {SG ID} @ {region} | 删除 0.0.0.0/0:ALL 入站规则,改白名单 |
| 🔴 ≤2h | {SG ID} 22/3389 公网 | 改 VPN 或跳板机访问 |
| 🟡 ≤7d | {空 SG 列表} | 清理无关联 SG |

## 四、扫描范围与边界

- **范围**:仅安全组规则(`DescribeSecurityGroups` + `DescribeSecurityGroupPolicies` + `DescribeSecurityGroupAssociationStatistics`),**未覆盖**其余 11 个 SOP 域(IAM/CVM/COS/数据库/CLB/TKE/中间件/密钥/日志/合规/SSL/CWP/事件流),如需全面巡检请说"巡检 {账户名}"。
- **数据源**:`vpc.tencentcloudapi.com 2017-03-12`,扫 {N} region,失败 {failed_regions or "0"} region。
- **判定阈值**:依据 SOP §02 §2.1(0.0.0.0/0:ALL 一律 🔴;22/3389/3306/6379/27017 对公网 + 有实例 🔴,无实例 🟡)。
- **scan_status**:`ok` / `degraded: <reason>`。

报告生成 {YYYY-MM-DD HH:MM} CST | 数据源 `DescribeSecurityGroups` @ {N} regions
```

### HARD 约束

1. 三桶必出(即便某桶 0 项,显式写"无"而不是省略子节)
2. §四扫描范围与边界**必出**,显式列未覆盖域
3. F34 同 §1

---

## §3 COS 事件取证窄查模板(`scope=narrow_forensic`)

**触发**:"这周/上周/最近 N 天有没有人删 COS 桶" / "DeleteBucket 事件" / "COS 桶被谁删了"
**数据源**:`cloudaudit.tencentcloudapi.com / LookUpEvents` @ ap-guangzhou(全局服务,任意 region 一致)
**查询**:`LookupAttributes=[{"AttributeKey":"EventName","AttributeValue":"DeleteBucket"}]`,默认时间窗 7 天

### 模板正文

```markdown
# {账户名} COS 删除事件应急取证 {YYYY-MM-DD}

| 字段 | 值 |
|---|---|
| 报告类型 | 应急取证(targeted forensics) |
| 查询事件 | `DeleteBucket` |
| 时间窗 | 最近 {N} 天({YYYY-MM-DD HH:MM} ~ {YYYY-MM-DD HH:MM} CST) |
| UIN | {uin} |
| 数据源 | 腾讯云 CloudAudit `LookUpEvents` @ ap-guangzhou |
| 生成时间 | {YYYY-MM-DD HH:MM} CST |

## 一、结论

{✅ / ⚠️ / 🔴} {一句话定性}。命中总数:{hit_total} 条,其中非服务账号 {non_service} 条 / 服务账号 {service} 条 / 失败操作 {failed} 条。{是否需立即排查的明确建议}

## 二、明细

### 2.1 非服务账号(逐条列)

| 时间(CST) | Username | SrcIP | Region | EventName | Resource | ErrorCode |
|---|---|---|---|---|---|---|

(0 条时显式写"**无**",不省略子节)

### 2.2 服务账号(聚合)

| 服务账号身份 | 命中条数 | 涉及桶数 | 备注 |
|---|---|---|---|

(0 条时显式写"**无**")

### 2.3 失败操作(`ErrorCode != 0`,可能是攻击者权限不足探测)

| 时间(CST) | Username | SrcIP | EventName | ErrorCode | ErrorMessage |
|---|---|---|---|---|---|

(0 条时显式写"**无**")

## 三、扫描可信度

| 维度 | 结果 |
|---|---|
| 接口连通性 | {ok / degraded: <reason>} |
| 调用账户身份 | 主账号 / 子账号({UserName},`is_root_caller={true/false}`) |
| 区域试探 | ap-guangzhou(全局服务,已确认一致) |
| 翻页 | NextToken 翻页 {N} 次,完整覆盖时间窗 |
| 重试 | {重试次数;0 表示首次即成功} |

## 四、补充提示(可选)

- CloudAudit 默认保留 90 天,如需更长窗扩到 `StartTime=now-30d` 即可
- 反取证清理(攻击者擦除审计):另查 `DeleteAuditTrack` / `StopLogging` 事件流,见 §5 通用骨架
- 0 命中**不代表**"审计未启用",CloudAudit 事件流是腾讯云默认开启的全局服务
```

### HARD 约束

1. **零事件不写"未启用审计"**(参考 SKILL.md §10.2 HARD #4):0 命中要写"✅ 本周无 DeleteBucket 操作",显式给出结论
2. 三个子节(非服务/服务/失败)**必出**,0 条时显式写"无"而不是省略整个子节
3. §三扫描可信度**必出**,避免"扫完没事 = 真没事"的歧义
4. F34 同 §1

---

## §4 CWP 入侵取证窄查模板(`scope=narrow_forensic`)

**触发**:"看看 XX 账户最近有没有被破解" / "CWP 暴破日志看下" / "主机有没有被入侵"
**数据源**:`cwp.tencentcloudapi.com / DescribeBruteAttacks`(逐 region)+ `DescribeMachines`(主机清单)
**落盘**:`$OUT_DIR/inspect_cwp.json`(若已存在 ≤30 min 则 Read 复用)

### 模板正文

```markdown
# {账户名} CWP 主机入侵取证 {YYYY-MM-DD}

| 字段 | 值 |
|---|---|
| 报告类型 | 应急取证(targeted forensics) |
| 查询主体 | CWP 暴破日志 + 异常登录 |
| 时间窗 | 最近 {N} 天 |
| UIN | {uin} |
| 数据源 | `cwp.DescribeBruteAttacks` + `DescribeHostLoginList` |
| 生成时间 | {YYYY-MM-DD HH:MM} CST |

## 一、结论

{🔴 / ⚠️ / ✅} 暴破成功 {success_count} 条 / 受攻击实例 {affected_instances} 台 / 失败暴破 Top 10 {top10_total} 次。{一句话定性,如"本周 lhins-XXX root@22 暴破 SUCCESS 528 次,来源法国 OVH IP,需立即下线核查后端 webshell/异常进程"}

## 二、明细

### 2.1 🔴 暴破成功(brute_success > 0,逐条列)

| 时间(CST) | InstanceId | MachineName | UserName | SrcIp | 国家/位置 | 协议:端口 | Count |
|---|---|---|---|---|---|---|---|

(0 条时显式写"✅ 本周无暴破成功")

### 2.2 暴破失败 Top 10(按 Count 倒序)

| 时间(CST) | InstanceId | UserName | SrcIp | Count |
|---|---|---|---|---|

### 2.3 异常登录(host_login,任一 IsRiskArea/IsRiskUser/IsRiskTime/IsRiskSrcIp = true)

| 时间(CST) | InstanceId | UserName | SrcIp | 风险维度 |
|---|---|---|---|---|

(0 条时显式写"**无**")

## 三、立即处置清单(`success_count > 0` 必出)

| 级别 | 资源 | 操作建议 |
|---|---|---|
| 🔴 ≤2h | {InstanceId} | 立即下线核查 / 检查 webshell + 异常进程 / 修改 root 密码 + 关闭密码登录改 key |
| 🔴 ≤2h | 来源 IP {SrcIp} | 安全组封禁 + 上报 |

## 四、扫描可信度

| 维度 | 结果 |
|---|---|
| 接口连通性 | {ok / degraded: <reason>;如 ap-osaka 报"地域不存在",已跳过} |
| 调用账户身份 | 主账号 / 子账号({UserName}) |
| 区域覆盖 | {N} region,失败 {failed_regions or "0"} region |
| Agent 覆盖 | {agent_installed} / {host_total} 台安装 CWP Agent({ratio}%) |
```

### HARD 约束

1. `brute_success > 0` **必逐条列**(SKILL.md §03 已强约束,本节复述):时间 / InstanceId / MachineName / UserName / SrcIp / 国家 / 协议端口 / Count 8 列齐
2. 零事件写"✅ 本周无暴破成功",**不要**写"未启用 CWP"
3. §三立即处置清单仅在 `success_count > 0` 时必出
4. F34 同 §1

---

## §5 targeted forensics 通用骨架(`scope=narrow_forensic` 兜底)

当用户问的事件不在 §3 §4 已模板化的范围内(如"上周谁改了 IAM 策略" / "销毁了 N 台 CVM" / "新建了多少 AK"),走本通用三段骨架。

### 模板正文

```markdown
# {账户名} {事件主体} 应急取证 {YYYY-MM-DD}

| 字段 | 值 |
|---|---|
| 报告类型 | 应急取证(targeted forensics) |
| 查询事件 | `{EventName}` 或 `{EventName1, EventName2, ...}`(≤3 个) |
| 时间窗 | 最近 {N} 天 |
| UIN | {uin} |
| 数据源 | `cloudaudit / LookUpEvents` @ ap-guangzhou |
| 生成时间 | {YYYY-MM-DD HH:MM} CST |

## 一、结论(**必出**)

{✅ / ⚠️ / 🔴} {一句话定性 + 命中数 + 时间窗 + 是否需立即排查}

## 二、明细(**必出**,三子节)

### 2.1 非服务账号(逐条列)
| 时间(CST) | Username | SrcIP | Region | EventName | Resource | ErrorCode |
|---|---|---|---|---|---|---|

(0 条显式写"无")

### 2.2 服务账号(聚合)
| 服务账号 | 命中条数 | 涉及资源数 |
|---|---|---|

(0 条显式写"无")

### 2.3 失败操作(`ErrorCode != 0`)
| 时间(CST) | Username | SrcIP | EventName | ErrorCode | ErrorMessage |
|---|---|---|---|---|---|

(0 条显式写"无")

## 三、扫描可信度(**必出**)

| 维度 | 结果 |
|---|---|
| 接口连通性 | {ok / degraded: <reason>} |
| 调用账户身份 | 主账号 / 子账号({UserName}) |
| 区域试探 | ap-guangzhou |
| 翻页 | NextToken {N} 次 |
| 重试 | {N} 次 |
```

### HARD 约束

1. **不**套 §一概览矩阵(4 大类)/ §二项目详情(4 大类×动态产品骨架)/ §三处置清单(三档)— 那是 13 模板的形态,**不要混搭**
2. 数据源固定为 `LookUpEvents`(单 EventName 或 EventName 列表 ≤3 个),时间窗参数化
3. 零事件**不**写"未启用审计";**必出** §三可信度,避免"扫完没事 = 真没事"歧义
4. F34 同 §1

---

## §6 单域报告与全量报告的差异表(LLM 速查)

给 LLM 一张速查表,避免在单域报告里误套全量段落或在全量报告里漏写硬骨架:

| 维度 | `scope=full`(13 模板) | `scope=narrow`(15 §1-§2) | `scope=narrow_forensic`(15 §3/§4/§5) |
|---|---|---|---|
| 顶层标题 | `# {账户名} 云资源安全巡检报告 {YYYY-MM-DD}` | `# {账户名} {目标域} 巡检 {YYYY-MM-DD}` | `# {账户名} {事件主体} 应急取证 {YYYY-MM-DD}` |
| §一 | 巡检概览(4 大类矩阵) | 单域指标表 + 关键判断 | 结论一句话 + 命中数 |
| §二 | 4 大类×动态产品骨架(每大类必出) | 单域明细(分桶 / 分子节) | 明细三子节(非服务/服务/失败) |
| §三 | 立即处置清单(三档) | 立即处置清单(三档,可省 🟡) | (可省;有 🔴 必出) |
| §四 | 扫描降级说明(可选) | 扫描范围与边界(**必出**) | 扫描可信度(**必出**) |
| §五 | (无) | (无) | 补充提示(可选) |
| 报告尾 | 文档生成时间 + SOP + 执行人 + 下次复查 | 报告生成时间 + 数据源 | 报告生成时间 + 数据源 |
| 落盘 | `report.md` + `report_doc.md` | `report.md` | `report.md` |
| 在线文档 | ❌ 用户显式说才创 | ❌ 用户显式说才创 | ❌ 用户显式说才创 |
| Step 4 grep 自检分支 | `full`(4 大类 + 3 护城河 + 概览 + 处置 + SOUL + F34) | `narrow`(目标域 ≥1 + 边界 + 处置 + SOUL + F34) | `narrow_forensic`(结论 + 明细 + 可信度 + SOUL + F34) |

---

## 与 13-report-template.md 的协作

- **本文件**(15)负责 narrow / narrow_forensic 的报告模板与 HARD 约束
- **13** 负责 full 的 4 大类×动态产品硬骨架(`scope=full` 唯一权威)
- 13 末尾有一节 §单域 / 应急取证报告模板(轻量导航)→ link 到本文件
- SKILL.md §使用方式分流入口分别 link 13(full 路径)与 15(narrow / narrow_forensic 路径)
- **不要**把 15 内容复制进 13,反之亦然 — 双份会随时间漂移,blame 显示 2026-05 三轮优化都遭遇过这个坑
