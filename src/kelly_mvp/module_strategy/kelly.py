"""The complete six-model higher-moment Kelly family."""

from __future__ import annotations

from math import exp, isfinite, log
from statistics import fmean
from typing import Callable

from ..config import StrategyConfig
from ..model import (
    _real_polynomial_roots,
    choose_exact_position,
    choose_position,
    empirical_objective,
)
from .contract import StrategyContext, StrategyDecision, StrategyDefinition


BUILTIN_STRATEGY_IDS = (
    "M2_LOG",
    "M3_LOG",
    "M4_LOG_ZERO",
    "M4_SIMPLE",
    "M4_LOG_MEAN",
    "EMPIRICAL_EXACT",
)


def _moments(values: tuple[float, ...], order: int = 4) -> tuple[float, ...]:
    return tuple(fmean(value**power for value in values) for power in range(1, order + 1))


def _poly_value(position: float, coefficients: tuple[float, ...]) -> float:
    return sum(coefficient * position**power for power, coefficient in enumerate(coefficients))


def _polynomial_optimum(
    coefficients: tuple[float, float, float, float, float],
    lower: float,
    upper: float,
) -> tuple[float | None, float]:
    derivative_coefficients = (
        coefficients[1],
        2 * coefficients[2],
        3 * coefficients[3],
        4 * coefficients[4],
    )
    roots = _real_polynomial_roots(derivative_coefficients)
    candidates = [lower, upper]
    if lower <= 0 <= upper:
        candidates.append(0.0)
    candidates.extend(root for root in roots if lower <= root <= upper)
    best_value = max(_poly_value(candidate, coefficients) for candidate in candidates)
    tied = [
        candidate for candidate in candidates
        if best_value - _poly_value(candidate, coefficients) <= 1e-12
    ]
    bounded = min(tied, key=lambda value: (abs(value), value))

    local_maxima = []
    for root in roots:
        second = 2 * coefficients[2] + 6 * coefficients[3] * root + 12 * coefficients[4] * root**2
        if second < -1e-12:
            local_maxima.append(root)
    raw = (
        max(local_maxima, key=lambda value: _poly_value(value, coefficients))
        if local_maxima else None
    )
    return raw, bounded


def _bounded_numeric_optimum(
    objective: Callable[[float], float], lower: float, upper: float
) -> float:
    steps = 1000
    width = (upper - lower) / steps
    candidates = [(lower + index * width, objective(lower + index * width)) for index in range(steps + 1)]
    valid = [(position, value) for position, value in candidates if isfinite(value)]
    if not valid:
        raise ValueError("strategy objective has no finite value inside the position bounds")
    best_position, _ = max(valid, key=lambda item: (item[1], -abs(item[0])))
    index = round((best_position - lower) / width)
    lo = max(lower, lower + (index - 1) * width)
    hi = min(upper, lower + (index + 1) * width)
    ratio = (5**0.5 - 1) / 2
    left = hi - ratio * (hi - lo)
    right = lo + ratio * (hi - lo)
    for _ in range(80):
        left_value = objective(left)
        right_value = objective(right)
        if not isfinite(left_value) or left_value < right_value:
            lo = left
            left = right
            right = lo + ratio * (hi - lo)
        else:
            hi = right
            right = left
            left = hi - ratio * (hi - lo)
    refined = (lo + hi) / 2
    options = valid + [(refined, objective(refined))]
    return max(options, key=lambda item: (item[1], -abs(item[0])))[0]


def _location(position: float, lower: float, upper: float) -> str:
    if abs(position - lower) <= 1e-9:
        return "lower_bound"
    if abs(position - upper) <= 1e-9:
        return "upper_bound"
    return "interior"


def _finish_kelly(
    context: StrategyContext,
    config: StrategyConfig,
    strategy_id: str,
    raw: float | None,
    bounded: float,
    objective: Callable[[float], float],
    moments: dict[str, float],
) -> StrategyDecision:
    exact = choose_exact_position(context.returns, config.lower_bound, config.upper_bound)
    empirical_at_bounded = empirical_objective(bounded, context.returns)
    exact_loss = (
        max(0.0, empirical_objective(exact, context.returns) - empirical_at_bounded)
        if isfinite(empirical_at_bounded) else None
    )
    position = max(
        config.lower_bound,
        min(config.upper_bound, config.kelly_fraction * bounded),
    )
    return StrategyDecision(
        raw_position=raw,
        bounded_position=bounded,
        position=position,
        optimizer_location=_location(bounded, config.lower_bound, config.upper_bound),
        objective=objective(position),
        exact_full_kelly=exact,
        exact_objective_loss=exact_loss,
        moments=moments,
        diagnostics={"kelly_model": strategy_id, "cash_return_assumption": "zero"},
    )


