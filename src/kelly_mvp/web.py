"""Local-only web interface for the research engine."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import io
import json
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .backtest import run_backtest
from .config import StrategyConfig
from .data import daily_prices_to_csv, parse_daily_prices, parse_price_workbook
from .demo import generate_demo_csv
from .eodhd import fetch_account_usage, fetch_price_bundle
from .market_jobs import create_fetch_job, launch_fetch_job, pause_fetch_job, resume_fetch_job
from .market_store import MarketStore
from .universes import fetch_universe_definitions, import_universes
from .module_strategy import (
    KELLY_STRATEGY_ID,
    get_builtin_strategy,
    load_user_strategy,
    strategy_catalog,
)
from .statistics import compare_models


STATIC_DIR = Path(__file__).with_name("web_static")
STRATEGY_TEMPLATE = Path(__file__).with_name("module_strategy") / "user_strategy_template.py"
MAX_REQUEST_BYTES = 35 * 1024 * 1024
_MARKET_STORE: MarketStore | None = None


def market_store() -> MarketStore:
    global _MARKET_STORE
    if _MARKET_STORE is None:
        _MARKET_STORE = MarketStore()
    return _MARKET_STORE


def calculate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    workbook_b64 = payload.get("workbook_b64")
    series = payload.get("price_series")
    if isinstance(workbook_b64, str):
        try:
            prices = parse_price_workbook(base64.b64decode(workbook_b64, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Excel 文件无法读取：{exc}") from exc
    elif isinstance(series, dict):
        parsed = {
            frequency: parse_daily_prices(text)
            for frequency, text in series.items()
            if frequency in {"daily", "weekly", "monthly"} and isinstance(text, str)
        }
        if not parsed:
            raise ValueError("没有收到可用的分频行情")
        prices = parsed
    else:
        csv_text = payload.get("csv_text")
        if not isinstance(csv_text, str):
            raise ValueError("没有收到 CSV 文件内容")
        prices = parse_daily_prices(csv_text)
    return _calculate_prices(prices, payload)


def _calculate_prices(prices: dict[str, list[Any]] | list[Any], payload: dict[str, Any]) -> dict[str, Any]:
    config = StrategyConfig()
    strategy_id = payload.get("strategy_id", KELLY_STRATEGY_ID)
    if strategy_id == "uploaded":
        source = payload.get("strategy_source")
        if not isinstance(source, str):
            raise ValueError("请选择要上传的 Python 策略文件")
        strategy = load_user_strategy(
            source,
            str(payload.get("strategy_filename", "uploaded_strategy.py")),
        )
    elif strategy_id == KELLY_STRATEGY_ID:
        strategy = get_builtin_strategy(KELLY_STRATEGY_ID)
    else:
        raise ValueError("未知的顶层策略")
    result = run_backtest(prices, config, strategy)
    if not result.periods:
        detail = "；".join(result.issues) or "数据不足"
        raise ValueError(f"没有产生可评价结果：{detail}")
    return {
        "strategy": strategy.public_dict(),
        "config": {
            "windows": config.windows,
            "bounds": [config.lower_bound, config.upper_bound],
            "wealth_floor": config.wealth_floor,
            "ewma_half_lives": config.ewma_half_lives,
        },
        "summaries": [asdict(row) for row in result.summaries],
        "periods": [asdict(row) for row in result.periods],
        "signals": [asdict(row) for row in result.signals],
        "trades": [asdict(row) for row in result.trades],
        "statistics": (
            [asdict(row) for row in compare_models(result, config)]
            if strategy.kind == "builtin_kelly_suite"
            else []
        ),
        "issues": list(result.issues),
    }


def strategy_catalog_payload() -> dict[str, object]:
    return {"strategies": strategy_catalog()}


def fetch_eodhd_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = payload.get("symbol")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("请输入EODHD代码")
    if not isinstance(start_date, str) or not start_date.strip():
        raise ValueError("请选择开始日期")
    store = market_store()
    normalized = symbol.strip().upper()
    requested_to = end_date or date.today().isoformat()
    local_bundle = {frequency: [row for row in store.get_price_series(normalized, frequency)
                                if start_date <= row.date.isoformat() <= requested_to]
                    for frequency in ("daily", "weekly", "monthly")}
    use_local = all(store.has_coverage(normalized, frequency, start_date, requested_to)
                    and local_bundle[frequency] for frequency in local_bundle)
    if not use_local:
        usage = fetch_account_usage()
        store.add_usage_snapshot(usage)
        try:
            remaining = int(usage.get("dailyRateLimit", 0)) - int(usage.get("apiRequests", 0) or 0)
        except (TypeError, ValueError):
            remaining = 0
        usage_date = str(usage.get("apiRequestsDate", ""))[:10]
        if usage_date and usage_date != datetime.now(timezone.utc).date().isoformat():
            remaining = int(usage.get("dailyRateLimit", 0) or 0)
        if remaining < 3:
            raise ValueError("EODHD剩余日额度不足以完成日/周/月三次请求；请到行情管理页查看额度")
        bundle = fetch_price_bundle(symbol, start_date, end_date)
        daily = bundle["daily"]
        normalized = daily[0].symbol
        store.ensure_instrument(normalized, asset_type="index" if normalized.endswith(".INDX") else "unknown")
        for frequency, rows in bundle.items():
            store.upsert_prices(rows, frequency)
            store.record_coverage(normalized, frequency, start_date, requested_to, rows)
        source = "EODHD（已写入SQLite本地行情库）"
    else:
        bundle = local_bundle
        daily = bundle["daily"]
        source = "SQLite本地行情库"
    return {
        "price_series": {frequency: daily_prices_to_csv(rows) for frequency, rows in bundle.items()},
        "symbol": daily[0].symbol,
        "rows": {frequency: len(rows) for frequency, rows in bundle.items()},
        "first_date": daily[0].date,
        "last_date": daily[-1].date,
        "source": source,
    }


def market_catalog_payload(query: str = "", collections: list[str] | None = None,
                           store: MarketStore | None = None, exchange: str = "") -> dict[str, Any]:
    store = store or market_store()
    instruments = store.list_instruments(collections, query, exchange)
    symbols = [str(row["symbol"]) for row in instruments]
    return {
        "database_path": str(store.path),
        "collections": store.collection_catalog(),
        "instruments": instruments,
        "exchanges": store.list_exchanges(),
        "price_status": store.price_status(symbols),
        "jobs": store.list_jobs(10),
    }


def market_export_csv(store: MarketStore, query: str = "", collections: list[str] | None = None,
                      exchange: str = "") -> bytes:
    rows = store.list_instruments(collections, query, exchange)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    fields = ("symbol", "name", "exchange", "asset_type", "validation_status", "collection_ids", "collection_names")
    writer.writerow(fields)
    def spreadsheet_safe(value: object) -> object:
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
    writer.writerows([[spreadsheet_safe(row.get(key, "")) for key in fields] for row in rows])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _market_series_payload(store: MarketStore, symbols: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for frequency in ("daily", "weekly", "monthly"):
        rows = [row for symbol in symbols for row in store.get_price_series(symbol, frequency)]
        if rows:
            output[frequency] = daily_prices_to_csv(sorted(rows, key=lambda row: (row.symbol, row.date)))
    return output


def research_market_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) for s in symbols):
        raise ValueError("请选择一个或多个本地清单代码")
    symbols = sorted(set(s.strip().upper() for s in symbols if s.strip()))
    store = market_store()
    known = store.list_instruments()
    by_symbol = {str(row["symbol"]): row for row in known}
    unknown = [symbol for symbol in symbols if symbol not in by_symbol]
    if unknown:
        raise ValueError(f"代码未导入清单：{', '.join(unknown[:10])}")
    unlisted = [symbol for symbol in symbols if by_symbol[symbol]["validation_status"] == "not_listed"]
    if unlisted:
        raise ValueError(f"EODHD交易所目录未确认这些代码，已跳过计费请求：{', '.join(unlisted[:10])}")
    missing = [symbol for symbol in symbols if any(not store.get_price_series(symbol, frequency)
                                                    for frequency in ("daily", "weekly", "monthly"))]
    if missing:
        collection_ids = sorted({collection_id for symbol in missing
                                 for collection_id in str(by_symbol[symbol].get("collection_ids") or "").split(",")
                                 if collection_id})
        job_id = create_fetch_job(store, collection_ids, symbols_filter=missing)
        launch_fetch_job(store, job_id)
        return {"status": "fetching", "job_id": job_id, "symbols": missing,
                "message": "本地分频数据不完整，已创建补数任务。"}
    prices = {frequency: [row for symbol in symbols for row in store.get_price_series(symbol, frequency)]
              for frequency in ("daily", "weekly", "monthly")}
    result = _calculate_prices(prices, payload)
    result["data_source"] = {"type": "SQLite", "symbols": symbols, "source": "SQLite本地行情库"}
    return result


class KellyRequestHandler(BaseHTTPRequestHandler):
    server_version = "StrategyLab/0.6"

    def _send_bytes(self, status: int, content_type: str, body: bytes,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=lambda item: item.isoformat() if isinstance(item, date) else str(item),
        ).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        routes = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/data": ("data.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/data.js": ("data.js", "text/javascript; charset=utf-8"),
            "/data.css": ("data.css", "text/css; charset=utf-8"),
        }
        if path == "/api/eodhd/status":
            self._send_json(
                HTTPStatus.OK,
                {"configured": bool(os.getenv("EODHD_API_TOKEN", "").strip())},
            )
            return
        if path == "/api/strategies":
            self._send_json(HTTPStatus.OK, strategy_catalog_payload())
            return
        if path == "/strategy-template.py":
            self._send_bytes(
                HTTPStatus.OK,
                "text/x-python; charset=utf-8",
                STRATEGY_TEMPLATE.read_bytes(),
            )
            return
        if path == "/demo.csv":
            self._send_bytes(
                HTTPStatus.OK,
                "text/csv; charset=utf-8",
                generate_demo_csv().encode("utf-8"),
            )
            return
        if path == "/api/market-data/catalog":
            params = parse_qs(parsed.query)
            selected = [s for s in params.get("collection", []) if s]
            self._send_json(HTTPStatus.OK, market_catalog_payload(
                params.get("q", [""])[0], selected or None,
                exchange=params.get("exchange", [""])[0]))
            return
        if path == "/api/market-data/export.csv":
            params = parse_qs(parsed.query)
            selected = [s for s in params.get("collection", []) if s]
            body = market_export_csv(market_store(), params.get("q", [""])[0], selected or None,
                                     params.get("exchange", [""])[0])
            self._send_bytes(HTTPStatus.OK, "text/csv; charset=utf-8", body,
                             {"Content-Disposition": "attachment; filename=market-instruments.csv"})
            return
        if path == "/api/market-data/jobs":
            self._send_json(HTTPStatus.OK, {"jobs": market_store().list_jobs(30)})
            return
        if path == "/api/market-data/quota":
            try:
                usage = fetch_account_usage()
                market_store().add_usage_snapshot(usage)
                safe_usage = {key: usage.get(key) for key in ("apiRequests", "apiRequestsDate", "dailyRateLimit", "extraLimit")}
                self._send_json(HTTPStatus.OK, {"usage": safe_usage})
            except (RuntimeError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path.startswith("/api/market-data/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            result = market_store().job_detail(job_id)
            self._send_json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                            result if result else {"error": "任务不存在"})
            return
        route = routes.get(path)
        if route is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在"})
            return
        filename, content_type = route
        self._send_bytes(HTTPStatus.OK, content_type, (STATIC_DIR / filename).read_bytes())

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        handlers = {
            "/api/backtest": calculate_payload,
            "/api/eodhd/prices": fetch_eodhd_payload,
            "/api/market-data/backtest": research_market_payload,
        }
        if path == "/api/market-data/import":
            try:
                definitions = fetch_universe_definitions()
                result = import_universes(market_store(), definitions)
                self._send_json(HTTPStatus.OK, {"collections": result})
            except (RuntimeError, ValueError, FileNotFoundError) as exc:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            return
        if path == "/api/market-data/jobs":
            self._handle_market_job_create()
            return
        if path.startswith("/api/market-data/jobs/"):
            self._handle_market_job_action(path)
            return
        handler = handlers.get(path)
        if handler is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("请求内容为空")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("文件过大，当前请求上限为35MB")
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式不正确")
            self._send_json(HTTPStatus.OK, handler(payload))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except RuntimeError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except (ArithmeticError, OverflowError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "行情数值超出可计算范围，请检查价格是否异常"},
            )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("请求内容为空")
        if length > MAX_REQUEST_BYTES:
            raise ValueError("请求内容超过35MB上限")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求格式不正确")
        return payload

    def _handle_market_job_create(self) -> None:
        try:
            payload = self._read_payload()
            collection_ids = payload.get("collections")
            frequencies = payload.get("frequencies")
            symbols = payload.get("symbols")
            exchange = payload.get("exchange", "")
            if not isinstance(collection_ids, list) or not all(isinstance(s, str) for s in collection_ids):
                raise ValueError("请选择至少一个数据清单")
            if frequencies is not None and (not isinstance(frequencies, list) or not all(isinstance(s, str) for s in frequencies)):
                raise ValueError("频率设置格式不正确")
            if symbols is not None and (not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols)):
                raise ValueError("代码筛选格式不正确")
            if not isinstance(exchange, str):
                raise ValueError("交易所后缀格式不正确")
            batch_size = int(payload.get("batch_size", 30))
            if not 1 <= batch_size <= 300:
                raise ValueError("每个额度检查批次须在1至300之间")
            store = market_store()
            job_id = create_fetch_job(store, collection_ids, frequencies, symbols, exchange)
            try:
                launch_fetch_job(store, job_id, batch_size=batch_size)
            except RuntimeError as exc:
                store.update_job(job_id, status="queued", message=str(exc))
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc), "job_id": job_id})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"job_id": job_id, "job": store.job_detail(job_id)["job"]})
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_market_job_action(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 5:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "任务接口不存在"})
            return
        _, _, _, job_id, action = parts
        store = market_store()
        if action == "pause":
            pause_fetch_job(store, job_id)
        elif action == "resume":
            try:
                resume_fetch_job(store, job_id)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "任务操作不存在"})
            return
        result = store.job_detail(job_id)
        self._send_json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                        result if result else {"error": "任务不存在"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the local strategy research interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("For data privacy this MVP only binds to 127.0.0.1 or localhost")
    server = ThreadingHTTPServer((args.host, args.port), KellyRequestHandler)
    print(f"Strategy Lab is running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
