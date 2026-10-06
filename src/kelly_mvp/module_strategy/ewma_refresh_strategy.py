"""Independent walk-forward A-share EWMA strategy and account simulator.

This module implements the first-part rules in the 2026-09-11 handover
document. It deliberately does not implement the proposed R1-R8 changes and
does not use the Kelly position-strategy contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from math import exp, isfinite, log, sqrt
from statistics import fmean, median, stdev
from typing import Iterable

import numpy as np

from ..data import PriceRow


STATES = ("extreme_left", "ordinary_left", "middle", "ordinary_right", "extreme_right")
EXIT_POLICIES = ("A", "B", "C")
STRATEGY_ID = "ewma_return_position_refresh_v1"


@dataclass(frozen=True, slots=True)
class EWMARefreshConfig:
    ranking_window: int = 1260
    initial_variance_window: int = 60
    ewma_lambda: float = 0.9
    cooldown_rows: int = 5
    holding_days: int = 5
    a_min_events: int = 60
    b_min_winners: int = 30
    refresh_interval: int = 63
    exploration_min_events: int = 100
    strict_min_events: int = 200
    block_length: int = 63
    sensitivity_block_length: int = 126
    bootstrap_repetitions: int = 10_000
    bootstrap_seed: int = 20260906
    alpha: float = 0.05
    cvar_floor: float = -0.10
    round_trip_cost: float = 0.002
    investment_fraction: float = 0.10
    allowed_exchanges: tuple[str, ...] = ("SHG", "SHE")

    def __post_init__(self) -> None:
        if self.ranking_window < 1 or self.initial_variance_window < 2:
            raise ValueError("EWMA windows are invalid")
        if not 0 < self.ewma_lambda < 1:
            raise ValueError("ewma_lambda must be in (0, 1)")
        if self.holding_days != 5 or self.cooldown_rows != 5:
            raise ValueError("the current A-share rule fixes K=5 and cooldown=5")
        if self.refresh_interval != 63 or self.a_min_events != 60 or self.b_min_winners != 30:
            raise ValueError("the current refresh and calibration thresholds are fixed")
        if self.bootstrap_repetitions < 1 or self.block_length < 1 or self.sensitivity_block_length < 1:
            raise ValueError("bootstrap settings must be positive")
        if self.round_trip_cost < 0 or self.investment_fraction <= 0:
            raise ValueError("cost and investment fraction are invalid")
        if self.investment_fraction * (1 + self.round_trip_cost) > 1:
            raise ValueError("investment_fraction * (1 + cost) must not exceed 1")


@dataclass(frozen=True, slots=True)
class EWMAFeature:
    symbol: str
    date: date
    log_return: float | None
    sigma_signal: float | None  # sigma(t | t-1), denominator for today's x
    standardized_log_return: float | None
    sigma_next: float | None  # sigma(t+1 | t), entry scale after today's update
    raw_rank: float | None
    standardized_rank: float | None


@dataclass(frozen=True, slots=True)
class EWMAEvent:
    symbol: str
    date: date
    row_index: int
    state: str
    event_number: int
    raw_rank: float
    standardized_rank: float
    sigma_entry: float


@dataclass(frozen=True, slots=True)
class FixedPathLabel:
    symbol: str
    event_date: date
    event_index: int
    state: str
    end_date: date | None
    fixed_log_return: float | None
    fixed_net_return: float | None
    log_mfe: float | None
    log_mae: float | None
    mfe_sigma: float | None
    mae_sigma: float | None
    complete: bool


@dataclass(frozen=True, slots=True)
class FixedHorizonOutcome:
    symbol: str
    event_date: date
    event_index: int
    state: str
    horizon: int
    end_date: date | None
    log_return: float | None
    gross_return: float | None
    net_return: float | None
    log_mfe: float | None
    log_mae: float | None
    complete: bool


@dataclass(frozen=True, slots=True)
class PathQuantile:
    state: str
    horizon: int
    complete_21_day_event_count: int
    q05_log_return: float | None
    q25_log_return: float | None
    median_log_return: float | None
    q75_log_return: float | None
    q95_log_return: float | None
    q05_net_return: float | None
    q25_net_return: float | None
    median_net_return: float | None
    q75_net_return: float | None
    q95_net_return: float | None


@dataclass(frozen=True, slots=True)
class PathDrawdown:
    symbol: str
    event_date: date
    state: str
    horizon: int
    log_drawdown: float
    simple_drawdown: float


@dataclass(frozen=True, slots=True)
class DirectionSummary:
    state: str
    signal_direction: str
    complete_k_event_count: int
    mean_fixed_k_net_return: float | None
    median_fixed_k_net_return: float | None
    direction_label: str


@dataclass(frozen=True, slots=True)
class Calibration:
    state: str
    event_index: int
    a: float | None
    b: float | None
    a_sample_count: int
    b_sample_count: int
    cutoff_index: int
    valid: bool


@dataclass(frozen=True, slots=True)
class ExitResult:
    symbol: str
    event_date: date
    state: str
    policy: str
    entry_date: date | None
    entry_index: int | None
    entry_price: float | None
    exit_date: date | None
    exit_index: int | None
    exit_price: float | None
    exit_reason: str
    holding_rows: int | None
    a: float | None
    b: float | None
    sigma_entry: float | None
    gross_return: float | None
    net_return: float | None
    cost: float | None
    complete: bool
    ambiguous_double_touch: bool = False
    audit: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class Approval:
    review_index: int
    review_date: date
    cutoff_index: int
    evidence_id: str
    policy: str
    state: str
    evaluation_count: int
    matched_count: int
    tail_mass: float
    mean_net_return: float | None
    median_net_return: float | None
    mean_matched_increment: float | None
    cvar05: float | None
    cvar_lcb95: float | None
    exploration_allowed: bool
    statistically_validated: bool
    evidence_sufficient: bool
    strict_lcbs_pass: bool
    holm_p_value: float | None
    bootstrap_valid: bool
    warnings: tuple[str, ...] = ()
    cvar_lcb95_sensitivity: float | None = None
    sensitivity_bootstrap_valid: bool = False


@dataclass(frozen=True, slots=True)
class AccountSignal:
    date: date
    state: str | None
    event_number: int | None
    policy: str
    approved: bool
    decision: str
    reason: str
    a: float | None
    b: float | None
    sigma_entry: float | None
    account: str = ""


@dataclass(frozen=True, slots=True)
class AccountTrade:
    policy: str
    event_date: date
    state: str
    entry_date: date
    entry_index: int
    entry_price: float
    exit_date: date | None
    exit_index: int | None
    exit_price: float | None
    exit_reason: str
    holding_rows: int | None
    shares: float
    invested_amount: float
    cost: float
    net_return: float | None
    status: str
    a: float
    b: float
    sigma_entry: float
    ambiguous_double_touch: bool = False


@dataclass(frozen=True, slots=True)
class LedgerRow:
    policy: str
    date: date
    cash: float
    shares: float
    close: float
    equity: float
    daily_return: float | None
    cumulative_return: float
    drawdown: float
    signal_status: str


@dataclass(frozen=True, slots=True)
class EWMARefreshResult:
    strategy_id: str
    symbol: str
    config: EWMARefreshConfig
    feature_start_index: int | None
    train_start_index: int | None
    validation_start_index: int | None
    test_start_index: int | None
    split_method: str
    input_snapshot: dict[str, object]
    features: tuple[EWMAFeature, ...]
    events: tuple[EWMAEvent, ...]
    fixed_labels: tuple[FixedPathLabel, ...]
    horizon_outcomes: tuple[FixedHorizonOutcome, ...]
    horizon_summaries: tuple[dict[str, object], ...]
    path_quantiles: tuple[PathQuantile, ...]
    path_drawdowns: tuple[PathDrawdown, ...]
    direction_summaries: tuple[DirectionSummary, ...]
    calibrations: tuple[Calibration, ...]
    exit_results: tuple[ExitResult, ...]
    approvals: tuple[Approval, ...]
    signals: tuple[AccountSignal, ...]
    trades: tuple[AccountTrade, ...]
    ledger: tuple[LedgerRow, ...]
    summaries: tuple[dict[str, object], ...]
    issues: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, object]:
        return _jsonable(asdict(self))


def _jsonable(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _input_snapshot(rows: list[PriceRow], symbol: str) -> dict[str, object]:
    material = []
    for row in rows:
        item = asdict(row)
        item["date"] = row.date.isoformat()
        material.append(item)
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "symbol": symbol,
        "row_count": len(rows),
        "date_start": rows[0].date.isoformat() if rows else None,
        "date_end": rows[-1].date.isoformat() if rows else None,
        "sha256": sha256(canonical.encode("utf-8")).hexdigest(),
        "rows_with_adjusted_ohlc": sum(row.adjusted_open is not None and row.adjusted_high is not None and row.adjusted_low is not None for row in rows),
        "rows_with_raw_ohlc": sum(row.raw_open is not None and row.raw_high is not None and row.raw_low is not None and row.raw_close is not None for row in rows),
        "rows_with_adjustment_factor": sum(row.adjustment_factor is not None for row in rows),
        "rows_with_trading_status": sum(bool(row.trading_status) for row in rows),
        "rows_with_verified_limits": sum(row.price_limits_verified for row in rows),
    }


def _rank(value: float, past: list[float]) -> float:
    return (0.5 + sum(item <= value for item in past)) / (len(past) + 1)


def _state(rank: float | None) -> str | None:
    if rank is None or not isfinite(rank) or not 0 < rank <= 1:
        return None
    if rank <= 0.05:
        return STATES[0]
    if rank <= 0.20:
        return STATES[1]
    if rank <= 0.80:
        return STATES[2]
    if rank <= 0.95:
        return STATES[3]
    return STATES[4]


def compute_ewma_features(
    prices: Iterable[PriceRow], config: EWMARefreshConfig | None = None,
) -> tuple[EWMAFeature, ...]:
    """Compute log-return EWMA features and strict trailing ranks per symbol."""
    cfg = config or EWMARefreshConfig()
    grouped: dict[str, list[PriceRow]] = {}
    for item in prices:
        grouped.setdefault(item.symbol.upper(), []).append(item)
    output: list[EWMAFeature] = []
    for symbol, unsorted in sorted(grouped.items()):
        rows = sorted(unsorted, key=lambda row: row.date)
        if not rows or len({item.date for item in rows}) != len(rows):
            raise ValueError(f"duplicate or empty dates for {symbol}")
        returns: list[float | None] = [None]
        for prev, curr in zip(rows, rows[1:]):
            returns.append(log(curr.adjusted_close / prev.adjusted_close))
        seed_end = cfg.initial_variance_window
        if len(rows) > seed_end and all(x is not None for x in returns[1:seed_end + 1]):
            seed = [float(x) for x in returns[1:seed_end + 1]]
            center = fmean(seed)
            variance = sum((x - center) ** 2 for x in seed) / (len(seed) - 1)
        else:
            variance = None
        sigmas: list[float | None] = [None] * len(rows)
        x_values: list[float | None] = [None] * len(rows)
        next_sigmas: list[float | None] = [None] * len(rows)
        for i in range(seed_end + 1, len(rows)):
            observed = returns[i]
            current_sigma = sqrt(variance) if variance is not None and variance >= 0 else None
            sigmas[i] = current_sigma
            if current_sigma is not None and current_sigma > 0 and observed is not None:
                x_values[i] = observed / current_sigma
            if variance is not None and observed is not None:
                variance = cfg.ewma_lambda * variance + (1 - cfg.ewma_lambda) * observed**2
                next_sigmas[i] = sqrt(variance) if variance >= 0 else None
        for i, row in enumerate(rows):
            raw_rank = standardized_rank = None
            if i >= cfg.ranking_window + 1 and returns[i] is not None:
                history = returns[i - cfg.ranking_window:i]
                if len(history) == cfg.ranking_window and all(value is not None for value in history):
                    raw_rank = _rank(float(returns[i]), [float(value) for value in history])
            if i >= seed_end + cfg.ranking_window + 1 and x_values[i] is not None:
                history_x = x_values[i - cfg.ranking_window:i]
                if len(history_x) == cfg.ranking_window and all(value is not None for value in history_x):
                    standardized_rank = _rank(float(x_values[i]), [float(value) for value in history_x])
            output.append(EWMAFeature(symbol, row.date, returns[i], sigmas[i], x_values[i], next_sigmas[i], raw_rank, standardized_rank))
    return tuple(output)


def _mark_events(
    features: tuple[EWMAFeature, ...], prices: list[PriceRow], config: EWMARefreshConfig,
) -> tuple[EWMAEvent, ...]:
    output: list[EWMAEvent] = []
    for symbol in sorted({row.symbol for row in features}):
        fs = [row for row in features if row.symbol == symbol]
        previous: str | None = None
        known = False
        last_kept: dict[str, int] = {}
        number = 0
        for i, feature in enumerate(fs):
            state = _state(feature.standardized_rank)
            entered = known and state is not None and state != previous
            entry_sigma = feature.sigma_next
            eligible = (entered and entry_sigma is not None and entry_sigma > 0 and
                        _row_tradeable(prices[i]) and i - last_kept.get(state or "", -999999) > config.cooldown_rows)
            if eligible and state is not None and feature.raw_rank is not None and feature.standardized_rank is not None:
                last_kept[state] = i
                number += 1
                output.append(EWMAEvent(symbol, feature.date, i, state, number, feature.raw_rank,
                                        feature.standardized_rank, float(entry_sigma)))
            if state is None:
                known = False
            else:
                previous, known = state, True
    return tuple(output)


def _row_tradeable(row: PriceRow) -> bool:
    status = (row.trading_status or "").strip().lower()
    return status not in {"suspended", "suspend", "停牌", "停牌中", "no_trade", "not_traded", "休市"}


def _require_a_share_ohlc(rows: list[PriceRow], config: EWMARefreshConfig) -> str:
    symbols = sorted({row.symbol.upper() for row in rows})
    if len(symbols) != 1:
        raise ValueError("the independent EWMA account runner accepts exactly one A-share symbol per run")
    symbol = symbols[0]
    exchange = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    if exchange not in config.allowed_exchanges:
        raise ValueError("EWMA refresh strategy only accepts SHG/SHE A-share codes")
    rows.sort(key=lambda row: row.date)
    if len({row.date for row in rows}) != len(rows):
        raise ValueError(f"duplicate dates for {symbol}")
    missing = [row.date.isoformat() for row in rows if any(value is None for value in
               (row.adjusted_open, row.adjusted_high, row.adjusted_low))]
    if missing:
        raise ValueError(f"EWMA execution requires adjusted OHLC; missing at {', '.join(missing[:10])}")
    for row in rows:
        values = (row.adjusted_open, row.adjusted_high, row.adjusted_low, row.adjusted_close)
        if any(value is None or not isfinite(value) or value <= 0 for value in values):
            raise ValueError(f"EWMA execution requires adjusted OHLC at {row.date}")
        if row.volume is not None and (not isfinite(row.volume) or row.volume < 0):
            raise ValueError(f"volume must be finite and non-negative at {row.date}")
        op, hi, lo, close = (float(value) for value in values)
        if not lo <= min(op, close) <= max(op, close) <= hi:
            raise ValueError(f"adjusted OHLC ordering is invalid at {row.date}")
        if row.price_limits_verified and (
            row.adjusted_limit_up is None or row.adjusted_limit_down is None or
            not isfinite(row.adjusted_limit_up) or not isfinite(row.adjusted_limit_down) or
            row.adjusted_limit_down <= 0 or row.adjusted_limit_down > row.adjusted_limit_up
        ):
            raise ValueError(f"verified adjusted price limits are missing or invalid at {row.date}")
    return symbol


def _fixed_labels(rows: list[PriceRow], events: tuple[EWMAEvent, ...], config: EWMARefreshConfig) -> tuple[FixedPathLabel, ...]:
    labels = []
    k = config.holding_days
    for event in events:
        entry_i, end_i = event.row_index + 1, event.row_index + k
        if end_i >= len(rows):
            labels.append(FixedPathLabel(event.symbol, event.date, event.row_index, event.state, None, None, None, None, None, None, None, False))
            continue
        entry = float(rows[entry_i].adjusted_open)
        path = rows[entry_i:end_i + 1]
        highs = [float(row.adjusted_high) for row in path]
        lows = [float(row.adjusted_low) for row in path]
        log_mfe = max(0.0, max(log(value / entry) for value in highs))
        log_mae = min(0.0, min(log(value / entry) for value in lows))
        fixed_log = log(float(rows[end_i].adjusted_close) / entry)
        fixed_net = exp(fixed_log) - 1 - config.round_trip_cost
        labels.append(FixedPathLabel(event.symbol, event.date, event.row_index, event.state, rows[end_i].date,
                      fixed_log, fixed_net, log_mfe, log_mae, log_mfe / event.sigma_entry,
                      abs(log_mae) / event.sigma_entry, True))
    return tuple(labels)


def _fixed_horizon_outcomes(
    rows: list[PriceRow], events: tuple[EWMAEvent, ...], config: EWMARefreshConfig,
) -> tuple[FixedHorizonOutcome, ...]:
    output: list[FixedHorizonOutcome] = []
    for event in events:
        entry_i = event.row_index + 1
        if entry_i >= len(rows):
            for horizon in (1, 2, 3, 5, 10, 21):
                output.append(FixedHorizonOutcome(event.symbol, event.date, event.row_index, event.state,
                                                  horizon, None, None, None, None, None, None, False))
            continue
        entry = float(rows[entry_i].adjusted_open)
        for horizon in (1, 2, 3, 5, 10, 21):
            end_i = event.row_index + horizon
            if end_i >= len(rows):
                output.append(FixedHorizonOutcome(event.symbol, event.date, event.row_index, event.state,
                                                  horizon, None, None, None, None, None, None, False))
                continue
            path = rows[entry_i:end_i + 1]
            log_return = log(float(rows[end_i].adjusted_close) / entry)
            gross = exp(log_return) - 1
            mfe = max(0.0, max(log(float(row.adjusted_high) / entry) for row in path))
            mae = min(0.0, min(log(float(row.adjusted_low) / entry) for row in path))
            output.append(FixedHorizonOutcome(event.symbol, event.date, event.row_index, event.state,
                                              horizon, rows[end_i].date, log_return, gross,
                                              gross - config.round_trip_cost, mfe, mae, True))
    return tuple(output)


def _summarize_horizons(outcomes: tuple[FixedHorizonOutcome, ...], config: EWMARefreshConfig) -> tuple[dict[str, object], ...]:
    output = []
    for state in STATES:
        for horizon in (1, 2, 3, 5, 10, 21):
            group = [row for row in outcomes if row.state == state and row.horizon == horizon and row.complete]
            logs = [float(row.log_return) for row in group if row.log_return is not None]
            net = [float(row.net_return) for row in group if row.net_return is not None]
            gross = [float(row.gross_return) for row in group if row.gross_return is not None]
            output.append({
                "statistic_type": "overlapping_fixed_horizon_event_conditional",
                "state": state, "horizon": horizon, "event_count": len(group),
                "mean_log_return": fmean(logs) if logs else None,
                "median_log_return": median(logs) if logs else None,
                "mean_gross_return": fmean(gross) if gross else None,
                "median_net_return": median(net) if net else None,
                "win_rate_net": sum(value > 0 for value in net) / len(net) if net else None,
                "q05_net_return": _type7(net, 0.05), "q25_net_return": _type7(net, 0.25),
                "q75_net_return": _type7(net, 0.75), "q95_net_return": _type7(net, 0.95),
                "cvar05_net_return": _cvar(net, config.alpha),
            })
    return tuple(output)


def _path_research(
    rows: list[PriceRow], events: tuple[EWMAEvent, ...], config: EWMARefreshConfig,
) -> tuple[tuple[PathQuantile, ...], tuple[PathDrawdown, ...]]:
    by_state: dict[str, list[tuple[EWMAEvent, list[float]]]] = {state: [] for state in STATES}
    drawdowns: list[PathDrawdown] = []
    for event in events:
        entry_i = event.row_index + 1
        end_i = event.row_index + 21
        if end_i >= len(rows):
            continue
        entry = float(rows[entry_i].adjusted_open)
        log_path = [log(float(rows[j].adjusted_close) / entry) for j in range(entry_i, end_i + 1)]
        by_state[event.state].append((event, log_path))
        wealth_path = [0.0] + log_path
        peak = wealth_path[0]
        max_dd = 0.0
        for value in wealth_path:
            max_dd = min(max_dd, value - peak)
            peak = max(peak, value)
        drawdowns.append(PathDrawdown(event.symbol, event.date, event.state, 21, max_dd, exp(max_dd) - 1))
    quantiles: list[PathQuantile] = []
    for state, paths in by_state.items():
        for horizon in range(1, 22):
            logs = [path[horizon - 1] for _, path in paths]
            net = [exp(value) - 1 - config.round_trip_cost for value in logs]
            quantiles.append(PathQuantile(
                state, horizon, len(paths), _type7(logs, 0.05), _type7(logs, 0.25),
                _type7(logs, 0.5), _type7(logs, 0.75), _type7(logs, 0.95),
                _type7(net, 0.05), _type7(net, 0.25), _type7(net, 0.5),
                _type7(net, 0.75), _type7(net, 0.95),
            ))
    return tuple(quantiles), tuple(drawdowns)


def _direction_summaries(
    events: tuple[EWMAEvent, ...], features: tuple[EWMAFeature, ...], outcomes: tuple[FixedHorizonOutcome, ...],
    config: EWMARefreshConfig,
) -> tuple[DirectionSummary, ...]:
    by_event = {(row.event_index, row.horizon): row for row in outcomes}
    groups: dict[tuple[str, str], list[float]] = {}
    for event in events:
        signal_return = features[event.row_index].log_return
        if signal_return is None or signal_return == 0:
            direction = "zero_or_missing"
        else:
            direction = "positive" if signal_return > 0 else "negative"
        outcome = by_event.get((event.row_index, config.holding_days))
        if outcome is not None and outcome.complete and outcome.net_return is not None:
            groups.setdefault((event.state, direction), []).append(outcome.net_return)
    output = []
    for state in STATES:
        for direction in ("negative", "zero_or_missing", "positive"):
            values = groups.get((state, direction), [])
            avg = fmean(values) if values else None
            med = median(values) if values else None
            if avg is None or med is None:
                label = "方向证据不足"
            elif avg * med < 0:
                label = "均值与中位数方向不一致"
            elif direction == "negative" and med > 0:
                label = "反转倾向（描述性）"
            elif direction == "negative":
                label = "下跌延续倾向（描述性）"
            elif direction == "positive" and med > 0:
                label = "动量倾向（描述性）"
            elif direction == "positive":
                label = "回落倾向（描述性）"
            else:
                label = "方向证据不足"
            output.append(DirectionSummary(state, direction, len(values), avg, med, label))
    return tuple(output)


def _calibrate(state: str, i: int, labels: tuple[FixedPathLabel, ...], config: EWMARefreshConfig) -> Calibration:
    # A label is mature only if its full K-day path ended before the signal close.
    eligible = [row for row in labels if row.state == state and row.complete and row.event_index + config.holding_days <= i - 1]
    a_values = [float(row.mfe_sigma) for row in eligible if row.mfe_sigma is not None]
    winners = [row for row in eligible if row.fixed_net_return is not None and row.fixed_net_return > 0]
    b_values = [float(row.mae_sigma) for row in winners if row.mae_sigma is not None]
    a = median(a_values) if len(a_values) >= config.a_min_events else None
    b = median(b_values) if len(b_values) >= config.b_min_winners else None
    valid = a is not None and b is not None and isfinite(a) and isfinite(b) and a > 0 and b > 0
    return Calibration(state, i, a if valid else a, b if valid else b, len(a_values), len(b_values), i - 1, bool(valid))


def _can_buy(row: PriceRow) -> tuple[bool, str]:
    if not _row_tradeable(row):
        return False, "signal_cancelled_non_trading_day"
    if row.volume == 0:
        return False, "signal_cancelled_zero_volume"
    # Without verified limit data, a single-price bar cannot confirm a fill.
    one_price = row.adjusted_open == row.adjusted_high == row.adjusted_low == row.adjusted_close
    verified_up_limit = (row.price_limits_verified and row.adjusted_limit_up is not None and
                         row.adjusted_open is not None and row.adjusted_open >= row.adjusted_limit_up)
    if verified_up_limit or (one_price and not row.price_limits_verified):
        return False, "signal_cancelled_single_price_uncertain"
    return True, "filled_at_next_open"


def _can_sell(row: PriceRow) -> bool:
    if not _row_tradeable(row) or row.volume == 0:
        return False
    one_price = row.adjusted_open == row.adjusted_high == row.adjusted_low == row.adjusted_close
    verified_down_limit = (row.price_limits_verified and row.adjusted_limit_down is not None and
                           row.adjusted_open is not None and row.adjusted_open <= row.adjusted_limit_down)
    return not (verified_down_limit or (one_price and not row.price_limits_verified))


def _simulate_exit(
    rows: list[PriceRow], event: EWMAEvent, calibration: Calibration, policy: str,
    features: tuple[EWMAFeature, ...], config: EWMARefreshConfig,
) -> ExitResult:
    if policy not in EXIT_POLICIES:
        raise ValueError("unknown A/B/C exit policy")
    i, a, b = event.row_index, calibration.a, calibration.b
    if not calibration.valid or a is None or b is None:
        return ExitResult(event.symbol, event.date, event.state, policy, None, None, None, None, None, None,
                          "calibration_unavailable", None, a, b, event.sigma_entry, None, None, None, False)
    entry_i = i + 1
    if entry_i >= len(rows):
        return ExitResult(event.symbol, event.date, event.state, policy, None, None, None, None, None, None,
                          "pending_entry_no_next_row", None, a, b, event.sigma_entry, None, None, None, False)
    allowed, why = _can_buy(rows[entry_i])
    if not allowed:
        return ExitResult(event.symbol, event.date, event.state, policy, rows[entry_i].date, entry_i, None,
                          None, None, None, why, 0, a, b, event.sigma_entry, None, None, None, False)
    entry = float(rows[entry_i].adjusted_open)
    initial_upper = entry * exp(a * event.sigma_entry)
    initial_lower = entry * exp(-b * event.sigma_entry)
    upper, lower = initial_upper, initial_lower
    audit: list[dict[str, object]] = []
    pending_exit: str | None = None
    ambiguous = False
    for u in range(entry_i + 1, len(rows)):
        row = rows[u]
        held_rows = u - entry_i
        if not _row_tradeable(row):
            audit.append({"date": row.date.isoformat(), "upper": upper, "lower": lower, "action": "suspended_no_update"})
            continue
        if pending_exit is not None and not _can_sell(row):
            audit.append({"date": row.date.isoformat(), "upper": upper, "lower": lower, "action": "exit_blocked_unconfirmed_trade"})
            continue
        if pending_exit is not None:
            exit_price = float(row.adjusted_open)
            audit.append({"date": row.date.isoformat(), "upper": upper, "lower": lower,
                          "open": exit_price, "action": "pending_exit_first_executable_open"})
            return _exit_result(event, policy, entry_i, entry, u, exit_price, pending_exit, held_rows,
                                a, b, event.sigma_entry, config, audit, ambiguous, rows)
        op, hi, lo = float(row.adjusted_open), float(row.adjusted_high), float(row.adjusted_low)
        reason: str | None = None
        price: float | None = None
        if op <= lower:
            reason, price = "stop_open", op
        elif op >= upper:
            reason, price = "take_profit_open", upper
        elif held_rows > config.holding_days:
            reason, price = "time_exit_open", op
        elif lo <= lower and hi >= upper:
            reason, price, ambiguous = "stop_double_touch", lower, True
        elif lo <= lower:
            reason, price = "stop_intraday", lower
        elif hi >= upper:
            reason, price = "take_profit_intraday", upper
        elif held_rows == config.holding_days:
            reason, price = "time_exit_close", float(row.adjusted_close)
        audit.append({"date": row.date.isoformat(), "upper": upper, "lower": lower,
                      "open": op, "high": hi, "low": lo, "action": reason or "hold"})
        if reason is not None and price is not None:
            if not _can_sell(row):
                pending_exit = reason
                continue
            return _exit_result(event, policy, entry_i, entry, u, price, reason, held_rows,
                                a, b, event.sigma_entry, config, audit, ambiguous, rows)
        # Today's observed close changes only the next day's boundary.
        sigma_next = features[u].sigma_next
        if sigma_next is not None and sigma_next > 0:
            if policy == "B":
                upper, lower = entry * exp(a * sigma_next), entry * exp(-b * sigma_next)
            elif policy == "C":
                lower = max(lower, float(row.adjusted_close) * exp(-b * sigma_next))
    return ExitResult(event.symbol, event.date, event.state, policy, rows[entry_i].date, entry_i, entry,
                      rows[-1].date, len(rows) - 1, float(rows[-1].adjusted_close), "unfinished_mark_to_market",
                      len(rows) - 1 - entry_i, a, b, event.sigma_entry, None, None, None, False,
                      ambiguous, tuple(audit))


def _exit_result(event: EWMAEvent, policy: str, entry_i: int, entry: float, exit_i: int, exit_price: float,
                 reason: str, held: int, a: float, b: float, sigma: float, config: EWMARefreshConfig,
                 audit: list[dict[str, object]], ambiguous: bool, rows: list[PriceRow]) -> ExitResult:
    gross = exit_price / entry - 1
    net = gross - config.round_trip_cost
    return ExitResult(event.symbol, event.date, event.state, policy, rows[entry_i].date, entry_i, entry,
                      rows[exit_i].date, exit_i, exit_price, reason, held, a, b, sigma, gross, net,
                      config.round_trip_cost, True, ambiguous, tuple(audit))


def _cvar(values: list[float], alpha: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mass = alpha * len(ordered)
    whole = int(mass)
    frac = mass - whole
    total = sum(ordered[:whole])
    if frac and whole < len(ordered):
        total += frac * ordered[whole]
    return total / mass


def _type7(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    h = (len(ordered) - 1) * p
    j = int(h)
    return ordered[j] + (h - j) * (ordered[min(j + 1, len(ordered) - 1)] - ordered[j])


def _block_bootstrap(
    values_by_index: dict[int, float], start: int, end: int, block_length: int,
    repetitions: int, seed: int, alpha: float, block_starts: np.ndarray | None = None,
) -> dict[str, object]:
    """Non-circular calendar block bootstrap with the handover's basic LCB."""
    days = end - start + 1
    if days < block_length or not values_by_index:
        return {"valid": False, "mean_lcb": None, "median_lcb": None,
                "cvar_lcb": None, "mean_p": None, "median_p": None, "cvar_p": None}
    event_indices = np.asarray(sorted(values_by_index), dtype=int)
    vals = np.asarray([values_by_index[int(i)] for i in event_indices], dtype=float)
    relative = event_indices - start
    if np.any(relative < 0) or np.any(relative >= days):
        return {"valid": False, "mean_lcb": None, "median_lcb": None,
                "cvar_lcb": None, "mean_p": None, "median_p": None, "cvar_p": None}
    n_blocks = int(np.ceil(days / block_length))
    offsets = np.arange(block_length)
    if block_starts is None:
        rng = np.random.default_rng(seed)
        block_starts = rng.integers(0, days - block_length + 1, size=(repetitions, n_blocks))
    if block_starts.shape != (repetitions, n_blocks):
        raise ValueError("calendar block sample shape does not match bootstrap settings")
    calendar_values = np.full(days, np.nan, dtype=float)
    calendar_values[relative] = vals
    boot_means: list[np.ndarray] = []
    boot_medians: list[np.ndarray] = []
    boot_cvars: list[np.ndarray] = []
    max_start = days - block_length
    for batch_start in range(0, repetitions, 256):
        batch_size = min(256, repetitions - batch_start)
        starts = block_starts[batch_start:batch_start + batch_size]
        sampled = (starts[:, :, None] + offsets[None, None, :]).reshape(batch_size, -1)[:, :days]
        sample_matrix = calendar_values[sampled]
        counts = np.isfinite(sample_matrix).sum(axis=1)
        # Any draw that omits the selected state makes the whole statistic
        # invalid; do not silently discard or regenerate that draw.
        if np.any(counts == 0):
            return {"valid": False, "mean_lcb": None, "median_lcb": None,
                    "cvar_lcb": None, "mean_p": None, "median_p": None, "cvar_p": None}
        boot_means.append(np.nanmean(sample_matrix, axis=1))
        boot_medians.append(np.nanmedian(sample_matrix, axis=1))
        ordered = np.sort(np.where(np.isfinite(sample_matrix), sample_matrix, np.inf), axis=1)
        tail_mass = alpha * counts
        whole = np.floor(tail_mass).astype(int)
        row_numbers = np.arange(batch_size)
        prefix = np.cumsum(np.where(np.isfinite(ordered), ordered, 0.0), axis=1)
        base = np.zeros(batch_size, dtype=float)
        has_whole = whole > 0
        base[has_whole] = prefix[row_numbers[has_whole], whole[has_whole] - 1]
        boundary = ordered[row_numbers, whole]
        boot_cvars.append((base + (tail_mass - whole) * boundary) / tail_mass)
    boot_mean_array = np.concatenate(boot_means)
    boot_median_array = np.concatenate(boot_medians)
    boot_cvar_array = np.concatenate(boot_cvars)
    boot_means_list = boot_mean_array.tolist()
    boot_medians_list = boot_median_array.tolist()
    boot_cvars_list = boot_cvar_array.tolist()
    point_mean = fmean(values_by_index.values())
    point_median = float(median(values_by_index.values()))
    point_cvar = _cvar(list(values_by_index.values()), alpha)
    mean_lcb = point_mean - float(_type7(boot_means_list, 0.95) - point_mean)
    median_lcb = point_median - float(_type7(boot_medians_list, 0.95) - point_median)
    cvar_lcb = point_cvar - float(_type7(boot_cvars_list, 0.95) - point_cvar) if point_cvar is not None else None
    def p_value(samples: list[float], point: float, threshold: float) -> float:
        return (1 + sum((sample - point) >= (point - threshold) for sample in samples)) / (repetitions + 1)
    return {"valid": True, "mean_lcb": mean_lcb, "median_lcb": median_lcb,
            "cvar_lcb": cvar_lcb, "mean_p": p_value(boot_means_list, point_mean, 0.0),
            "median_p": p_value(boot_medians_list, point_median, 0.0),
            "cvar_p": p_value(boot_cvars_list, float(point_cvar), -0.10)}


