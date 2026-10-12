# 01 IAM / CAM 账号安全巡检

> SOP: TC-SEC-SOP-001 | 章节 01
> 操作路径:CAM 访问管理 → https://console.cloud.tencent.com/cam

---

## 0. 巡检前置:调用者身份判定(2026-05-12 新增)

CAM 大量接口要求**主账号身份或被显式授权**的身份才能调,扫描凭证(环境变量
中的 secret_id)如果是子账号 AK,**默认**以下接口会被拒或只返自身数据。
但如果子账号被授予了 **CAM 只读类策略**(如 `QcloudCamReadOnlyAccess` 或自定义 `cam:Get*/Describe*/List*`),则大部分接口可正常返回数据。

| API | 子账号无 CAM 策略 | 子账号有 cam-read 策略 |
|---|---|---|
| `ListAccessKeys`(不传 `TargetUin`) | 返调用者自己的 AK | 返调用者自己的 AK(不变) |
| `ListAccessKeys --TargetUin {他人}` | OperationDenied | ✅ **正常返回**(实测确认 2026-05-13) |
| `DescribeSafeAuthFlagColl --SubUin {他人}` | AccessDenied | ✅ 正常返回(待验证) |
| `GetUser --Name {他人}` | AuthFailure / RecentlyLoginTime 返空 | ✅ **正常返回**(实测确认 2026-05-13) |
| `DescribeSubAccounts --FilterSubAccountUin` | 被拒 / LastLoginTime 返空 | ✅ 正常返回(待验证) |
| `ListGroupsForUser --SubUin {他人}` | 被拒 / GroupInfo 返空 | ✅ **正常返回**(实测确认 2026-05-13) |
| `GetSecurityLastUsed --SecretIdList` | 被拒 | ✅ 正常返回(待验证) |
| `cloudaudit DescribeEvents` 全量 | 通常被拒 | 通常被拒(需单独 cloudaudit 授权) |
| `ListUsers` / `ListCollaborators` / `ListPolicies` / `GetPolicy` / `ListEntitiesForPolicy` / `ListUsersForGroup` | 默认有权,**正常返数据** | 同左 |

**前置步骤**:`inspect_cam.py` 已在 Phase 0 调用 `GetUserAppId` 探测身份,并在
`inspect_cam.json.caller_identity` 顶层写入 `{uin, owner_uin, is_root_caller,
degraded_reason}`,同时在每个 principal 上挂 `aks_scan_status` /
`mfa_scan_status` / `last_login_scan_status` / `groups_scan_status`,顶层挂
`root_aks_scan_status` / `collaborators_scan_status` / `audit_cross_check.scan_status`。

**(HARD) 2026-05-13 新增:子账号身份不再无条件跳过 -- 先试调接口,调不通再降级**

`is_root_caller=false` 时,**不得直接跳过下列检查项**。必须先尝试调用对应 API,只有在 API 实际返回错误(AuthFailure / AccessDenied / OperationDenied)时,才将该项标记为"未采集"并写降级话术。原因:子账号如果被授予了 `cam-read` 等 CAM 只读策略,这些接口可正常返回数据。

| 检查项 | 先试调的 API | 成功时正常出报告 | 失败时降级为"未采集" |
|:------|:---------|:---------|:----------|
| 离职/未活跃 | `DescribeSubAccounts` / `GetUser` | ✅ 正常输出登录时间+判定 | ❌ 降级 |
| AK/SK 审查 | `ListAccessKeys --TargetUin` | ✅ 正常输出 AK 持有情况 | ❌ 降级 |
| MFA 状态 | `DescribeSafeAuthFlagColl` | ✅ 正常输出 MFA 开启率 | ❌ 降级 |
| 用户组归属 | `ListGroupsForUser` | ✅ 正常输出归属情况 | ❌ 降级 |
| AK 闲置 | `ListAccessKeys` + `GetSecurityLastUsed` | ✅ 正常输出闲置 AK | ❌ 降级 |

