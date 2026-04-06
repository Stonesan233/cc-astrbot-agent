"""
BashTool — Shell 命令执行

对应原版 src/tools/BashTool/
"""

import asyncio
from pathlib import Path
from pydantic import BaseModel
from .base import BaseTool


class BashToolInput(BaseModel):
    command: str
    timeout: int = 120000  # ms
    run_in_background: bool = False
    description: str = ""


class BashTool(BaseTool):
    """执行 shell 命令"""

    name = "bash"
    aliases = ["Bash"]
    search_hint = "run shell commands"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return BashToolInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = BashToolInput(**args)
        cwd = self.project_root
        timeout_sec = input_data.timeout / 1000

        try:
            proc = await asyncio.create_subprocess_shell(
                input_data.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[:50000],
                "stderr": stderr.decode("utf-8", errors="replace")[:10000],
                "command": input_data.command,
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_sec}s",
                "command": input_data.command,
                "error": "timeout",
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": input_data.command,
                "error": str(e),
            }

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        cmd = (input_data or {}).get("command", "")
        return f"执行 shell 命令: {cmd}" if cmd else "执行 shell 命令"

    async def prompt(self, options: dict = None) -> str:
        return """Execute bash commands. Supports timeout, background execution, and sandbox isolation.
Usage: command (required), timeout (optional, default 120s), description (optional)."""

    def is_concurrency_safe(self, input_data: dict) -> bool:
        # Bash 默认不安全，并发时需要串行
        return False

    def is_read_only(self, input_data: dict) -> bool:
        return False

    def user_facing_name(self, input_data=None) -> str:
        cmd = (input_data or {}).get("command", "")
        return f"Bash({cmd[:40]})" if cmd else "Bash"
