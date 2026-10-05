"""Causal EWMA volatility and historical-rank features for the A-share strategy.

This module intentionally contains no account simulation. It preserves the
signal-time feature contract so the higher-level EWMA execution engine can
consume auditable features without confusing it with the Kelly suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite, log, sqrt
from statistics import fmean, variance
from typing import Iterable

from ..data import PriceRow


@dataclass(frozen=True, slots=True)
class EWMAReturnPositionConfig:
    ranking_window: int = 1260
    initial_variance_window: int = 60
    ewma_lambda: float = 0.9
    tie_rule: str = "less_equal"

    def __post_init__(self) -> None:
        if self.ranking_window < 1 or self.initial_variance_window < 2:
            raise ValueError("EWMA windows must be positive; variance window must be at least 2")
        if not isfinite(self.ewma_lambda) or not 0 < self.ewma_lambda < 1:
            raise ValueError("ewma_lambda must be finite and strictly between 0 and 1")
        if self.tie_rule != "less_equal":
            raise ValueError("the documented rank tie rule is <=")


@dataclass(frozen=True, slots=True)
class EWMARankFeature:
    symbol: str
    date: date
    simple_return: float | None
    forecast_volatility: float | None
    standardized_return: float | None
    raw_rank: float | None
    volatility_adjusted_rank: float | None


STATE_BUCKETS = (
    (0.0, 0.05, "extreme_left"),
    (0.05, 0.20, "ordinary_left"),
    (0.20, 0.80, "middle"),
    (0.80, 0.95, "ordinary_right"),
    (0.95, 1.000000000000001, "extreme_right"),
)


@dataclass(frozen=True, slots=True)
class EWMAStateEvent:
    symbol: str
    date: date
    state: str | None
    entered_state: bool | None
    retained_event: bool
    event_number: int | None


@dataclass(frozen=True, slots=True)
class EWMAForwardOutcome:
    """Fixed-horizon close-to-close outcome after a retained state event."""

    symbol: str
    event_date: date
    state: str
    event_number: int
    horizon: int
    end_date: date | None
    simple_return: float | None
    log_mfe: float | None
    log_mae: float | None
    complete: bool


@dataclass(frozen=True, slots=True)
class EWMAConditionalSummary:
    symbol: str
    state: str
    horizon: int
    event_count: int
    completed_count: int
    mean_return: float | None
    median_return: float | None
    win_rate: float | None
    q05_return: float | None
    cvar05_return: float | None
    mean_log_mfe: float | None
    median_log_mfe: float | None
    mean_log_mae: float | None
    median_log_mae: float | None


def state_for_rank(value: float | None) -> str | None:
    if value is None or not isfinite(value) or not 0 < value <= 1:
        return None
    for lower, upper, name in STATE_BUCKETS:
        if lower < value <= upper:
            return name
    return None


def mark_state_events(
    features: Iterable[EWMARankFeature],
    *,
    cooldown_rows: int = 5,
    tradable: dict[tuple[str, date], bool] | None = None,
) -> tuple[EWMAStateEvent, ...]:
    """Mark only first entries, applying independent per-state cooldowns."""
    if cooldown_rows < 0:
        raise ValueError("cooldown_rows must be nonnegative")
    grouped: dict[str, list[EWMARankFeature]] = {}
    for feature in features:
        grouped.setdefault(feature.symbol, []).append(feature)
    output: list[EWMAStateEvent] = []
    for symbol, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row.date)
        previous_state: str | None = None
        state_known = False
        last_event_index: dict[str, int] = {}
        number = 0
        for index, row in enumerate(rows):
            state = state_for_rank(row.volatility_adjusted_rank)
            entered: bool | None = None if not state_known or state is None else state != previous_state
            eligible = bool(
                state is not None
                and entered is True
                and index - last_event_index.get(state, -cooldown_rows - 1) > cooldown_rows
                and (tradable is None or tradable.get((symbol, row.date), True))
            )
            if eligible:
                last_event_index[state] = index
                number += 1
            output.append(EWMAStateEvent(symbol, row.date, state, entered, eligible, number if eligible else None))
            if state is not None:
                previous_state = state
                state_known = True
        # Unknown feature rows leave the prior state unknown until a rank forms.
    return tuple(output)


def _sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        raise ValueError("sample variance requires at least two returns")
    return variance(values)


def _rank(value: float, history: list[float]) -> float:
    return (0.5 + sum(item <= value for item in history)) / (len(history) + 1)


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _lower_tail_mean(values: list[float], mass: float = 0.05) -> float | None:
    """Mean of exactly the worst `mass` fraction, with fractional boundary weight."""
    if not values:
        return None
    ordered = sorted(values)
    tail_count = mass * len(ordered)
    whole = int(tail_count)
    remainder = tail_count - whole
    total = sum(ordered[:whole])
    if remainder and whole < len(ordered):
        total += remainder * ordered[whole]
    return total / tail_count


def analyze_ewma_state_events(
    prices: Iterable[PriceRow],
    config: EWMAReturnPositionConfig | None = None,
    *,
    horizons: tuple[int, ...] = (1, 2, 3, 5, 10, 21),
    cooldown_rows: int = 5,
    tradable: dict[tuple[str, date], bool] | None = None,
) -> tuple[tuple[EWMAStateEvent, ...], tuple[EWMAForwardOutcome, ...], tuple[EWMAConditionalSummary, ...]]:
    """Create causal state events and fixed-horizon conditional outcome tables.

    Event dates are observed after the close. Forward outcomes start at that
    close and use only subsequent adjusted closes. An incomplete horizon is
    retained as pending and excluded from summaries. This intentionally does
    not claim to simulate next-open entry or any stop-exit rule.
    """
    if not horizons or any(isinstance(value, bool) or value < 1 for value in horizons):
        raise ValueError("horizons must contain positive trading-row counts")
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons must not contain duplicates")
    ordered_prices = sorted(prices, key=lambda row: (row.symbol, row.date))
    features = compute_ewma_rank_features(ordered_prices, config)
    events = mark_state_events(features, cooldown_rows=cooldown_rows, tradable=tradable)
    prices_by_symbol: dict[str, list[PriceRow]] = {}
    for row in ordered_prices:
        prices_by_symbol.setdefault(row.symbol, []).append(row)
    index_by_key = {(row.symbol, row.date): i for symbol, rows in prices_by_symbol.items() for i, row in enumerate(rows)}
    outcomes: list[EWMAForwardOutcome] = []
    for event in events:
        if not event.retained_event or event.state is None or event.event_number is None:
            continue
        rows = prices_by_symbol[event.symbol]
        index = index_by_key[(event.symbol, event.date)]
        start = rows[index].adjusted_close
        for horizon in horizons:
            end_index = index + horizon
            complete = end_index < len(rows)
            if not complete:
                outcomes.append(EWMAForwardOutcome(event.symbol, event.date, event.state, event.event_number, horizon, None, None, None, None, False))
                continue
            path = [row.adjusted_close for row in rows[index + 1:end_index + 1]]
            simple_return = path[-1] / start - 1.0
            ratios = [value / start for value in path]
            outcomes.append(EWMAForwardOutcome(
                event.symbol, event.date, event.state, event.event_number, horizon,
                rows[end_index].date, simple_return, log(max(ratios)), log(min(ratios)), True,
            ))
    groups: dict[tuple[str, str, int], list[EWMAForwardOutcome]] = {}
    event_counts: dict[tuple[str, str, int], int] = {}
    for outcome in outcomes:
        key = (outcome.symbol, outcome.state, outcome.horizon)
        event_counts[key] = event_counts.get(key, 0) + 1
        if outcome.complete and outcome.simple_return is not None:
            groups.setdefault(key, []).append(outcome)
    summaries: list[EWMAConditionalSummary] = []
    for key, count in sorted(event_counts.items()):
        symbol, state, horizon = key
        rows = groups.get(key, [])
        returns = [float(row.simple_return) for row in rows if row.simple_return is not None]
        mfes = [float(row.log_mfe) for row in rows if row.log_mfe is not None]
        maes = [float(row.log_mae) for row in rows if row.log_mae is not None]
        summaries.append(EWMAConditionalSummary(
            symbol, state, horizon, count, len(rows),
            fmean(returns) if returns else None,
            _quantile(returns, 0.5),
            (sum(value > 0 for value in returns) / len(returns)) if returns else None,
            _quantile(returns, 0.05), _lower_tail_mean(returns),
            fmean(mfes) if mfes else None, _quantile(mfes, 0.5),
            fmean(maes) if maes else None, _quantile(maes, 0.5),
        ))
    return events, tuple(outcomes), tuple(summaries)


def compute_ewma_rank_features(
    prices: Iterable[PriceRow],
    config: EWMAReturnPositionConfig | None = None,
) -> tuple[EWMARankFeature, ...]:
    """Compute returns, one-time EWMA seed, and strictly trailing ranks.

    The current observation is compared with (but never added to) its rank
    history. Invalid or absent standardized returns are not backfilled.
    """
    active = config or EWMAReturnPositionConfig()
    grouped: dict[str, list[PriceRow]] = {}
    for row in prices:
        grouped.setdefault(row.symbol, []).append(row)
    result: list[EWMARankFeature] = []
    for symbol, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: row.date)
        if len({row.date for row in ordered}) != len(ordered):
            raise ValueError(f"duplicate date for {symbol}")
        simple_returns: list[float | None] = [None]
        for previous, current in zip(ordered, ordered[1:]):
            value = current.adjusted_close / previous.adjusted_close - 1.0
            if not isfinite(value) or value <= -1:
                simple_returns.append(None)
            else:
                simple_returns.append(value)

        seed_end = active.initial_variance_window
        seeded = len(simple_returns) > seed_end and all(
            value is not None for value in simple_returns[1:seed_end + 1]
        )
        variance_forecast = _sample_variance([float(x) for x in simple_returns[1:seed_end + 1]]) if seeded else None
        forecasts: list[float | None] = [None] * len(ordered)
        standardized: list[float | None] = [None] * len(ordered)
        for index in range(seed_end + 1, len(ordered)):
            observed = simple_returns[index]
            if variance_forecast is None or variance_forecast <= 0 or observed is None:
                forecast = None if variance_forecast is None or variance_forecast < 0 else sqrt(variance_forecast)
                forecasts[index] = forecast
                standardized[index] = None
            else:
                forecast = sqrt(variance_forecast)
                forecasts[index] = forecast
                standardized[index] = observed / forecast
            if observed is not None and variance_forecast is not None:
                variance_forecast = active.ewma_lambda * variance_forecast + (1 - active.ewma_lambda) * observed**2

        raw_values = [float(value) if value is not None else None for value in simple_returns]
        for index, row in enumerate(ordered):
            raw_rank = vol_rank = None
            if index >= active.ranking_window + 1:
                current_raw = raw_values[index]
                raw_history = raw_values[index - active.ranking_window:index]
                if current_raw is not None and len(raw_history) == active.ranking_window and all(v is not None for v in raw_history):
                    raw_rank = _rank(current_raw, [float(value) for value in raw_history])
            if index >= active.initial_variance_window + active.ranking_window + 1:
                current_vol = standardized[index]
                vol_history = standardized[index - active.ranking_window:index]
                if current_vol is not None and len(vol_history) == active.ranking_window and all(v is not None for v in vol_history):
                    vol_rank = _rank(current_vol, [float(value) for value in vol_history])
            result.append(EWMARankFeature(
                symbol=symbol,
                date=row.date,
                simple_return=raw_values[index],
                forecast_volatility=forecasts[index],
                standardized_return=standardized[index],
                raw_rank=raw_rank,
                volatility_adjusted_rank=vol_rank,
            ))
    return tuple(result)
