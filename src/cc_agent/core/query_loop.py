"""
QueryLoop — Agent 核心查询循环

对应原版 src/query.ts 的 query() + queryLoop()

核心循环流程：
    用户 task → 构建 system prompt + messages
        → 调用 API（Claude SSE 流式 / GLM OpenAI 兼容）
        → 收集文本输出（yield 给调用方）
        → 收集 tool_use 块 → 执行工具 → 结果回传 → 继续循环
        → 模型 end_turn 或达到轮次上限 → 结束

GLM-5.1 专项优化：
    - 更强的 system prompt，明确要求使用工具而非只回复确认语
    - Fallback 机制：连续 2 轮无 tool_use 时，强制输出 Markdown 总结
    - 工具列表显式嵌入 system prompt，引导模型选择正确工具
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
# System Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BASE = """\
你是一个专业的编程助手（Coding Agent）。你可以使用工具来完成任务。

## 核心规则

1. **必须使用工具获取信息**：不要凭记忆或猜测回答问题。必须先调用相应工具（如 scan_project、read_file）获取最新项目信息，再进行分析和回答。
2. **先思考再行动**：收到任务后，先分析需要哪些信息，然后按步骤调用工具获取。
3. **不要只回复确认语**：禁止回复"好的，让我先…"、"我会…"等确认性文字而不实际调用工具。每次回复都必须包含工具调用或最终结论。
4. **使用专用工具**：优先使用专用工具（如 read_file、write_file、bash）而非通用命令。
5. **多步骤任务**：将复杂任务分解为多个工具调用步骤，每步收集必要信息。
6. **最终输出**：完成所有工具调用后，输出完整的 Markdown 格式结果。

## 可用工具

{tool_descriptions}

## 工具使用说明

- `scan_project(path, max_depth)` — 扫描项目目录结构，返回文件列表。审查项目时**第一步就调用**此工具。
- `read_file(path, offset, limit)` — 读取文件内容，支持指定行范围。查看代码时必须使用此工具。
- `write_file(path, content)` — 写入文件。
- `edit_file(path, old_string, new_string)` — 精确替换文件中的字符串。
- `bash(command, timeout)` — 执行 shell 命令。
- `generate_patch(files)` — 生成 unified diff 补丁。
- `glob(pattern, path)` — 按模式匹配文件路径。
- `grep(pattern, path, output_mode)` — 搜索文件内容。
"""

# GLM-5.1 专用增强 prompt
_GLM_EXTRA_PROMPT = """

## 重要提示（当前模型需要特别注意）

你**必须**使用 function calling（tool_calls）来调用工具。操作流程：

1. 收到任务后，**立即**调用 scan_project 工具扫描项目结构
2. 根据扫描结果，调用 read_file 工具读取关键文件
3. 完成信息收集后，输出完整的分析报告

