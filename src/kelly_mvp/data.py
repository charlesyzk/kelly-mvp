"""Strict CSV ingestion and conservative daily/weekly/monthly aggregation."""

from __future__ import annotations

import calendar
import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class PriceRow:
    date: date
    symbol: str
    adjusted_close: float


def _parse_rows(handle: Iterable[str]) -> list[PriceRow]:
    rows: list[PriceRow] = []
    seen: set[tuple[str, date]] = set()
    reader = csv.DictReader(handle)
    required = {"date", "symbol", "adjusted_close"}
    missing = required.difference(reader.fieldnames or ())
    if missing:
        raise ValueError(f"input CSV is missing columns: {sorted(missing)}")
    for line_number, raw in enumerate(reader, start=2):
        try:
            observed = date.fromisoformat(raw["date"].strip())
            symbol = raw["symbol"].strip()
            close = float(raw["adjusted_close"])
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid input at line {line_number}") from exc
        if not symbol:
            raise ValueError(f"empty symbol at line {line_number}")
        if not isfinite(close) or close <= 0:
            raise ValueError(f"adjusted_close must be finite and positive at line {line_number}")
        key = (symbol, observed)
        if key in seen:
            raise ValueError(f"duplicate symbol/date at line {line_number}: {symbol} {observed}")
        seen.add(key)
        rows.append(PriceRow(observed, symbol, close))
    if not rows:
        raise ValueError("input CSV contains no data rows")
    return sorted(rows, key=lambda row: (row.symbol, row.date))


def parse_daily_prices(text: str) -> list[PriceRow]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("input CSV is empty")
    return _parse_rows(io.StringIO(text.lstrip("\ufeff")))


def load_daily_prices(path: str | Path) -> list[PriceRow]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"input CSV does not exist: {source}")
    return parse_daily_prices(source.read_text(encoding="utf-8-sig"))


def daily_prices_to_csv(rows: Iterable[PriceRow]) -> str:
    """Serialize normalized prices using the project's public CSV contract."""

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "symbol", "adjusted_close"))
    count = 0
    for row in rows:
        writer.writerow((row.date.isoformat(), row.symbol, format(row.adjusted_close, ".15g")))
        count += 1
    if count == 0:
        raise ValueError("cannot serialize an empty price series")
    return output.getvalue()


def _period_key(observed: date, frequency: str) -> tuple[int, int]:
    if frequency == "weekly":
        iso = observed.isocalendar()
        return iso.year, iso.week
    if frequency == "monthly":
        return observed.year, observed.month
    raise ValueError(f"unsupported aggregate frequency: {frequency}")


def aggregate_prices(rows: Iterable[PriceRow], frequency: str) -> list[PriceRow]:
    ordered = sorted(rows, key=lambda row: row.date)
    if frequency == "daily":
        return ordered
    if frequency not in {"weekly", "monthly"}:
        raise ValueError(f"unsupported frequency: {frequency}")
    groups: list[list[PriceRow]] = []
    for row in ordered:
        key = _period_key(row.date, frequency)
        if not groups or _period_key(groups[-1][-1].date, frequency) != key:
            groups.append([])
        groups[-1].append(row)
    # Earlier groups are proven complete by the existence of the next period.
    # The final group is only retained when the input reaches an unambiguous
    # calendar boundary; otherwise it is conservatively excluded.
    complete = groups[:-1]
    if groups:
        final = groups[-1]
        last = final[-1].date
        if frequency == "weekly":
            end = last + timedelta(days=4 - last.weekday())
        else:
            end = date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
        if last >= end:
            complete.append(final)
    return [group[-1] for group in complete]


def split_by_symbol(rows: Iterable[PriceRow]) -> dict[str, list[PriceRow]]:
    result: dict[str, list[PriceRow]] = {}
    for row in rows:
        result.setdefault(row.symbol, []).append(row)
    return result
