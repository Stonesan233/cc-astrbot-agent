"""
ScanProjectTool — 项目扫描工具

支持深度限制、排除目录、文件大小过滤、可配置条目上限，
防止扫描大目录时卡死。提前停止时返回清晰提示。
"""

import logging
import os
from pathlib import Path
from typing import Optional, Callable
from pydantic import BaseModel, Field
from .base import BaseTool

logger = logging.getLogger(__name__)

# 默认排除的目录名
EXCLUDED_DIRS = {
    ".", "..", "__pycache__", "node_modules", ".git", ".svn", ".hg",
    ".venv", "venv", ".env", ".idea", ".vscode", ".claude",
    "dist", "build", ".next", ".nuxt", "coverage",
    "site-packages", ".tox", ".mypy_cache", ".pytest_cache",
    ".cache", ".gradle", ".mvn", "target", "vendor", "Pods",
    ".terraform", ".serverless", ".webpack", ".rollup.cache",
}

# 默认排除的文件扩展名（二进制 / 生成产物）
EXCLUDED_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".obj", ".o",
    ".class", ".jar", ".war", ".egg", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".wav",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3",
    ".lock", ".min.js", ".min.css",
}

# 默认跳过的单个文件大小上限 (1 MB)
DEFAULT_MAX_FILE_SIZE = 1 * 1024 * 1024

# 默认条目扫描上限
DEFAULT_ENTRY_LIMIT = 20000

# 进度报告间隔
PROGRESS_INTERVAL = 1000


class ScanProjectInput(BaseModel):
    path: str = "."
    max_depth: int = Field(default=5, ge=1, le=20, description="递归深度限制")
    entry_limit: int = Field(
        default=DEFAULT_ENTRY_LIMIT,
        ge=1000,
        le=50000,
        description="扫描条目上限，超过后提前停止",
    )
    max_file_size: int = Field(
        default=DEFAULT_MAX_FILE_SIZE,
        ge=0,
        description="单文件大小上限（字节），超过则跳过",
    )


class ScanProjectTool(BaseTool):
    """扫描项目目录结构"""

    name = "scan_project"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self):
        return ScanProjectInput

    async def call(
        self,
        args: dict,
        context=None,
        on_progress: Optional[Callable] = None,
    ) -> dict:
        input_data = ScanProjectInput(**args)
        scan_path = Path(self.project_root) / input_data.path
        max_depth = input_data.max_depth
        entry_limit = input_data.entry_limit
        max_file_size = input_data.max_file_size

        logger.info(
            f"[ScanProject] Scanning: {scan_path} | "
            f"max_depth={max_depth} | entry_limit={entry_limit}"
        )

        if not scan_path.exists():
            return {"error": f"路径不存在: {scan_path}"}

        files: list[str] = []
        dirs: list[str] = []
        skipped_dirs: list[str] = []
        total_scanned = 0
        total_files = 0
        skipped_size = 0
        skipped_ext = 0
        truncated = False

        def _report_progress() -> None:
            if on_progress:
                on_progress(
                    f"[ScanProject] 已扫描 {total_scanned} 个条目，"
                    f"收集 {len(files)} 个文件，{len(dirs)} 个目录"
                )

        try:
            for entry in scan_path.rglob("*"):
                total_scanned += 1
                rel = entry.relative_to(scan_path)
                parts = rel.parts

                # ---- 深度限制 ----
                if len(parts) > max_depth:
                    continue

                # ---- 跳过排除的目录 ----
                skip = False
                for p in parts:
                    if p in EXCLUDED_DIRS or p.startswith("."):
                        # 记录被跳过的顶层排除目录（仅记录第一层）
                        if len(parts) == 1 and p not in (".", "..") and p not in skipped_dirs:
                            skipped_dirs.append(p)
                        skip = True
                        break
                if skip:
                    continue

                if entry.is_file():
                    total_files += 1

                    # ---- 文件扩展名过滤 ----
                    if entry.suffix.lower() in EXCLUDED_EXTENSIONS:
                        skipped_ext += 1
                        continue

                    # ---- 文件大小过滤 ----
                    try:
                        if max_file_size > 0 and entry.stat().st_size > max_file_size:
                            skipped_size += 1
                            continue
                    except OSError:
                        continue

                    files.append(str(rel))

                elif entry.is_dir():
                    if len(dirs) < entry_limit:
                        dirs.append(str(rel))

                # ---- 进度报告 ----
                if total_scanned % PROGRESS_INTERVAL == 0:
                    _report_progress()

                # ---- 条目上限检查 ----
                if total_scanned >= entry_limit:
                    truncated = True
                    logger.warning(
                        f"[ScanProject] Hit entry limit ({entry_limit}), stopping early"
                    )
                    break

        except PermissionError:
            logger.warning(f"[ScanProject] Permission denied: {scan_path}")
        except Exception as e:
            logger.error(f"[ScanProject] Error: {e}")
            return {"error": str(e)}

        # ---- 汇总日志 ----
        logger.info(
            f"[ScanProject] Done | scanned={total_scanned} | "
            f"files={len(files)} | dirs={len(dirs)} | "
            f"skipped_dirs={len(skipped_dirs)} | "
            f"skipped_by_size={skipped_size} | skipped_by_ext={skipped_ext}"
        )

        # ---- 构建返回结果 ----
        result = {
            "files": sorted(files),
            "dirs": sorted(dirs),
            "root": str(scan_path),
            "stats": {
                "total_entries_scanned": total_scanned,
                "total_files": total_files,
                "scanned_files": len(files),
                "scanned_dirs": len(dirs),
                "skipped_dirs": len(skipped_dirs),
                "skipped_by_size": skipped_size,
                "skipped_by_ext": skipped_ext,
            },
        }

        if truncated:
            result["warning"] = (
                f"项目过大，已扫描部分条目（共 {total_scanned} 个），"
                f"收集到 {len(files)} 个文件和 {len(dirs)} 个目录。"
                f"如需完整扫描，请增大 entry_limit 参数或缩小扫描路径。"
            )

        return result

    async def description(self, input_data: dict = None, options: dict = None) -> str:
        return "扫描项目目录结构，返回文件和目录列表"

    async def prompt(self, options: dict = None) -> str:
        return (
            "扫描项目目录结构，返回文件和目录列表。"
            "支持递归深度限制、排除目录、文件大小过滤。"
        )

    def is_read_only(self, input_data: dict) -> bool:
        return True

    def is_concurrency_safe(self, input_data: dict) -> bool:
        return True
