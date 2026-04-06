"""
FileEditTool — 文件编辑（精确字符串替换）

对应原版 src/tools/FileEditTool/
"""

from pathlib import Path
from pydantic import BaseModel
from .base import BaseTool


class FileEditInput(BaseModel):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class FileEditTool(BaseTool):
    """通过精确字符串替换编辑文件"""

    name = "file_edit"
    aliases = ["Edit"]
    search_hint = "edit file with string replacement"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return FileEditInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = FileEditInput(**args)
        file_path = Path(input_data.file_path)

        if not file_path.is_absolute():
            file_path = Path(self.project_root) / file_path

        if not file_path.exists():
            return {"content": "", "error": f"File not found: {file_path}"}

        try:
            content = file_path.read_text(encoding="utf-8")

            if input_data.old_string not in content:
                return {
                    "content": "",
                    "error": f"old_string not found in {file_path}. The exact string must match.",
                }

            count = content.count(input_data.old_string)
            if count > 1 and not input_data.replace_all:
                return {
                    "content": "",
                    "error": f"Found {count} occurrences of old_string. Use replace_all=true to replace all, or provide a more specific string.",
                }

            if input_data.replace_all:
                new_content = content.replace(input_data.old_string, input_data.new_string)
            else:
                new_content = content.replace(input_data.old_string, input_data.new_string, 1)

            file_path.write_text(new_content, encoding="utf-8")

            return {
                "content": f"Successfully edited {file_path}",
                "replacements": count if input_data.replace_all else 1,
                "file_path": str(file_path),
            }
        except Exception as e:
            return {"content": "", "error": str(e)}

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        path = (input_data or {}).get("file_path", "")
        return f"编辑文件: {path}" if path else "编辑文件（精确字符串替换）"

    async def prompt(self, options: dict = None) -> str:
        return "Edit files using exact string replacement. Requires old_string and new_string."

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return False

    def is_read_only(self, input_data: dict) -> bool:
        return False
