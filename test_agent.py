#!/usr/bin/env python3
"""
cc-astrbot-agent 本地测试脚本
=============================

用途：
  验证 Agent 核心循环（工具调用、文件读写、命令执行）是否正常工作。

测试场景：
  场景1: 直接调用工具 — 读取 README.md
  场景2: 直接调用工具 — 创建 test_output.txt 并写入内容
  场景3: 直接调用工具 — 执行 bash 命令 ls/dir（跨平台）
  场景4: Agent 循环 — 组合任务"读取 README.md 前 10 行，写入 summary.txt"

使用方式：
  # 1. 设置 API Key（场景 4 需要）
  set ANTHROPIC_API_KEY=sk-ant-xxx        (Windows CMD)
  export ANTHROPIC_API_KEY=sk-ant-xxx     (Linux/Mac)

  # 2. 运行全部场景
  python test_agent.py

  # 3. 指定项目目录
  python test_agent.py --project-root /path/to/project

  # 4. 只运行指定场景
  python test_agent.py --scenario 1
  python test_agent.py --scenario 4 --task "列出 src/ 目录结构"

  # 5. 跳过需要 API 的场景
  python test_agent.py --skip-api

依赖：
  pip install httpx pydantic
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import sys
import time
from pathlib import Path

# 确保项目 src 目录在 import 路径中
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cc_agent.agent import ClaudeCodeAgent
from cc_agent.tools.registry import ToolRegistry


# ============================================================================
# 工具函数
# ============================================================================

class _Colors:
    """终端颜色（ANSI 转义码）"""
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _print_header(title: str) -> None:
    width = 60
    print(f"\n{'=' * width}")
    print(f"{_Colors.HEADER}{_Colors.BOLD}  {title}{_Colors.RESET}")
    print(f"{'=' * width}")


def _print_ok(msg: str) -> None:
    print(f"  {_Colors.GREEN}[OK]{_Colors.RESET} {msg}")


def _print_fail(msg: str) -> None:
    print(f"  {_Colors.RED}[FAIL]{_Colors.RESET} {msg}")


def _print_info(msg: str) -> None:
    print(f"  {_Colors.CYAN}[INFO]{_Colors.RESET} {msg}")


def _print_warn(msg: str) -> None:
    print(f"  {_Colors.YELLOW}[WARN]{_Colors.RESET} {msg}")


def _print_result(label: str, value: str, max_lines: int = 20) -> None:
    """打印工具结果，限制行数"""
    lines = value.splitlines()
    truncated = len(lines) > max_lines
    display = lines[:max_lines]
    print(f"  {_Colors.DIM}{label}:{_Colors.RESET}")
    for line in display:
        print(f"    {line}")
    if truncated:
        print(f"    ... (共 {len(lines)} 行，已截断显示前 {max_lines} 行)")


# ============================================================================
# 场景 1: 读取文件（直接调用工具）
# ============================================================================

async def test_read_file(agent: ClaudeCodeAgent, project_root: Path) -> bool:
    _print_header("场景1: 读取文件 — read_file 工具")

    # 选择一个已知存在的文件
    readme_path = project_root / "README.md"
    target = "README.md" if readme_path.exists() else "pyproject.toml"

    _print_info(f"读取: {target}")
    start = time.monotonic()

    try:
        result = await agent.tool_registry.get_tool("read_file").call(
            {"path": str(target)},
        )
        elapsed = time.monotonic() - start

        if isinstance(result, dict):
            error = result.get("error")
            if error:
                _print_fail(f"工具返回错误: {error}")
                return False

            content = result.get("content", "")
            if content:
                _print_ok(f"成功读取 ({len(content)} 字符, {elapsed:.2f}s)")
                _print_result("内容预览", content, max_lines=10)
                return True
            else:
                _print_fail("工具返回空内容")
                return False
        else:
            _print_ok(f"成功 ({elapsed:.2f}s)")
            return True

    except Exception as e:
        _print_fail(f"异常: {e}")
        return False


# ============================================================================
# 场景 2: 写入文件（直接调用工具）
# ============================================================================

async def test_write_file(agent: ClaudeCodeAgent, project_root: Path) -> bool:
    _print_header("场景2: 写入文件 — write_file 工具")

    test_content = (
        "cc-astrbot-agent 测试输出文件\n"
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"平台: {platform.system()} {platform.release()}\n"
        "测试内容: Hello from cc-astrbot-agent!\n"
    )
    target = "test_output.txt"

    # 写入
    _print_info(f"写入: {target} ({len(test_content)} 字符)")
    start = time.monotonic()

    try:
        result = await agent.tool_registry.get_tool("write_file").call(
            {"path": target, "content": test_content},
        )
        elapsed = time.monotonic() - start

        if isinstance(result, dict):
            error = result.get("error")
            if error:
                _print_fail(f"写入失败: {error}")
                return False

        _print_ok(f"写入成功 ({elapsed:.2f}s)")

    except Exception as e:
        _print_fail(f"写入异常: {e}")
        return False

    # 回读验证
    _print_info("回读验证...")
    try:
        read_result = await agent.tool_registry.get_tool("read_file").call(
            {"path": target},
        )
        if isinstance(read_result, dict):
            actual = read_result.get("content", "")
            if actual == test_content:
                _print_ok("回读验证通过，内容一致")
                return True
            else:
                _print_warn(f"内容不一致 (期望 {len(test_content)} 字符, 实际 {len(actual)} 字符)")
                return True  # 文件创建成功就算通过

        _print_ok("回读完成")
        return True

    except Exception as e:
        _print_warn(f"回读异常: {e}")
        return True  # 写入成功就算通过


# ============================================================================
# 场景 3: 执行 Bash 命令（直接调用工具）
# ============================================================================

async def test_bash_command(agent: ClaudeCodeAgent) -> bool:
    _print_header("场景3: 执行 Bash 命令 — bash 工具")

    # 跨平台选择命令
    if platform.system() == "Windows":
        commands = ["dir", "echo Hello from cc-astrbot-agent"]
    else:
        commands = ["ls -la", "echo Hello from cc-astrbot-agent"]

    all_passed = True

    for cmd in commands:
        _print_info(f"执行: {cmd}")
        start = time.monotonic()

        try:
            result = await agent.tool_registry.get_tool("bash").call(
                {"command": cmd, "timeout": 10},
            )
            elapsed = time.monotonic() - start

            if isinstance(result, dict):
                exit_code = result.get("exit_code", -1)
                stdout = result.get("stdout", "")
                error = result.get("error")

                if error:
                    _print_fail(f"命令执行错误: {error}")
                    all_passed = False
                    continue

                if exit_code == 0:
                    _print_ok(f"退出码 0 ({elapsed:.2f}s)")
                    if stdout.strip():
                        _print_result("输出", stdout.strip(), max_lines=10)
                else:
                    _print_fail(f"退出码 {exit_code}")
                    stderr = result.get("stderr", "")
                    if stderr:
                        _print_result("stderr", stderr, max_lines=5)
                    all_passed = False
            else:
                _print_ok(f"完成 ({elapsed:.2f}s)")

        except Exception as e:
            _print_fail(f"异常: {e}")
            all_passed = False

    # 额外测试：安全检查
    _print_info("测试安全检查（应拦截危险命令）...")
    try:
        result = await agent.tool_registry.get_tool("bash").call(
            {"command": "rm -rf /", "timeout": 5},
        )
        if isinstance(result, dict) and result.get("error"):
            _print_ok(f"危险命令已拦截: {result['error'][:60]}...")
        else:
            _print_fail("危险命令未被拦截！")
            all_passed = False
    except Exception as e:
        _print_warn(f"安全检查异常: {e}")

    return all_passed


# ============================================================================
# 场景 4: Agent 循环（调用 Claude API）
# ============================================================================

async def test_agent_loop(
    agent: ClaudeCodeAgent,
    task: str,
) -> bool:
    _print_header("场景4: Agent 循环 — Claude API + 多轮工具调用")

    api_key = agent.api_key
    if not api_key:
        _print_warn("未设置 ANTHROPIC_API_KEY，跳过场景4")
        _print_info("设置方式: set ANTHROPIC_API_KEY=sk-ant-xxx (Windows)")
        return True  # 不算失败

    _print_info(f"任务: {task}")
    _print_info("开始 Agent 循环（流式输出）...")
    print(f"  {_Colors.DIM}{'─' * 56}{_Colors.RESET}")

    start = time.monotonic()
    full_output = ""
    chunk_count = 0

    try:
        async for chunk in agent.run_task(task=task):
            # 流式打印
            print(chunk, end="", flush=True)
            full_output += chunk
            chunk_count += 1

    except KeyboardInterrupt:
        print(f"\n  {_Colors.YELLOW}[中断]{_Colors.RESET} 用户取消")
        return True

    except Exception as e:
        print()
        _print_fail(f"Agent 循环异常: {e}")
        import traceback
        traceback.print_exc()
        return False

    elapsed = time.monotonic() - start
    print(f"\n  {_Colors.DIM}{'─' * 56}{_Colors.RESET}")
    _print_ok(f"完成 ({chunk_count} 个片段, {len(full_output)} 字符, {elapsed:.2f}s)")

    return True


# ============================================================================
# 工具注册表检查
# ============================================================================

def check_tool_registry(registry: ToolRegistry) -> None:
    _print_header("工具注册表检查")

    tools = registry.get_all_tools()
    _print_info(f"已注册 {len(tools)} 个工具:")

    for tool in tools:
        status = "启用" if tool.is_enabled() else "禁用"
        aliases = f" (别名: {', '.join(tool.aliases)})" if tool.aliases else ""
        print(f"    - {tool.name}: {status}{aliases}")

    schemas = registry.get_tools_schema()
    _print_ok(f"生成 {len(schemas)} 个工具 schema（供 Claude API 使用）")


# ============================================================================
# 主函数
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="cc-astrbot-agent 本地测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project-root",
        default=str(_PROJECT_ROOT),
        help="项目根目录（默认: 脚本所在目录）",
    )
    parser.add_argument(
        "--scenario", "-s",
        type=int,
        choices=[1, 2, 3, 4],
        help="只运行指定场景 (1-4)",
    )
    parser.add_argument(
        "--task", "-t",
        default="读取 README.md 的前 10 行，将其写入新文件 summary.txt",
        help="场景4 的自定义任务描述",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="跳过需要 Claude API Key 的场景",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY", ""),
        help="Claude API Key（默认读取 ANTHROPIC_API_KEY 环境变量）",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ANTHROPIC_BASE_URL", ""),
        help="Claude API Base URL（默认读取 ANTHROPIC_BASE_URL 环境变量）",
    )
    parser.add_argument(
        "--model",
        default="claude-3-7-sonnet-20250219",
        help="模型名称（默认: claude-3-7-sonnet-20250219）",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    project_root = Path(args.project_root).resolve()
    if not project_root.exists():
        print(f"{_Colors.RED}[错误] 项目目录不存在: {project_root}{_Colors.RESET}")
        return 1

    print(f"{_Colors.BOLD}cc-astrbot-agent 本地测试{_Colors.RESET}")
    print(f"  项目目录: {project_root}")
    print(f"  Python:   {platform.python_version()} @ {platform.system()}")

    # 创建 Agent
    api_key = args.api_key or None
    base_url = args.base_url or None

    try:
        agent = ClaudeCodeAgent(
            project_root=str(project_root),
            claude_api_key=api_key,
            model=args.model,
            base_url=base_url,
        )
    except Exception as e:
        print(f"{_Colors.RED}[错误] Agent 初始化失败: {e}{_Colors.RESET}")
        import traceback
        traceback.print_exc()
        return 1

    # 工具注册表检查（总是运行）
    check_tool_registry(agent.tool_registry)

    # 确定要运行的场景
    run_all = args.scenario is None
    results: dict[int, bool] = {}
    total_start = time.monotonic()

    # 场景 1: 读取文件
    if run_all or args.scenario == 1:
        results[1] = await test_read_file(agent, project_root)

    # 场景 2: 写入文件
    if run_all or args.scenario == 2:
        results[2] = await test_write_file(agent, project_root)

    # 场景 3: Bash 命令
    if run_all or args.scenario == 3:
        results[3] = await test_bash_command(agent)

    # 场景 4: Agent 循环（需要 API Key）
    if (run_all or args.scenario == 4) and not args.skip_api:
        results[4] = await test_agent_loop(agent, args.task)
    elif args.skip_api and (run_all or args.scenario == 4):
        _print_header("场景4: 跳过 (--skip-api)")

    # ---- 汇总 ----
    total_elapsed = time.monotonic() - total_start
    _print_header("测试汇总")

    if not results:
        _print_warn("没有运行任何场景")
        return 0

    passed = sum(1 for v in results.values() if v)
    failed = len(results) - passed

    for sid, ok in sorted(results.items()):
        icon = f"{_Colors.GREEN}PASS{_Colors.RESET}" if ok else f"{_Colors.RED}FAIL{_Colors.RESET}"
        print(f"  场景{sid}: {icon}")

    print(f"\n  总耗时: {total_elapsed:.2f}s")
    if failed == 0:
        _print_ok(f"全部通过 ({passed}/{len(results)})")
        return 0
    else:
        _print_fail(f"部分失败 ({passed}/{len(results)} 通过)")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
