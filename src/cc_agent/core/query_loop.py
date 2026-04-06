"""
QueryLoop — Agent 核心查询循环（MVP 简化版）

对应原版 src/query.ts 的 query() + queryLoop()

MVP 阶段简化说明：
- 不实现 token budget / auto-compact / micro-compact
- 不实现 hooks（pre/post tool hooks）
- 不实现 MCP / 权限交互 / 沙箱
- 不实现 context collapse / history snip
- 最大工具调用轮次硬编码为 5

核心循环流程：
    用户 task → 构建 system prompt + messages
        → 调用 Claude API（流式 SSE）
        → 收集文本输出（yield 给调用方）
        → 收集 tool_use 块（累积 input_json_delta）
        → 执行工具 → 结果塞回 messages → 继续循环
        → 模型 end_turn 或达到轮次上限 → 结束
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Optional

from ..services.api import ClaudeAPIClient
from ..tools.registry import ToolRegistry


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
    核心查询循环（MVP 简化版）

    职责：
    1. 将用户消息 + system prompt + tools 发送给 Claude API
    2. 流式接收文本输出，yield 给上层
    3. 遇到 tool_use 块时累积 input，然后执行对应工具
    4. 将工具结果回传 API，继续循环
    5. 直到模型 end_turn 或达到最大轮次
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
    ):
        self.api_client = ClaudeAPIClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self.model = model
        self.tool_registry = tool_registry or ToolRegistry(project_root=project_root)

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
            str: 模型输出的文本片段（流式）
        """
        system_prompt = self._build_system_prompt()
        tools_schema = self.tool_registry.get_tools_schema()
        messages: list[dict] = [
            {"role": "user", "content": task},
        ]

        # ---- 多轮工具调用循环 ----
        for _turn in range(self.MAX_TURNS):
            # 本轮收集的状态
            text_parts: list[str] = []
            tool_use_blocks: list[dict] = []
            stop_reason = ""
            has_error = False

            # 用于累积正在接收的 tool_use 块
            current_tool_id = ""
            current_tool_name = ""
            current_tool_json_parts: list[str] = []

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
                    msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
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
                        # 累积工具输入 JSON 片段
                        partial = delta.get("partial_json", "")
                        if partial:
                            current_tool_json_parts.append(partial)

                # -- 内容块开始：工具调用在这里初始化 --
                elif event_type == "content_block_start":
                    content_block = event.get("content_block", {})
                    if content_block.get("type") == "tool_use":
                        current_tool_id = content_block.get("id", "")
                        current_tool_name = content_block.get("name", "")
                        current_tool_json_parts = []

                # -- 内容块结束：工具调用在这里封存 --
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
                        # 重置累积状态
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_json_parts = []

                # -- 消息增量：检查 stop_reason --
                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    stop_reason = delta.get("stop_reason", "")

            # ---- 处理本轮结果 ----
            if has_error:
                return

            # 没有工具调用 → 结束
            if not tool_use_blocks:
                return

            # ---- 构建助手消息（文本 + tool_use 块） ----
            assistant_content: list[dict] = []
            if text_parts:
                assistant_content.append({
                    "type": "text",
                    "text": "".join(text_parts),
                })
            assistant_content.extend(tool_use_blocks)
            messages.append({"role": "assistant", "content": assistant_content})

            # ---- 执行工具，收集 tool_result ----
            tool_results: list[dict] = []
            for block in tool_use_blocks:
                result_str = await self._execute_tool(
                    block["name"], block.get("input", {}),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("id", str(uuid.uuid4())),
                    "content": result_str,
                })

            # 将工具结果作为 user 消息追加
            messages.append({"role": "user", "content": tool_results})

            # 如果模型已经 end_turn，给模型一次机会看结果后结束
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

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        执行单个工具并返回结果字符串。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            str: 工具执行结果的字符串表示
        """
        tool = self.tool_registry.find_tool(tool_name)
        if tool is None:
            return f"错误：工具 '{tool_name}' 未注册"

        try:
            result = await tool.call(args=tool_input)
            if isinstance(result, dict):
                if result.get("error"):
                    return f"工具执行失败: {result['error']}"
                stdout = result.get("stdout", "")
                if stdout:
                    return stdout
                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            return f"工具执行异常: {e}"
