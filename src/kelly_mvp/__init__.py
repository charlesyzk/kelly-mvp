"""Minimal rolling fourth-moment Kelly research framework."""

from .backtest import BacktestResult, SignalResult, TradeResult, run_backtest
from .config import StrategyConfig
from .data import PriceRow, load_daily_prices, parse_daily_prices
from .eodhd import fetch_daily_prices
from .module_strategy import (
    BUILTIN_STRATEGY_IDS,
    StrategyContext,
    StrategyDefinition,
    get_builtin_strategy,
    load_user_strategy,
)

__all__ = [
    "BacktestResult",
    "BUILTIN_STRATEGY_IDS",
    "PriceRow",
    "SignalResult",
    "StrategyConfig",
    "StrategyContext",
    "StrategyDefinition",
    "TradeResult",
    "fetch_daily_prices",
    "get_builtin_strategy",
    "load_daily_prices",
    "load_user_strategy",
    "parse_daily_prices",
    "run_backtest",
]
