"""
GrepTool — 文件内容搜索

对应原版 src/tools/GrepTool/
优先使用 ripgrep (rg) 命令行工具获得更好性能，
不可用时自动 fallback 到 Python re 模块纯实现。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .base import BaseTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class GrepInput(BaseModel):
    """GrepTool 的输入参数"""
    pattern: str                           # 搜索模式（正则表达式）
    path: Optional[str] = "."              # 搜索根路径（默认当前目录）
    recursive: bool = True                 # 是否递归搜索子目录
    glob: Optional[str] = None             # 文件名 glob 过滤（如 "*.py"）
    output_mode: str = "content"           # content | files_with_matches | count
    case_insensitive: bool = False         # 是否忽略大小写
    head_limit: int = 100                  # 最大返回结果数


# ---------------------------------------------------------------------------
# rg 可用性检测（模块加载时执行一次）
# ---------------------------------------------------------------------------

_HAS_RG = shutil.which("rg") is not None


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

class GrepTool(BaseTool):
    """文件内容正则搜索（ripgrep 优先，Python re 备用）"""

    name = "grep"
    aliases = ["Grep", "search"]
    search_hint = "search file contents with regex"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self) -> type[BaseModel]:
        return GrepInput

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def call(
        self,
        args: dict,
        context: Optional[object] = None,
        on_progress: Optional[object] = None,
    ) -> dict:
        """
        搜索文件内容。

        流程:
        1. 解析并校验输入
        2. 将相对路径解析为绝对路径
        3. 编译正则表达式
        4. 优先使用 rg（如果可用），否则 fallback 到 Python re
        5. 格式化匹配结果
        """
        # ---- 1. 解析输入 ----
        try:
            inp = GrepInput(**args)
        except Exception as e:
            return {"matches": [], "total": 0, "error": f"参数校验失败: {e}"}

        if not inp.pattern:
            return {"matches": [], "total": 0, "error": "pattern 不能为空"}

        # ---- 2. 解析路径 ----
        search_root = Path(inp.path) if inp.path else Path(self.project_root)
        if not search_root.is_absolute():
            search_root = Path(self.project_root) / search_root

        if not search_root.exists():
            return {"matches": [], "total": 0, "error": f"路径不存在: {search_root}"}

        # ---- 3. 编译正则 ----
        flags = re.IGNORECASE if inp.case_insensitive else 0
        try:
            regex = re.compile(inp.pattern, flags)
        except re.error as e:
            return {"matches": [], "total": 0, "error": f"无效的正则表达式: {e}"}

        # ---- 4. 选择搜索引擎 ----
        if _HAS_RG:
            logger.info(f"[GrepTool] Using ripgrep | pattern={inp.pattern}")
            return await self._search_with_rg(inp, search_root)
        else:
            logger.info(f"[GrepTool] Using Python re fallback | pattern={inp.pattern}")
            return self._search_with_re(inp, regex, search_root)

    # ------------------------------------------------------------------
    # ripgrep 搜索
    # ------------------------------------------------------------------

    async def _search_with_rg(self, inp: GrepInput, search_root: Path) -> dict:
        """使用 ripgrep 子进程搜索"""

        # 构建 rg 命令行参数
        cmd = ["rg"]

        # 输出格式
        if inp.output_mode == "files_with_matches":
            cmd.append("-l")
        elif inp.output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("--line-number")

        # 大小写
        if inp.case_insensitive:
            cmd.append("-i")

        # 递归深度
        if not inp.recursive:
            cmd.append("--max-depth=1")

        # glob 过滤
        if inp.glob:
            cmd.extend(["--glob", inp.glob])

        # 最大结果数
        cmd.extend(["-m", str(inp.head_limit)])

        # pattern 和 path
        cmd.append(inp.pattern)
        cmd.append(str(search_root))

        logger.info(f"[GrepTool] rg command: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0
            )

            # rg 返回码: 0=有匹配, 1=无匹配, 2+=错误
            if proc.returncode not in (0, 1):
                err_msg = stderr.decode(errors="replace").strip()
                logger.warning(f"[GrepTool] rg failed (rc={proc.returncode}): {err_msg}")
                # fallback 到 Python re
                flags = re.IGNORECASE if inp.case_insensitive else 0
                regex = re.compile(inp.pattern, flags)
                return self._search_with_re(inp, regex, search_root)

            output = stdout.decode(errors="replace")
            if not output.strip():
                return {
                    "matches": [],
                    "total": 0,
                    "pattern": inp.pattern,
                    "mode": inp.output_mode,
                    "engine": "ripgrep",
                }

            # 解析输出
            matches = self._parse_rg_output(output, inp.output_mode, search_root)
            return {
                "matches": matches,
                "total": len(matches),
                "pattern": inp.pattern,
                "mode": inp.output_mode,
                "engine": "ripgrep",
            }

        except asyncio.TimeoutError:
            logger.warning("[GrepTool] rg timeout, falling back to Python re")
            flags = re.IGNORECASE if inp.case_insensitive else 0
            regex = re.compile(inp.pattern, flags)
            return self._search_with_re(inp, regex, search_root)
        except FileNotFoundError:
            logger.warning("[GrepTool] rg not found, falling back to Python re")
            flags = re.IGNORECASE if inp.case_insensitive else 0
            regex = re.compile(inp.pattern, flags)
            return self._search_with_re(inp, regex, search_root)

    def _parse_rg_output(self, output: str, mode: str, search_root: Path) -> list[dict]:
        """解析 ripgrep 的标准输出"""
        matches: list[dict] = []

        if mode == "files_with_matches":
            for line in output.strip().splitlines():
                if line.strip():
                    matches.append({"file": self._relative_path(line.strip(), search_root)})

        elif mode == "count":
            for line in output.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split(":", 1)
                if len(parts) == 2:
                    matches.append({
                        "file": self._relative_path(parts[0], search_root),
                        "count": int(parts[1]),
                    })

        else:  # content mode: filepath:lineno:content
            for line in output.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "file": self._relative_path(parts[0], search_root),
                        "line": int(parts[1]),
                        "content": parts[2][:500],
                    })

        return matches[:200]  # 硬上限

    # ------------------------------------------------------------------
    # Python re 备用搜索
    # ------------------------------------------------------------------

    def _search_with_re(self, inp: GrepInput, regex: re.Pattern, search_root: Path) -> dict:
        """使用 Python re 模块搜索（rg 不可用时的备用方案）"""
        matches: list[dict] = []
        max_file_size = 1_000_000  # 跳过超过 1MB 的文件

        # 排除的目录名
        _EXCLUDED = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}

        # 选择遍历方式
        glob_pattern = inp.glob or "**/*"
        file_iter = search_root.glob(glob_pattern) if inp.recursive else search_root.glob(inp.glob or "*")

        for file_path in sorted(file_iter):
            if not file_path.is_file():
                continue

            # 跳过隐藏目录和排除目录
            parts = file_path.relative_to(search_root).parts
            if any(p.startswith(".") or p in _EXCLUDED for p in parts):
                continue

            # 跳过二进制和大文件
            try:
                if file_path.stat().st_size > max_file_size:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            file_matches: list[tuple[int, str]] = []
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    file_matches.append((i, line.strip()))

            if not file_matches:
                continue

            # 格式化结果
            if inp.output_mode == "files_with_matches":
                matches.append({"file": self._relative_path(str(file_path), search_root)})

            elif inp.output_mode == "count":
                matches.append({
                    "file": self._relative_path(str(file_path), search_root),
                    "count": len(file_matches),
                })

            else:  # content mode
                for line_no, line_text in file_matches[:50]:
                    matches.append({
                        "file": self._relative_path(str(file_path), search_root),
                        "line": line_no,
                        "content": line_text[:500],
                    })

            if len(matches) >= inp.head_limit:
                break

        return {
            "matches": matches[:200],
            "total": len(matches),
            "pattern": inp.pattern,
            "mode": inp.output_mode,
            "engine": "python_re",
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _relative_path(file_path_str: str, search_root: Path) -> str:
        """安全地将绝对路径转为相对路径，失败时返回原字符串。"""
        try:
            return str(Path(file_path_str).relative_to(search_root))
        except ValueError:
            return file_path_str

    # ------------------------------------------------------------------
    # 元信息方法
    # ------------------------------------------------------------------

    async def description(self, input_data: Optional[dict] = None, options: Optional[dict] = None) -> str:
        """返回针对当前输入的一行工具描述。"""
        pattern = (input_data or {}).get("pattern", "")
        return f"搜索: {pattern}" if pattern else "文件内容正则搜索"

    async def prompt(self, options: Optional[dict] = None) -> str:
        """返回工具的完整 prompt 模板。"""
        return (
            "在项目文件中搜索匹配正则表达式的内容。"
            "优先使用 ripgrep (rg) 以获得更好性能，不可用时自动降级到 Python re。"
            "支持三种输出模式: content（文件+行号+内容）、files_with_matches（仅文件名）、count（计数）。"
            "支持 glob 文件名过滤、大小写开关、递归深度控制。"
        )

    # ------------------------------------------------------------------
    # 只读 & 并发安全标记
    # ------------------------------------------------------------------

    def is_read_only(self, input_data: dict) -> bool:
        """搜索是纯只读操作，不会修改任何文件。"""
        return True

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """多个搜索操作之间互不影响，可以安全并发执行。"""
        return True
