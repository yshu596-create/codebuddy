# 10 日志与监控巡检

> SOP: TC-SEC-SOP-001 | 章节 10

## 10.1 云审计 CloudAudit

> **2026-05-12 已收编**:CloudAudit 跟踪集采集已在 Step 1 `inspect_scan.py` 直出
> `inspect_cloudaudit.json`,字段 `{audits:[],tracks:[],audits_total_all,tracks_total_all,
> caller_uin,owner_uin,is_root_caller,scan_status}`,scan_status 三态见
> `references/13-report-template.md §10` 的 HARD 规则。**禁止再走下面步骤里"登录控制台
> 手工确认"或 `cloudaudit.ListAudits` 单接口**——双接口(ListAudits 旧版 +
> DescribeAuditTracks 新版)+ caller_identity 都已自动化,LLM 直接读 inspect_cloudaudit.json
> 即可判定,不要再让用户手动复核(除 `scan_status='caller_subuser'` 必须提醒主账号确认外)。
>
> **历史坑(2026-05-12 修)**:
> 1. 旧 spec 用 `action=DescribeAudits/region=ap-shanghai` 双错(腾讯云不存在此 action,
>    cloudaudit 全局只在 ap-guangzhou 提供),inspect_cloudaudit.json 永远空 → §10/§12 一律误报 🟠
> 2. 接口改对(ListAudits @ ap-guangzhou)后,子账号身份调用空返不报错,
>    与"主账号没建跟踪集"无法区分,仍误报 🟠
> 3. 子账号是 `audit:DescribeAuditTracks` 默认有权但返空时不可断言,
>    需 `caller_identity` 三态降级到 ⚠️ 需主账号复核

| 检查项 | 操作方法 | 判定标准 | 风险等级 |
|--------|---------|---------|---------|
| 跟踪集 | CloudAudit > 跟踪集 | 至少 **1 个**开启投递 | 🟠 高 |
| 投递目标 | 跟踪集 > 投递 | 投递至 COS 和/或 CLS | 🟠 高 |
| 保留期 | 存储策略 | **≥ 90 天** | 🟠 高 |
| 多账号 | 跟踪集 > 多账号 | 多 UIN 统一审计 | 🟡 中 |

---

## 10.1.bis CloudAudit 高危事件流扫描

> **2026-05-12 第 6 轮重构**:第 5 轮把 §10 等同于"跟踪集有没有建",但用户质疑"审计这默认就有的,这个功能我理解为是要检测高危操作才对吧"——完全对。事件流是腾讯云 CloudAudit 的真正核心(默认采集 90 天,DescribeEvents 接口),跟踪集只是把日志投递到 CLS/COS 持久化的合规摆设。报告 §10/§12 的核心信号必须取自事件流,不是跟踪集状态。
>
> **数据源**:`scripts/inspect_audit_events.py` 产出 `$OUT_DIR/inspect_audit_events.json`,默认 7 天周巡检窗口,`AUDIT_WINDOW_DAYS=30` 切换月度合规模式。

### 高危 EventName 9 大类清单(43 个,2026-05-12 probe 全部验证有效)

| 类别 | EventName 列表 | 风险等级 | 判定标准 |
|------|----------------|----------|----------|
| audit_tamper | DeleteAuditTrack / StopLogging / UpdateAuditTrack | 🔴 严重 | 任一非空 → 攻击者反取证标志,逐条列 + 立即排查 |
| ak_lifecycle | CreateAccessKey / DeleteAccessKey / UpdateAccessKey | 🔴 高 | 任一非服务账号事件,主账号 AK 操作直接 🔴 |
| permission_grant | Attach/Detach UserPolicy/GroupPolicy/RolePolicy + Create/Update/DeletePolicy | 🔴 高 | 任一非服务账号事件,Resource 含 `*:*` Admin 模板 → 🔴 |
| resource_destroy | TerminateInstances/TerminateInstancesByApi / DeleteBucket / PutBucketAcl / Isolate+DeleteDBInstance / OfflineIsolatedInstances | 🔴 高 | 任一非服务账号事件,COS 公有读改动 + DB 销毁直接 🔴 |
| kms_tamper | DisableKey / ScheduleKeyDeletion / CancelKeyDeletion | 🔴 高 | 任一非空,主密钥禁用/计划删除是数据丢失前兆 |
| mfa_change | SetMfaFlag / UnbindMFADevice | 🟠 中 | UnbindMFADevice 非空 → 核对人员变动 |
| user_lifecycle | CreateUser/DeleteUser/UpdateUser + Create/Update/DeleteLoginProfile | 🟠 中 | 非服务账号 ≥ 3 → 真人造号需核对工单;服务账号(如 bh-add-white-list)聚合一行 |
| role_change | CreateRole / DeleteRole / UpdateRole / UpdateAssumeRolePolicy | 🟠 中 | 任一非空,角色变更可能影响信任关系 |
| sg_change | Create/Modify/DeleteSecurityGroupPolicies / DeleteSecurityGroup / ReplaceSecurityGroupPolicy | 🟡 中 | 非服务账号 ≥ 5 才展开,日常运维改 SG 是常态 |

