# CodeBuddy CLI 专家团调用指南 - 核心总结

## 🎯 你的问题答案

### Q: 可以用 CLI 命令执行 WorkBuddy 上的专家团吗？

**✅ 答案：完全可以！**

CodeBuddy CLI 提供了强大的 Agent（智能体/专家团）功能，可以通过以下方式使用：

---

## 📋 三种使用专家团的方式

### 方式 1: 使用预定义 Agent (简单)

```bash
# 使用 --agent 参数指定单个 agent
codebuddy --agent my-reviewer "审查这段代码"

# 在 Python 中
config = CodeBuddyConfig(agent_name="my-reviewer")
cli = CodeBuddyCLI(config)
result = cli.ask("进行代码审查")
```

### 方式 2: 动态定义专家团 (灵活) ⭐推荐

```bash
# 使用 --agents 参数动态定义多个专家 (JSON格式)
codebuddy --agents '{
  "reviewer": {
    "description": "代码审查专家",
    "prompt": "你是高级代码审查员...",
    "tools": ["Read", "Grep"]
  },
  "tester": {
    "description": "测试专家", 
    "prompt": "你负责生成测试用例..."
  }
}' "全面审查这个项目"
```

**Python 中更简洁：**

```python
from codebuddy_cli_wrapper import CodeBuddyCLI, AgentConfig

# 定义你的专家团队
experts = {
    "security-expert": AgentConfig(
        name="security-expert",
        description="安全审计专家",
        prompt="检查SQL注入、XSS、CSRF等安全问题...",
        tools=["Read", "Grep"],
        model="sonnet"
    ),
    
    "performance-expert": AgentConfig(
        name="performance-expert",
        description="性能优化专家",
        prompt="分析时间复杂度和I/O效率...",
        tools=["Read", "Bash"]
    )
}

# 设置到 CLI 并使用
cli = CodeBuddyCLI()
cli.set_agents(experts)

# 发送查询 - 自动调度最合适的专家!
result = cli.ask("审查这个API的安全性和性能")
```

### 方式 3: 使用预定义模板 (最快)

```python
from codebuddy_cli_wrapper import ExpertTeams

# 一行代码加载完整专家团!
cli.set_agents(ExpertTeams.code_review_team())   # 3个审查专家
cli.set_agents(ExpertTeams.development_team())   # 3个开发角色
cli.set_agents(ExpertTeams.devops_team())        # 3个运维专家
cli.set_agents(ExpertTeams.full_stack_team())    # 10个全栈专家 ⭐
```

---

## 🚀 Python 脚本触发 CLI 的完整流程

### 步骤 1: 环境准备

```bash
# 安装 Node.js (18.0+)
# 下载: https://nodejs.org/

# 安装 CodeBuddy CLI
npm install -g @tencent-ai/codebuddy-code

# 登录认证 (微信/Google/GitHub)
codebuddy auth login

# 初始化项目上下文 (提升AI理解能力!)
cd your-project
codebuddy /init
```

### 步骤 2: 编写 Python 脚本

