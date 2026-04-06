"""
FileWriteTool — 文件写入（覆盖）

对应原版 src/tools/FileWriteTool/
"""

from pathlib import Path
from pydantic import BaseModel
from .base import BaseTool


class FileWriteInput(BaseModel):
    file_path: str
    content: str


class FileWriteTool(BaseTool):
    """写入文件（覆盖）"""

    name = "file_write"
    aliases = ["Write"]
    search_hint = "write file content"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return FileWriteInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = FileWriteInput(**args)
        file_path = Path(input_data.file_path)

        if not file_path.is_absolute():
            file_path = Path(self.project_root) / file_path

        try:
            # 自动创建父目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(input_data.content, encoding="utf-8")

            lines = input_data.content.count("\n") + 1
            return {
                "content": f"Successfully wrote {lines} lines to {file_path}",
                "file_path": str(file_path),
                "lines": lines,
            }
        except Exception as e:
            return {"content": "", "error": str(e)}

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        path = (input_data or {}).get("file_path", "")
        return f"写入文件: {path}" if path else "写入文件（覆盖）"

    async def prompt(self, options: dict = None) -> str:
        return "Write content to a file (overwrites existing). Creates parent directories as needed."

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return False

    def is_read_only(self, input_data: dict) -> bool:
        return False