### 服务账号自动识别规则(脚本侧实现,降噪关键)

满足任一即标 `is_service=true`:
1. Username 含 `QcsRole` / `QCSLinkedRole`(腾讯云服务关联角色)
2. 同 Username 单 EventName 出现 ≥ 5 次(自动化批处理特征)
3. Username 含 `_role` / `-role` / `-sg` / `-add` 后缀且事件总数 ≥ 3

服务账号在报告 §10.2 里**只出 C 表单行汇总**,严禁逐条展开(否则会淹没真高危信号)。

### 时间窗约束

- 默认 7d(周巡检节奏):事件不会被反复告警(30d 窗口下一个事件被报 30 次,告警疲劳)
- 月度合规:`AUDIT_WINDOW_DAYS=30`
- 实时告警:`AUDIT_WINDOW_DAYS=1`(配 cron 跑每天)
- 上限严格 < 30:腾讯云 DescribeEvents 单次跨度 < 30 天,29 天稳妥;脚本 clamp 到 [1,29]

### 历史坑(2026-05-12 第 6 轮 probe 时踩过)

1. **ActionType=Write 全拉行不通**:30 天窗口下 STS GetFederationToken 单类就 1k+ 条,分页 2 分钟没出结果。改成 LookupAttributes 单 EventName 过滤 → 6 路并发 ~5s/账户。
2. **EventTime 是字符串**:必须 int() 转换,否则 strftime 报 `'str' object cannot be interpreted as an integer`。
3. **Resources 是 dict 单值**`{ResourceName, ResourceType}`,不是 list。
4. **call_api 不支持 timeout 参数**(2026-05-12 踩坑,直接用默认即可)。
5. **腾讯云未实现的 EventName 安静返空,不报错**:白名单可以放心列扩展项,失败 EventName=0(三账户 43 类全部成功)。
6. **一中心账户偶发 NetworkError 超时**:必须 retry=3 + sleep 0.5,否则当月报告会有失败类。
7. **子账号有 audit:DescribeEvents 默认权限**(与跟踪集查询不同),无 caller_subuser 三态歧义,scan_status 只有 ok/denied 两态。

### 实测三账户 7 天数据(2026-05-12)

| 账户 | 命中类 | 总事件 | 服务账号识别 | 关键真高危 |
|------|--------|--------|--------------|------------|
| 一中心 | 4 类 | 18 | bh-add-white-list / cam-add_user | bazhshi 5/6 销毁 2 台 CVM + 5/7 删 SG |
| 营销 | 3 类 | 6 | CVM_QCSLinkedRoleInClawPro | **🔴 5/12 13:24 aslinwang 删 COS 桶 prd-ai-1325126984** |
| TSP | 0 类 | 0 | - | ✅ 本周无高危操作 |

---

## 10.2 CLS 日志服务

| 检查项 | 操作方法 | 判定标准 | 处置 |
|--------|---------|---------|------|
| 核心日志接入 | CLS > 日志主题 | 核心业务接入 CLS | 配采集规则 |
| 索引配置 | CLS > 索引 | 开启全文/键值索引 | 配置索引 |
| 日志告警 | CLS > 告警 | 核心日志配告警 | 创建告警策略 |
| 保留期 | CLS > 存储 | **≥ 30 天** | 调整策略 |
| 日志脱敏 | CLS > 脱敏 | 敏感字段脱敏 | 配置脱敏规则 |

### 巡检步骤