def _log_polynomial_decision(
    context: StrategyContext, config: StrategyConfig, strategy_id: str
) -> StrategyDecision:
    log_returns = tuple(log(1 + value) for value in context.returns)
    m1, m2, m3, m4 = _moments(log_returns)
    if strategy_id == "M2_LOG":
        coefficients = (0.0, m1 + m2 / 2, -m2 / 2, 0.0, 0.0)
    elif strategy_id == "M3_LOG":
        coefficients = (
            0.0,
            m1 + m2 / 2 + m3 / 6,
            -m2 / 2 - m3 / 2,
            m3 / 3,
            0.0,
        )
    else:
        coefficients = (
            0.0,
            m1 + m2 / 2 + m3 / 6 + m4 / 24,
            -m2 / 2 - m3 / 2 - 7 * m4 / 24,
            m3 / 3 + m4 / 2,
            -m4 / 4,
        )
    raw, bounded = _polynomial_optimum(
        coefficients, config.lower_bound, config.upper_bound
    )
    objective = lambda position: _poly_value(position, coefficients)
    moments = {"m1": m1, "m2": m2, "m3": m3, "m4": m4}
    return _finish_kelly(context, config, strategy_id, raw, bounded, objective, moments)


def _m4_simple(context: StrategyContext, config: StrategyConfig) -> StrategyDecision:
    decision = choose_position(
        context.returns,
        lower=config.lower_bound,
        upper=config.upper_bound,
        fraction=config.kelly_fraction,
    )
    moments = {
        "q1": decision.moments.q1,
        "q2": decision.moments.q2,
        "q3": decision.moments.q3,
        "q4": decision.moments.q4,
    }
    return StrategyDecision(
        raw_position=decision.raw_kelly,
        bounded_position=decision.full_kelly,
        position=decision.position,
        optimizer_location=decision.optimizer_location,
        objective=decision.objective,
        exact_full_kelly=decision.exact_full_kelly,
        exact_objective_loss=decision.exact_objective_loss,
        moments=moments,
        diagnostics={"kelly_model": "M4_SIMPLE", "cash_return_assumption": "zero"},
    )


def _m4_log_mean(context: StrategyContext, config: StrategyConfig) -> StrategyDecision:
    log_returns = tuple(log(1 + value) for value in context.returns)
    mu = fmean(log_returns)
    centered = tuple(value - mu for value in log_returns)
    nu2 = fmean(value**2 for value in centered)
    nu3 = fmean(value**3 for value in centered)
    nu4 = fmean(value**4 for value in centered)

    def objective(position: float) -> float:
        denominator = 1 + position * (exp(mu) - 1)
        if denominator <= 0:
            return float("-inf")
        probability = position * exp(mu) / denominator
        return (
            log(denominator)
            + probability * (1 - probability) * nu2 / 2
            + probability * (1 - probability) * (1 - 2 * probability) * nu3 / 6
            + probability
            * (1 - probability)
            * (1 - 6 * probability + 6 * probability**2)
            * nu4
            / 24
        )

    bounded = _bounded_numeric_optimum(objective, config.lower_bound, config.upper_bound)
    moments = {"mu": mu, "nu2": nu2, "nu3": nu3, "nu4": nu4}
    return _finish_kelly(
        context, config, "M4_LOG_MEAN", None, bounded, objective, moments
    )


def _empirical_exact(context: StrategyContext, config: StrategyConfig) -> StrategyDecision:
    exact = choose_exact_position(context.returns, config.lower_bound, config.upper_bound)
    position = max(
        config.lower_bound,
        min(config.upper_bound, config.kelly_fraction * exact),
    )
    q1, q2, q3, q4 = _moments(context.returns)
    return StrategyDecision(
        raw_position=None,
        bounded_position=exact,
        position=position,
        optimizer_location=_location(exact, config.lower_bound, config.upper_bound),
        objective=empirical_objective(position, context.returns),
        exact_full_kelly=exact,
        exact_objective_loss=0.0,
        moments={"q1": q1, "q2": q2, "q3": q3, "q4": q4},
        diagnostics={"kelly_model": "EMPIRICAL_EXACT", "cash_return_assumption": "zero"},
    )


def _log_decider(strategy_id: str):
    return lambda context, config: _log_polynomial_decision(
        context, config, strategy_id
    )


_DESCRIPTIONS = {
    "M2_LOG": "对数收益二阶 Taylor 基准：均值与二阶风险。",
    "M3_LOG": "在二阶基准上加入对数收益三阶矩。",
    "M4_LOG_ZERO": "围绕零点展开的四阶对数收益模型。",
    "M4_SIMPLE": "当前策略：直接对简单收益的对数财富目标作四阶展开。",
    "M4_LOG_MEAN": "围绕样本均值展开的四阶对数收益模型。",
    "EMPIRICAL_EXACT": "直接最大化窗口经验样本的平均精确对数增长。",
}


BUILTIN_STRATEGIES = {
    strategy_id: StrategyDefinition(
        id=strategy_id,
        name=strategy_id,
        version="1.0",
        description=_DESCRIPTIONS[strategy_id],
        kind="builtin_kelly",
        uses_kelly_fraction=True,
        decide=(
            _m4_simple if strategy_id == "M4_SIMPLE"
            else _m4_log_mean if strategy_id == "M4_LOG_MEAN"
            else _empirical_exact if strategy_id == "EMPIRICAL_EXACT"
            else _log_decider(strategy_id)
        ),
    )
    for strategy_id in BUILTIN_STRATEGY_IDS
}
