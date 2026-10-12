# CodeBuddy CloudOps 专家团 应用指南

> **适用对象**: 已完成环境搭建的用户  
> **前置条件**: 已安装 `cloudops-team-pro` 插件 v2.0.0  
> **最后更新**: 2026-06-29

---

## 目录

- [一、快速上手：5 分钟学会召唤专家](#一快速上手5-分钟学会召唤专家)
- [二、8 大专家详解](#二8-大专家详解)
- [三、实战场景手册](#三实战场景手册)
- [四、Python 脚本自动化调用](#四python-脚本自动化调用)
- [五、输出结果解读](#五输出结果解读)
- [六、高级技巧](#六高级技巧)
- [七、常见问题 FAQ](#七常见问题-faq)

---

## 一、快速上手：5 分钟学会召唤专家

### 1.1 启动交互模式

```bash
# 进入 CodeBuddy 交互模式
codebuddy

# 你将看到提示符: >
```

### 1.2 召唤专家的 3 种方式

#### ✅ 方式一：自然语言（最简单，推荐）

**不需要任何特殊格式，直接说人话就行：**

```
> 分析一下我这个月的腾讯云账单
> 帮我做一次安全巡检
> 看看有没有闲置的 CVM 可以省钱
> 查一下这周谁改过安全组
> 列出我所有的云资源清单
> 画一下我的云架构拓扑图
```

CodeBuddy 会根据关键词**自动匹配**最合适的专家。

#### ✅ 方式二：指定云厂商（精确路由）

```
# 腾讯云账单
> 分析我的腾讯云费用趋势和环比

# 华为云账单（自动派 huawei-billing-analyst-pro）
> 分析我的华为云月度账单
```

#### ✅ 方式三：Python 脚本批量调用（自动化场景）

```python
from codebuddy_cli_wrapper import quick_ask

result = quick_ask("对腾讯云账号做全面安全巡检")
print(result.output)
```

### 1.3 关键词 → 专家映射表

| 你说的话 | 召唤的专家 |
|---------|-----------|
| 账单 / 费用 / 成本 / 花费 / 开销 | `cloud-billing-analyst-pro` |
| 华为云账单 / 华为费用 | `huawei-billing-analyst-pro` |
| 巡检 / 安全检查 / 合规 / 漏洞 | `cloud-inspector-pro` |
| 审计 / 日志 / 谁操作的 / 高危事件 | `cloud-auditor-pro` |
| 降本 / 省钱 / 闲置 / 优化 / 规格 | `cloud-cost-optimizer-pro` |
| 资源清单 / 有多少 / 盘点 / 列出 | `cloud-resource-manager-pro` |
| 架构图 / 拓扑 / 评估 / 健康度 | `cloud-architect-pro` |
| 复杂任务（涉及多个领域）| **云掌柜** (`cloudops-team-pro-team-lead`) 自动协调 |

---

## 二、8 大专家详解

### 🧑‍💼 1. 云掌柜 — 主理人/调度员 (Team Lead)

**ID**: `cloudops-team-pro-team-lead`
**花名**: 云掌柜 | **职业**: 云运维经理
**角色**: AIOps 智能运维总调度——不是干活的，是**派活的**。你只管说需求，它自动识别意图、选账号、派专家、汇总结果。

```
什么时候触发:
- 当你的需求涉及多个领域时（"全面体检""账单+巡检都看看"）
- 当你不确定该找哪个专家时
- 多账号场景时自动弹选择器（单云多项目/多云切换）

示例:
> 我要做一个全面的云运营月报（账单+安全+资源+架构）
> 帮我从头到尾梳理一下我们云上存在的问题
> 对比一下所有项目上月账单（自动并发跑多个）
```

**工作原理**:
```
你说需求 → 云掌柜判定语言(中/英) → 账号选择仲裁(Layer 1→2→3)
         → 意图识别路由表 → spawn 1-N 个专业专家(并行)
         → 收齐结果 → 重写AIOps概览(≤600字) → 追加PPT引导语 → 交付你
```

**核心职责（不只是转发）**：
- **输出语言仲裁**：中文问中文答，英文问英文答，对话+看板+PPT 三者语言一致
- **三层短路账号选择**：关键词直派(0步) → 唯一性直派(0步) → 弹选择框
- **防遮挡铁律**：并行任务必须收齐全部结果才结束回合，绝不提前终止
- **凭证安全红线**：绝不向用户索取 AK/SK，不编造样例值，不诱导贴密钥

---

### 📊 2. 老账 — 腾讯云账单分析师

**ID**: `cloud-billing-analyst-pro`
**花名**: 老账 | **职业**: 云账单分析专家
**核心能力**: 腾讯云账单深度分析与可视化（只解读事实，不做降本建议）

#### 能做什么？

| 功能 | 说明 | 输出格式 |
|------|------|---------|
| 月度账单分析 | 按产品/地域/项目维度拆解费用 | HTML 数据看板 |
| 费用趋势分析 | 同比/环比变化，异常波动检测 | Markdown + 图表 |
| 成本分摊 | 按部门/业务线/环境(Dev/Test/Prod)分摊 | Excel/CSV |
| 预算告警 | 设置阈值，超支提醒 | 即时通知 |
| 闲置资源费用统计 | 识别"买了但没用"的资源浪费 | 降本建议报告 |

#### 使用示例

```
基础查询:
> 分析这个月的账单，看看哪些产品花钱最多

进阶查询:
> 对比最近 3 个月的费用趋势，找出异常增长的产品

深度分析:
> 按项目维度分析 Q2 的云成本，给出各部门分摊明细
```

#### 输出示例

```
📊 腾讯云账单分析报告 (2026-06)

━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 总费用: ¥142,356.78
📈 环比上月: +12.3% ⚠️
🏷️ 同比去年: -5.7% ✅

TOP 5 费用产品:
1. CVM 云服务器       ¥58,200  (40.9%)
2. COS 对象存储       ¥32,100  (22.5%)
3. CLB 负载均衡       ¥18,500  (13.0%)
4. CDB MySQL          ¥15,800  (11.1%)
5. CDN 内容分发       ¥12,400  (8.7%)

⚠️ 异常发现:
- CVM 费用环比增长 23%，新增 5 台高配实例
- COS 流量费激增 45%，可能存在大文件频繁下载

📁 完整报告已生成: reports/billing-2026-06.html
```

---

### 🌩️ 3. 华为云账单分析师 (Huawei Billing Analyst)

**ID**: `huawei-billing-analyst-pro`
**花名**: 华账 | **职业**: 华为云账单分析专家
**核心能力**: 华为云 BSS API 直连分析（也走 SSM 凭证！）

#### 与腾讯云版(老账)的区别

| 特性 | 老账（腾讯云） | 华账（华为云） |
|------|--------------|---------------|
| 数据来源 | 腾讯云 DescribeBillSummary API | 华为云 BSS ShowCustomerMonthlySum API |
| 认证方式 | SSM (`cloudops/tencent/{项目}`) | **也是 SSM** (`cloudops/huawei/{项目}`) |
| 支持账户 | UIN 多账户 | domain_id 多账户 |
| 特色功能 | 标签分账(head/business维度) | 企业项目维度分账 + 国际站支持 |
| 分账维度 | 按标签/产品/地域/计费模式 | 按云服务类型/企业项目/地域 |

> **重要**：华账的凭证**不是**直接设 `HUAWEICLOUD_SDK_AK/SK` 环境变量，而是与老账一样统一存在腾讯云 SSM 中（路径前缀 `cloudops/huawei/`），用同一把 runner key 读取。

#### 使用示例

```
> 分析华为云本月账单，按企业项目维度拆解
> 华为云 ModelArts 这个月花了多少钱？
> 对比华为云两个月的费用变化
```

---

### 🛡️ 4. 云巡检 — 安全巡检工程师

**ID**: `cloud-inspector-pro`
**花名**: 云巡检 | **职业**: 云安全巡检工程师
**核心能力**: 12 类云资源安全合规全覆盖（SOP TC-SEC-SOP-001 V1.0 标准）

#### 巡检范围（12 域）

```
┌─────────────────────────────────────┐
│  🔐 IAM 身份与权限                  │  ← AK/SK/IAM策略/R角色
│  🌐 网络 Security Group             │  ← 安全组规则/端口暴露
│  ☁️ CVM 云服务器                    │  ← 安全组绑定/公网IP/镜像过期
│  🪣 COS 对象存储                    │  ← 公开读/加密/版本控制
│  🗄️ 数据库 (CDB/Redis)             │  ← 公网访问/备份/SSL/TDE
│  ⚖️ CLB 负载均衡                   │  ← SSL证书/协议版本/安全组
│  🐳 TKE 容器集群                    │  ← API Server暴露/版本/RBAC
│  🔧 中间件                          │  ← Kafka/RabbitMQ/ES 安全配置
│  🔑 密钥管理 (KMS/SecretsManager)   │  ← 密钥轮换/访问审计
│  📋 日志服务 (CLS)                  │  ← 日志保留/实时投递
│  ⚖️ 合规基线                        │  ← CIS/等保2.0/最佳实践
│  🚨 应急响应                         │  ← 高危漏洞修复SLA
└─────────────────────────────────────┘
```

#### 4 种巡检模式

| 模式 | 触发关键词 | 说明 |
|------|-----------|------|
| **full** (全量巡检) | "全面巡检"、"扫一下账号"、"出报告" | 覆盖所有 12 域，输出 HTML 报告 |
| **narrow** (单域查询) | "看看 SSL"、"检查 SG"、"列 AK" | 只查指定单一资源 |
| **narrow_forensic** (应急取证) | "谁删了 COS"、"上周改了什么" | 定向查询单一事件 |
| **sla_query** (纯问答) | "AK 闲置多久"、"漏洞修多久" | 不跑扫描，只答知识 |

#### 使用示例

```
全量巡检:
> 对我的腾讯云账号做一次全面安全巡检
> 出一份本周的安全合规报告

单域查询:
> 看看我的安全组有没有开放危险端口
> 列出所有超过90天没用的 Access Key
> 检查 SSL 证书还有多少天过期

应急取证:
> 查一下过去7天有没有人删除过 COS 存储桶
> 谁改过 IAM 策略？给我列出操作记录

纯问答:
> AK 的闲置阈值是多少天才算风险？
> 安全组开放 22 端口有什么风险？
```

#### 巡检等级说明

```
🔴 CRITICAL (严重): 立即修复，已被利用的高风险
🟠 HIGH (高):     24小时内修复，易被利用
🟡 MEDIUM (中):   7天内修复，需关注
🔵 LOW (低):      建议修复，最佳实践
⚪ INFO (信息):   无风险，仅供参考
```

---

### 🔍 5. 云审计官 — 审计分析师

**ID**: `cloud-auditor-pro`
**花名**: 云审计官 | **职业**: 云审计分析师
**核心能力**: CloudAudit 高危事件流扫描（9大类43事件 + 服务账号自动降噪）

#### 覆盖的 9 大类 43 种高危事件

```
类别 1: 审计篡改 (Audit Tampering)
  - DeleteMultiLogSets, ModifyLogSetTimestamp, etc.

类别 2: AK 生命周期 (AK Lifecycle)
  - CreateAccessKey, DeleteAccessKey, UpdateAccessKey

类别 3: 权限授予 (Permission Grants)
  - AttachUserPolicy, AttachRolePolicy, CreatePolicy, etc.

类别 4: 资源销毁 (Resource Destruction)
  - DeleteBucket, TerminateInstances, DeleteCluster, etc.

类别 5: KMS 操作 (密钥管理)
  - ScheduleKeyDeletion, Decrypt, Encrypt, etc.

类别 6: MFA 管理
  - UnbindMFADevice (高风险!)

类别 7: 用户 CRUD
  - CreateUser, DeleteUser, AddUserToGroup

类别 8: 角色变更
  - CreateRole, DeleteRole, UpdateAssumeRolePolicy

类别 9: 安全组变更
  - CreateSecurityGroup, RevokeSecurityGroup, etc.
```

#### 使用示例

```
> 扫描最近30天的所有高危操作
> 查一下谁删过 CVM 实例
> 有没有人创建过新的管理员策略？
> 给我做个审计日志合规检查
```

---

### 💰 6. 云降本医生 — 成本优化专家

**ID**: `cloud-cost-optimizer-pro`
**花名**: 云降本医生 | **职业**: 云降本优化专家
**核心能力**: 账单驱动 + 30天监控数据 + 风险分级降本建议（不拍脑袋，用监控数据说话）

#### 优化维度

| 维度 | 检测内容 | 典型节省比例 |
|------|---------|------------|
| **闲置实例** | CPU<5% 且无网络流量 30 天 | 30-70% |
| **规格过度** | 实际利用率 < 配置 20% | 20-50% |
| **未使用磁盘** | 未挂载 CBS/快照过多 | 10-30% |
| **闲置 IP** | 未绑定 CVM/NAT 的 EIP | 100%（可直接释放）|
| **预留实例** | 稳定负载可转 RI/CVR | 30-60% |
| **存储分层** | 冷数据应转归档存储 | 50-80% |

#### 使用示例

```
> 帮我找找有哪些闲置资源可以省钱
> 分析一下哪些 CVM 规格过大，可以降配
> 给出具体的降本建议，要有数据支撑
> 我的 Redis 有没有可以缩容的？
```

#### 输出结构

```
💰 云成本优化建议报告
═════════════════════════════

📊 总览:
  潜在月节省: ¥23,456 (16.5%)
  发现问题数: 12 项

🔴 高优先级 (立即行动):
  1. [闲置 CVM] i-xxxxx - 月省 ¥2,100 (CPU<3%, 45天)
  2. [规格过度] i-yyyy - 4核8G→2核4G, 月省 ¥850
  3. [闲置 EIP] eip-zzzz - 未绑定, 直接释放 ¥180/月

🟡 中优先级 (计划执行):
  4. [CBS 闲置] disk-aaa - 未挂载, 考虑删除
  5. [预留实例] 符合 CVR 条件, 年省 ¥15,000

📁 完整报告: reports/cost-opt-2026-06.html
```

---

### 📦 7. 云管家 — 资源管理专家

**ID**: `cloud-resource-manager-pro`
**花名**: 云管家 | **职业**: 云资源管理专家
**核心能力**: 全账号跨地域资源清点与盘点（20+ 类资源，只读不写，不画架构图）

#### 盘点范围

```
✅ 子账号列表 (CAM Users)
✅ CVM 实例 (含 GPU 实例)
✅ TKE 集群及节点规格
✅ COS 存储桶
✅ CBS 云硬盘
✅ CLB 负载均衡
✅ CDB MySQL/PostgreSQL
✅ Redis/Memcached
✅ 弹性公网 IP (EIP)
✅ VPC 私有网络
✅ 安全组 (Security Group)
✅ 轻量应用服务器 Lighthouse
```

#### 使用示例

```
> 列出我账号里所有的云资源
> 我有多少台 CVM？分别是什么配置？
> 有几个 COS Bucket？各自多大？
> 统计一下各地域的资源分布情况
```

#### 输出示例

```
📦 腾讯云资源清单
═════════════════════════════

📍 地域分布:
  ap-shanghai (上海):    45 资源
  ap-guangzhou (广州):   32 资源
  ap-beijing (北京):     18 资源

☁️ CVM 实例: 12 台
  ├─ 运行中: 10 台
  ├─ 已停止:  2 台 (⚠️ 仍在计费!)
  └─ GPU 实例: 2 台 (GN7/GN10x)

🗄️ 数据库:
  ├─ CDB MySQL:  5 实例
  └─ Redis:      3 实例

🪣 存储:
  ├─ COS 桶:     8 个 (总计 2.3TB)
  └─ CBS 磁盘:   15 块 (总计 5TB)

📁 完整清单: resources-inventory.html
```

---

### 🏗️ 8. 云图 — 架构分析师

**ID**: `cloud-architect-pro`
**花名**: 云图 | **职业**: 云架构分析师
**核心能力**: 分层架构拓扑可视化 + 架构健康度评估（A~F 评级，基于真实数据推理）

#### 能力一览

| 功能 | 说明 |
|------|------|
| **架构拓扑图** | 自动生成分层架构图 (访问层→计算层→数据层→存储层→网络层) |
| **健康度评分** | 0-100 分 + A~F 等级评级 |
| **多维评估** | HA/高可用、多区域容灾、弹性伸缩、成本健康、数据可靠性 |
| **风险识别** | SPOF 单点故障、缺乏冗余、安全暴露面等 |
| **演进建议** | 从当前状态到目标架构的迁移路径 |

#### 使用示例

```
> 画一下我的云架构拓扑图
> 评估一下我的架构健壮性
> 我的架构有什么风险点？
> 给出架构优化的建议
```

#### 评估等级说明

```
A (90-100): 优秀 - 企业级生产就绪
B (80-89):  良好 - 有少量改进空间
C (70-79):  一般 - 需要关注的风险
D (60-69):  及格 - 存在明显隐患
E (50-59):  不佳 - 需要紧急改进
F (<50):    危险 - 不适合生产环境
```

---

## 三、实战场景手册

### 场景 1: 每日晨会 - 快速了解云状态

```
> 给我一个今天云资源的概况摘要，包括：
>   1. 昨天的费用概览
>   2. 有没有新的高危操作
>   3. 资源总数和运行状态
```

**预期输出**: 一页纸的 Executive Summary，适合汇报领导

---

### 场景 2: 月底财务 - 费用分析报告

```
> 生成本月的完整云账单分析报告，要求：
>   1. 按产品线拆解费用
>   2. 与上月和去年同期对比
>   3. 标注异常增长项并分析原因
>   4. 给出下个月的预算建议
>   5. 输出 HTML 格式看板
```

**预期输出**: 包含图表的 HTML 看板文件，可直接在浏览器打开展示

---

### 场景 3: 季度审计 - 合规检查

```
> 做一次季度安全合规审计，包括：
>   1. 全量安全巡检 (12个域)
>   2. 近90天的高危操作审计
>   3. IAM 权限最小化检查
>   4. 识别不符合等保2.0要求的项
>   5. 生成 PPT 格式的审计报告
```

**预期输出**: HTML 详细报告 + PPT 汇报材料

---

### 场景 4: 成本优化专项 - 降本落地

```
> 我们要削减 20% 的云成本，帮我：
>   1. 扫描所有闲置资源
>   2. 识别规格过度的实例
>   3. 评估预留实例可行性
>   4. 给出具体的降本 Action List
>   5. 估算每个 action 的节省金额
```

**预期输出**: 按优先级排序的可执行清单，每条都有预估收益

---

### 场景 5: 新项目上线 - 架构评审

```
> 我要上线一个新的电商微服务项目，帮我：
>   1. 画出推荐的云架构拓扑图
>   2. 评估高可用和容灾方案
>   3. 给出各组件的推荐规格
>   4. 估算首月成本
>   5. 列出安全 Checklist
```

**预期输出**: 架构图 + 评审意见 + 成本预估 + 安全 CheckList

---

### 场景 6: 故障排查 - 应急响应

```
> 今天上午 10 点左右我们的 API 响应变慢了，帮我：
>   1. 查一下那个时间段有没有人动过资源
>   2. 检查 CVM 的 CPU/内存使用率趋势
>   3. 看看数据库连接池是不是满了
>   4. 分析 CLB 是否有异常流量
>   5. 给出可能的原因和解决方案
```

**预期输出**: 时间线还原 + 根因分析 + 处置建议

---

## 四、Python 脚本自动化调用

### 4.1 基础调用

```python
#!/usr/bin/env python3
"""CloudOps 专家自动化调用示例"""

import sys
sys.path.insert(0, '/Users/lenon/Documents/code buddy/cloud ops')

from codebuddy_cli_wrapper import (
    CodeBuddyCLI,
    CodeBuddyConfig,
    PermissionMode,
    quick_ask
)


def example_basic():
    """基础调用"""
    
    # 一行搞定
    result = quick_ask("分析腾讯云本月账单")
    print(result.output)


def example_with_config():
    """带配置的调用"""
    
    config = CodeBuddyConfig(
        work_dir="/Users/lenon/Documents/code buddy/cloud ops",
        permission_mode=PermissionMode.AUTO,  # 自动接受文件写入
    )
    
    cli = CodeBuddyCLI(config)
    
    result = cli.ask("做一次全量安全巡检", timeout=300)  # 5分钟超时
    
    if result.success:
        print(f"✅ 完成，耗时 {result.elapsed_time:.1f}秒")
        print(result.output)
    else:
        print(f"❌ 失败: {result.error}")


if __name__ == "__main__":
    example_basic()
```

### 4.2 批量巡检（定时任务）

```python
#!/usr/bin/env python3
"""每日自动巡检脚本 - 可配合 crontab/scheduler"""

import json
import sys
from datetime import datetime
sys.path.insert(0, '/Users/lenon/Documents/code buddy/cloud ops')

from codebuddy_cli_wrapper import CodeBuddyCLI, CodeBuddyConfig


def daily_cloud_ops_check():
    """每日云运维巡检"""
    
    config = CodeBuddyConfig(
        permission_mode='auto',
    )
    cli = CodeBuddyCLI(config)
    
    today = datetime.now().strftime("%Y-%m-%d")
    report = {
        'date': today,
        'checks': {}
    }
    
    # 1. 账单快速检查
    print("[1/4] 正在检查账单...")
    r1 = cli.ask("简述今天的云费用概况，一句话总结", timeout=120)
    report['checks']['billing'] = {
        'status': 'success' if r1.success else 'failed',
        'summary': r1.output[:200] if r1.success else r1.error
    }
    
    # 2. 安全事件检查
    print("[2/4] 正在检查安全事件...")
    r2 = cli.ask("近24小时有没有高危操作？简单列举", timeout=120)
    report['checks']['security'] = {
        'status': 'success' if r2.success else 'failed',
        'summary': r2.output[:200] if r2.success else r2.error
    }
    
    # 3. 闲置资源检查
    print("[3/4] 正在检查闲置资源...")
    r3 = cli.ask("快速检查有没有明显的闲置资源", timeout=120)
    report['checks']['cost'] = {
        'status': 'success' if r3.success else 'failed',
        'summary': r3.output[:200] if r3.success else r3.error
    }
    
    # 4. 资源数量快照
    print("[4/4] 正在统计资源数量...")
    r4 = cli.ask("简要列出各类资源数量，表格形式", timeout=120)
    report['checks']['inventory'] = {
        'status': 'success' if r4.success else 'failed',
        'summary': r4.output[:300] if r4.success else r4.error
    }
    
    # 保存报告
    report_path = f"/Users/lenon/Documents/code buddy/cloud ops/reports/daily-{today}.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 巡检完成！报告保存至: {report_path}")
    return report


if __name__ == "__main__":
    daily_cloud_ops_check()
```

### 4.3 月度综合报告

```python
#!/usr/bin/env python3
"""月度云运营综合报告生成器"""

import os
import sys
from datetime import datetime
sys.path.insert(0, '/Users/lenon/Documents/code buddy/cloud ops')

from codebuddy_cli_wrapper import CodeBuddyCLI, CodeBuddyConfig


def generate_monthly_report():
    """生成年月度综合报告"""
    
    cli = CodeBuddyCLI(CodeBuddyConfig(permission_mode='auto'))
    
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    report_dir = f"/Users/lenon/Documents/code buddy/cloud ops/reports/{month_str}"
    os.makedirs(report_dir, exist_ok=True)
    
    tasks = [
        ("billing", "生成本月完整账单分析报告，HTML格式"),
        ("security", "做一次全量安全巡检，HTML报告格式"),
        ("audit", "扫描本月所有CloudAudit高危事件"),
        ("costopt", "分析闲置资源和降本机会，输出HTML看板"),
        ("inventory", "生成全账号资源清点清单"),
        ("architecture", "绘制架构拓扑图并进行健康度评估"),
    ]
    
    results = {}
    
    for task_id, prompt in tasks:
        print(f"\n▶️ [{task_id.upper()}] {prompt[:40]}...")
        
        result = cli.ask(prompt, timeout=600)  # 10分钟
        
        results[task_id] = {
            'success': result.success,
            'output': result.output[:500] if result.success else None,
            'error': result.error if not result.success else None,
            'elapsed': result.elapsed_time,
        }
        
        status = "✅" if result.success else "❌"
        print(f"  {status} {result.elapsed_time:.1f}s")
    
    # 生成摘要
    summary_path = f"{report_dir}/SUMMARY.md"
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(f"# 云运营月报 - {month_str}\n\n")
        
        for task_id, data in results.items():
            status = "✅ 通过" if data['success'] else "❌ 失败"
            f.write(f"## {task_id.upper()} - {status}\n\n")
            
            if data['output']:
                f.write(f"{data['output']}\n\n")
            else:
                f.write(f"错误: {data['error']}\n\n")
        
        f.write(f"\n---\n*生成时间: {now.strftime('%Y-%m-%d %H:%M')}*\n")
    
    print(f"\n📊 月报生成完成！查看: {report_dir}")
    return results


if __name__ == "__main__":
    generate_monthly_report()
```

---

## 五、输出结果解读

### 5.1 输出格式

| 格式 | 触发方式 | 用途 |
|------|---------|------|
| **Markdown 文本** | 默认输出 | 终端阅读，快速获取结论 |
| **HTML 看板** | 明确要求 "HTML/看板/仪表盘" | 浏览器打开，含图表交互 |
| **PPT 幻灯片** | 说 "导出PPT/汇报材料/给老板看的" | 会议演示 |
| **JSON 结构化** | Python 中用 `ask_json()` | 程序二次处理 |

### 5.2 HTML 看板特点

当专家生成 HTML 报告时，通常具有以下特征：

```
📁 输出位置: ./reports/<类型>/<日期>.html
   例: reports/billing/2026-06-29.html
       reports/security/2026-06-29.html

🎨 视觉风格:
   - 深色玻璃拟态 (Dark Glass-morphism)
   - Chart.js 动态图表
   - 响应式布局，支持移动端浏览
   
📊 包含内容:
   - KPI 核心指标卡
   - 趋势折线图/柱状饼图
   - 明细数据表 (支持排序/筛选)
   - 风险标注和颜色编码
```

### 5.3 如何打开 HTML 报告

```bash
# macOS
open reports/billing/2026-06.html

# Windows
start reports/billing/2026-06.html

# 或者在 CodeBuddy 中直接预览（如果 IDE 支持）
```

---

## 六、高级技巧

### 6.1 上下文记忆（Context Memory）

首次使用某专家时，它会要求创建 `user_cloud_context.md` 来记住你的账号信息：

```
Do you want to create user_cloud_context.md?
> 1. Yes
> 2. Yes, and don't ask again this session
> 3. No
```

**选 1 或 2**，之后就不需要重复说明账号信息了。

上下文文件内容示例：
```markdown
---
name: User cloud ops context
description: Manages multi-cloud billing/cost for Company ABC
type: user
---

Company uses Tencent Cloud and Huawei Cloud.
Primary account: MAIN (domain_id: 5dxxxx)
Monthly spend: ~¥1.4M
52+ cloud services with heavy AI/ModelArts usage.
Communicates in Chinese.
```

### 6.2 组合多个专家

```
复杂需求示例:
> 我要做一份给CTO看的季度汇报，包括：
>   (1) 本季度的费用趋势和预算达成
>   (2) 安全状况和重大事件
>   (3) 降本成果和下一步计划
>   (4) 架构演进和未来规划
>   
>   请协调各领域专家一起完成，最终输出 PPT
```

Team Lead 会自动把任务拆分给：
- Billing Analyst → 负责 (1)(3)
- Inspector/Auditor → 负责 (2)
- Architect → 负责 (4)
- 最后汇总成 PPT

### 6.3 追问与深入

得到初步结果后，可以继续追问：

```
> 上面的账单分析中，CVM 费用增长 23% 是为什么？
> 能不能进一步列出那 5 台新增实例的详情？
> 安全巡检发现的 HIGH 级别问题，怎么修复？给我具体命令
> 这个架构评估给了 C 级，主要扣分项在哪里？
```

### 6.4 使用模板提高效率

如果你经常重复相同的查询，可以做成模板：

```python
# my_templates.py
TEMPLATES = {
    "daily_check": "每日云资源快速巡检，包括：费用摘要、安全事件、资源数量",
    "weekly_report": "本周云运营周报，包括：费用环比、安全事件汇总、资源变化、待处理事项",
    "monthly_billing": "{month} 月完整账单分析，HTML 看板格式，含同比环比",
    "security_audit": "全量安全巡检，12域覆盖，输出 HTML 报告",
    "cost_optimization": "全面成本优化分析，识别闲置资源，给出可执行的降本 Action List",
}

def run_template(name, **kwargs):
    prompt = TEMPLATES[name].format(**kwargs)
    return quick_ask(prompt)

# 使用
run_template("daily_check")
run_template("monthly_billing", month="2026-06")
```

---

## 七、常见问题 FAQ

### Q1: 专家说"无法访问云资源"，怎么办？

**A:** `cloudops-team-pro` 使用 **SSM + KMS 分层信任模型**（不是传统的 `tccli configure` 或 `TENCENTCLOUD_SECRET_ID`）。按以下顺序排查：

```bash
# ① 检查 BOOTSTRAP 变量（runner key）是否生效
echo "BOOTSTRAP_ID: ${TENCENTCLOUD_BOOTSTRAP_SECRET_ID:-❌ 未设置}"
echo "BOOTSTRAP_KEY: ${TENCENTCLOUD_BOOTSTRAP_SECRET_KEY:+✅ 已设置}"
echo "BOOTSTRAP_KEY: ${TENCENTCLOUD_BOOTSTRAP_SECRET_KEY:-❌ 未设置}"
echo "REGION: ${TENCENTCLOUD_BOOTSTRAP_REGION:-ap-guangzhou(默认)}"

# ② 检查标准配置文件是否存在
ls -la ~/.workbuddy/secrets/cloudops.env 2>/dev/null || echo "⚠️ 标准配置文件不存在"

# ③ 如果变量没值但文件存在 → source 一下
source ~/.workbuddy/secrets/cloudops.env

# ④ 如果文件也不存在 → 需要创建
# 参考《环境搭建指南》第六章完整步骤
```

**常见原因对照表**：

| 现象 | 原因 | 解决方法 |
|------|------|---------|
| `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` 为空 | 未配置 runner key | 创建 `~/.workbuddy/secrets/cloudops.env` 并 source |
| 变量有值但仍报错（腾讯云） | SSM 中无对应 `cloudops/tencent/{项目}` 的密钥 | 在腾讯云 SSM 控制台创建密钥（见指南第七章）|
| 华为云专家报凭证错误 | 你直接设了 `HUAWEICLOUD_SDK_AK/SK`？| **华为云也走 SSM！** 需在 SSM 创建 `cloudops/huawei/{项目}` 密钥 |
| `exit code 5` | 专家脚本启动时读不到环境变量 | 确认已 `source cloudops.env`，或重开终端 |
| SSM AccessDenied | runner key 权限不足 | CAM 策略需包含: `ssm:GetSecretValue` + `ssm:ListSecrets` + `ssm:ListSecretVersionIds` |
| 刚配完还是不行 | 当前终端未刷新 | 重开终端窗口或执行 `source ~/.zshrc` |

> 💡 **记忆口诀**: "一 key 进宿主，**三云全进 SSM**"——本地只放一把 runner key，腾讯云/华为云/阿里云的业务 AK/SK 全在腾讯云 SSM 里加密存着（含华为云！）。

### Q2: 不同专家的结果矛盾怎么办？

**A:** 正常现象。不同专家视角不同：
- Cost Optimizer 说"这台 CVM 闲置该关"
- Security Inspector 说"这台是跳板机不该关"

此时让**云掌柜**协调裁决：
```
> 关于 instance-xxx，成本专家说闲置建议释放，但安全专家说是跳板机。
> 请综合评估，给出最终建议。
```

### Q3: 分析太慢了，怎么加速？

**A:** 取决于因素：

| 因素 | 建议 |
|------|------|
| 任务范围太大 | 缩小范围："只看 CVM，不要其他资源" |
| 数据量太多 | 限定时间范围："只看最近7天" |
| 输出格式复杂 | 先要 Markdown 摘要，再单独要 HTML |
| 网络延迟 | 检查到腾讯云 API 的网络连通性 |

### Q4: 可以同时问多个专家吗？

**A:** 在交互模式下不行（串行）。但在 Python 中可以用多线程：

```python
from concurrent.futures import ThreadPoolExecutor
from codebuddy_cli_wrapper import quick_ask

queries = [
    "简述今日费用",
    "检查高危操作",
    "统计资源数量",
]

with ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(quick_ask, queries))

for r in results:
    print(r.output)
```

> ⚠️ 注意：并发可能触发 API Rate Limit，建议间隔 2-3 秒

### Q5: 如何把结果分享给同事？

**A:** 

- **HTML/PPT 文件**: 直接发送文件
- **Markdown 文本**: 粘贴到飞书/钉钉/企微文档
- **截图**: 终端截图（不太推荐，不可搜索）
- **集成飞书机器人**: 用 Webhook 推送（需要自行开发）

### Q6: 专家的分析准确吗？

**A:** 基于**真实的云 API 数据**，不是编造的。但：

- ✅ 准确的：资源数量、费用金额、操作记录、配置状态
- ⚠️ 供参考的：优化建议、风险评估、架构评级（基于最佳实践的推断）
- ❌ 不能替代的：人工决策、业务上下文判断、合规审批流程

**建议**: 专家报告作为决策依据之一，关键操作仍需人工审核。

### Q7: 支持多云吗？

**A:** 目前支持的厂商：

| 厂商 | 支持程度 | 专家 |
|------|---------|------|
| 腾讯云 | ✅ 完整 | 6/8 专家 |
| 华为云 | ⚠️ 账单为主 | 1 专家 (huawei-billing) |
| AWS | ❌ 暂不支持 | - |
| Azure | ❌ 暂不支持 | - |
| 阿里云 | ❌ 暂不支持 | - |

后续版本可能会增加更多厂商支持。

### Q8: 数据安全吗？我的云数据会不会泄露？

**A:** 安全机制：

1. **凭证本地存储**: API Key 存在你本机的 SSM/环境变量，不上传
2. **直连云 API**: 数据从腾讯云 API → 本地处理 → 本地展示，不经第三方
3. **上下文文件可选**: `user_cloud_context.md` 保存在本地 `.codebuddy/memory/`
4. **不记录日志**: 默认不会把你的云资源信息上传到任何服务器

> 但要注意：不要把包含敏感信息的报告文件提交到公开 Git 仓库！

---

## 附录：专家能力矩阵速查表

```
                     账单  安全  审计  成本  资源  架构  华为
Billing Analyst      ★★★  ☆    ☆    ★★   ★    ☆    ☆
Huawei Billing       ☆    ☆    ☆    ★★   ☆    ☆    ★★★
Security Inspector   ☆    ★★★  ★★   ★    ★    ★    ☆
Auditor              ☆    ★★   ★★★  ☆    ★    ☆    ☆
Cost Optimizer       ★★   ★    ☆    ★★★  ★★★  ★    ☆
Resource Manager     ★    ★    ★    ★    ★★★  ★★   ☆
Architect            ☆    ★★   ☆    ★★   ★★   ★★★  ☆
Team Lead            ★★   ★★   ★★   ★★   ★★   ★★   ★

★★★ 核心专长   ★★ 可以做   ★ 基础能力   ☆ 不涉及
```

---

## 附录：常用 Prompt 模板库

复制即用的 Prompt 集合：

```text
=== 账单类 ===

[简报]
给我今天的云费用一句话摘要

[详细分析]
分析本月账单：按产品拆解费用，标出 TOP5，指出异常增长项，HTML 看板

[对比]
对比最近3个月的费用趋势，用表格呈现

[预测]
基于历史数据预测下个月的费用，考虑即将上线的项目

=== 安全类 ===

[快速检查]
快速安全检查：只看高危和中危问题，忽略 LOW/INFO

[全量巡检]
全量安全巡检：12域覆盖，输出 HTML 报告

[单项检查]
只检查安全组：列出所有规则，标记危险的入站规则

[审计]
列出近7天的所有 Delete/Terminate 类操作

=== 成本类 ===

[快速筛查]
快速找出月花费>$500且CPU利用率<10%的CVM

[深度分析]
全面成本优化分析：闲置+规格过度+预留机会+存储优化，输出Action List

[ROI计算]
如果我把这5台CVM转预留实例，一年能省多少钱？回本是几个月？

=== 资源类 ===

[清单]
列出所有资源：按类型分组统计数量和总费用

[地域分析]
资源的地域分布情况，是否有跨地域冗余需求？

[标签检查]
有多少资源缺少标签？哪些应该补上 Environment/Owner 标签？

=== 架构类 ===

[画图]
画出当前的云架构拓扑图，要清晰美观

[评估]
评估架构健康度：HA、DR、弹性、成本、安全 5个维度打分

[对标]
对比阿里云/AWS 的同规模最佳实践，我的架构差距在哪？

[迁移]
如果要迁移到 Kubernetes，给我一个渐进式迁移方案
```

---

*文档版本: v1.0 | 更新时间: 2026-06-29 | 作者: CodeBuddy + CloudOps Team*
