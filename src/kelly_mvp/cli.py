"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import run_backtest
from .config import StrategyConfig
from .data import load_daily_prices
from .report import write_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rolling-60 M4 Kelly research backtest")
    parser.add_argument("--input", required=True, help="CSV: date,symbol,adjusted_close")
    parser.add_argument("--output", required=True, help="new output directory")
    parser.add_argument("--config", help="optional JSON configuration")
    parser.add_argument("--cost-bps", type=float, help="override one-way turnover cost in bps")
    return parser


def _config(args: argparse.Namespace) -> StrategyConfig:
    values: dict[str, object] = {}
    if args.config:
        values = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.cost_bps is not None:
        values["transaction_cost_bps"] = args.cost_bps
    return StrategyConfig(**values)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_backtest(load_daily_prices(args.input), _config(args))
    if not result.periods:
        print("No evaluable periods were produced.")
        for issue in result.issues:
            print(f"- {issue}")
        return 2
    paths = write_outputs(result, args.output)
    print(f"Completed: {len(result.periods)} period evaluations")
    for path in paths:
        print(path.resolve())
    if result.issues:
        print("Data sufficiency warnings:")
        for issue in result.issues:
            print(f"- {issue}")
    return 0
