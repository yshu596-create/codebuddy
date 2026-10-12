#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeBuddy CLI Python 封装器
============================
通过 Python 脚本触发 CodeBuddy CLI 执行指令，支持调用专家团(Agent)

功能特性:
- 单次命令模式 (Print Mode)
- 交互式会话管理
- 专家团/Agent 动态定义与调用
- 结构化输出 (JSON)
- 管道输入支持
- 后台任务管理
- 错误处理与日志记录

作者: AI Assistant
日期: 2026-06-29
"""

import subprocess
import json
import sys
import os
from typing import Optional, Dict, List, Union, Any
from dataclasses import dataclass, field
from enum import Enum
import logging
import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PermissionMode(Enum):
    """权限模式枚举"""
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    DONT_ASK = "dontAsk"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"


class OutputFormat(Enum):
    """输出格式枚举"""
    TEXT = "text"
    JSON = "json"
    STREAM_JSON = "stream-json"


@dataclass
class AgentConfig:
    """
    Agent/专家团配置类
    
    用于定义自定义的 Sub-Agent，实现专家团功能
    
    示例:
        agent = AgentConfig(
            name="code-reviewer",
            description="专业代码审查专家",
            prompt="你是一名高级代码审查员，专注于代码质量、安全性和最佳实践。",
            tools=["Read", "Grep", "Glob", "Bash"],
            model="sonnet"
        )
    """
    name: str
    description: str
    prompt: str
    tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化"""
        result = {
            "description": self.description,
            "prompt": self.prompt,
        }
        if self.tools:
            result["tools"] = self.tools
        if self.disallowed_tools:
            result["disallowedTools"] = self.disallowed_tools
        if self.model:
            result["model"] = self.model
        return result


@dataclass
class CodeBuddyConfig:
    """CodeBuddy CLI 配置类"""
    # 基础配置
    work_dir: str = "."
    model: Optional[str] = None
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    output_format: OutputFormat = OutputFormat.TEXT
    
    # 高级配置
    max_turns: Optional[int] = None
    verbose: bool = False
    debug: bool = False
    skip_permissions: bool = False  # 对应 -y 参数
    
    # Agent 相关
    agent_name: Optional[str] = None  # 预定义 agent 名称
    agents: Optional[Dict[str, AgentConfig]] = None  # 自定义专家团
    
    # 工具控制
    allowed_tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    
    # 会话相关
    session_id: Optional[str] = None
    continue_last: bool = False
    
    # MCP 配置
    mcp_config: Optional[str] = None


