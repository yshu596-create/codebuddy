# 07 TKE 容器安全巡检

> SOP: TC-SEC-SOP-001 | 章节 07  
> 操作路径：TKE → https://console.cloud.tencent.com/tke2

## 7.1 集群访问安全

| 检查项 | 操作方法 | 判定标准 | 风险等级 | 处置 |
|--------|---------|---------|---------|------|
| APIServer 公网 | 集群 > 基本信息 | 非必要不开放；须配 IP 白名单 | 🔴 严重 | 关闭或配白名单 |
| K8s 版本 | 集群 > 版本 | 不落后官方 **2 个大版本** | 🟠 高 | 规划升级 |
| Dashboard 暴露 | Ingress / Service | 不应对外暴露 | 🟠 高 | 限内网访问 |
| etcd 访问 | 集群配置 | 不应暴露 etcd 端口 | 🔴 严重 | 安全组限制 |

### 巡检步骤

1. 登录 https://console.cloud.tencent.com/tke2/cluster
2. 进入每个集群 > 基本信息，检查 APIServer 是否开启外网访问
3. 若已开启外网访问，检查 IP 白名单是否精确配置
4. 核查 K8s 版本，对落后 2 个大版本以上的集群制定升级计划
5. 使用 `kubectl get svc -A | grep -E 'LoadBalancer|NodePort'` 检查对外暴露的服务
6. 确认 Kubernetes Dashboard 未对外暴露（建议仅内网访问）

---

## 7.2 镜像安全

| 检查项 | 操作方法 | 判定标准 | 风险等级 | 处置 |
|--------|---------|---------|---------|------|
| 镜像来源 | Deployment image | 来自可信仓库（TCR） | 🟠 高 | 迁至 TCR 企业版 |
| 漏洞扫描 | TCR > 镜像安全 | 开启自动扫描 | 🟠 高 | 修复后重新推送 |
| 基础镜像 | Dockerfile | 用官方/最小化基础镜像 | 🟡 中 | 替换 alpine 等 |
| 镜像签名 | TCR > 内容信任 | 核心业务开启签名 | 🟡 中 | 开启内容信任 |
| 镜像拉取 | imagePullPolicy | Always（生产环境） | 🟡 中 | 修改 Pod 策略 |

### 巡检步骤

1. 检查所有运行中 Pod 的镜像来源：
   ```bash
   kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.containers[*].image}{"\n"}{end}'
   ```
2. 对非 TCR 来源镜像，评估迁移到 TCR 企业版
3. 前往 TCR 控制台，确认镜像自动扫描已开启
4. 对扫描出严重/高危漏洞的镜像，通知业务团队修复后重新推送

---

## 7.3 RBAC 与 Pod 安全

| 检查项 | 操作方法 | 判定标准 | 风险等级 | 处置 |
|--------|---------|---------|---------|------|
| RBAC 权限 | 授权管理 | 最小权限，禁 cluster-admin 滥用 | 🟠 高 | 收敛为 ns 级别 |
| 特权容器 | securityContext | 限 `privileged: true` | 🟠 高 | 移除非必要特权 |
| Root 运行 | runAsUser | 建议非 root 运行 | 🟠 高 | `runAsNonRoot: true` |
| hostNetwork | Pod spec | 限 `hostNetwork: true` | 🟠 高 | 改 ClusterIP / NodePort |
| hostPID/IPC | Pod spec | 应禁止 | 🟠 高 | 设为 false |
| 审计日志 | 日志管理 | 开启集群审计到 CLS | 🟡 中 | 开启审计 |
| ServiceAccount | Pod spec | 禁用自动挂载 SA Token | 🟡 中 | `automountServiceAccountToken: false` |

### 巡检步骤

1. 检查特权容器：
   ```bash
   kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .spec.containers[*]}{.securityContext.privileged}{"\t"}{end}{"\n"}{end}'
   ```
2. 检查以 root 运行的容器（runAsUser: 0 或未设置 runAsNonRoot）
3. 检查使用 hostNetwork/hostPID/hostIPC 的 Pod
4. 核查 ClusterRoleBinding，确认 cluster-admin 绑定对象最小化

---

## 7.4 网络策略

| 检查项 | 操作方法 | 判定标准 | 处置 |
|--------|---------|---------|------|
| Service 暴露 | `kubectl get svc` | 检查 LB / NodePort 类型 | 非必要改 ClusterIP |
| Ingress | `kubectl get ingress` | 检查不必要对外暴露 | 清理不必要规则 |
| DNS 策略 | CoreDNS | 限制外部域名解析 | 按需配置 |

### 巡检步骤

1. 列出所有 LoadBalancer 和 NodePort 类型 Service，评估是否必要
2. 检查所有 Ingress 规则，对无业务的规则清理
3. 核查集群审计日志是否已投递至 CLS
