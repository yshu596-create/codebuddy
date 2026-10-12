# CodeBuddy CLI Python 封装器

## 📖 概述

这是一个功能完整的 Python 封装库，允许你通过 Python 脚本触发 **CodeBuddy CLI** 执行指令。最强大的特性是支持**专家团（Agent Team）**的动态定义与调用。

### ✨ 核心能力

- ✅ **单次命令模式**: 通过 `-p` 参数执行查询
- ✅ **专家团/Agent 调用**: 使用 `--agent` 和 `--agents` 参数
- ✅ **结构化输出**: 支持 JSON 格式返回
- ✅ **管道输入支持**: 可传递文件内容、日志等
- ✅ **后台任务管理**: 支持长时间运行的异步任务
- ✅ **批量处理**: 一次性处理多个查询
- ✅ **完善的错误处理和日志**

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 安装 Node.js (需要 18.0+)
# 下载地址: https://nodejs.org/

# 安装 CodeBuddy CLI
npm install -g @tencent-ai/codebuddy-code

# 验证安装
codebuddy --version

# 首次登录 (微信/Google/GitHub)
codebuddy auth login

# 初始化项目上下文 (强烈推荐)
cd your-project
codebuddy /init
```

### 2. 基础使用

```python
from codebuddy_cli_wrapper import CodeBuddyCLI, quick_ask

# 方式一: 一行代码完成查询 (最简单)
result = quick_ask("解释这个项目的主要功能")
print(result.output)

# 方式二: 使用完整 API
cli = CodeBuddyCLI()
result = cli.ask("分析代码质量")
if result.success:
    print(result.output)
```

---

## 🎯 专家团（Agent）功能详解 ⭐核心功能

这是最强大的功能！你可以定义一组**专业领域的 AI 专家**，CodeBuddy 会根据任务自动调度最合适的专家来处理。

### 使用场景

- 🔍 **代码审查团队**：质量 + 性能 + 安全 三维审查
- 👨‍💻 **开发团队**：编码 + 测试 + 文档 协同工作
- 🛠️ **DevOps 团队**：CI/CD + 监控 + 架构 运维保障
- 🏗️ **全栈团队**：以上所有角色的综合

### 示例 1: 使用预定义专家团

```python
from codebuddy_cli_wrapper import (
    CodeBuddyCLI, 
    ExpertTeams,
    CodeBuddyConfig,
    PermissionMode
)

# 创建配置
config = CodeBuddyConfig(
    work_dir="./my-project",
    permission_mode=PermissionMode.AUTO,  # 自动接受文件操作
)

# 初始化 CLI
cli = CodeBuddyCLI(config)

# 加载代码审查专家团 (包含3个专家: 质量/性能/安全)
cli.set_agents(ExpertTeams.code_review_team())

# 现在发送查询会自动调用合适的专家！
result = cli.ask("全面审查 src/main.py 的代码质量")
print(result.output)

# 每个专家会从自己的角度进行分析:
# - quality-reviewer: 检查可读性、可维护性
# - performance-reviewer: 分析性能瓶颈
# - security-reviewer: 检测安全漏洞
```

### 示例 2: 自定义专家团

```python
from codebuddy_cli_wrapper import CodeBuddyCLI, AgentConfig, CodeBuddyConfig

# 定义你的专家团
my_experts = {
    "frontend-expert": AgentConfig(
        name="frontend-expert",
        description="前端开发专家",  # 必填: 描述何时使用该专家
        prompt="你是一名React/Vue前端专家...",  # 必填: 系统提示词
        tools=["Read", "Edit", "Write", "Grep"],  # 可选: 可用工具
        model="sonnet"  # 可选: 使用的模型
    ),
    
    "backend-expert": AgentConfig(
        name="backend-expert",
        description="后端API开发专家",
        prompt="你是一名Python/Django后端专家...",
        tools=["Read", "Edit", "Write", "Bash", "Grep"],
        model="opus"
    ),
    
    "database-expert": AgentConfig(
        name="database-expert",
        description="数据库设计和优化专家",
        prompt="你是一名数据库架构师...",
        tools=["Read", "Grep"]
    )
}