class CodeBuddyCLI:
    """
    CodeBuddy CLI Python 封装器主类
    
    提供对 CodeBuddy CLI 的完整封装，支持所有主要功能
    
    使用示例:
        >>> cli = CodeBuddyCLI()
        >>> result = cli.ask("帮我分析这个项目的结构")
        >>> print(result.output)
        
        >>> # 使用专家团
        >>> reviewer = AgentConfig(
        ...     name="security-expert",
        ...     description="安全审查专家",
        ...     prompt="你是一名网络安全专家...",
        ...     tools=["Read", "Grep", "Bash"]
        ... )
        >>> cli.set_agents({"security-expert": reviewer})
        >>> result = cli.ask("审查这个项目的安全性")
    """
    
    def __init__(self, config: Optional[CodeBuddyConfig] = None):
        """
        初始化 CodeBuddy CLI 封装器
        
        Args:
            config: CodeBuddyConfig 配置对象，如果为 None 则使用默认配置
        """
        self.config = config or CodeBuddyConfig()
        self._last_result: Optional[subprocess.CompletedProcess] = None
        
    def _build_command(self, query: str, input_data: Optional[str] = None) -> List[str]:
        """
        构建 CLI 命令
        
        Args:
            query: 查询/提示词
            input_data: 可选的管道输入数据
            
        Returns:
            完整的命令参数列表
        """
        cmd = ["codebuddy"]
        
        # 添加基础参数
        cmd.extend(["-p", query])  # 使用 Print 模式
        
        # 权限模式
        if self.config.skip_permissions:
            cmd.append("-y")
        elif self.config.permission_mode != PermissionMode.DEFAULT:
            cmd.extend(["--permission-mode", self.config.permission_mode.value])
        
        # 输出格式
        if self.config.output_format != OutputFormat.TEXT:
            cmd.extend(["--output-format", self.config.output_format.value])
        
        # 模型选择
        if self.config.model:
            cmd.extend(["--model", self.config.model])
        
        # 最大轮次
        if self.config.max_turns:
            cmd.extend(["--max-turns", str(self.config.max_turns)])
        
        # Agent 配置 (核心：专家团功能)
        if self.config.agent_name:
            cmd.extend(["--agent", self.config.agent_name])
            
        if self.config.agents:
            agents_dict = {name: agent.to_dict() for name, agent in self.config.agents.items()}
            agents_json = json.dumps(agents_dict, ensure_ascii=False)
            cmd.extend(["--agents", agents_json])
        
        # 工具控制
        if self.config.allowed_tools:
            cmd.extend(["--allowedTools"] + self.config.allowed_tools)
        if self.config.disallowed_tools:
            cmd.extend(["--disallowedTools"] + self.config.disallowed_tools)
        if self.config.tools:
            cmd.extend(["--tools", ",".join(self.config.tools)])
        
        # 会话恢复
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])
        elif self.config.continue_last:
            cmd.append("--continue")
        
        # MCP 配置
        if self.config.mcp_config:
            cmd.extend(["--mcp-config", self.config.mcp_config])
        
        # 调试模式
        if self.config.verbose:
            cmd.append("--verbose")
        if self.config.debug:
            cmd.append("--debug")
        
        return cmd
    
    def ask(self, query: str, input_data: Optional[str] = None, timeout: int = 300) -> 'CodeBuddyResult':
        """
        执行单次查询 (核心方法)
        
        这是主要的交互方法，向 CodeBuddy 发送查询并获取结果
        
        Args:
            query: 自然语言查询/指令 (必填)
            input_data: 可选的管道输入数据 (如日志内容、文件内容等)
            timeout: 超时时间(秒)，默认 5 分钟
            
        Returns:
            CodeBuddyResult 对象，包含输出、状态等信息
            
        Raises:
            RuntimeError: 当 CLI 执行失败时
            TimeoutError: 当执行超时时
            
        示例:
            >>> # 基本用法
            >>> result = cli.ask("解释这个函数的作用")
            >>> 
            >>> # 分析日志
            >>> with open('error.log') as f:
            ...     logs = f.read()
            >>> result = cli.ask("分析这些错误日志", input_data=logs)
            >>>
            >>> # 使用专家团
            >>> result = cli.ask("进行代码安全审查")
        """
        cmd = self._build_command(query, input_data)
        
        logger.info(f"执行命令: {' '.join(cmd[:5])}...")  # 只显示前5个参数避免日志过长
        
        try:
            start_time = time.time()
            
            # 执行命令
            self._last_result = subprocess.run(
                cmd,
                cwd=self.config.work_dir,
                capture_output=True,
                text=True,
                input=input_data,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "en_US.UTF-8"}  # 确保UTF-8编码
            )
            
            elapsed_time = time.time() - start_time
            logger.info(f"命令执行完成，耗时: {elapsed_time:.2f}秒")
            
            # 构建结果对象
            result = CodeBuddyResult(
                stdout=self._last_result.stdout,
                stderr=self._last_result.stderr,
                return_code=self._last_result.returncode,
                success=self._last_result.returncode == 0,
                elapsed_time=elapsed_time,
                command=cmd,
                query=query
            )
            
            if not result.success:
                logger.error(f"命令执行失败，返回码: {result.return_code}")
                logger.error(f"错误输出: {result.stderr[:500]}")  # 截断错误信息
                
            return result
            
        except subprocess.TimeoutExpired:
            logger.error(f"命令执行超时 ({timeout}秒)")
            raise TimeoutError(f"CodeBuddy CLI 执行超时 ({timeout}秒)")
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 codebuddy 命令。请确保已安装 CodeBuddy CLI:\n"
                "  npm install -g @tencent-ai/codebuddy-code"
            )
        except Exception as e:
            logger.error(f"执行命令时发生错误: {str(e)}")
            raise RuntimeError(f"执行 CodeBuddy CLI 失败: {str(e)}")
    
    def ask_json(self, query: str, **kwargs) -> Dict[str, Any]:
        """
        以 JSON 格式执行查询并返回解析后的字典
        
        适合需要程序化处理结果的场景
        
        Args:
            query: 查询语句
            **kwargs: 传递给 ask() 的其他参数
            
        Returns:
            解析后的 JSON 字典
            
        示例:
            >>> result = cli.ask_json("统计这个项目的代码行数")
            >>> print(result.get('total_lines'))
        """
        original_format = self.config.output_format
        self.config.output_format = OutputFormat.JSON
        
        try:
            result = self.ask(query, **kwargs)
            
            if result.success and result.stdout.strip():
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    logger.warning("无法解析 JSON 输出，返回原始文本")
                    return {"raw_output": result.stdout, "parse_error": True}
            else:
                return {"error": result.stderr, "success": False}
                
        finally:
            self.config.output_format = original_format
    
    def set_agents(self, agents: Dict[str, AgentConfig]):
        """
        设置专家团配置 (Agent Team)
        
        允许同时配置多个专家 Agent，CodeBuddy 会根据任务自动选择合适的专家
        
        Args:
            agents: 字典，key为agent名称，value为AgentConfig对象
            
        示例:
            >>> experts = {
            ...     "reviewer": AgentConfig(
            ...         name="reviewer",
            ...         description="代码审查专家",
            ...         prompt="专注于代码质量和最佳实践...",
            ...         tools=["Read", "Grep"]
            ...     ),
            ...     "tester": AgentConfig(
            ...         name="tester",
            ...         description="测试专家",
            ...         prompt="专注于生成单元测试和集成测试..."
            ...     ),
            ...     "security": AgentConfig(
            ...         name="security",
            ...         description="安全专家",
            ...         prompt="专注于安全性分析和漏洞检测...",
            ...         model="sonnet"
            ...     )
            ... }
            >>> cli.set_agents(experts)
            >>> # 现在所有查询都会自动使用这些专家
            >>> result = cli.ask("全面审查这个项目")  # 会自动调度合适的专家
        """
        self.config.agents = agents
        logger.info(f"已设置 {len(agents)} 个专家 Agent: {list(agents.keys())}")
    
    def set_agent(self, agent_name: str):
        """
        设置单个预定义 Agent
        
        Args:
            agent_name: Agent 名称 (如 my-reviewer, debugger 等)
        """
        self.config.agent_name = agent_name
        logger.info(f"已设置当前 Agent: {agent_name}")
    
    def clear_agents(self):
        """清除所有 Agent 配置"""
        self.config.agents = None
        self.config.agent_name = None
        logger.info("已清除 Agent 配置")


