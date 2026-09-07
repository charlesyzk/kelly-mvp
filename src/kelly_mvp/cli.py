"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest import run_backtest
from .config import StrategyConfig
from .data import load_daily_prices, load_price_workbook
from .eodhd import fetch_price_bundle
from .report import write_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Six-model rolling Kelly research validation")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="daily CSV or XLSX with daily/weekly/monthly sheets")
    source.add_argument("--eodhd-symbol", help="download provider daily/weekly/monthly prices")
    parser.add_argument("--eodhd-from", help="EODHD start date: YYYY-MM-DD")
    parser.add_argument("--eodhd-to", help="EODHD end date; defaults to today")
    parser.add_argument("--output", required=True, help="new output directory")
    parser.add_argument("--config", help="optional JSON configuration")
    parser.add_argument("--kappa", type=float, help="override the pre-registered SAFE parameter")
    return parser


def _config(args: argparse.Namespace) -> StrategyConfig:
    values: dict[str, object] = {}
    if args.config:
        values = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.kappa is not None:
        values["convergence_kappa"] = args.kappa
    return StrategyConfig(**values)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.eodhd_symbol and not args.eodhd_from:
        parser.error("--eodhd-symbol requires --eodhd-from")
    try:
        config = _config(args)
        if args.eodhd_symbol:
            rows = fetch_price_bundle(args.eodhd_symbol, args.eodhd_from, args.eodhd_to)
        else:
            source = Path(args.input)
            rows = load_price_workbook(source) if source.suffix.lower() == ".xlsx" else load_daily_prices(source)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Data error: {exc}")
        return 2
    result = run_backtest(rows, config)
    if not result.periods:
        print("No evaluable periods were produced.")
        for issue in result.issues:
            print(f"- {issue}")
        return 2
    paths = write_outputs(result, args.output, config)
    print(f"Completed: {len(result.periods)} period evaluations")
    for path in paths:
        print(path.resolve())
    if result.issues:
        print("Data sufficiency warnings:")
        for issue in result.issues:
            print(f"- {issue}")
    return 0
