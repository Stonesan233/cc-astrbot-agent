"""
ToolUseContext — 工具执行上下文

对应原版 src/Tool.ts 中的 ToolUseContext 类型。
在每次工具调用时提供运行时依赖：注册表、消息历史、取消信号等。

MVP 简化版：
- 不包含 MCP 客户端、权限管理器、hooks 执行器
- 不包含文件状态缓存、token 追踪
- 不包含 AppState 响应式状态
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..tools.registry import ToolRegistry


@dataclass
class ToolUseContext:
    """
    工具执行上下文（MVP 简化版）

    每轮工具调用时由 QueryLoop 构建，传递给 tool.call(args, context)。
    工具可通过 context 访问：
    - project_root: 项目根目录
    - tool_registry: 工具注册表（用于工具间协作）
    - messages: 当前对话消息历史
    - abort_event: 取消信号（外部中断时 set）
    - current_persona: 当前人格标识（仅记录）
    - on_progress: 进度回调（可选）
    """

    # ---- 核心字段 ----

    project_root: str = ""
    """项目根目录绝对路径"""

    tool_registry: Optional[ToolRegistry] = None
    """工具注册表，工具可通过它查找其他工具"""

    messages: list[dict] = field(default_factory=list)
    """当前对话的完整消息历史（Claude API 格式）"""

    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    """
    取消信号。
    外部（用户中断 / 超时）调用 abort_event.set() 后，
    工具应检查并提前退出。
    """

    current_persona: str = "default"
    """当前人格标识，仅用于日志/记录，不影响工具逻辑"""

    # ---- 可选字段 ----

    on_progress: Optional[Callable[[str], None]] = None
    """
    进度回调。
    工具可调用此函数报告执行进度（如文件读取百分比、命令输出行数）。
    """

    session_id: str = ""
    """当前会话 ID"""

    turn: int = 0
    """当前工具调用轮次（从 0 开始）"""

    extra: dict = field(default_factory=dict)
    """
    扩展字段，用于传递工具特定的上下文信息。
    避免频繁修改 ToolUseContext 的 dataclass 定义。
    """

    # ---- 便捷方法 ----

    def is_aborted(self) -> bool:
        """检查是否已被取消"""
        return self.abort_event.is_set()

    def report_progress(self, message: str) -> None:
        """报告进度（安全调用，on_progress 为 None 时忽略）"""
        if self.on_progress is not None:
            self.on_progress(message)

    def get_extra(self, key: str, default: Any = None) -> Any:
        """获取扩展字段"""
        return self.extra.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        """设置扩展字段"""
        self.extra[key] = value