**绝对不要**只回复"好的"、"我来分析一下"等确认语。
**每次回复要么包含 tool_calls，要么是最终结论。**
如果你已经获取了足够的信息，请直接输出最终结论，不要再确认。
"""


# ---------------------------------------------------------------------------
# Fallback 提示词（连续无工具调用时注入）
# ---------------------------------------------------------------------------

_FALLBACK_PROMPT_TURN_1 = (
    "你刚才的回复没有调用任何工具。请立即使用工具来执行任务。"
    "例如：调用 scan_project 扫描项目、read_file 读取文件、bash 执行命令。"
    "不要回复确认语，直接发起 tool_call。"
)

_FALLBACK_PROMPT_FORCE_MD = (
    "你已经连续多次没有调用工具。请现在根据已掌握的信息，"
    "直接输出一份完整的 Markdown 格式报告。"
    "不要再回复确认语，直接给出最终结果。"
)


# ---------------------------------------------------------------------------
# QueryLoop
# ---------------------------------------------------------------------------

class QueryLoop:
    """
    核心查询循环

    职责：
    1. 将用户消息 + system prompt + tools 发送给 API
    2. 接收文本输出，yield 给上层
    3. 遇到 tool_use 块时执行对应工具
    4. 将工具结果回传 API，继续循环
    5. 直到模型 end_turn 或达到最大轮次

    GLM-5.1 优化：
    - 连续无 tool_use 轮次追踪 + fallback 注入
    - 更强的 system prompt 引导
    """

    # 最大工具调用轮次（防无限循环）
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

        # ---- 模型类型检测 ----
        # GLM/MiniMax 兼容模式：使用 OpenAI /v1/chat/completions 端点
        # 但如果 base_url 含 "anthropic"，说明是 Anthropic 兼容代理，走 Claude 标准模式
        is_anthropic_endpoint = "anthropic" in self.base_url.lower()
        _model_lower = self.model.lower()
        self._is_glm = (
            ("glm" in _model_lower or "minimax" in _model_lower)
            and not is_anthropic_endpoint
        )
        self._is_glm_anthropic = (
            ("glm" in _model_lower or "minimax" in _model_lower)
            and is_anthropic_endpoint
        )

        logger.info(
            f"QueryLoop initialized | model={self.model} | "
            f"is_glm={self._is_glm} | is_glm_anthropic={self._is_glm_anthropic} | "
            f"base_url={self.base_url} | max_turns={self.max_turns}"
        )

    # ==================================================================
    # System Prompt 构建
    # ==================================================================

    def _build_system_prompt(self) -> str:
        """
        构建 system prompt。

        - 基础 prompt 包含工具列表和使用规则
        - GLM 模式追加额外的强制工具使用提示
        """
        # 构建工具描述列表
        tool_lines: list[str] = []
        for tool in self.tool_registry.get_all_tools():
            if tool.is_enabled():
                brief = tool.get_brief_description()
                # 获取工具的 input_schema 信息
                schema = tool.input_schema.model_json_schema()
                properties = schema.get("properties", {})
                param_names = list(properties.keys())
                param_desc = ", ".join(param_names) if param_names else "无参数"
                tool_lines.append(f"- `{tool.name}({param_desc})` — {brief}")

        prompt = _SYSTEM_PROMPT_BASE.format(
            tool_descriptions="\n".join(tool_lines),
        )

        # GLM 模式追加增强提示
        if self._is_glm:
            prompt += _GLM_EXTRA_PROMPT

        return prompt

    # ==================================================================
    # GLM 兼容模式：OpenAI 格式 API 调用
    # ==================================================================

    async def _glm_api_call(
        self,
        messages: list[dict],
        system: str,
        tools_schema: list[dict] | None = None,
    ) -> dict:
        """
        GLM 兼容模式：使用 OpenAI 兼容格式调用 API（非流式）。

        使用 /v1/chat/completions 端点，Bearer 认证。
        """
        logger.info(
            f"[GLM] API call | model={self.model} | "
            f"messages={len(messages)} | tools={len(tools_schema) if tools_schema else 0}"
        )

        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # 转换为 OpenAI 消息格式
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

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            response = await client.post(url, json=body, headers=headers)

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    f"[GLM] API error | status={response.status_code} | "
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
            logger.info(f"[GLM] Response OK | keys={list(result.keys())}")
            return result

    def _parse_glm_response(self, response: dict) -> tuple[str, list[dict]]:
        """
        解析 GLM (OpenAI 兼容格式) 响应。

        Returns:
            (text_content, tool_use_blocks)
        """
        text_content = ""
        tool_use_blocks: list[dict] = []

        choices = response.get("choices", [])
        if not choices:
            logger.warning("[GLM] No choices in response")
            return text_content, tool_use_blocks

        choice = choices[0]
        message = choice.get("message", {})

        # 提取文本
        text_content = message.get("content", "") or ""
        logger.info(
            f"[GLM] Parsed | text_len={len(text_content)} | "
            f"text_preview={text_content[:150]!r}"
        )

        # 提取 tool_calls
        raw_tool_calls = message.get("tool_calls", [])
        logger.info(f"[GLM] tool_calls count: {len(raw_tool_calls)}")

        for tc in raw_tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            tool_args_str = func.get("arguments", "{}")

            try:
                tool_input = json.loads(tool_args_str) if tool_args_str else {}
            except json.JSONDecodeError:
                logger.warning(
                    f"[GLM] Failed to parse tool args: {tool_args_str[:200]}"
                )
                tool_input = {"_raw": tool_args_str}

            tool_use_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", str(uuid.uuid4())),
                "name": tool_name,
                "input": tool_input,
            })
            logger.info(
                f"[GLM] Tool: {tool_name} | args_keys="
                f"{list(tool_input.keys()) if isinstance(tool_input, dict) else '?'}"
            )

        finish_reason = choice.get("finish_reason", "")
        logger.info(
            f"[GLM] Done | text={len(text_content)} | "
            f"tools={len(tool_use_blocks)} | finish={finish_reason}"
        )

        return text_content, tool_use_blocks

    # ==================================================================
    # GLM 查询循环（含 fallback）
    # ==================================================================

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

        核心优化：
        - 追踪连续无 tool_use 轮次 (consecutive_empty)
        - 第 1 次无 tool_use：注入提醒，继续尝试
        - 第 2 次无 tool_use：强制输出 Markdown 总结
        """
        logger.info(f"[GLM] _query_glm started | task={task[:100]}")

        # 连续无工具调用计数器
        consecutive_empty = 0
        MAX_CONSECUTIVE_EMPTY = 2

        for turn in range(self.max_turns):
            logger.info(f"[GLM] === Turn {turn + 1}/{self.max_turns} === "
                        f"consecutive_empty={consecutive_empty}")

            # ---- 调用 API ----
            response = await self._glm_api_call(
                messages=messages,
                system=system_prompt,
                tools_schema=tools_schema,
            )

            # ---- API 错误 ----
            if response.get("type") == "api_error":
                error = response.get("error", {})
                msg = (
                    error.get("message", str(error))
                    if isinstance(error, dict) else str(error)
                )
                logger.error(f"[GLM] API error turn {turn + 1}: {msg[:300]}")
                yield f"\n[API 错误] {msg}\n"
                return

            # ---- 解析响应 ----
            text_content, tool_use_blocks = self._parse_glm_response(response)

            # ---- 有工具调用 → 重置计数器，执行工具 ----
            if tool_use_blocks:
                consecutive_empty = 0

                # yield 文本
                if text_content:
                    yield text_content

                # 构建助手消息（OpenAI 格式）
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": text_content or None,
                }
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
                if tool_use_blocks:
                    assistant_msg.setdefault(
                        "tool_call_id", tool_use_blocks[0]["id"]
                    )
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
                        f"[GLM] Executing: {tool_name} | "
                        f"args={json.dumps(tool_input, ensure_ascii=False)[:200]}"
                    )

                    result_str = await self._execute_tool(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        context=context,
                    )

                    logger.info(
                        f"[GLM] Tool done: {tool_name} | "
                        f"result_len={len(result_str)}"
                    )

                    # 追加工具结果（OpenAI 格式）
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": result_str,
                    })

                    if abort_event.is_set():
                        yield "\n[工具执行已被取消]\n"
                        return

                # 继续下一轮
                continue

            # ---- 无工具调用 → Fallback 逻辑 ----
            consecutive_empty += 1

            # yield 已有文本
            if text_content:
                yield text_content

            logger.warning(
                f"[GLM] No tool_use | consecutive_empty={consecutive_empty} | "
                f"turn={turn + 1}"
            )

            # --- 第 1 次无工具：注入提醒，继续尝试 ---
            if consecutive_empty == 1:
                logger.info("[GLM] Injecting fallback reminder (turn 1)")
                messages.append({
                    "role": "assistant",
                    "content": text_content or "",
                })
                messages.append({
                    "role": "user",
                    "content": _FALLBACK_PROMPT_TURN_1,
                })
                continue

            # --- 第 2 次无工具：强制 Markdown 总结 ---
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                logger.info("[GLM] Forcing markdown summary (fallback)")
                messages.append({
                    "role": "assistant",
                    "content": text_content or "",
                })
                messages.append({
                    "role": "user",
                    "content": _FALLBACK_PROMPT_FORCE_MD,
                })

                # 最后一次调用（不传 tools，让模型直接输出文本）
                final_response = await self._glm_api_call(
                    messages=messages,
                    system=system_prompt,
                    tools_schema=None,  # 不传工具，强制纯文本输出
                )

                if final_response.get("type") == "api_error":
                    yield "\n[输出阶段 API 错误，已停止]\n"
                    return

                final_text, _ = self._parse_glm_response(final_response)
                if final_text:
                    yield final_text
                else:
                    yield "\n（模型未能生成有效输出）\n"
                return

            # 不应该到这里，但保险起见
            return

        # 达到最大轮次
        yield "\n[已达到最大工具调用轮次限制，自动停止]\n"

    # ==================================================================
    # Claude 标准模式查询循环（含 fallback）
    # ==================================================================

    async def _query_claude(
        self,
        task: str,
        system_prompt: str,
        tools_schema: list[dict],
        messages: list[dict],
        abort_event: asyncio.Event,
        persona: str = "default",
    ) -> AsyncIterator[str]:
        """
        Claude 标准模式查询循环（Anthropic API 流式 SSE）。

        同样包含 fallback 逻辑：连续无 tool_use 时引导模型。
        """
        # 连续无工具调用计数器
        consecutive_empty = 0
        MAX_CONSECUTIVE_EMPTY = 2

        for turn in range(self.max_turns):
            # 本轮收集的状态
            text_parts: list[str] = []
            tool_use_blocks: list[dict] = []
            stop_reason = ""
            has_error = False

            # 累积正在接收的 tool_use 块
            current_tool_id = ""
            current_tool_name = ""
            current_tool_json_parts: list[str] = []

            logger.info(
                f"[Claude] === Turn {turn + 1}/{self.max_turns} === "
                f"consecutive_empty={consecutive_empty}"
            )

            # ---- 流式调用 API ----
            async for event in self.api_client.stream_messages(
                system=system_prompt,
                messages=messages,
                tools=tools_schema,
                model=self.model,
            ):
                event_type = event.get("type", "")

                # API 错误
                if event_type == "api_error":
                    error = event.get("error", {})
                    msg = (
                        error.get("message", str(error))
                        if isinstance(error, dict) else str(error)
                    )
                    logger.error(f"[Claude] API error: {msg[:300]}")
                    yield f"\n[API 错误] {msg}\n"
                    has_error = True
                    break

                # 文本增量
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

                # 内容块开始
                elif event_type == "content_block_start":
                    content_block = event.get("content_block", {})
                    if content_block.get("type") == "tool_use":
                        current_tool_id = content_block.get("id", "")
                        current_tool_name = content_block.get("name", "")
                        current_tool_json_parts = []
                        logger.info(
                            f"[Claude] tool_use start: {current_tool_name}"
                        )

                # 内容块结束
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
                            f"[Claude] tool_use complete: {current_tool_name}"
                        )
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_json_parts = []

                # 消息增量
                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    stop_reason = delta.get("stop_reason", "")

            # ---- 处理本轮结果 ----
            if has_error:
                return

            text_total = "".join(text_parts)
            logger.info(
                f"[Claude] Turn {turn + 1} done | "
                f"tools={len(tool_use_blocks)} | "
                f"text={len(text_total)} | stop={stop_reason}"
            )

            # ---- 有工具调用 → 重置计数器，执行 ----
            if tool_use_blocks:
                consecutive_empty = 0

                # 构建助手消息
                assistant_content: list[dict] = []
                if text_parts:
                    assistant_content.append({
                        "type": "text",
                        "text": text_total,
                    })
                assistant_content.extend(tool_use_blocks)
                messages.append({
                    "role": "assistant",
                    "content": assistant_content,
                })

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

                # 执行工具
                tool_results: list[dict] = []
                for block in tool_use_blocks:
                    tool_name = block["name"]
                    tool_input = block.get("input", {})
                    tool_use_id = block.get("id", str(uuid.uuid4()))

                    logger.info(
                        f"[Claude] Executing: {tool_name} | "
                        f"args={json.dumps(tool_input, ensure_ascii=False)[:200]}"
                    )

                    result_str = await self._execute_tool(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        context=context,
                    )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    })

                    if abort_event.is_set():
                        yield "\n[工具执行已被取消]\n"
                        return

                messages.append({"role": "user", "content": tool_results})

                if stop_reason == "end_turn":
                    continue

            # ---- 无工具调用 → Fallback ----
            else:
                consecutive_empty += 1

                logger.warning(
                    f"[Claude] No tool_use | consecutive_empty={consecutive_empty}"
                )

                # 第 1 次无工具：注入提醒
                if consecutive_empty == 1:
                    if not text_parts:
                        yield "（模型未返回任何内容）"
                    # 把已有文本作为助手消息，追加 fallback 提醒
                    if text_parts:
                        messages.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": text_total}],
                        })
                    messages.append({
                        "role": "user",
                        "content": _FALLBACK_PROMPT_TURN_1,
                    })
                    continue

                # 第 2+ 次无工具：强制输出
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    if not text_parts:
                        # 尝试一次强制纯文本输出
                        messages.append({
                            "role": "user",
                            "content": _FALLBACK_PROMPT_FORCE_MD,
                        })
                        # 用非流式获取最终输出
                        final_resp = await self.api_client.send_messages(
                            system=system_prompt,
                            messages=messages,
                            tools=[],  # 不传工具
                            model=self.model,
                        )
                        if final_resp.get("type") == "api_error":
                            yield "\n（模型未能生成输出）\n"
                        else:
                            content_blocks = final_resp.get("content", [])
                            for cb in content_blocks:
                                if cb.get("type") == "text":
                                    yield cb.get("text", "")
                    return

                # 兜底
                if not text_parts:
                    yield "（模型未返回任何内容）"
                return

        # 达到最大轮次
        yield "\n[已达到最大工具调用轮次限制，自动停止]\n"

    # ==================================================================
    # 主入口
    # ==================================================================

    async def query(
        self,
        task: str,
        persona: str = "default",
    ) -> AsyncIterator[str]:
        """
        主查询入口，yield 流式文本片段。

        根据 model 名称自动选择调用模式：
        - 含 "glm"/"minimax" 且 base_url 非 Anthropic → GLM OpenAI 兼容
        - 其他 → Claude Anthropic 标准模式

        persona 参数仅用于日志记录，不影响任何逻辑。
        """
        logger.info(
            f"QueryLoop started | model={self.model} | "
            f"is_glm={self._is_glm} | persona={persona} | "
            f"task={task[:100]}"
        )

        system_prompt = self._build_system_prompt()
        tools_schema = self.tool_registry.get_tools_schema()

        logger.info(
            f"Tools prepared | count={len(tools_schema)} | "
            f"names={sorted(t['name'] for t in tools_schema)}"
        )

        messages: list[dict] = [
            {"role": "user", "content": task},
        ]

        abort_event = asyncio.Event()

        # ---- 路由到对应的查询模式 ----
        if self._is_glm:
            logger.info("Routing to GLM OpenAI mode")
            async for chunk in self._query_glm(
                task=task,
                system_prompt=system_prompt,
                tools_schema=tools_schema,
                messages=messages,
                abort_event=abort_event,
                persona=persona,
            ):
                yield chunk
        else:
            mode_label = "GLM-Anthropic" if self._is_glm_anthropic else "Claude"
            logger.info(f"Routing to {mode_label} standard mode")
            async for chunk in self._query_claude(
                task=task,
                system_prompt=system_prompt,
                tools_schema=tools_schema,
                messages=messages,
                abort_event=abort_event,
                persona=persona,
            ):
                yield chunk

        logger.info("QueryLoop finished")

    # ==================================================================
    # 工具执行
    # ==================================================================

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        context: ToolUseContext,
    ) -> str:
        """
        执行单个工具并返回结果字符串。

        流程：查找工具 → 输入校验 → 调用 tool.call() → 格式化结果
        """
        logger.info(
            f"_execute_tool | {tool_name} | "
            f"args={json.dumps(tool_input, ensure_ascii=False)[:300]}"
        )

        # 1. 查找工具
        tool = self.tool_registry.find_tool(tool_name)
        if tool is None:
            available = sorted(t.name for t in self.tool_registry.get_all_tools())
            logger.warning(f"Tool not found: {tool_name} | available: {available}")
            return f"Error: tool '{tool_name}' not registered. Available: {available}"

        # 2. 输入校验
        try:
            validation = await tool.validate_input(tool_input, context)
            if not validation.result:
                msg = validation.message or "输入校验失败"
                logger.warning(f"Tool {tool_name} validation failed: {msg}")
                return f"输入校验失败: {msg}"
        except Exception as e:
            logger.warning(f"Tool {tool_name} validation error (skipping): {e}")

        # 3. 执行
        try:
            result = await tool.call(
                args=tool_input,
                context=context,
                on_progress=context.on_progress,
            )
        except asyncio.CancelledError:
            return "工具执行已被取消"
        except Exception as e:
            logger.exception(f"Tool {tool_name} execution error: {e}")
            return f"工具执行异常: {e}"

        # 4. 格式化
        return self._format_tool_result(tool_name, result)

    @staticmethod
    def _format_tool_result(tool_name: str, result) -> str:
        """将工具返回值格式化为字符串。"""
        if not isinstance(result, dict):
            return str(result)

        # 有 error → 失败
        error = result.get("error")
        if error:
            return f"工具执行失败: {error}"

        # 有 stdout → 优先返回
        stdout = result.get("stdout", "")
        if stdout:
            max_len = 50_000
            if len(stdout) > max_len:
                return stdout[:max_len] + (
                    f"\n... (输出已截断，原始长度 {len(stdout)} 字符)"
                )
            return stdout

        # 有 warning → 附加警告
        warning = result.get("warning", "")

        # 返回 JSON
        formatted = json.dumps(result, ensure_ascii=False, indent=2)
        if warning:
            formatted = f"⚠️ {warning}\n\n{formatted}"
        return formatted
