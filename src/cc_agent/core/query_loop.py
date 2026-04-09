"""
QueryLoop — Agent 核心查询循环（MVP 简化版 + GLM 兼容模式）

对应原版 src/query.ts 的 query() + queryLoop()

MVP 阶段简化说明：
- 不实现 token budget / auto-compact / micro-compact
- 不实现 hooks（pre/post tool hooks）
- 不实现 MCP / 权限交互 / 沙箱
- 不实现 context collapse / history snip
- 最大工具调用轮次硬编码为 5

核心循环流程：
    用户 task → 构建 system prompt + messages
        → 调用 Claude API（流式 SSE）或 GLM API（OpenAI 兼容格式）
        → 收集文本输出（yield 给调用方）
        → 收集 tool_use 块（累积 input_json_delta）
        → 构建 ToolUseContext → 执行工具 → 结果塞回 messages → 继续循环
        → 模型 end_turn 或达到轮次上限 → 结束

GLM 兼容模式：
    当 model 包含 "glm" 时自动启用：
    - 使用 OpenAI 兼容格式调用 API（/v1/chat/completions）
    - 非流式请求，便于调试
    - 不强制要求 tool_use，直接返回文本也可以
    - 检测到 tool_calls 时正常执行工具循环
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import httpx

from ..services.api import ClaudeAPIClient
from ..tools.registry import ToolRegistry
from .tool_use_context import ToolUseContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认 system prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
你是一个专业的编程助手。你可以使用以下工具来完成任务：

{tool_descriptions}

使用规则：
1. 优先使用专用工具（如 read_file、write_file、bash）而非通用命令
2. 如果需要执行多个独立操作，可以在一次回复中发起多个工具调用
3. 完成任务后直接给出最终结论，不要调用不必要的工具
4. 如果信息不足，先使用工具收集信息再行动
"""


# ---------------------------------------------------------------------------
# QueryLoop
# ---------------------------------------------------------------------------

