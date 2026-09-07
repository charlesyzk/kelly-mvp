"""Six-model rolling Kelly research framework."""

from .backtest import BacktestResult, SignalResult, TradeResult, classify_trade, run_backtest
from .config import StrategyConfig
from .data import PriceRow, load_daily_prices, load_price_workbook, parse_daily_prices, parse_price_workbook
from .eodhd import fetch_daily_prices, fetch_price_bundle
from .statistics import compare_models

__all__ = [
    "BacktestResult",
    "PriceRow",
    "SignalResult",
    "StrategyConfig",
    "TradeResult",
    "classify_trade",
    "compare_models",
    "fetch_daily_prices",
    "fetch_price_bundle",
    "load_daily_prices",
    "load_price_workbook",
    "parse_daily_prices",
    "parse_price_workbook",
    "run_backtest",
]
