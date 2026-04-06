"""
FileWriteTool — 文件写入

对应原版 src/tools/FileWriteTool/
支持覆盖写入（"w"）和追加写入（"a"），自动创建父目录，内置敏感路径保护。
"""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .base import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# 输入 Schema
# ---------------------------------------------------------------------------

class FileWriteInput(BaseModel):
    """FileWriteTool 的输入参数"""
    path: str                                  # 文件路径（绝对路径或相对于 project_root）
    content: str                               # 要写入的内容
    mode: Optional[str] = "w"                  # 写入模式："w" 覆盖 / "a" 追加


# ---------------------------------------------------------------------------
# 安全策略：禁止写入的系统敏感路径前缀
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
    # Windows 系统目录（已用正斜号，因为 _is_sensitive_path 会先 replace \ → /）
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
)


def _is_sensitive_path(path: Path) -> bool:
    """
    检查路径是否落在系统敏感目录下。跨平台兼容。

    匹配逻辑：
    1. resolved 路径直接以某个前缀开头（Linux 绝对路径场景）
    2. resolved 路径去除盘符后以某个前缀开头（Windows /foo → C:\\foo 场景）
    """
    resolved = str(path.resolve()).replace("\\", "/")
    # 去掉 Windows 盘符（如 C:）以便匹配 POSIX 风格前缀
    if len(resolved) >= 2 and resolved[1] == ":":
        resolved_no_drive = resolved[2:]  # e.g. "/etc/passwd"
    else:
        resolved_no_drive = resolved

    for prefix in _BLOCKED_PREFIXES:
        if resolved.startswith(prefix) or resolved_no_drive == prefix or resolved_no_drive.startswith(prefix + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

class FileWriteTool(BaseTool):
    """写入文件内容（覆盖或追加）"""

    name = "write_file"
    aliases = ["Write", "file_write"]
    search_hint = "write file content"

    def __init__(self, project_root: str = "/app/project"):
        self.project_root = project_root

    @property
    def input_schema(self) -> type[BaseModel]:
        return FileWriteInput

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
        将内容写入指定文件。

        流程:
        1. 解析并校验输入
        2. 将相对路径解析为绝对路径
        3. 敏感路径安全检查
        4. 校验写入模式（只允许 "w" 或 "a"）
        5. 自动创建父目录
        6. 执行写入
        """
        # 解析输入
        try:
            inp = FileWriteInput(**args)
        except Exception as e:
            return ToolResult(error=f"参数校验失败: {e}")

        # 解析路径：相对路径基于 project_root
        file_path = Path(inp.path)
        was_relative = not file_path.is_absolute()
        if was_relative:
            file_path = Path(self.project_root) / file_path

        # ---- 安全检查 ----

        # 禁止写入系统敏感路径
        if _is_sensitive_path(file_path):
            return ToolResult(error=f"安全限制: 禁止写入系统目录 {file_path}")

        # 禁止路径穿越：相对路径解析后逃逸出 project_root 的场景
        # 注意：绝对路径（如用户显式指定的路径）不在此检查范围内
        if was_relative:
            try:
                resolved = file_path.resolve()
                root_resolved = Path(self.project_root).resolve()
                if not str(resolved).startswith(str(root_resolved)):
                    return ToolResult(
                        error=f"安全限制: 路径 {resolved} 逃逸出项目根目录 {root_resolved}"
                    )
            except Exception:
                pass  # resolve() 在某些极端情况下可能失败，不阻塞正常流程

        # 校验写入模式
        if inp.mode not in ("w", "a"):
            return ToolResult(error=f"不支持的写入模式 '{inp.mode}'，仅支持 'w'（覆盖）或 'a'（追加）")

        # ---- 执行写入 ----

        # 自动创建父目录
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(error=f"创建父目录失败: {e}")

        # 写入文件
        try:
            if inp.mode == "a":
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(inp.content)
            else:
                file_path.write_text(inp.content, encoding="utf-8")
        except OSError as e:
            return ToolResult(error=f"写入文件失败: {e}")

        # 统计信息
        lines = inp.content.count("\n") + (1 if inp.content and not inp.content.endswith("\n") else 0)
        mode_label = "追加" if inp.mode == "a" else "写入"

        return ToolResult(
            data={
                "file_path": str(file_path),
                "lines": lines,
                "chars": len(inp.content),
                "mode": inp.mode,
                "message": f"{mode_label}完成: {file_path} ({lines} 行, {len(inp.content)} 字符)",
            }
        )

    # ------------------------------------------------------------------
    # 元信息方法
    # ------------------------------------------------------------------

    async def description(self, input_data: Optional[dict] = None, options: Optional[dict] = None) -> str:
        """返回针对当前输入的一行工具描述。"""
        path = (input_data or {}).get("path", "")
        return f"写入文件: {path}" if path else "写入文件内容"

    async def prompt(self, options: Optional[dict] = None) -> str:
        """返回工具的完整 prompt 模板（用于注入 system prompt）。"""
        return (
            "将内容写入指定文件。默认覆盖写入（mode='w'），支持追加模式（mode='a'）。"
            "自动创建不存在的父目录。内置安全检查：禁止写入系统敏感路径和项目根目录以外的位置。"
            "路径可以是绝对路径或相对于项目根目录的相对路径。"
        )

    # ------------------------------------------------------------------
    # 只读 & 并发安全标记
    # ------------------------------------------------------------------

    def is_read_only(self, input_data: dict) -> bool:
        """文件写入会修改文件系统，不是只读操作。"""
        return False

    def is_concurrency_safe(self, input_data: dict) -> bool:
        """写入操作之间可能存在冲突，必须串行执行。"""
        return False