**实测数据**(2026-05-13 营销账号 aiops-agent + cam-read 策略):
- `GetUser --Name yujundeng` → RecentlyLoginTime=2026-04-01 11:14:52 ✅
- `ListGroupsForUser --SubUin 100045437701` → [开发测试组] ✅
- `ListAccessKeys --TargetUin 100045437701` → 0 个 AK ✅

**禁止**以下行为(已过时):
- ❌ 看到 `is_root_caller=false` 就直接写"子账号身份无法采集"
- ❌ 不调接口直接跳过整个检查项
- ❌ 把"子账号身份"作为唯一降级条件(应该以 API 实际返回为准)

**正常照出**:`admin_grants` / `custom_admin_policies` / `policies`(自定义策略列表)
这三块子账号身份默认有权,降级模式下也正常输出,不要“宁可信其无”。

---

### IAM 巡检 7 个检测项完整接口清单（2026-05-13 用户确认版）

**1️⃣ 账号总量统计**
- `cam.ListUsers` → 获取所有子账号（含 ConsoleLogin 字段区分可登录/禁止登录）
- `cam.ListCollaborators` → 获取所有协助者

**2️⃣ 疑似离职检测（>90天未登录）**
- `cam.ListUsers` → 筛选 ConsoleLogin=1 的子账号 UIN
- `cam.ListCollaborators` → 获取协助者 UIN
- `cam.DescribeSubAccounts`(`FilterSubAccountUin`)→ 每批 50 个,返回真实 LastLoginTime
- `cam.GetUser`(`Name`)→ 子用户 `RecentlyLoginTime`(含 SSO/企微扫码)

**3️⃣ MFA 开启检查**
- `cam.DescribeSafeAuthFlag` → 主账号 MFA 状态(LoginFlag 字段)
- `cam.DescribeSafeAuthFlagColl`(`SubUin`)→ 逐个查子账号/协助者 MFA(每次 1 个 UIN)

**4️⃣ AK/SK 人机分离审查**
- `cam.ListAccessKeys`(不传 TargetUin)→ 主账号 AK
- `cam.ListUsers` → 筛选 ConsoleLogin=1 的子用户
- `cam.ListCollaborators` → 获取协助者
- `cam.ListAccessKeys`(`TargetUin`)→ 逐个查可登录账号是否持有 AK

**5️⃣ Administrator 权限审查**
- `cam.ListPolicies`(`Keyword=AdministratorAccess`,`Scope=All`)→ 获取预设策略 PolicyId
- `cam.ListEntitiesForPolicy`(`PolicyId`,`Rp=200`)→ 获取策略关联的用户和用户组
- `cam.ListUsersForGroup`(`GroupId`,`Rp=100`)→ 展开用户组内成员

**6️⃣ 用户组归属检查**
- `cam.ListUsers` → 获取人员账号
- `cam.ListCollaborators` → 获取协助者
- `cam.ListGroupsForUser`(`SubUin`)→ 逐个查每个人员账号归属的用户组

**7️⃣ AK/SK 闲置检测**
- `cam.ListUsers` → 所有子账号
- `cam.ListCollaborators` → 所有协助者
- `cam.ListAccessKeys`(`TargetUin`)→ 逐个获取 AccessKeyId
- `cam.GetSecurityLastUsed`(`SecretIdList`,每批≤10个)→ 批量查 AK 最近使用日期

> Why:2026-05-12 一中心管控账户(扫描凭证为子账号 UIN=100047213731)报告里
> 误报"主账号活跃 AK 1 个,最近使用今日"--那把 AK 实际是扫描凭证自己,
> "最近使用=今日"是因为 inspector 刚发完请求。修复后 `root_aks=[]` +
> `root_aks_scan_status=skipped_subuser_caller`,报告侧改写为"未采集,需主账号复核"。

---

## 1.0 账号总量统计

> 数据由 `inspect_cam.py`(Phase 0)落盘到 `inspect_cam.json`,字段:`Sub*Count` / `CollaboratorCount`。报告按 `账号总数 = 子账号总数 + 协助者数量` 渲染。

---