def _split(features: tuple[EWMAFeature, ...], config: EWMARefreshConfig) -> tuple[int | None, int | None, str]:
    valid = [i for i, row in enumerate(features) if row.standardized_rank is not None]
    if not valid:
        return None, None, "insufficient_features"
    first = valid[0]
    n = len(valid)
    train = int(n * 0.60)
    validation = int(n * 0.20)
    capacity = int(np.ceil(max(0, validation - config.holding_days) / (config.cooldown_rows + 1)))
    if capacity < config.exploration_min_events:
        train = n // 3
        validation = n // 3
        method = "three_equal_capacity_fallback"
    else:
        method = "60_20_20"
    val_start = valid[min(train, n - 1)]
    test_offset = min(train + validation, n - 1)
    test_start = valid[test_offset]
    return val_start, test_start, method


def _holm(candidate_ps: dict[tuple[str, str], float | None], alpha: float = 0.05) -> dict[tuple[str, str], float | None]:
    valid = sorted((p if p is not None else 1.0, key) for key, p in candidate_ps.items())
    adjusted: dict[tuple[str, str], float | None] = {}
    running = 0.0
    total = len(valid)
    for rank, (p, key) in enumerate(valid):
        running = max(running, min(1.0, (total - rank) * p))
        adjusted[key] = running if candidate_ps.get(key) is not None else None
    return adjusted


