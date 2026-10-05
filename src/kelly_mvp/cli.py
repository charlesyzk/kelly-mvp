"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .backtest import run_backtest
from .config import StrategyConfig
from .data import load_daily_prices, load_price_workbook
from .eodhd import fetch_account_usage, fetch_exchange_symbols, fetch_price_bundle
from .market_jobs import _api_symbol_key, _quota_remaining, create_fetch_job, run_fetch_job
from .market_store import MarketStore
from .module_strategy import KELLY_STRATEGY_ID, get_builtin_strategy, load_user_strategy
from .report import write_outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Six-model rolling Kelly research validation")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="daily CSV or XLSX with daily/weekly/monthly sheets")
    source.add_argument("--eodhd-symbol", help="download provider daily/weekly/monthly prices")
    source.add_argument("--market-data", action="store_true", help="read selected codes from the shared SQLite market database")
    parser.add_argument("--market-collection", action="append", help="market-data collection id; repeatable")
    parser.add_argument("--market-symbol", action="append", help="limit market-data run to a code; repeatable")
    parser.add_argument("--market-db", help="SQLite market database path")
    parser.add_argument("--eodhd-from", help="EODHD start date: YYYY-MM-DD")
    parser.add_argument("--eodhd-to", help="EODHD end date; defaults to today")
    parser.add_argument("--output", required=True, help="new output directory")
    parser.add_argument("--config", help="optional JSON configuration")
    parser.add_argument("--kappa", type=float, help="override the pre-registered SAFE parameter")
    parser.add_argument("--strategy-file", help="trusted Python strategy implementing the upload template")
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
        if args.market_data:
            if not args.market_collection:
                parser.error("--market-data requires at least one --market-collection")
            store = MarketStore(args.market_db)
            symbols = store.collection_symbols(args.market_collection)
            if args.market_symbol:
                selected = set(s.strip().upper() for s in args.market_symbol)
                symbols = [symbol for symbol in symbols if symbol in selected]
            if not symbols:
                raise ValueError("所选清单和代码筛选没有匹配标的")
            missing = [symbol for symbol in symbols if any(not store.get_price_series(symbol, frequency)
                                                            for frequency in ("daily", "weekly", "monthly"))]
            if missing:
                job_id = create_fetch_job(store, args.market_collection, symbols_filter=missing)
                detail = run_fetch_job(store, job_id, progress=print)
                if detail["job"]["status"] != "completed":
                    raise RuntimeError(f"本地补数任务暂停或有问题：{detail['job']['status']}；{detail['job']['message']}")
            rows = {frequency: [row for symbol in symbols for row in store.get_price_series(symbol, frequency)]
                    for frequency in ("daily", "weekly", "monthly")}
        elif args.eodhd_symbol:
            store = MarketStore(args.market_db)
            symbol = args.eodhd_symbol.strip().upper()
            requested_to = args.eodhd_to or date.today().isoformat()
            cached = {frequency: [row for row in store.get_price_series(symbol, frequency)
                                  if args.eodhd_from <= row.date.isoformat() <= requested_to]
                      for frequency in ("daily", "weekly", "monthly")}
            if all(store.has_coverage(symbol, frequency, args.eodhd_from, requested_to) and cached[frequency]
                   for frequency in cached):
                rows = cached
            else:
                usage = fetch_account_usage()
                store.add_usage_snapshot(usage)
                remaining = _quota_remaining(usage)
                if remaining is None:
                    raise ValueError("无法读取 EODHD 当前额度，已停止行情请求")
                if "." in symbol:
                    exchange = symbol.rsplit(".", 1)[-1]
                    directory = store.exchange_directory(exchange)
                    if directory is None:
                        if remaining < 4:
                            raise ValueError("剩余额度不足以校验代码并拉取三种频率")
                        directory = fetch_exchange_symbols(exchange)
                        store.save_exchange_directory(exchange, directory)
                        usage = fetch_account_usage()
                        store.add_usage_snapshot(usage)
                        remaining = _quota_remaining(usage)
                    if _api_symbol_key(symbol) not in {_api_symbol_key(code) for code in directory}:
                        raise ValueError("EODHD交易所代码目录未返回该代码，已阻止计费行情请求")
                if remaining is None or remaining < 3:
                    raise ValueError("剩余日额度不足以拉取日、周、月三种频率")
                rows = fetch_price_bundle(symbol, args.eodhd_from, args.eodhd_to)
                store.ensure_instrument(symbol, asset_type="index" if symbol.endswith(".INDX") else "unknown")
                for frequency, series in rows.items():
                    store.upsert_prices(series, frequency)
                    store.record_coverage(symbol, frequency, args.eodhd_from, requested_to, series)
                store.add_usage_snapshot(fetch_account_usage())
        else:
            source = Path(args.input)
            rows = load_price_workbook(source) if source.suffix.lower() == ".xlsx" else load_daily_prices(source)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Data error: {exc}")
        return 2
    strategy = get_builtin_strategy(KELLY_STRATEGY_ID)
    if args.strategy_file:
        source = Path(args.strategy_file)
        try:
            strategy = load_user_strategy(source.read_text(encoding="utf-8"), source.name)
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
            print(f"Strategy error: {exc}")
            return 2
    result = run_backtest(rows, config, strategy)
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
