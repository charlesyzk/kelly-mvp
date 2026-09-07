"""No-lookahead six-model rolling evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite, log
from statistics import fmean, pstdev
from typing import Iterable, Mapping

from .config import FREQUENCIES, MODEL_IDS, POSITION_TYPES, StrategyConfig
from .data import PriceRow, aggregate_prices, split_by_symbol
from .model import empirical_objective, solve_all_models


PERIODS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12}


@dataclass(frozen=True, slots=True)
class SignalResult:
    symbol: str
    frequency: str
    signal_date: date
    window_start_date: date
    window_end_date: date
    window_size: int
    model_id: str
    position_type: str
    position_value: float | None
    previous_position: float | None
    position_change: float | None
    turnover: float | None
    m1: float
    m2: float
    m3: float
    m4: float
    mu: float
    nu2: float
    nu3: float
    nu4: float
    q1: float
    q2: float
    q3: float
    q4: float
    raw_objective: float | None
    bounded_objective: float
    safe_objective: float
    position_objective: float | None
    exact_empirical_objective_loss: float | None
    solution_location: str
    raw_solution_type: str
    safe_domain_type: str
    safe_domain_intervals: str
    kappa: float
    wealth_floor: float
    status: str
    evaluation_status: str


@dataclass(frozen=True, slots=True)
class PeriodResult:
    symbol: str
    frequency: str
    signal_date: date
    return_date: date
    window_start_date: date
    window_end_date: date
    window_size: int
    model_id: str
    position_type: str
    position_value: float | None
    previous_position: float | None
    position_change: float | None
    next_return: float
    wealth_multiplier: float | None
    truncated_log_growth: float | None
    direction_success: bool | None
    wealth_feasible: bool | None
    bankrupt: bool
    cumulative_wealth: float
    buy_hold_wealth: float
    turnover: float | None
    m1: float
    m2: float
    m3: float
    m4: float
    mu: float
    nu2: float
    nu3: float
    nu4: float
    q1: float
    q2: float
    q3: float
    q4: float
    raw_objective: float | None
    bounded_objective: float
    safe_objective: float
    position_objective: float | None
    exact_empirical_objective_loss: float | None
    solution_location: str
    raw_solution_type: str
    safe_domain_type: str
    status: str


@dataclass(frozen=True, slots=True)
class SummaryResult:
    symbol: str
    frequency: str
    model_id: str
    position_type: str
    window: int
    opportunities: int
    observations: int
    active_observations: int
    direction_observations: int
    coverage: float
    direction_accuracy: float | None
    wealth_feasibility_rate: float | None
    bankruptcies: int
    average_truncated_log_growth: float | None
    total_return: float | None
    annualized_return: float | None
    buy_hold_return: float
    growth_difference_vs_buy_hold: float | None
    max_drawdown: float | None
    annualized_volatility: float | None
    average_turnover: float | None
    average_abs_position: float | None
    mean_abs_exact_position_gap: float | None
    mean_exact_empirical_objective_loss: float | None
    exact_direction_agreement: float | None
    boundary_rate: float | None
    formal_sample_eligible: bool


@dataclass(frozen=True, slots=True)
class TradeResult:
    symbol: str
    frequency: str
    model_id: str
    position_type: str
    signal_date: date
    return_date: date | None
    action: str
    previous_position: float
    target_position: float
    position_change: float
    turnover: float
    next_return: float | None
    wealth_multiplier: float | None
    truncated_log_growth: float | None
    cumulative_wealth: float | None
    evaluation_status: str
    research_only: bool


@dataclass(frozen=True, slots=True)
class BacktestResult:
    periods: tuple[PeriodResult, ...]
    signals: tuple[SignalResult, ...]
    trades: tuple[TradeResult, ...]
    summaries: tuple[SummaryResult, ...]
    issues: tuple[str, ...]

    def period_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.periods]

    def signal_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.signals]

    def summary_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.summaries]

    def trade_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.trades]


def classify_trade(previous: float | None, target: float | None, tolerance: float = 1e-12) -> str | None:
    """Classify a target-position change without implying broker execution."""

    if previous is None or target is None or abs(target - previous) <= tolerance:
        return None
    previous_is_zero = abs(previous) <= tolerance
    target_is_zero = abs(target) <= tolerance
    if previous_is_zero:
        return "open_long" if target > 0 else "open_short"
    if target_is_zero:
        return "close_long" if previous > 0 else "close_short"
    if previous > 0 > target:
        return "reverse_to_short"
    if previous < 0 < target:
        return "reverse_to_long"
    if previous > 0:
        return "add_long" if target > previous else "reduce_long"
    return "add_short" if target < previous else "cover_short"


def _returns(prices: list[PriceRow]) -> list[float]:
    return [prices[i].adjusted_close / prices[i - 1].adjusted_close - 1 for i in range(1, len(prices))]


def _compound(values: Iterable[float]) -> float:
    wealth = 1.0
    for value in values:
        wealth *= max(0.0, 1 + value)
    return wealth - 1


def _drawdown(values: Iterable[float]) -> float:
    wealth = peak = 1.0
    worst = 0.0
    for value in values:
        wealth *= max(0.0, 1 + value)
        peak = max(peak, wealth)
        worst = min(worst, wealth / peak - 1 if peak else -1.0)
    return worst


def _frequency_rows(
    data: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]], frequency: str
) -> list[PriceRow]:
    if isinstance(data, Mapping):
        return sorted(tuple(data.get(frequency, ())), key=lambda row: (row.symbol, row.date))
    rows = tuple(data)
    return sorted(
        (row for group in split_by_symbol(rows).values() for row in aggregate_prices(group, frequency)),
        key=lambda row: (row.symbol, row.date),
    )


def _summary(
    rows: list[PeriodResult],
    window: int,
    minimum_matches: int,
    exact_rows: Mapping[tuple[date, date], PeriodResult],
    lower_bound: float,
    upper_bound: float,
) -> SummaryResult:
    first = rows[0]
    available = [row for row in rows if row.position_value is not None]
    active = [row for row in available if abs(row.position_value or 0.0) > 1e-12]
    directional = [row for row in active if row.direction_success is not None]
    growth = [row.truncated_log_growth for row in available if row.truncated_log_growth is not None]
    returns = [(row.wealth_multiplier or 0.0) - 1 for row in available]
    benchmark = [row.next_return for row in rows]
    n = len(available)
    annualizer = PERIODS_PER_YEAR[first.frequency]
    total = _compound(returns) if n else None
    annualized = None
    if total is not None:
        annualized = (1 + total) ** (annualizer / n) - 1 if total > -1 else -1.0
    average_growth = fmean(growth) if len(growth) == n and n else None
    benchmark_growth = fmean(log(max(1e-12, 1 + value)) for value in benchmark)
    volatility = pstdev(returns) * annualizer**0.5 if n > 1 else (0.0 if n == 1 else None)
    turnover = [row.turnover for row in available if row.turnover is not None]
    paired = [
        (row, exact_rows[(row.signal_date, row.return_date)])
        for row in available
        if (row.signal_date, row.return_date) in exact_rows
        and exact_rows[(row.signal_date, row.return_date)].position_value is not None
    ]
    directional_pairs = [
        (row.position_value, exact.position_value)
        for row, exact in paired
        if abs(row.position_value or 0.0) > 1e-12 and abs(exact.position_value or 0.0) > 1e-12
    ]
    return SummaryResult(
        symbol=first.symbol,
        frequency=first.frequency,
        model_id=first.model_id,
        position_type=first.position_type,
        window=window,
        opportunities=len(rows),
        observations=n,
        active_observations=len(active),
        direction_observations=len(directional),
        coverage=n / len(rows) if rows else 0.0,
        direction_accuracy=(sum(row.direction_success is True for row in directional) / len(directional) if directional else None),
        wealth_feasibility_rate=(sum(row.wealth_feasible is True for row in available) / n if n else None),
        bankruptcies=sum(row.bankrupt for row in available),
        average_truncated_log_growth=average_growth,
        total_return=total,
        annualized_return=annualized,
        buy_hold_return=_compound(benchmark),
        growth_difference_vs_buy_hold=(average_growth - benchmark_growth if average_growth is not None else None),
        max_drawdown=_drawdown(returns) if n else None,
        annualized_volatility=volatility,
        average_turnover=fmean(turnover) if turnover else None,
        average_abs_position=fmean(abs(row.position_value or 0.0) for row in available) if available else None,
        mean_abs_exact_position_gap=(fmean(abs((row.position_value or 0.0) - (exact.position_value or 0.0)) for row, exact in paired) if paired else None),
        mean_exact_empirical_objective_loss=(fmean(row.exact_empirical_objective_loss for row in available if row.exact_empirical_objective_loss is not None) if any(row.exact_empirical_objective_loss is not None for row in available) else None),
        exact_direction_agreement=(sum(left * right > 0 for left, right in directional_pairs) / len(directional_pairs) if directional_pairs else None),
        boundary_rate=(sum(abs((row.position_value or 0.0) - lower_bound) <= 1e-10 or abs((row.position_value or 0.0) - upper_bound) <= 1e-10 for row in available) / len(available) if available else None),
        formal_sample_eligible=n >= minimum_matches,
    )


def run_backtest(
    prices: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]],
    config: StrategyConfig | None = None,
) -> BacktestResult:
    active = config or StrategyConfig()
    periods: list[PeriodResult] = []
    signals: list[SignalResult] = []
    issues: list[str] = []
    states: dict[tuple[str, str, str, str], tuple[float | None, float]] = {}
    benchmark_states: dict[tuple[str, str], float] = {}

    for frequency in FREQUENCIES:
        rows = _frequency_rows(prices, frequency)
        if not rows:
            issues.append(f"{frequency}: no provider-supplied price series")
            continue
        for symbol, symbol_prices in split_by_symbol(rows).items():
            returns = _returns(symbol_prices)
            window = active.windows[frequency]
            if len(returns) <= window:
                issues.append(f"{symbol}/{frequency}: need at least {window + 2} prices; got {len(symbol_prices)}")
                continue
            for signal_index in range(window, len(returns) + 1):
                sample = returns[signal_index - window:signal_index]
                decisions = solve_all_models(
                    sample,
                    lower=active.lower_bound,
                    upper=active.upper_bound,
                    kappa=active.convergence_kappa,
                    wealth_floor=active.wealth_floor,
                )
                evaluated = signal_index < len(returns)
                exact_decision = next(item for item in decisions if item.model_id == "EMPIRICAL_EXACT")
                exact_positions = dict(exact_decision.positions())
                for decision in decisions:
                    intervals = json.dumps(decision.safe_domain_intervals, separators=(",", ":"))
                    for position_type, position in decision.positions():
                        key = (symbol, frequency, decision.model_id, position_type)
                        previous, wealth = states.get(key, (0.0, 1.0))
                        change = None if position is None or previous is None else position - previous
                        turnover = abs(change) if change is not None else None
                        position_objective = {
                            "RAW": decision.raw_objective,
                            "BOUNDED": decision.bounded_objective,
                            "SAFE": decision.safe_objective,
                        }[position_type]
                        exact_position = exact_positions[position_type]
                        exact_loss = None
                        if position is not None and exact_position is not None:
                            selected_exact_value = empirical_objective(position, sample)
                            best_exact_value = empirical_objective(exact_position, sample)
                            if isfinite(selected_exact_value) and isfinite(best_exact_value):
                                exact_loss = max(0.0, best_exact_value - selected_exact_value)
                        location = (
                            "missing" if position is None
                            else "lower_bound" if abs(position - active.lower_bound) <= 1e-10
                            else "upper_bound" if abs(position - active.upper_bound) <= 1e-10
                            else "interior"
                        )
                        moments = decision.moments
                        signals.append(SignalResult(
                            symbol, frequency, symbol_prices[signal_index].date,
                            symbol_prices[signal_index - window].date,
                            symbol_prices[signal_index].date, window,
                            decision.model_id, position_type, position, previous,
                            change, turnover,
                            moments.m1, moments.m2, moments.m3, moments.m4,
                            moments.mu, moments.nu2, moments.nu3, moments.nu4,
                            moments.q1, moments.q2, moments.q3, moments.q4,
                            decision.raw_objective, decision.bounded_objective,
                            decision.safe_objective, position_objective, exact_loss,
                            location,
                            decision.raw_solution_type, decision.safe_domain_type,
                            intervals, active.convergence_kappa, active.wealth_floor,
                            decision.status, "evaluated" if evaluated else "pending",
                        ))
                        if not evaluated:
                            continue
                        next_return = returns[signal_index]
                        multiplier = None if position is None else 1 + position * next_return
                        feasible = None if multiplier is None else multiplier > 0 and isfinite(multiplier)
                        bankrupt = multiplier is not None and not feasible
                        truncated = None if multiplier is None else log(max(active.wealth_floor, multiplier))
                        if multiplier is not None:
                            wealth *= max(0.0, multiplier) if isfinite(multiplier) else 0.0
                        benchmark_key = (symbol, frequency)
                        if decision.model_id == MODEL_IDS[0] and position_type == POSITION_TYPES[0]:
                            benchmark_states[benchmark_key] = benchmark_states.get(benchmark_key, 1.0) * max(0.0, 1 + next_return)
                        buy_hold_wealth = benchmark_states.get(benchmark_key, 1.0)
                        direction = None
                        if position is not None and abs(position) > 1e-12:
                            direction = position * next_return > 0
                        periods.append(PeriodResult(
                            symbol, frequency, symbol_prices[signal_index].date,
                            symbol_prices[signal_index + 1].date,
                            symbol_prices[signal_index - window].date,
                            symbol_prices[signal_index].date, window,
                            decision.model_id, position_type, position, previous,
                            change, next_return, multiplier, truncated, direction,
                            feasible, bankrupt, wealth, buy_hold_wealth, turnover,
                            moments.m1, moments.m2, moments.m3, moments.m4,
                            moments.mu, moments.nu2, moments.nu3, moments.nu4,
                            moments.q1, moments.q2, moments.q3, moments.q4,
                            decision.raw_objective, decision.bounded_objective,
                            decision.safe_objective, position_objective, exact_loss,
                            location,
                            decision.raw_solution_type, decision.safe_domain_type,
                            decision.status,
                        ))
                        states[key] = (position, wealth)

    grouped: dict[tuple[str, str, str, str], list[PeriodResult]] = {}
    for row in periods:
        grouped.setdefault((row.symbol, row.frequency, row.model_id, row.position_type), []).append(row)
    summaries = tuple(
        _summary(
            rows,
            active.windows[key[1]],
            active.minimum_matches[key[1]],
            {
                (row.signal_date, row.return_date): row
                for row in grouped.get((key[0], key[1], "EMPIRICAL_EXACT", key[3]), ())
            },
            active.lower_bound,
            active.upper_bound,
        )
        for key, rows in sorted(grouped.items())
    )
    period_lookup = {
        (row.symbol, row.frequency, row.model_id, row.position_type, row.signal_date): row
        for row in periods
    }
    trades = tuple(
        TradeResult(
            signal.symbol, signal.frequency, signal.model_id, signal.position_type,
            signal.signal_date, period.return_date if period else None, action,
            signal.previous_position, signal.position_value, signal.position_change,
            signal.turnover, period.next_return if period else None,
            period.wealth_multiplier if period else None,
            period.truncated_log_growth if period else None,
            period.cumulative_wealth if period else None,
            signal.evaluation_status, signal.position_type == "RAW",
        )
        for signal in signals
        if (action := classify_trade(signal.previous_position, signal.position_value)) is not None
        for period in [period_lookup.get((signal.symbol, signal.frequency, signal.model_id, signal.position_type, signal.signal_date))]
    )
    return BacktestResult(tuple(periods), tuple(signals), trades, summaries, tuple(issues))
