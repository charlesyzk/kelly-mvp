"""No-lookahead rolling backtest and focused performance metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import expm1, isfinite, log
from statistics import fmean, pstdev
from typing import Iterable

from .config import FREQUENCIES, StrategyConfig
from .data import PriceRow, aggregate_prices, split_by_symbol
from .model import choose_position


PERIODS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12}


@dataclass(frozen=True, slots=True)
class PeriodResult:
    symbol: str
    frequency: str
    signal_date: date
    return_date: date
    window_start_date: date
    window_end_date: date
    raw_kelly: float
    full_kelly: float
    exact_full_kelly: float
    exact_kelly_gap: float
    exact_objective_loss: float | None
    optimizer_location: str
    position: float
    previous_position: float
    position_change: float
    q1: float
    q2: float
    q3: float
    q4: float
    next_return: float
    direction_correct: bool | None
    turnover: float
    cost_rate: float
    gross_return: float
    net_return: float
    log_growth: float | None
    wealth: float
    buy_hold_wealth: float
    bankrupt: bool
    segment: str


@dataclass(frozen=True, slots=True)
class SignalResult:
    symbol: str
    frequency: str
    signal_date: date
    window_start_date: date
    window_end_date: date
    raw_kelly: float
    full_kelly: float
    exact_full_kelly: float
    exact_kelly_gap: float
    exact_objective_loss: float | None
    optimizer_location: str
    position: float
    previous_position: float
    position_change: float
    turnover: float
    evaluation_status: str
    segment: str


@dataclass(frozen=True, slots=True)
class TradeResult:
    symbol: str
    frequency: str
    signal_date: date
    return_date: date | None
    action: str
    previous_position: float
    target_position: float
    position_change: float
    turnover: float
    cost_rate: float
    next_return: float | None
    net_return: float | None
    wealth_after: float | None
    evaluation_status: str
    segment: str


@dataclass(frozen=True, slots=True)
class SummaryResult:
    symbol: str
    frequency: str
    segment: str
    window: int
    observations: int
    active_observations: int
    direction_observations: int
    coverage: float
    direction_accuracy: float | None
    total_return: float
    annualized_return: float
    average_log_growth: float | None
    annualized_log_growth: float | None
    buy_hold_return: float
    excess_return: float
    max_drawdown: float
    annualized_volatility: float
    sharpe_zero_rf: float | None
    average_abs_position: float
    average_turnover: float
    mean_abs_exact_kelly_gap: float
    exact_direction_agreement: float | None
    boundary_rate: float
    bankruptcies: int
    transaction_cost_bps: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    periods: tuple[PeriodResult, ...]
    signals: tuple[SignalResult, ...]
    trades: tuple[TradeResult, ...]
    summaries: tuple[SummaryResult, ...]
    issues: tuple[str, ...]

    def period_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.periods]

    def summary_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.summaries]

    def trade_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.trades]

    def signal_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.signals]


def classify_trade(previous: float, target: float, tolerance: float = 1e-12) -> str | None:
    """Describe a target-position change without inventing execution details."""

    if abs(target - previous) <= tolerance:
        return None
    previous_is_zero = abs(previous) <= tolerance
    target_is_zero = abs(target) <= tolerance
    if previous_is_zero:
        return "open_long" if target > 0 else "open_short"
    if target_is_zero:
        return "close_long" if previous > 0 else "close_short"
    if previous > 0 and target < 0:
        return "reverse_to_short"
    if previous < 0 and target > 0:
        return "reverse_to_long"
    if previous > 0:
        return "add_long" if target > previous else "reduce_long"
    return "add_short" if target < previous else "cover_short"


def _returns(prices: list[PriceRow]) -> list[float]:
    return [prices[i].adjusted_close / prices[i - 1].adjusted_close - 1 for i in range(1, len(prices))]


def _drawdown(period_returns: Iterable[float]) -> float:
    wealth = peak = 1.0
    worst = 0.0
    for value in period_returns:
        wealth *= max(0.0, 1 + value)
        peak = max(peak, wealth)
        if peak:
            worst = min(worst, wealth / peak - 1)
    return worst


def _compound(period_returns: Iterable[float]) -> float:
    wealth = 1.0
    for value in period_returns:
        wealth *= max(0.0, 1 + value)
    return wealth - 1


def _summary(rows: list[PeriodResult], segment: str, window: int, cost_bps: float) -> SummaryResult:
    chosen = rows if segment == "all" else [row for row in rows if row.segment == segment]
    net = [row.net_return for row in chosen]
    benchmark = [row.next_return for row in chosen]
    active = [row for row in chosen if abs(row.position) > 1e-12]
    directional = [row for row in active if row.direction_correct is not None]
    correct = sum(row.direction_correct is True for row in directional)
    n = len(chosen)
    total = _compound(net)
    annualizer = PERIODS_PER_YEAR[chosen[0].frequency]
    annualized = (1 + total) ** (annualizer / n) - 1 if n and total > -1 else -1.0
    log_growth_values = [row.log_growth for row in chosen if row.log_growth is not None]
    average_log_growth = (
        fmean(log_growth_values) if len(log_growth_values) == n else None
    )
    try:
        annualized_log_growth = (
            expm1(average_log_growth * annualizer)
            if average_log_growth is not None else -1.0
        )
    except OverflowError:
        annualized_log_growth = None
    volatility = pstdev(net) * annualizer**0.5 if n > 1 else 0.0
    mean = fmean(net) * annualizer if n else 0.0
    diagnostic = [
        row for row in chosen
        if abs(row.full_kelly) > 1e-12 and abs(row.exact_full_kelly) > 1e-12
    ]
    return SummaryResult(
        symbol=chosen[0].symbol,
        frequency=chosen[0].frequency,
        segment=segment,
        window=window,
        observations=n,
        active_observations=len(active),
        direction_observations=len(directional),
        coverage=len(active) / n if n else 0.0,
        direction_accuracy=correct / len(directional) if directional else None,
        total_return=total,
        annualized_return=annualized,
        average_log_growth=average_log_growth,
        annualized_log_growth=annualized_log_growth,
        buy_hold_return=_compound(benchmark),
        excess_return=total - _compound(benchmark),
        max_drawdown=_drawdown(net),
        annualized_volatility=volatility,
        sharpe_zero_rf=mean / volatility if volatility > 0 else None,
        average_abs_position=fmean(abs(row.position) for row in chosen),
        average_turnover=fmean(row.turnover for row in chosen),
        mean_abs_exact_kelly_gap=fmean(abs(row.exact_kelly_gap) for row in chosen),
        exact_direction_agreement=(
            sum(row.full_kelly * row.exact_full_kelly > 0 for row in diagnostic) / len(diagnostic)
            if diagnostic else None
        ),
        boundary_rate=sum(row.optimizer_location != "interior" for row in chosen) / n,
        bankruptcies=sum(row.bankrupt for row in chosen),
        transaction_cost_bps=cost_bps,
    )


def run_backtest(daily_prices: Iterable[PriceRow], config: StrategyConfig | None = None) -> BacktestResult:
    active = config or StrategyConfig()
    periods: list[PeriodResult] = []
    signals: list[SignalResult] = []
    issues: list[str] = []
    for symbol, symbol_prices in split_by_symbol(daily_prices).items():
        for frequency in FREQUENCIES:
            prices = aggregate_prices(symbol_prices, frequency)
            returns = _returns(prices)
            window = active.windows[frequency]
            available = len(returns) - window
            if available <= 0:
                issues.append(
                    f"{symbol}/{frequency}: need at least {window + 2} usable prices; got {len(prices)}"
                )
                continue
            split_index = max(1, min(available - 1, round(available * active.development_fraction)))
            wealth = benchmark_wealth = 1.0
            previous_position = 0.0
            for evaluation_index, signal_index in enumerate(range(window, len(returns) + 1)):
                sample = returns[signal_index - window:signal_index]
                decision = choose_position(
                    sample,
                    lower=active.lower_bound,
                    upper=active.upper_bound,
                    fraction=active.kelly_fraction,
                )
                turnover = abs(decision.position - previous_position)
                position_change = decision.position - previous_position
                evaluated = signal_index < len(returns)
                segment = (
                    "pending" if not evaluated
                    else "development" if evaluation_index < split_index
                    else "holdout"
                )
                signals.append(SignalResult(
                    symbol=symbol,
                    frequency=frequency,
                    signal_date=prices[signal_index].date,
                    window_start_date=prices[signal_index - window].date,
                    window_end_date=prices[signal_index].date,
                    raw_kelly=decision.raw_kelly,
                    full_kelly=decision.full_kelly,
                    exact_full_kelly=decision.exact_full_kelly,
                    exact_kelly_gap=decision.full_kelly - decision.exact_full_kelly,
                    exact_objective_loss=decision.exact_objective_loss,
                    optimizer_location=decision.optimizer_location,
                    position=decision.position,
                    previous_position=previous_position,
                    position_change=position_change,
                    turnover=turnover,
                    evaluation_status="evaluated" if evaluated else "pending",
                    segment=segment,
                ))
                if not evaluated:
                    previous_position = decision.position
                    continue
                next_return = returns[signal_index]
                cost_rate = turnover * active.transaction_cost_bps / 10_000
                gross_multiplier = 1 + decision.position * next_return
                net_multiplier = gross_multiplier * (1 - cost_rate)
                bankrupt = net_multiplier <= 0 or not isfinite(net_multiplier)
                net_multiplier = max(0.0, net_multiplier) if isfinite(net_multiplier) else 0.0
                gross_return = gross_multiplier - 1
                net_return = net_multiplier - 1
                log_growth = log(net_multiplier) if net_multiplier > 0 else None
                wealth *= net_multiplier
                benchmark_wealth *= max(0.0, 1 + next_return)
                direction = (
                    None
                    if abs(decision.position) <= 1e-12 or abs(next_return) <= 1e-15
                    else decision.position * next_return > 0
                )
                moments = decision.moments
                periods.append(PeriodResult(
                    symbol=symbol,
                    frequency=frequency,
                    signal_date=prices[signal_index].date,
                    return_date=prices[signal_index + 1].date,
                    window_start_date=prices[signal_index - window].date,
                    window_end_date=prices[signal_index].date,
                    raw_kelly=decision.raw_kelly,
                    full_kelly=decision.full_kelly,
                    exact_full_kelly=decision.exact_full_kelly,
                    exact_kelly_gap=decision.full_kelly - decision.exact_full_kelly,
                    exact_objective_loss=decision.exact_objective_loss,
                    optimizer_location=decision.optimizer_location,
                    position=decision.position,
                    previous_position=previous_position,
                    position_change=position_change,
                    q1=moments.q1,
                    q2=moments.q2,
                    q3=moments.q3,
                    q4=moments.q4,
                    next_return=next_return,
                    direction_correct=direction,
                    turnover=turnover,
                    cost_rate=cost_rate,
                    gross_return=gross_return,
                    net_return=net_return,
                    log_growth=log_growth,
                    wealth=wealth,
                    buy_hold_wealth=benchmark_wealth,
                    bankrupt=bankrupt,
                    segment=segment,
                ))
                previous_position = decision.position
    summaries: list[SummaryResult] = []
    period_lookup = {
        (row.symbol, row.frequency, row.signal_date): row for row in periods
    }
    trades = tuple(
        TradeResult(
            symbol=signal.symbol,
            frequency=signal.frequency,
            signal_date=signal.signal_date,
            return_date=period.return_date if period else None,
            action=action,
            previous_position=signal.previous_position,
            target_position=signal.position,
            position_change=signal.position_change,
            turnover=signal.turnover,
            cost_rate=signal.turnover * active.transaction_cost_bps / 10_000,
            next_return=period.next_return if period else None,
            net_return=period.net_return if period else None,
            wealth_after=period.wealth if period else None,
            evaluation_status=signal.evaluation_status,
            segment=signal.segment,
        )
        for signal in signals
        if (action := classify_trade(signal.previous_position, signal.position)) is not None
        for period in [period_lookup.get((signal.symbol, signal.frequency, signal.signal_date))]
    )
    grouped: dict[tuple[str, str], list[PeriodResult]] = {}
    for row in periods:
        grouped.setdefault((row.symbol, row.frequency), []).append(row)
    for (_, frequency), rows in grouped.items():
        for segment in ("development", "holdout", "all"):
            selected = rows if segment == "all" else [row for row in rows if row.segment == segment]
            if selected:
                summaries.append(_summary(rows, segment, active.windows[frequency], active.transaction_cost_bps))
    return BacktestResult(tuple(periods), tuple(signals), trades, tuple(summaries), tuple(issues))
