# CodeBuddy CLI + CloudOps 专家团 环境搭建指南

> **适用系统**: macOS / Windows 10&11  
> **最后更新**: 2026-06-29

---

## 目录

- [一、macOS 环境搭建](#一macos-环境搭建)
- [二、Windows 环境搭建](#二windows-环境搭建)
- [三、验证安装](#三验证安装)
- [四、CodeBuddy 账号登录](#四codebuddy-账号登录)
- [五、从 Marketplace 安装 CloudOps 插件](#五从-marketplace-安装-cloudops-插件)
- [六、专家团凭证配置 SSM+KMS](#六专家团凭证配置-ssmkms)
- [七、华为云认证（可选）](#七华为云认证可选)
- [六、常见问题排查](#六常见问题排查)

---

## 一、macOS 环境搭建

### 方案 A: 使用 nvm（推荐，可管理多版本）

```bash
# ========================================
# 步骤 1: 安装 nvm (Node.js 版本管理器)
# ========================================
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash

# 重新加载 shell 配置
source ~/.zshrc

# 验证 nvm 安装
nvm --version
# 输出: 0.40.x

# ========================================
# 步骤 2: 安装 Node.js
# ========================================
nvm install 20          # 安装 LTS 版本（推荐）
# 或安装最新版:
nvm install 24          # 当前最新版 v24.18.0

# 设为默认版本
nvm alias default 20

# 验证 Node.js 和 npm
node --version   # v20.x.x 或 v24.x.x
npm --version    # 10.x.x 或 11.x.x

# ========================================
# 步骤 3: 安装 CodeBuddy CLI
# ========================================
npm install -g @tencent-ai/codebuddy-code

# 验证安装
codebuddy --version
# 输出类似: 2.113.0

# ========================================
# 步骤 4: 刷新 PATH (如果提示 command not found)
# ========================================
source ~/.zshrc
# 或关闭终端重新打开
```

### 方案 B: 官方安装包（适合不想用命令行的用户）

```bash
# 1. 访问 https://nodejs.org/
# 2. 下载 macOS Installer (.pkg)
# 3. 双击安装，按向导完成
# 4. 打开 Terminal，继续步骤 3 安装 CodeBuddy CLI
```

### macOS 常见坑

| 问题 | 解决方法 |
|------|---------|
| `command not found: codebuddy` | 运行 `source ~/.zshrc` 或新开终端 |
| npm 权限错误 | 加 `sudo`: `sudo npm install -g ...` |
| nvm 安装后找不到 | 检查 `~/.zshrc` 是否有 nvm 配置行 |
| Apple Silicon (M1/M2/M3) | nvm 自动处理架构，无需额外操作 |

---

## 二、Windows 环境搭建

### 方案 A: 使用 nvm-windows（推荐）

```powershell
# ========================================
# 以管理员身份运行 PowerShell
# ========================================

# ========================================
# 步骤 1: 安装 nvm-windows
# ========================================
# 方法 1: 下载安装包 (推荐)
# 访问: https://github.com/coreybutler/nvm-windows/releases
# 下载: nvm-setup.exe
# 双击运行，按默认设置安装 (路径建议 C:\nvm)
#
# 方法 2: Winget 一键安装
winget install coreybutler.nvm-windows

# 关闭并重新打开 PowerShell (管理员模式)

# ========================================
# 步骤 2: 安装 Node.js
# ========================================
nvm install 20           # LTS 版本
nvm use 20               # 使用该版本

# 验证
node --version    # v20.x.x
npm --version     # 10.x.x

# ========================================
# 步骤 3: 安装 CodeBuddy CLI
# ========================================
npm install -g @tencent-ai/codebuddy-code

# 验证
codebuddy --version      # 2.113.0

# ========================================
# 步骤 4: 如果提示找不到命令
# ========================================
# 关闭 PowerShell，重新以管理员身份打开
```

### 方案 B: 官方 MSI 安装包

```powershell
# 1. 访问 https://nodejs.org/
# 2. 下载 Windows Binary (.msi) - 选择 LTS 版本
# 3. 双击安装，勾选 "Add to PATH"
# 4. 重新打开 CMD/PowerShell
# 5. 继续 CodeBuddy CLI 安装步骤
```

### Windows 常见坑

| 问题 | 解决方法 |
|------|---------|
| `'npm' 不是内部或外部命令` | 重启 CMD/PowerShell；检查 PATH 是否包含 npm 路径 |
| `EACCES: permission denied` | 必须用**管理员**权限运行 PowerShell/CMD |
| npm 安装很慢 | 设置淘宝镜像: `npm config set registry https://registry.npmmirror.com` |
| Python 脚本报编码错误 | 在脚本开头加 `# -*- coding: utf-8-*` 并用 UTF-8 保存 |

### Windows 终端选择

| 终端 | 推荐度 | 说明 |
|------|:------:|------|
| **Windows Terminal** | ⭐⭐⭐⭐⭐ | 微软官方，支持多标签，推荐！ |
| PowerShell 7+ | ⭐⭐⭐⭐ | 功能强大，兼容性好 |
| CMD | ⭐⭐ | 基础够用，但体验一般 |
| Git Bash | ⭐⭐⭐ | 类 Unix 体验，部分 Windows 命令不兼容 |

> 💡 **强烈推荐安装 [Windows Terminal](https://aka.ms/terminal)** (Microsoft Store 可直接装)

---

## 三、验证安装

无论哪个系统，完成后都执行以下验证：

```bash
# ========================================
# 完整验证流程
# ========================================

echo "=== 系统信息 ==="
uname -a                    # macOS/Linux
# 或者: systeminfo           # Windows

echo ""
echo "=== Node.js ==="
node --version              # 需要 >= 18.0.0

echo ""
echo "=== npm ==="
npm --version               # 需要 >= 9.0.0

echo ""
echo "=== CodeBuddy CLI ==="
codebuddy --version         # 需要 >= 2.100.0

echo ""
echo "=== 全局模块位置 ==="
npm list -g @tencent-ai/codebuddy-code

echo ""
echo "=== codebuddy 位置 ==="
which codebuddy             # macOS/Linux
where codebuddy             # Windows

echo ""
echo "✅ 所有检查通过!"
```

**预期输出示例**：
```
=== Node.js ===
v24.18.0

=== npm ===
11.16.0

=== CodeBuddy CLI ===
2.113.0

✅ 所有检查通过!
```

---

## 四、CodeBuddy 账号登录

```bash
# 启动交互式登录向导
codebuddy auth login
```

会弹出以下选项：

| 登录方式 | 适用场景 | 特点 |
|---------|---------|------|
| **微信扫码** | 国内用户最方便 | 国内网络直连，模型选择较少 |
| **Google 登录** | 国际用户/需要全模型 | 支持最多模型 (GPT-4/Claude 等) |
| **GitHub 登录** | 开发者首选 | 与 GitHub 生态集成 |
| **腾讯云 API Key** | 需要调用云服务资源 | 单独配置，非账号登录 |

> ⚠️ CodeBuddy 账号（用于 CLI/交互模式）和腾讯云 API 凭证（用于调用云资源）是**两个独立认证体系**。

---

## 五、从 Marketplace 安装 CloudOps 专家团插件

> 这是获得 8 位云运维 AI 专家 + 14 项专业技能的关键步骤。
> **完整流程 = 注册本地市场源 → 安装插件（两步缺一不可）**

### CodeBuddy Plugin 命令速查表

> CodeBuddy 的插件管理有 **两种使用方式**，不要混用：

| 使用场景 | 命令格式 | 示例 |
|---------|---------|------|
| **交互模式内** (`>` 提示符) | 以 `/` 开头 | `> /plugin marketplace add /path` |
| **终端直接执行** | `codebuddy plugin ...` | `codebuddy plugin marketplace add /path` |

**核心命令一览**：

| 操作 | 交互模式命令 | 终端命令 | 说明 |
|------|------------|---------|------|
| 打开插件管理器 | `/plugin` | — | GUI 四标签页界面 |
| 添加市场源 | `/plugin market add <路径>` | `codebuddy plugin marketplace add <路径>` | 支持本地目录/GitHub/URL |
| 列出已添加市场 | `/plugin market list` | `codebuddy plugin marketplace list` | 查看市场状态 |
| 安装插件 | `/plugin install <名>@<市场>` | `codebuddy plugin install <名>@<市场>` | 从指定市场安装 |
| 已装插件列表 | `/plugin` → Installed 标签 | `codebuddy plugin list` | 查看启用状态 |
| 更新市场索引 | `/plugin market update <名>` | `codebuddy plugin marketplace update <名>` | 刷新插件列表 |

> 💡 **快捷写法**: `/plugin market` = `/plugin marketplace`，`rm` = `remove`

---

### 步骤 1：注册本地插件市场源（必须先做！）

这一步告诉 CodeBuddy："去 `my-experts` 目录里找插件"。**不执行的话后续搜不到任何东西。**

#### 方式 A：交互模式内操作（推荐）

```bash
# ① 进入 CodeBuddy 交互模式
codebuddy

# ② 在 > 提示符下执行:
> /plugin marketplace add /Users/lenon/.workbuddy/plugins/marketplaces/my-experts

# Windows 用户请改为绝对路径:
> /plugin marketplace add C:\Users\你的用户名\.workbuddy\plugins\marketplaces\my-experts
```

#### 方式 B：GUI 界面操作

```
1. 输入 /plugin 回车，打开插件管理器
2. 切换到 Marketplaces 标签页（Tab 或 Shift+Tab）
3. 选择 "Add marketplace"
4. 输入路径后回车，等待自动识别类型并加载插件清单
```

#### 方式 C：终端一行搞定（不用进交互模式）

```bash
codebuddy plugin marketplace add /Users/lenon/.workbuddy/plugins/marketplaces/my-experts
```

**预期输出**：
```
✅ Marketplace 已添加: my-experts (file:///.../my-experts)
```

#### 支持的市场源格式

| 格式 | 示例 | 适用场景 |
|------|------|---------|
| 本地目录 | `/path/to/marketplace` | 开发调试 / 私有分发 |
| GitHub 仓库 | `owner/repo` | 公开或私有 GitHub 市场 |
| Git URL | `https://github.com/owner/repo.git` | GitLab/Bitbucket 等 |
| HTTP URL | `https://example.com/marketplace.json` | 远程 JSON 清单 |

---

### 步骤 2：从市场安装 cloudops-team-pro 插件

#### 方式 A：交互式界面安装（推荐新手）

```bash
# ① 进入 CodeBuddy 交互模式
codebuddy

# ② 在 > 提示符下执行:
> /plugin install cloudops-team-pro@my-experts
```

或者通过 GUI：
```
1. 输入 /plugin 回车
2. 在 Discover 标签页搜索: cloudops-team-pro
3. 找到作者 fangxing，确认详情:
   ├── Agents:   8  (专家)
   ├── Skills:   9  (技能)
   └── Commands: 1  (命令)
4. 选择作用域: User（推荐）→ 按 Enter 安装
5. 看到安装成功提示 → 回到 > 提示符
```

#### 方式 B：终端一行搞定

```bash
# 注意：必须指定 @市场名，否则找不到插件！
codebuddy plugin install cloudops-team-pro@my-experts
```

> ⚠️ 如果不写 `@my-experts`，CodeBuddy 只会在官方市场搜索，找不到你的私有插件。

### 5.3 验证安装成功

```bash
# 交互模式内:
> /plugin

# 或终端直接运行:
codebuddy plugin list
```

**预期输出**：
```
已启用插件 (7):
  ✓ cloudops-team-pro    v2.0.0    by fangxing
    ├─ Agents:   8
    ├─ Skills:   9
    └─ Commands: 1

总览:
  Agents:   8 已启用
  Skills:   9 已注册
  Commands: 1 可用
```

### 5.4 插件包含的 8 大专家

| # | 专家花名 | 职业 | 英文 Agent ID | 核心能力 |
|---|---------|------|--------------|---------|
| 1 | **云掌柜** | 云运维经理 | `cloudops-team-pro-team-lead` | 意图识别、智能路由到对应专家、多账号并行编排、汇总回复 |
| 2 | **老账** | 云账单分析专家 | `cloud-billing-analyst-pro` | **腾讯云**费用构成/环比/资源级TOP消费/异常定位 + HTML看板 |
| 3 | **华账** | 华为云账单分析专家 | `huawei-billing-analyst-pro` | **华为云**BSS API 费用拆解/企业项目分账/地域分布 + HTML看板 |
| 4 | **云审计官** | 云审计分析师 | `cloud-auditor-pro` | CloudAudit 高危事件(9大类43事件)/服务账号降噪/应急取证 |
| 5 | **云巡检** | 云安全巡检工程师 | `cloud-inspector-pro` | 12域安全配置扫描/SOP TC-SEC-SOP-001/4级风险定级/HTML报告 |
| 6 | **云降本医生** | 云降本优化专家 | `cloud-cost-optimizer-pro` | 账单驱动+30天监控/闲置识别/4档风险降本建议/节省预估 |
| 7 | **云管家** | 云资源管理专家 | `cloud-resource-manager-pro` | 全账号跨地域资源清点(CVM/GPU/COS/TKE等20+类)/到期续费点查 |
| 8 | **云图** | 云架构分析师 | `cloud-architect-pro` | 分层架构拓扑图(接入→计算→数据→存储→网络)+健康度评分(A~F) |

> 使用方式：你只需用自然语言说需求（如"看一下上月账单""全面巡检"），**云掌柜**自动识别意图并调度对应专家，无需手动指定专家。

### 5.5 9 项专业技能（Skills）

```
✅ cloud-billing-pro         — 腾讯云账单：DescribeBillSummary + 环比对比 + 标签分账
✅ cloud-billing-huawei-pro  — 华为云账单：ShowCustomerMonthlySum + 企业项目分账
✅ cloud-inspector-pro       — 安全巡检：12域配置项扫描 + SOP 四档风险定级
✅ cloud-auditor-pro         — 审计取证：9大类43种高危事件 + 服务账号降噪
✅ cloud-costopt-pro         — 成本优化：账单驱动 + 30天监控 + 风险分级降本建议
✅ cloud-resource-pro       — 资源清点：20+类资源并发采集 + 清点HTML看板
✅ cloud-report-pptx-pro     — PPT导出：VCIC模板(深空蓝+翠绿) 16:9幻灯片
✅ cloudops-accounts-pro     — 多账号管理：SSM凭证解析 + 进程内隔离 + list_accounts
✅ cache                     — 缓存层：按UIN+月份做cache key的本地缓存
```

### 5.6 支持的云平台

| 云厂商 | SSM 凭据路径前缀 | 说明 |
|--------|-----------------|------|
| **腾讯云** | `cloudops/tencent/{项目码}` | 主力支持，全部专家可用 |
| **华为云** | `cloudops/huawei/{项目码}` | 仅账单专家(华账)，凭证也走 SSM |
| **阿里云** | `cloudops/aliyun/{项目码}` | 基础支持（规划中） |

> **关键点**：所有云厂商的业务 AK/SK **统一托管在腾讯云 SSM** 中（KMS 加密），宿主端只需配一把 runner key 即可访问全部。**华为云不需要单独设环境变量。**

---

## 六、CloudOps 专家团凭证配置（SSM + KMS 信任模型）

> **时机**: 插件安装完成后，首次召唤云运维专家时系统会引导配置。也可提前手动完成。
>
> ⚠️ `cloudops-team-pro` **不使用**传统的 `tccli configure` 或 `TENCENTCLOUD_SECRET_ID`。它采用 **SSM + KMS 分层信任模型**。

### 6.1 模型架构

```
┌──────────────────────────────────────────────────────┐
│                  你的本地机器                          │
│                                                      │
│ ~/.workbuddy/secrets/cloudops.env                    │
│  ┌──────────────────────────────────┐               │
│  │ TENCENTCLOUD_BOOTSTRAP_SECRET_ID │ ← 唯一凭证     │
│  │ TENCENTCLOUD_BOOTSTRAP_SECRET_KEY│   (runner key) │
│  │ TENCENTCLOUD_BOOTSTRAP_REGION    │               │
│  └───────────────┬──────────────────┘               │
│                  │                                   │
│                  ▼                                   │
│         专家团脚本进程                                │
│     调用 resolve(cloud, project)                     │
│                  │                                   │
└──────────────────┼───────────────────────────────────┘
                   │  ssm:GetSecretValue
                   ▼
┌──────────────────────────────────────────────────────┐
│                腾讯云 SSM 服务                        │
│                                                      │
│  cloudops/{cloud}/{project}                          │
│  ├── cloudops/tencent/YX    ← 腾讯云账号              │
│  ├── cloudops/huawei/TSP    ← 华为云账号（也走SSM！）  │
│  ├── cloudops/aliyun/DEF    ← 阿里云账号（规划中）     │
│  └── ...                                              │
│                                                      │
│  每条凭据内容: KMS 加密存储的 JSON                      │
│  ┌──────────────────────────────────┐               │
│  │ 业务 AK/SK（KMS 加密存储）        │ ← 不落盘！     │
│  └──────────────────────────────────┘               │
│          │                                          │
│          ▼  服务端 KMS 解密                          │
│     返回明文 AK/SK → 进程内存 → 即用即弃              │
└──────────────────────────────────────────────────────┘
```

**一句话记忆：一个 runner key 进宿主，全部业务 key（含华为云）进 SSM**

### 6.2 步骤 1：获取 Runner Key

在腾讯云 CAM 控制台操作：
1. 创建子账号（或复用现有账号）
2. 授予权限策略（只需 SSM 读取权限）：

```json
{
  "version": "2.0",
  "statement": [{
    "effect": "allow",
    "action": [
      "ssm:GetSecretValue",
      "ssm:ListSecrets",
      "ssm:ListSecretVersionIds"
    ],
    "resource": "*"
  }]
}
```

3. 生成该子账号的 `SecretId` + `SecretKey`

> ⚠️ **三大铁律**：
> - 不要给 runner key 加 `kms:Decrypt` 权限——SSM 是服务端解密，runner key 不需要 KMS 权限
> - runner key **不能直接签发任何业务 API**——它只能读 SSM
> - `BOOTSTRAP` 是保留项目代号，**永不能**作为业务账号被选中

### 6.3 步骤 2：创建标准配置文件

**macOS/Linux**:
```bash
mkdir -p ~/.workbuddy/secrets

cat > ~/.workbuddy/secrets/cloudops.env << 'EOF'
# CloudOps 专家团 - 宿主环境唯一凭证（只放 runner key）
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="AKID<你的runner-key-secret-id>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<你的runner-key-secret-key>"
export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"
EOF

chmod 600 ~/.workbuddy/secrets/cloudops.env
```

**Windows (PowerShell)**:
```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.workbuddy\secrets"

@"
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="AKID<你的runner-key>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<your-secret-key>"
export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"
"@ | Out-File "$env:USERPROFILE\.workbuddy\secrets\cloudops.env" -Encoding utf8
```

### 6.4 步骤 3：让配置生效

```bash
# 立即生效（当前终端）
source ~/.workbuddy/secrets/cloudops.env

# 持久化（每次开终端自动加载）
echo 'source ~/.workbuddy/secrets/cloudops.env' >> ~/.zshrc   # zsh
```

### 6.5 步骤 4：验证配置

```bash
echo "BOOTSTRAP_ID: ${TENCENTCLOUD_BOOTSTRAP_SECRET_ID:0:10}..."
echo "REGION: $TENCENTCLOUD_BOOTSTRAP_REGION"
```

### 6.6 三大禁忌

| ❌ 绝对不要做 | 后果 |
|-------------|------|
| 把业务 AK/SK 写进 `cloudops.env` | 该文件只放 runner key。业务的走 SSM 路径 `cloudops/tencent/{项目}`，由 `resolve()` 解密进**进程内存**，不落盘 |
| 给 runner key 加 `kms:Decrypt` 权限 | SSM 服务端解密，加了反而扩大攻击面 |
| 将 `cloudops.env` 提交到 Git | `.gitignore` 必须包含 `*.env`、`secrets/`、`.workbuddy/` |

### 6.7 当前状态自查

```bash
echo "BOOTSTRAP_ID: ${TENCENTCLOUD_BOOTSTRAP_SECRET_ID:-❌ 未设置}"
echo "BOOTSTRAP_KEY: ${TENCENTCLOUD_BOOTSTRAP_SECRET_KEY:+✅ 已设置}"
echo "BOOTSTRAP_KEY: ${TENCENTCLOUD_BOOTSTRAP_SECRET_KEY:-❌ 未设置}"
ls -la ~/.workbuddy/secrets/cloudops.env 2>/dev/null || echo "⚠️ 配置文件不存在"
```

| 结果 | 含义 | 下一步 |
|------|------|--------|
| 变量有值 + 文件存在 | 正常 | 直接使用 |
| 变量有值 + 文件不存在 | 通过其他途径临时注入 | 建议 6.3 固化 |
| 变量无值 | 未配置 | 完成 6.2~6.4 |

---

## 七、在 SSM 中添加业务账号凭据

> 配好 runner key（第六章）后，还需要把**每个要管理的云账号 AK/SK 存入腾讯云 SSM**。  
> **包括华为云和阿里云的凭证也统一存在腾讯云 SSM 里**——SSM 是全部云的唯一凭证库。

### 7.1 操作步骤

1. 登录 [腾讯云 SSM 控制台](https://console.cloud.tencent.com/ssm)
2. 点击「新建凭据」→ 选择「通用型」→ 勾选 **KMS 加密**
3. 按以下命名规则和内容格式创建凭据：

#### 腾讯云账号

```
凭据名称:  cloudops/tencent/<项目码>
            项目码 = 2-10位大写字母数字，如 YX / TSP / GW

凭据内容(JSON，小写键):
{
  "secret_id": "AKIDxxxxxxxx",
  "secret_key": "xxxxxxxxxx",
  "uin": "100012345678",
  "region": "ap-guangzhou"
}
```

#### 华为云账号

```
凭据名称:  cloudops/huawei/<项目码>

凭据内容(Json):
{
  "ak": "AccessKeyID",
  "sk": "SecretAccessKey",
  "bss_endpoint": "bss.myhuaweicloud.com"
}
# 国际站: bss_endpoint 改为 bss-intl.myhuaweicloud.com
```

#### 阿里云账号（规划中）

```
凭据名称:  cloudops/aliyun/<项目码>

凭据内容(Json):
{
  "access_key_id": "LTAI...",
  "access_key_secret": "...",
  "region_id": "cn-hangzhou"
}
```

### 7.2 跨账号代查（可选，高级用法）

如需用主账号 AK 代查子账号资源，在目标账号的 SSM 凭据中加：

```json
{
  "secret_id": "<子账号AK>",
  "secret_key": "<子账号SK>",
  "role_arn": "qcs::cam::uin/<目标UIN>:roleName/<角色名>",
  "assume_from": "PARENT"
}
```

并单独建一份 `cloudops/tencent/PARENT` 只放主账号 AK。

> `uin`、`region`、`domain_id` 等可省略——专家运行时会通过各云 API 自动反查。

### 7.3 验证 SSM 配置

```bash
# 列出 SSM 中已有的账号（只读名称，不解密）
python3 ~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team-pro/skills/cloudops-accounts-pro/scripts/list_accounts.py --human
```

**预期输出示例**：
```
已配置的云账户:
├── tencent
│   ├── YX   (UIN: 100012345678 · ap-guangzhou)
│   └── TSP  (UIN: 100098765432 · ap-shanghai)
└── huawei
    └── YX   (domain_id: 0a1b2c3d4e5f)
```

---

## 六、常见问题排查

### 6.1 Node.js/npm 相关

```bash
# 问题: npm 安装全局包报 EACCES 错误
# macOS 解决方案:
sudo chown -R $(whoami) ~/.npm
sudo chown -R $(whoami) /usr/local/lib/node_modules

# Windows 解决方案: 必须以管理员身份运行

# 问题: npm 安装速度慢
# 使用淘宝镜像 (国内加速):
npm config set registry https://registry.npmmirror.com

# 验证镜像是否生效:
npm config get registry
```

### 6.2 CodeBuddy CLI 相关

```bash
# 问题: command not found: codebuddy (macOS/Linux)
# 解决方案:
source ~/.zshrc        # zsh 用户
# 或 source ~/.bashrc   # bash 用户
# 或新开一个终端窗口

# 问题: 'codebuddy' 不是内部或外部命令 (Windows)
# 解决方案:
# 1. 重启 CMD/PowerShell
# 2. 或手动添加到 PATH:
#    找到 npm 全局路径: npm root -g
#    将其父级 bin 目录添加到系统 PATH

# 查看 codebuddy 实际安装位置:
npm list -g --depth=0
```

### 6.3 认证相关

```bash
# 问题: Authentication required
# 解决方案:
codebuddy auth login
# 重新走一次登录流程

# 问题: 云专家提示"无法访问云资源"（SSM 凭证未配置）
# → 见本章「六、专家团凭证配置」6.7 自查:
echo "BOOTSTRAP: ${TENCENTCLOUD_BOOTSTRAP_SECRET_ID:-未设置}"
ls ~/.workbuddy/secrets/cloudops.env 2>/dev/null || echo "配置文件不存在"

# 问题: exit code 5（脚本读不到 BOOTSTRAP 环境变量）
source ~/.workbuddy/secrets/cloudops.env

# 问题: SSM AccessDenied
# runner key 的 CAM 策略需包含: ssm:GetSecretValue + ssm:ListSecrets + ssm:ListSecretVersionIds

# 问题: 华为云专家提示凭证未配置（但你觉得配了 HUAWEICLOUD_SDK_AK/SK）
# → 华为云也走 SSM！需要在腾讯云 SSM 控制台建 cloudops/huawei/<项目码> 的凭据
#    不是直接 export HUAWEICLOUD_SDK_AK/SK
```

### 6.4 插件相关

```bash
# ══════════════════════════════════════
# 问题 1: /plugin marketplace add 报错 "command not found"
# ══════════════════════════════════════
# 原因: 你在终端里直接执行了以 / 开头的命令（交互模式专用）
# 解决: 两种正确用法，任选其一:
#
#   方式 A — 先进交互模式，再输入斜杠命令:
codebuddy
# > /plugin marketplace add /path/to/my-experts    ← 在 > 提示符下输入
#
#   方式 B — 终端直接执行（不用 / 前缀）:
codebuddy plugin marketplace add /path/to/my-experts

# ══════════════════════════════════════
# 问题 2: 添加市场成功但 /install 搜不到插件
# ══════════════════════════════════════
# 原因 A: 目录下缺少 .codebuddy-plugin/marketplace.json
ls /path/to/my-experts/.codebuddy-plugin/marketplace.json
#
# 原因 B: 安装时没指定 @市场名
# 错误: codebuddy plugin install cloudops-team-pro          ← 只搜官方市场
# 正确: codebuddy plugin install cloudops-team-pro@my-experts  ← 指定你的市场
#
# 原因 C: 市场索引过期，需要刷新
> /plugin market update my-experts

# ══════════════════════════════════════
# 问题 3: 插件安装失败
# ══════════════════════════════════════
# 清除缓存重试:
npm cache clean --force
codebuddy plugin uninstall cloudops-team-pro@my-experts
codebuddy plugin install cloudops-team-pro@my-experts

# 检查网络连接
ping registry.npmjs.org

# ══════════════════════════════════════
# 问题 4: 专家没有被正确加载
# ══════════════════════════════════════
# 查看已启用插件:
codebuddy plugin list

# 重启 CodeBuddy 会话:
# 退出交互模式 (Ctrl+C 或输入 exit)
# 重新进入: codebuddy
```

### 6.5 Python 封装脚本相关

```python
# 问题: ImportError: No module named 'xxx'
# 确保标准库都已导入（脚本只用到了内置库）
# 无需 pip install 任何依赖

# 问题: 中文输出乱码 (Windows)
# 在 Python 脚本开头添加:
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 问题: subprocess 调用超时
# 增加 timeout 参数:
result = cli.ask("复杂任务", timeout=600)  # 10分钟
```

---

## 快速参考卡片

### macOS 一键安装（复制粘贴即可）

```bash
# 完整安装流程（约 5 分钟）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash && \
source ~/.zshrc && \
nvm install 20 && \
nvm alias default 20 && \
npm install -g @tencent-ai/codebuddy-code && \
codebuddy --version && \
echo "✅ macOS 安装完成! 接下来请运行: codebuddy auth login"
```

### Windows 一键安装（PowerShell 管理员模式）

```powershell
# 安装 Node.js (如果有 winget)
winget install OpenJS.NodeJS.LTS

# 安装 CodeBuddy
npm install -g @tencent-ai/codebuddy-code

# 验证
codebuddy --version

# 开始使用
codebuddy auth login
```

---

## 下一步

环境搭建完成后，请阅读 **《专家应用指南》** 学习如何高效使用 8 位云运维专家！

---

*文档版本: v1.1 | 更新时间: 2026-06-30*
