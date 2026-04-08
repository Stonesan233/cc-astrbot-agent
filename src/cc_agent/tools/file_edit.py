"""
FileEditTool — 文件编辑（精确字符串替换）

对应原版 src/tools/FileEditTool/
支持精确字符串匹配替换，自动检测多处匹配，生成 diff 预览。
内置敏感路径保护。
"""

import difflib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .base import BaseTool


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class FileEditInput(BaseModel):
    """FileEditTool 的输入参数"""
    path: str                    # 文件路径（绝对路径或相对于 project_root）
    old_string: str              # 要查找的原始字符串（精确匹配）
    new_string: str              # 替换后的新字符串
    replace_all: bool = False    # 是否替换所有匹配（默认只替换第一处）


# ---------------------------------------------------------------------------
# 安全策略：禁止修改的系统敏感路径前缀
# ---------------------------------------------------------------------------

_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/root",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/sbin",
    "/usr/sbin",
    "/lib",
    "/usr/lib",
    # Windows 系统目录
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
)


def _is_sensitive_path(path: Path) -> bool:
    """
    检查路径是否落在系统敏感目录下。跨平台兼容。
    """
    resolved = str(path.resolve()).replace("\\", "/")
    if len(resolved) >= 2 and resolved[1] == ":":
        resolved_no_drive = resolved[2:]
    else:
        resolved_no_drive = resolved

    for prefix in _BLOCKED_PREFIXES:
        if resolved.startswith(prefix) or resolved_no_drive == prefix or resolved_no_drive.startswith(prefix + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

class FileEditTool(BaseTool):
    """通过精确字符串替换编辑文件"""

    name = "edit_file"
    aliases = ["Edit", "file_edit"]
    search_hint = "edit file with string replacement"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self) -> type[BaseModel]:
        return FileEditInput

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
        精确字符串替换编辑文件。

        流程:
        1. 解析并校验输入
        2. 将相对路径解析为绝对路径
        3. 安全检查（敏感路径 + 路径穿越）
        4. 读取文件内容
        5. 查找 old_string（精确匹配）
        6. 处理多处匹配（要求 replace_all 或更精确的 old_string）
        7. 执行替换并写回文件
        8. 生成 diff 预览
        """
        # ---- 1. 解析输入 ----
        try:
            inp = FileEditInput(**args)
        except Exception as e:
            return {"error": f"参数校验失败: {e}"}

        # 空值检查
        if not inp.old_string:
            return {"error": "old_string 不能为空"}
        if inp.old_string == inp.new_string:
            return {"error": "old_string 和 new_string 相同，无需替换"}

        # ---- 2. 解析路径 ----
        file_path = Path(inp.path)
        was_relative = not file_path.is_absolute()
        if was_relative:
            file_path = Path(self.project_root) / file_path

        # ---- 3. 安全检查 ----

        # 禁止修改系统敏感路径
        if _is_sensitive_path(file_path):
            return {"error": f"安全限制: 禁止修改系统目录 {file_path}"}

        # 禁止路径穿越（相对路径逃逸出 project_root）
        if was_relative:
            try:
                resolved = file_path.resolve()
                root_resolved = Path(self.project_root).resolve()
                if not str(resolved).startswith(str(root_resolved)):
                    return {"error": "安全限制: 路径逃逸出项目根目录"}
            except Exception:
                pass

        # ---- 4. 存在性检查 ----
        if not file_path.exists():
            return {"error": f"文件不存在: {file_path}"}
        if not file_path.is_file():
            return {"error": f"目标不是文件: {file_path}"}

        # ---- 5. 读取文件 ----
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as e:
            return {"error": f"读取文件失败: {e}"}

        # ---- 6. 查找 old_string ----
        if inp.old_string not in content:
            # 提供文件前 500 字符作为上下文提示
            preview = content[:500] if len(content) > 500 else content
            return {
                "error": (
                    f"未找到 old_string。请确保提供的字符串与文件内容完全一致"
                    f"（包括缩进、空格、换行）。\n"
                    f"文件前 500 字符:\n{preview}"
                )
            }

        # 多处匹配检查：要求用户明确 replace_all 或提供更精确的 old_string
        count = content.count(inp.old_string)
        if count > 1 and not inp.replace_all:
            return {
                "error": (
                    f"在文件中找到 {count} 处匹配。"
                    f"请提供更具体的 old_string 以精确定位，"
                    f"或设置 replace_all=true 替换所有匹配。"
                )
            }

        # ---- 7. 执行替换 ----
        if inp.replace_all:
            new_content = content.replace(inp.old_string, inp.new_string)
            replacement_count = count
        else:
            new_content = content.replace(inp.old_string, inp.new_string, 1)
            replacement_count = 1

        # 写回文件
        try:
            file_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return {"error": f"写入文件失败: {e}"}

        # ---- 8. 生成 diff 预览 ----
        diff_preview = _generate_diff_preview(content, new_content, max_lines=20)

        return {
            "success": True,
            "file_path": str(file_path),
            "replacements": replacement_count,
            "diff_preview": diff_preview,
            "message": f"已编辑 {file_path}（替换 {replacement_count} 处）",
        }

    # ------------------------------------------------------------------
    # 元信息方法
    # ------------------------------------------------------------------

    async def description(self, input_data: Optional[dict] = None, options: Optional[dict] = None) -> str:
        """返回针对当前输入的一行工具描述。"""
        path = (input_data or {}).get("path", "")
        return f"编辑文件: {path}" if path else "编辑文件（精确字符串替换）"

    async def prompt(self, options: Optional[dict] = None) -> str:
        """返回工具的完整 prompt 模板。"""
        return (
            "通过精确字符串替换编辑文件。要求提供 old_string（原文）和 new_string（新文）。"
            "old_string 必须与文件内容精确匹配（包括缩进、空格、换行）。"
            "若存在多处匹配，需提供更具体的 old_string 或设置 replace_all=true。"
            "内置安全检查：禁止修改系统敏感路径。"
        )

    # ------------------------------------------------------------------
    # 只读 & 并发安全标记
    # ------------------------------------------------------------------

    def is_read_only(self, input_data: dict) -> bool:
        """文件编辑会修改文件内容，不是只读操作。"""
        return False

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """编辑操作之间可能冲突，必须串行执行。"""
        return False


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _generate_diff_preview(old_content: str, new_content: str, max_lines: int = 20) -> str:
    """
    生成简易 unified diff 预览（标注 -/+ 变更行）。

    使用 Python 标准库 difflib.unified_diff 生成。
    输出超过 max_lines 时截断并标注总数。
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=3)
    diff_lines = list(diff)

    if not diff_lines:
        return "（无变更）"

    if len(diff_lines) > max_lines:
        return "\n".join(diff_lines[:max_lines]) + f"\n... (共 {len(diff_lines)} 行 diff)"

    return "\n".join(diff_lines)
