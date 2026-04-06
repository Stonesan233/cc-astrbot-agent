"""
ToolRegistry — 工具注册表

对应原版 Tool[] + findToolByName() + buildTool()
"""

from typing import Optional
from .base import BaseTool


class ToolNotFoundError(Exception):
    """工具未找到"""
    def __init__(self, name: str):
        super().__init__(f"Tool not found: {name}")
        self.tool_name = name


class ToolRegistry:
    """
    工具注册表，管理所有可用工具

    职责:
    1. 注册工具实例
    2. 按名称查找工具
    3. 列出所有可用工具
    4. 提供工具 schema 给 API
    """

    def __init__(self, project_root: str = "/app/project"):
        self._tools: dict[str, BaseTool] = {}
        self.project_root = project_root
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册 MVP 阶段的默认工具"""
        from .bash import BashTool
        from .file_read import FileReadTool
        from .file_edit import FileEditTool
        from .file_write import FileWriteTool
        from .glob import GlobTool
        from .grep import GrepTool
        from .scan_project import ScanProjectTool

        default_tools = [
            BashTool(project_root=self.project_root),
            FileReadTool(project_root=self.project_root),
            FileEditTool(project_root=self.project_root),
            FileWriteTool(project_root=self.project_root),
            GlobTool(project_root=self.project_root),
            GrepTool(project_root=self.project_root),
            ScanProjectTool(project_root=self.project_root),
        ]

        for tool in default_tools:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        """注册工具"""
        self._tools[tool.name] = tool
        for alias in getattr(tool, "aliases", []):
            self._tools[alias] = tool

    def get_tool(self, name: str) -> BaseTool:
        """按名称查找工具，未找到则抛异常"""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        return tool

    def find_tool(self, name: str) -> Optional[BaseTool]:
        """按名称查找工具，未找到返回 None"""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """列出所有已注册工具（去重，只取主名）"""
        seen = set()
        result = []
        for tool in self._tools.values():
            if tool.name not in seen:
                seen.add(tool.name)
                result.append(tool)
        return result

    def get_tools_schema(self) -> list[dict]:
        """获取所有工具的 API schema（用于发送给 Claude）"""
        schemas = []
        for tool in self.list_tools():
            if tool.is_enabled():
                schema = tool.input_schema.model_json_schema()
                schemas.append({
                    "name": tool.name,
                    "description": f"{tool.__class__.__doc__ or ''}".strip(),
                    "input_schema": schema,
                })
        return schemas
