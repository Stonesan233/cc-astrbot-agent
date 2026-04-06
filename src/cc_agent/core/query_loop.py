"""
QueryLoop — Agent 的核心查询循环

对应原版 src/query.ts 的 query() + queryLoop()
无限循环: API 调用 → 解析 tool_use → 执行工具 → 结果回传 → 循环
"""

from typing import AsyncIterator, Optional
from .query_config import QueryConfig


class QueryLoop:
    """
    核心查询循环

    职责:
    1. 将消息发送给 Claude API（流式）
    2. 收到 tool_use 块后交给 ToolOrchestration 执行
    3. 将工具结果回传 API，继续循环
    4. 直到模型返回 end_turn 或达到限制
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    async def query(self, config: QueryConfig) -> AsyncIterator[str]:
        """
        主查询入口，yield 流式文本事件

        后续会逐步替换为完整实现:
        - 构建 system_prompt + messages
        - 调用 ClaudeAPIClient.stream_messages()
        - 解析 tool_use → 执行 → 回传
        """
        yield f"[QueryLoop] session={config.session_id[:8]} model={config.model}\n"
        yield "[QueryLoop] MVP 阶段，尚未接入真实 API 循环\n"
