"""
AstrBot Bridge — Agent 与 AstrBot Persona 的对接接口

对应规格书第 5 节预留接口
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from dataclasses import dataclass
from enum import Enum


# ==================== 权限相关类型 ====================

class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRequest:
    """权限请求"""
    tool_name: str
    input_summary: str
    message: str
    risk_level: str = "MEDIUM"


@dataclass
class PermissionDecision:
    """权限决策"""
    behavior: PermissionBehavior
    updated_input: Optional[dict] = None
    feedback: Optional[str] = None


# ==================== 提问相关类型 ====================

@dataclass
class AskQuestionRequest:
    """用户提问请求"""
    question: str
    options: list[dict] = None


@dataclass
class QuestionResponse:
    """用户提问响应"""
    selected: str = ""
    free_text: Optional[str] = None


# ==================== 回调接口 ====================

class IPersonaCallback(ABC):
    """
    Agent 回调 AstrBot Persona 的接口

    Agent 通过这个接口将事件推送给 AstrBot 的 Persona 层
    """

    @abstractmethod
    async def on_assistant_message(self, text: str) -> None:
        """流式文本输出回调"""

    @abstractmethod
    async def on_tool_call(self, tool_name: str, input_summary: str) -> None:
        """工具调用通知"""

    @abstractmethod
    async def on_tool_result(self, tool_name: str, result_summary: str) -> None:
        """工具结果通知"""

    @abstractmethod
    async def on_permission_request(self, request: PermissionRequest) -> PermissionDecision:
        """权限请求回调（阻塞等待用户决定）"""

    @abstractmethod
    async def on_question(self, question: AskQuestionRequest) -> QuestionResponse:
        """用户提问回调"""

    @abstractmethod
    async def on_error(self, error: str) -> None:
        """错误通知"""

    @abstractmethod
    async def on_turn_complete(self, summary: str = "") -> None:
        """一轮完成通知"""


# ==================== 统一入口接口 ====================

class IAstrBotBridge(ABC):
    """
    AstrBot Persona 调用 Agent 的统一入口接口

    AstrBot 通过这个接口与 Agent 交互
    """

    @abstractmethod
    async def chat(self, user_input: str, *,
                   session_id: Optional[str] = None,
                   context: Optional[dict] = None,
                   stream: bool = True) -> AsyncIterator[str]:
        """主对话入口"""

    @abstractmethod
    async def execute_skill(self, skill_name: str, args: str = "",
                           session_id: Optional[str] = None) -> str:
        """直接调用技能"""

    @abstractmethod
    async def get_status(self) -> dict:
        """获取 Agent 状态"""

    @abstractmethod
    async def interrupt(self, reason: str = "user_interrupt") -> None:
        """中断当前执行"""

    @abstractmethod
    async def respond_permission(self, decision: PermissionDecision) -> None:
        """响应当前权限请求"""
