#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lib_report_translate.py — cloud-inspector-pro 报告"输出层"整篇翻译引擎。

为什么需要它（与 lib_i18n_inspector.py 的 L() 互补，不重复）：
  · L()/STRINGS 走"生成期按串翻译"，只能覆盖**源码里写死的 UI 文案**。
  · 但报告里大量中文来自**数据**：腾讯云 advisor 接口返回的 strategy_name（规则名）
    和 repair（整改建议）、资源-规则矩阵单元格等。这些是运行时数据，L() 够不到。
  · 因此对"规则名 + 建议 + 仍漏的骨架句"必须在**装配好的整篇 markdown 落盘前**
    做一次输出层替换。这正是 Desktop 旧版 translate_report 的职责（旧版已失传，此处重建）。

设计约束（安全第一）：
  · lang == "zh"（默认）→ **原样返回，零替换、零行为变化**，保证 zh 路径字节级不变。
  · lang == "en" 才执行：① _FRAMES 正则模板（带数字/占位符的句子，按长度降序 longest-first）
    ② _TERMS 子串字典（longest-first 替换，避免短词污染长词）。
  · 只翻译"腾讯云标准枚举 + 我方 UI 话术"；**绝不翻译业务自定义值**
    （客户名/项目名/人名/资源自定义名/安全组名/IP/UIN/SKU 标签）——这些不入字典即天然保留。
  · re.sub 替换串里**禁止 \\uXXXX 转义**，直接写字面字符（≤ ≥ “” — 等），
    否则会被当成正则转义破坏输出（Desktop 踩坑）。
