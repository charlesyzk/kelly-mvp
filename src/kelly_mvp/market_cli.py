"""Command-line management for the local market-data database."""

from __future__ import annotations

import argparse
import json
import sys

from .config import FREQUENCIES
from .market_jobs import create_fetch_job, pause_fetch_job, resume_fetch_job, run_fetch_job
from .market_store import MarketStore
from .universes import fetch_universe_definitions, import_universes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local EODHD universes and price history")
    parser.add_argument("--db", help="SQLite database path (defaults to data/market_data.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-universes", help="fetch and snapshot the five configured user lists")
    imp.add_argument("--html-dir", help="directory containing the five supplied HTML files")
    fetch = sub.add_parser("fetch", help="fetch full/incremental daily, weekly and monthly history")
    fetch.add_argument("--collection", action="append", dest="collections", help="collection id; repeatable, defaults to all")
    fetch.add_argument("--frequency", action="append", choices=FREQUENCIES, help="frequency; repeatable, defaults to all")
    fetch.add_argument("--batch-size", type=int, default=30, help="maximum EOD requests per quota checkpoint")
    fetch.add_argument("--exchange", help="restrict to one EODHD exchange suffix, e.g. US, SHE, SHG or INDX")
    fetch.add_argument("--symbols", help="comma-separated code subset, limited to selected collections")
    status = sub.add_parser("status", help="show collections and recent data jobs")
    status.add_argument("--job-id")
    resume = sub.add_parser("resume", help="resume pending or failed items in a job")
    resume.add_argument("job_id")
    resume.add_argument("--batch-size", type=int, default=30)
    pause = sub.add_parser("pause", help="pause a running job after its current request")
    pause.add_argument("job_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    store = MarketStore(args.db)
    try:
        if args.command == "import-universes":
            definitions = fetch_universe_definitions(html_dir=args.html_dir)
            result = import_universes(store, definitions)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            if args.job_id:
                result = store.job_detail(args.job_id)
                if not result:
                    print("找不到该任务", file=sys.stderr)
                    return 2
            else:
                result = {"collections": store.collection_catalog(), "jobs": store.list_jobs()}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "fetch":
            collections = args.collections or [str(row["id"]) for row in store.collection_catalog()]
            symbols = [s for s in (args.symbols or "").split(",") if s.strip()] if args.symbols else None
            job_id = create_fetch_job(store, collections, args.frequency or list(FREQUENCIES), symbols,
                                      exchange_filter=args.exchange or "")
            print(f"任务已创建：{job_id}")
            detail = run_fetch_job(store, job_id, batch_size=max(1, args.batch_size), progress=print)
            print(json.dumps(detail["job"], ensure_ascii=False, indent=2))
            return 0 if detail["job"]["status"] in {"completed", "completed_with_issues"} else 2
        if args.command == "resume":
            detail = store.job_detail(args.job_id)
            if not detail:
                print("找不到该任务", file=sys.stderr)
                return 2
            resume_fetch_job(store, args.job_id, batch_size=max(1, args.batch_size), run_async=False)
            detail = run_fetch_job(store, args.job_id, batch_size=max(1, args.batch_size), progress=print)
            print(json.dumps(detail["job"], ensure_ascii=False, indent=2))
            return 0 if detail["job"]["status"] in {"completed", "completed_with_issues"} else 2
        if args.command == "pause":
            pause_fetch_job(store, args.job_id)
            return 0
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"数据管理错误：{exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
