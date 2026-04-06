"""
QueryConfig — 查询配置快照

对应原版 src/query/config.ts
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class QueryConfig:
    """单次查询的不可变配置"""

    session_id: str
    model: str = "claude-3-7-sonnet-20250219"
    max_tokens: int = 8192
    project_root: str = "/app/project"

    # Token 预算（对应原版 query/tokenBudget.ts）
    token_budget: Optional[int] = None
    max_turns: Optional[int] = None

    # 功能开关
    stream: bool = True
    verbose: bool = False