# 设置专家团到 CLI
config = CodeBuddyConfig(work_dir="./project")
cli = CodeBuddyCLI(config)
cli.set_agents(my_experts)

# 查询会自动路由到最相关的专家
result1 = cli.ask("优化数据库查询性能")  # → database-expert 处理
result2 = cli.ask("重构React组件")       # → frontend-expert 处理  
result3 = cli.ask("设计RESTful API")     # → backend-expert 处理
```

### 示例 3: 单个 Agent 调用

如果你只想使用一个特定的预定义 Agent:

```python
# 使用 --agent 参数指定单个 agent
config = CodeBuddyConfig(agent_name="my-reviewer")  # 预定义的 agent 名称
cli = CodeBuddyCLI(config)
result = cli.ask("审查这段代码的安全性")
```

---

## 📋 可用的预定义专家团模板

| 专家团名称 | 包含的专家 | 适用场景 |
|-----------|----------|---------|
| `code-review` | quality-reviewer, performance-reviewer, security-reviewer | 代码质量审查 |
| `development` | feature-developer, test-engineer, document-writer | 功能开发 |
| `devops` | ci-cd-engineer, monitoring-expert, infrastructure-architect | 运维部署 |
| `full-stack` | 以上所有 + tech-lead | 大型完整项目 |

```python
# 快速使用预定义团队
from codebuddy_cli_wrapper import ExpertTeams

# 加载不同的团队
cli.set_agents(ExpertTeams.code_review_team())   # 审查
cli.set_agents(ExpertTeams.development_team())   # 开发
cli.set_agents(ExpertTeams.devops_team())        # 运维
cli.set_agents(ExpertTeams.full_stack_team())    # 全栈 (推荐)
```

---

## 💡 高级用法

### JSON 结构化输出

适合程序化处理结果：

```python
# 获取结构化数据
result = cli.ask_json("统计项目的代码行数")

if result.get('success'):
    print(f"总行数: {result.get('total_lines')}")
    print(f"文件数: {result.get('file_count')}")
    
    for lang in result.get('languages', []):
        print(f"  {lang['name']}: {lang['lines']} 行")
```

### 管道输入

传递日志、文件内容等给 CodeBuddy 分析：

```python
# 分析错误日志
with open('error.log', 'r') as f:
    log_content = f.read()

result = cli.ask(
    "分析这些错误日志，找出主要问题和解决方案",
    input_data=log_content  # 通过管道传入
)

# 分析代码片段
code_snippet = '''
def calculate_total(prices):
    total = 0
    for price in prices:
        total += price * 1.1  # 加10%税费
    return total
'''
result = cli.ask("优化这个函数的性能和可读性", input_data=code_snippet)
```

### 批量处理

自动化工作流中连续执行多个任务：

```python
from codebuddy_cli_wrapper import batch_process

queries = [
    "分析项目的整体架构",
    "为 utils.py 生成单元测试",
    "更新 README.md 文档",
    "检查安全漏洞",
    "优化 database.py 的SQL查询"
]

results = batch_process(
    queries, 
    delay=2.0  # 每个任务间隔2秒，避免API限流
)

for i, result in enumerate(results, 1):
    status = "✅" if result.success else "❌"
    print(f"{status} 任务 {i}: {result.query[:40]}... ({result.elapsed_time:.1f}s)")
```

### 权限控制

在自动化脚本中使用：

```python
from codebuddy_cli_wrapper import CodeBuddyConfig, PermissionMode

# 方式1: 跳过所有权限 (慎用!)
config = CodeBuddyConfig(skip_permissions=True)  # 对应 -y 参数

# 方式2: 自动接受编辑操作 (推荐用于自动化)
config = CodeBuddyConfig(permission_mode=PermissionMode.AUTO)