```python
#!/usr/bin/env python3
"""通过Python触发CodeBuddy CLI执行指令"""

import sys
sys.path.insert(0, '/path/to/codebuddy_cli_wrapper.py')

from codebuddy_cli_wrapper import (
    CodeBuddyCLI,
    CodeBuddyConfig,
    AgentConfig,
    ExpertTeams,
    PermissionMode,
    quick_ask
)


def main():
    """主函数"""
    
    # ========== 配置 ==========
    config = CodeBuddyConfig(
        work_dir="./your-project",           # 工作目录
        permission_mode=PermissionMode.AUTO, # 自动接受编辑操作
        skip_permissions=True,               # 跳过权限确认 (-y)
        output_format=OutputFormat.TEXT,     # 输出格式
    )
    
    # 初始化 CLI
    cli = CodeBuddyCLI(config)
    
    # ========== 加载专家团 ==========
    cli.set_agents(ExpertTeams.code_review_team())
    print("✅ 已加载代码审查专家团 (3位专家)")
    
    # ========== 执行任务 ==========
    tasks = [
        "分析项目整体架构",
        "审查 src/main.py 的代码质量",
        "检测潜在的安全漏洞",
        "生成单元测试",
        "更新 README.md 文档"
    ]
    
    for i, task in enumerate(tasks, 1):
        print(f"\n▶️ [{i}/{len(tasks)}] {task}")
        
        try:
            result = cli.ask(task, timeout=120)  # 2分钟超时
            
            if result.success:
                print(f"✅ 完成! 耗时 {result.elapsed_time:.1f}秒")
                print(result.output[:500])  # 显示前500字符
                
                # 如果是JSON输出可以解析处理
                # data = result.json()
                
            else:
                print(f"❌ 失败: {result.error[:200]}")
                
        except Exception as e:
            print(f"❌ 错误: {e}")
    
    print("\n🎉 所有任务完成!")


if __name__ == "__main__":
    main()
```

### 步骤 3: 运行脚本

```bash
python your_script.py
```

---

## 🎨 实际应用场景

### 场景 1: CI/CD 自动化流水线

```python
# .github/workflows/ai-code-review.yml 或 Jenkins Pipeline
def run_ai_code_review():
    """CI/CD中自动运行AI代码审查"""
    
    config = CodeBuddyConfig(
        work_dir=".",
        permission_mode=PermissionMode.AUTO,
        output_format=OutputFormat.JSON
    )
    
    cli = CodeBuddyCLI(config)
    cli.set_agents(ExpertTeams.code_review_team())  # 加载审查专家
    
    # 获取最新变更的文件列表
    changed_files = get_git_changed_files()  # 你需要实现这个函数
    
    results = []
    for file_path in changed_files:
        result = cli.ask(f"审查 {file_path} 的代码质量、性能和安全性")
        results.append({
            'file': file_path,
            'passed': result.success,
            'feedback': result.output if result.success else result.error
        })
    
    return results
```

### 场景 2: 定时任务 - 每日健康检查

```python
import schedule
import time

def daily_health_check():
    """每日运行系统健康检查"""
    
    cli = CodeBuddyCLI(CodeBuddyConfig(skip_permissions=True))
    
    checks = [
        "检查依赖包是否有已知漏洞",
        "分析是否有技术债务积累",
        "统计测试覆盖率变化趋势",
        "识别复杂的代码区域需要重构"
    ]
    
    for check in checks:
        result = cli.ask(check)
        send_to_slack(f"🤖 {check}: {result.output[:200]}")

schedule.every().day.at("09:00").do(daily_health_check)

while True:
    schedule.run_pending()
    time.sleep(60)
```

### 场景 3: 批量重构助手

```python
def batch_refactor(pattern="*.py"):
    """批量重构匹配的文件"""
    
    import glob
    
    files = glob.glob(pattern, recursive=True)
    cli = CodeBuddyCLI(CodeBuddyConfig(permission_mode=PermissionMode.ACCEPT_EDITS))
    
    for file_path in files:
        print(f"🔧 重构: {file_path}")
        
        result = cli.ask(f"""
        重构 {file_path}:
        1. 应用现代Python特性 (类型注解、f-string、dataclass等)
        2. 提高代码可读性
        3. 优化性能
        4. 保持功能不变
        """)
        
        print(f"{'✅' if result.success else '❌'} {result.elapsed_time:.1f}s")
```

---

## 🔑 关键参数速查表

### CLI 核心参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `-p` | 单次命令模式 | `codebuddy -p "查询"` |
| `--agent` | 指定单个Agent | `--agent reviewer` |
| `--agents` | **动态定义Agent组** (JSON) | 见上方示例 |
| `-y` | 跳过权限确认 | 用于自动化脚本 |
| `--output-format` | 输出格式 (text/json/stream-json) | `--output-format json` |
| `--permission-mode` | 权限模式 | `--permission-mode auto` |
| `--model` | 选择模型 | `--model gpt-5` |
| `--max-turns` | 最大轮次限制 | `--max-turns 5` |

