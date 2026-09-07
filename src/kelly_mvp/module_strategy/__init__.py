"""Strategy registry and upload contract."""

from .contract import StrategyContext, StrategyDecision, StrategyDefinition
from .kelly import BUILTIN_STRATEGIES, BUILTIN_STRATEGY_IDS, KELLY_STRATEGY_ID
from .loader import load_user_strategy


def get_builtin_strategy(strategy_id: str) -> StrategyDefinition:
    try:
        return BUILTIN_STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown built-in strategy: {strategy_id}") from exc


def strategy_catalog() -> list[dict[str, object]]:
    return [BUILTIN_STRATEGIES[strategy_id].public_dict() for strategy_id in BUILTIN_STRATEGY_IDS]


__all__ = [
    "BUILTIN_STRATEGIES",
    "BUILTIN_STRATEGY_IDS",
    "KELLY_STRATEGY_ID",
    "StrategyContext",
    "StrategyDecision",
    "StrategyDefinition",
    "get_builtin_strategy",
    "load_user_strategy",
    "strategy_catalog",
]
