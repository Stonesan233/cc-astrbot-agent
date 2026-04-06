"""
ClaudeAPIClient — Anthropic API 通信层

对应原版 src/services/api/claude.ts
"""

from typing import AsyncIterator, Optional
import httpx


class ClaudeAPIClient:
    """
    Anthropic Messages API 客户端

    职责:
    1. 流式调用 Messages API
    2. 构建请求体（system, messages, tools）
    3. 处理重试和错误
    4. 追踪 token 使用量
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or "https://api.anthropic.com"
        self.model = "claude-3-7-sonnet-20250219"

    async def stream_messages(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[dict]:
        """
        流式调用 Messages API

        后续 MVP 实现将:
        - 使用 httpx 异步流式请求
        - 解析 SSE 事件
        - yield ContentBlock / ToolUseBlock / MessageStop 等事件
        """
        # MVP 阶段占位
        yield {"type": "placeholder", "message": "ClaudeAPIClient.stream_messages() not yet implemented"}
