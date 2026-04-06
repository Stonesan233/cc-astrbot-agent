"""
FileReadTool — 文件读取

对应原版 src/tools/FileReadTool/
"""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from .base import BaseTool


class FileReadInput(BaseModel):
    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = 2000


class FileReadTool(BaseTool):
    """读取文件内容（支持行偏移和限制）"""

    name = "file_read"
    aliases = ["Read"]
    search_hint = "read file contents"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return FileReadInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = FileReadInput(**args)
        file_path = Path(input_data.file_path)

        # 支持相对路径
        if not file_path.is_absolute():
            file_path = Path(self.project_root) / file_path

        if not file_path.exists():
            return {"content": "", "error": f"File not found: {file_path}"}
        if not file_path.is_file():
            return {"content": "", "error": f"Not a file: {file_path}"}

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()

            offset = input_data.offset or 0
            limit = input_data.limit or 2000

            selected = lines[offset : offset + limit]

            # 格式化为 cat -n 风格
            numbered = []
            for i, line in enumerate(selected, start=offset + 1):
                numbered.append(f"     {i}\t{line}")

            return {
                "content": "\n".join(numbered),
                "total_lines": len(lines),
                "shown_lines": f"{offset + 1}-{offset + len(selected)}",
                "file_path": str(file_path),
            }
        except Exception as e:
            return {"content": "", "error": str(e)}

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        path = (input_data or {}).get("file_path", "")
        return f"读取文件: {path}" if path else "读取文件内容"

    async def prompt(self, options: dict = None) -> str:
        return "Read file contents with line numbers. Supports offset/limit for large files."

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True

    def is_read_only(self, input_data: dict) -> bool:
        return True
