# CLAUDE.md — cc-astrbot-agent

## 项目定位

AstrBot 插件，提供纯 Python 实现的 Claude Code Agent 核心。不包含任何人格逻辑，仅提供多轮工具调用能力。人格设定由上层（AstrBot Persona 或其他插件如 astrbot_plugin_bot_chatroom）负责。

## 关键路径

- 插件入口: `main.py` → `ClaudeCodePlugin(Star)`
- Agent 核心: `src/cc_agent/agent.py` → `ClaudeCodeAgent`
- 查询循环: `src/cc_agent/core/query_loop.py` → `QueryLoop`
- API 客户端: `src/cc_agent/services/api.py` → `ClaudeAPIClient`
- 工具注册: `src/cc_agent/tools/registry.py` → `ToolRegistry`
- 工具基类: `src/cc_agent/tools/base.py` → `BaseTool`, `ToolResult`

## 导入约定

`main.py` 在模块级别将 `src/` 加入 `sys.path`，然后 `from cc_agent.agent import ClaudeCodeAgent`。所有子模块使用相对导入（如 `from .core.query_loop import QueryLoop`）。

其他插件引用本插件时，需要将 `cc-astrbot-agent/src` 加入 `sys.path` 后 `from cc_agent.agent import ClaudeCodeAgent`。

## 架构要点

### QueryLoop 双模式

`QueryLoop.query()` 根据模型名称自动选择调用模式：
- 模型名含 "glm" → OpenAI 兼容格式 (`_query_glm`)
- 其他 → Anthropic SSE 流式 (`_query_claude`)

### ClaudeCodeAgent API

```python
agent = ClaudeCodeAgent(project_root=..., claude_api_key=..., model=..., base_url=...)

# 主入口：流式任务执行
async for chunk in agent.run_task(task="...", persona="luna"):
    print(chunk, end="")

# 直接工具调用（绕过 LLM）
result = await agent.scan_project()
content = await agent.read_file("path")
result = await agent.write_file("path", "content")
result = await agent.execute_command("ls -la", timeout=60)
```

`persona` 参数仅用于日志标记，不影响 Agent 行为。

### 工具系统

所有工具继承 `BaseTool`，在 `ToolRegistry` 中注册。每个工具需实现 `call(input_dict) -> ToolResult`。工具 Schema 通过 `get_tools_schema()` 导出为 Anthropic 兼容格式。

安全机制：
- `BashTool`: 危险命令黑名单 (rm -rf /, dd, mkfs, fork bomb 等)
- `FileWriteTool` / `FileEditTool`: 阻止系统敏感路径写入，防止路径遍历
- 工具输出截断：50,000 字符上限

### Bridge 接口

`src/cc_agent/bridge/astrbot_bridge.py` 定义了两个抽象接口：
- `IPersonaCallback`: Agent → Persona 回调（流式输出、工具通知、权限请求、错误）
- `IAstrBotBridge`: Persona → Agent 入口（对话、技能、状态、中断、权限）

这些接口是预留合约，尚未完全接入。

## AstrBot 插件模式

- 继承 `Star` 基类 (`astrbot.api.star`)
- 用 `@filter.command("cc")` 注册命令
- 响应用 `yield event.plain_result(...)` (async generator)
- 流式中继用 `await event.send(batch)`
- 配置通过 `_conf_schema.json` 定义，运行时 `self.config` 读取

## 常量

- `MAX_TURNS = 5`: QueryLoop 硬编码最大轮次
- API 超时: 300s 总计 / 30s 连接
- 工具输出上限: 50,000 字符
- 文件读取上限: 2000 行 / 20MB