1. 登录 https://console.cloud.tencent.com/cls
2. 核查核心业务（Web 应用/数据库/安全设备）是否已接入 CLS
3. 检查每个日志主题的索引配置，确保关键字段可检索
4. 对核心日志主题配置告警规则（如错误率突增、访问频率异常）
5. 核查日志中是否包含密码、AK/SK 等敏感信息，配置脱敏规则

---

## 10.3 云监控告警

| 检查项 | 操作方法 | 判定标准 | 风险等级 |
|--------|---------|---------|---------|
| 告警覆盖 | 云监控 > 策略 | CVM/DB/CLB 必须有告警 | 🟠 高 |
| 告警指标 | 策略详情 | 覆盖 CPU/内存/磁盘/网络 | 🟠 高 |
| 通知渠道 | 通知模板 | 短信/邮件/企微至少一种 | 🟡 中 |
| 通知有效 | 测试通知 | 定期验证渠道畅通 | 🟡 中 |
| 告警回顾 | 告警历史 | 定期回顾处置情况 | 🟡 中 |

### 巡检步骤

1. 登录 https://console.cloud.tencent.com/monitor
2. 检查告警策略列表，核查所有核心资源是否有告警策略绑定
3. 重点确认以下指标告警已配置：
   - CVM：CPU ≥ 90%、内存 ≥ 90%、磁盘 ≥ 85%
   - 数据库：连接数、慢查询数
   - CLB：5xx 错误率
4. 发送测试告警，验证短信/邮件/企微通知可正常接收

---

## 10.4 安全态势感知

| 检查项 | 操作方法 | 判定标准 | 处置 |
|--------|---------|---------|------|
| 安全评分 | 云安全中心 > 总览 | 评分 **≥ 80 分** | 按建议整改 |
| 风险项 | 云安全中心 > 风险 | 无严重/高风险未处置 | 优先处置 |
| 资产暴露面 | 云安全中心 > 资产 | 已识别全量资产 | 补充遗漏资产 |
| 安全事件 | 云安全中心 > 事件 | 无未处置事件 | 处置并闭环 |

### 巡检步骤

1. 登录 https://console.cloud.tencent.com/ssa
2. 查看安全评分，低于 80 分需记录整改计划
3. 查看风险项列表，对严重/高风险项按时限处置
4. 确认已发现全量资产，补充遗漏项
5. 检查安全事件队列，确认无未处置事件

---

## §10/§12 CloudAudit 写报告骨架(2026-05-12 第 6 轮重构,§10 拆为两个子节)

> 本节承接 SKILL.md §使用方式 / Step 4 渲染指引。Step 4 LLM 渲染 §10/§12 报告时**必读本节**,否则会出现"跟踪集 = 审计能力"误解 / "服务账号事件刷屏" / "0 事件写成审计未启用" 等错误。

> **重构原因**:第 5 轮把"跟踪集有没有建"当成 §10/§12 唯一信号,但用户一句质疑戳穿--
> "审计这默认就有的,只是可以投递到 CLS,这个功能我理解为是要检测高危操作才对吧"。
> 完全对。腾讯云事件流(DescribeEvents)默认采集 90 天,跟踪集只是把日志投递到
> CLS/COS 长期持久化,跟踪集没建≠没有审计。**真正的运维价值 = 事件流里的高危操作**,
> 跟踪集配置只是合规摆设。本轮拆成两个子节,跟踪集降权,事件流升为核心。

### §10.1 CloudAudit 跟踪集(配置层,合规摆设,🟡 中)

数据源:`inspect_cloudaudit.json`。判定按 `scan_status` 三态(下表),**风险等级从 🟠 降为 🟡**(没建只影响 90 天外能不能查,事件流仍在采集):

| `cloudaudit.scan_status` | §10.1 跟踪集行写法 |
|---|---|
| `ok` 且 `any_track_configured=False` | `🟡 未配置跟踪集(主账号身份确认):事件流默认采集 90 天,但未投递到 CLS/COS 长期持久化` |
| `ok` 且 `any_track_configured=True` | `✅ 已配置 N 个跟踪集`,N = max(audits_total, tracks_total) |
| `caller_subuser` | `⚠️ 扫描凭证为子账号 {caller_uin}(OwnerUin={owner_uin}),跟踪集配置需主账号本人复核`,**禁止**写"未配置 🟠/🟡" |
| `denied` | `⚠️ 权限不足(CamNoAuth),无法判定` |