class QueryLoop:
    """
    核心查询循环（MVP 简化版 + GLM 兼容）

    职责：
    1. 将用户消息 + system prompt + tools 发送给 API
    2. 接收文本输出，yield 给上层
    3. 遇到 tool_use 块时执行对应工具
    4. 将工具结果回传 API，继续循环
    5. 直到模型 end_turn 或达到最大轮次
    """

    # 最大工具调用轮次（防无限循环），可通过 __init__ 覆盖
    MAX_TURNS = 5

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-7-sonnet-20250219",
        base_url: Optional[str] = None,
        tool_registry: Optional[ToolRegistry] = None,
        project_root: str = "",
        max_turns: int = 5,
    ):
        self.api_client = ClaudeAPIClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.project_root = project_root
        self.tool_registry = tool_registry or ToolRegistry(project_root=project_root)
        self.max_turns = max_turns

        # GLM/MiniMax 兼容模式检测
        # MiniMax API 是 OpenAI 兼容格式（/v1/chat/completions），需要走 GLM 模式
        # 如果 base_url 是 Anthropic 兼容端点（如智谱的 /api/anthropic），则使用 Claude 标准模式
        # 只有 base_url 不是 Anthropic 格式时才用 OpenAI 兼容模式
        is_anthropic_endpoint = "anthropic" in self.base_url.lower()
        _model_lower = self.model.lower()
        self._is_glm = ("glm" in _model_lower or "minimax" in _model_lower) and not is_anthropic_endpoint
        self._is_glm_anthropic = ("glm" in _model_lower or "minimax" in _model_lower) and is_anthropic_endpoint
        logger.info(
            f"QueryLoop initialized | model={self.model} | "
            f"is_glm={self._is_glm} | is_glm_anthropic={self._is_glm_anthropic} | "
            f"base_url={self.base_url}"
        )

    # ---- GLM 兼容模式：OpenAI 格式 API 调用 --------------------------------

    async def _glm_api_call(
        self,
        messages: list[dict],
        system: str,
        tools_schema: list[dict] | None = None,
    ) -> dict:
        """
        GLM 兼容模式：使用 OpenAI 兼容格式调用 API（非流式）。

        使用 /v1/chat/completions 端点，Authorization: Bearer 认证。
        不强制要求 tool_use，模型可以直接返回文本。
        """
        logger.info(f"[GLM Mode] Calling API | model={self.model} | base_url={self.base_url}")

        url = f"{self.base_url}/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # 转换消息格式为 OpenAI 兼容
        openai_messages: list[dict] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)

        body: dict = {
            "model": self.model,
            "messages": openai_messages,
            "max_tokens": 8192,
        }

        # 转换工具 schema 为 OpenAI function calling 格式
        if tools_schema:
            openai_tools = []
            for t in tools_schema:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    }
                })
            body["tools"] = openai_tools
            logger.info(f"[GLM Mode] Included {len(openai_tools)} tools (optional, not forced)")

        logger.info(f"[GLM Mode] Request URL: {url}")
        logger.info(f"[GLM Mode] Request body keys: {list(body.keys())}")
        logger.info(f"[GLM Mode] Messages count: {len(openai_messages)}")
        logger.info(
            f"[GLM Mode] First user message: "
            f"{json.dumps(openai_messages[-1], ensure_ascii=False)[:200]}"
            if openai_messages else "[no messages]"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            response = await client.post(url, json=body, headers=headers)

            logger.info(f"[GLM Mode] HTTP status: {response.status_code}")

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    f"[GLM Mode] API error | status={response.status_code} | "
                    f"body={error_text[:500]}"
                )
                return {
                    "type": "api_error",
                    "error": {
                        "status_code": response.status_code,
                        "message": error_text,
                    },
                }

            result = response.json()
            logger.info(f"[GLM Mode] Response keys: {list(result.keys())}")
            return result

    def _parse_glm_response(self, response: dict) -> tuple[str, list[dict]]:
        """
        解析 GLM (OpenAI 兼容格式) 响应。

        Returns:
            (text_content, tool_use_blocks)
            - text_content: 模型返回的文本
            - tool_use_blocks: 标准化的 tool_use 块列表（可能为空）
        """
        text_content = ""
        tool_use_blocks: list[dict] = []

        choices = response.get("choices", [])
        if not choices:
            logger.warning("[GLM Mode] No choices in response")
            return text_content, tool_use_blocks

        choice = choices[0]
        message = choice.get("message", {})

        # 提取文本内容
        text_content = message.get("content", "") or ""
        logger.info(f"[GLM Mode] Text content length: {len(text_content)}")
        if text_content:
            logger.info(f"[GLM Mode] Text preview: {text_content[:200]}")

        # 提取 tool_calls（OpenAI 格式）
        raw_tool_calls = message.get("tool_calls", [])
        logger.info(f"[GLM Mode] tool_calls count: {len(raw_tool_calls)}")

        for tc in raw_tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            tool_args_str = func.get("arguments", "{}")

            try:
                tool_input = json.loads(tool_args_str) if tool_args_str else {}
            except json.JSONDecodeError:
                logger.warning(
                    f"[GLM Mode] Failed to parse tool args JSON: {tool_args_str[:200]}"
                )
                tool_input = {"_raw": tool_args_str}

            tool_use_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", str(uuid.uuid4())),
                "name": tool_name,
                "input": tool_input,
            })
            logger.info(
                f"[GLM Mode] Parsed tool_call: name={tool_name} | "
                f"args_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else '?'}"
            )

        finish_reason = choice.get("finish_reason", "")
        logger.info(
            f"[GLM Mode] Parsed response | text_len={len(text_content)} | "
            f"tool_count={len(tool_use_blocks)} | finish_reason={finish_reason}"
        )

        return text_content, tool_use_blocks

    # ---- GLM 兼容模式的查询循环 -------------------------------------------

    async def _query_glm(
        self,
        task: str,
        system_prompt: str,
        tools_schema: list[dict],
        messages: list[dict],
        abort_event: asyncio.Event,
        persona: str = "default",
    ) -> AsyncIterator[str]:
        """
        GLM 兼容模式的查询循环。

        使用 OpenAI 兼容格式调用 API，非流式。
        支持 tool_calls 多轮循环，也支持纯文本回复直接结束。
        """
        logger.info(f"[GLM Mode] _query_glm started | task={task[:100]}")

        for turn in range(self.max_turns):
            logger.info(
                f"[GLM Mode] === Turn {turn + 1}/{self.max_turns} ==="
            )

            # ---- 调用 API ----
            logger.info(f"Calling GLM API with model: {self.model}")
            response = await self._glm_api_call(
                messages=messages,
                system=system_prompt,
                tools_schema=tools_schema,
            )

            # ---- 处理 API 错误 ----
            if response.get("type") == "api_error":
                error = response.get("error", {})
                msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                logger.error(f"[GLM Mode] API error on turn {turn + 1}: {msg[:300]}")
                yield f"\n[API 错误] {msg}\n"
                return

            # ---- 解析响应 ----
            text_content, tool_use_blocks = self._parse_glm_response(response)

            logger.info(
                f"Received response, has tool_use: {len(tool_use_blocks) > 0} | "
                f"text_len={len(text_content)} | tool_count={len(tool_use_blocks)}"
            )

            # ---- yield 文本内容 ----
            if text_content:
                logger.info(f"[GLM Mode] Yielding text: {text_content[:100]}...")
                yield text_content

            # ---- 没有工具调用 → 正常结束 ----
            if not tool_use_blocks:
                logger.info(
                    "[GLM Mode] No tool_use blocks, returning text reply as final output"
                )
                if not text_content:
                    logger.warning("[GLM Mode] No text and no tool_use - empty response")
                    yield "（模型未返回任何内容）"
                return

            # ---- 有工具调用 → 执行工具循环 ----
            logger.info(f"[GLM Mode] Executing {len(tool_use_blocks)} tool call(s)")

            # 构建助手消息（OpenAI 格式：含 tool_calls）
            assistant_msg: dict = {
                "role": "assistant",
                "content": text_content or None,
            }
            # 转换 tool_use_blocks 回 OpenAI 格式的 tool_calls
            openai_tool_calls = []
            for block in tool_use_blocks:
                openai_tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(
                            block.get("input", {}),
                            ensure_ascii=False,
                        ),
                    }
                })
            assistant_msg["tool_calls"] = openai_tool_calls
            # 有些 OpenAI 兼容 API 需要 tool_call_id 字段
            if tool_use_blocks:
                assistant_msg.setdefault("tool_call_id", tool_use_blocks[0]["id"])

            messages.append(assistant_msg)

            # 构建 ToolUseContext
            context = ToolUseContext(
                project_root=self.project_root,
                tool_registry=self.tool_registry,
                messages=list(messages),
                abort_event=abort_event,
                current_persona=persona,
                on_progress=lambda msg: logger.debug("[tool progress] %s", msg),
                turn=turn,
            )

            # ---- 执行每个工具 ----
            for block in tool_use_blocks:
                tool_name = block["name"]
                tool_input = block.get("input", {})
                tool_use_id = block.get("id", str(uuid.uuid4()))

                logger.info(
                    f"Executing tool: {tool_name} with args: "
                    f"{json.dumps(tool_input, ensure_ascii=False)[:200]}"
                )

                result_str = await self._execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    context=context,
                )

                logger.info(
                    f"Tool {tool_name} finished with result: "
                    f"{result_str[:300]}... | len={len(result_str)}"
                )

                # 将工具结果追加为 tool role 消息（OpenAI 格式）
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": result_str,
                })

                # 检查取消信号
                if abort_event.is_set():
                    yield "\n[工具执行已被取消]\n"
                    return

            # 继续下一轮（让模型看到工具结果）
            logger.info("[GLM Mode] Tool execution round complete, continuing to next turn")

        # 达到最大轮次
        yield "\n[已达到最大工具调用轮次限制，自动停止]\n"

    # ---- 主入口 ------------------------------------------------------------

    async def query(
        self,
        task: str,
        persona: str = "default",
    ) -> AsyncIterator[str]:
        """
        主查询入口，yield 流式文本片段。

        完整的多轮工具调用循环在这里实现。
        persona 参数仅用于日志记录，不影响任何逻辑。

        Args:
            task: 用户任务描述
            persona: 人格标识（仅记录，不影响逻辑）

        Yields:
            str: 模型输出的文本片段
        """
        logger.info(f"QueryLoop started with task: {task[:100]}")
        logger.info(
            f"QueryLoop config | model={self.model} | "
            f"base_url={self.base_url} | is_glm={self._is_glm} | "
            f"is_glm_anthropic={self._is_glm_anthropic} | persona={persona}"
        )

        system_prompt = self._build_system_prompt()
        tools_schema = self.tool_registry.get_tools_schema()

        logger.info(
            f"QueryLoop prepared | tools_count={len(tools_schema)} | "
            f"tool_names={sorted(t['name'] for t in tools_schema)}"
        )

        messages: list[dict] = [
            {"role": "user", "content": task},
        ]

        # 构建 cancel 信号
        abort_event = asyncio.Event()

        # ---- GLM OpenAI 兼容模式（base_url 非 Anthropic 端点） ----
        if self._is_glm:
            logger.info("Entering GLM OpenAI compatibility mode")
            async for chunk in self._query_glm(
                task=task,
                system_prompt=system_prompt,
                tools_schema=tools_schema,
                messages=messages,
                abort_event=abort_event,
                persona=persona,
            ):
                yield chunk
            logger.info("QueryLoop (GLM OpenAI mode) finished")
            return

        # ---- Claude 标准模式 / GLM-Anthropic 兼容模式 ----
        # 两者都走 Anthropic API 格式（流式 SSE）
        mode_label = "GLM-Anthropic" if self._is_glm_anthropic else "Claude"
        logger.info(f"Entering {mode_label} standard mode (Anthropic API format)")

        # ---- 多轮工具调用循环 ----
        for turn in range(self.max_turns):
            # 本轮收集的状态
            text_parts: list[str] = []
            tool_use_blocks: list[dict] = []
            stop_reason = ""
            has_error = False

            # 用于累积正在接收的 tool_use 块
            current_tool_id = ""
            current_tool_name = ""
            current_tool_json_parts: list[str] = []

            logger.info(
                f"Calling Claude API with model: {self.model} | "
                f"turn={turn + 1}/{self.max_turns}"
            )

            # ---- 流式调用 API，逐事件处理 ----
            async for event in self.api_client.stream_messages(
                system=system_prompt,
                messages=messages,
                tools=tools_schema,
                model=self.model,
            ):
                event_type = event.get("type", "")

                # -- API 错误 --
                if event_type == "api_error":
                    error = event.get("error", {})
                    msg = (
                        error.get("message", str(error))
                        if isinstance(error, dict)
                        else str(error)
                    )
                    logger.error(f"Received API error: {msg[:300]}")
                    yield f"\n[API 错误] {msg}\n"
                    has_error = True
                    break

                # -- 文本增量 --
                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            text_parts.append(text)
                            yield text

                    elif delta_type == "input_json_delta":
                        partial = delta.get("partial_json", "")
                        if partial:
                            current_tool_json_parts.append(partial)

                # -- 内容块开始 --
                elif event_type == "content_block_start":
                    content_block = event.get("content_block", {})
                    if content_block.get("type") == "tool_use":
                        current_tool_id = content_block.get("id", "")
                        current_tool_name = content_block.get("name", "")
                        current_tool_json_parts = []
                        logger.info(
                            f"Received tool_use block start: "
                            f"name={current_tool_name} id={current_tool_id}"
                        )

                # -- 内容块结束 --
                elif event_type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        raw_json = "".join(current_tool_json_parts)
                        try:
                            tool_input = json.loads(raw_json) if raw_json else {}
                        except json.JSONDecodeError:
                            tool_input = {"_raw": raw_json}

                        tool_use_blocks.append({
                            "type": "tool_use",
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "input": tool_input,
                        })
                        logger.info(
                            f"Received tool_use block complete: "
                            f"name={current_tool_name} | "
                            f"input_keys={list(tool_input.keys()) if isinstance(tool_input, dict) else '?'}"
                        )
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_json_parts = []

                # -- 消息增量 --
                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    stop_reason = delta.get("stop_reason", "")

            # ---- 处理本轮结果 ----
            if has_error:
                logger.warning(f"Turn {turn + 1} aborted due to API error")
                return

            has_tool_use = len(tool_use_blocks) > 0
            text_total = "".join(text_parts)
            logger.info(
                f"Received response, has tool_use: {has_tool_use} | "
                f"stop_reason={stop_reason} | "
                f"text_len={len(text_total)} | tool_count={len(tool_use_blocks)}"
            )

            # 没有工具调用 → 正常结束
            if not tool_use_blocks:
                if not text_parts:
                    logger.warning(
                        "No text output and no tool_use blocks - check API response"
                    )
                    yield "（模型未返回任何内容）"
                else:
                    logger.info(
                        f"No tool_use blocks, returning text only | len={len(text_total)}"
                    )
                return

            # ---- 构建助手消息 ----
            assistant_content: list[dict] = []
            if text_parts:
                assistant_content.append({
                    "type": "text",
                    "text": "".join(text_parts),
                })
            assistant_content.extend(tool_use_blocks)
            messages.append({"role": "assistant", "content": assistant_content})

            # ---- 构建 ToolUseContext ----
            context = ToolUseContext(
                project_root=self.project_root,
                tool_registry=self.tool_registry,
                messages=list(messages),
                abort_event=abort_event,
                current_persona=persona,
                on_progress=lambda msg: logger.debug("[tool progress] %s", msg),
                turn=turn,
            )

            # ---- 执行工具 ----
            tool_results: list[dict] = []
            for block in tool_use_blocks:
                tool_name = block["name"]
                tool_input = block.get("input", {})
                tool_use_id = block.get("id", str(uuid.uuid4()))

                logger.info(
                    f"Executing tool: {tool_name} with args: "
                    f"{json.dumps(tool_input, ensure_ascii=False)[:200]}"
                )

                result_str = await self._execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    context=context,
                )

                logger.info(
                    f"Tool {tool_name} finished with result: "
                    f"{result_str[:300]}... | len={len(result_str)}"
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_str,
                })

                if abort_event.is_set():
                    yield "\n[工具执行已被取消]\n"
                    return

            # 将工具结果作为 user 消息追加
            messages.append({"role": "user", "content": tool_results})

            if stop_reason == "end_turn":
                continue

        # 达到最大轮次
        yield "\n[已达到最大工具调用轮次限制，自动停止]\n"

    # ---- 内部方法 ----------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """构建 system prompt，包含可用工具描述"""
        descriptions: list[str] = []
        for tool in self.tool_registry.get_all_tools():
            if tool.is_enabled():
                doc = (tool.__class__.__doc__ or tool.name).strip()
                descriptions.append(f"- {tool.name}: {doc}")

        return _SYSTEM_PROMPT_TEMPLATE.format(
            tool_descriptions="\n".join(descriptions),
        )

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        context: ToolUseContext,
    ) -> str:
        """
        执行单个工具并返回结果字符串。

        流程：
        1. 通过 ToolRegistry 查找工具
        2. 输入校验（validate_input）
        3. 调用 tool.call(args, context)
        4. 格式化结果（处理 error / stdout / dict）

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数（已从 SSE 累积解析）
            context: 工具执行上下文

        Returns:
            str: 工具执行结果字符串
        """
        logger.info(
            f"_execute_tool called | tool={tool_name} | "
            f"args={json.dumps(tool_input, ensure_ascii=False)[:300]}"
        )

        # ---- 1. 查找工具 ----
        tool = self.tool_registry.find_tool(tool_name)
        if tool is None:
            available = sorted(t.name for t in self.tool_registry.get_all_tools())
            logger.warning(
                f"Tool not found: {tool_name} | available: {available}"
            )
            return f"Error: tool '{tool_name}' not registered. Available: {available}"

        logger.info(f"Tool found: {tool_name} | type={type(tool).__name__}")

        # ---- 2. 输入校验 ----
        try:
            validation = await tool.validate_input(tool_input, context)
            if not validation.result:
                msg = validation.message or "输入校验失败"
                logger.warning(f"工具 {tool_name} 输入校验失败: {msg}")
                return f"输入校验失败: {msg}"
        except Exception as e:
            logger.warning(f"工具 {tool_name} 输入校验异常（跳过校验）: {e}")

        # ---- 3. 执行工具 ----
        logger.info(f"Calling tool.call() | tool={tool_name}")
        try:
            result = await tool.call(
                args=tool_input,
                context=context,
                on_progress=context.on_progress,
            )
            logger.info(
                f"tool.call() returned | tool={tool_name} | "
                f"result_type={type(result).__name__}"
            )
        except asyncio.CancelledError:
            logger.info(f"工具 {tool_name} 被取消")
            return "工具执行已被取消"
        except Exception as e:
            logger.exception(f"工具 {tool_name} 执行异常: {e}")
            return f"工具执行异常: {e}"

        # ---- 4. 格式化结果 ----
        formatted = self._format_tool_result(tool_name, result)
        logger.info(
            f"Tool {tool_name} finished with result: "
            f"{formatted[:300]}... | len={len(formatted)}"
        )
        return formatted

    @staticmethod
    def _format_tool_result(tool_name: str, result) -> str:
        """
        将工具返回值格式化为字符串。

        tool.call() 返回 dict，可能包含 error / stdout 等字段。
        """
        if not isinstance(result, dict):
            return str(result)

        # 有 error 字段 → 失败
        error = result.get("error")
        if error:
            return f"工具执行失败: {error}"

        # 有 stdout 字段 → 优先返回 stdout
        stdout = result.get("stdout", "")
        if stdout:
            # 截断超长输出
            max_len = 50_000
            if len(stdout) > max_len:
                truncated = stdout[:max_len]
                return truncated + f"\n... (输出已截断，原始长度 {len(stdout)} 字符)"
            return stdout

        # 否则返回完整 JSON
        return json.dumps(result, ensure_ascii=False, indent=2)