### Agent 配置字段

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `description` | ✅ | 决定何时调用该Agent |
| `prompt` | ✅ | 系统提示词 |
| `tools` | ❌ | 工具白名单 |
| `model` | ❌ | 使用的模型 |
| `disallowedTools` | ❌ | 工具黑名单 |

---

## 📦 我已为你生成的文件

在 `/Users/lenon/Documents/code buddy/cloud ops/` 目录下：

1. **`codebuddy_cli_wrapper.py`** ⭐ 核心
   - 完整的 Python 封装库
   - 支持所有 CLI 功能
   - 包含4套预定义专家团模板
   - 完善的错误处理和日志

2. **`README_codebuddy_cli.md`**
   - 详细使用文档
   - 配置参数参考
   - 故障排除指南
   - 实际应用案例

3. **`quickstart_examples.py`**
   - 6个快速入门示例
   - 可直接运行学习

4. **本文件 (SUMMARY.md)**
   - 核心要点总结

---

## ✨ 下一步行动

1. **安装环境**
   ```bash
   npm install -g @tencent-ai/codebuddy-code
   codebuddy auth login
   ```

2. **测试基本功能**
   ```bash
   cd "/Users/lenon/Documents/code buddy/cloud ops"
   python codebuddy_cli_wrapper.py --demo  # 运行演示
   ```

3. **尝试专家团功能**
   ```bash
   python quickstart_examples.py  # 运行完整示例
   ```

4. **开始自动化**
   - 复制示例代码到你的项目中
   - 根据需求定制专家团
   - 集成到 CI/CD 流水线

---

## 💡 最佳实践建议

### ✅ 推荐

- ✅ 使用 `/init` 初始化项目提升理解准确度
- ✅ 为不同任务配置专门的专家团
- ✅ 设置合理的超时时间 (建议60-300秒)
- ✅ 使用 JSON 输出便于程序化处理
- ✅ 批量任务间添加延迟避免 API 限流
- ✅ 记录所有交互日志便于调试

### ⚠️ 注意

- ⚠️ 生产环境中慎用 `skip_permissions=True`
- ⚠️ 敏感信息不要放在 Prompt 中
- ⚠️ 大型项目首次查询可能较慢
- ⚠️ 定期更新 CodeBuddy CLI 版本

---

## 🆘 常见问题

**Q: 如何知道有哪些预定义的 Agent?**
A: 查看 CLI 文档或使用 `--agent` 后按 Tab 补全

**Q: 专家团的响应速度如何?**
A: 取决于任务复杂度和模型选择，一般10-60秒

**Q: 能否同时使用多个专家?**
A: 可以！定义多个 Agent 后，CodeBuddy 会根据任务自动调度

**Q: 自定义专家有数量限制吗?**
A: 没有硬性限制，但建议 3-7 个以保持高效

**Q: 如何调试专家的行为?**
A: 使用 `--verbose` 参数或检查日志输出

---

## 📚 参考资源

- **CLI 完整文档**: https://www.codebuddy.ai/docs/cli/cli-reference
- **快速入门**: https://www.codebuddy.cn/docs/zh/cli/quickstart
- **官方安装**: https://www.w3cschool.cn/codebuddydocs/codebuddy-cli-quickstart.html

---

## 🎉 总结

**是的，你可以完全通过 Python 触发 CodeBuddy CLI 的专家团功能！**

核心步骤：
1. 定义专家团队 (`AgentConfig`)
2. 配置到 CLI (`set_agents()`)
3. 发送查询 (`ask()`)

这为自动化开发流程提供了无限可能！🚀

---

*最后更新: 2026-06-29*
