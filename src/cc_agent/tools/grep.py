"""
GrepTool — 文件内容搜索

对应原版 src/tools/GrepTool/
"""

import asyncio
import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from .base import BaseTool


class GrepInput(BaseModel):
    pattern: str
    path: Optional[str] = None
    glob: Optional[str] = None
    output_mode: str = "content"  # content | files_with_matches | count
    context: int = 0
    head_limit: int = 0


class GrepTool(BaseTool):
    """文件内容正则搜索"""

    name = "grep"
    aliases = ["Grep"]
    search_hint = "search file contents with regex"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return GrepInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = GrepInput(**args)
        search_root = Path(input_data.path) if input_data.path else Path(self.project_root)

        if not search_root.is_absolute():
            search_root = Path(self.project_root) / search_root

        try:
            regex = re.compile(input_data.pattern, re.IGNORECASE)
            glob_pattern = input_data.glob or "**/*"
            matches = []

            for file_path in search_root.glob(glob_pattern):
                if not file_path.is_file():
                    continue
                # 跳过二进制和大文件
                try:
                    if file_path.stat().st_size > 1_000_000:
                        continue
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except (PermissionError, OSError):
                    continue

                file_matches = []
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        file_matches.append((i, line.strip()))

                if file_matches:
                    if input_data.output_mode == "files_with_matches":
                        matches.append({"file": str(file_path.relative_to(search_root))})
                    elif input_data.output_mode == "count":
                        matches.append({"file": str(file_path.relative_to(search_root)), "count": len(file_matches)})
                    else:
                        for line_no, line_text in file_matches[:50]:
                            matches.append({
                                "file": str(file_path.relative_to(search_root)),
                                "line": line_no,
                                "content": line_text[:500],
                            })

                if input_data.head_limit and len(matches) >= input_data.head_limit:
                    break

            return {
                "matches": matches[:200],
                "total": len(matches),
                "pattern": input_data.pattern,
                "mode": input_data.output_mode,
            }
        except re.error as e:
            return {"matches": [], "total": 0, "error": f"Invalid regex: {e}"}
        except Exception as e:
            return {"matches": [], "total": 0, "error": str(e)}

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        pattern = (input_data or {}).get("pattern", "")
        return f"搜索: {pattern}" if pattern else "文件内容正则搜索"

    async def prompt(self, options: dict = None) -> str:
        return "Search file contents using regex. Supports glob filtering and multiple output modes."

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True

    def is_read_only(self, input_data: dict) -> bool:
        return True
