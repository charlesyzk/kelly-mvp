"""No-lookahead rolling backtest and focused performance metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
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
    full_kelly: float
    position: float
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
    wealth: float
    buy_hold_wealth: float
    bankrupt: bool
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
    buy_hold_return: float
    excess_return: float
    max_drawdown: float
    annualized_volatility: float
    sharpe_zero_rf: float | None
    average_abs_position: float
    average_turnover: float
    bankruptcies: int
    transaction_cost_bps: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    periods: tuple[PeriodResult, ...]
    summaries: tuple[SummaryResult, ...]
    issues: tuple[str, ...]

    def period_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.periods]

    def summary_dicts(self) -> list[dict[str, object]]:
        return [asdict(row) for row in self.summaries]


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
    volatility = pstdev(net) * annualizer**0.5 if n > 1 else 0.0
    mean = fmean(net) * annualizer if n else 0.0
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
        buy_hold_return=_compound(benchmark),
        excess_return=total - _compound(benchmark),
        max_drawdown=_drawdown(net),
        annualized_volatility=volatility,
        sharpe_zero_rf=mean / volatility if volatility > 0 else None,
        average_abs_position=fmean(abs(row.position) for row in chosen),
        average_turnover=fmean(row.turnover for row in chosen),
        bankruptcies=sum(row.bankrupt for row in chosen),
        transaction_cost_bps=cost_bps,
    )


def run_backtest(daily_prices: Iterable[PriceRow], config: StrategyConfig | None = None) -> BacktestResult:
    active = config or StrategyConfig()
    periods: list[PeriodResult] = []
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
            for evaluation_index, return_index in enumerate(range(window, len(returns))):
                sample = returns[return_index - window:return_index]
                decision = choose_position(
                    sample,
                    lower=active.lower_bound,
                    upper=active.upper_bound,
                    fraction=active.kelly_fraction,
                )
                next_return = returns[return_index]
                turnover = abs(decision.position - previous_position)
                cost_rate = turnover * active.transaction_cost_bps / 10_000
                gross_multiplier = 1 + decision.position * next_return
                net_multiplier = gross_multiplier * (1 - cost_rate)
                bankrupt = net_multiplier <= 0 or not isfinite(net_multiplier)
                net_multiplier = max(0.0, net_multiplier) if isfinite(net_multiplier) else 0.0
                gross_return = gross_multiplier - 1
                net_return = net_multiplier - 1
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
                    signal_date=prices[return_index].date,
                    return_date=prices[return_index + 1].date,
                    window_start_date=prices[return_index - window].date,
                    window_end_date=prices[return_index].date,
                    full_kelly=decision.full_kelly,
                    position=decision.position,
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
                    wealth=wealth,
                    buy_hold_wealth=benchmark_wealth,
                    bankrupt=bankrupt,
                    segment="development" if evaluation_index < split_index else "holdout",
                ))
                previous_position = decision.position
    summaries: list[SummaryResult] = []
    grouped: dict[tuple[str, str], list[PeriodResult]] = {}
    for row in periods:
        grouped.setdefault((row.symbol, row.frequency), []).append(row)
    for (_, frequency), rows in grouped.items():
        for segment in ("development", "holdout", "all"):
            selected = rows if segment == "all" else [row for row in rows if row.segment == segment]
            if selected:
                summaries.append(_summary(rows, segment, active.windows[frequency], active.transaction_cost_bps))
    return BacktestResult(tuple(periods), tuple(summaries), tuple(issues))