# 方式3: 只允许计划模式
config = CodeBuddyConfig(permission_mode=PermissionMode.PLAN)

cli = CodeBuddyCLI(config)
result = cli.ask("自动修复所有lint警告")  # 不需要手动确认
```

---

## 🛠️ 命令行使用

除了作为 Python 库导入，还支持直接命令行调用：

```bash
# 基本用法
python codebuddy_cli_wrapper.py "解释这个函数"

# 使用专家团
python codebuddy_cli_wrapper.py "审查代码质量" --expert-team code-review

# JSON 输出
python codebuddy_cli_wrapper.py "统计代码行数" --json --verbose

# 跳过权限 (CI/CD 场景)
python codebuddy_cli_wrapper.py "运行测试套件" -y --timeout 600

# 批量处理
echo -e "任务1\n任务2\n任务3" > tasks.txt
python codebuddy_cli_wrapper.py --batch-file tasks.txt

# 运行内置演示
python codebuddy_cli_wrapper.py --demo
```

---

## 🎨 实际应用案例

### 案例 1: CI/CD 自动化流水线

```python
#!/usr/bin/env python3
"""CI/CD 流水线: 自动代码审查"""

import sys
from codebuddy_cli_wrapper import (
    CodeBuddyCLI, 
    CodeBuddyConfig,
    PermissionMode,
    ExpertTeams
)

def run_ci_checks():
    """运行CI检查"""
    config = CodeBuddyConfig(
        work_dir=".",
        permission_mode=PermissionMode.AUTO,
        output_format=OutputFormat.JSON
    )
    
    cli = CodeBuddyCLI(config)
    cli.set_agents(ExpertTeams.code_review_team())
    
    checks = [
        "运行单元测试并报告覆盖率",
        "检查代码中的潜在安全问题",
        "验证代码是否符合PEP8规范"
    ]
    
    all_passed = True
    for check in checks:
        print(f"\n▶️  执行: {check}")
        result = cli.ask(check)
        
        if result.success:
            print(f"✅ 通过")
            if result.json():
                print(f"   详情: {result.json().get('summary')}")
        else:
            print(f"❌ 失败: {result.error}")
            all_passed = False
    
    return all_passed

if __name__ == "__main__":
    success = run_ci_checks()
    sys.exit(0 if success else 1)
```

### 案例 2: 自动文档生成

```python
#!/usr/bin/env python3
"""自动生成项目文档"""

import os
from codebuddy_cli_wrapper import CodeBuddyCLI, batch_process

def generate_docs():
    """为整个项目生成文档"""
    cli = CodeBuddyCLI()
    
    # 收集所有需要生成文档的模块
    modules = []
    for root, dirs, files in os.walk("./src"):
        for file in files:
            if file.endswith(".py"):
                module_path = os.path.join(root, file)
                modules.append(f"为 {module_path} 生成详细的API文档")
    
    # 批量生成
    results = batch_process(modules)
    
    success_count = sum(1 for r in results if r.success)
    print(f"\n📝 文档生成完成: {success_count}/{len(results)} 个模块")

generate_docs()
```

### 案例 3: 智能代码重构助手

```python
#!/usr/bin/env python3
"""交互式重构助手"""

from codebuddy_cli_wrapper import (
    CodeBuddyCLI, 
    CodeBuddyConfig,
    ExpertTeams,
    PermissionMode
)

