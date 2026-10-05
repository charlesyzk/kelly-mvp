"""Causal opening-gap frequency feature from the auction-strategy note.

This is a reusable single-symbol filter only. It does not provide historical
index membership, portfolio selection, or account/order simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from ..data import PriceRow, split_by_symbol


@dataclass(frozen=True, slots=True)
class OpeningGapFilterConfig:
    window: int = 30
    minimum_gap_up_days: int = 10

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be positive")
        if not 0 <= self.minimum_gap_up_days <= self.window:
            raise ValueError("minimum_gap_up_days must be between zero and window")


@dataclass(frozen=True, slots=True)
class OpeningGapFeature:
    symbol: str
    signal_date: date
    observations: int
    gap_up_count: int | None
    eligible: bool | None


def compute_opening_gap_filter(
    prices: Iterable[PriceRow],
    config: OpeningGapFilterConfig | None = None,
) -> tuple[OpeningGapFeature, ...]:
    """Count strict adjusted-open > prior adjusted-close gaps over prior days.

    The signal on date t uses the preceding `window` completed sessions and
    excludes date t itself. A missing open in that lookback leaves the feature
    unavailable instead of silently counting the missing day as no gap.
    """
    active = config or OpeningGapFilterConfig()
    output: list[OpeningGapFeature] = []
    for symbol, unsorted in sorted(split_by_symbol(prices).items()):
        rows = sorted(unsorted, key=lambda row: row.date)
        if len({row.date for row in rows}) != len(rows):
            raise ValueError(f"duplicate date for {symbol}")
        for index, row in enumerate(rows):
            if index < active.window + 1:
                output.append(OpeningGapFeature(symbol, row.date, max(0, index - 1), None, None))
                continue
            first_gap_index = index - active.window
            days = rows[first_gap_index:index]
            if any(item.adjusted_open is None for item in days):
                output.append(OpeningGapFeature(symbol, row.date, len(days), None, None))
                continue
            count = sum(
                float(item.adjusted_open) > rows[position - 1].adjusted_close
                for position, item in enumerate(days, start=first_gap_index)
            )
            output.append(OpeningGapFeature(
                symbol, row.date, len(days), count,
                count >= active.minimum_gap_up_days,
            ))
    return tuple(output)
