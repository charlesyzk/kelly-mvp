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
from .module_strategy import get_builtin_strategy, load_user_strategy, strategy_catalog


STATIC_DIR = Path(__file__).with_name("web_static")
STRATEGY_TEMPLATE = Path(__file__).with_name("module_strategy") / "user_strategy_template.py"
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
    strategy_id = payload.get("strategy_id", "M4_SIMPLE")
    if strategy_id == "uploaded":
        source = payload.get("strategy_source")
        if not isinstance(source, str):
            raise ValueError("请选择要上传的 Python 策略文件")
        filename = payload.get("strategy_filename", "uploaded_strategy.py")
        strategy = load_user_strategy(source, str(filename))
    elif isinstance(strategy_id, str):
        strategy = get_builtin_strategy(strategy_id)
    else:
        raise ValueError("策略编号格式不正确")
    result = run_backtest(parse_daily_prices(csv_text), config, strategy)
    if not result.periods:
        detail = "；".join(result.issues) or "数据不足"
        raise ValueError(f"没有产生可评价结果：{detail}")
    return {
        "strategy": strategy.public_dict(),
        "config": {
            "windows": config.windows,
            "kelly_fraction": config.kelly_fraction,
            "bounds": [config.lower_bound, config.upper_bound],
            "transaction_cost_bps": config.transaction_cost_bps,
            "development_fraction": config.development_fraction,
        },
        "summaries": [asdict(row) for row in result.summaries],
        "periods": [asdict(row) for row in result.periods],
        "signals": [asdict(row) for row in result.signals],
        "trades": [asdict(row) for row in result.trades],
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
    server_version = "StrategyLab/0.5"

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
            allow_nan=False,
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
        if self.path == "/api/strategies":
            self._send_json(HTTPStatus.OK, strategy_catalog_payload())
            return
        if self.path == "/strategy-template.py":
            self._send_bytes(
                HTTPStatus.OK,
                "text/x-python; charset=utf-8",
                STRATEGY_TEMPLATE.read_bytes(),
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
