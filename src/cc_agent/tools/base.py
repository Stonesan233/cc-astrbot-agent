"""
BaseTool — 所有工具的抽象基类

对应原版 src/Tool.ts 的 Tool<Input, Output, Progress> 类型
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Callable
from pydantic import BaseModel


class ToolResult(BaseModel):
    """工具执行结果"""
    data: Any = None
    error: Optional[str] = None
    new_messages: list = []

    model_config = {"arbitrary_types_allowed": True}


class ValidationResult(BaseModel):
    """输入校验结果"""
    result: bool
    message: Optional[str] = None
    error_code: int = 0


class BaseTool(ABC):
    """
    工具基类，对应原版 Tool<Input, Output, Progress>

    每个工具必须实现:
    - name: 工具名称
    - input_schema: Pydantic 输入模型类
    - call(): 执行逻辑
    - description(): 工具描述
    - prompt(): 完整 prompt 模板
    """

    name: str = ""
    aliases: list[str] = []
    search_hint: str = ""

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """Pydantic 输入模型类"""

    @abstractmethod
    async def call(
        self,
        args: dict,
        context: Any = None,
        on_progress: Optional[Callable] = None,
    ) -> dict:
        """
        执行工具

        Args:
            args: 工具输入参数（已通过 input_schema 校验）
            context: ToolUseContext
            on_progress: 进度回调

        Returns:
            dict: 工具结果
        """

    @abstractmethod
    async def description(self, input_data: dict, options: dict = None) -> str:
        """返回工具描述文本"""

    @abstractmethod
    async def prompt(self, options: dict = None) -> str:
        """返回完整工具 prompt 模板"""

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """是否可以与其他工具并发执行（默认否）"""
        return False

    def is_read_only(self, input_data: dict) -> bool:
        """是否只读（默认否）"""
        return False

    def is_enabled(self) -> bool:
        """是否启用（默认是）"""
        return True

    async def validate_input(
        self, input_data: dict, context: Any = None
    ) -> ValidationResult:
        """校验输入（默认通过）"""
        return ValidationResult(result=True)

    def user_facing_name(self, input_data: Optional[dict] = None) -> str:
        """用户可见的工具名称"""
        return self.name

    def get_brief_description(self) -> str:
        """获取工具简短描述（用于 system prompt 工具列表）"""
        return (self.__class__.__doc__ or self.name).strip().split("\n")[0]

    def map_tool_result_to_block(self, content: Any, tool_use_id: str) -> dict:
        """将工具结果映射为 API tool_result block"""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(content) if content is not None else "",
        }
