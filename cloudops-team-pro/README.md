# 云运维专家团 · Cloud Ops Team

一站式云运维 AIOps 专家团。账单、审计、巡检、降本多位专家分工协作，你用大白话说需求，主理人「云掌柜」自动识别意图、调度对应专家出活——全程不用记命令、不用切工具。

## 类型

Team 型（多角色协作团队）

## 成员

| 成员 | 名字 | 职业 | 承接场景 |
|---|---|---|---|
| `cloudops-team-pro-team-lead` | 云掌柜 | 云运维经理 | 意图识别、编排调度、汇总回复 |
| `cloud-billing-analyst-pro` | 老账 | 云账单分析专家 | 账单/费用/成本拆解/环比 |
| `cloud-auditor-pro` | 云审计官 | 云审计分析师 | 审计日志/高危操作/取证 |
| `cloud-inspector-pro` | 云巡检 | 云安全巡检工程师 | 安全巡检/风险发现/合规 |
| `cloud-cost-optimizer-pro` | 云降本医生 | 云降本优化专家 | 降本/省钱/闲置资源/规格降配 |

> 规划中：故障诊断、资源管理等专家将陆续加入。

## 功能

- 💰 **云账单分析**：费用构成 / 同环比 / 资源级 TOP 消费 / 异常消费定位 + 可视化看板
- 🔍 **云审计取证**：9 大类高危事件扫描 + 操作者&时间线取证报告
- 🛡️ **云安全巡检**：12 域配置项扫描 + 4 级风险定级 + 处置时限 + HTML 风险报告
- 📉 **云降本优化**：账单驱动 + 30 天监控数据 + 风险分级的可执行降本建议 + 节省预估

所有专家统一以 **SSM + KMS 作为唯一凭证源**：各云账号 AK/SK 以 KMS 加密后存放在腾讯云凭据管理服务（SSM），secret 名 `cloudops/{cloud}/{project}`（如 `cloudops/tencent/ADC`、`cloudops/huawei/TSP`）。专家运行时仅凭一把 **runner key** 在线读取，KMS 解密在 SSM 服务端由服务角色完成，调用方无需任何 kms 权限。

宿主侧只需配置 runner key（仅需 SSM 只读权限 `ssm:GetSecretValue` / `ListSecrets` / `ListSecretVersionIds`）：
- `TENCENTCLOUD_BOOTSTRAP_SECRET_ID` / `TENCENTCLOUD_BOOTSTRAP_SECRET_KEY`（必填）
- `TENCENTCLOUD_BOOTSTRAP_REGION`（可选，SSM 地域，默认 `ap-guangzhou`）

配好 runner key 即自动启用 SSM（无需 `CLOUDOPS_SSM_ENABLED=true`）。华为云账号凭证同样存于 SSM，仍用腾讯云 runner key 拉取（SSM 是腾讯云服务）。

业务账号 AK/SK **绝不**经 shell env / 对话 / 文件传递，专家只读不写。多账号时主理人会弹自适应选择器（单云/单项目自动跳过；项目支持多选 ≤3 并发）。

## 使用示例

- 看一下上月云账单（单账号直派；多账号弹选择器）
- 分析腾讯云 XX 项目上月账单（XX 替换成你配的项目代号，关键词直派 0 步交互）
- 对比一下我所有项目上月账单（多选项目并发跑，最多 3 个并行）
- 检查最近有没有高危操作
- 全面巡检我的云账号 + 给我降本建议

## 头像

头像已生成在 `avatars/` 目录下。如需替换为自定义头像，要求：
- 格式：PNG（推荐）或 JPG
- 尺寸：512×512 px
- 大小：单张不超过 500KB

## 安装

将专家包目录放到以下路径：

```
~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team/
```

## 打包分享

```bash
zip -r cloudops-team.zip cloudops-team/
```
