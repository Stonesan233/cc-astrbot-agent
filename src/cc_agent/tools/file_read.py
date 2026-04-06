"""
FileReadTool — 文件读取

对应原版 src/tools/FileReadTool/
支持行号格式输出、offset/limit 分块读取，防止大文件一次性加载。
"""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .base import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    """FileReadTool 的输入参数"""
    path: str                                    # 文件路径（绝对路径或相对于 project_root）
    offset: Optional[int] = None                 # 起始行号（0-based），不传则从第 0 行开始
    limit: Optional[int] = None                  # 最大读取行数，不传则使用默认值


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

_DEFAULT_LIMIT = 2000        # 单次最大读取行数
_MAX_FILE_BYTES = 20 * 1024 * 1024   # 拒绝读取超过 20 MB 的文件


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

class FileReadTool(BaseTool):
    """读取文件内容（支持行号、分块读取）"""

    name = "read_file"
    aliases = ["Read", "file_read"]
    search_hint = "read file contents"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self) -> type[BaseModel]:
        return FileReadInput

    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------

    async def call(
        self,
        args: dict,
        context: Optional[object] = None,
        on_progress: Optional[object] = None,
    ) -> ToolResult:
        """
        读取指定文件的内容。

        流程:
        1. 解析并校验输入
        2. 将相对路径解析为绝对路径
        3. 检查文件是否存在、是否为普通文件、是否过大
        4. 按行读取，根据 offset/limit 截取
        5. 格式化为 cat -n 风格（行号 + 内容）
        """
        # 解析输入
        try:
            inp = FileReadInput(**args)
        except Exception as e:
            return ToolResult(error=f"参数校验失败: {e}")

        # 解析路径：相对路径基于 project_root
        file_path = Path(inp.path)
        if not file_path.is_absolute():
            file_path = Path(self.project_root) / file_path

        # 存在性检查
        if not file_path.exists():
            return ToolResult(error=f"文件不存在: {file_path}")

        if not file_path.is_file():
            return ToolResult(error=f"目标不是文件: {file_path}")

        # 大小检查：防止将超大文件或二进制文件一次性读入内存
        try:
            file_size = file_path.stat().st_size
        except OSError as e:
            return ToolResult(error=f"无法获取文件信息: {e}")

        if file_size > _MAX_FILE_BYTES:
            return ToolResult(
                error=f"文件过大 ({file_size / 1024 / 1024:.1f} MB)，"
                      f"上限 {_MAX_FILE_BYTES / 1024 / 1024:.0f} MB。"
                      f"请使用 offset/limit 分块读取。"
            )

        # 读取文件内容
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(error=f"读取文件失败: {e}")

        lines = text.splitlines()
        total_lines = len(lines)

        # 计算 offset 和 limit
        offset = inp.offset if inp.offset is not None else 0
        limit = inp.limit if inp.limit is not None else _DEFAULT_LIMIT

        # 边界保护
        if offset < 0:
            offset = 0
        if offset > total_lines:
            offset = total_lines

        selected = lines[offset: offset + limit]

        # 格式化为 cat -n 风格: 右对齐行号 + tab + 内容
        numbered_lines: list[str] = []
        width = len(str(offset + len(selected)))  # 行号对齐宽度
        for i, line in enumerate(selected, start=offset + 1):
            numbered_lines.append(f"{i:>{width}}\t{line}")

        content = "\n".join(numbered_lines)
        shown_count = len(selected)

        return ToolResult(
            data={
                "content": content,
                "file_path": str(file_path),
                "total_lines": total_lines,
                "shown_range": f"{offset + 1}-{offset + shown_count}",
                "shown_count": shown_count,
            }
        )

    # ------------------------------------------------------------------
    # 元信息方法
    # ------------------------------------------------------------------

    async def description(self, input_data: Optional[dict] = None, options: Optional[dict] = None) -> str:
        """返回针对当前输入的一行工具描述。"""
        path = (input_data or {}).get("path", "")
        return f"读取文件: {path}" if path else "读取文件内容"

    async def prompt(self, options: Optional[dict] = None) -> str:
        """返回工具的完整 prompt 模板（用于注入 system prompt）。"""
        return (
            "读取指定文件的内容并以 cat -n 格式（行号 + 内容）返回。"
            "支持通过 offset 和 limit 分块读取大文件，避免一次性加载过多内容。"
            "路径可以是绝对路径或相对于项目根目录的相对路径。"
        )

    # ------------------------------------------------------------------
    # 只读 & 并发安全标记
    # ------------------------------------------------------------------

    def is_read_only(self, input_data: dict) -> bool:
        """文件读取是纯只读操作，不会修改任何文件。"""
        return True

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """多个读取操作之间互不影响，可以安全并发执行。"""
        return True
