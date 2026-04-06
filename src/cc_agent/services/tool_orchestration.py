"""
ToolOrchestration — 工具编排执行

对应原版 src/services/tools/toolOrchestration.ts
"""

from typing import AsyncIterator


async def run_tools(
    tool_use_blocks: list[dict],
    assistant_messages: list[dict],
    can_use_tool=None,
    context=None,
) -> AsyncIterator[dict]:
    """
    编排工具并发执行

    策略:
    - 只读工具（is_concurrency_safe=True）并行执行
    - 写工具串行执行
    - 结果按工具出现顺序 yield

    对应原版 runTools() → partitionToolCalls() → runToolsConcurrently/Serially
    """
    # MVP 阶段占位
    for block in tool_use_blocks:
        yield {"type": "placeholder", "tool": block.get("name", "unknown")}