| 检查项 | 操作方法 | 判断标准 | 异常处置 |
|--------|---------|---------|---------|
| 离职账号清理 | ListUsers + DescribeSubAccounts 两步 API(见下方) | 大于 **90天** 未登录判定为疑似离职 | 确认后禁用或删除该账号 |
| AK/SK 权限审查 | CAM > 用户列表 + 协助者列表 + 主账号,检查所有可登录控制台的人员账号(主账号 + 子用户 + 协助者) | 可登录控制台的人员账号不应同时拥有 API 密钥;主账号应避免直接使用 AK/SK | 移除不必要的 API 密钥或调整登录方式;主账号 AK/SK 应迁移至子用户或 STS 临时凭证 |
| Administrator 权限审查 | CAM > 策略,搜索 AdministratorAccess,查看关联用户 | 拥有 Administrator 权限的子账号应尽量少且有合理理由 | 回收不合理的 Administrator 权限,改用最小权限策略 |
| 用户组归属检查 | ListGroupsForUser 遍历人员账号(控制台子用户+协助者) | 每个人员账号应归属至少一个用户组(服务账号跳过) | 调整用户组归属,移除不匹配的关系 |
| MFA 开启检查 | DescribeSafeAuthFlag + DescribeSafeAuthFlagColl(见下方) | 所有账号 LoginFlag 至少一种不为 0 | 立即绑定虚拟/硬件 MFA 设备 |

### AK/SK 权限审查方法

> **核心原则**:可登录控制台的人员账号(主账号 + 子用户 + 协助者)不应同时持有 API 密钥。人机分离,降低凭证泄露攻击面。

**检查范围:**

| 序号 | 账号类型 | 获取方式 | AK 查询方式 |
|------|---------|---------|-------------|
| 1 | 主账号 | 当前凭证对应的根账号 | `ListAccessKeys`(不传 TargetUin) |
| 2 | 子用户(可登录控制台) | `ListUsers` → 筛选 `ConsoleLogin=1` | `ListAccessKeys --TargetUin {UIN}` |
| 3 | 协助者 | `ListCollaborators` | `ListAccessKeys --TargetUin {UIN}` |

**流程:**

```
1. 查主账号 AK → ListAccessKeys(不传 TargetUin)
       ↓
   有 AK → 🔴 风险(主账号不应直接使用 AK/SK)
       ↓
2. ListUsers 筛选 ConsoleLogin=1 的子用户
       ↓
   对每个 UIN → ListAccessKeys --TargetUin {UIN}
       ↓
   有 AK → ❌ 命中(人机未分离)
       ↓
3. ListCollaborators 获取所有协助者
       ↓
   对每个 UIN → ListAccessKeys --TargetUin {UIN}
       ↓
   有 AK → ❌ 命中(人机未分离)
```

**执行方法:** 由 `inspect_cam.py` 落盘 `inspect_cam.json` 中 `principals[].access_keys` / `root_aks` 字段。

**判定标准:**

| 风险等级 | 条件 | 处置 |
|---------|------|------|
| 🔴 严重 | 主账号持有 AK/SK | 立即迁移至子用户或 STS 临时凭证 |
| ❌ 高风险 | 可登录控制台的子用户/协助者持有 AK | 移除 AK 或禁用控制台登录(改为纯服务账号) |
| ✅ 合规 | 可登录控制台但无 AK,或禁止登录控制台的服务账号持有 AK | 无需处置 |

> **注意**:禁止登录控制台的子用户(`ConsoleLogin=0`)持有 AK 是正常的,这是标准的服务账号模式,不纳入本项审查范围。

---

### Administrator 权限审查方法

> **核心原则**:`AdministratorAccess` 是超级管理员策略,拥有该策略的账号可操作账号下所有资源。应严格控制数量,每个关联账号必须有合理理由。

**检查范围:**

| 类型 | 说明 |
|------|------|
| 策略直接关联的用户 | 直接绑定 AdministratorAccess 的子用户/协助者 |
| 策略关联的用户组 | 组内所有成员等效拥有 Administrator 权限 |
| 自定义策略 | 名称不叫 AdministratorAccess 但实际包含 `*:*` 全量权限 |

**流程:**