"""
import re

# ────────────────────────────────────────────────────────────────────────
# A. 正则帧 _FRAMES：带数字/动态片段的"模板句"。按 pattern 长度降序应用，
#    长模板优先，避免短模板先吃掉公共前缀。值里用字面字符，分组用 \1 \2。
#    注意：很多句子里的产品名（如"云服务器 (CVM)"）此时已被 tr() 转过半形括号，
#    所以帧里对产品名用宽松匹配 [（(] 兼容全/半角。
# ────────────────────────────────────────────────────────────────────────
_FRAME_SRC = [
    # —— 每产品"命中 N 类风险"汇总句 ——
    (r"^(.+?) 命中 (\d+) 类风险；其中 (\d+) 类返回可定位资源明细，涉及 (\d+) 个可定位资源。$",
     r"\1 hit \2 risk categories; \3 returned locatable resource details, involving \4 locatable resources."),
    (r"^(.+?) 平台策略返回 (\d+) 类风险、合计 (\d+) 条命中，但本次未返回可定位资源明细；本节不作为可直接整改清单展示，已列入数据完整性与复核项。$",
     r"\1 platform policy returned \2 risk categories, \3 hits total, but no locatable resource details this time; this section is not shown as a directly-actionable list and has been moved to Data Integrity & Review Items."),
    (r"^(.+?) 的可定位明细本次未能稳定渲染，已列入数据完整性与复核项。$",
     r"\1 locatable details could not be stably rendered this time and have been moved to Data Integrity & Review Items."),
    (r"^(.+?) 内多条规则命中同一组资源，已合并为下方资源-规则矩阵；逐规则的处置建议见各规则段落。$",
     r"\1 has multiple rules hitting the same resource set; merged into the resource-rule matrix below. Per-rule remediation is in each rule section."),
    # —— CAM 账号统计 / MFA / 复核 ——
    (r"^(\d+) 个子账号（可登录 (\d+) / 禁止登录 (\d+)），协助者 (\d+) 个$",
     r"\1 sub-accounts (\2 login-enabled / \3 login-disabled), \4 collaborators"),
    (r"^(\d+) 个需复核（含从未登录或超过90天未登录）$",
     r"\1 need review (incl. never logged in or inactive >90 days)"),
    (r"^(\d+)/(\d+) 已开启；未开启 (\d+) 个$", r"\1/\2 enabled; \3 not enabled"),
    (r"^(\d+) 个超过90天未用或从未使用$", r"\1 idle >90 days or never used"),
    (r"^(\d+) 个人员账号具备全量权限$", r"\1 user accounts have full privileges"),
    (r"MFA：✅ (\d+)/(\d+) 已开启$", r"MFA: ✅ \1/\2 enabled"),
    (r"^(-?\d+) 天未用$", r"idle for \1 days"),
    (r"^(-?\d+) 天$", r"\1 days"),
    (r"^(\d+) 个$", r"\1"),
    (r"^(\d+) 个（无）$", r"\1 (none)"),
    # —— 概览/网络汇总 ——
    (r"^共扫描 (\d+) 个安全组，发现全端口开放 (\d+) 条、高危端口 (\d+) 条、孤儿安全组 (\d+) 个。$",
     r"Scanned \1 security groups; found \2 fully-open, \3 high-risk-port, \4 orphan security groups."),
    (r"^合计 (\d+) 个空安全组分布于 (\d+) 个地域。$",
     r"\1 empty security groups across \2 regions in total."),
    (r"^数据口径：原始命中 (\d+) 条已按 \(安全组 ID, 端口, 协议\) 去重为 (\d+) 条；同一规则在采集中被多次记录的重复行已合并。$",
     r"Data scope: \1 raw hits deduplicated to \2 by (security group ID, port, protocol); duplicate rows recorded multiple times during collection have been merged."),
    (r"^本节覆盖跟踪集投递配置与最近 (\d+) 天高危操作事件流（(.+?) ~ (.+?)）。$",
     r"This section covers tracking-set delivery config and the high-risk operation event stream of the last \1 days (\2 ~ \3)."),
    (r"^标签完整性扫描基于 (.+?) 月已产生消费资源，规则为 head 必填且 business/customer 二选一；当前标签不完整资源 (\d+) 项。$",
     r"Tag completeness scan is based on billed resources in \1; the rule requires head plus one of business/customer; currently \2 tag-incomplete resources."),
    (r"^本次报告基于子账号身份（caller_uin=(.+?)）调用云原生 API 采集，关键口径：$",
     r"This report is based on cloud-native APIs called under a sub-account identity (caller_uin=\1); key scopes:"),
    (r"^- \*\*异常登录\*\*：CWP 风险登录窗口为采样数据（(\d+) 全量中采 (\d+) 条）$",
     r"- **Anomalous Login**: CWP risky-login window is sampled data (\2 of \1 total)"),
    (r"\*\*未安装客户端的主机明细（共 (\d+) 台）\*\*",
     r"**Hosts Without Agent Installed (\1 total)**"),
    (r"\*\*空安全组实例明细（共 (\d+) 个）\*\*", r"**Empty Security Group Instances (\1 total)**"),
    (r"\*\*采样明细（全部 (\d+) 条原始记录）\*\*", r"**Sampling Detail (all \1 raw records)**"),
    (r"\*\*高风险登录明细（采样命中 (\d+) 次）\*\*", r"**High-risk Login Detail (\1 sampled hits)**"),
    (r"\*\*桶级合并清单\*\*（共 (\d+) 个桶命中 (\d+) 类规则；以下列出命中 ≥ 2 类规则的 (\d+) 个桶）",
     r"**Bucket-level Merged List** (\1 buckets hit \2 rule categories; the \3 buckets hitting ≥2 categories are listed below)"),
    # —— 报告 footer / 下次复查 ——
    (r"^报告生成：(.+?) GMT\+8 \| SOP: (.+?)\n下次复查：(.+)$",
     r"Report generated: \1 GMT+8 | SOP: \2\nNext review: \3"),
    (r"^报告生成：(.+?) GMT\+8 \| SOP: (.+)$", r"Report generated: \1 GMT+8 | SOP: \2"),
    (r"^下次复查：(.+)$", r"Next review: \1"),
    # —— Administrator 角色长说明（带数字） ——
    (r"^Administrator 权限人员账号减少（当前 (\d+) 个），长期未登录账号和闲置 AK 完成禁用或删除（当前闲置 AK (\d+) 个）$",
     r"Reduce user accounts with Administrator privilege (currently \1); disable or delete long-inactive accounts and idle AKs (currently \2 idle AKs)"),
    # —— 地域安全组计数 / 计数枚举 ——
    (r"该地域共 (\d+) 个安全组", r"\1 security groups in this region"),
    (r"^配置 (\d+) 个$", r"\1 configured"),
    (r"^域名 (\d+) 个$", r"\1 domains"),
    (r"^BGP 实例 (\d+) 个$", r"\1 BGP instances"),
    (r"^(\d+) 个超过90天未用或从未使用$", r"\1 idle >90 days or never used"),
    (r"^(\d+) 个（无）$", r"\1 (none)"),
    # —— 命中 N 条；地域分布 模板（规则名英文已就位，仅尾部中文需收）——
    (r"（命中 (\d+) 条；地域分布：(.+?)）", r"(\1 hits; by region: \2)"),
    (r"（命中 (\d+) 条）", r"(\1 hits)"),
    (r"^(.+?)（另含 (\d+) 条规则未在此处列出）$", r"\1 (plus \2 more rules not listed here)"),
    # —— "Region not returned" 与地域分布里残留的中文 ——
    (r"接口未返回名称", r"name not returned by API"),
    # —— 表格单元格内数字模板（无行首锚定，可命中 | … | 之间的内容）——
    (r"(\d+) 个子账号（可登录 (\d+) / 禁止登录 (\d+)），(?:协助者|Collaborator) (\d+) 个",
     r"\1 sub-accounts (\2 login-enabled / \3 login-disabled), \4 collaborators"),
    (r"(\d+)/(\d+) 已开启；未开启 (\d+) 个", r"\1/\2 enabled; \3 not enabled"),
    (r"(\d+) 个需复核（含从未登录或超过90天未登录）", r"\1 need review (incl. never logged in or inactive >90 days)"),
    (r"(\d+) 个超过90天未用或(?:从未使用|never used)", r"\1 idle >90 days or never used"),
    (r"(-?\d+) 天未用", r"idle for \1 days"),
    (r"(\d+) 个（无）", r"\1 (none)"),
    (r"(-?\d+) 天(?=\s*\|)", r"\1 days"),       # 单元格内"N 天"（右侧紧跟表格分隔）
    (r"(?<=[|\s])(\d+) 个(?=\s*\|)", r"\1"),     # 单元格内孤立"N 个"
    # —— 引用句内产品名/规则名（「… → 产品名规则」）——
    (r"由 LLM 从 (?:cloud policy|云端策略) finding 推断,脚本不枚举 CDB 实例",
     r"inferred by LLM from cloud-policy findings; the script does not enumerate CDB instances"),
]
_FRAMES = sorted(
    ((re.compile(p), r) for p, r in _FRAME_SRC),
    key=lambda x: -len(x[0].pattern),
)

# ────────────────────────────────────────────────────────────────────────
# B. 子串字典 _TERMS：{中文: 英文}。longest-first 应用。
#    分三组便于维护：B1 腾讯云规则名(advisor strategy_name) / B2 整改建议(repair)
#    / B3 我方 UI 骨架与状态枚举。三组合并后统一按 key 长度降序替换。
# ────────────────────────────────────────────────────────────────────────

# B1 — advisor 规则名（41 条，源自真实报告 strategy_name）
_RULE_NAMES = {
    "云安全中心（CSC）发现主机未开启防护": "CSC: hosts found without protection enabled",
    "云安全中心（CSC）发现公网 IP 未开启防护": "CSC: public IPs found without protection enabled",
    "云安全中心（CSC）域名未开启防护": "CSC: domains without protection enabled",
    "云安全中心（CSC）数据库未开启安全防护": "CSC: databases without security protection enabled",
    "云数据库（MongoDB）未配置跨可用区部署": "TencentDB (MongoDB) not deployed across availability zones",
    "云数据库（MySQL）容灾情况": "TencentDB (MySQL) disaster-recovery status",
    "云数据库（MySQL）未配置跨可用区部署": "TencentDB (MySQL) not deployed across availability zones",
    "云数据库（MySQL）非 root 账号拥有高危命令权限": "TencentDB (MySQL) non-root account has high-risk command privileges",
    "云数据库（PostgreSQL）内核小版本过低": "TencentDB (PostgreSQL) minor kernel version too low",
    "云数据库（Redis）未升级为集群版": "TencentDB (Redis) not upgraded to cluster edition",
    "云数据库（Redis）未配置跨可用区部署": "TencentDB (Redis) not deployed across availability zones",
    "云数据库（Redis）未禁用高危命令": "TencentDB (Redis) high-risk commands not disabled",
    "云服务器（CVM）实例 CPU 使用率过高": "CVM instance CPU utilization too high",
    "云服务器（CVM）实例使用本地盘风险": "CVM instance using local disk risk",
    "云服务器（CVM）实例内存使用率过高": "CVM instance memory utilization too high",
    "云服务器（CVM）实例磁盘使用率过高": "CVM instance disk utilization too high",
    "云服务器（CVM）未绑定置放群组": "CVM not bound to a placement group",
    "云服务器（CVM）系统盘未创建快照": "CVM system disk has no snapshot created",
    "云硬盘（CBS）未创建快照": "CBS has no snapshot created",
    "容器服务（TKE）Pod OOM": "TKE Pod OOM",
    "容器服务（TKE）集群 APIServer 负载过高": "TKE cluster APIServer load too high",
    "容器服务（TKE）集群存在 CoreDNS 被删除风险": "TKE cluster at risk of CoreDNS deletion",
    "容器服务（TKE）集群存在命名空间级联删除风险": "TKE cluster at risk of namespace cascading deletion",
    "容器服务（TKE）集群存在正在使用中的 PV（PersistentVolume）被删除风险": "TKE cluster at risk of in-use PV (PersistentVolume) deletion",
    "容器服务（TKE）集群存在正在运行的 Node 被删除风险": "TKE cluster at risk of running-Node deletion",
    "容器服务（TKE）集群存在自定义 CRD 级联删除风险": "TKE cluster at risk of custom CRD cascading deletion",
    "容器服务（TKE）集群节点单点风险": "TKE cluster node single-point-of-failure risk",
    "对象存储（COS）存储桶日志投递失败风险": "COS bucket log delivery failure risk",
    "对象存储（COS）存储桶未启用版本控制": "COS bucket versioning not enabled",
    "对象存储（COS）存储桶未配置多 AZ 特性": "COS bucket multi-AZ feature not configured",
    "对象存储（COS）存储桶未配置防盗链（Referer）": "COS bucket hotlink protection (Referer) not configured",
    "对象存储（COS）存储桶未配置跨域访问头部": "COS bucket cross-origin access headers not configured",
    "对象存储（COS）存储桶配置公有读权限风险": "COS bucket configured with public-read permission risk",
    "弹性公网IP（EIP）带宽高负载": "EIP bandwidth under high load",
    "文件存储（CFS）定期快照备份策略未配置": "CFS scheduled snapshot backup policy not configured",
    "文件存储（CFS）实例无快照备份": "CFS instance has no snapshot backup",
    "消息队列（CKafka）Topic 单副本": "CKafka Topic single replica",
    "负载均衡（CLB）健康检查存在跳变情况": "CLB health check has flapping",
    "负载均衡（CLB）健康检查未配置": "CLB health check not configured",
    "负载均衡（CLB）后端存在串流风险": "CLB backend has stream-crossing risk",
    "负载均衡（CLB）后端服务单点风险": "CLB backend service single-point-of-failure risk",
    "轻量应用服务器（LH）实例内存高负载": "Lighthouse (LH) instance memory under high load",
    "轻量应用服务器（LH）实例到期": "Lighthouse (LH) instance expiration",
    "安全组（SG）开放公网/内网可访问权限风险": "Security Group (SG) opens public/private access permission risk",
    "Elasticsearch Service 集群单可用区部署风险": "Elasticsearch Service cluster single-AZ deployment risk",
    "Elasticsearch 指定 Lucene 布尔查询中允许的子句的最大数量过大": "Elasticsearch max Lucene boolean-query clause count set too high",
    "Elasticsearch 集群存在可更新内核版本": "Elasticsearch cluster has an upgradable kernel version",
    "Elasticsearch Service 集群": "Elasticsearch Service cluster",
    # --- v3.9 补：ADC 报告实测缺失的规则名 ---
    "容器服务（TKE）托管集群资源对象数过多": "TKE managed cluster has too many resource objects",
    "云函数（SCF）函数运行时间突增": "SCF function runtime spike",
    "私有网络 VPN 网关（VPNGW）带宽利用率": "VPC VPN Gateway (VPNGW) bandwidth utilization",
    "TCR 在 COS 单 AZ 地域未开启跨地域复制": "TCR cross-region replication not enabled in COS single-AZ region",
    "托管集群资源对象数过多": "managed cluster has too many resource objects",
    "函数运行时间突增": "function runtime spike",
    # --- v3.9 补-2：ADC 报告第二轮缺失的规则名 ---
    "Web 应用防火墙（WAF）未开启日志服务（域名接入）": "WAF log service not enabled (domain access)",
    "云数据库（MySQL）root 账号安全风险": "TencentDB (MySQL) root account security risk",
    "消息队列（TDMQ）缺少备份消费者": "TDMQ missing backup consumers",
    "消息队列（TDMQ）Pulsar 版未配置跨可用区部署": "TDMQ Pulsar edition not deployed across availability zones",
    "Elasticsearch 集群未开启自动快照备份": "Elasticsearch cluster automated snapshot backup not enabled",
    "云数据库（PostgreSQL）未配置跨可用区部署": "TencentDB (PostgreSQL) not deployed across availability zones",
    "消息队列（Ckafka）跨可用区部署": "CKafka cross-AZ deployment",
    "云服务器（CVM）操作系统版本升级至tencentos": "CVM OS version upgrade to TencentOS",
    "TCR 使用的 COS 桶未开启多 AZ 特性": "COS bucket used by TCR has multi-AZ feature not enabled",
    "存储桶未接入公网下行流量监控": "bucket not onboarded to internet downstream traffic monitoring",
    "root 账号安全风险": "root account security risk",
    "缺少备份消费者": "missing backup consumers",
    "Pulsar 版未配置跨可用区部署": "Pulsar edition not deployed across availability zones",
    "未开启自动快照备份": "automated snapshot backup not enabled",
    "跨可用区部署": "cross-AZ deployment",
    "操作系统版本升级至tencentos": "OS version upgrade to TencentOS",
}

# B2 — 整改建议 repair（63 条，源自真实报告；含 markdown 链接，原样保留 URL）
_ADVICE = {
    "1.控制台重启集群以更新到最新的内核版本 2.如果ES集群版本为5.6.4、6.4.3，建议升级集群版本到6.8.2；如果集群版本为7.5.1，建议升级集群版本到7.10.1或7.14.2":
        "1. Restart the cluster from the console to update to the latest kernel version. 2. If the ES cluster version is 5.6.4 or 6.4.3, upgrade to 6.8.2; if 7.5.1, upgrade to 7.10.1 or 7.14.2.",
    "22/SSH、3389/RDP 等高危端口直接暴露公网风险较高，建议绑定堡垒机或限制为运维 IP 白名单访问。":
        "High-risk ports such as 22/SSH and 3389/RDP exposed directly to the public network are risky; bind a bastion host or restrict to an ops IP allowlist.",
    "Topic 存在单副本，可参考[消息队列 CKafka Topic 问题](https://cloud.tencent.com/document/product/597/55819)":
        "The Topic has a single replica; see [CKafka Topic issues](https://cloud.tencent.com/document/product/597/55819).",
    "上述主机未安装云镜（CWP）客户端，无法纳入主机安全防护，建议参考 [快速入门](https://cloud.tencent.com/document/product/296/12236) 安装并接入。":
        "The hosts above have no Cloud Workload Protection (CWP) agent installed and cannot be covered by host security; see [Quick Start](https://cloud.tencent.com/document/product/296/12236) to install and onboard.",
    "上述安全组当前无任何规则配置，建议确认是否仍在被引用，未引用的可清理回收。":
        "The security groups above currently have no rules configured; confirm whether they are still referenced and reclaim the unused ones.",
    "以下安全组对 0.0.0.0/0 全协议开放，建议根据业务实际需求调整为最小化方式放通访问规则，仅保留业务白名单来源。":
        "The security groups below are open to 0.0.0.0/0 for all protocols; adjust to least-privilege rules based on actual business needs and keep only business allowlist sources.",
    "以下对象存储桶检测到公开读/写权限，建议核实公开必要性；不必要的请收敛 ACL 至业务最小授权。":
        "The object storage buckets below were detected with public read/write permissions; verify whether public access is necessary and, if not, tighten the ACL to least-privilege.",
    "以下账号持有的密钥超过 90 天未使用或从未使用，建议确认必要性后禁用或删除；本表不输出密钥 ID。":
        "The accounts below hold keys unused for over 90 days or never used; confirm necessity then disable or delete. This table does not output key IDs.",
    "依据最小化权限原则，为存储桶[ACL 访问控制](https://cloud.tencent.com/document/product/436/12470) 配置\"私有读写\"权限，按需分配存储桶读写权限，提高数据安全性。请查看[防盗刷指引](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-1)":
        "Following least-privilege, configure \"private read/write\" for the bucket via [ACL access control](https://cloud.tencent.com/document/product/436/12470), assign read/write as needed, and improve data security. See [anti-abuse guide](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-1).",
    "如果业务上对 1ms 的网络延迟不敏感，建议采用主从跨可用区部署，提高数据库的可用性。请查看[最佳实践](https://cloud.tencent.com/developer/article/1956902)":
        "If the business is not sensitive to 1ms network latency, use primary-secondary cross-AZ deployment to improve database availability. See [best practices](https://cloud.tencent.com/developer/article/1956902).",
    "如果业务有异地容灾或等保要求，可以在控制台[创建 MySQL 灾备实例](https://cloud.tencent.com/document/product/236/7272)。请查看[最佳实践](https://cloud.tencent.com/developer/article/1972680)":
        "If the business requires geo-disaster-recovery or compliance, create a [MySQL DR instance](https://cloud.tencent.com/document/product/236/7272) from the console. See [best practices](https://cloud.tencent.com/developer/article/1972680).",
    "存储桶版本控制有助于恢复被误删或应用程序故障而丢失的数据。建议为存储桶[设置版本控制](https://cloud.tencent.com/document/product/436/19881)，提高数据安全性。请查看[最佳实践](https://cloud.tencent.com/developer/article/1973107)":
        "Bucket versioning helps recover data lost from accidental deletion or application failure. [Enable versioning](https://cloud.tencent.com/document/product/436/19881) for the bucket to improve data security. See [best practices](https://cloud.tencent.com/developer/article/1973107).",
    "建议 CLB 监听器或转发规则绑定两个及以上的后端服务实例，避免单点故障":
        "Bind two or more backend service instances to the CLB listener or forwarding rule to avoid a single point of failure.",
    "建议[创建快照](https://cloud.tencent.com/document/product/362/5755),或[创建定期快照策略](https://cloud.tencent.com/document/product/362/8191)，以提高数据安全性，实现业务的低成本高容灾":
        "[Create a snapshot](https://cloud.tencent.com/document/product/362/5755) or a [scheduled snapshot policy](https://cloud.tencent.com/document/product/362/8191) to improve data security and achieve low-cost high disaster recovery.",
    "建议[升级实例架构](https://cloud.tencent.com/document/product/239/46458)成集群版，当 cpu、流量、连接数、内存等使用不够时可以随时横向变配，具备较强的抗风险能力":
        "[Upgrade the instance architecture](https://cloud.tencent.com/document/product/239/46458) to cluster edition, so you can scale out anytime when CPU, traffic, connections or memory are insufficient, gaining stronger risk resilience.",
    "建议[扩容云硬盘](https://cloud.tencent.com/document/product/213/34068)，或清理磁盘内容，避免磁盘读写受到容量影响。请查看[最佳实践](https://cloud.tencent.com/developer/article/1999102)":
        "[Expand the cloud disk](https://cloud.tencent.com/document/product/213/34068) or clean up disk contents to avoid capacity affecting disk I/O. See [best practices](https://cloud.tencent.com/developer/article/1999102).",
    "建议为包年包月实例设置[自动续费](https://cloud.tencent.com/document/product/1207/47835)，或者在资源列表中[手动续费](https://cloud.tencent.com/document/product/1207/47835)，以防止实例因为过期被销毁":
        "Set [auto-renewal](https://cloud.tencent.com/document/product/1207/47835) for monthly/yearly instances, or [renew manually](https://cloud.tencent.com/document/product/1207/47835) in the resource list, to prevent instances from being destroyed upon expiration.",
    "建议为存储桶[设置跨域访问](https://cloud.tencent.com/document/product/436/13318)，补充 CORS Allow-Headers/Expose-Headers 头部设置，提升在跨域访问场景下的连续性。请查看[最佳实践](https://cloud.tencent.com/developer/article/1973892)":
        "[Configure cross-origin access](https://cloud.tencent.com/document/product/436/13318) for the bucket and add CORS Allow-Headers/Expose-Headers settings to improve continuity in cross-origin scenarios. See [best practices](https://cloud.tencent.com/developer/article/1973892).",
    "建议依据最小化权限原则，通过[修改账号权限](https://cloud.tencent.com/document/product/236/35795)，为账号分配适当的权限，去掉非必须的 DROP，DELETE 权限。请查看[最佳实践](https://cloud.tencent.com/developer/article/2456790)":
        "Following least-privilege, [modify account privileges](https://cloud.tencent.com/document/product/236/35795) to assign appropriate permissions and remove unnecessary DROP and DELETE privileges. See [best practices](https://cloud.tencent.com/developer/article/2456790).",
    "建议修改绑定的后端服务策略，尽量避免多个负载均衡绑定同一后端服务器的同一端口，详情可参考：https://cloud.tencent.com/document/product/214/5411#19。":
        "Modify the bound backend service policy to avoid multiple load balancers binding the same port on the same backend server. See: https://cloud.tencent.com/document/product/214/5411#19.",
    "建议升级到最新的内核小版本，参考指引：[内核小版本升级](https://cloud.tencent.com/document/product/409/88985)":
        "Upgrade to the latest minor kernel version. See: [minor kernel version upgrade](https://cloud.tencent.com/document/product/409/88985).",
    "建议可根据业务实际情况评估将部分 CVM 加入置放群组中，提升业务容灾能力，参考：[管理实例的置放群组](https://cloud.tencent.com/document/product/213/41156)。":
        "Evaluate adding some CVMs to a placement group based on actual business needs to improve disaster recovery. See: [manage instance placement groups](https://cloud.tencent.com/document/product/213/41156).",
    "建议合理[设置该配置](https://cloud.tencent.com/document/product/845/16997)，保持在500-2000之间。":
        "[Set this configuration](https://cloud.tencent.com/document/product/845/16997) reasonably, keeping it between 500 and 2000.",
    "建议将主机接入主机安全，及时检测和清除病毒和恶意软件，可以前往 [云安全中心-资产中心-主机资产](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=server)或[主机安全-防护授权管理](https://console.cloud.tencent.com/cwp/setting/authorize)对主机开启防护。（由于云安全中心存在一定的资产同步延时，“评估中的资源列表”中可能会存在已销毁的主机信息，无需关注，明天将自动恢复）":
        "Onboard hosts to host security to detect and remove viruses and malware in time. Go to [CSC - Asset Center - Host Assets](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=server) or [Host Security - Protection Authorization](https://console.cloud.tencent.com/cwp/setting/authorize) to enable protection. (Due to asset sync latency in CSC, the \"resources under assessment\" list may contain already-destroyed hosts; no action needed, it will auto-recover tomorrow.)",
    "建议将云数据库所在的网络接入NAT 边界防火墙，基于反代原理实现数据库的登录运维，可以前往[云防火墙-零信任防护-数据库管理](https://console.cloud.tencent.com/cfw/identityauth/database) 对数据库开启防护":
        "Onboard the database network to the NAT edge firewall and use reverse-proxy for database login/ops. Go to [Cloud Firewall - Zero Trust - Database Management](https://console.cloud.tencent.com/cfw/identityauth/database) to enable protection.",
    "建议将公网 IP 接入云防火墙，限制网络流量，防止恶意攻击和入侵，前往 [云安全中心-资产中心-公网IP资产](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=publicIp) 或 [云防火墙-入侵防御](https://console.cloud.tencent.com/cfw/ips)对 IP 开启防护":
        "Onboard public IPs to Cloud Firewall to limit traffic and prevent attacks and intrusions. Go to [CSC - Asset Center - Public IP Assets](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=publicIp) or [Cloud Firewall - IPS](https://console.cloud.tencent.com/cfw/ips) to enable protection.",
    "建议将域名接入Web应用防火墙，检测和阻止Web应用程序中的安全漏洞和攻击，前往 [云安全中心-资产中心-域名资产](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=domain)或 [Web应用防火墙](https://console.cloud.tencent.com/guanjia/tea-domain) 对域名开启防护":
        "Onboard domains to the Web Application Firewall to detect and block web vulnerabilities and attacks. Go to [CSC - Asset Center - Domain Assets](https://console.cloud.tencent.com/csip/asset?tab=assetList&subtab=domain) or [WAF](https://console.cloud.tencent.com/guanjia/tea-domain) to enable protection.",
    "建议开启[防盗链设置](https://cloud.tencent.com/document/product/436/13319)并拒绝空 Referer 访问，减少存储桶被恶意盗刷的风险。请查看[防盗刷指引](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-2)":
        "Enable [hotlink protection](https://cloud.tencent.com/document/product/436/13319) and reject empty-Referer access to reduce the risk of malicious bucket abuse. See [anti-abuse guide](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-2).",
    "建议按需[升级实例套餐](https://cloud.tencent.com/document/product/1207/51730)，提升单实例性能；或者增加实例个数，将计算请求分散到多个 LH 实例":
        "[Upgrade the instance plan](https://cloud.tencent.com/document/product/1207/51730) as needed to improve single-instance performance, or add more instances to spread compute requests across multiple LH instances.",
    "建议按需升级[实例规格](https://cloud.tencent.com/document/product/213/11518)，提升单实例性能；或者增加实例个数，将计算请求分散到多个 CVM 实例":
        "[Upgrade the instance spec](https://cloud.tencent.com/document/product/213/11518) as needed to improve single-instance performance, or add more instances to spread compute requests across multiple CVM instances.",
    "建议核实存储桶的日志管理配置，确保源存储桶的日志能够正确的投递。请查看[最佳实践](https://cloud.tencent.com/developer/article/1984185)":
        "Verify the bucket's log management config to ensure source-bucket logs are delivered correctly. See [best practices](https://cloud.tencent.com/developer/article/1984185).",
    "建议根据业务实际需求，调整为最小化方式放通访问规则；如果安全组没有使用计划，建议及时清理":
        "Adjust to least-privilege access rules based on actual business needs; if a security group has no usage plan, clean it up promptly.",
    "建议根据业务需要做好数据备份及容灾部署，或评估将满足文档中条件的实例切换为云硬盘；如业务确认对应实例上存储的数据存在备份或具备容灾机制，可忽略该风险。本地盘转云盘可参考文档：[本地盘调整为云硬盘](https://cloud.tencent.com/document/product/213/31978)":
        "Set up data backup and disaster recovery as needed, or evaluate switching eligible instances to cloud disks. If the business confirms the data is backed up or has DR, this risk can be ignored. For local-to-cloud disk migration see: [local disk to cloud disk](https://cloud.tencent.com/document/product/213/31978).",
    "建议检查 CLB 监听器或转发规则对应的后端服务状态，排查导致探测异常的可能原因，及时排除后端服务可能失效带来的业务隐患，参考[监控检查异常排查](https://cloud.tencent.com/document/product/214/3394) ，如有疑问请联系腾讯云工程师。":
        "Check the backend service status for the CLB listener or forwarding rule, investigate possible causes of probe anomalies, and eliminate business risks from potential backend failure in time. See [health check troubleshooting](https://cloud.tencent.com/document/product/214/3394); contact a Tencent Cloud engineer if in doubt.",
    "建议禁用flushall、flushdb、keys，其中flushall、flushdb是避免业务误操作清空数据库，keys是避免业务全表扫引发大量执行慢查询；请参考[禁用命令](https://cloud.tencent.com/document/product/239/36758)，为实例配置禁用命令，提升实例的安全性和伸缩性":
        "Disable flushall, flushdb and keys — flushall/flushdb prevent accidental database wipes, and keys prevents full scans causing many slow queries. See [disable commands](https://cloud.tencent.com/document/product/239/36758) to configure disabled commands for the instance and improve its security and scalability.",
    "建议结合带宽利用率及业务实际情况，评估提升 EIP 限速，详情可参考：[调整网络配置](https://cloud.tencent.com/document/product/1199/41705)":
        "Evaluate raising the EIP rate limit based on bandwidth utilization and actual business needs. See: [adjust network config](https://cloud.tencent.com/document/product/1199/41705).",
    "建议通过[集群多可用区部署](https://cloud.tencent.com/document/product/845/35551)开启 ES 集群多可用区部署容灾":
        "Enable ES cluster multi-AZ deployment for disaster recovery via [cluster multi-AZ deployment](https://cloud.tencent.com/document/product/845/35551).",
    "建议采用跨可用区部署方案：[变更可用区](https://cloud.tencent.com/document/product/239/54272)":
        "Adopt a cross-AZ deployment plan: [change availability zone](https://cloud.tencent.com/document/product/239/54272).",
    "建议采用跨可用区部署方案：[调整实例可用区](https://cloud.tencent.com/document/product/240/64593)":
        "Adopt a cross-AZ deployment plan: [adjust instance availability zone](https://cloud.tencent.com/document/product/240/64593).",
    "异常登录可能存在凭据泄露或账号被盗用风险，建议核实登录人合理性、轮换密码并开启 MFA；如为运维例行任务，请在堡垒机/跳板机集中登录。":
        "Anomalous logins may indicate credential leakage or account compromise; verify the login user's legitimacy, rotate passwords and enable MFA. For routine ops tasks, log in centrally via a bastion/jump host.",
    "扩容集群节点数，确保集群节点数大于一个":
        "Scale out the cluster node count to ensure more than one node.",
    "暂不支持修改存储桶的多 AZ 特性，建议在创建存储桶时，通过控制台[开启多 AZ ](https://cloud.tencent.com/document/product/436/13309)，支持地域和使用限制详见[多 AZ 特性概述](https://cloud.tencent.com/document/product/436/40548)。如需将当前桶数据存储为多 AZ，[请联系我们](https://console.cloud.tencent.com/workorder/category?level1_id=83&level2_id=84&source=14&data_title=%E5%AF%B9%E8%B1%A1%E5%AD%98%E5%82%A8&step=1)进行数据迁移。":
        "Modifying a bucket's multi-AZ feature is not currently supported; [enable multi-AZ](https://cloud.tencent.com/document/product/436/13309) from the console when creating the bucket. For supported regions and limits see [multi-AZ overview](https://cloud.tencent.com/document/product/436/40548). To store current bucket data as multi-AZ, [contact us](https://console.cloud.tencent.com/workorder/category?level1_id=83&level2_id=84&source=14&data_title=%E5%AF%B9%E8%B1%A1%E5%AD%98%E5%82%A8&step=1) for data migration.",
    "检测到您未配置健康检查，CLB可能将请求转发给异常的后端服务，从而影响前端业务。建议您参考[健康检查产品说明](https://cloud.tencent.com/document/product/214/6097)配置响应的健康检查，如有疑问请联系腾讯云工程师。":
        "No health check is configured; CLB may forward requests to abnormal backend services and affect the frontend business. Configure health checks per [health check docs](https://cloud.tencent.com/document/product/214/6097); contact a Tencent Cloud engineer if in doubt.",
    "请检查 OOM 原因，或者调大容器内存 Limit 限制":
        "Investigate the OOM cause, or increase the container memory Limit.",
    "请检查业务组件是否存在频繁 List 资源请求导致的高负载，建议[优化List请求](https://cloud.tencent.com/document/product/457/68804#.E8.B4.AD.E4.B9.B0.E9.A1.BB.E7.9F.A5)，同时建议手动升配 master 节点到更高配置，提升控制面组件的稳定性":
        "Check whether business components cause high load via frequent List resource requests; [optimize List requests](https://cloud.tencent.com/document/product/457/68804#.E8.B4.AD.E4.B9.B0.E9.A1.BB.E7.9F.A5) and manually upgrade the master node to a higher spec to improve control-plane stability.",
    "请评估业务需求，尽快创建快照进行数据备份。参考文档： https://cloud.tencent.com/document/product/362/5755":
        "Assess business needs and create a snapshot for data backup as soon as possible. See: https://cloud.tencent.com/document/product/362/5755",
    # --- v3.9 补：ADC 报告实测缺失的 repair ---
    "建议提交工单联系 COS 团队将 COS 桶原地升级到多 AZ COS 桶。":
        "Submit a ticket to the COS team to upgrade the bucket to multi-AZ in place.",
    "建议开启 TCR 的复制实例，提升 TCR 服务的可用性。":
        "Enable TCR replication instances to improve TCR service availability.",
    "函数运行时间突增，请根据业务日志，排查出性能瓶颈，查看[日志详情参考](https://cloud.tencent.com/document/product/583/36143)":
        "Function runtime spiked; check business logs to identify the bottleneck. See [log details](https://cloud.tencent.com/document/product/583/36143).",
    "建议结合带宽利用率及业务实际情况，评估提升 VPNGW 限速，详情可参考[修改 IPSec VPN 网关](https://cloud.tencent.com/document/product/554/52862)，如有疑问，可咨询腾讯云工程师":
        "Evaluate raising the VPNGW rate limit based on bandwidth utilization and actual business needs. See [modify IPSec VPN gateway](https://cloud.tencent.com/document/product/554/52862); contact a Tencent Cloud engineer if in doubt.",
    "请检查业务组件是否存在 Bug 异常写入资源数或未及时清理，建议手动升配集群到更高规格，提升控制面组件的稳定性，以及获取更高的集群资源配额值，[如何选择集群规格](https://cloud.tencent.com/document/product/457/54460)":
        "Check whether business components have bugs causing abnormal resource writes or failing to clean up; manually upgrade the cluster to a higher spec to improve control-plane stability and obtain higher resource quotas. See [how to choose cluster spec](https://cloud.tencent.com/document/product/457/54460).",
    # --- v3.9 补-2：ADC 报告第二轮实测缺失的 repair ---
    "建议购买安全日志服务包或者开启日志投递服务，开启访问[日志配置指导](https://cloud.tencent.com/document/product/627/60792) [日志投递配置指导](https://cloud.tencent.com/document/product/627/70276)":
        "Purchase a security log service pack or enable log delivery; enable access [log config guide](https://cloud.tencent.com/document/product/627/60792) [log delivery config guide](https://cloud.tencent.com/document/product/627/70276).",
    "建议配置外网下行流量监控告警或者在Cloud Monitor（腾讯云可观测平台）开启此告警。请查看[防盗刷指引](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-3)":
        "Configure internet downstream traffic monitoring alerts or enable this alert in Cloud Monitor. See [anti-abuse guide](https://cloud.tencent.com/document/product/436/97973#7feed294-911e-41e7-ae60-d61606423dd8-3).",
    "如果使用灾备模式或共享模式，建议在业务允许情况下请启动多个消费者，这样能保证消费者的高可用。请查看[最佳实践](https://cloud.tencent.com/developer/article/1967167)":
        "If using DR or shared mode, start multiple consumers if the business allows, to ensure consumer high availability. See [best practices](https://cloud.tencent.com/developer/article/1967167).",
    "建议把重要的业务放在多可用区部署的集群。对于现有业务，可新购专业集群，再将实例迁移到新集群。请参考[购买指引](https://cloud.tencent.com/document/product/1179/63552)和[跨可用区部署指引](https://cloud.tencent.com/document/product/1179)":
        "Place important workloads on multi-AZ clusters. For existing workloads, purchase a professional cluster and migrate instances. See [purchase guide](https://cloud.tencent.com/document/product/1179/63552) and [multi-AZ deployment guide](https://cloud.tencent.com/document/product/1179).",
    "建议开启 Elasticsearch 集群自动快照备份，能显著提高数据的可靠性，请参考[开启 ES 集群自动快照备份](https://cloud.tencent.com/document/product/845/31900)":
        "Enable Elasticsearch cluster automated snapshot backups to significantly improve data reliability. See [enable ES automated snapshot backup](https://cloud.tencent.com/document/product/845/31900).",
    "建议采用跨可用区部署方案提升业务可靠性。请查看：[设置实例可用区容灾或者地域容灾](https://cloud.tencent.com/document/product/409/68422)":
        "Adopt a cross-AZ deployment plan to improve business reliability. See [configure instance AZ DR or regional DR](https://cloud.tencent.com/document/product/409/68422).",
    "建议[创建跨可用区容灾实例](https://cloud.tencent.com/document/product/597/52786)，提升 Ckafka 实例整体可用性。请查看[最佳实践](https://cloud.tencent.com/developer/article/1967163)":
        "[Create a cross-AZ DR instance](https://cloud.tencent.com/document/product/597/52786) to improve overall CKafka instance availability. See [best practices](https://cloud.tencent.com/developer/article/1967163).",
    "建议依据最小化权限原则，根据不同的业务需求[创建不同的账号](https://cloud.tencent.com/document/product/236/35794)，并分配适当的权限，提升数据隔离性和安全性，也便于管理。请查看[最佳实践](https://cloud.tencent.com/developer/article/1972601)":
        "Following least-privilege, [create separate accounts](https://cloud.tencent.com/document/product/236/35794) for different business needs and assign appropriate permissions to improve data isolation and security. See [best practices](https://cloud.tencent.com/developer/article/1972601).",
    "请根据业务情况评估将操作系统版本升级至tencentos https://cloud.tencent.com/document/product/213/109841":
        "Assess upgrading the OS version to TencentOS based on business needs. See https://cloud.tencent.com/document/product/213/109841.",
    # 资源-规则矩阵说明 / 集群级联块（无产品前缀的短规则名，供矩阵单元格命中）
    "集群存在 CoreDNS 被删除风险": "cluster at risk of CoreDNS deletion",
    "集群存在命名空间级联删除风险": "cluster at risk of namespace cascading deletion",
    "集群存在正在使用中的 PV（PersistentVolume）被删除风险": "cluster at risk of in-use PV (PersistentVolume) deletion",
    "集群存在正在运行的 Node 被删除风险": "cluster at risk of running-Node deletion",
    "集群存在自定义 CRD 级联删除风险": "cluster at risk of custom CRD cascading deletion",
    "集群 APIServer 负载过高": "cluster APIServer load too high",
    "集群节点单点风险": "cluster node single-point-of-failure risk",
}

# B3 — 我方 UI 骨架 + 状态枚举 + 散句（仍漏的；不含已由 L() 覆盖的）
_SKELETON = {
    # CAM 子节标题
    "**1. 账号统计**": "**1. Account Statistics**",
    "**2. 离职/未活跃账号检测**": "**2. Departed / Inactive Account Detection**",
    "**3. AK/SK 权限审查**": "**3. AK/SK Privilege Review**",
    "**4. Administrator 权限**": "**4. Administrator Privileges**",
    "**5. 用户组归属检查**": "**5. User Group Membership Check**",
    "**6. MFA 开启检查**": "**6. MFA Enablement Check**",
    "**7. AK 闲置清理**": "**7. Idle AK Cleanup**",
    "**8. 协助者持 AK 明细**": "**8. Collaborator AK Holdings Detail**",
    # 云审计子节标题
    "**A. 跟踪集状态**": "**A. Tracking Set Status**",
    "**B. 高危事件流总览**": "**B. High-risk Event Stream Overview**",
    "**C. 高危事件明细**": "**C. High-risk Event Detail**",
    "**D. 服务账号自动化操作汇总**": "**D. Service-account Automation Operations Summary**",
    "**E. 安全组改动明细**": "**E. Security Group Change Detail**",
    # CVM / 网络 子标题
    "**按操作系统分布**": "**Distribution by OS**",
    "**未装 CWP 主机 - 明细**": "**Hosts Without CWP - Detail**",
    "**主体 + IP 段聚合**": "**Principal + IP-range Aggregation**",
    "**高危端口规则**": "**High-risk Port Rules**",
    "**地域分布**": "**Region Distribution**",
    "**平台未返回可定位资源 ID 的复核项**": "**Review Items Where Platform Returned No Locatable Resource ID**",
    # 详情字段标签（advisor detail 块）
    "**严重度**": "**Severity**",
    "**名称**": "**Name**",
    "**地域**": "**Region**",
    "- **Class**": "- **Class**",
    # 散句（骨架）
    "以下资源被多个产品 / 子规则命中，建议优先核查整改：":
        "The following resources were hit by multiple products / sub-rules; prioritize review and remediation:",
    "CAM 检测覆盖子账号、协助者、Administrator 权限、MFA、人机分离与闲置凭据；本次当前巡检账号为子账号身份，主账号直接持有密钥状态未采集。":
        "CAM detection covers sub-accounts, collaborators, Administrator privileges, MFA, human-machine separation and idle credentials; this inspection caller is a sub-account, so the AK-holding status of the primary account is not collected.",
    "✅ 协助者均未持有 AK。": "✅ No collaborator holds an AK.",
    "VPC / 子网 / 网络 ACL / NAT 网关 / 堡垒机覆盖情况，用于评估网络架构面与边界防护。":
        "Coverage of VPC / subnet / network ACL / NAT gateway / bastion host, used to assess network architecture surface and perimeter protection.",
    "以下清单展示已能定位到的公网资产或外网入口；网络放通规则在上方“安全组规则与高危端口”小节已单独展示。":
        "The list below shows locatable public assets or external entry points; network access rules are shown separately in the \"Security Group Rules & High-risk Ports\" section above.",
    "综合 WAF / DDoS 高防 / 云防火墙 / 数据库审计 等边界与纵深防护产品的覆盖情况。":
        "Combined coverage of perimeter and defense-in-depth products such as WAF / Anti-DDoS / Cloud Firewall / Database Audit.",
    "基线配置覆盖组织归属、安全联系人和消息订阅；部分接口未授权时会以「未配置/采集受限」标注，需补充权限后复扫。":
        "Baseline config covers org membership, security contacts and message subscriptions; when some APIs are unauthorized, items are marked \"not configured / collection restricted\" and need a rescan after adding permissions.",
    "配置审计接口本次未获授权，无法确认标签完整率。":
        "The config audit API was not authorized this time; tag completeness cannot be confirmed.",
    "本次整改建议按「降低暴露面、保障连续性、补齐责任归属」的顺序推进。先处理可能带来直接安全或业务中断影响的问题，再推进账号、标签、备份、审计等治理项，最后通过复核确认风险是否真正关闭。":
        "This remediation is recommended in the order of \"reduce exposure, ensure continuity, complete accountability\". First handle issues that may cause direct security or business interruption, then advance governance items such as accounts, tags, backup and audit, and finally confirm via review whether risks are truly closed.",
    "NAT 平台策略返回 2 类风险、合计 8 条命中，但本次未返回可定位资源明细；本节不作为可直接整改清单展示，已列入数据完整性与复核项。":
        "NAT platform policy returned 2 risk categories, 8 hits total, but no locatable resource details this time; this section is not shown as a directly-actionable list and has been moved to Data Integrity & Review Items.",
    # 数据口径要点（- ** ** 列表）
    "- **CAM 用户**：principals 列表去重后按 type 拆分（user/collaborator）；可登录定义为 console_login==1":
        "- **CAM Users**: principals list deduplicated then split by type (user/collaborator); login-enabled means console_login==1",
    "- **AK 闲置**：阈值 90 天，含从未使用（即 LastUsedDate 为空的 AK）":
        "- **Idle AK**: threshold 90 days, incl. never-used (AKs with empty LastUsedDate)",
    "- **MFA 开启**：仅统计 console_login==1 的人员账号":
        "- **MFA Enabled**: counts only user accounts with console_login==1",
    "- **CVM 主机安全**：以云镜（CWP）防护状态为口径；未安装客户端=未防护":
        "- **CVM Host Security**: based on Cloud Workload Protection (CWP) status; no agent installed = unprotected",
    "- **公网风险**：以 云端策略 平台策略 + 安全组 + 公网 IP 三视角交叉":
        "- **Public Network Risk**: cross-referenced from three views — cloud policy + security groups + public IPs",
    "- **采集受限项**：以下条目本次未能完整采集，仅状态标注：":
        "- **Collection-restricted Items**: the following could not be fully collected this time, only status is annotated:",
    "- 主账号 AK 列表（root_aks_scan_status='skipped_subuser_caller'）— 子账号身份无权列主账号 AK":
        "- Primary-account AK list (root_aks_scan_status='skipped_subuser_caller') — a sub-account identity cannot list the primary account's AKs",
    "- 配置审计（compliance.scan_status='denied'）— 服务未启用或子账号无权":
        "- Config audit (compliance.scan_status='denied') — service not enabled or sub-account unauthorized",
    "- 云审计（cloudaudit.scan_status='caller_subuser'）— 子账号身份限制":
        "- CloudAudit (cloudaudit.scan_status='caller_subuser') — sub-account identity restriction",
    "- 部分 infra 子项（DescribeOrganization / DescribeAccountAttributes / DescribeBhInstances 等 API 在子账号身份下不可用）":
        "- Some infra sub-items (APIs like DescribeOrganization / DescribeAccountAttributes / DescribeBhInstances are unavailable under a sub-account identity)",
    # 散落的 Organization 说明（开头"说明："）
    "说明：表中“组织管控系统角色（Organization）”是腾讯云 Organization 组织管控为跨账号管理自动注入的 STS 角色（CAM 内部记 UIN=0），**不是人员账号也不是协助者**，因此未计入上方 Administrator 权限的 5 个人员账号；如本账号未加入组织或不需要该角色，请由账号管理员到 CAM 控制台核查并清理。":
        "Note: the \"Organization control system role (Organization)\" in the table is an STS role auto-injected by Tencent Cloud Organization for cross-account management (recorded internally as UIN=0); it is **neither a user account nor a collaborator**, so it is not counted in the 5 user accounts with Administrator privileges above. If this account has not joined an organization or does not need this role, have an account admin review and clean it up in the CAM console.",
    "说明：错误码 0 表示操作成功；非 0 错误码表示该次调用未成功，需要结合 RequestId 进一步确认失败原因。":
        "Note: error code 0 means the operation succeeded; a non-zero error code means the call failed and the cause should be confirmed using the RequestId.",
    # 状态枚举（短）
    "✅ 通过": "✅ Pass",
    "⚠️ 未确认": "⚠️ Unconfirmed",
    "🔴 严重": "🔴 Critical",
    "🔴 高": "🔴 High",
    "🟠 高风险": "🟠 High risk",
    "🟠 高": "🟠 High",
    "🟡 中风险": "🟡 Medium risk",
    "🟡 中": "🟡 Medium",
    "业务负责人，缺失为严重": "business owner; missing is Critical",
    # --- v3.9 补：零散中文残句 ---
    "由其所属主账号管理，持 AK 行为请由对应业务方确认；本表不输出密钥 ID。":
        "Collaborators are managed by their owning primary account; key-holding should be confirmed by the corresponding business owner; this table does not output key IDs.",
    "未发现public-read/写桶，或 COS 这块本次无可用风险明细。":
        "No public-read/write buckets found, or no available COS risk detail this time.",
    "近 90 天内有使用": "Used within last 90 days",
    "个超过90天未使用": " keys idle >90 days",
    "个，按人员账号密钥明细collected，报告不输出密钥 ID":
        " keys; collected per user account key detail, key IDs not output in report",
    "个，按人员账号密钥明细已采集，报告不输出密钥 ID":
        " keys; collected per user account key detail, key IDs not output in report",
    "需主账号或补充只读权限复核": "needs primary account or added read-only permission to review",
    "当前巡检账号为子账号，需主账号本人复核": "the inspection caller is a sub-account; the primary account holder must review",
    "未确认加入组织": "organization membership unconfirmed",
    # 阶段计划阶段名 / 目标（出现在表格单元格）
    "第一阶段：风险收敛": "Phase 1: Risk Convergence",
    "直接暴露面和高权限风险下降，关键入口具备基本安全防护":
        "Direct exposure surface and high-privilege risks reduced; key entry points have basic security protection",
    "核心链路的单点、容量和性能风险得到评估和处理":
        "Single-point, capacity and performance risks on core paths are assessed and handled",
}

# ── 腾讯云产品名（封闭集，数据驱动来自 advisor / 资源名）。收多种写法，
#    longest-first 由 _TERMS 排序保证，"对象存储（COS）" 先于 "对象存储" 替换。──
_PRODUCTS = {
    # 计算
    "云服务器（CVM）": "Cloud Virtual Machine (CVM)",
    "云服务器CVM-GPU计算型PNV6": "CVM GPU Compute PNV6",
    "云服务器CVM-GPU渲染型GC88": "CVM GPU Render GC88",
    "云服务器CVM-标准型SA9": "CVM Standard SA9",
    "云服务器CVM": "Cloud Virtual Machine (CVM)",
    "云服务器 CVM": "Cloud Virtual Machine (CVM)",
    "轻量应用服务器-锐驰型（中国境内）": "Lighthouse Ruichi (China mainland)",
    "轻量应用服务器 (LH)": "Lighthouse (LH)",
    "轻量应用服务器": "Lighthouse (LH)",
    "云函数SCF": "Cloud Function (SCF)",
    "容器服务（TKE）": "Tencent Kubernetes Engine (TKE)",
    "容器服务 (TKE)": "Tencent Kubernetes Engine (TKE)",
    # 存储
    "对象存储（COS）": "Cloud Object Storage (COS)",
    "COS 对象存储": "COS Object Storage",
    "COS 标准存储": "COS Standard Storage",
    "云硬盘（CBS）": "Cloud Block Storage (CBS)",
    "云硬盘CBS": "Cloud Block Storage (CBS)",
    "通用型SSD云硬盘": "General-Purpose SSD CBS",
    "增强型SSD云硬盘": "Enhanced SSD CBS",
    "高性能云硬盘": "High-Performance CBS",
    "文件存储（CFS）": "Cloud File Storage (CFS)",
    "文件存储 (CFS)": "Cloud File Storage (CFS)",
    "数据万象CI": "Cloud Infinite (CI)",
    "智能媒资托管": "Smart Media Hosting (SMH)",
    "SMH-PAAS版服务": "SMH PaaS Service",
    # 网络
    "负载均衡（CLB）": "Cloud Load Balancer (CLB)",
    "负载均衡 CLB": "Cloud Load Balancer (CLB)",
    "负载均衡": "Cloud Load Balancer (CLB)",
    "弹性公网 IP（EIP）": "Elastic IP (EIP)",
    "弹性公网 IP（EIP）": "Elastic IP (EIP)",
    "公网 NAT 网关": "Public NAT Gateway",
    "NAT 网关": "NAT Gateway",
    "公网 IP": "Public IP",
    "公网域名": "Public Domain",
    "私有连接": "Private Link",
    "DDoS 高防": "Anti-DDoS Advanced",
    # 数据库 / 中间件
    "云数据库（PostgreSQL）": "TencentDB (PostgreSQL)",
    "云数据库（MongoDB）": "TencentDB (MongoDB)",
    "云数据库（MySQL）": "TencentDB (MySQL)",
    "云数据库（Redis）": "TencentDB (Redis)",
    "云数据库 (PostgreSQL)": "TencentDB (PostgreSQL)",
    "云数据库 (MongoDB)": "TencentDB (MongoDB)",
    "云数据库 (MySQL)": "TencentDB (MySQL)",
    "云数据库 (Redis)": "TencentDB (Redis)",
    "云数据库 MySQL": "TencentDB (MySQL)",
    "消息队列（Ckafka）": "Message Queue (CKafka)",
    "消息队列 (Ckafka)": "Message Queue (CKafka)",
    # 安全
    "公网安全中心资产": "CSC public asset",
    "云防火墙": "Cloud Firewall (CFW)",
    "堡垒机": "Bastion Host",
    "数据库审计": "Database Audit",
    # 注意：不收孤立的"云安全中心"短键 —— 它会与完整规则名/引用短语
    # （如 "云安全中心（CSC）发现主机未开启防护"）冲突造成 "(CSC)（CSC）" 双写；
    # CSC 相关一律走 _RULE_NAMES 完整键 + 下方 _REF_PHRASES 引用短语。
    # 日志 / 监控 / 审计
    "日志服务CLS": "Cloud Log Service (CLS)",
    "云监控": "Cloud Monitor",
    "Prometheus监控服务": "Managed Prometheus",
    "CloudAudit 跟踪集": "CloudAudit tracking set",
    # 音视频 / AI
    "点播媒体处理": "VOD Media Processing",
    "点播-按日结算": "VOD daily-settlement",
    "点播VOD": "Video on Demand (VOD)",
    "实时语音识别（后付费）": "Real-Time ASR (pay-as-you-go)",
    "实时语音识别（预付费）": "Real-Time ASR (prepaid)",
    "录音文件识别（预付费）": "Recording-File ASR (prepaid)",
    "腾讯云语音识别": "Tencent Cloud ASR",
    "腾讯云小微数智人": "Tencent Cloud Digital Human",
    "腾讯云智能体开发平台": "Tencent Cloud Agent Platform",
    "腾讯云TI平台TI-ONE": "Tencent Cloud TI Platform (TI-ONE)",
    "腾讯云代码助手-旗舰版": "Tencent Cloud Coding Copilot Ultimate",
    "腾讯云代码助手 (IDE) - 资源包": "Tencent Cloud Coding Copilot (IDE) resource pack",
    "腾讯云代码助手": "Tencent Cloud Coding Copilot",
    "图像分析TIIA": "Image Analysis (TIIA)",
    "基础图片处理": "Basic Image Processing",
    "文本内容安全/服务": "Text Moderation Service",
    "T-Sec-腾讯安全天御-文本内容安全": "T-Sec Tianyu Text Moderation",
    "免费资源包MaaS": "Free Resource Pack (MaaS)",
    "联网搜索API": "Web-Search API",
    "云联络中心": "Cloud Contact Center",
    "即时通信IM": "Instant Messaging (IM)",
    "资源组预付费": "Resource Group (prepaid)",
    "KMS 主密钥": "KMS master key",
    # --- v3.9 补：ADC 报告实测缺失的产品名 ---
    "云函数（SCF）": "Cloud Function (SCF)",
    "VPN网关（VPNGW）": "VPN Gateway (VPNGW)",
    "云联网": "Cloud Connect Network (CCN)",
    "云桌面": "Cloud Desktop",
    "云硬盘快照Snapshot": "CBS Snapshot",
    "云硬盘快照": "CBS Snapshot",
    "COS 多AZ标准存储": "COS Multi-AZ Standard Storage",
    "私有域解析 Private DNS": "Private DNS",
    "数据湖计算": "Data Lake Compute",
    "数据湖存储": "Data Lake Storage",
    "容器镜像服务（TCR）": "Tencent Container Registry (TCR)",
    # --- v3.9 补-2：ADC 报告第二轮缺失的产品名 ---
    "Web 应用防火墙（WAF）": "Web Application Firewall (WAF)",
    "消息队列（TDMQ）": "Tencent Distributed Message Queue (TDMQ)",
    "腾讯云可观测平台": "Cloud Monitor (Tencent Cloud Observability Platform)",
}

# ── 地域名（封闭集）──
_REGIONS = {
    "华南地区（广州）": "South China (Guangzhou)",
    "华北地区（北京）": "North China (Beijing)",
    "华东地区（上海）": "East China (Shanghai)",
    "西南地区（成都）": "Southwest China (Chengdu)",
    "西南地区（重庆）": "Southwest China (Chongqing)",
    "其他地区（其他）": "Other regions (others)",
    "北京市-北京市": "Beijing-Beijing",
    "广东省-深圳市": "Guangdong-Shenzhen",
    "广东省-广州市": "Guangdong-Guangzhou",
    "上海市-上海市": "Shanghai-Shanghai",
    "湖北省-武汉市": "Hubei-Wuhan",
    "中国香港": "Hong Kong, China",
    # --- v3.9 补：ADC 报告实测缺失的地域名 ---
    "华东地区（上海自动驾驶云）": "East China (Shanghai ADC)",
    "华南地区（上海自动驾驶云）": "South China (Shanghai ADC)",
    "欧洲地区（法兰克福）": "Europe (Frankfurt)",
    "东南亚地区（新加坡）": "Southeast Asia (Singapore)",
    "美国西部（硅谷）": "Western US (Silicon Valley)",
}

# ── UI 标题 / 角色名 / 状态枚举 / 阶段名（封闭集，我方骨架 + advisor 标签）──
_ENUMS = {
    # H2/H3/H4 标题（基础报告这些标题同时是客户报告 split_h2 的键，仅 report.md 英化，
    # 客户报告侧已在 split 前用中文键切片，互不影响 —— 见 generate_inspector_customer_report.py）
    "## 资产标签治理": "## Asset Tag Governance",
    "## 整改推进计划": "## Remediation Plan",
    "## 数据完整性与扫描降级说明": "## Data Integrity & Scan-Degradation Notes",
    "## 云安全": "## Cloud Security",
    "## 云可靠": "## Cloud Reliability",
    "## 云性能": "## Cloud Performance",
    "## 云限制": "## Cloud Limits",
    "### 标签完整性检查": "### Tag Completeness Check",
    "### 标签治理看板": "### Tag Governance Dashboard",
    "### CAM 账户安全": "### CAM Account Security",
    "### 云审计": "### CloudAudit",
    "### CVM 主机安全": "### CVM Host Security",
    "### SSL 证书": "### SSL Certificates",
    "### 网络与安全组": "### Network & Security Groups",
    "### 数据口径": "### Data Scope",
    "### 基线配置检查": "### Baseline Config Check",
    "### 配置审计合规率": "### Config-Audit Compliance Rate",
    "### 整体推进思路": "### Overall Approach",
    "### 阶段计划": "### Phased Plan",
    "### 责任协同建议": "### Responsibility & Collaboration",
    "### 下次复核指标": "### Next-Review Metrics",
    "### 复核范围": "### Review Scope",
    "### 重点资源汇总": "### Key Resource Summary",
    "### 按业务码分组的标签不完整资源": "### Tag-Incomplete Resources by Business Code",
    "### 云数据库（Redis / MySQL）": "### TencentDB (Redis / MySQL)",
    "#### 数据安全配置": "#### Data Security Config",
    "#### 网络基础设施": "#### Network Infrastructure",
    "#### 公网云资源清单": "#### Public Cloud Resource Inventory",
    "#### 安全产品覆盖": "#### Security Product Coverage",
    "#### 安全组扩展发现（云端策略）": "#### Security Group Extended Findings (Cloud Policy)",
    "#### 扩展安全发现（防盗链 / 公有读 / CORS）": "#### Extended Security Findings (Hotlink Protection / Public-Read / CORS)",
    "#### 空安全组分布（19 个地域聚合）": "#### Empty Security Group Distribution (aggregated over 19 regions)",
    "#### MySQL 非 root 高危权限": "#### MySQL Non-root High-Risk Privileges",
    "#### Redis 高危命令风险": "#### Redis High-Risk Command Risk",
    "#### 安全组规则与高危端口": "#### Security Group Rules & High-Risk Ports",
    # 阶段名
    "第二阶段：稳定性治理": "Phase 2: Stability Governance",
    "第三阶段:账号与密钥治理": "Phase 3: Account & Key Governance",
    "第三阶段：账号与密钥治理": "Phase 3: Account & Key Governance",
    "第四阶段:治理基线完善": "Phase 4: Governance Baseline Refinement",
    "第四阶段：治理基线完善": "Phase 4: Governance Baseline Refinement",
    # 角色名 / 责任人
    "安全负责人、各业务负责人": "Security lead, each business lead",
    "业务系统负责人、数据库负责人": "Business-system lead, database lead",
    "财务/成本负责人、业务负责人": "Finance/cost lead, business lead",
    "存储、数据库、业务系统负责人": "Storage, database, business-system leads",
    "网络、运维、业务系统负责人": "Network, ops, business-system leads",
    "安全与平台负责人": "Security & platform lead",
    "平台/运维负责人": "Platform/ops lead",
    "账号权限管理员": "Account & Permission Admin",
    "云资源管理员": "Cloud Resource Admin",
    "安全联系人": "Security contact",
    "安全负责人": "Security lead",
    # 状态 / 枚举 / 短标签
    "✅ 全部已归属": "✅ All attributed",
    "平台未返回可定位资源明细": "platform returned no locatable resource details",
    "可登录子用户持有 AK": "login-enabled sub-user holds AK",
    "子账号（可登录控制台）": "sub-account (console login allowed)",
    "子账号（禁止登录控制台）": "sub-account (console login disabled)",
    "—（系统角色）": "— (system role)",
    "主账号持有 AK": "primary account holds AK",
    "协助者持有 AK": "collaborator holds AK",
    "离职/未活跃账号": "departed/inactive accounts",
    "AK/SK 权限审查": "AK/SK privilege review",
    "AK 生命周期": "AK lifecycle",
    "AK 闲置": "AK idle",
    "MFA 开启": "MFA enabled",
    "MFA 变更": "MFA change",
    "用户 CRUD": "User CRUD",
    "角色 CRUD": "Role CRUD",
    "审计反取证": "audit anti-forensics",
    "资源毁灭": "resource destruction",
    "CLS/COS 投递": "CLS/COS delivery",
    "用户组归属": "user-group membership",
    "组织归属": "organization membership",
    "消息订阅": "message subscription",
    "权限授予": "privilege grant",
    "安全组改动": "security-group change",
    "公司访问": "corporate access",
    "运维中心": "Ops Center",
    "策略管理": "Policy Management",
    "公有读": "public-read",
    "未采集": "not collected",
    "已采集": "collected",
    "从未使用": "never used",
    "月份窗口": "month window",
    "覆盖范围": "coverage",
    "月度计费精度差异": "monthly billing-precision variance",
    "月度计费精度差异": "monthly billing-precision variance",
    "未发现实例": "no instances found",
    "缺 ACL 子网": "subnets missing ACL",
    "WAF回源安全组": "WAF origin-pull security group",
    "API 不可用": "API unavailable",
    "启用=False": "Enabled=False",
    "其他": "Other",
    "必填": "Required",
    "合计": "Total",
    "接口": "API",
    "等保": "MLPS",
    "业务标识": "Business ID",
    "客户标识": "Customer ID",
    "按月订阅费": "monthly subscription fee",
    "固定号码时长": "fixed-number duration",
    "公网暴露": "Public Exposure",
    "身份权限": "Identity & Permissions",
    "主机与证书": "Hosts & Certificates",
    "TKE 稳定性": "TKE Stability",
    "可靠性与性能": "Reliability & Performance",
    "资产标签": "Asset Tags",
    "巡检账户": "Inspection Account",
    "明细完整性": "Detail Completeness",
    "需人工确认项": "Manual-Confirmation Items",
    "基线配置": "Baseline Config",
    "安全产品覆盖": "Security Product Coverage",
    "配置审计": "Config Audit",
    "账号总量统计": "Account Total Statistics",
    "协助者": "Collaborator",
    # --- v3.9 补：ADC 报告实测缺失的短枚举 ---
    "个：": ": ",
    "未命名_系统盘": "unnamed_system_disk",
    "系统盘": "system disk",
    # --- v3.9 补-3：零散短枚举 ---
    "6 个 LB 未绑定": "6 LBs unbound",
}

# ── 第三批：中长短语（产品后缀 / advisor 引用句 / 责任人聚合 / 列表枚举句）。
#    均为长键，longest-first 保证先于产品/枚举短键命中，避免子串被吃。──
_PHRASES = {
    # 产品名后缀（公网入口分类）
    "/ 公网主机": "/ public host",
    "/ 公网入口": "/ public ingress",
    "公网主机": "public host",
    # advisor 引用短语（「域 → 产品名规则」above; same remediation 句的中文残段）
    "云安全中心（CSC）发现公网 IP 未开启防护": "CSC: public IPs found without protection enabled",
    "云安全中心（CSC）发现主机未开启防护": "CSC: hosts found without protection enabled",
    "云安全中心（CSC）数据库未开启安全防护": "CSC: databases without security protection enabled",
    "云安全中心（CSC）域名未开启防护": "CSC: domains without protection enabled",
    "发现公网 IP 未开启防护": "found public IPs without protection enabled",
    "发现主机未开启防护": "found hosts without protection enabled",
    "数据库未开启安全防护": "found databases without security protection enabled",
    "域名未开启防护": "found domains without protection enabled",
    "未创建快照": "no snapshot created",
    "实例内存使用率过高": "instance memory utilization too high",
    "带宽高负载": "bandwidth under high load",
    "巡检策略": "inspection policy",
    "未完全授权，需补充只读权限后复扫": "not fully authorized; add read-only permission and re-scan",
    # 资源-规则矩阵单元格（存储桶 …风险 / …）
    "存储桶日志投递失败风险": "bucket log delivery failure risk",
    "存储桶未启用版本控制": "bucket versioning not enabled",
    "存储桶未配置多 AZ 特性": "bucket multi-AZ feature not configured",
    "存储桶配置公有读权限风险": "bucket public-read permission risk",
    "存储桶未配置防盗链": "bucket hotlink-protection not configured",
    "存储桶未配置跨域访问": "bucket cross-origin access not configured",
    # AdministratorAccess 来源括注
    "（直接 + 用户组）": " (direct + user group)",
    "（用户组）": " (user group)",
    "（组织管控自动注入，非Collaborator）": " (auto-injected by org control, not a collaborator)",
    "（组织管控自动注入，非协助者）": " (auto-injected by org control, not a collaborator)",
    "组织管控系统角色（Organization）": "Org-Control System Role (Organization)",
    "Administrator 权限": "Administrator privilege",
    # CWP / 风险维度
    "异地 风险用户 异常时段 风险IP": "cross-region / risky-user / abnormal-window / risky-IP",
    "安全组添加规则": "security-group rule added",
    "建议核心子网绑定网络 ACL": "Recommend binding core subnets to network ACLs",
    # 责任人聚合句（下次复核 / 责任协同）
    "业务系统负责人、数据库负责人": "Business-system lead, database lead",
    "财务/成本负责人、业务负责人": "Finance/cost lead, business lead",
    "存储、数据库、业务系统负责人": "Storage, database, business-system leads",
    "网络、运维、业务系统负责人": "Network, ops, business-system leads",
    "安全负责人、各业务负责人": "Security lead, each business lead",
    # 下次复核指标 / 阶段目标 列表句
    "安全组公网开放、过期证书、CWP 主机安全组件缺口、暴力破解成功事件、高权限人员账号、数据库/缓存高危配置":
        "Public-open security groups, expired certs, CWP host-agent gaps, brute-force successes, high-privilege accounts, database/cache high-risk configs",
    "TKE 级联删除、Pod OOM、CLB 健康检查、NAT、CKafka、数据库、CVM/EIP 性能与可靠性问题":
        "TKE cascading deletion, Pod OOM, CLB health checks, NAT, CKafka, database, CVM/EIP performance & reliability issues",
    "全协议对公网开放安全组数量下降，高危端口开放规则收敛到明确来源地址":
        "Fewer all-protocol public-open security groups; high-risk-port rules converged to explicit source addresses",
    "主机安全组件覆盖率提升，暴力破解成功事件完成处置，过期证书完成续签或下线":
        "Host-security agent coverage improved; brute-force successes remediated; expired certs renewed or retired",
    "级联删除和 Pod OOM 风险有处置记录，关键集群对象与异常 Pod 数量下降":
        "Cascading-deletion and Pod-OOM risks have remediation records; critical cluster objects and abnormal Pods reduced",
    "NAT、CLB、CKafka、数据库、CVM、EIP 等关键风险有架构整改、容量调整或风险接受记录":
        "Key risks on NAT, CLB, CKafka, database, CVM, EIP have architecture fixes, capacity adjustments, or risk-acceptance records",
    "标签不完整资源完成补标（当前 122 项），尤其是缺少负责人标签的资源优先清零":
        "Tag-incomplete resources are fully tagged (currently 122); resources missing an owner tag are cleared first",
    # --- v3.9 补：零散中文残句 ---
    "标签不完整资源完成补标（当前 46 项），尤其是缺少负责人标签的资源优先清零":
        "Complete tagging for incomplete-tag resources (currently 46), prioritizing resources missing the owner tag",
    "身份权限边界清晰，长期不用的访问入口完成回收":
        "Identity/permission boundaries are clear; long-unused access entries are reclaimed",
    "责任归属、审计追溯、数据保护和恢复能力形成长期机制":
        "Accountability, audit traceability, and data protection/recovery become long-term mechanisms",
    # 复核范围 / 数据口径 列表句
    "资源标签、CloudAudit、COS 安全配置、备份策略、跨可用区部署":
        "Resource tags, CloudAudit, COS security config, backup policy, cross-AZ deployment",
    "主账号 AK、CloudAudit 长期跟踪、COS 全量桶安全配置、CWP 攻击事件细分等需由具备相应权限的负责人最终确认":
        "Primary-account AK, long-term CloudAudit tracking, full COS bucket security config, and CWP attack-event breakdown require final confirmation by an authorized owner",
    "长期未登录账号、闲置 AK、Collaborator AK、未归属用户组":
        "Long-inactive accounts, idle AKs, collaborator AKs, unassigned user groups",
    "长期未登录账号、闲置 AK、协助者 AK、未归属用户组":
        "Long-inactive accounts, idle AKs, collaborator AKs, unassigned user groups",
    "公网安全组、WAF、证书、主机安全": "Public security groups, WAF, certs, host security",
    "CAM 账号、权限、AK、用户组": "CAM accounts, permissions, AKs, user groups",
    "CloudAudit、COS、备份和恢复策略": "CloudAudit, COS, backup & recovery policy",
    "仅覆盖该月已产生消费的资源，无消费资源未纳入":
        "covers only resources that incurred spend this month; resources with no spend are excluded",
    "二选一": "(choose one)",
    # 标题/小标题残段
    "标签不完整资源明细": "Tag-Incomplete Resource Details",
    "云安全、云可靠、云性能、云限制、Asset Tags治理五类问题":
        "five categories: Cloud Security, Cloud Reliability, Cloud Performance, Cloud Limits, Asset-Tag Governance",
    "安全组、AK idle、标签明细、Reliability & Performance风险等长表均应在文档中展开":
        "Long tables such as security groups, idle AKs, tag details, and reliability/performance risks should be expanded in the document",
    "Identity & Permissions边界清晰，长期不用的访问入口完成回收":
        "Identity/permission boundaries are clear; long-unused access entries are reclaimed",
    "Asset Tags、费用归属、资源生命周期": "Asset tags, cost attribution, resource lifecycle",
    "只绑定1个 EIP": "bound to only 1 EIP",
    "未开启流日志": "flow logs not enabled",
    # —— 第四批：半角括号 CSC 引用 / 标题完整键 / 矩阵产品组合 / OPA 长建议 / 小残段 ——
    # 半角括号版 CSC 规则引用（cleanup 路径产生 "云安全中心 (CSC)" 半角空格+半角括号）
    "云安全中心 (CSC)发现Public IP 未开启防护": "CSC: public IPs found without protection enabled",
    "云安全中心 (CSC)发现公网 IP 未开启防护": "CSC: public IPs found without protection enabled",
    "云安全中心 (CSC)found hosts without protection enabled": "CSC: hosts found without protection enabled",
    "云安全中心 (CSC)found databases without security protection enabled": "CSC: databases found without security protection enabled",
    "云安全中心 (CSC)found domains without protection enabled": "CSC: domains found without protection enabled",
    "云安全中心 (CSC)": "CSC",
    "云安全中心（CSC）": "CSC",
    # 完整标题键（必须先于 "## 云安全" 等短键，longest-first 已保证）
    "### 云安全中心（CSC）发现公网 IP 未开启防护": "### CSC: public IPs found without protection enabled",
    "### 云安全中心（CSC）": "### Cloud Security Center (CSC)",
    "### 数据口径与采集说明": "### Data Scope & Collection Notes",
    "数据口径与采集说明": "Data Scope & Collection Notes",
    # 矩阵 risk-rule 引用尾段（规则名已英化，仅"（另含 N 条规则未在此处列出）"中文残留——
    # 这是表格单元格内容，frame 的 ^$ 锚不上，故用子串收尾）
    "（另含 1 条规则未在此处列出）": " (plus 1 more rule not listed here)",
    "（另含 2 条规则未在此处列出）": " (plus 2 more rules not listed here)",
    "（另含 3 条规则未在此处列出）": " (plus 3 more rules not listed here)",
    "（另含 4 条规则未在此处列出）": " (plus 4 more rules not listed here)",
    # OPA 策略整改长建议（5 条，模板化：策略名不同，前后缀一致）
    "在腾讯云控制台容器服务找到对应集群，在集群详情-": "find the cluster in Tencent Cloud Console > TKE, then in cluster details -",
    "优选策略页面，找到策略名称为": "go to the Preferred Policies page, find the policy named",
    "的策略，点击新建实例，实例内容保持为默认的内容。创建完成后，确认策略运行模式为deny即可，参考":
        ", click New Instance keeping the default content; after creation, confirm the policy run-mode is deny. See",
    "的策略，将运行模式切换为 deny，参考": ", switch its run-mode to deny. See",
    "的策略，将运行模式切换为deny，参考": ", switch its run-mode to deny. See",
    "。也可以调用API": ". You can also call the API",
    "。也可以调用 云API ModifyOpenPolicyList 完成策略创建。": ". You can also call the cloud API ModifyOpenPolicyList to create the policy.",
    "完成策略创建。": " to create the policy.",
    "完成策略切换。": " to switch the policy.",
    "存在pod的命名空间不允许删除": "namespaces with pods cannot be deleted",
    "存在 cr 的 crd 不允许删除": "CRDs with CRs cannot be deleted",
    "非封锁状态的 Node 不允许删除": "non-cordoned Nodes cannot be deleted",
    "PV 处于绑定状态则不允许删除": "bound PVs cannot be deleted",
    "CoreDNS 组件删除保护": "CoreDNS component deletion protection",
    "批量修改opa策略": "Batch-modify OPA policies",
    # 子账号跟踪集复核提示
    "当前巡检账号为子账号": "the current inspection caller is a sub-account",
    "跟踪集配置需主账号本人复核": "tracking-set config must be reviewed by the primary account holder",
    # 数据口径长句（去重说明）
    "数据口径：原始命中": "Data scope: raw hits",
    "条已按 (安全组 ID, 端口, 协议) 去重为": "deduplicated by (security group ID, port, protocol) to",
    "条；同一规则在采集中被多次记录的重复行已合并。":
        "; duplicate rows recorded multiple times during collection have been merged.",
    # 计费 API 标签为空说明
    "（TagKey=head/business/customer, TagValue 为空）": " (TagKey=head/business/customer, TagValue empty)",
    "TagValue 为空": "TagValue empty",
    # 公网风险三视角句
    "平台策略 + 安全组 + ": "platform policy + security group + ",
    "三视角交叉": "cross-referenced across three views",
    # 责任协同表格首列（多产品缩写串）
    "数据库、CVM/EIP": "database, CVM/EIP",
    "费用归属、资源生命周期": "cost attribution, resource lifecycle",
    # 残段小词
    "Administrator privilege人员账号减少": "Reduce accounts with Administrator privilege",
    "等长表均应在文档中展开": " — such long tables should be expanded in the document",
    "风险等长表均应在文档中展开": " risks — such long tables should be expanded in the document",
    "治理五类问题": " governance — five categories of issues",
    "命中次数": "Hit Count",
    "配置 0": "0 configured",
    "域名 0": "0 domains",
    "BGP 实例 0": "0 BGP instances",
    "云可靠": "Cloud Reliability",
    # —— 第五批：行内"框架词"残段（引用句框架 / 章节标题框架 / OPA 连接词）——
    # 引用句框架：「公网云资源 → …」above; same remediation —— 收 「域 框架词与连接符
    "Same as section 「公网云资源 → ": "Same as section 「Public Cloud Resources → ",
    "Same as section 「云端策略 → ": "Same as section 「Cloud Policy → ",
    # CSC 引用残尾（短键 云安全中心 (CSC)→CSC 命中后留 found...，补 "CSC" 与 "found" 间空格 +
    #  防止把 "CSC: public..." 再处理；这里仅补缺空格的情况）
    "CSCfound hosts without protection enabled": "CSC: hosts found without protection enabled",
    "CSCfound databases without security protection enabled": "CSC: databases found without security protection enabled",
    "CSCfound domains without protection enabled": "CSC: domains found without protection enabled",
    # 产品名 (CBS)/(CVM)/(EIP) 引用残尾（产品名已英化，前面有中文产品名残留）
    "云硬盘 (CBS)no snapshot created": "CBS: no snapshot created",
    "云服务器 (CVM)instance memory utilization too high": "CVM: instance memory utilization too high",
    "EIP (EIP)bandwidth under high load": "EIP: bandwidth under high load",
    "云硬盘 (CBS)": "CBS",
    "云服务器 (CVM)": "CVM",
    "云服务器 (CVM)": "CVM",
    # 章节标题框架
    "安全组扩展发现": "Security Group Extended Findings",
    "#### 安全组扩展发现（cloud policy）": "#### Security Group Extended Findings (Cloud Policy)",
    # 公网风险三视角行（剩 框架词）
    "**公网风险**：以 ": "**Public Exposure Risk**: ",
    "公网风险": "Public Exposure Risk",
    # OPA 连接词残段
    "建议find the cluster": "Recommend: find the cluster",
    "。也可以调用API": ". You can also call the API ",
    " to switch the policy.": " to switch the policy.",
    "”，": "\", ",
    "“": "\"",
    "”": "\"",
    # Administrator 长句残段（人员账号减少…）
    "Administrator privilege人员账号减少（当前 5 个），长期未登录账号和闲置 AK 完成禁用或删除（当前闲置 AK 13 个）":
        "Reduce accounts with Administrator privilege (currently 5); disable or delete long-inactive accounts and idle AKs (currently 13 idle AKs)",
    # 责任协同表残留聚合串
    "云安全、Cloud Reliability、云性能、云限制、Asset Tags governance — five categories of issues":
        "Cloud Security, Cloud Reliability, Cloud Performance, Cloud Limits, Asset-Tag Governance — five categories of issues",
    "安全组、AK idle、标签明细、Reliability & Performance risks — such long tables should be expanded in the document":
        "Security groups, idle AKs, tag details, reliability & performance risks — such long tables should be expanded in the document",
    "(共 10 条)": " (10 items)",
    # Rule 单元格残留
    "- **Rule**：ALL:ALL 对 0.0.0.0/0": "- **Rule**: ALL:ALL to 0.0.0.0/0",
    " 对 0.0.0.0/0": " to 0.0.0.0/0",
    # —— 第六批：顿号列表元素 + 残留连接符（逐词收，避免整句键被短键破坏）——
    # 五类问题 / 长表清单等顿号列表的基础词（这些词单独出现在多处列表里）
    "云安全": "Cloud Security",
    "云性能": "Cloud Performance",
    "云限制": "Cloud Limits",
    "标签明细": "tag details",
    "安全组": "security group",
    "费用归属": "cost attribution",
    "资源生命周期": "resource lifecycle",
    "资产标签治理": "Asset-Tag Governance",
    "Asset Tags治理": "Asset-Tag Governance",
    # OPA 长建议尾部中文句号 + 连接（原文 "参考[...]。也可以调用接口[...]完成策略切换。"）
    # 注意：键必须用原文"接口"而非"API"——"接口"会被 _ENUMS 提前替换成 API，
    # 但本键(更长)在原文阶段就应先命中，longest-first 已保证。
    "。也可以调用接口": ". You can also call the API",
    "。也可以调用 云API ModifyOpenPolicyList": ". You can also call the cloud API ModifyOpenPolicyList",
    # Administrator 长句（带全角括号数字，逐段收）
    "Administrator privilege人员账号减少（当前 5 个），长期未登录账号和闲置 AK 完成禁用或删除（当前闲置 AK 13 个）":
        "Reduce accounts with Administrator privilege (currently 5); disable or delete long-inactive accounts and idle AKs (currently 13 idle AKs)",
    "人员账号减少（当前 ": " accounts reduced (currently ",
    " 个），长期未登录账号和闲置 AK 完成禁用或删除（当前闲置 AK ":
        "); disable or delete long-inactive accounts and idle AKs (currently ",
    " 个）": ")",
}

# 合并并按 key 长度降序，保证 longest-first（长键先替换，避免被短键吃掉公共子串）
_TERMS = sorted(
    {**_RULE_NAMES, **_ADVICE, **_SKELETON, **_PRODUCTS, **_REGIONS, **_ENUMS, **_PHRASES}.items(),
    key=lambda kv: -len(kv[0]),
)

# ────────────────────────────────────────────────────────────────────────
# C. 动态翻译缓存 + Agent LLM 自动补全层
#    解决核心痛点：静态字典永远追不上每个客户账号的动态数据。
#    三层架构：① 静态字典（快、免费、术语一致）
#              ② 翻译缓存（跨账号复用，持久化 JSON）
#              ③ 未翻译串提取 → Agent LLM 补全 → 写回缓存
#    用户自定义资源名（实例名/安全组名/标签值等）通过启发式跳过，不翻译。
# ────────────────────────────────────────────────────────────────────────
import json
import os
from pathlib import Path

# 缓存文件路径：全局共享，跨账号复用（TMD 规则名/repair 在不同账号间高度重叠）
_TRANSLATE_CACHE_PATH = Path(
    os.environ.get("INSPECTOR_TRANSLATE_CACHE",
                   str(Path.home() / ".cache" / "cloudops-inspector" / "translate_cache.json"))
)

# 未翻译串输出路径（供 Agent LLM 读取并补全）
_UNTRANSLATED_PATH = Path(
    os.environ.get("INSPECTOR_UNTRANSLATED",
                   str(Path.home() / ".cache" / "cloudops-inspector" / "untranslated.json"))
)

# ── 启发式：识别"用户自定义资源名"，这些不应翻译 ──
# 特征：短片段（<6 中文字）+ 混合英文/数字/连字符，且不含标准产品/规则关键词
_USER_DEFINED_PATTERNS = re.compile(
    r"(?:"
    # 安全组名/实例名：中英混排带连字符 "sgm-windows-远程测试SDK专用"
    r"[A-Za-z0-9_\-]+[\u4e00-\u9fff]+|[^\u4e00-\u9fff]*[\u4e00-\u9fff]+[A-Za-z0-9_\-]"
    r"|"
    # 纯短中文 ≤3 字，出现在表格数据单元格（可能是备注/标签值）
    r"^[\u4e00-\u9fff]{1,3}$"
    r")"
)

# 标准关键词：如果中文字符串包含这些，大概率是 API 返回的标准文案，应该翻译
_STANDARD_KEYWORDS = [
    "建议", "请", "参考", "查看", "配置", "开启", "升级", "创建", "删除", "修改",
    "检查", "评估", "确认", "清理", "禁用", "启用", "接入", "部署", "迁移", "扩容",
    "风险", "安全", "防护", "备份", "监控", "日志", "权限", "账号", "密钥", "证书",
    "集群", "实例", "存储", "网络", "域名", "端口", "规则", "策略", "版本", "快照",
    "跨可用区", "单点", "级联", "高可用", "容灾", "恢复", "可靠性", "性能", "利用",
    "未开启", "未配置", "未创建", "未安装", "未绑定", "未接入", "未启用", "未授权",
    "过高", "突增", "单副本", "无快照", "未归属",
]


def _is_user_defined_name(s: str) -> bool:
    """判断一个中文字符串是否为用户自定义资源名（不应翻译）。

    启发式规则：
    1. 中英混排带连字符/下划线 → 大概率是资源名（如 "sgm-windows-远程测试SDK专用"）
    2. 纯短中文 ≤3 字且不含标准关键词 → 大概率是备注/标签值（如 "勿删"、"长安poc"）
    3. 包含标准关键词（建议/请/风险/配置等）→ 一定不是资源名，应翻译
    """
    if not s or not re.search(r"[\u4e00-\u9fff]", s):
        return False
    # 规则 3：含标准关键词 → 不是资源名
    for kw in _STANDARD_KEYWORDS:
        if kw in s:
            return False
    # 规则 1：中英混排带连字符/下划线/数字（且无标准关键词）
    if re.search(r"[A-Za-z0-9_\-]", s) and re.search(r"[\u4e00-\u9fff]", s):
        # 但如果是 "云数据库（MySQL）" 这种标准产品名格式，不应跳过
        if re.search(r"[（(][A-Za-z]+[)）]", s):
            return False
        return True
    # 规则 2：纯短中文 ≤3 字
    chinese_only = re.sub(r"[^\u4e00-\u9fff]", "", s)
    if len(chinese_only) <= 3:
        return True
    return False


def _load_translate_cache() -> dict:
    """加载翻译缓存 JSON。返回 {中文: 英文} 字典。"""
    try:
        if _TRANSLATE_CACHE_PATH.exists():
            with open(_TRANSLATE_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_translate_cache(cache: dict) -> None:
    """保存翻译缓存 JSON。"""
    _TRANSLATE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_TRANSLATE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _apply_cache_pass(text: str, cache: dict) -> str:
    """应用翻译缓存（longest-first，与 _TERMS 相同策略）。"""
    if not cache:
        return text
    for zh, en in sorted(cache.items(), key=lambda kv: -len(kv[0])):
        if zh in text:
            text = text.replace(zh, en)
    return text


def _extract_untranslated_chinese(text: str) -> list[dict]:
    """提取翻译后仍残留的中文字符串（排除用户自定义资源名）。

    返回 [{"text": "原始中文", "context": "所在行前80字", "skip": true/false}]
    skip=true 表示是用户自定义名，不应翻译。
    """
    results = []
    seen = set()
    for line in text.split("\n"):
        # 找连续中文字符片段（≥2字）
        for m in re.finditer(r"[\u4e00-\u9fff][\u4e00-\u9fff\u3000-\u303f\uff00-\uffefA-Za-z0-9_\-（）()《》【】、，。！？：；""''·…—\s/]+", line):
            snippet = m.group().strip()
            if len(snippet) < 2 or snippet in seen:
                continue
            seen.add(snippet)
            is_ud = _is_user_defined_name(snippet)
            results.append({
                "text": snippet,
                "context": line[:80],
                "skip": is_ud,
            })
    return results


def _write_untranslated(items: list[dict], report_path: str = "") -> str:
    """将未翻译串写入 JSON 文件，供 Agent LLM 读取并补全。

    返回写入的文件路径。
    """
    _UNTRANSLATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_path": report_path,
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "instructions": (
            "Translate each item's 'text' from Chinese to English. "
            "Skip items where skip=true (user-defined resource names). "
            "Write results to the same cache file as a JSON object {Chinese: English}. "
            "Then re-run the report generation script."
        ),
        "items": items,
    }
    with open(_UNTRANSLATED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(_UNTRANSLATED_PATH)


def update_cache_from_dict(new_translations: dict) -> int:
    """从外部字典更新翻译缓存（供 Agent LLM 补全后调用）。

    返回新增条目数。
    """
    cache = _load_translate_cache()
    added = 0
    for zh, en in new_translations.items():
        if zh not in cache and en:
            cache[zh] = en
            added += 1
    if added:
        _save_translate_cache(cache)
    return added


def translate_report(text: str, lang: str = "zh") -> str:
    """对装配好的整篇报告做输出层翻译。lang=='zh' 原样返回（零行为变化）。

    三层架构：
    ① 帧正则 + 静态字典（快、免费、术语一致）
    ② 翻译缓存（跨账号复用，持久化 JSON）
    ③ 提取残留中文 → 输出到 untranslated.json（供 Agent LLM 自动补全）
    """
    if lang != "en" or not text:
        return text
    # ① 先跑帧（按行，多为整行模板句），再跑全文子串字典
    out_lines = []
    for line in text.split("\n"):
        s = line
        for rx, rep in _FRAMES:
            s2 = rx.sub(rep, s)
            if s2 != s:
                s = s2
        out_lines.append(s)
    text = "\n".join(out_lines)
    # ② 子串字典 longest-first（含规则名 / 建议 / 骨架 / 枚举）
    for zh, en in _TERMS:
        if zh in text:
            text = text.replace(zh, en)
    # ③ 翻译缓存 pass（跨账号复用，持久化 JSON）
    cache = _load_translate_cache()
    text = _apply_cache_pass(text, cache)
    # ④ 提取残留中文 → 输出到 untranslated.json
    remaining = _extract_untranslated_chinese(text)
    translatable = [it for it in remaining if not it["skip"]]
    if translatable:
        _write_untranslated(remaining)
    elif _UNTRANSLATED_PATH.exists():
        # 全部翻完了，清理 untranslated 文件
        _UNTRANSLATED_PATH.unlink(missing_ok=True)
    return text
