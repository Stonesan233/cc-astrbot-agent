"""
AstrBot 插件：Claude Code Custom (cc-astrbot-agent)
====================================================

将 cc_agent 核心包装为 AstrBot 插件。
通过 /cc 或 /coding 命令调用 Claude Code Agent 执行编程任务。

功能：
- /cc <任务描述>       — 调用 Agent 执行编程任务
- /cc help             — 显示帮助
- /cc scan             — 扫描项目结构
- /cc read <路径>      — 读取文件
- /cc write <路径> <内容> — 写入文件
- /cc run <命令>       — 执行 Bash 命令
- /cc status           — 查看 Agent 状态与配置

特性：
- 管理员权限检查（仅管理员可使用）
- Persona 日志记录（朝日娘 / 露娜大人，仅记录不影响行为）
- 流式输出支持

后续扩展：
- StreamingToolExecutor 流式工具执行
- 多会话隔离
- 权限白名单
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

# ---------------------------------------------------------------------------
# 将 cc_agent 包加入 import 路径
# ---------------------------------------------------------------------------
_PLUGIN_DIR = Path(__file__).resolve().parent
_SRC_DIR = _PLUGIN_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cc_agent.agent import ClaudeCodeAgent  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_claude_code_custom"

# ---------------------------------------------------------------------------
# Persona 名称映射（仅用于日志标记，不影响 Agent 行为）
# ---------------------------------------------------------------------------
_PERSONA_LABELS = {
    "Asahi": "朝日娘(asahi)",
    "Luna": "露娜大人(luna)",
}


# ---------------------------------------------------------------------------
# 插件主类
# ---------------------------------------------------------------------------

class ClaudeCodePlugin(Star):
    """
    Claude Code Custom 插件

    在 AstrBot 中提供编程 Agent 能力。
    Agent 核心逻辑不包含任何人格设定，完全由 ClaudeCodeAgent 纯净实现。
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._agent: Optional[ClaudeCodeAgent] = None

    # ---- 生命周期 ----------------------------------------------------------

    async def initialize(self):
        """插件初始化：读取配置，创建 ClaudeCodeAgent 实例。"""
        api_key = self.config.get("claude_api_key", "").strip()
        if not api_key:
            logger.warning(
                f"[{PLUGIN_NAME}] 未配置 claude_api_key，"
                "请在插件设置中填写 Anthropic API Key"
            )

        # 项目根目录：优先配置，回退到插件目录
        project_root = self.config.get("project_root", "").strip()
        if not project_root:
            project_root = str(_PLUGIN_DIR)
            logger.info(
                f"[{PLUGIN_NAME}] 未配置 project_root，使用插件目录: {project_root}"
            )

        # 模型
        model = self.config.get("model", "claude-3-7-sonnet-20250219")

        # 自定义 API 端点
        base_url = self.config.get("base_url", "").strip() or None

        # 创建 Agent
        try:
            self._agent = ClaudeCodeAgent(
                project_root=project_root,
                claude_api_key=api_key or None,
                model=model,
                base_url=base_url,
            )
            logger.info(
                f"[{PLUGIN_NAME}] 初始化完成 | "
                f"root={project_root} | model={model}"
            )
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] Agent 初始化失败: {e}")
            self._agent = None

    async def terminate(self):
        """插件销毁：清理资源"""
        self._agent = None
        logger.info(f"[{PLUGIN_NAME}] 已卸载")

    # ---- 辅助方法 ----------------------------------------------------------

    def _get_persona_label(self, event: AstrMessageEvent) -> str:
        """
        从事件上下文中提取当前 Persona 标签。
        仅用于日志记录，不影响 Agent 行为。
        """
        # 尝试通过 conversation_manager 获取当前会话的 persona_id
        try:
            umo = event.unified_msg_origin
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if conv_mgr:
                curr_cid = asyncio.get_event_loop().run_until_complete(
                    conv_mgr.get_curr_conversation_id(umo)
                ) if not asyncio.get_event_loop().is_running() else None
                # 在 async 上下文中不能用 run_until_complete，改用同步方式
        except Exception:
            pass

        # 尝试从 event extra 获取
        for attr in ("persona_id", "persona"):
            val = getattr(event, attr, None)
            if val:
                return _PERSONA_LABELS.get(val, val)

        # 尝试从 session 获取
        session = getattr(event, "session", None)
        if session:
            pid = getattr(session, "persona_id", None) or getattr(session, "persona", None)
            if pid:
                return _PERSONA_LABELS.get(pid, str(pid))

        return "default"

    async def _get_persona_id_async(self, event: AstrMessageEvent) -> str:
        """异步获取当前 persona_id"""
        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return "default"
            umo = event.unified_msg_origin
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            if not curr_cid:
                return "default"
            conv = await conv_mgr.get_conversation(umo, curr_cid)
            if conv and hasattr(conv, "persona_id") and conv.persona_id:
                return conv.persona_id
        except Exception:
            pass
        return "default"

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        """检查当前用户是否为管理员"""
        # AstrBot 内置的 is_admin 检查
        if hasattr(event, "is_admin") and callable(event.is_admin):
            return event.is_admin()
        # 通过 role 检查
        role = getattr(event, "role", "")
        if role == "admin":
            return True
        return False

    def _ensure_agent(self) -> ClaudeCodeAgent:
        """确保 Agent 已初始化，否则抛出友好错误"""
        if self._agent is None:
            raise RuntimeError(
                "Agent 未初始化。请检查插件配置中的 claude_api_key 和 project_root。"
            )
        return self._agent

    # ---- 命令入口 ----------------------------------------------------------

    @filter.command("cc")
    async def cc_command(self, event: AstrMessageEvent, args: GreedyStr = ""):
        """
        Claude Code 主命令（仅管理员可用）

        用法:
          /cc <任务描述>          执行编程任务
          /cc scan               扫描项目结构
          /cc read <路径>         读取文件
          /cc write <路径> <内容>  写入文件
          /cc run <命令>          执行 Bash 命令
          /cc status             查看 Agent 状态
          /cc help               显示帮助
        """
        # ---- 权限检查 ----
        if not self._check_admin(event):
            yield event.plain_result("抱歉，/cc 命令仅限管理员使用。")
            return

        raw_args = args.strip()

        # 无参数 → 显示帮助
        if not raw_args:
            yield event.plain_result(self._help_text())
            return

        # 解析子命令
        parts = raw_args.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        # ---- 子命令分发 ----
        if sub == "help":
            yield event.plain_result(self._help_text())
            return

        if sub == "status":
            yield event.plain_result(self._status_text())
            return

        if sub == "scan":
            yield event.chain_result(await self._handle_scan(event))
            return

        if sub == "read":
            if not rest:
                yield event.plain_result("用法: /cc read <文件路径>")
                return
            yield event.chain_result(await self._handle_read(event, rest.strip()))
            return

        if sub == "write":
            if not rest:
                yield event.plain_result("用法: /cc write <文件路径> <内容>")
                return
            yield event.chain_result(await self._handle_write(event, rest.strip()))
            return

        if sub in ("run", "exec", "bash"):
            if not rest:
                yield event.plain_result("用法: /cc run <shell 命令>")
                return
            yield event.chain_result(await self._handle_run(event, rest.strip()))
            return

        # ---- 默认：作为任务交给 Agent ----
        yield event.chain_result(await self._handle_task(event, raw_args))

    # ---- 子命令处理器 ------------------------------------------------------

    async def _handle_scan(self, event: AstrMessageEvent) -> MessageChain:
        """扫描项目结构"""
        persona_label = await self._get_persona_id_async(event)
        logger.info(f"[{PLUGIN_NAME}] scan | persona={persona_label}")
        try:
            agent = self._ensure_agent()
            result = await agent.scan_project()
            files = result.get("files", [])
            msg = f"项目扫描完成，共发现 {len(files)} 个文件。"
            if files:
                preview = "\n".join(f"  - {f}" for f in files[:20])
                if len(files) > 20:
                    preview += f"\n  ... 等共 {len(files)} 个文件"
                msg += f"\n\n{preview}"
            return MessageChain().message(msg)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] scan 失败: {e}")
            return MessageChain().message(f"项目扫描失败: {e}")

    async def _handle_read(self, event: AstrMessageEvent, file_path: str) -> MessageChain:
        """读取文件"""
        persona_label = await self._get_persona_id_async(event)
        logger.info(f"[{PLUGIN_NAME}] read {file_path} | persona={persona_label}")
        try:
            agent = self._ensure_agent()
            content = await agent.read_file(file_path)
            if not content:
                return MessageChain().message(f"文件为空或不存在: {file_path}")
            # 截断超长内容
            max_chars = 4000
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n... (已截断，原始 {len(content)} 字符)"
            return MessageChain().message(f"📄 {file_path}:\n\n{content}")
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] read 失败: {e}")
            return MessageChain().message(f"读取文件失败: {e}")

    async def _handle_write(self, event: AstrMessageEvent, args_str: str) -> MessageChain:
        """写入文件：/cc write <路径> <内容>"""
        persona_label = await self._get_persona_id_async(event)
        parts = args_str.split(maxsplit=1)
        if len(parts) < 2:
            return MessageChain().message("用法: /cc write <文件路径> <文件内容>")

        file_path, content = parts[0], parts[1]
        logger.info(f"[{PLUGIN_NAME}] write {file_path} ({len(content)} chars) | persona={persona_label}")
        try:
            agent = self._ensure_agent()
            result = await agent.write_file(file_path, content)
            if isinstance(result, dict) and result.get("error"):
                return MessageChain().message(f"写入失败: {result['error']}")
            return MessageChain().message(f"✅ 已写入: {file_path} ({len(content)} 字符)")
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] write 失败: {e}")
            return MessageChain().message(f"写入文件失败: {e}")

    async def _handle_run(self, event: AstrMessageEvent, command: str) -> MessageChain:
        """执行 Bash 命令：/cc run <命令>"""
        persona_label = await self._get_persona_id_async(event)
        timeout = self.config.get("command_timeout", 60)
        logger.info(
            f"[{PLUGIN_NAME}] run: {command[:60]}... | "
            f"persona={persona_label} | timeout={timeout}s"
        )
        try:
            agent = self._ensure_agent()
            result = await agent.execute_command(command, timeout=timeout)
            if isinstance(result, dict):
                exit_code = result.get("exit_code", -1)
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                error = result.get("error")

                if error:
                    return MessageChain().message(f"命令执行错误: {error}")

                # 截断输出
                if len(stdout) > 3000:
                    stdout = stdout[:3000] + f"\n... (已截断，原始 {len(stdout)} 字符)"

                msg = f"退出码: {exit_code}"
                if stdout.strip():
                    msg += f"\n\n stdout:\n{stdout.strip()}"
                if stderr.strip():
                    stderr_preview = stderr[:1000]
                    msg += f"\n\n stderr:\n{stderr_preview.strip()}"
                return MessageChain().message(msg)

            return MessageChain().message(str(result))
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] run 失败: {e}")
            return MessageChain().message(f"命令执行失败: {e}")

    async def _handle_task(self, event: AstrMessageEvent, task: str) -> MessageChain:
        """
        执行 Agent 任务（核心入口）

        调用 agent.run_task() 进行多轮工具调用，
        收集流式输出后返回给用户。
        支持 enable_streaming 配置。
        """
        persona_id = await self._get_persona_id_async(event)
        persona_label = _PERSONA_LABELS.get(persona_id, persona_id)
        logger.info(
            f"[{PLUGIN_NAME}] 任务开始 | persona={persona_label} | "
            f"task={task[:80]}..."
        )

        start = time.monotonic()
        enable_streaming = self.config.get("enable_streaming", True)

        try:
            agent = self._ensure_agent()

            # ---- 流式模式 ----
            if enable_streaming and hasattr(event, "send_streaming"):
                output_parts: list[str] = []
                try:
                    async for chunk in agent.run_task(task=task, persona=persona_id):
                        output_parts.append(chunk)
                        # 尝试流式发送中间结果
                        if len(output_parts) % 5 == 0:
                            try:
                                await event.send(
                                    MessageChain().message("".join(output_parts[-5:]))
                                )
                            except Exception:
                                pass  # 平台不支持多段发送则忽略

                    full_output = "".join(output_parts)
                except Exception:
                    full_output = "".join(output_parts) if output_parts else ""

            # ---- 非流式模式 ----
            else:
                output_parts: list[str] = []
                async for chunk in agent.run_task(task=task, persona=persona_id):
                    output_parts.append(chunk)
                full_output = "".join(output_parts)

            elapsed = time.monotonic() - start

            if not full_output.strip():
                full_output = "（Agent 未返回任何输出）"

            # 添加耗时标记
            result_text = f"{full_output}\n\n---\n⏱ 耗时 {elapsed:.1f}s"

            logger.info(
                f"[{PLUGIN_NAME}] 任务完成 | persona={persona_label} | "
                f"耗时={elapsed:.1f}s | 输出={len(full_output)}字符"
            )

            return MessageChain().message(result_text)

        except RuntimeError as e:
            logger.error(f"[{PLUGIN_NAME}] Agent 错误: {e}")
            return MessageChain().message(f"❌ {e}")

        except asyncio.CancelledError:
            logger.info(f"[{PLUGIN_NAME}] 任务被取消 | persona={persona_label}")
            return MessageChain().message("任务已被取消。")

        except Exception as e:
            logger.error(
                f"[{PLUGIN_NAME}] 任务异常 | persona={persona_label}\n"
                f"{traceback.format_exc()}"
            )
            return MessageChain().message(
                f"❌ 任务执行异常: {e}\n\n请检查插件配置和日志获取详细信息。"
            )

    # ---- 帮助 / 状态 -------------------------------------------------------

    def _help_text(self) -> str:
        return (
            "Claude Code Custom — 编程 Agent\n"
            "\n"
            "用法:\n"
            "  /cc <任务描述>          让 Agent 执行编程任务\n"
            "  /cc scan               扫描项目结构\n"
            "  /cc read <路径>         读取文件内容\n"
            "  /cc write <路径> <内容>  写入文件\n"
            "  /cc run <命令>          执行 Bash 命令\n"
            "  /cc status             查看 Agent 状态\n"
            "  /cc help               显示此帮助\n"
            "\n"
            "示例:\n"
            "  /cc 读取 README.md 并总结\n"
            "  /cc 在 src/ 下创建一个 hello.py\n"
            "  /cc read src/main.py\n"
            "  /cc write test.txt Hello World\n"
            "  /cc run pip list\n"
            "\n"
            "注意: 仅管理员可使用此命令。Agent 不会修改人格设定。"
        )

    def _status_text(self) -> str:
        """Agent 状态报告"""
        if self._agent is None:
            return "❌ Agent 未初始化。请检查 claude_api_key 配置。"

        model = self._agent.model or "未知"
        root = str(self._agent.project_root)
        tools = self._agent.tool_registry.get_all_tools()
        tool_names = sorted(t.name for t in tools if t.is_enabled())

        has_key = bool(self._agent.api_key)
        key_status = "已配置" if has_key else "未配置"
        streaming = self.config.get("enable_streaming", True)
        timeout = self.config.get("command_timeout", 60)

        return (
            "Claude Code Agent 状态\n"
            "\n"
            f"  API Key:    {key_status}\n"
            f"  模型:       {model}\n"
            f"  项目目录:   {root}\n"
            f"  流式输出:   {'开启' if streaming else '关闭'}\n"
            f"  命令超时:   {timeout}s\n"
            f"  可用工具:   {', '.join(tool_names)}"
        )