def _outcome_map(results: tuple[ExitResult, ...], labels: tuple[FixedPathLabel, ...], val_start: int,
                 cutoff: int, k: int) -> dict[tuple[str, str], dict[int, float]]:
    index_by_key = {(row.event_date, row.state): row.event_index for row in labels}
    label_map = {row.event_index: row for row in labels}
    output: dict[tuple[str, str], dict[int, float]] = {}
    for row in results:
        if (row.state not in STATES or row.net_return is None or not row.complete or row.entry_index is None):
            continue
        idx = index_by_key.get((row.event_date, row.state))
        if idx is None or idx < val_start or idx > cutoff or idx + k > cutoff:
            continue
        label = label_map[idx]
        if label is None or not label.complete:
            continue
        output.setdefault((row.state, row.policy), {})[idx] = row.net_return
    return output


def _match_increments(
    rows: list[PriceRow], features: tuple[EWMAFeature, ...], events: tuple[EWMAEvent, ...],
    target_results: tuple[ExitResult, ...],
    calibration_by_state: dict[str, list[Calibration]], val_start: int, cutoff: int,
    config: EWMARefreshConfig,
) -> dict[tuple[str, str], dict[int, float]]:
    """Match non-target-state dates within calendar quarter and volatility."""
    feature_by_index = features
    target_results_by_key = {(result.event_date, result.state, result.policy): result for result in target_results}
    controls: dict[tuple[str, str, int], ExitResult] = {}
    for target_state in STATES:
        calibrations = calibration_by_state[target_state]
        for i in range(val_start, min(cutoff + 1, len(rows))):
            feature = feature_by_index[i]
            observed_state = _state(feature.standardized_rank)
            if observed_state is None or observed_state == target_state or feature.sigma_next is None or i + config.holding_days > cutoff:
                continue
            calibration = calibrations[i]
            if not calibration.valid:
                continue
            proxy = EWMAEvent(feature.symbol, feature.date, i, target_state, -(i + 1),
                              float(feature.raw_rank or 0.5), float(feature.standardized_rank), feature.sigma_next)
            for policy in EXIT_POLICIES:
                result = _simulate_exit(rows, proxy, calibration, policy, features, config)
                if result.complete and result.net_return is not None:
                    controls[(target_state, policy, i)] = result
    by_quarter: dict[tuple[str, str, tuple[int, int]], list[tuple[int, ExitResult]]] = {}
    for (state, policy, idx), result in controls.items():
        quarter = (rows[idx].date.year, (rows[idx].date.month - 1) // 3)
        by_quarter.setdefault((state, policy, quarter), []).append((idx, result))
    increments: dict[tuple[str, str], dict[int, float]] = {}
    for event in events:
        i = event.row_index
        if i < val_start or i + config.holding_days > cutoff:
            continue
        quarter = (rows[i].date.year, (rows[i].date.month - 1) // 3)
        for policy in EXIT_POLICIES:
            target = target_results_by_key.get((event.date, event.state, policy))
            if target is None or not target.complete or target.net_return is None:
                continue
            candidates: list[tuple[float, int, int, float]] = []
            for j, control in by_quarter.get((event.state, policy, quarter), []):
                if control.net_return is None or j == i:
                    continue
                sigma = feature_by_index[j].sigma_next
                if sigma is None or not 0.8 <= sigma / event.sigma_entry <= 1.25:
                    continue
                distance = abs(log(sigma / event.sigma_entry))
                candidates.append((distance, abs(j - i), j, control.net_return))
            candidates.sort(key=lambda item: (item[0], item[1], item[2]))
            selected = candidates[:5]
            # The manual requires at least three distinct effective matches.
            if len(selected) >= 3:
                mean_base = fmean(item[3] for item in selected)
                increments.setdefault((event.state, policy), {})[i] = target.net_return - mean_base
    return increments


def _build_approvals(
    rows: list[PriceRow], events: tuple[EWMAEvent, ...], labels: tuple[FixedPathLabel, ...],
    exit_results: tuple[ExitResult, ...], increments: dict[tuple[str, str], dict[int, float]],
    val_start: int, test_start: int, config: EWMARefreshConfig,
) -> tuple[Approval, ...]:
    approvals: list[Approval] = []
    reviews = [test_start]
    for review in reviews:
        cutoff = review - 1
        if cutoff < val_start:
            continue
        values = _outcome_map(exit_results, labels, val_start, cutoff, config.holding_days)
        calendar_days = cutoff - val_start + 1
        if calendar_days >= config.block_length:
            primary_nblocks = int(np.ceil(calendar_days / config.block_length))
            primary_starts = np.random.default_rng(config.bootstrap_seed + review).integers(
                0, calendar_days - config.block_length + 1,
                size=(config.bootstrap_repetitions, primary_nblocks),
            )
        else:
            primary_starts = None
        if calendar_days >= config.sensitivity_block_length:
            sensitivity_nblocks = int(np.ceil(calendar_days / config.sensitivity_block_length))
            sensitivity_starts = np.random.default_rng(config.bootstrap_seed + 100_000 + review).integers(
                0, calendar_days - config.sensitivity_block_length + 1,
                size=(config.bootstrap_repetitions, sensitivity_nblocks),
            )
        else:
            sensitivity_starts = None
        raw: dict[tuple[str, str], dict[str, object]] = {}
        candidate_ps: dict[tuple[str, str], float | None] = {}
        for state in STATES:
            for policy in EXIT_POLICIES:
                key = (state, policy)
                observations = values.get(key, {})
                matched = {i: value for i, value in increments.get(key, {}).items()
                           if val_start <= i <= cutoff and i + config.holding_days <= cutoff}
                ordered_values = list(observations.values())
                ordered_matched = list(matched.values())
                point_mean = fmean(ordered_values) if ordered_values else None
                point_median = float(median(ordered_values)) if ordered_values else None
                point_cvar = _cvar(ordered_values, config.alpha)
                point_increment = fmean(ordered_matched) if ordered_matched else None
                boot_main = _block_bootstrap(observations, val_start, cutoff, config.block_length,
                                             config.bootstrap_repetitions, config.bootstrap_seed + review, config.alpha,
                                             primary_starts)
                boot_match = _block_bootstrap(matched, val_start, cutoff, config.block_length,
                                              config.bootstrap_repetitions, config.bootstrap_seed + review, config.alpha,
                                              primary_starts)
                boot_sensitivity = _block_bootstrap(observations, val_start, cutoff,
                                                    config.sensitivity_block_length,
                                                    config.bootstrap_repetitions,
                                                    config.bootstrap_seed + 100_000 + review, config.alpha,
                                                    sensitivity_starts)
                bootstrap_valid = bool(boot_main["valid"] and boot_match["valid"])
                p_values = []
                if bootstrap_valid:
                    p_values = [float(boot_main["mean_p"]), float(boot_main["median_p"]),
                                float(boot_match["mean_p"]), float(boot_main["cvar_p"])]
                candidate_p = max(p_values) if p_values else None
                candidate_ps[key] = candidate_p
                raw[key] = {
                    "observations": observations, "matched": matched, "mean": point_mean,
                    "median": point_median, "increment": point_increment, "cvar": point_cvar,
                    "boot_main": boot_main, "boot_match": boot_match,
                    "boot_sensitivity": boot_sensitivity,
                    "bootstrap_valid": bootstrap_valid, "candidate_p": candidate_p,
                }
        adjusted_ps = _holm(candidate_ps)
        for (state, policy), item in raw.items():
            observations = item["observations"]
            matched = item["matched"]
            main = item["boot_main"]
            match = item["boot_match"]
            n_eval, n_matched = len(observations), len(matched)
            tail_mass = config.alpha * n_eval
            cvar_lcb = main["cvar_lcb"]
            mean_lcb = main["mean_lcb"]
            median_lcb = main["median_lcb"]
            increment_lcb = match["mean_lcb"]
            bootstrap_valid = bool(item["bootstrap_valid"])
            exploration = bool(
                n_eval >= config.exploration_min_events and n_matched >= config.exploration_min_events and
                tail_mass >= 5 and bootstrap_valid and item["mean"] is not None and item["mean"] > 0 and
                item["increment"] is not None and item["increment"] > 0 and cvar_lcb is not None and
                cvar_lcb >= config.cvar_floor
            )
            strict_lcbs_pass = bool(
                bootstrap_valid and mean_lcb is not None and mean_lcb > 0 and
                median_lcb is not None and median_lcb > 0 and increment_lcb is not None and
                increment_lcb > 0 and cvar_lcb is not None and cvar_lcb >= config.cvar_floor
            )
            strict_conditions = bool(
                n_eval >= config.strict_min_events and n_matched >= config.strict_min_events and
                tail_mass >= 10 and strict_lcbs_pass
            )
            holm_p = adjusted_ps.get((state, policy))
            strict_pass = strict_conditions and holm_p is not None and holm_p <= 0.05
            sensitivity = item["boot_sensitivity"]
            warnings = []
            if n_eval < config.exploration_min_events: warnings.append("exploration_evaluation_sample_below_100")
            if n_matched < config.exploration_min_events: warnings.append("exploration_matched_sample_below_100")
            if not bootstrap_valid: warnings.append("block_bootstrap_invalid")
            if mean_lcb is None or mean_lcb <= 0: warnings.append("mean_lcb_not_positive")
            if increment_lcb is None or increment_lcb <= 0: warnings.append("matched_increment_lcb_not_positive")
            if cvar_lcb is None or cvar_lcb < config.cvar_floor: warnings.append("cvar_lcb_below_floor")
            if item["median"] is None or item["median"] <= 0: warnings.append("median_net_return_not_positive")
            if not sensitivity["valid"]: warnings.append("126_day_sensitivity_invalid")
            elif sensitivity["cvar_lcb"] is None or sensitivity["cvar_lcb"] < config.cvar_floor:
                warnings.append("126_day_sensitivity_below_floor")
            if not strict_pass: warnings.append("strict_statistical_validation_not_passed")
            evidence_id = f"{policy}-{state}-{rows[review].date.isoformat()}-cutoff-{rows[cutoff].date.isoformat()}"
            approvals.append(Approval(
                review, rows[review].date, cutoff, evidence_id, policy, state,
                n_eval, n_matched, tail_mass, item["mean"], item["median"], item["increment"],
                item["cvar"], cvar_lcb, bool(exploration), bool(strict_pass),
                bool(n_eval >= config.strict_min_events and n_matched >= config.strict_min_events and tail_mass >= 10 and bootstrap_valid),
                bool(strict_lcbs_pass), holm_p, bootstrap_valid, tuple(warnings),
                sensitivity["cvar_lcb"], bool(sensitivity["valid"]),
            ))
    return tuple(approvals)


def _account_run(
    rows: list[PriceRow], features: tuple[EWMAFeature, ...], events: tuple[EWMAEvent, ...],
    calibrations: tuple[Calibration, ...], exit_results: tuple[ExitResult, ...], approvals: tuple[Approval, ...],
    test_start: int, policy: str, refresh: bool, config: EWMARefreshConfig,
) -> tuple[tuple[AccountSignal, ...], tuple[AccountTrade, ...], tuple[LedgerRow, ...], dict[str, object]]:
    account_name = f"{policy}_{'refresh' if refresh else 'fixed'}"
    by_event = {(row.event_date, row.state, row.policy): row for row in exit_results}
    event_by_index = {row.row_index: row for row in events}
    calibration_by_index = {(row.state, row.event_index): row for row in calibrations}
    approval_by_review_state = {(row.review_index, row.state): row for row in approvals if row.policy == policy}
    initial_allowed = {row.state for row in approvals if row.policy == policy and row.review_index == test_start and row.exploration_allowed}
    reviews = {row.review_index for row in approvals if row.policy == policy}
    cash = 1.0
    shares = 0.0
    active: dict[str, object] | None = None
    pending: tuple[EWMAEvent, ExitResult] | None = None
    ledger: list[LedgerRow] = []
    signals: list[AccountSignal] = []
    trades: list[AccountTrade] = []
    previous_equity: float | None = None
    peak = 1.0
    cumulative = 1.0
    for i in range(test_start, len(rows)):
        row = rows[i]
        # A close signal from yesterday is attempted at this row's open.
        if pending is not None:
            event, planned = pending
            pending = None
            if planned.entry_index == i and planned.entry_price is not None:
                equity_before = previous_equity if previous_equity is not None else cash
                invested = config.investment_fraction * equity_before
                cost = config.round_trip_cost * invested
                shares = invested / float(row.adjusted_open)
                cash = equity_before - invested - cost
                active = {"event": event, "planned": planned, "shares": shares,
                          "invested": invested, "cost": cost, "entry_equity": equity_before}
                if planned.complete and planned.exit_index == i:
                    raise RuntimeError("A-share T+1 invariant violated: exit on entry day")
            else:
                trades.append(AccountTrade(
                    account_name, event.date, event.state, row.date, i, float(row.adjusted_open),
                    None, None, None, planned.exit_reason, 0, 0.0, 0.0, 0.0, None,
                    "cancelled_or_not_filled", float(planned.a or 0), float(planned.b or 0), event.sigma_entry,
                ))
        # The forward simulation is used only as a causal execution schedule;
        # proceeds are posted when its exit row arrives.
        if active is not None:
            planned = active["planned"]
            if planned.complete and planned.exit_index == i:
                exit_price = float(planned.exit_price)
                cash += shares * exit_price
                trade_net = exit_price / float(planned.entry_price) - 1 - config.round_trip_cost
                event = active["event"]
                trades.append(AccountTrade(
                    account_name, event.date, event.state, rows[planned.entry_index].date, planned.entry_index,
                    float(planned.entry_price), row.date, i, exit_price, planned.exit_reason,
                    planned.holding_rows, shares, float(active["invested"]), float(active["cost"]), trade_net,
                    "completed", float(planned.a), float(planned.b), event.sigma_entry,
                    planned.ambiguous_double_touch,
                ))
                shares, active = 0.0, None
        equity = cash + shares * float(row.adjusted_close)
        if previous_equity is not None:
            daily_return = equity / previous_equity - 1 if previous_equity > 0 else None
            cumulative *= (1 + daily_return) if daily_return is not None else 1.0
        else:
            daily_return = None
        peak = max(peak, equity)
        drawdown = equity / peak - 1 if peak > 0 else 0.0
        # A review is made after this row's close from data through i-1.
        if refresh and i in reviews and i != test_start:
            pass  # immutable approval snapshots already exist at the close.
        event = event_by_index.get(i)
        state = _state(features[i].standardized_rank)
        approved = state in initial_allowed if not refresh else False
        if refresh:
            eligible_reviews = [review for review in reviews if review <= i]
            if eligible_reviews and state is not None:
                latest = max(eligible_reviews)
                snapshot = approval_by_review_state.get((latest, state))
                approved = bool(snapshot and snapshot.exploration_allowed)
        reason = "no_retained_state_event"
        decision = "no_buy"
        calibration: Calibration | None = None
        if event is not None:
            calibration = calibration_by_index.get((state or "", i))
            if active is not None:
                reason = "account_position_open"
            elif pending is not None:
                reason = "buy_already_pending"
            elif not approved:
                reason = "state_not_exploration_approved"
            elif calibration is None or not calibration.valid:
                reason = "calibration_unavailable"
            else:
                planned = by_event.get((event.date, event.state, policy))
                if planned is None:
                    reason = "execution_result_unavailable"
                elif planned.entry_index is None:
                    reason, decision = "pending_entry_no_next_row", "pending"
                    trades.append(AccountTrade(account_name, event.date, event.state, event.date, i,
                                               None, None, None, None, reason, None, 0.0, 0.0, 0.0,
                                               None, "pending", float(planned.a or 0), float(planned.b or 0),
                                               event.sigma_entry))
                else:
                    pending = (event, planned)
                    reason, decision = "next_input_row_open", "buy_pending"
        signals.append(AccountSignal(row.date, state, event.event_number if event else None, policy,
                                     bool(approved), decision, reason,
                                     calibration.a if calibration else None,
                                     calibration.b if calibration else None,
                                     features[i].sigma_next, account_name))
        ledger.append(LedgerRow(account_name, row.date, cash, shares, float(row.adjusted_close), equity,
                                daily_return, cumulative, drawdown, decision))
        previous_equity = equity
    # A signal on the final close has no following input row.
    if pending is not None:
        event, planned = pending
        trades.append(AccountTrade(account_name, event.date, event.state, event.date, event.row_index,
                                   None, None, None, None, "pending_entry_no_next_row", None,
                                   0.0, 0.0, 0.0, None, "pending", float(planned.a or 0),
                                   float(planned.b or 0), event.sigma_entry))
    if active is not None:
        event, planned = active["event"], active["planned"]
        trades.append(AccountTrade(
            account_name, event.date, event.state, rows[planned.entry_index].date, planned.entry_index,
            float(planned.entry_price), None, None, None, "unfinished_mark_to_market",
            len(rows) - 1 - planned.entry_index, shares, float(active["invested"]), float(active["cost"]),
            None, "unfinished", float(planned.a), float(planned.b), event.sigma_entry,
            planned.ambiguous_double_touch,
        ))
    returns = [row.daily_return for row in ledger if row.daily_return is not None]
    total = ledger[-1].equity / ledger[0].equity - 1 if ledger else 0.0
    periods = max(1, len(returns))
    cagr = (ledger[-1].equity / ledger[0].equity) ** (252 / periods) - 1 if ledger and ledger[-1].equity > 0 else None
    sharpe = sqrt(252) * fmean(returns) / stdev(returns) if len(returns) > 1 and stdev(returns) > 0 else None
    prior_equity_by_date: dict[date, float] = {}
    prior_value = ledger[0].equity if ledger else 1.0
    for row in ledger:
        prior_equity_by_date[row.date] = prior_value
        prior_value = row.equity
    turnover_by_date: dict[date, float] = {row.date: 0.0 for row in ledger}
    exposure_dates = {row.date for row in ledger if row.shares > 0}
    for trade in trades:
        if trade.status != "completed" and trade.status != "unfinished":
            continue
        turnover_by_date[trade.entry_date] = turnover_by_date.get(trade.entry_date, 0.0) + trade.invested_amount
        exposure_dates.add(trade.entry_date)
        if trade.exit_date is not None and trade.exit_price is not None:
            turnover_by_date[trade.exit_date] = turnover_by_date.get(trade.exit_date, 0.0) + trade.shares * trade.exit_price
            exposure_dates.add(trade.exit_date)
    daily_turnover = [turnover_by_date[item.date] / prior_equity_by_date[item.date]
                      for item in ledger if prior_equity_by_date[item.date] > 0]
    summary = {
        "account": account_name, "policy": policy, "approval_mode": "refresh" if refresh else "fixed_initial",
        "initial_equity": ledger[0].equity if ledger else 1.0,
        "final_equity": ledger[-1].equity if ledger else 1.0,
        "total_return": total, "cagr": cagr, "max_drawdown": min((row.drawdown for row in ledger), default=0.0),
        "sharpe_conventional": sharpe,
        "exposure_day_ratio": len(exposure_dates) / len(ledger) if ledger else 0.0,
        "mean_daily_turnover": fmean(daily_turnover) if daily_turnover else 0.0,
        "completed_trades": sum(row.status == "completed" for row in trades),
        "cancelled_trades": sum(row.status == "cancelled_or_not_filled" for row in trades),
        "pending_entries": sum(row.status == "pending" for row in trades),
        "unfinished_positions": sum(row.status == "unfinished" for row in trades),
        "total_cost": sum(row.cost for row in trades),
        "any_strictly_validated_state_snapshot": any(row.statistically_validated
                                                      for row in approvals if row.policy == policy and
                                                      (row.review_index == test_start if not refresh else True)),
        "exploration_allowed_states_initial": sorted(initial_allowed),
    }
    return tuple(signals), tuple(trades), tuple(ledger), summary


def _passive_baseline(
    rows: list[PriceRow], test_start: int, config: EWMARefreshConfig,
) -> tuple[tuple[LedgerRow, ...], dict[str, object]]:
    """Same-budget buy-and-hold reference from the first test-period open."""
    account = "PASSIVE_BUY_HOLD"
    cash, shares, cost = 1.0, 0.0, 0.0
    invested = 0.0
    ledger: list[LedgerRow] = []
    previous_equity: float | None = None
    cumulative, peak = 1.0, 1.0
    for i in range(test_start, len(rows)):
        row = rows[i]
        decision = "hold_cash"
        if i == test_start:
            decision = "initial_cash_anchor"
        elif i == test_start + 1:
            can_buy, reason = _can_buy(row)
            if can_buy:
                prior_equity = previous_equity if previous_equity is not None else 1.0
                invested = config.investment_fraction * prior_equity
                cost = config.round_trip_cost * invested
                shares = invested / float(row.adjusted_open)
                cash = prior_equity - invested - cost
                decision = "buy_and_hold_open"
            else:
                decision = reason
        equity = cash + shares * float(row.adjusted_close)
        daily_return = equity / previous_equity - 1 if previous_equity is not None and previous_equity > 0 else None
        if daily_return is not None:
            cumulative *= 1 + daily_return
        peak = max(peak, equity)
        ledger.append(LedgerRow(account, row.date, cash, shares, float(row.adjusted_close), equity,
                                daily_return, cumulative, equity / peak - 1, decision))
        previous_equity = equity
    returns = [row.daily_return for row in ledger if row.daily_return is not None]
    total = ledger[-1].equity / ledger[0].equity - 1 if ledger else 0.0
    periods = max(1, len(returns))
    return_annual = (ledger[-1].equity / ledger[0].equity) ** (252 / periods) - 1 if ledger and ledger[-1].equity > 0 else None
    sharpe = sqrt(252) * fmean(returns) / stdev(returns) if len(returns) > 1 and stdev(returns) > 0 else None
    summary = {
        "account": account, "approval_mode": "benchmark_same_budget_buy_and_hold",
        "initial_equity": ledger[0].equity if ledger else 1.0,
        "final_equity": ledger[-1].equity if ledger else 1.0,
        "total_return": total, "cagr": return_annual,
        "max_drawdown": min((row.drawdown for row in ledger), default=0.0),
        "sharpe_conventional": sharpe, "completed_trades": 0,
        "cancelled_trades": int(len(rows) > test_start + 1 and shares == 0),
        "pending_entries": 0, "unfinished_positions": int(shares > 0),
        "total_cost": cost,
    }
    return tuple(ledger), summary


def run_ewma_refresh_backtest(
    prices: Iterable[PriceRow], config: EWMARefreshConfig | None = None,
) -> EWMARefreshResult:
    """Run the independent A-share EWMA strategy on one OHLC symbol.

    Output includes descriptive overlapping event statistics and six account
    ledgers (three exits by refreshed/frozen exploration approval).
    """
    cfg = config or EWMARefreshConfig()
    rows = sorted(list(prices), key=lambda row: (row.symbol.upper(), row.date))
    symbol = _require_a_share_ohlc(rows, cfg)
    features = compute_ewma_features(rows, cfg)
    events = _mark_events(features, rows, cfg)
    labels = _fixed_labels(rows, events, cfg)
    horizon_outcomes = _fixed_horizon_outcomes(rows, events, cfg)
    horizon_summaries = _summarize_horizons(horizon_outcomes, cfg)
    path_quantiles, path_drawdowns = _path_research(rows, events, cfg)
    direction_summaries = _direction_summaries(events, features, horizon_outcomes, cfg)
    n = len(rows)
    calibrations_list: list[Calibration] = []
    calibration_by_state: dict[str, list[Calibration]] = {state: [] for state in STATES}
    for state in STATES:
        for i in range(n):
            calibration = _calibrate(state, i, labels, cfg)
            calibration_by_state[state].append(calibration)
            calibrations_list.append(calibration)
    calibrations = tuple(calibrations_list)
    exits: list[ExitResult] = []
    for event in events:
        calibration = calibration_by_state[event.state][event.row_index]
        for policy in EXIT_POLICIES:
            exits.append(_simulate_exit(rows, event, calibration, policy, features, cfg))
    exit_results = tuple(exits)
    val_start, test_start, split_method = _split(features, cfg)
    if val_start is None or test_start is None:
        empty_summary = ({"account": "none", "explanation": "fewer than one valid standardized rank"},)
        return EWMARefreshResult(STRATEGY_ID, symbol, cfg, None, None, None, None, split_method,
                                 _input_snapshot(rows, symbol), features, events, labels, horizon_outcomes, horizon_summaries,
                                 path_quantiles, path_drawdowns, direction_summaries,
                                 calibrations, exit_results, (), (), (), empty_summary,
                                 ("not enough history to form q_vol",))
    increments: dict[tuple[str, str], dict[int, float]] = {}
    approvals_list: list[Approval] = []
    for review in range(test_start, n, cfg.refresh_interval):
        cutoff = review - 1
        if cutoff < val_start:
            continue
        review_increments = _match_increments(rows, features, events, exit_results,
                                              calibration_by_state, val_start, cutoff, cfg)
        for key, pairs in review_increments.items():
            increments.setdefault(key, {}).update(pairs)
        approvals_list.extend(_build_approvals(rows, events, labels, exit_results, increments,
                                               val_start, review, cfg))
    approvals = tuple(approvals_list)
    all_signals: list[AccountSignal] = []
    all_trades: list[AccountTrade] = []
    all_ledger: list[LedgerRow] = []
    summaries: list[dict[str, object]] = []
    for policy in EXIT_POLICIES:
        for refresh in (True, False):
            signals, trades, ledger, summary = _account_run(rows, features, events, calibrations,
                                                            exit_results, approvals, test_start,
                                                            policy, refresh, cfg)
            all_signals.extend(signals)
            all_trades.extend(trades)
            all_ledger.extend(ledger)
            summaries.append(summary)
    passive_ledger, passive_summary = _passive_baseline(rows, test_start, cfg)
    all_ledger.extend(passive_ledger)
    summaries.append(passive_summary)
    # Explicitly label the event table as overlapping conditionals; no event
    # return is compounded into the account equity curve.
    event_summaries: list[dict[str, object]] = []
    path_summaries: list[dict[str, object]] = []
    for state in STATES:
        state_path = [item for item in path_quantiles if item.state == state]
        positive_days = [item.horizon for item in state_path
                         if item.median_net_return is not None and item.median_net_return > 0]
        state_drawdowns = [item.log_drawdown for item in path_drawdowns if item.state == state]
        path_summaries.append({
            "statistic_type": "complete_21_day_path_diagnostic_not_account_metric",
            "state": state,
            "complete_21_day_event_count": max((item.complete_21_day_event_count for item in state_path), default=0),
            "median_net_path_first_positive_day": min(positive_days) if positive_days else None,
            "median_21_day_log_drawdown": median(state_drawdowns) if state_drawdowns else None,
            "q05_21_day_log_drawdown": _type7(state_drawdowns, 0.05),
            "q95_21_day_log_drawdown": _type7(state_drawdowns, 0.95),
            "path_band_is_not_a_confidence_interval": True,
        })
    label_by_key = {(row.event_date, row.state): row for row in labels}
    for state in STATES:
        for policy in EXIT_POLICIES:
            result_rows = [row for row in exit_results if row.state == state and row.policy == policy and row.complete and row.net_return is not None]
            values = [float(row.net_return) for row in result_rows]
            event_summaries.append({
                "statistic_type": "overlapping_event_conditional_not_account_return",
                "state": state, "exit_policy": policy, "event_count": len(values),
                "mean_net_return": fmean(values) if values else None,
                "median_net_return": median(values) if values else None,
                "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
                "q05_net_return": _type7(values, 0.05), "q25_net_return": _type7(values, 0.25),
                "q75_net_return": _type7(values, 0.75), "q95_net_return": _type7(values, 0.95),
                "cvar05_net_return": _cvar(values, cfg.alpha),
                "complete_fixed_k_and_exit_count": sum(
                    row.complete and row.net_return is not None and
                    label_by_key.get((row.event_date, row.state), FixedPathLabel("", row.event_date, 0, "", None, None, None, None, None, None, None, False)).complete
                    for row in result_rows),
            })
    issues = ["daily adjusted OHLC must be quality-checked; historical data quality is not certified by this runner"]
    missing_limits = len(rows) - sum(row.price_limits_verified for row in rows)
    if missing_limits:
        issues.append(f"verified daily price-limit fields are unavailable for {missing_limits} rows; single-price fills are conservatively blocked")
    missing_raw = len(rows) - sum(row.raw_open is not None and row.raw_high is not None and
                                  row.raw_low is not None and row.raw_close is not None for row in rows)
    if missing_raw:
        issues.append(f"raw OHLC provenance is unavailable for {missing_raw} rows; adjustment continuity cannot be audited from this input alone")
    missing_factor = len(rows) - sum(row.adjustment_factor is not None for row in rows)
    if missing_factor:
        issues.append(f"adjustment factors are unavailable for {missing_factor} rows; this run does not certify corporate-action continuity")
    missing_status = len(rows) - sum(bool(row.trading_status) for row in rows)
    if missing_status:
        issues.append(f"explicit trading status is unavailable for {missing_status} rows; suspension checks rely on supplied status and volume fields")
    missing_volume = sum(row.volume is None for row in rows)
    if missing_volume:
        issues.append(f"volume is unavailable for {missing_volume} rows; zero-volume fill blocking cannot be verified on those rows")
    if not any(row.exploration_allowed for row in approvals):
        issues.append("no state/exit pair passed exploration approval; this is not evidence of statistically validated advantage")
    if any(row.adjusted_open is None for row in rows):
        issues.append("OHLC input incomplete")
    return EWMARefreshResult(
        STRATEGY_ID, symbol, cfg,
        next((i for i, feature in enumerate(features) if feature.standardized_rank is not None), None),
        next((i for i, feature in enumerate(features) if feature.standardized_rank is not None), 0),
        val_start, test_start, split_method, _input_snapshot(rows, symbol), features, events, labels, horizon_outcomes,
        horizon_summaries, path_quantiles, path_drawdowns, direction_summaries, calibrations,
        exit_results, approvals, tuple(all_signals), tuple(all_trades), tuple(all_ledger),
        tuple(event_summaries + list(horizon_summaries) + path_summaries +
              [asdict(item) for item in direction_summaries] + summaries),
        tuple(issues),
    )
