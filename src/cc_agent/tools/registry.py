"""
ToolRegistry — 工具注册表

对应原版 src/Tool.ts 中的 findToolByName() + tools 数组管理。
负责工具的注册、查找、列举、schema 导出。
"""

from __future__ import annotations

from typing import Optional

from .base import BaseTool


class ToolRegistry:
    """
    工具注册表（纯功能，不含人格逻辑）

    内部用 dict[str, BaseTool] 存储，key 同时包含主名和别名，
    因此同一个工具实例可能被多个 key 引用。去重由 _seen_names 集合保证。
    """

    def __init__(self, project_root: str = "/app/project"):
        self._tools: dict[str, BaseTool] = {}
        self._seen_names: set[str] = set()          # 已注册的工具主名（去重用）
        self.project_root: str = project_root
        self._register_default_tools()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register_tool(self, tool: BaseTool) -> None:
        """
        注册单个工具实例。

        工具的 name 作为主键存入 _tools，aliases 列表中的每一项
        也会作为额外 key 指向同一个实例。若 name 已存在则覆盖。
        """
        if not tool.name:
            raise ValueError("工具的 name 不能为空字符串")

        self._tools[tool.name] = tool
        self._seen_names.add(tool.name)

        # 别名也指向同一实例
        for alias in getattr(tool, "aliases", []):
            self._tools[alias] = tool

    def register_tools(self, tools: list[BaseTool]) -> None:
        """批量注册工具列表，遍历调用 register_tool。"""
        for tool in tools:
            self.register_tool(tool)

    def _register_default_tools(self) -> None:
        """注册 MVP 阶段的内置工具（延迟导入，避免循环依赖）。"""
        from .bash import BashTool
        from .file_read import FileReadTool
        from .file_edit import FileEditTool
        from .file_write import FileWriteTool
        from .glob import GlobTool
        from .grep import GrepTool
        from .patch import PatchTool
        from .scan_project import ScanProjectTool

        self.register_tools([
            BashTool(project_root=self.project_root),
            FileReadTool(project_root=self.project_root),
            FileEditTool(project_root=self.project_root),
            FileWriteTool(project_root=self.project_root),
            GlobTool(project_root=self.project_root),
            GrepTool(project_root=self.project_root),
            PatchTool(project_root=self.project_root),
            ScanProjectTool(project_root=self.project_root),
        ])

    # ------------------------------------------------------------------
    # 查找
    # ------------------------------------------------------------------

    def get_tool(self, name: str) -> BaseTool:
        """
        根据名称（主名或别名）获取工具实例。

        不存在时抛出 KeyError，附带可读的错误信息。
        """
        if name in self._tools:
            return self._tools[name]
        raise KeyError(f"工具未注册: '{name}'。可用工具: {sorted(self._seen_names)}")

    def find_tool(self, name: str) -> Optional[BaseTool]:
        """根据名称查找工具，不存在时返回 None（不抛异常）。"""
        return self._tools.get(name)

    # ------------------------------------------------------------------
    # 列举
    # ------------------------------------------------------------------

    def get_all_tools(self) -> list[BaseTool]:
        """
        返回所有已注册工具（去重，只保留每个工具的主名对应实例）。

        返回顺序与注册顺序一致。
        """
        result: list[BaseTool] = []
        for tool in self._tools.values():
            if tool.name not in {t.name for t in result}:
                result.append(tool)
        return result

    # 别名：兼容旧调用
    list_tools = get_all_tools

    # ------------------------------------------------------------------
    # Schema 导出（供 Claude API 调用使用）
    # ------------------------------------------------------------------

    def get_tools_schema(self) -> list[dict]:
        """
        生成所有已启用工具的 JSON Schema 列表。

        每项包含 name、description、input_schema 三个字段，
        可直接作为 tools 参数传给 Anthropic Messages API。
        """
        schemas: list[dict] = []
        for tool in self.get_all_tools():
            if tool.is_enabled():
                schemas.append({
                    "name": tool.name,
                    "description": (tool.__class__.__doc__ or "").strip(),
                    "input_schema": tool.input_schema.model_json_schema(),
                })
        return schemas

    # ------------------------------------------------------------------
    # 工具发现（预留）
    # ------------------------------------------------------------------

    def discover_tools(self) -> None:
        """
        自动发现并注册工具（预留接口）。

        后续实现:
        - 扫描 tools/ 目录下所有模块
        - 或从 MCP 服务器动态获取工具
        - 或从插件系统加载工具
        """
        pass
