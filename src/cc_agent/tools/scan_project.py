"""
ScanProjectTool — 项目扫描占位工具

MVP 阶段用于 agent.py 中的 scan_project() 调用
"""

import os
from pathlib import Path
from pydantic import BaseModel
from .base import BaseTool


class ScanProjectInput(BaseModel):
    path: str = "."


class ScanProjectTool(BaseTool):
    """扫描项目目录结构"""

    name = "scan_project"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return ScanProjectInput

    async def call(self, args: dict, context=None, on_progress=None) -> dict:
        input_data = ScanProjectInput(**args)
        scan_path = Path(self.project_root) / input_data.path

        files = []
        dirs = []
        try:
            for entry in scan_path.rglob("*"):
                rel = entry.relative_to(scan_path)
                # 跳过隐藏目录和 node_modules 等
                parts = rel.parts
                if any(p.startswith(".") for p in parts) or "node_modules" in parts or "__pycache__" in parts:
                    continue
                if entry.is_file():
                    files.append(str(rel))
                elif entry.is_dir():
                    dirs.append(str(rel))
        except PermissionError:
            pass

        return {
            "files": sorted(files)[:200],  # 最多 200 个
            "dirs": sorted(dirs)[:50],
            "root": str(scan_path),
        }

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        return "扫描项目目录结构，返回文件和目录列表"

    async def prompt(self, options: dict = None) -> str:
        return "扫描项目目录结构，返回文件和目录列表。"

    def is_read_only(self, input_data: dict) -> bool:
        return True

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True
