"""Six Kelly objectives with independent RAW, BOUNDED and SAFE solutions."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, isfinite, log, log1p, pi, sqrt
from typing import Callable, Iterable

import numpy as np

from .config import MODEL_IDS


TOL = 1e-10


def _bisect_root(function: Callable[[float], float], lower: float, upper: float) -> float:
    """Find a bracketed scalar root without making SciPy a runtime requirement."""

    left, right = float(lower), float(upper)
    f_left, f_right = function(left), function(right)
    if not isfinite(f_left) or not isfinite(f_right) or f_left * f_right > 0:
        raise ValueError("root is not bracketed by finite endpoint values")
    if abs(f_left) <= TOL:
        return left
    if abs(f_right) <= TOL:
        return right
    for _ in range(120):
        middle = (left + right) / 2
        f_middle = function(middle)
        if not isfinite(f_middle):
            raise ValueError("non-finite value encountered while solving root")
        if abs(f_middle) <= TOL or right - left <= TOL * max(1.0, abs(middle)):
            return middle
        if f_left * f_middle <= 0:
            right, f_right = middle, f_middle
        else:
            left, f_left = middle, f_middle
    return (left + right) / 2


@dataclass(frozen=True, slots=True)
class MomentEstimate:
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


@dataclass(frozen=True, slots=True)
class ModelDecision:
    model_id: str
    raw_position: float | None
    bounded_position: float
    safe_position: float
    raw_solution_type: str
    safe_domain_type: str
    safe_domain_intervals: tuple[tuple[float, float], ...]
    raw_objective: float | None
    bounded_objective: float
    safe_objective: float
    status: str
    moments: MomentEstimate

    def positions(self) -> tuple[tuple[str, float | None], ...]:
        return (
            ("RAW", self.raw_position),
            ("BOUNDED", self.bounded_position),
            ("SAFE", self.safe_position),
        )


def estimate_moments(simple_returns: Iterable[float]) -> MomentEstimate:
    z = np.asarray(tuple(float(v) for v in simple_returns), dtype=float)
    if z.size == 0 or not np.isfinite(z).all() or np.any(z <= -1):
        raise ValueError("returns must be finite, nonempty and greater than -1")
    y = np.log1p(z)
    mu = float(y.mean())
    centered = y - mu
    raw_y = [float(np.mean(y**k)) for k in range(1, 5)]
    central = [float(np.mean(centered**k)) for k in range(2, 5)]
    raw_z = [float(np.mean(z**k)) for k in range(1, 5)]
    return MomentEstimate(*raw_y, mu, *central, *raw_z)


def _real_roots(coefficients_descending: Iterable[float]) -> tuple[float, ...]:
    coefficients = np.asarray(tuple(coefficients_descending), dtype=float)
    if not np.isfinite(coefficients).all():
        return ()
    scale = float(np.max(np.abs(coefficients))) if coefficients.size else 0.0
    if scale == 0:
        return ()
    first = 0
    while first < len(coefficients) - 1 and abs(coefficients[first]) <= scale * 1e-12:
        first += 1
    roots = np.roots(coefficients[first:])
    result = sorted(float(root.real) for root in roots if abs(root.imag) <= 1e-8 * max(1.0, abs(root.real)))
    unique: list[float] = []
    for root in result:
        if not unique or abs(root - unique[-1]) > 1e-8 * max(1.0, abs(root)):
            unique.append(root)
    return tuple(unique)


def _choose(candidates: Iterable[float], objective: Callable[[float], float]) -> tuple[float, float]:
    evaluated = [(float(f), objective(float(f))) for f in candidates]
    valid = [(f, value) for f, value in evaluated if isfinite(value)]
    if not valid:
        raise ValueError("no finite feasible candidate")
    best = max(value for _, value in valid)
    tied = [f for f, value in valid if best - value <= 1e-10 * max(1.0, abs(best))]
    selected = min(tied, key=lambda f: (abs(f), f))
    return selected, objective(selected)


def _wealth_interval(sample: np.ndarray, lower: float, upper: float, floor: float) -> tuple[float, float]:
    lo, hi = lower, upper
    for value in sample:
        if value > 0:
            lo = max(lo, (floor - 1.0) / float(value))
        elif value < 0:
            hi = min(hi, (floor - 1.0) / float(value))
    if lo > hi + TOL:
        raise ValueError("wealth-safe domain is empty")
    return lo, hi


def _intersection(intervals: Iterable[tuple[float, float]], lo: float, hi: float) -> tuple[tuple[float, float], ...]:
    result = []
    for left, right in intervals:
        a, b = max(left, lo), min(right, hi)
        if a <= b + TOL:
            result.append((a, b))
    return tuple(result)


def _predicate_intervals(predicate: Callable[[float], bool], lower: float, upper: float) -> tuple[tuple[float, float], ...]:
    """Resolve connected feasible intervals deterministically on a bounded domain."""

    grid = np.linspace(lower, upper, 16385)
    flags = [bool(predicate(float(value))) for value in grid]
    intervals: list[tuple[float, float]] = []
    start: float | None = lower if flags[0] else None

    def boundary(a: float, b: float, state_at_b: bool) -> float:
        for _ in range(70):
            mid = (a + b) / 2
            if bool(predicate(mid)) == state_at_b:
                b = mid
            else:
                a = mid
        return b if state_at_b else a

    for index in range(1, len(grid)):
        if flags[index] == flags[index - 1]:
            continue
        edge = boundary(float(grid[index - 1]), float(grid[index]), flags[index])
        if flags[index]:
            start = edge
        elif start is not None:
            intervals.append((start, edge))
            start = None
    if start is not None:
        intervals.append((start, upper))
    for special in (0.0, 1.0):
        if lower <= special <= upper and predicate(special) and not any(a - TOL <= special <= b + TOL for a, b in intervals):
            intervals.append((special, special))
    return tuple(sorted(intervals))


def _log_radius(position: float, center: float) -> float:
    if abs(position) <= TOL or abs(position - 1.0) <= TOL:
        return inf
    if 0 < position < 1:
        return sqrt((log((1 - position) / position) - center) ** 2 + pi**2)
    return abs(log(1 - 1 / position) - center)


def _log_safe_intervals(
    lower: float,
    upper: float,
    center: float,
    dmax: float,
    kappa: float,
    predicate: Callable[[float], bool],
) -> tuple[tuple[float, float], ...]:
    """Partition the bounded line at every analytic convergence boundary."""

    required = dmax / kappa
    points = {lower, upper}
    for special in (0.0, 1.0):
        if lower <= special <= upper:
            points.add(special)
    for s in (center - required, center + required):
        if abs(s) > 1e-14:
            f = 1.0 / (1.0 - exp(s))
            if lower < f < upper and (f < 0 or f > 1):
                points.add(f)
    if required > pi:
        offset = sqrt(required * required - pi * pi)
        for s in (center - offset, center + offset):
            f = 1.0 / (1.0 + exp(s))
            if lower < f < upper:
                points.add(f)
    ordered = sorted(points)
    pieces: list[tuple[float, float]] = []
    for left, right in zip(ordered, ordered[1:]):
        mid = (left + right) / 2
        if predicate(mid):
            pieces.append((left, right))
    for point in ordered:
        if predicate(point) and not any(a - TOL <= point <= b + TOL for a, b in pieces):
            pieces.append((point, point))
    merged: list[tuple[float, float]] = []
    for left, right in sorted(pieces):
        if merged and left <= merged[-1][1] + TOL:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return tuple(merged)


def _objective_and_roots(model_id: str, m: MomentEstimate, sample: np.ndarray):
    if float(np.max(np.abs(sample))) <= TOL:
        return lambda _f: 0.0, (0.0,), "flat"
    if model_id == "M2_LOG":
        objective = lambda f: f * m.m1 + 0.5 * f * (1 - f) * m.m2
        roots = () if abs(m.m2) <= TOL else (0.5 + m.m1 / m.m2,)
        return objective, roots, "global" if roots else "flat"
    if model_id == "M3_LOG":
        objective = lambda f: (
            f * m.m1 + 0.5 * f * (1 - f) * m.m2
            + f * (1 - f) * (1 - 2 * f) * m.m3 / 6
        )
        roots = _real_roots((m.m3, -(m.m2 + m.m3), m.m1 + 0.5 * m.m2 + m.m3 / 6))
        local = tuple(f for f in roots if 2 * m.m3 * f - (m.m2 + m.m3) < -TOL)
        return objective, local, "local" if local else "unavailable"
    if model_id == "M4_LOG_ZERO":
        objective = lambda f: (
            f * m.m1 + 0.5 * f * (1 - f) * m.m2
            + f * (1 - f) * (1 - 2 * f) * m.m3 / 6
            + f * (1 - f) * (1 - 6 * f + 6 * f * f) * m.m4 / 24
        )
        roots = _real_roots((-m.m4, m.m3 + 1.5 * m.m4, -(m.m2 + m.m3 + 7 * m.m4 / 12), m.m1 + m.m2 / 2 + m.m3 / 6 + m.m4 / 24))
        return objective, roots, "global" if roots else "flat"
    if model_id == "M4_SIMPLE":
        objective = lambda f: m.q1 * f - m.q2 * f * f / 2 + m.q3 * f**3 / 3 - m.q4 * f**4 / 4
        roots = _real_roots((-m.q4, m.q3, -m.q2, m.q1))
        return objective, roots, "global" if roots else "flat"
    if model_id == "M4_LOG_MEAN":
        a = exp(m.mu)
        delta = a - 1.0

        def objective(f: float) -> float:
            d = 1 + f * delta
            if d <= 0:
                return -inf
            p = f * a / d
            return (
                log(d) + p * (1 - p) * m.nu2 / 2
                + p * (1 - p) * (1 - 2 * p) * m.nu3 / 6
                + p * (1 - p) * (1 - 6 * p + 6 * p * p) * m.nu4 / 24
            )

        q0 = m.nu2 / 2 + m.nu3 / 6 + m.nu4 / 24
        q1 = -m.nu2 - m.nu3 - 7 * m.nu4 / 12
        q2 = m.nu3 + 1.5 * m.nu4
        q3 = -m.nu4
        p_roots = _real_roots((-delta * q3, a * q3 - delta * q2, a * q2 - delta * q1, a * q1 - delta * q0, delta + a * q0))
        candidates = []
        for p in p_roots:
            denominator = a - delta * p
            if abs(denominator) <= TOL:
                continue
            f = p / denominator
            if 1 + f * delta <= 0:
                continue
            h = 1e-5 * max(1.0, abs(f))
            if objective(f + h) - 2 * objective(f) + objective(f - h) < 0:
                candidates.append(f)
        return objective, tuple(candidates), "local" if candidates else "unavailable"
    if model_id == "EMPIRICAL_EXACT":
        def objective(f: float) -> float:
            values = 1 + f * sample
            return float(np.mean(np.log(values))) if np.all(values > 0) else -inf

        positive, negative = sample[sample > 0], sample[sample < 0]
        if not positive.size and not negative.size:
            return objective, (0.0,), "flat"
        if not positive.size or not negative.size:
            return objective, (), "unavailable"
        lo = max(-1 / positive)
        hi = min(-1 / negative)
        left, right = np.nextafter(lo, inf), np.nextafter(hi, -inf)
        slope = lambda f: float(np.mean(sample / (1 + f * sample)))
        root = _bisect_root(slope, left, right)
        return objective, (root,), "global"
    raise ValueError(f"unsupported model_id: {model_id}")


def _bounded_candidates(model_id: str, roots: tuple[float, ...], sample: np.ndarray, lower: float, upper: float) -> tuple[float, ...]:
    candidates = [lower, upper, 0.0]
    candidates.extend(f for f in roots if lower <= f <= upper)
    if model_id == "EMPIRICAL_EXACT":
        lo, hi = _wealth_interval(sample, lower, upper, np.finfo(float).eps)
        candidates = [lo, hi, 0.0]
        slope = lambda f: float(np.mean(sample / (1 + f * sample)))
        if lo < hi and slope(lo) > 0 > slope(hi):
            candidates.append(_bisect_root(slope, lo, hi))
    return tuple(candidates)


def solve_model(
    model_id: str,
    simple_returns: Iterable[float],
    *,
    lower: float = -1.0,
    upper: float = 1.0,
    kappa: float = 0.8,
    wealth_floor: float = 1e-12,
) -> ModelDecision:
    if model_id not in MODEL_IDS:
        raise ValueError(f"unsupported model_id: {model_id}")
    if not 0 < kappa < 1:
        raise ValueError("kappa must be strictly between 0 and 1")
    sample = np.asarray(tuple(float(v) for v in simple_returns), dtype=float)
    moments = estimate_moments(sample)
    objective, raw_roots, raw_type = _objective_and_roots(model_id, moments, sample)

    if raw_type == "flat":
        raw, raw_value = 0.0, objective(0.0)
    elif raw_roots:
        raw, raw_value = _choose(raw_roots, objective)
    else:
        raw, raw_value = None, None

    bounded, bounded_value = _choose(
        _bounded_candidates(model_id, raw_roots, sample, lower, upper), objective
    )
    wealth_lo, wealth_hi = _wealth_interval(sample, lower, upper, wealth_floor)
    if model_id == "M4_SIMPLE":
        zmax = float(np.max(np.abs(sample)))
        cap = upper if zmax == 0 else kappa / zmax
        safe_intervals = _intersection(((-cap, cap),), wealth_lo, wealth_hi)
        safe_type = "SIMPLE_TAYLOR"
    elif model_id == "EMPIRICAL_EXACT":
        safe_intervals = ((wealth_lo, wealth_hi),)
        safe_type = "EXACT_DOMAIN"
    else:
        center = moments.mu if model_id == "M4_LOG_MEAN" else 0.0
        dmax = max(abs(log1p(float(value)) - center) for value in sample)

        def feasible(f: float) -> bool:
            if not wealth_lo - TOL <= f <= wealth_hi + TOL:
                return False
            if model_id == "M4_LOG_MEAN" and 1 + f * (exp(moments.mu) - 1) <= 0:
                return False
            return dmax <= kappa * _log_radius(f, center) + TOL

        safe_intervals = _log_safe_intervals(
            wealth_lo, wealth_hi, center, dmax, kappa, feasible
        )
        safe_type = "LOG_TAYLOR"
    if not safe_intervals:
        raise ValueError(f"{model_id} safe domain is empty")
    safe_candidates = [point for interval in safe_intervals for point in interval]
    safe_candidates.append(0.0)
    safe_candidates.extend(
        f for f in raw_roots if any(a - TOL <= f <= b + TOL for a, b in safe_intervals)
    )
    if model_id == "EMPIRICAL_EXACT":
        slope = lambda f: float(np.mean(sample / (1 + f * sample)))
        for a, b in safe_intervals:
            if a < b and slope(a) > 0 > slope(b):
                safe_candidates.append(_bisect_root(slope, a, b))
    safe, safe_value = _choose(safe_candidates, objective)
    return ModelDecision(
        model_id=model_id,
        raw_position=raw,
        bounded_position=bounded,
        safe_position=safe,
        raw_solution_type=raw_type,
        safe_domain_type=safe_type,
        safe_domain_intervals=safe_intervals,
        raw_objective=raw_value,
        bounded_objective=bounded_value,
        safe_objective=safe_value,
        status="ok",
        moments=moments,
    )


def solve_all_models(simple_returns: Iterable[float], **kwargs: float) -> tuple[ModelDecision, ...]:
    sample = tuple(simple_returns)
    return tuple(solve_model(model_id, sample, **kwargs) for model_id in MODEL_IDS)


# Compatibility helpers retained for callers that used the original MVP module.
raw_moments = estimate_moments


def objective(position: float, moments: MomentEstimate) -> float:
    return moments.q1 * position - moments.q2 * position**2 / 2 + moments.q3 * position**3 / 3 - moments.q4 * position**4 / 4


def derivative(position: float, moments: MomentEstimate) -> float:
    return moments.q1 - moments.q2 * position + moments.q3 * position**2 - moments.q4 * position**3


def empirical_objective(position: float, returns: Iterable[float]) -> float:
    sample = np.asarray(tuple(returns), dtype=float)
    values = 1 + position * sample
    return float(np.mean(np.log(values))) if sample.size and np.all(values > 0) else -inf


def choose_exact_position(returns: Iterable[float], lower: float = -1.0, upper: float = 1.0) -> float:
    return solve_model("EMPIRICAL_EXACT", returns, lower=lower, upper=upper).bounded_position
