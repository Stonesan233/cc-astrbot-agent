"""
GlobTool — 文件模式匹配搜索

对应原版 src/tools/GlobTool/
"""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from .base import BaseTool


class GlobInput(BaseModel):
    pattern: str = "**/*"
    path: Optional[str] = None


class GlobTool(BaseTool):
    """文件模式匹配搜索"""

    name = "glob"
    aliases = ["Glob"]
    search_hint = "find files by pattern"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return GlobInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = GlobInput(**args)
        search_root = Path(input_data.path) if input_data.path else Path(self.project_root)

        if not search_root.is_absolute():
            search_root = Path(self.project_root) / search_root

        try:
            matches = sorted(search_root.glob(input_data.pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            files = []
            for m in matches[:500]:  # 上限 500
                try:
                    rel = m.relative_to(search_root)
                    files.append(str(rel))
                except ValueError:
                    files.append(str(m))

            return {
                "files": files,
                "count": len(files),
                "pattern": input_data.pattern,
                "root": str(search_root),
            }
        except Exception as e:
            return {"files": [], "count": 0, "error": str(e)}

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        pattern = (input_data or {}).get("pattern", "")
        return f"文件搜索: {pattern}" if pattern else "文件模式匹配搜索"

    async def prompt(self, options: dict = None) -> str:
        return "Fast file pattern matching using glob patterns. Returns paths sorted by modification time."

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True

    def is_read_only(self, input_data: dict) -> bool:
        return True
