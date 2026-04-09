"""
ClaudeCodeAgent — 纯净的 Coding Agent 核心
不包含任何人格逻辑，只提供功能。由 AstrBot Persona 层调用。
"""

from typing import AsyncIterator, Optional
from pathlib import Path
import uuid

from .core.query_loop import QueryLoop
from .bridge.astrbot_bridge import IAstrBotBridge, IPersonaCallback
from .tools.registry import ToolRegistry
from .tools.base import ToolResult


class ClaudeCodeAgent:
    """
    纯净的 Coding Agent 核心
    不包含任何人格逻辑，只提供功能
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

    # ---- 主入口 ------------------------------------------------------------

    async def run_task(
        self,
        task: str,
        persona: str = "default",
        stream: bool = True,
        callback: Optional[IPersonaCallback] = None,
    ) -> AsyncIterator[str]:
        """
        AstrBot Persona 调用 Agent 的统一入口。
        persona 参数仅用于记录，不影响 Agent 内部逻辑。

        流式 yield 模型的文本输出（包含多轮工具调用）。
        """
        async for chunk in self.query_loop.query(task=task, persona=persona):
            yield chunk

    # ---- 预留接口（供 AstrBot 直接调用工具） --------------------------------

    async def scan_project(self) -> dict:
        """供 AstrBot 直接调用"""
        tool = self.tool_registry.get_tool("scan_project")
        return await tool.call({"path": "."})

    async def read_file(self, file_path: str) -> str:
        tool = self.tool_registry.get_tool("read_file")
        result = await tool.call({"path": file_path})
        if isinstance(result, ToolResult):
            return (result.data or {}).get("content", "") if result.error is None else f"Error: {result.error}"
        return result.get("content", "")

    async def write_file(self, file_path: str, content: str) -> dict:
        tool = self.tool_registry.get_tool("write_file")
        return await tool.call({"path": file_path, "content": content})

    async def execute_command(self, command: str, timeout: int = 60) -> dict:
        tool = self.tool_registry.get_tool("bash")
        return await tool.call({"command": command, "timeout": timeout})