def interactive_refactor():
    """交互式重构流程"""
    
    config = CodeBuddyConfig(
        work_dir=".",
        permission_mode=PermissionMode.ACCEPT_EDITS  # 允许编辑
    )
    
    cli = CodeBuddyCLI(config)
    
    # 第1步: 分析现状
    print("🔍 步骤 1: 分析当前代码...")
    analysis = cli.ask("""
    分析这个项目的技术债务：
    1. 识别重复代码
    2. 找出复杂度过高的函数
    3. 检测过时的依赖
    4. 提供重构建议
    """)
    print(analysis.output)
    
    # 第2步: 使用专家团制定方案
    print("\n📋 步骤 2: 制定重构方案...")
    cli.set_agents(ExpertTeams.full_stack_team())
    
    plan = cli.ask("""
    基于上面的分析，制定一个分阶段的重构计划：
    - 优先级排序
    - 风险评估
    - 所需时间估算
    """)
    print(plan.output)
    
    # 第3步: 执行重构 (用户确认后)
    confirm = input("\n⚠️  是否执行重构? (yes/no): ")
    if confirm.lower() == 'yes':
        refactor = cli.ask("按照计划执行第一阶段的重构")
        print(refactor.output)
    else:
        print("❌ 已取消重构操作")

interactive_refactor()
```

---

## ⚙️ 配置参数参考

### CodeBuddyConfig 配置项

| 参数名 | 类型 | 默认值 | 说明 |
|-------|------|--------|------|
| `work_dir` | str | "." | 工作目录路径 |
| `model` | str | None | 使用的模型 (如 gpt-5, sonnet) |
| `permission_mode` | Enum | DEFAULT | 权限控制模式 |
| `output_format` | Enum | TEXT | 输出格式 (text/json/stream-json) |
| `max_turns` | int | None | 最大对话轮次 |
| `skip_permissions` | bool | False | 跳过权限确认 (-y) |
| `agent_name` | str | None | 单个预定义Agent名称 |
| `agents` | Dict | None | 自定义专家团字典 |
| `session_id` | str | None | 恢复指定会话 |
| `continue_last` | bool | False | 继续上一次对话 |

### AgentConfig 配置项

| 字段名 | 是否必填 | 说明 |
|-------|---------|------|
| `name` | ✅ 是 | Agent标识名称 |
| `description` | ✅ 是 | 自然语言描述，决定何时调用该Agent |
| `prompt` | ✅ 是 | 系统提示词，指导Agent行为 |
| `tools` | ❌ 否 | 工具白名单 (Read/Edit/Bash等) |
| `disallowed_tools` | ❌ 否 | 工具黑名单 |
| `model` | ❌ 否 | 使用的模型别名 |

---

## 🐛 故障排除

### 问题 1: 未找到 codebuddy 命令

**错误信息**: `FileNotFoundError: 未找到 codebuddy 命令`

**解决方案**:
```bash
# 安装 CodeBuddy CLI
npm install -g @tencent-ai/codebuddy-code

# 验证安装
which codebuddy
codebuddy --version
```

### 问题 2: 认证失败

**错误信息**: Authentication required

**解决方案**:
```bash
# 重新登录
codebuddy auth login

# 或清除缓存重新登录
rm -rf ~/.codebuddy/
codebuddy auth login
```

### 问题 3: 权限拒绝

**错误信息**: Operation requires permissions...

**解决方案**:
```python
# 在脚本中添加权限参数
config = CodeBuddyConfig(skip_permissions=True)  # 或使用 AUTO 模式
```

### 问题 4: 执行超时

**解决方案**:
```python
# 增加超时时间
result = cli.ask("复杂任务", timeout=600)  # 10分钟
```

---

## 📚 更多资源

- **官方文档**: https://www.codebuddy.ai/docs/cli/cli-reference
- **快速入门**: https://www.codebuddy.cn/docs/zh/cli/quickstart
- **安装指南**: https://www.w3cschool.cn/codebuddydocs/codebuddy-cli-quickstart.html
- **MCP集成**: https://www.codebuddy.ai/docs/cli/mcp

---

## 🤝 贡献与反馈

欢迎提交 Issue 和 Pull Request！

---

## 📄 许可证

MIT License

---

## 📮 联系方式

如有问题或建议，欢迎通过以下方式联系：
- GitHub Issues
- 官方文档反馈

---

**最后更新**: 2026-06-29