### §10.2 CloudAudit 高危事件流(行为层,**§10/§12 真正核心**,🔴/🟠)

数据源:`inspect_audit_events.json`(`scripts/inspect_audit_events.py` 产出,默认 7 天窗口)。
**§10.2 是 §10 的主体,放在 §10.1 之后,优先输出**。

判定主流程(对照 `events_by_category` 9 大类):

| 类别 key | 中文名 | 判定阈值 | 报告写法 |
|---|---|---|---|
| `audit_tamper` | 审计反取证 | 任一非空 | 🔴 严重(攻击者行为标志,逐条列时间/用户/IP/资源,标"立即排查") |
| `ak_lifecycle` | AK 生命周期 | 任一**非服务账号**事件 | 🔴 高(AK 是核心凭证,逐条列;主账号 AK 操作直接 🔴) |
| `permission_grant` | 权限授予 | 任一**非服务账号**事件,或 Resource 含 `*:*` Admin 模板 | 🔴 高(逐条列,服务账号(如 `cam-add_user`)聚合一行) |
| `resource_destroy` | 资源毁灭 | 任一**非服务账号**事件 | 🔴 高(逐条列;服务关联角色销毁是自动化生命周期管理,聚合) |
| `kms_tamper` | KMS 主密钥 | 任一非空 | 🔴 高(逐条列,主密钥禁用/计划删除是数据丢失前兆) |
| `mfa_change` | MFA 变更 | `UnbindMFADevice` 非空 | 🟠 中(MFA 解绑要核对人员变动) |
| `user_lifecycle` | 用户 CRUD | 非服务账号 ≥ 3 | 🟠 中(服务账号自动造号聚合一行;真人逐条) |
| `role_change` | 角色 CRUD | 任一非空 | 🟠 中(逐条列,角色变更可能影响信任关系) |
| `sg_change` | 安全组改动 | 非服务账号 ≥ 5 | 🟡 中(改 SG 是日常运维,只关注高频或服务账号外的) |

**HARD 规则**(下游报告必须遵守):

1. **服务账号行为聚合**:`is_service=true` 的 Username 在报告里**只出一行汇总**(如"`bh-add-white-list` 服务账号本周自动化创建 10 个子用户"),**禁止**逐条展开 -- 否则一中心 33 条 CreateUser 会淹没真高危信号。
2. **真高危信号必须明细到行**:非服务账号的 `audit_tamper`/`ak_lifecycle`/`permission_grant`/`resource_destroy`/`kms_tamper` 必须**逐条列**:`时间 / Username / SourceIP / Region / ResourceName / ErrorCode`,不要聚合。
3. **窗口透明**:报告章节抬头必须写明"本节覆盖最近 N 天高危操作"(从 `time_window.days` 取),N=7 是周巡检默认,N=30 是月度合规。
4. **零事件不是合规**:`total_events==0` 写 `✅ 本周无高危操作`,不要写"无审计配置/审计未启用"--事件流默认采集,空值就是真空。
5. **失败事件单独标注**:`error_code != "0"` 的事件标"操作被拒"(可能是攻击者权限不足探测,或合法操作被策略拦截),与成功事件分开列。
6. **§12 应急响应**复用 §10.2 的明细表,但角度切换:§10.2 是"本周发生过这些事",§12 是"如果发生入侵/异常,从这些事件追溯"。同一份数据,不同视角。
7. **跟踪集与事件流不可混淆**:报告里 §10.1 写跟踪集(投递配置)、§10.2 写事件流(行为信号),**严禁**写成"未配置审计 = 没记录"--事件流永远在记录(腾讯云 90 天默认窗口)。

> Why(2026-05-12 第 6 轮): 第 5 轮单看跟踪集相当于看"录像机有没有挂硬盘",而真正
> 该看的是"录像里有没有人撬保险柜"。Probe 三账户 7 天窗口实测:营销 2026-05-12 13:24
> aslinwang 删 COS 桶 prd-ai-1325126984(🔴 真高危,以前完全漏报);一中心 bazhshi
> 5/6 销毁 2 台 CVM(🔴);TSP 0 命中(✅,纯静态环境)。这种逐条事件证据是月度
> 报告以前完全缺失的,补上后 §10/§12 才真正有运维价值。
