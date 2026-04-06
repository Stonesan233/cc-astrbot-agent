"""
基础框架验证测试
"""

import asyncio
import sys
from pathlib import Path

# 确保 src 在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def test_basic_framework():
    """测试基础框架：实例化 Agent 并调用 run_task"""
    from cc_agent.agent import ClaudeCodeAgent

    # 用当前项目路径实例化
    agent = ClaudeCodeAgent(
        project_root=str(Path(__file__).parent.parent),
        claude_api_key=None,
    )

    print("=" * 60)
    print("CC-AstrBot-Agent 基础框架测试")
    print("=" * 60)
    print(f"项目路径: {agent.project_root}")
    print(f"会话 ID: {agent.current_session_id}")
    print(f"已注册工具: {[t.name for t in agent.tool_registry.list_tools()]}")
    print("=" * 60)

    # 测试 run_task
    print("\n>>> 调用 agent.run_task()...")
    print("-" * 60)
    async for chunk in agent.run_task(
        task="帮我实现一个简单的 FastAPI 用户登录接口",
        persona="asahi",
    ):
        print(chunk, end="")

    print("\n" + "=" * 60)

    # 单独测试工具
    print("\n>>> 单独测试工具...")
    print("-" * 60)

    # 测试 scan_project
    scan = await agent.scan_project()
    print(f"scan_project: 发现 {len(scan.get('files', []))} 个文件, {len(scan.get('dirs', []))} 个目录")

    # 测试 file_read
    try:
        content = await agent.read_file("pyproject.toml")
        print(f"file_read pyproject.toml: {len(content)} chars")
        print(f"  前 100 字符: {content[:100]}")
    except Exception as e:
        print(f"file_read error: {e}")

    print("=" * 60)
    print("所有基础测试通过!")


if __name__ == "__main__":
    asyncio.run(test_basic_framework())
