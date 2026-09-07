"""Small, auditable EODHD end-of-day price adapter."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from math import isfinite
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import FREQUENCIES
from .data import PriceRow


EODHD_BASE_URL = "https://eodhd.com/api/eod"
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HttpTransport = Callable[[str, float], object]


def _date(value: str | date, name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 YYYY-MM-DD") from exc


def validate_request(
    symbol: str,
    start_date: str | date,
    end_date: str | date | None = None,
) -> tuple[str, date, date]:
    normalized_symbol = str(symbol).strip().upper()
    if not _SYMBOL.fullmatch(normalized_symbol):
        raise ValueError("EODHD代码只能包含字母、数字、点、横线和下划线")
    start = _date(start_date, "开始日期")
    end = date.today() if end_date in (None, "") else _date(end_date, "结束日期")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    return normalized_symbol, start, end


def build_eodhd_url(
    symbol: str,
    start_date: str | date,
    end_date: str | date | None,
    api_token: str,
    frequency: str = "daily",
) -> str:
    normalized_symbol, start, end = validate_request(symbol, start_date, end_date)
    if not isinstance(api_token, str) or not api_token.strip():
        raise ValueError("服务端尚未配置 EODHD_API_TOKEN")
    period = {"daily": "d", "weekly": "w", "monthly": "m"}.get(frequency)
    if period is None:
        raise ValueError(f"unsupported EODHD frequency: {frequency}")
    query = urlencode({
        "api_token": api_token.strip(),
        "fmt": "json",
        "order": "a",
        "period": period,
        "from": start.isoformat(),
        "to": end.isoformat(),
    })
    return f"{EODHD_BASE_URL}/{normalized_symbol}?{query}"


def _default_transport(url: str, timeout_seconds: float) -> object:
    request = Request(url, headers={"User-Agent": "kelly-mvp/0.5"})
    with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("EODHD响应超过50MB上限")
    return json.loads(raw.decode("utf-8"))


def fetch_prices(
    symbol: str,
    start_date: str | date,
    end_date: str | date | None = None,
    *,
    api_token: str | None = None,
    timeout_seconds: float = 30.0,
    http_transport: HttpTransport | None = None,
    frequency: str = "daily",
) -> list[PriceRow]:
    """Fetch provider-supplied adjusted closes; never expose the token."""

    normalized_symbol, start, end = validate_request(symbol, start_date, end_date)
    token = api_token if api_token is not None else os.getenv("EODHD_API_TOKEN", "")
    url = build_eodhd_url(normalized_symbol, start, end, token, frequency)
    try:
        payload = (http_transport or _default_transport)(url, timeout_seconds)
    except (ValueError, json.JSONDecodeError):
        raise
    except Exception as exc:
        raise RuntimeError(f"EODHD请求失败（{type(exc).__name__}）") from None

    if isinstance(payload, dict):
        if "code" in payload or "message" in payload or "error" in payload:
            raise ValueError("EODHD拒绝了请求，请检查Token、订阅权限和代码")
        raise ValueError("EODHD返回了无法识别的对象")
    if not isinstance(payload, list):
        raise ValueError("EODHD返回格式不是行情数组")
    if not payload:
        raise ValueError("EODHD没有返回该代码和日期范围内的行情")

    rows: list[PriceRow] = []
    seen: set[date] = set()
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"EODHD第{index}条行情格式错误")
        try:
            observed = date.fromisoformat(str(item["date"]))
            adjusted_close = float(item["adjusted_close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"EODHD第{index}条行情缺少有效日期或复权收盘价") from exc
        if observed < start or observed > end:
            raise ValueError(f"EODHD返回了请求范围外的日期：{observed.isoformat()}")
        if not isfinite(adjusted_close) or adjusted_close <= 0:
            raise ValueError(f"EODHD第{index}条复权收盘价必须有限且大于零")
        if observed in seen:
            raise ValueError(f"EODHD返回重复日期：{observed.isoformat()}")
        seen.add(observed)
        rows.append(PriceRow(observed, normalized_symbol, adjusted_close))
    return sorted(rows, key=lambda row: row.date)


def fetch_daily_prices(*args, **kwargs) -> list[PriceRow]:
    """Backward-compatible daily adapter."""

    kwargs["frequency"] = "daily"
    return fetch_prices(*args, **kwargs)


def fetch_price_bundle(
    symbol: str,
    start_date: str | date,
    end_date: str | date | None = None,
    *,
    api_token: str | None = None,
    timeout_seconds: float = 30.0,
    http_transport: HttpTransport | None = None,
) -> dict[str, list[PriceRow]]:
    """Fetch EODHD's own daily, weekly and monthly adjusted-close series."""

    return {
        frequency: fetch_prices(
            symbol,
            start_date,
            end_date,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            http_transport=http_transport,
            frequency=frequency,
        )
        for frequency in FREQUENCIES
    }