@dataclass
class CodeBuddyResult:
    """
    CodeBuddy 执行结果数据类
    
    包含执行的所有相关信息
    """
    stdout: str  # 标准输出
    stderr: str  # 标准错误
    return_code: int  # 返回码
    success: bool  # 是否成功
    elapsed_time: float  # 执行耗时(秒)
    command: List[str]  #执行的命令
    query: str  # 原始查询
    
    @property
    def output(self) -> str:
        """获取标准输出 (快捷属性)"""
        return self.stdout
    
    @property
    def error(self) -> str:
        """获取错误信息 (快捷属性)"""
        return self.stderr
    
    def json(self) -> Optional[Dict[str, Any]]:
        """尝试将输出解析为 JSON"""
        try:
            return json.loads(self.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
    
    def print_summary(self):
        """打印执行摘要"""
        print("\n" + "="*60)
        print("CodeBuddy CLI 执行摘要")
        print("="*60)
        print(f"✅ 状态: {'成功' if self.success else '失败'}")
        print(f"⏱️  耗时: {self.elapsed_time:.2f} 秒")
        print(f"🔢 返回码: {self.return_code}")
        print(f"💬 查询: {self.query[:50]}...")
        print("-"*60)
        if self.stdout:
            print("📤 输出:")
            print(self.stdout[:1000])  # 限制输出长度
            if len(self.stdout) > 1000:
                print(f"\n... (输出已截断，共{len(self.stdout)}字符)")
        if self.stderr:
            print("⚠️  错误:")
            print(self.stderr[:500])
        print("="*60 + "\n")


# ============================================================
# 预定义的专家团模板 (Expert Team Templates)
# ============================================================

class ExpertTeams:
    """
    预定义的专家团模板集合
    
    提供常用的专家团队配置，可直接使用或作为模板定制
    
    使用示例:
        >>> cli = CodeBuddyCLI()
        >>> cli.set_agents(ExpertTeams.full_stack_team())
        >>> result = cli.ask("重构这个模块")
    """
    
    @staticmethod
    def code_review_team() -> Dict[str, AgentConfig]:
        """
        代码审查专家团
        
        包含: 代码质量、性能、安全三个维度的审查专家
        """
        return {
            "quality-reviewer": AgentConfig(
                name="quality-reviewer",
                description="代码质量审查专家。在代码变更后主动使用。专注于代码可读性、可维护性和最佳实践。",
                prompt="你是一名资深代码审查专家，专注于代码质量。检查代码的可读性、可维护性、命名规范、代码重复、复杂度等问题。提供具体的改进建议。",
                tools=["Read", "Grep", "Glob"],
                model="sonnet"
            ),
            "performance-reviewer": AgentConfig(
                name="performance-reviewer",
                description="性能优化专家。识别性能瓶颈和优化机会。",
                prompt="你是一名性能优化专家。分析代码的时间复杂度、空间复杂度、I/O 操作效率、数据库查询性能等。提供具体的性能优化建议和预期提升。",
                tools=["Read", "Grep", "Bash"],
                model="sonnet"
            ),
            "security-reviewer": AgentConfig(
                name="security-reviewer",
                description="安全审计专家。检测安全漏洞和潜在风险。",
                prompt="你是一名网络安全专家。检查SQL注入、XSS、CSRF、认证授权问题、敏感数据处理等安全风险。提供安全修复建议和合规性检查。",
                tools=["Read", "Grep", "Glob"],
                model="sonnet"
            )
        }
    
    @staticmethod
    def development_team() -> Dict[str, AgentConfig]:
        """
        开发专家团
        
        包含: 功能开发、测试生成、文档编写三个角色
        """
        return {
            "feature-developer": AgentConfig(
                name="feature-developer",
                description="功能开发专家。负责新功能的实现和代码编写。",
                prompt="你是一名全栈开发工程师，擅长将需求转化为高质量的代码。遵循 SOLID 原则和设计模式，编写清晰、高效、可测试的代码。",
                tools=["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
                model="opus"
            ),
            "test-engineer": AgentConfig(
                name="test-engineer",
                description="测试工程师。生成全面的测试用例。",
                prompt="你是一名测试工程师，专注于生成单元测试、集成测试和端到端测试。确保测试覆盖边界条件、异常场景和正常流程。使用适当的测试框架和模式。",
                tools=["Read", "Edit", "Write", "Grep", "Bash"],
                model="sonnet"
            ),
            "document-writer": AgentConfig(
                name="document-writer",
                description="技术文档专家。编写和维护项目文档。",
                prompt="你是一名技术写作专家。编写清晰的 API 文档、README、架构说明和使用指南。使用标准的文档格式和结构。",
                tools=["Read", "Edit", "Write", "Glob"],
                model="sonnet"
            )
        }
    
    @staticmethod
    def devops_team() -> Dict[str, AgentConfig]:
        """
        DevOps/运维专家团
        
        包含: CI/CD、监控告警、基础设施管理
        """
        return {
            "ci-cd-engineer": AgentConfig(
                name="ci-cd-engineer",
                description="CI/CD 工程师。构建和管理自动化流水线。",
                prompt="你是一名 DevOps 工程师，专精于 CI/CD 流水线设计、Docker 容器化、Kubernetes 编排。设计高效、可靠的自动化部署流程。",
                tools=["Read", "Edit", "Write", "Bash", "Glob"],
                model="opus"
            ),
            "monitoring-expert": AgentConfig(
                name="monitoring-expert",
                description="监控专家。设计和实施监控系统。",
                prompt="你是一名系统监控专家。设计日志聚合、指标采集、告警策略和可视化面板。确保系统的可观测性和故障快速定位能力。",
                tools=["Read", "Edit", "Write", "Bash", "Grep"],
                model="sonnet"
            ),
            "infrastructure-architect": AgentConfig(
                name="infrastructure-architect",
                description="基础设施架构师。规划云资源和系统架构。",
                prompt="你是一名云架构师。设计高可用、可扩展、成本优化的基础设施方案。熟悉 AWS、Azure、GCP、阿里云等主流云平台。",
                tools=["Read", "Write", "Bash"],
                model="opus"
            )
        }
    
    @staticmethod
    def full_stack_team() -> Dict[str, AgentConfig]:
        """
        全栈开发完整专家团
        
        综合以上所有专家，适用于大型项目
        """
        team = {}
        team.update(ExpertTeams.code_review_team())
        team.update(ExpertTeams.development_team())
        team.update(ExpertTeams.devops_team())
        
        # 添加架构师作为总协调
        team["tech-lead"] = AgentConfig(
            name="tech-lead",
            description="技术负责人/架构师。协调各专家工作，把控技术方向。",
            prompt="你是技术负责人(Tech Lead)。负责技术决策、架构设计、任务分解和质量把关。协调各个专家的工作，确保项目的技术质量和进度。",
            tools=["Read", "Grep", "Glob", "Bash"],
            model="opus"
        )
        
        return team


# ============================================================
# 便捷函数 (Convenience Functions)
# ============================================================

def quick_ask(query: str, 
              work_dir: str = ".", 
              use_experts: bool = False,
              expert_team: Optional[str] = None,
              skip_permissions: bool = False,
              output_json: bool = False) -> CodeBuddyResult:
    """
    快速查询便捷函数
    
    一行代码完成查询，适合简单场景
    
    Args:
        query: 查询语句
        work_dir: 工作目录
        use_experts: 是否使用专家团
        expert_team: 专家团名称 ('code-review', 'development', 'devops', 'full-stack')
        skip_permissions: 是否跳过权限确认
        output_json: 是否使用 JSON 输出
        
    Returns:
        CodeBuddyResult 对象
        
    示例:
        >>> # 最简用法
        >>> result = quick_ask("这个函数是做什么的？")
        >>> print(result.output)
        >>>
        >>> # 使用专家团
        >>> result = quick_ask("审查代码质量", use_experts=True, expert_team='code-review')
        >>>
        >>> # 自动化脚本中使用
        >>> result = quick_ask("运行测试套件", skip_permissions=True)
        >>> if not result.success:
        ...     print(f"错误: {result.error}")
    """
    config = CodeBuddyConfig(
        work_dir=work_dir,
        skip_permissions=skip_permissions,
        output_format=OutputFormat.JSON if output_json else OutputFormat.TEXT
    )
    
    cli = CodeBuddyCLI(config)
    
    if use_experts and expert_team:
        team_map = {
            'code-review': ExpertTeams.code_review_team(),
            'development': ExpertTeams.development_team(),
            'devops': ExpertTeams.devops_team(),
            'full-stack': ExpertTeams.full_stack_team()
        }
        if expert_team in team_map:
            cli.set_agents(team_map[expert_team])
    
    return cli.ask(query)


def batch_process(queries: List[str], 
                  config: Optional[CodeBuddyConfig] = None,
                  delay: float = 1.0) -> List[CodeBuddyResult]:
    """
    批量处理多个查询
    
    适用于需要连续执行多个任务的场景
    
    Args:
        queries: 查询列表
        config: 共享的配置对象
        delay: 每个查询之间的延迟(秒)，避免API限流
        
    Returns:
        结果列表
        
    示例:
        >>> queries = [
        ...     "分析 utils.py 的功能",
        ...     "为主函数生成单元测试",
        ...     "创建 README 文档"
        ... ]
        >>> results = batch_process(queries)
        >>> for i, r in enumerate(results):
        ...     print(f"任务{i+1}: {'✅' if r.success else '❌'}")
    """
    cli = CodeBuddyCLI(config)
    results = []
    
    total = len(queries)
    for i, query in enumerate(queries, 1):
        logger.info(f"处理第 {i}/{total} 个查询...")
        try:
            result = cli.ask(query)
            results.append(result)
        except Exception as e:
            logger.error(f"查询 '{query[:30]}...' 失败: {str(e)}")
            # 创建一个失败的结果占位
            results.append(CodeBuddyResult(
                stdout="", 
                stderr=str(e), 
                return_code=-1,
                success=False,
                elapsed_time=0,
                command=[],
                query=query
            ))
        
        # 添加延迟，避免请求过快
        if i < total and delay > 0:
            time.sleep(delay)
    
    return results


# ============================================================
# 主程序入口 (CLI 模式支持)
# ============================================================

def main():
    """
    主程序入口
    
    支持命令行参数调用，也可作为模块导入使用
    
    用法:
        python codebuddy_cli_wrapper.py "你的查询"
        python codebuddy_cli_wrapper.py "查询" --expert-team full-stack --json
        python codebuddy_cli_wrapper.py --interactive
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description='CodeBuddy CLI Python 封装器 - 通过Python触发CodeBuddy CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s "分析项目结构"
  %(prog)s "审查代码质量" --expert-team code-review --json
  %(prog)s "运行测试" --skip-permissions --work-dir ./my-project
  %(prog)s --demo                    # 运行内置演示
        """
    )
    
    parser.add_argument('query', nargs='?', help='要发送给 CodeBuddy 的查询')
    parser.add_argument('--work-dir', '-w', default='.', help='工作目录 (默认: .)')
    parser.add_argument('--expert-team', '-e', choices=['code-review', 'development', 'devops', 'full-stack'],
                       help='使用的预定义专家团')
    parser.add_argument('--json', '-j', action='store_true', help='使用 JSON 格式输出')
    parser.add_argument('--skip-permissions', '-y', action='store_true', help='跳过权限确认 (-y)')
    parser.add_argument('--model', '-m', help='指定模型')
    parser.add_argument('--timeout', type=int, default=300, help='超时时间(秒) (默认: 300)')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    parser.add_argument('--demo', action='store_true', help='运行演示示例')
    parser.add_argument('--batch-file', help='从文件读取批量查询(每行一个查询)')
    
    args = parser.parse_args()
    
    # 演示模式
    if args.demo:
        run_demo()
        return
    
    # 必须提供查询或批量文件
    if not args.query and not args.batch_file:
        parser.print_help()
        print("\n❌ 错误: 请提供查询语句或使用 --batch-file 指定批量查询文件")
        sys.exit(1)
    
    # 创建配置
    config = CodeBuddyConfig(
        work_dir=args.work_dir,
        skip_permissions=args.skip_permissions,
        output_format=OutputFormat.JSON if args.json else OutputFormat.TEXT,
        model=args.model
    )
    
    cli = CodeBuddyCLI(config)
    
    # 设置专家团
    if args.expert_team:
        team_map = {
            'code-review': ExpertTeams.code_review_team(),
            'development': ExpertTeams.development_team(),
            'devops': ExpertTeams.devops_team(),
            'full-stack': ExpertTeams.full_stack_team()
        }
        cli.set_agents(team_map[args.expert_team])
        print(f"🎯 已加载专家团: {args.expert_team}")
    
    try:
        if args.batch_file:
            # 批量模式
            with open(args.batch_file, 'r', encoding='utf-8') as f:
                queries = [line.strip() for line in f if line.strip()]
            
            print(f"📋 开始处理 {len(queries)} 个查询...\n")
            results = batch_process(queries, config)
            
            success_count = sum(1 for r in results if r.success)
            print(f"\n✅ 完成! 成功: {success_count}/{len(results)}")
            
            for i, result in enumerate(results, 1):
                status = "✅" if result.success else "❌"
                print(f"{status} 任务 {i}: {result.query[:50]}...")
                if args.verbose or not result.success:
                    if result.error:
                        print(f"   错误: {result.error[:200]}")
                    
        else:
            # 单次查询模式
            print(f"🚀 执行查询: {args.query}\n")
            result = cli.ask(args.query, timeout=args.timeout)
            result.print_summary()
            
            if args.json and result.json():
                # 将 JSON 结果保存到文件
                output_file = f"codebuddy_result_{int(time.time())}.json"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(result.json(), f, ensure_ascii=False, indent=2)
                print(f"📁 JSON 结果已保存到: {output_file}")
            
    except KeyboardInterrupt:
        print("\n⚠️  用户中断操作")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def run_demo():
    """
    运行演示示例
    
    展示各种功能的使用方法
    """
    print("="*70)
    print("CodeBuddy CLI Python 封装器 - 功能演示")
    print("="*70)
    
    demo_dir = "/Users/lenon/Documents/code buddy/cloud ops"
    
    # 演示1: 基本查询
    print("\n📌 演示 1: 基本查询 - 了解当前目录结构")
    print("-"*70)
    try:
        config = CodeBuddyConfig(work_dir=demo_dir, skip_permissions=True)
        cli = CodeBuddyCLI(config)
        result = cli.ask("简要描述当前目录的结构和主要内容", timeout=30)
        
        if result.success:
            print("✅ 查询成功!")
            print("\n📤 CodeBuddy 回复:")
            print(result.output[:800])
        else:
            print(f"❌ 查询失败: {result.error[:200]}")
    except Exception as e:
        print(f"⚠️  演示1出错(可能是未安装CLI): {e}")
    
    # 演示2: 专家团配置展示
    print("\n\n📌 演示 2: 专家团(Agent)配置展示")
    print("-"*70)
    
    print("\n🎯 可用的预定义专家团:")
    teams = ['code-review', 'development', 'devops', 'full-stack']
    for team in teams:
        agents = ExpertTeams.full_stack_team() if team == 'full-stack' else \
                 getattr(ExpertTeams, f"{team.replace('-', '_')}_team")()
        print(f"  • {team}: 包含 {len(agents)} 个专家 - {list(agents.keys())}")
    
    print("\n💡 自定义专家团示例:")
    custom_agent = AgentConfig(
        name="python-expert",
        description="Python语言专家",
        prompt="你是一名Python语言专家，精通Python生态系统、性能优化和最佳实践。",
        tools=["Read", "Edit", "Write", "Bash", "Grep"],
        model="sonnet"
    )
    print(f"   名称: {custom_agent.name}")
    print(f"   描述: {custom_agent.description}")
    print(f"   工具: {custom_agent.tools}")
    print(f"   Prompt: {custom_agent.prompt[:80]}...")
    
    # 演示3: Python API 使用方式
    print("\n\n📌 演示 3: Python API 使用示例代码")
    print("-"*70)
    
    examples = '''
# 示例 1: 基本使用
from codebuddy_cli_wrapper import CodeBuddyCLI, quick_ask

# 一行代码完成查询
result = quick_ask("解释这个函数的作用")
print(result.output)

# 示例 2: 使用专家团
cli = CodeBuddyCLI()
cli.set_agents({
    "reviewer": AgentConfig(
        name="reviewer",
        description="代码审查专家",
        prompt="专注代码质量审查...",
        tools=["Read", "Grep"]
    )
})
result = cli.ask("审查 src/main.py 的代码质量")

# 示例 3: 批量处理
queries = [
    "分析项目结构",
    "生成单元测试",
    "更新文档"
]
results = batch_process(queries)

# 示例 4: JSON 输出
result = cli.ask_json("统计代码行数")
print(result.get('lines_of_code'))

# 示例 5: 在自动化脚本中使用
if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "Hello"
    result = quick_ask(query, skip_permissions=True, output_json=True)
    print(result.json())
'''
    print(examples)
    
    # 演示4: 命令行使用
    print("\n\n📌 演示 4: 命令行使用方式")
    print("-"*70)
    
    usage_examples = '''
# 基本用法
python codebuddy_cli_wrapper.py "你的查询"

# 使用专家团
python codebuddy_cli_wrapper.py "审查代码" --expert-team code-review

# JSON 输出
python codebuddy_cli_wrapper.py "分析项目" --json --verbose

# 批量处理
cat queries.txt | xargs -I {} python codebuddy_cli_wrapper.py "{}"

# 跳过权限(自动化场景)
python codebuddy_cli_wrapper.py "运行测试" -y --timeout 600

# 指定工作目录
python codebuddy_cli_wrapper.py "重构模块" --work-dir /path/to/project
'''
    print(usage_examples)
    
    print("\n" + "="*70)
    print("✨ 演示完成!")
    print("="*70)
    print("\n💡 提示:")
    print("  1. 确保已安装 CodeBuddy CLI: npm install -g @tencent-ai/codebuddy-code")
    print("  2. 首次使用需要登录: codebuddy auth login")
    print("  3. 推荐先运行: codebuddy /init  (初始化项目上下文)")
    print("  4. 查看 CLI 文档: https://www.codebuddy.ai/docs/cli/cli-reference")
    print()


if __name__ == "__main__":
    main()
