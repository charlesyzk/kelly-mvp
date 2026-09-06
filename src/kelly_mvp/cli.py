"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import run_backtest
from .config import StrategyConfig
from .data import load_daily_prices
from .eodhd import fetch_daily_prices
from .module_strategy import BUILTIN_STRATEGY_IDS, get_builtin_strategy, load_user_strategy
from .report import write_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rolling-60 pluggable strategy research backtest")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="CSV: date,symbol,adjusted_close")
    source.add_argument("--eodhd-symbol", help="download daily prices, for example 300308.SHE")
    parser.add_argument("--eodhd-from", help="EODHD start date: YYYY-MM-DD")
    parser.add_argument("--eodhd-to", help="EODHD end date; defaults to today")
    parser.add_argument("--output", required=True, help="new output directory")
    parser.add_argument("--config", help="optional JSON configuration")
    parser.add_argument("--cost-bps", type=float, help="override one-way turnover cost in bps")
    strategy = parser.add_mutually_exclusive_group()
    strategy.add_argument(
        "--strategy",
        choices=BUILTIN_STRATEGY_IDS,
        default="M4_SIMPLE",
        help="built-in strategy model (default: M4_SIMPLE)",
    )
    strategy.add_argument("--strategy-file", help="trusted Python strategy following the template")
    return parser


def _config(args: argparse.Namespace) -> StrategyConfig:
    values: dict[str, object] = {}
    if args.config:
        values = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.cost_bps is not None:
        values["transaction_cost_bps"] = args.cost_bps
    return StrategyConfig(**values)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.eodhd_symbol and not args.eodhd_from:
        parser.error("--eodhd-symbol requires --eodhd-from")
    try:
        rows = (
            fetch_daily_prices(args.eodhd_symbol, args.eodhd_from, args.eodhd_to)
            if args.eodhd_symbol
            else load_daily_prices(args.input)
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Data error: {exc}")
        return 2
    try:
        selected_strategy = (
            load_user_strategy(
                Path(args.strategy_file).read_text(encoding="utf-8"),
                Path(args.strategy_file).name,
            )
            if args.strategy_file else get_builtin_strategy(args.strategy)
        )
    except (FileNotFoundError, UnicodeError, ValueError) as exc:
        print(f"Strategy error: {exc}")
        return 2
    result = run_backtest(rows, _config(args), selected_strategy)
    if not result.periods:
        print("No evaluable periods were produced.")
        for issue in result.issues:
            print(f"- {issue}")
        return 2
    paths = write_outputs(result, args.output)
    print(f"Completed {selected_strategy.name}: {len(result.periods)} period evaluations")
    for path in paths:
        print(path.resolve())
    if result.issues:
        print("Data sufficiency warnings:")
        for issue in result.issues:
            print(f"- {issue}")
    return 0
