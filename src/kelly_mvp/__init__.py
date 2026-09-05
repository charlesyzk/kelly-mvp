"""Minimal rolling fourth-moment Kelly research framework."""

from .backtest import BacktestResult, run_backtest
from .config import StrategyConfig
from .data import PriceRow, load_daily_prices, parse_daily_prices
from .eodhd import fetch_daily_prices

__all__ = [
    "BacktestResult",
    "PriceRow",
    "StrategyConfig",
    "fetch_daily_prices",
    "load_daily_prices",
    "parse_daily_prices",
    "run_backtest",
]
