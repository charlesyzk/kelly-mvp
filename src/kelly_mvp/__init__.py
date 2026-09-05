"""Minimal rolling fourth-moment Kelly research framework."""

from .backtest import BacktestResult, SignalResult, TradeResult, run_backtest
from .config import StrategyConfig
from .data import PriceRow, load_daily_prices, parse_daily_prices
from .eodhd import fetch_daily_prices

__all__ = [
    "BacktestResult",
    "PriceRow",
    "SignalResult",
    "StrategyConfig",
    "TradeResult",
    "fetch_daily_prices",
    "load_daily_prices",
    "parse_daily_prices",
    "run_backtest",
]
