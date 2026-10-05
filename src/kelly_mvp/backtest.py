"""No-lookahead six-model rolling evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from math import isfinite, log
from statistics import fmean, pstdev
from typing import Iterable, Mapping

import numpy as np

from .config import FREQUENCIES, MODEL_IDS, POSITION_TYPES, StrategyConfig
from .data import PriceRow, aggregate_prices, split_by_symbol, suspected_adjustment_anomalies
from .model import empirical_objective, estimate_moments, solve_model
from .module_strategy import KELLY_STRATEGY_ID, StrategyContext, StrategyDefinition


PERIODS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12}
STOP_VARIANTS = ("WITHOUT_STOP", "WITH_STOP")


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
    wealth_floor: float
    status: str
    evaluation_status: str
    strategy_name: str = "Kelly 六模型策略"
    diagnostics: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    optimized_position: float | None = None
    position_status: str = "optimized"
    stop_variant: str = "WITHOUT_STOP"
    contains_suspected_adjustment_anomaly: bool = False


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
    strategy_name: str = "Kelly 六模型策略"
    diagnostics: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    optimized_position: float | None = None
    position_status: str = "optimized"
    stop_variant: str = "WITHOUT_STOP"
    asset_return_realized: float | None = None
    stop_triggered: bool = False
    contains_suspected_adjustment_anomaly: bool = False


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
    strategy_name: str = "Kelly 六模型策略"
    stop_variant: str = "WITHOUT_STOP"


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
    strategy_name: str = "Kelly 六模型策略"
    stop_variant: str = "WITHOUT_STOP"


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


@dataclass(frozen=True, slots=True)
class _StopState:
    direction: int
    peak_close: float
    trough_close: float
    stop_price: float
    active: bool = True
    exit_price: float | None = None


def _apply_close_stop(
    start_close: float,
    path: list[tuple[date, float]],
    position: float,
    prior: _StopState | None,
    volatility: float,
    long_k: float,
    short_k: float,
) -> tuple[float, _StopState | None, bool, bool]:
    """Apply prior-close stop first, then trail for the following close."""
    if abs(position) <= 1e-12:
        return 0.0, None, False, False
    direction = 1 if position > 0 else -1
    if prior is None or prior.direction != direction:
        prior = _StopState(direction, start_close, start_close, start_close * (np.exp(-long_k * volatility) if direction > 0 else np.exp(short_k * volatility)))
    elif not prior.active:
        recovered = start_close > (prior.exit_price or prior.stop_price) if direction > 0 else start_close < (prior.exit_price or prior.stop_price)
        if not recovered:
            return 0.0, prior, False, True
        prior = _StopState(direction, start_close, start_close, start_close * (np.exp(-long_k * volatility) if direction > 0 else np.exp(short_k * volatility)))
    for _, close in path:
        crossed = close <= prior.stop_price if direction > 0 else close >= prior.stop_price
        if crossed:
            realized = prior.stop_price / start_close - 1.0
            return realized, _StopState(direction, prior.peak_close, prior.trough_close, prior.stop_price, False, prior.stop_price), True, False
        peak = max(prior.peak_close, close)
        trough = min(prior.trough_close, close)
        candidate = peak * np.exp(-long_k * volatility) if direction > 0 else trough * np.exp(short_k * volatility)
        line = max(prior.stop_price, candidate) if direction > 0 else min(prior.stop_price, candidate)
        prior = _StopState(direction, peak, trough, float(line))
    end_close = path[-1][1] if path else start_close
    return end_close / start_close - 1.0, prior, False, False


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


def _trade_origin(signal: SignalResult) -> tuple[float | None, str | None]:
    previous = signal.previous_position
    if previous is None and signal.position_value is not None:
        previous = 0.0  # account starts in cash; this is not a model fallback position
    return previous, classify_trade(previous, signal.position_value)


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
        strategy_name=first.strategy_name,
        stop_variant=first.stop_variant,
    )


def run_backtest(
    prices: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]],
    config: StrategyConfig | None = None,
    strategy: StrategyDefinition | None = None,
) -> BacktestResult:
    active = config or StrategyConfig()
    source = {frequency: tuple(rows) for frequency, rows in prices.items()} if isinstance(prices, Mapping) else tuple(prices)
    base = _run_backtest_without_stop(source, active, strategy)
    if strategy is not None and strategy.kind == "uploaded":
        return base
    return _add_stop_paths(base, source, active)


def _run_backtest_without_stop(
    prices: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]],
    config: StrategyConfig | None = None,
    strategy: StrategyDefinition | None = None,
) -> BacktestResult:
    active = config or StrategyConfig()
    if strategy is not None and strategy.kind == "uploaded":
        return _run_uploaded_backtest(prices, active, strategy)
    if strategy is not None and strategy.id != KELLY_STRATEGY_ID:
        raise ValueError(f"unsupported top-level strategy: {strategy.id}")
    periods: list[PeriodResult] = []
    signals: list[SignalResult] = []
    issues: list[str] = []
    states: dict[tuple[str, str, str, str, str], tuple[float | None, float]] = {}
    stop_states: dict[tuple[str, str, str, str, str], _StopState | None] = {}
    benchmark_states: dict[tuple[str, str, str], float] = {}
    source = {key: tuple(value) for key, value in prices.items()} if isinstance(prices, Mapping) else tuple(prices)
    daily_by_symbol = split_by_symbol(_frequency_rows(source, "daily"))
    anomaly_dates = suspected_adjustment_anomalies(
        (row for group in daily_by_symbol.values() for row in group)
    )

    for frequency in FREQUENCIES:
        rows = _frequency_rows(source, frequency)
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
                ewma_half_life = active.ewma_half_lives[frequency]
                ewma_weights = 2.0 ** (-np.arange(window - 1, -1, -1, dtype=float) / ewma_half_life)
                ewma_weights /= ewma_weights.sum()
                log_returns = np.log1p(np.asarray(sample, dtype=float))
                weighted_mean = float(np.dot(ewma_weights, log_returns))
                stop_volatility = float(np.sqrt(np.dot(ewma_weights, (log_returns - weighted_mean) ** 2)))
                # Solve each specified model with its own prespecified history weights.
                decisions = tuple(
                    solve_model(
                        model_id, sample,
                        lower=active.lower_bound,
                        upper=active.upper_bound,
                        wealth_floor=active.wealth_floor,
                        weights=ewma_weights if model_id.startswith("EWMA_") else None,
                    )
                    for model_id in MODEL_IDS
                )
                evaluated = signal_index < len(returns)
                window_start_date = symbol_prices[signal_index - window].date
                signal_date = symbol_prices[signal_index].date
                signal_anomaly = any(
                    anomaly_symbol == symbol and window_start_date <= anomaly_date <= signal_date
                    for anomaly_symbol, anomaly_date in anomaly_dates
                )
                for decision in decisions:
                    model_weights = ewma_weights if decision.model_id.startswith("EWMA_") else None
                    exact_model = "EWMA_EMPIRICAL_EXACT" if decision.model_id.startswith("EWMA_") else "EMPIRICAL_EXACT"
                    exact_decision = next(item for item in decisions if item.model_id == exact_model)
                    exact_positions = dict(exact_decision.positions())
                    intervals = json.dumps(decision.safe_domain_intervals, separators=(",", ":"))
                    for position_type, position in decision.positions():
                        key = (symbol, frequency, decision.model_id, position_type)
                        previous, wealth = states.get(key, (None, 1.0))
                        optimized_position = position
                        if position is None:
                            position = previous
                            position_status = (
                                "carried_forward_no_finite_optimum" if previous is not None
                                else "insufficient_history"
                            )
                        elif abs(position) <= 1e-12:
                            position_status = "optimized_zero"
                        else:
                            position_status = "optimized"
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
                            selected_exact_value = empirical_objective(position, sample, model_weights)
                            best_exact_value = empirical_objective(exact_position, sample, model_weights)
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
                            intervals, active.wealth_floor,
                            position_status, "evaluated" if evaluated else "pending",
                            optimized_position=optimized_position,
                            position_status=position_status,
                            contains_suspected_adjustment_anomaly=signal_anomaly,
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
                        if position is not None and abs(position) > 1e-12 and abs(next_return) > 1e-12:
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
                            position_status,
                            optimized_position=optimized_position,
                            position_status=position_status,
                            contains_suspected_adjustment_anomaly=(
                                signal_anomaly or any(
                                    anomaly_symbol == symbol and signal_date < anomaly_date <= symbol_prices[signal_index + 1].date
                                    for anomaly_symbol, anomaly_date in anomaly_dates
                                )
                            ),
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
                for row in grouped.get((key[0], key[1], "EWMA_EMPIRICAL_EXACT" if key[2].startswith("EWMA_") else "EMPIRICAL_EXACT", key[3]), ())
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
            _trade_origin(signal)[0], signal.position_value, signal.position_change,
            signal.turnover, period.next_return if period else None,
            period.wealth_multiplier if period else None,
            period.truncated_log_growth if period else None,
            period.cumulative_wealth if period else None,
            signal.evaluation_status, signal.position_type == "RAW",
        )
        for signal in signals
        if (action := _trade_origin(signal)[1]) is not None
        for period in [period_lookup.get((signal.symbol, signal.frequency, signal.model_id, signal.position_type, signal.signal_date))]
    )
    return BacktestResult(tuple(periods), tuple(signals), trades, summaries, tuple(issues))


def _add_stop_paths(
    base: BacktestResult,
    prices: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]],
    config: StrategyConfig,
) -> BacktestResult:
    """Add an isolated close-monitored stop path for every Kelly position path."""
    source = {name: tuple(rows) for name, rows in prices.items()} if isinstance(prices, Mapping) else tuple(prices)
    daily_by_symbol = split_by_symbol(_frequency_rows(source, "daily"))
    base_periods = {(row.symbol, row.frequency, row.model_id, row.position_type, row.signal_date): row for row in base.periods}
    grouped_signals: dict[tuple[str, str, str, str], list[SignalResult]] = {}
    for row in base.signals:
        grouped_signals.setdefault((row.symbol, row.frequency, row.model_id, row.position_type), []).append(row)
    stop_signals: list[SignalResult] = []
    stop_periods: list[PeriodResult] = []
    issues = list(base.issues)

    for (symbol, frequency, model_id, position_type), rows in sorted(grouped_signals.items()):
        rows.sort(key=lambda row: row.signal_date)
        frequency_rows = _frequency_rows(source, frequency)
        prices_by_symbol = split_by_symbol(frequency_rows).get(symbol, [])
        price_index = {row.date: index for index, row in enumerate(prices_by_symbol)}
        period_map = {row.signal_date: base_periods.get((symbol, frequency, model_id, position_type, row.signal_date)) for row in rows}
        previous: float | None = None
        wealth = 1.0
        stop_state: _StopState | None = None
        for signal in rows:
            optimized = signal.optimized_position
            position = optimized if optimized is not None else previous
            status = (
                "optimized_zero" if optimized is not None and abs(optimized) <= 1e-12
                else "optimized" if optimized is not None
                else "carried_forward_no_finite_optimum" if previous is not None
                else "insufficient_history"
            )
            base_period = period_map[signal.signal_date]
            if base_period is None:
                change = None if position is None or previous is None else position - previous
                stop_signals.append(replace(
                    signal, position_value=position, previous_position=previous,
                    position_change=change, turnover=abs(change) if change is not None else None,
                    status=status, position_status=status, stop_variant="WITH_STOP",
                ))
                continue

            stop_triggered = reentry_blocked = False
            realized_return = base_period.next_return
            next_stop = stop_state
            if position is not None:
                i = price_index.get(signal.signal_date)
                if i is None or i + 1 >= len(prices_by_symbol):
                    raise ValueError(f"missing signal/return prices for {symbol}/{frequency}/{signal.signal_date}")
                sample = [
                    prices_by_symbol[j].adjusted_close / prices_by_symbol[j - 1].adjusted_close - 1.0
                    for j in range(max(1, i - signal.window_size + 1), i + 1)
                ]
                half_life = config.ewma_half_lives[frequency]
                weights = 2.0 ** (-np.arange(len(sample) - 1, -1, -1, dtype=float) / half_life)
                weights /= weights.sum()
                y = np.log1p(np.asarray(sample, dtype=float))
                mean = float(np.dot(weights, y))
                volatility = float(np.sqrt(np.dot(weights, (y - mean) ** 2)))
                next_date = base_period.return_date
                path = [
                    (item.date, item.adjusted_close)
                    for item in daily_by_symbol.get(symbol, ())
                    if signal.signal_date < item.date <= next_date
                ]
                if frequency != "daily" and not daily_by_symbol.get(symbol):
                    path = [(next_date, prices_by_symbol[i + 1].adjusted_close)]
                    issue = f"{symbol}/{frequency}: WITH_STOP uses period closes; daily path unavailable"
                    if issue not in issues:
                        issues.append(issue)
                realized_return, next_stop, stop_triggered, reentry_blocked = _apply_close_stop(
                    prices_by_symbol[i].adjusted_close, path, position, stop_state,
                    volatility, config.long_stop_k, config.short_stop_k,
                )
                if optimized is not None and abs(optimized) <= 1e-12:
                    next_stop = None
                if reentry_blocked:
                    status = "reentry_blocked"
                elif stop_triggered:
                    status = "stopped_out"

            active_exposure = 0.0 if reentry_blocked else position
            change = None if active_exposure is None or previous is None else active_exposure - previous
            turnover = abs(change) if change is not None else None
            multiplier = None if active_exposure is None else 1 + active_exposure * realized_return
            feasible = None if multiplier is None else multiplier > 0 and isfinite(multiplier)
            bankrupt = multiplier is not None and not feasible
            truncated = None if multiplier is None else log(max(config.wealth_floor, multiplier))
            if multiplier is not None:
                wealth *= max(0.0, multiplier) if isfinite(multiplier) else 0.0
            direction = None
            if position is not None and abs(position) > 1e-12 and abs(base_period.next_return) > 1e-12:
                direction = position * base_period.next_return > 0
            stop_signals.append(replace(
                signal, position_value=active_exposure, previous_position=previous,
                position_change=change, turnover=turnover, status=status,
                position_status=status, stop_variant="WITH_STOP",
            ))
            stop_periods.append(replace(
                base_period, position_value=active_exposure, previous_position=previous,
                position_change=change, turnover=turnover, wealth_multiplier=multiplier,
                truncated_log_growth=truncated, direction_success=direction,
                wealth_feasible=feasible, bankrupt=bankrupt, cumulative_wealth=wealth,
                position_status=status, stop_variant="WITH_STOP",
                asset_return_realized=realized_return, stop_triggered=stop_triggered,
            ))
            stop_state = next_stop
            previous = 0.0 if stop_triggered or reentry_blocked else active_exposure

    all_signals = tuple(replace(row, stop_variant="WITHOUT_STOP") for row in base.signals) + tuple(stop_signals)
    all_periods = tuple(replace(row, stop_variant="WITHOUT_STOP", asset_return_realized=row.next_return) for row in base.periods) + tuple(stop_periods)
    grouped_periods: dict[tuple[str, str, str, str, str], list[PeriodResult]] = {}
    for row in all_periods:
        grouped_periods.setdefault((row.symbol, row.frequency, row.model_id, row.position_type, row.stop_variant), []).append(row)
    summaries: list[SummaryResult] = []
    for key, rows in sorted(grouped_periods.items()):
        exact_id = "EWMA_EMPIRICAL_EXACT" if key[2].startswith("EWMA_") else "EMPIRICAL_EXACT"
        exact_rows = {
            (row.signal_date, row.return_date): row
            for row in grouped_periods.get((key[0], key[1], exact_id, key[3], key[4]), ())
        }
        summaries.append(_summary(rows, config.windows[key[1]], config.minimum_matches[key[1]], exact_rows, config.lower_bound, config.upper_bound))

    period_lookup = {
        (row.symbol, row.frequency, row.model_id, row.position_type, row.stop_variant, row.signal_date): row
        for row in all_periods
    }
    trades = tuple(
        TradeResult(
            signal.symbol, signal.frequency, signal.model_id, signal.position_type,
            signal.signal_date, period.return_date if period else None, action,
            _trade_origin(signal)[0], signal.position_value, signal.position_change,
            signal.turnover, period.next_return if period else None,
            period.wealth_multiplier if period else None,
            period.truncated_log_growth if period else None,
            period.cumulative_wealth if period else None,
            signal.evaluation_status, signal.position_type == "RAW",
            stop_variant=signal.stop_variant,
        )
        for signal in all_signals
        if (action := _trade_origin(signal)[1]) is not None
        for period in [period_lookup.get((signal.symbol, signal.frequency, signal.model_id, signal.position_type, signal.stop_variant, signal.signal_date))]
    )
    return BacktestResult(all_periods, all_signals, trades, tuple(summaries), tuple(issues))


def _run_uploaded_backtest(
    prices: Iterable[PriceRow] | Mapping[str, Iterable[PriceRow]],
    config: StrategyConfig,
    strategy: StrategyDefinition,
) -> BacktestResult:
    """Run one trusted uploaded target-position strategy through the common audit chain."""

    if strategy.decide is None:
        raise ValueError(f"uploaded strategy {strategy.id} has no decide function")
    periods: list[PeriodResult] = []
    signals: list[SignalResult] = []
    issues: list[str] = []
    states: dict[tuple[str, str], tuple[float, float]] = {}
    benchmark_states: dict[tuple[str, str], float] = {}

    for frequency in FREQUENCIES:
        rows = _frequency_rows(prices, frequency)
        if not rows:
            issues.append(f"{frequency}: no provider-supplied price series")
            continue
        for symbol, symbol_prices in split_by_symbol(rows).items():
            returns = _returns(symbol_prices)
            window = config.windows[frequency]
            if len(returns) <= window:
                issues.append(
                    f"{symbol}/{frequency}: need at least {window + 2} prices; got {len(symbol_prices)}"
                )
                continue
            key = (symbol, frequency)
            for signal_index in range(window, len(returns) + 1):
                sample = returns[signal_index - window:signal_index]
                context = StrategyContext(
                    symbol=symbol,
                    frequency=frequency,
                    signal_date=symbol_prices[signal_index].date,
                    window_start_date=symbol_prices[signal_index - window].date,
                    window_end_date=symbol_prices[signal_index].date,
                    prices=tuple(
                        row.adjusted_close
                        for row in symbol_prices[signal_index - window:signal_index + 1]
                    ),
                    returns=tuple(sample),
                )
                decision = strategy.decide(context, config)
                moments = estimate_moments(sample)
                position = decision.position
                previous, wealth = states.get(key, (0.0, 1.0))
                change = position - previous
                turnover = abs(change)
                evaluated = signal_index < len(returns)
                diagnostics = dict(decision.diagnostics)
                common = dict(
                    symbol=symbol,
                    frequency=frequency,
                    signal_date=symbol_prices[signal_index].date,
                    window_start_date=symbol_prices[signal_index - window].date,
                    window_end_date=symbol_prices[signal_index].date,
                    window_size=window,
                    model_id=strategy.id,
                    position_type="TARGET",
                    position_value=position,
                    previous_position=previous,
                    position_change=change,
                    turnover=turnover,
                    m1=moments.m1,
                    m2=moments.m2,
                    m3=moments.m3,
                    m4=moments.m4,
                    mu=moments.mu,
                    nu2=moments.nu2,
                    nu3=moments.nu3,
                    nu4=moments.nu4,
                    q1=moments.q1,
                    q2=moments.q2,
                    q3=moments.q3,
                    q4=moments.q4,
                    raw_objective=None,
                    bounded_objective=decision.objective,
                    safe_objective=decision.objective,
                    position_objective=decision.objective,
                    exact_empirical_objective_loss=None,
                    solution_location=decision.optimizer_location,
                    raw_solution_type="USER_TARGET",
                    safe_domain_type="USER_BOUNDS",
                    status="ok",
                    strategy_name=strategy.name,
                    diagnostics=diagnostics,
                )
                signals.append(SignalResult(
                    **common,
                    safe_domain_intervals="[]",
                    wealth_floor=config.wealth_floor,
                    evaluation_status="evaluated" if evaluated else "pending",
                ))
                if not evaluated:
                    continue
                next_return = returns[signal_index]
                multiplier = 1 + position * next_return
                feasible = multiplier > 0 and isfinite(multiplier)
                bankrupt = not feasible
                truncated = log(max(config.wealth_floor, multiplier))
                wealth *= max(0.0, multiplier) if isfinite(multiplier) else 0.0
                benchmark = benchmark_states.get(key, 1.0) * max(0.0, 1 + next_return)
                benchmark_states[key] = benchmark
                direction = None if abs(position) <= 1e-12 or abs(next_return) <= 1e-12 else position * next_return > 0
                periods.append(PeriodResult(
                    **common,
                    return_date=symbol_prices[signal_index + 1].date,
                    next_return=next_return,
                    wealth_multiplier=multiplier,
                    truncated_log_growth=truncated,
                    direction_success=direction,
                    wealth_feasible=feasible,
                    bankrupt=bankrupt,
                    cumulative_wealth=wealth,
                    buy_hold_wealth=benchmark,
                ))
                states[key] = (position, wealth)

    grouped: dict[tuple[str, str], list[PeriodResult]] = {}
    for row in periods:
        grouped.setdefault((row.symbol, row.frequency), []).append(row)
    summaries = tuple(
        _summary(
            rows,
            config.windows[frequency],
            config.minimum_matches[frequency],
            {},
            config.lower_bound,
            config.upper_bound,
        )
        for (_, frequency), rows in sorted(grouped.items())
    )
    period_lookup = {
        (row.symbol, row.frequency, row.signal_date): row for row in periods
    }
    trades = tuple(
        TradeResult(
            signal.symbol,
            signal.frequency,
            signal.model_id,
            signal.position_type,
            signal.signal_date,
            period.return_date if period else None,
            action,
            signal.previous_position,
            signal.position_value,
            signal.position_change,
            signal.turnover,
            period.next_return if period else None,
            period.wealth_multiplier if period else None,
            period.truncated_log_growth if period else None,
            period.cumulative_wealth if period else None,
            signal.evaluation_status,
            False,
            strategy.name,
        )
        for signal in signals
        if (action := classify_trade(signal.previous_position, signal.position_value)) is not None
        for period in [period_lookup.get((signal.symbol, signal.frequency, signal.signal_date))]
    )
    return BacktestResult(tuple(periods), tuple(signals), trades, summaries, tuple(issues))
