"""
ToolExecution — 单工具执行流程

对应原版 src/services/tools/toolExecution.ts
"""

from typing import AsyncIterator, Optional


async def run_tool_use(
    tool_use: dict,
    assistant_message: dict,
    can_use_tool=None,
    context=None,
) -> AsyncIterator[dict]:
    """
    单个工具的完整执行流程:

    1. findToolByName() — 查找工具定义
    2. inputSchema.safeParse() — 解析输入
    3. validateInput() — 校验输入
    4. runPreToolUseHooks() — 执行前置钩子
    5. resolveHookPermissionDecision() — 解析钩子权限
    6. checkPermissions() — 权限检查
    7. canUseTool() — 用户交互确认
    8. tool.call() — 执行工具
    9. runPostToolUseHooks() — 执行后置钩子
    10. mapToolResultToBlock() — 格式化结果

    对应原版 runToolUse()
    """
    # MVP 阶段占位
    yield {"type": "placeholder", "message": f"Tool execution for {tool_use.get('name', '?')}"}