```
1. ListPolicies 搜索 "AdministratorAccess" 获取预设策略 PolicyId
       ↓
2. ListPolicies 遍历所有自定义策略 → GetPolicy 获取策略文档
       ↓
   检查 Statement 中是否包含 Action:"*" + Resource:"*" + Effect:"Allow"
       ↓
   命中 → 等效全量权限策略,纳入审查
       ↓
3. 对每个命中的策略 → ListEntitiesForPolicy 获取关联用户和用户组
       ↓
4. 对关联的用户组 → ListUsersForGroup 展开组内成员
       ↓
5. 汇总所有拥有全量权限的账号(去重),判定是否合理
```

**执行方法:** 由 `inspect_cam.py` 落盘 `inspect_cam.json` 中 `admin_grants` / `custom_admin_policies` 字段(预设 AdministratorAccess + 自定义等效全量权限 + 关联用户与用户组成员展开)。

**判定标准:**

| 风险等级 | 条件 | 处置 |
|---------|------|------|
| 🔴 严重 | 预设 AdministratorAccess + 自定义等效全量权限策略绑定的总用户数(去重) > 5 | 立即回收多余权限,改用最小权限策略;删除或缩小自定义策略范围 |
| ✅ 合规 | 总用户数(去重) ≤ 5,且均为核心运维人员 | 无需处置 |

> **自定义等效全量权限策略识别规则**:策略文档中存在任意 Statement 同时满足 `Effect: Allow` + `Action: "*"` + `Resource: "*"` 即判定为等效全量权限。这包括效果上等同于 AdministratorAccess 但名称不同的策略。

---

### 离职账号检测方法(两步 API + CloudAudit 交叉校验)

> **重要**:
> - `DescribeSubAccounts` 的 `LastLoginTime` 对 SSO/企微扫码登录的账号可能返回空值,导致误判为"从未登录"
> - **子用户必须用 `GetUser`**(按 Name 查询),其 `RecentlyLoginTime` 字段才是准确的登录时间数据源(含 SSO/企微扫码登录)
> - 协作者仍用 `DescribeSubAccounts` 作为 fallback(`GetUser` 不支持协作者)
> - 2026-05-11 实测发现: 营销账号下 garyyang/lingxinxia/joyzzzhang 被 `DescribeSubAccounts` 误判为"从未登录",`GetUser` 返回了正确的登录时间

| 步骤 | 子用户 | 协作者 |
|------|---------|--------|
| 获取登录时间 | `GetUser --Name {name}` → `RecentlyLoginTime`(✅ 含 SSO) | `DescribeSubAccounts` → `LastLoginTime`(⚠️ 可能不含 SSO) |
| 判定标准 | >90天未登录 = 疑似离职 | >90天未登录 = 疑似离职 |
| API 限频 | 20 QPS,并发控制在 4 路 + 重试 | 每批 50 个 UIN |

**流程:**

```
1. 调用 ListUsers
       ↓
获取所有子用户 UIN 列表
       ↓
按每批 50 个 UIN 分组
       ↓
2. 分批调用 DescribeSubAccounts
       ↓
提取 LastLoginTime / LastLoginIp
```

**执行方法:** 由 `inspect_cam.py` 落盘 `inspect_cam.json` 中 `principals[].last_login_time` / `last_login_scan_status` 字段(子用户 `GetUser.RecentlyLoginTime` 主源 + 协作者 `DescribeSubAccounts.LastLoginTime` fallback)。

**关键说明:**

| 步骤 | API | 说明 |
|------|-----|------|
| 1 | `cam.ListUsers` + `cam.ListCollaborators` | 获取所有可登录子用户 + 协助者的 UIN 列表 |
| 2 | `cam.DescribeSubAccounts`(`FilterSubAccountUin`) | 每批最多 50 个 UIN,返回真实的 LastLoginTime / LastLoginIp |

> `DescribeSubAccounts` 返回的 `LastLoginTime` 是控制台实际登录时间,比 `ListUsers` 中的字段更准确。
> 空值或 `0000-00-00 00:00:00` 表示从未登录过控制台。

