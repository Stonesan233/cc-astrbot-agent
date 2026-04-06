"""
Message Types — 消息类型定义

对应原版 src/types/message.ts
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Union
from enum import Enum


class MessageType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    PROGRESS = "progress"
    ATTACHMENT = "attachment"
    TOOL_USE_SUMMARY = "tool_use_summary"
    TOMBSTONE = "tombstone"


@dataclass
class Message:
    """消息基类"""
    type: MessageType
    uuid: str
    timestamp: float = 0.0


@dataclass
class UserMessage(Message):
    """用户消息（含 tool_result）"""
    content: Any = None
    tool_use_result: Optional[str] = None
    source_tool_assistant_uuid: Optional[str] = None
    is_meta: bool = False


@dataclass
class AssistantMessage(Message):
    """模型回复（含 tool_use 块）"""
    content: Any = None
    api_error: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[dict] = None


@dataclass
class SystemMessage(Message):
    """系统消息"""
    content: str = ""


@dataclass
class ProgressMessage(Message):
    """进度更新"""
    data: Any = None
    tool_use_id: Optional[str] = None


@dataclass
class AttachmentMessage(Message):
    """附件消息（hook 结果、通知等）"""
    attachment: Any = None


@dataclass
class ToolUseSummaryMessage(Message):
    """工具使用摘要"""
    summary: str = ""


@dataclass
class TombstoneMessage(Message):
    """已删除消息占位符"""
    pass
