# 00 总览与巡检准备

> SOP: TC-SEC-SOP-001 | 巡检准备阶段

## 巡检频率

| 巡检类型 | 频率 | 执行方式 | 适用场景 |
|---------|------|---------|---------|
| 日常巡检 | 每日 | 自动化 + 人工抽检 | 安全态势感知 |
| 周度巡检 | 每周 | 自动化报告 + 人工复核 | 风险趋势分析 |
| 月度巡检 | 每月 | 全量人工巡检 | 全面安全评估 |
| 专项巡检 | 按需 | 专人执行 | 护网 / 攻防演练 |
| 上线前巡检 | 每次上线 | 流水线集成 | 新业务发布 |

## 角色与职责

| 角色 | 职责 | 要求 |
|------|------|------|
| 安全巡检负责人 | 制定计划、汇总报告、推动闭环 | 安全团队 |
| 云资源管理员 | 配合提供账号权限、协助梳理 | 运维/DevOps |
| 业务负责人 | 确认资产清单、响应工单 | 业务侧 |
| 安全接口人 | 接收告警、跟进修复 | 各业务指定 |

## 工具准备

| 工具/平台 | 用途 | 访问地址 |
|---------|------|---------|
| 腾讯云控制台 | 资源配置检查 | console.cloud.tencent.com |
| 云审计 CloudAudit | API调用审计 | console.cloud.tencent.com/cloudaudit |
| 云安全中心 | 安全评分 | console.cloud.tencent.com/ssa |
| 主机安全(云镜) | 主机入侵检测 | console.cloud.tencent.com/cwp |
| WAF | Web防护状态 | console.cloud.tencent.com/guanjia |
| CAM 访问管理 | 权限审计 | console.cloud.tencent.com/cam |
| CLS 日志服务 | 日志检索分析 | console.cloud.tencent.com/cls |
| 漏洞扫描服务 | 漏洞检测 | console.cloud.tencent.com/vss |

## 资产清单梳理

执行巡检前需完成以下资产盘点：

- [ ] 云服务器 CVM 实例清单（含地域、VPC、安全组绑定）
- [ ] 轻量应用服务器 Lighthouse 清单
- [ ] 对象存储 COS 存储桶清单
- [ ] 云数据库实例清单（MySQL/Redis/PostgreSQL/MongoDB等）
- [ ] 负载均衡 CLB 实例与域名映射
- [ ] 容器服务 TKE/EKS 集群清单
- [ ] 弹性公网 IP / NAT 网关 / VPN 网关清单
- [ ] 域名与 SSL 证书清单
- [ ] CAM 子账号与 API 密钥清单
- [ ] 云资产标签情况（一级业务/二级业务/负责人）