### 巡检步骤

1. Read `$OUT_DIR/inspect_cam.json` 的 `principals[].last_login_time`,获取每个可登录账号的真实最后登录时间
2. 对超过 90 天未登录的账号:确认人员状态 → 禁用账号 → 通知 HR 确认
3. 检查 MFA 状态,对未开启 MFA 的账号发送整改通知
4. 搜索 AdministratorAccess 策略,核查每个关联账号是否有必要

### MFA 检测方法(两个 API)

> **重要**:`ListUsers` 和 `DescribeSubAccounts` 都不返回 MFA 状态字段,必须通过专门的安全认证接口获取。

| 对象 | API | 参数 |
|--------|-----|------|
| 主账号 | `cam.DescribeSafeAuthFlag` | 无参数 |
| 子账号/协助者 | `cam.DescribeSafeAuthFlagColl`(`SubUin`) | 每次只能查一个 UIN |

**返回字段说明:**

```json
{
  "LoginFlag": {       // 登录保护
    "Phone": 0,        // 手机验证码
    "Token": 0,        // 硬件 MFA Token
    "Stoken": 1,       // 软件 MFA (Stoken) ← 最常用
    "Wechat": 0,       // 微信扫码
    "U2FToken": 0      // U2F 安全密钥
  },
  "ActionFlag": { ... } // 敏感操作保护(同结构)
}
```

**判断标准:**
- LoginFlag 中 `Phone + Token + Stoken + Wechat + U2FToken` 全为 0 → ❌ 未开启任何 MFA
- 任意一项为 1 → ✅ 已开启 MFA
- `Stoken: 1` 是最常见的 MFA 类型(腐讯安全令牌)

**执行方法:** 由 `inspect_cam.py` 落盘 `inspect_cam.json` 中 `principals[].mfa` / `mfa_scan_status` 字段(主账号 `DescribeSafeAuthFlag` + 子账号/协助者 `DescribeSafeAuthFlagColl` 单 UIN 串扫)。

> **注意**:`DescribeSafeAuthFlagColl` 每次只能查询一个 UIN,账号多时请注意 API 调用频率。

---


## 1.2 AK/SK 闲置检查

| 检查项 | 操作方法 | 判断标准 | 异常处置 |
|--------|---------|---------|----------|
| AK 闲置检测 | ListAccessKeys + GetSecurityLastUsed(见下方) | >90天未调用 = 闲置 | 先禁用,确认无影响后删除 |

### AK/SK 闲置检测方法(两步 API)

> **重要**:不需要查云审计,CAM 提供了 `GetSecurityLastUsed` 接口直接返回每个 AK 的最近使用日期。

**流程:**

```
1. 对每个子账号 + 协助者调用 ListAccessKeys --TargetUin {UIN}
       ↓
收集所有 AccessKeyId
       ↓
2. 批量调用 GetSecurityLastUsed (file://json)
       ↓
获取 LastUsedDate → 计算闲置天数
```

> **注意**:`ListAccessKeys` 对子账号和协助者均有效,必须同时遍历两者(`ListUsers` + `ListCollaborators`)确保全量覆盖。

**关键说明:**

| 步骤 | API | 说明 |
|------|-----|------|
| 1 | `cam.ListAccessKeys`(`TargetUin`) | 遍历子账号 + 协助者,字段名为 `AccessKeyId`(不是 SecretId) |
| 2 | `cam.GetSecurityLastUsed`(`SecretIdList`) | JSON: `{"SecretIdList":["AKID..."]}` 每批≤10个 |

> `LastUsedDate` 格式为 `YYYY-MM-DD`,空值表示从未使用。

**判定标准:**
- >90天未使用 → 🔴 建议先禁用,确认无影响后删除
- 30-90天未使用 → 🟡 关注
- 从未使用 → ⚠️ 确认是否通过 SDK 调用(新创建的 AK 可能尚未记录)

### ⚠️ 注意事项

- 严禁将 AK/SK 硬编码在代码中
- 严禁明文传输 AK/SK
- 推荐使用 STS 临时凭证替代永久密钥
