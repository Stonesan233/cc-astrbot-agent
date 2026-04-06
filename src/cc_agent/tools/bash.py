"""
BashTool — Shell 命令执行工具

使用 asyncio.create_subprocess_shell 执行 shell 命令，
支持超时、工作目录、安全检查，并实时捕获 stdout/stderr。

对应原版 src/tools/BashTool/
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .base import BaseTool


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class BashInput(BaseModel):
    """BashTool 输入参数"""
    command: str = Field(description="要执行的 shell 命令")
    timeout: Optional[int] = Field(
        default=60,
        description="超时时间（秒），默认 60 秒",
    )
    cwd: Optional[str] = Field(
        default=None,
        description="工作目录，默认使用 project_root",
    )


# ---------------------------------------------------------------------------
# 危险命令黑名单（简易版）
# ---------------------------------------------------------------------------

# 匹配到任意一条即视为危险命令，直接拒绝执行
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    # rm -rf / 及其变体
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-[a-zA-Z]*r[a-zA-Z]*\s+/?\s*$", re.IGNORECASE),
    re.compile(r"\brm\s+--recursive\s+--force\s+/", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/(?!\S)", re.IGNORECASE),
    # format 盘符
    re.compile(r"\bformat\s+[A-Za-z]:", re.IGNORECASE),
    # dd 写零 / 覆盖磁盘
    re.compile(r"\bdd\s+.*if=/dev/zero", re.IGNORECASE),
    re.compile(r"\bdd\s+.*of=/dev/sd", re.IGNORECASE),
    re.compile(r"\bdd\s+.*of=/dev/hd", re.IGNORECASE),
    # mkfs
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    # 覆盖关键文件
    re.compile(r">\s*/etc/passwd", re.IGNORECASE),
    re.compile(r">\s*/etc/shadow", re.IGNORECASE),
    # 无限 fork 炸弹
    re.compile(r":\(\)\{.*:\|:&\}", re.IGNORECASE),
    re.compile(r"\bfork\s+bomb\b", re.IGNORECASE),
    # 删除整个根目录 / 家目录
    re.compile(r"\brm\s+-rf\s+~", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+\$HOME", re.IGNORECASE),
    # chmod 777 关键目录
    re.compile(r"\bchmod\s+(-R\s+)?777\s+/", re.IGNORECASE),
]


def _is_dangerous_command(command: str) -> Optional[str]:
    """检查命令是否包含危险模式，返回原因字符串；安全则返回 None"""
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return f"命令匹配到危险模式: {pattern.pattern}"
    return None


# ---------------------------------------------------------------------------
# BashTool 实现
# ---------------------------------------------------------------------------

class BashTool(BaseTool):
    """Shell 命令执行工具"""

    name: str = "bash"
    # 支持 "execute_command" 别名，方便上层注册
    aliases: list[str] = ["execute_command"]
    search_hint: str = "execute shell commands"

    def __init__(self, project_root: str = ""):
        self._project_root = project_root or str(Path.cwd())

    @property
    def input_schema(self) -> type[BaseModel]:
        return BashInput

    # ---- 核心执行 ----------------------------------------------------------

    async def call(self, args: dict, context: Any = None, on_progress=None) -> dict:
        """
        执行 shell 命令并返回结果。

        Args:
            args: 包含 command / timeout / cwd 的参数字典
            context: 工具执行上下文（可选）
            on_progress: 进度回调（可选）

        Returns:
            dict: 包含 exit_code / stdout / stderr / command / error
        """
        # 参数解析
        try:
            parsed = BashInput(**args)
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "command": args.get("command", ""),
                "error": f"参数解析失败: {e}",
            }

        command: str = parsed.command
        timeout_sec: int = parsed.timeout or 60
        cwd: Optional[str] = parsed.cwd

        # 1) 空命令检查
        if not command.strip():
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "command": "",
                "error": "命令不能为空",
            }

        # 2) 安全检查
        danger_reason = _is_dangerous_command(command)
        if danger_reason:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "command": command,
                "error": f"安全检查未通过: {danger_reason}。该命令已被拦截，如需执行请联系管理员。",
            }

        # 3) 确定工作目录
        work_dir = Path(cwd) if cwd else Path(self._project_root)
        if not work_dir.exists():
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "command": command,
                "error": f"工作目录不存在: {work_dir}",
            }

        # 4) 执行命令
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )

            # 流式收集输出 + 超时控制
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                # 超时，杀死进程
                process.kill()
                await process.wait()
                return {
                    "exit_code": -9,
                    "stdout": "",
                    "stderr": f"命令执行超时（{timeout_sec}秒），进程已被终止",
                    "command": command,
                    "error": "timeout",
                }

            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            returncode = process.returncode or 0

            result = {
                "exit_code": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
            }

            # 非零退出码视为错误
            if returncode != 0:
                result["error"] = f"命令执行失败（退出码: {returncode}）"

            return result

        except FileNotFoundError as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command,
                "error": f"无法执行命令，请检查 shell 是否可用: {e}",
            }
        except OSError as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command,
                "error": f"系统错误: {e}",
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command,
                "error": f"命令执行异常: {e}",
            }

    # ---- 描述 / Prompt -----------------------------------------------------

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        """返回工具调用的简短描述"""
        cmd = (input_data or {}).get("command", "")
        if not cmd:
            return "执行 shell 命令"
        # 取第一行，截断展示
        first_line = cmd.splitlines()[0]
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return f"执行: {first_line}"

    async def prompt(self, options: dict = None) -> str:
        """返回完整的工具 prompt 说明"""
        return (
            "执行给定的 shell 命令并返回其输出。\n"
            "\n"
            "工作目录在命令间持久化，但 shell 状态不会。"
            " shell 环境从用户的 profile（bash 或 zsh）初始化。\n"
            "\n"
            "使用说明:\n"
            "- 如果命令之间相互独立且可以并行运行，"
            "在一条消息中发起多个 Bash 工具调用。\n"
            "- 如果命令之间相互依赖且必须按顺序运行，"
            "使用单个 Bash 调用并用 && 串联。\n"
            "- 使用 ; 仅在需要按顺序运行但不关心先前命令是否失败时。\n"
            "- 不要使用换行符分隔命令（引号字符串内的换行可以）。\n"
            "- 尝试在整个会话中通过使用绝对路径维持当前工作目录。\n"
            "- 可以指定可选的超时时间（秒），默认 60 秒。\n"
        )

    # ---- 只读 / 并发安全判定 -----------------------------------------------

    def is_read_only(self, input_data: dict) -> bool:
        """
        根据命令内容判断是否为只读操作。

        简单版：如果命令包含明显的写入/删除/修改关键词，返回 False。
        """
        cmd = (input_data.get("command") or "").strip() if input_data else ""

        if not cmd:
            return True

        # 提取首个命令词
        first_token = cmd.split()[0] if cmd.split() else ""

        # 已知的只读命令集合
        _READ_ONLY_COMMANDS = {
            "ls", "dir", "cat", "head", "tail", "less", "more",
            "echo", "printf", "pwd", "whoami", "hostname", "uname",
            "date", "which", "whereis", "find", "grep", "rg", "ag",
            "wc", "stat", "file", "strings", "jq",
            "git", "gh", "diff", "tree", "du", "df",
            "env", "printenv", "type", "command",
            "curl", "wget",
        }

        if first_token in _READ_ONLY_COMMANDS:
            # 输出重定向到文件则不算只读
            if re.search(r"[>]\s*\S", cmd) and ">>" not in cmd:
                return False
            # 管道中包含写入操作也不算只读
            _WRITE_INDICATORS = [
                r"\btee\b", r"\brm\b", r"\bmv\b", r"\bcp\b",
                r"\binstall\b", r"\bchmod\b", r"\bchown\b",
                r"\bdd\b", r"\bmkdir\b", r"\btouch\b",
            ]
            for indicator in _WRITE_INDICATORS:
                if re.search(indicator, cmd):
                    return False
            return True

        # 包含写入关键词的命令
        _WRITE_COMMANDS = {
            "rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod",
            "chown", "ln", "dd", "install", "mkfs", "format",
            "pip", "npm", "yarn", "pnpm", "apt", "yum", "dnf",
            "brew", "cargo", "go",
        }
        if first_token in _WRITE_COMMANDS:
            return False

        # 默认保守判断为非只读
        return False

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """
        判断是否可以安全并发执行。

        BashTool 的命令执行需谨慎。
        仅当命令被判定为只读时才认为并发安全。
        """
        return self.is_read_only(input_data)

    def user_facing_name(self, input_data: dict = None) -> str:
        """用户可见的工具名称"""
        cmd = (input_data or {}).get("command", "")
        if cmd:
            display = cmd.splitlines()[0]
            if len(display) > 40:
                display = display[:37] + "..."
            return f"Bash({display})"
        return "Bash"
