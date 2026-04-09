# cc-astrbot-agent

AstrBot 插件：Claude Code Agent 核心（纯 Python 重写），提供多轮工具调用的编程 Agent 能力。

## 概述

将 Claude Code 的核心功能封装为 AstrBot 插件，通过 `/cc` 命令在聊天中调用 AI Agent 执行编程任务。

Agent 核心不包含任何人格逻辑，仅提供纯净的工具调用能力。人格设定由 AstrBot 的 Persona 层负责。

## 功能

| 命令 | 说明 |
|---|---|
| `/cc <任务描述>` | 让 Agent 执行编程任务 |
| `/cc scan` | 扫描项目结构 |
| `/cc read <路径>` | 读取文件 |
| `/cc write <路径> <内容>` | 写入文件 |
| `/cc run <命令>` | 执行 Bash 命令 |
| `/cc status` | 查看 Agent 状态与配置 |
| `/cc help` | 显示帮助 |

> 仅管理员可使用 `/cc` 命令。

## 安装

将插件目录放置在 AstrBot 的插件目录下，或在 AstrBot 管理面板中安装。

### 依赖

- Python >= 3.11
- httpx
- anthropic
- pydantic
- aiofiles

## 配置

在 AstrBot 管理面板中配置，或直接编辑 `_conf_schema.json`。

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `claude_api_key` | string | `""` | Anthropic API Key（必填） |
| `base_url` | string | `""` | 自定义 API 端点，留空使用默认 |
| `model` | string | `claude-3-7-sonnet-20250219` | 模型名称，支持 Claude 和 GLM |
| `project_root` | string | `""` | Agent 工作目录，留空使用插件目录 |
| `enable_auto_apply` | bool | `false` | 自动应用文件修改 |
| `max_tool_turns` | int | `5` | 最大工具调用轮次（1-20） |
| `command_timeout` | int | `60` | 命令执行超时秒数（10-300） |
| `enable_streaming` | bool | `true` | 启用流式输出 |

## 架构

```
cc-astrbot-agent/
├── main.py                          # AstrBot 插件入口 (ClaudeCodePlugin)
├── metadata.yaml                    # 插件元数据
├── _conf_schema.json                # 配置 Schema
├── pyproject.toml                   # 构建配置
├── tests/                           # 测试
└── src/cc_agent/                    # Agent 核心
    ├── agent.py                     # ClaudeCodeAgent 门面类
    ├── bridge/
    │   └── astrbot_bridge.py        # 抽象接口 (IAstrBotBridge, IPersonaCallback)
    ├── core/
    │   ├── query_loop.py            # 核心多轮工具调用循环
    │   ├── query_config.py          # 查询配置 (不可变快照)
    │   ├── token_budget.py          # Token 预算追踪
    │   └── tool_use_context.py      # 工具调用上下文
    ├── services/
    │   ├── api.py                   # Anthropic API 客户端 (SSE 流式)
    │   ├── tool_execution.py        # 工具执行 (预留)
    │   └── tool_orchestration.py    # 工具编排 (预留)
    ├── tools/
    │   ├── base.py                  # BaseTool / ToolResult / ValidationResult
    │   ├── registry.py              # ToolRegistry 注册与 Schema 导出
    │   ├── bash.py                  # Shell 命令执行
    │   ├── file_read.py             # 文件读取
    │   ├── file_write.py            # 文件写入
    │   ├── file_edit.py             # 精确字符串替换编辑
    │   ├── glob.py                  # 文件模式匹配
    │   ├── grep.py                  # 内容搜索 (ripgrep / Python re)
    │   ├── patch.py                 # Unified Diff 生成
    │   └── scan_project.py          # 项目结构扫描
    └── types/
        └── messages.py              # 消息类型层次
```

### 核心流程

```
用户 /cc <任务>
  → ClaudeCodePlugin.cc_command()
    → ClaudeCodeAgent.run_task(task, persona)
      → QueryLoop.query()
        → 构建 system prompt (注入工具描述)
        → 循环 (最多 5 轮):
            → 调用 Claude API (SSE 流式)
            → 收集文本输出 (yield 给调用方)
            → 收集 tool_use 块
            → 执行工具
            → 追加 tool_result
            → 继续循环 (如有工具调用)
```

### 支持的模型

- **Claude 系列**: 通过 Anthropic Messages API (SSE 流式)
- **GLM 系列**: 当模型名包含 "glm" 时自动切换到 OpenAI 兼容格式 (`/v1/chat/completions`)

## 内置工具 (8 个)

| 工具 | 只读 | 说明 |
|---|---|---|
| `bash` | 视命令 | 执行 Shell 命令，有危险命令黑名单 |
| `read_file` | Yes | 读取文件（带行号，支持分页） |
| `write_file` | No | 写入文件（自动创建目录，阻止敏感路径） |
| `edit_file` | No | 精确字符串替换（生成 diff 预览） |
| `glob` | Yes | 文件模式匹配（按修改时间排序） |
| `grep` | Yes | 内容搜索（优先 ripgrep） |
| `generate_patch` | Yes | 生成 unified diff（不修改文件） |
| `scan_project` | Yes | 扫描项目目录树 |

## 开发

```bash
# 安装依赖
pip install -e .

# 运行测试
python -m pytest tests/

# 手动测试
python test_agent.py
```

## 许可

MIT License
