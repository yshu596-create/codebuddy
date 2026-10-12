#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeBuddy CLI 快速入门示例
==========================
展示如何通过 Python 调用 CodeBuddy CLI 和专家团功能

使用前请确保:
1. 已安装 Node.js 18+
2. 已安装 CodeBuddy: npm install -g @tencent-ai/codebuddy-code
3. 已完成登录: codebuddy auth login

用法:
    python3 quickstart_examples.py              # 交互模式: 每跑完一个示例等待回车
    python3 quickstart_examples.py --no-pause   # 连续运行 (CI / 非交互场景)
    python3 quickstart_examples.py --only 2     # 只运行示例 2
"""

import argparse
import json
import os
import sys

# 以脚本自身位置推导工程目录，避免硬编码绝对路径
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from codebuddy_cli_wrapper import (
    CodeBuddyCLI,
    CodeBuddyConfig,
    AgentConfig,
    ExpertTeams,
    PermissionMode,
    OutputFormat,
    quick_ask,
    batch_process
)

# 审查类任务涉及多轮推理，单次调用默认给足时间
DEFAULT_TIMEOUT = 180


def _preview(text, limit=500):
    """安全截断输出: 空值有兜底，未截断时不拼省略号。"""
    text = (text or "").strip()
    if not text:
        return "(无输出)"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (已截断，共 {len(text)} 字符)"


def example_1_basic_query():
    """示例1: 基本查询"""
    print("=" * 70)
    print("📌 示例 1: 基本查询")
    print("=" * 70)

    result = quick_ask(
        "简要介绍你自己",
        work_dir=WORKSPACE,
        skip_permissions=True
    )

    print(f"✅ 状态: {'成功' if result.success else '失败'}")
    print(f"📤 输出:\n{_preview(result.output)}")
    if not result.success:
        print(f"⚠️  错误信息: {_preview(result.error, 300)}")
    print()


def example_2_expert_team():
    """示例2: 使用专家团 (最强大的功能!)"""
    print("=" * 70)
    print("📌 示例 2: 使用专家团进行代码审查")
    print("=" * 70)

    # 注意: skip_permissions(-y) 与 permission-mode 互斥，
    # 同时设置时 -y 生效、permission_mode 会被静默丢弃，这里只保留一种。
    # 审查只读场景用 ACCEPT_EDITS 即可，不建议用 AUTO(放行一切操作)。
    config = CodeBuddyConfig(
        work_dir=WORKSPACE,
        permission_mode=PermissionMode.ACCEPT_EDITS,
        output_format=OutputFormat.TEXT
    )

    cli = CodeBuddyCLI(config)
    cli.set_agents(ExpertTeams.code_review_team())

    print(f"🎯 已加载 {len(cli.config.agents)} 个专家:")
    for name, agent in cli.config.agents.items():
        print(f"   • {name}: {agent.description}")
    print()

    # 超时时 ask() 会 raise TimeoutError，不会返回 success=False，必须显式捕获
    try:
        result = cli.ask(
            "审查 codebuddy_cli_wrapper.py 文件的代码质量，"
            "包括可读性、性能和安全性三个维度",
            timeout=DEFAULT_TIMEOUT
        )
    except TimeoutError as e:
        print(f"⏱️  审查超时: {e}\n(调大 DEFAULT_TIMEOUT 后重试)")
        return

    if result.success:
        print("✅ 审查完成!")
        print(f"\n📤 专家团分析结果:\n{'-' * 70}")
        print(_preview(result.output, 1500))
    else:
        print(f"❌ 执行失败(返回码 {result.return_code}): {_preview(result.error, 300)}")
    print()


def example_3_custom_agents():
    """示例3: 自定义专家团"""
    print("=" * 70)
    print("📌 示例 3: 自定义专家团 - Python开发团队")
    print("=" * 70)

    # model 留空则沿用 CLI 默认模型，避免填入当前账号不支持的别名导致启动失败
    my_team = {
        "python-guru": AgentConfig(
            name="python-guru",
            description="Python语言大师",
            prompt="你是Python语言专家，精通CPython实现、性能优化、异步编程和设计模式。"
                   "提供符合PEP8规范的最佳实践建议。",
            tools=["Read", "Edit", "Write", "Grep", "Glob", "Bash"]
        ),

        "testing-ninja": AgentConfig(
            name="testing-ninja",
            description="测试忍者",
            prompt="你是测试工程师专家，精通pytest、unittest、mock、覆盖率分析等。"
                   "编写全面的单元测试、集成测试和端到端测试。",
            tools=["Read", "Edit", "Write", "Bash"]
        ),

        "doc-wizard": AgentConfig(
            name="doc-wizard",
            description="文档巫师",
            prompt="你是技术文档专家，擅长编写清晰的API文档、教程和使用指南。"
                   "使用Sphinx、Markdown和reStructuredText格式。",
            tools=["Read", "Write", "Edit"]
        )
    }

    config = CodeBuddyConfig(work_dir=WORKSPACE)
    cli = CodeBuddyCLI(config)
    cli.set_agents(my_team)

    print(f"👥 自定义团队: {list(my_team.keys())}\n")

    # cli.ask() 是"整团参与、由 CLI 路由"，要点名某个专家须用 set_agent() 切换
    tasks = [
        ("python-guru", "审查 codebuddy_cli_wrapper.py 的 Python 实现，给出性能优化建议"),
        ("testing-ninja", "为 codebuddy_cli_wrapper.py 中的核心函数生成 pytest 测试用例"),
        ("doc-wizard", "为 codebuddy_cli_wrapper.py 的公开 API 补充文档注释")
    ]

    for expert_name, task in tasks:
        print(f"▶️ 调用专家 [{expert_name}] 处理任务: {task}")
        cli.set_agent(expert_name)
        try:
            result = cli.ask(task, timeout=DEFAULT_TIMEOUT)
        except TimeoutError as e:
            print(f"⏱️  超时: {e}\n")
            continue
        status = "✅" if result.success else "❌"
        print(f"{status} 完成 ({result.elapsed_time:.1f}s)")
        if result.success:
            print(f"   摘要: {_preview(result.output, 200)}\n")
        else:
            print(f"   错误: {_preview(result.error, 200)}\n")


def example_4_json_output():
    """示例4: JSON结构化输出"""
    print("=" * 70)
    print("📌 示例 4: JSON 结构化输出")
    print("=" * 70)

    config = CodeBuddyConfig(
        work_dir=WORKSPACE,
        output_format=OutputFormat.JSON
    )
    cli = CodeBuddyCLI(config)

    # 结论: --output-format json 只保证"输出是合法 JSON"，
    # 具体字段名由模型回答决定，不要假设固定 schema，取前先做容错判断。
    result = cli.ask_json(
        "统计当前目录下 Python 文件的数量，只返回一个 JSON 对象，"
        "形如 {\"python_files\": <数量>, \"files\": [<文件名>]}",
        timeout=DEFAULT_TIMEOUT
    )

    if not isinstance(result, dict):
        print(f"❌ 未返回可解析的 JSON: {result}")
        return
    if result.get("success") is False or "error" in result:
        print(f"❌ 执行失败: {_preview(str(result.get('error', result)), 300)}")
        return

    print("✅ 获取到JSON数据:")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:800])

    if result.get("parse_error"):
        print("\n⚠️ CLI 未返回合法 JSON，已回退为 raw_output 字段；"
              "如需稳定字段，请在 prompt 里显式约束输出结构。")
        return

    # 程序化处理: 只在字段确实存在时读取
    if isinstance(result.get("python_files"), int):
        print(f"\n📊 Python 文件数: {result['python_files']}")
    for name in (result.get("files") or [])[:5]:
        print(f"   • {name}")
    print()


def example_5_batch_processing():
    """示例5: 批量处理"""
    print("=" * 70)
    print("📌 示例 5: 批量处理多个任务")
    print("=" * 70)

    queries = [
        "列出当前目录的所有Python文件",
        "找出最长的Python文件",
        "检查是否有TODO或FIXME标记"
    ]

    print(f"📋 待处理任务数: {len(queries)}\n")

    # batch_process 不转发 timeout，长任务需自行循环调用 cli.ask(query, timeout=...)
    results = batch_process(
        queries,
        delay=1.0,  # 每个任务间隔1秒
        config=CodeBuddyConfig(work_dir=WORKSPACE, skip_permissions=True)
    )

    for i, result in enumerate(results, 1):
        status = "✅ 成功" if result.success else "❌ 失败"
        print(f"[{i}/{len(queries)}] {status} | 耗时:{result.elapsed_time:.1f}s | {result.query[:40]}...")
        if not result.success:
            print(f"        原因: {_preview(result.error, 200)}")
    print()


def example_6_pipeline_input():
    """示例6: 管道输入 - 分析外部内容"""
    print("=" * 70)
    print("📌 示例 6: 管道输入 - 分析代码片段")
    print("=" * 70)

    code_snippet = '''
def process_data(data_list):
    result = []
    for item in data_list:
        processed = {
            'id': item.get('id'),
            'name': item.get('name'),
            'value': float(item.get('value', 0)) * 1.2,
            'status': 'active' if item.get('enabled') else 'inactive'
        }
        result.append(processed)

    # 排序
    sorted_result = sorted(result, key=lambda x: x['value'], reverse=True)

    return sorted_result
'''

    cli = CodeBuddyCLI(CodeBuddyConfig(work_dir=WORKSPACE))

    # 打印模式下 stdin 是否被消费取决于 CLI 版本，
    # 因此把内容同时写进 prompt 兜底，保证代码一定会进入上下文。
    query = (
        "审查下面这段Python代码的质量，并提供优化建议:\n"
        "1. 类型安全性\n2. 错误处理\n3. 性能优化\n4. 可读性改进\n\n"
        f"```python\n{code_snippet}\n```"
    )

    try:
        result = cli.ask(query, input_data=code_snippet, timeout=DEFAULT_TIMEOUT)
    except TimeoutError as e:
        print(f"⏱️  超时: {e}\n")
        return

    if result.success:
        print("✅ 代码审查完成:")
        print(_preview(result.output, 1000))
    else:
        print(f"❌ 执行失败: {_preview(result.error, 300)}")
    print()


def main():
    """运行示例"""
    parser = argparse.ArgumentParser(description="CodeBuddy CLI 快速入门示例")
    parser.add_argument("--only", type=int, help="只运行指定编号的示例 (1-6)")
    parser.add_argument("--no-pause", action="store_true",
                        help="不等待回车，连续运行 (非交互/CI 场景)")
    args = parser.parse_args()

    print("\n" + "🚀" * 35)
    print("CodeBuddy CLI Python 封装器 - 快速入门示例")
    print("🚀" * 35 + "\n")

    examples = [
        ("基本查询", example_1_basic_query),
        ("专家团功能 ⭐", example_2_expert_team),
        ("自定义专家团", example_3_custom_agents),
        ("JSON输出", example_4_json_output),
        ("批量处理", example_5_batch_processing),
        ("管道输入", example_6_pipeline_input),
    ]

    if args.only is not None:
        if not 1 <= args.only <= len(examples):
            parser.error(f"--only 取值需在 1~{len(examples)} 之间")
        examples = [examples[args.only - 1]]

    no_pause = args.no_pause

    for i, (name, func) in enumerate(examples, 1):
        try:
            print(f"\n{'=' * 70}")
            print(f"▶️ 运行示例 {i}/{len(examples)}: {name}")
            print(f"{'=' * 70}\n")

            func()

            if not no_pause and i < len(examples):
                try:
                    input("\n按 Enter 键继续下一个示例...")
                except EOFError:
                    # 非交互环境没有 stdin，自动切换为连续运行
                    print("\n⚠️ 检测到非交互环境，后续示例不再等待回车")
                    no_pause = True

        except KeyboardInterrupt:
            print("\n⚠️ 用户中断")
            break
        except TimeoutError as e:
            print(f"\n⚠️ 执行超时: {e}")
            continue
        except RuntimeError as e:
            # 通常是 command not found
            print(f"\n⚠️ 示例出错: {e}")
            continue
        except Exception as e:
            print(f"\n⚠️ 示例出错: {e}")
            continue

    print("\n" + "=" * 70)
    print("✨ 所有示例演示完成!")
    print("=" * 70)
    print("\n💡 下一步:")
    print("  1. 安装 CodeBuddy CLI: npm install -g @tencent-ai/codebuddy-code")
    print("  2. 运行认证: codebuddy auth login")
    print("  3. 初始化项目: cd your-project && codebuddy /init")
    print("  4. 再次运行此脚本查看完整效果")
    print()


if __name__ == "__main__":
    main()
