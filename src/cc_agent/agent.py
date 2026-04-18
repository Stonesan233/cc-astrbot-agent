"""
ClaudeCodeAgent — 纯净的 Coding Agent 核心

不包含任何人格逻辑，只提供多轮工具调用能力。
人格设定由上层（AstrBot Persona 或其他插件）负责。

对应原版 Claude Code 的核心 Agent 入口。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from .core.query_loop import QueryLoop
from .bridge.astrbot_bridge import IAstrBotBridge, IPersonaCallback
from .tools.registry import ToolRegistry
from .tools.base import ToolResult


class ClaudeCodeAgent:
    """
    纯净的 Coding Agent 核心

    职责：
    - 管理 QueryLoop 和 ToolRegistry 的生命周期
    - 提供流式任务执行入口 (run_task)
    - 提供直接工具调用接口 (scan_project / read_file / write_file / execute_command)

    不包含任何 Persona 逻辑。
    """

    def __init__(
        self,
        project_root: str = "/app/project",
        claude_api_key: Optional[str] = None,
        model: str = "claude-3-7-sonnet-20250219",
        base_url: Optional[str] = None,
        max_turns: int = 5,
    ):
        self.project_root = Path(project_root).resolve()
        self.api_key = claude_api_key
        self.model = model

        # 共享的工具注册表
        self.tool_registry = ToolRegistry(project_root=str(self.project_root))

        # 核心查询循环
        self.query_loop = QueryLoop(
            api_key=self.api_key,
            model=self.model,
            base_url=base_url,
            tool_registry=self.tool_registry,
            project_root=str(self.project_root),
            max_turns=max_turns,
        )

        self.current_session_id = str(uuid.uuid4())

    # ---- 主入口 --------------------------------------------------------

    async def run_task(
        self,
        task: str,
        persona: str = "default",
        stream: bool = True,
        callback: Optional[IPersonaCallback] = None,
    ) -> AsyncIterator[str]:
        """
        流式任务执行入口。

        persona 参数仅用于日志标记，不影响 Agent 行为。
        yield 模型的文本输出（包含多轮工具调用的全部文本）。
        """
        async for chunk in self.query_loop.query(task=task, persona=persona):
            yield chunk

    # ---- 直接工具调用接口（绕过 LLM） ------------------------------------

    async def scan_project(self, **kwargs) -> dict:
        """直接调用项目扫描工具"""
        tool = self.tool_registry.get_tool("scan_project")
        return await tool.call({"path": ".", **kwargs})

    async def read_file(self, file_path: str, **kwargs) -> str:
        """直接调用文件读取工具"""
        tool = self.tool_registry.get_tool("read_file")
        result = await tool.call({"path": file_path, **kwargs})
        if isinstance(result, ToolResult):
            return (
                (result.data or {}).get("content", "")
                if result.error is None
                else f"Error: {result.error}"
            )
        return result.get("content", "")

    async def write_file(self, file_path: str, content: str, **kwargs) -> dict:
        """直接调用文件写入工具"""
        tool = self.tool_registry.get_tool("write_file")
        return await tool.call({"path": file_path, "content": content, **kwargs})

    async def execute_command(self, command: str, timeout: int = 60, **kwargs) -> dict:
        """直接调用 Bash 工具"""
        tool = self.tool_registry.get_tool("bash")
        return await tool.call({"command": command, "timeout": timeout, **kwargs})
