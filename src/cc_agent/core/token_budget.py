"""
BudgetTracker — Token 预算追踪

对应原版 src/query/tokenBudget.ts
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetTracker:
    """跨循环迭代的 token 预算追踪器"""

    continuation_count: int = 0
    last_delta_tokens: int = 0
    last_global_turn_tokens: int = 0
    started_at: float = 0.0


@dataclass
class TokenBudgetDecision:
    """Token 预算决策结果"""

    action: str  # "continue" | "stop"
    nudge_message: Optional[str] = None
    continuation_count: int = 0
    pct: int = 0
    turn_tokens: int = 0
    budget: int = 0


COMPLETION_THRESHOLD = 0.9
DIMINISHING_THRESHOLD = 500


def check_token_budget(
    tracker: BudgetTracker,
    agent_id: Optional[str],
    budget: Optional[int],
    global_turn_tokens: int,
) -> TokenBudgetDecision:
    """
    检查 token 预算，决定继续还是停止

    对应原版 checkTokenBudget()
    """
    if agent_id or budget is None or budget <= 0:
        return TokenBudgetDecision(action="stop")

    turn_tokens = global_turn_tokens
    pct = round(turn_tokens / budget * 100)
    delta_since_last_check = global_turn_tokens - tracker.last_global_turnTokens

    is_diminishing = (
        tracker.continuation_count >= 3
        and delta_since_last_check < DIMINISHING_THRESHOLD
        and tracker.last_delta_tokens < DIMINISHING_THRESHOLD
    )

    if not is_diminishing and turn_tokens < budget * COMPLETION_THRESHOLD:
        tracker.continuation_count += 1
        tracker.last_delta_tokens = delta_since_last_check
        tracker.last_global_turn_tokens = global_turn_tokens
        return TokenBudgetDecision(
            action="continue",
            nudge_message=f"Token usage: {pct}% ({turn_tokens}/{budget}). Continuing...",
            continuation_count=tracker.continuation_count,
            pct=pct,
            turn_tokens=turn_tokens,
            budget=budget,
        )

    return TokenBudgetDecision(action="stop", pct=pct, turn_tokens=turn_tokens, budget=budget)
