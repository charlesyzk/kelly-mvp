"""Deterministic synthetic data for interface and engineering demonstrations."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from math import exp, sin
from random import Random


def generate_demo_csv() -> str:
    random = Random(20260905)
    observed = date(2015, 1, 1)
    price = 100.0
    rows: list[tuple[str, str, str]] = []
    trading_index = 0
    while observed <= date(2026, 8, 31):
        if observed.weekday() < 5:
            cyclical = 0.00025 * sin(trading_index / 70)
            shock = random.gauss(0.00025 + cyclical, 0.012)
            price *= exp(shock)
            rows.append((observed.isoformat(), "SYNTHETIC_DEMO", f"{price:.8f}"))
            trading_index += 1
        observed += timedelta(days=1)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "symbol", "adjusted_close"))
    writer.writerows(rows)
    return output.getvalue()
