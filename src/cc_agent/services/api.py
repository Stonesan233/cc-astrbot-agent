"""
ClaudeAPIClient — Anthropic Messages API 流式客户端

MVP 简化版：
- 使用 httpx 异步流式请求
- 解析 SSE (Server-Sent Events) 事件
- yield 结构化事件（text / tool_use / message_stop）
- 不包含重试逻辑、token 追踪等高级功能

对应原版 src/services/api/claude.ts
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import httpx


# ---------------------------------------------------------------------------
# SSE 解析
# ---------------------------------------------------------------------------

async def _parse_sse_stream(lines: AsyncIterator[str]) -> AsyncIterator[dict]:
    """
    解析 SSE 文本流，yield 解析后的事件 dict。

    SSE 格式：
        event: message_start
        data: {"type":"message_start",...}

        event: content_block_delta
        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"},...}

    空行分隔事件。
    """
    current_event = ""
    current_data = ""

    async for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")

        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data = line[len("data:"):].strip()
        elif line == "":
            # 空行 = 事件边界
            if current_data:
                try:
                    parsed = json.loads(current_data)
                    yield parsed
                except json.JSONDecodeError:
                    pass
            current_event = ""
            current_data = ""


# ---------------------------------------------------------------------------
# ClaudeAPIClient
# ---------------------------------------------------------------------------

class ClaudeAPIClient:
    """
    Anthropic Messages API 客户端（MVP 简化版）

    职责：
    1. 流式调用 Messages API
    2. 解析 SSE 事件流
    3. yield 结构化事件给 QueryLoop 消费
    """

    BASE_URL = "https://api.anthropic.com"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-3-7-sonnet-20250219",
    ):
        self.api_key = api_key
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.model = model

    # ---- 公开接口 ----------------------------------------------------------

    async def stream_messages(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[dict]:
        """
        流式调用 Anthropic Messages API，yield SSE 事件。

        Args:
            system: system prompt 文本
            messages: 消息列表（Claude API 格式）
            tools: 工具 schema 列表
            model: 模型名（覆盖默认）
            max_tokens: 最大输出 token 数

        Yields:
            dict: API 返回的每个 SSE 事件（已解析 JSON）
        """
        model = model or self.model

        url = f"{self.base_url}/v1/messages"

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "stream": True,
            "system": system,
            "messages": messages,
        }

        # 只在有工具时才传 tools 参数
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode(errors="replace")
                    yield {
                        "type": "api_error",
                        "error": {
                            "status_code": response.status_code,
                            "message": error_text,
                        },
                    }
                    return

                # 解析 SSE 流
                async for event in _parse_sse_stream(response.aiter_lines()):
                    yield event

    async def send_messages(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 8192,
    ) -> dict:
        """
        非流式调用（用于调试或简单场景）。

        Returns:
            dict: 完整的 API 响应
        """
        model = model or self.model

        url = f"{self.base_url}/v1/messages"

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "stream": False,
            "system": system,
            "messages": messages,
        }

        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            response = await client.post(url, json=body, headers=headers)
            if response.status_code != 200:
                return {
                    "type": "api_error",
                    "error": {
                        "status_code": response.status_code,
                        "message": response.text,
                    },
                }
            return response.json()
