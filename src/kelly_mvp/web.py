"""Local-only web interface for the research engine."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .backtest import run_backtest
from .config import StrategyConfig
from .data import daily_prices_to_csv, parse_daily_prices
from .demo import generate_demo_csv
from .eodhd import fetch_daily_prices


STATIC_DIR = Path(__file__).with_name("web_static")
MAX_REQUEST_BYTES = 25 * 1024 * 1024


def calculate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    csv_text = payload.get("csv_text")
    if not isinstance(csv_text, str):
        raise ValueError("没有收到 CSV 文件内容")
    try:
        fraction = float(payload.get("kelly_fraction", 0.5))
        cost_bps = float(payload.get("transaction_cost_bps", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Kelly比例和交易成本必须是数字") from exc
    config = StrategyConfig(kelly_fraction=fraction, transaction_cost_bps=cost_bps)
    result = run_backtest(parse_daily_prices(csv_text), config)
    if not result.periods:
        detail = "；".join(result.issues) or "数据不足"
        raise ValueError(f"没有产生可评价结果：{detail}")
    return {
        "config": {
            "windows": config.windows,
            "kelly_fraction": config.kelly_fraction,
            "bounds": [config.lower_bound, config.upper_bound],
            "transaction_cost_bps": config.transaction_cost_bps,
            "development_fraction": config.development_fraction,
        },
        "summaries": [asdict(row) for row in result.summaries],
        "periods": [asdict(row) for row in result.periods],
        "trades": [asdict(row) for row in result.trades],
        "issues": list(result.issues),
    }


def fetch_eodhd_payload(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = payload.get("symbol")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("请输入EODHD代码")
    if not isinstance(start_date, str) or not start_date.strip():
        raise ValueError("请选择开始日期")
    rows = fetch_daily_prices(symbol, start_date, end_date)
    return {
        "csv_text": daily_prices_to_csv(rows),
        "symbol": rows[0].symbol,
        "rows": len(rows),
        "first_date": rows[0].date,
        "last_date": rows[-1].date,
        "source": "EODHD",
    }


class KellyRequestHandler(BaseHTTPRequestHandler):
    server_version = "KellyMVP/0.3"

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, date) else str(item),
        ).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        routes = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if self.path == "/api/eodhd/status":
            self._send_json(
                HTTPStatus.OK,
                {"configured": bool(os.getenv("EODHD_API_TOKEN", "").strip())},
            )
            return
        if self.path == "/demo.csv":
            self._send_bytes(
                HTTPStatus.OK,
                "text/csv; charset=utf-8",
                generate_demo_csv().encode("utf-8"),
            )
            return
        route = routes.get(self.path)
        if route is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在"})
            return
        filename, content_type = route
        self._send_bytes(HTTPStatus.OK, content_type, (STATIC_DIR / filename).read_bytes())

    def do_POST(self) -> None:  # noqa: N802
        handlers = {
            "/api/backtest": calculate_payload,
            "/api/eodhd/prices": fetch_eodhd_payload,
        }
        handler = handlers.get(self.path)
        if handler is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("请求内容为空")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("文件过大，当前上限为25MB")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the local Kelly MVP web interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("For data privacy this MVP only binds to 127.0.0.1 or localhost")
    server = ThreadingHTTPServer((args.host, args.port), KellyRequestHandler)
    print(f"Kelly MVP is running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
