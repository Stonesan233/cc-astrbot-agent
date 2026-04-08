"""
ScanProjectTool — 项目扫描工具

支持深度限制、排除目录、文件数量限制，防止扫描大目录时卡死。
"""

import logging
import os
from pathlib import Path
from pydantic import BaseModel
from .base import BaseTool

logger = logging.getLogger(__name__)

# 默认排除的目录名
_EXCLUDED_DIRS = {
    ".", "..", "__pycache__", "node_modules", ".git", ".svn", ".hg",
    ".venv", "venv", ".env", ".idea", ".vscode", ".claude",
    "dist", "build", ".next", ".nuxt", "coverage",
    "site-packages", ".tox", ".mypy_cache", ".pytest_cache",
}


class ScanProjectInput(BaseModel):
    path: str = "."
    max_depth: int = 3


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
        max_depth = input_data.max_depth

        logger.info(
            f"[ScanProject] Scanning: {scan_path} | max_depth={max_depth}"
        )

        if not scan_path.exists():
            return {"error": f"路径不存在: {scan_path}"}

        files = []
        dirs = []
        max_files = 200
        max_dirs = 50
        total_scanned = 0

        try:
            for entry in scan_path.rglob("*"):
                total_scanned += 1
                rel = entry.relative_to(scan_path)
                parts = rel.parts

                # 深度限制
                if len(parts) > max_depth:
                    continue

                # 跳过排除的目录
                if any(p in _EXCLUDED_DIRS or p.startswith(".") for p in parts):
                    continue

                if entry.is_file():
                    if len(files) < max_files:
                        files.append(str(rel))
                elif entry.is_dir():
                    if len(dirs) < max_dirs:
                        dirs.append(str(rel))

                # 每 1000 个条目检查一次，超过 5000 就提前退出
                if total_scanned > 5000:
                    logger.warning(
                        f"[ScanProject] Hit entry limit (5000), stopping early"
                    )
                    break

        except PermissionError:
            logger.warning(f"[ScanProject] Permission denied: {scan_path}")
        except Exception as e:
            logger.error(f"[ScanProject] Error: {e}")
            return {"error": str(e)}

        logger.info(
            f"[ScanProject] Done | scanned={total_scanned} | "
            f"files={len(files)} | dirs={len(dirs)}"
        )

        return {
            "files": sorted(files),
            "dirs": sorted(dirs),
            "root": str(scan_path),
            "scanned": total_scanned,
        }

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        return "扫描项目目录结构，返回文件和目录列表"

    async def prompt(self, options: dict = None) -> str:
        return "扫描项目目录结构，返回文件和目录列表。"

    def is_read_only(self, input_data: dict) -> bool:
        return True

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True
