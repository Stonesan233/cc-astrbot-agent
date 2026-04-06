"""
ClaudeCodeAgent — 纯净的 Coding Agent 核心
不包含任何人格逻辑，只提供功能。由 AstrBot Persona 层调用。
"""

from typing import Dict, Any, AsyncIterator, Optional
from pathlib import Path
import uuid

from .core.query_loop import QueryLoop
from .core.query_config import QueryConfig
from .bridge.astrbot_bridge import IAstrBotBridge, IPersonaCallback
from .tools.registry import ToolRegistry
from .tools.base import ToolResult


class ClaudeCodeAgent:
    """
    纯净的 Coding Agent 核心
    不包含任何人格逻辑，只提供功能
    """

    def __init__(self, project_root: str = "/app/project", claude_api_key: Optional[str] = None):
        self.project_root = Path(project_root).resolve()
        self.api_key = claude_api_key
        self.query_loop = QueryLoop(self.api_key)
        self.tool_registry = ToolRegistry(project_root=str(self.project_root))
        self.current_session_id = str(uuid.uuid4())

    async def run_task(
        self,
        task: str,
        persona: str = "default",
        stream: bool = True,
        callback: Optional[IPersonaCallback] = None,
    ) -> AsyncIterator[str]:
        """
        AstrBot Persona 调用 Agent 的统一入口
        persona 参数仅用于记录，不影响 Agent 内部逻辑
        """
        session_id = self.current_session_id

        # 构建 Query 参数（后续会逐步完善）
        config = QueryConfig(
            session_id=session_id,
            model="claude-3-7-sonnet-20250219",  # 可配置
            max_tokens=8192,
            project_root=str(self.project_root),
        )

        # 这里是 MVP 阶段的简化版循环，后续替换为完整 QueryLoop
        yield f"[Agent 开始处理任务] persona={persona} | session={session_id[:8]}...\n"
        yield f"项目路径: {self.project_root}\n\n"

        # 模拟第一步：扫描项目
        scan_result = await self.tool_registry.get_tool("scan_project").call({"path": "."})
        yield f"项目扫描完成，发现 {len(scan_result.get('files', []))} 个文件。\n"

        # TODO: 后续接入完整工具循环 + Claude API 调用
        yield "正在思考最佳实现方案...\n"

        # 占位返回（后续替换为真实工具执行结果）
        yield "任务处理完成（当前为框架占位阶段）。\n"
        yield "朝日娘 / 露娜大人可在 AstrBot 层面基于此结果生成最终回复。\n"

    # ==================== 预留接口（方便 AstrBot 插件调用） ====================

    async def scan_project(self) -> Dict:
        """供 AstrBot 直接调用"""
        tool = self.tool_registry.get_tool("scan_project")
        return await tool.call({"path": "."})

    async def read_file(self, file_path: str) -> str:
        tool = self.tool_registry.get_tool("read_file")
        result = await tool.call({"path": file_path})
        if isinstance(result, ToolResult):
            return (result.data or {}).get("content", "") if result.error is None else f"Error: {result.error}"
        return result.get("content", "")

    # ... 后续会继续添加 write_file, execute_command 等
