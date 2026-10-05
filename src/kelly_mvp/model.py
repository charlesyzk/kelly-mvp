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
    safe_position: float | None
    raw_solution_type: str
    safe_domain_type: str
    safe_domain_intervals: tuple[tuple[float, float], ...]
    raw_objective: float | None
    bounded_objective: float
    safe_objective: float | None
    status: str
    moments: MomentEstimate

    def positions(self) -> tuple[tuple[str, float | None], ...]:
        return (
            ("RAW", self.raw_position),
            ("BOUNDED", self.bounded_position),
            ("SAFE", self.safe_position),
        )


def estimate_moments(simple_returns: Iterable[float], weights: Iterable[float] | None = None) -> MomentEstimate:
    z = np.asarray(tuple(float(v) for v in simple_returns), dtype=float)
    if z.size == 0 or not np.isfinite(z).all() or np.any(z <= -1):
        raise ValueError("returns must be finite, nonempty and greater than -1")
    w = np.full(z.size, 1.0 / z.size) if weights is None else np.asarray(tuple(weights), dtype=float)
    if w.shape != z.shape or not np.isfinite(w).all() or np.any(w <= 0) or not np.isclose(w.sum(), 1.0, atol=1e-12):
        raise ValueError("weights must be positive, normalized and match returns")
    y = np.log1p(z)
    weighted = lambda values: float(np.dot(w, values))
    mu = weighted(y)
    centered = y - mu
    raw_y = [weighted(y**k) for k in range(1, 5)]
    central = [weighted(centered**k) for k in range(2, 5)]
    raw_z = [weighted(z**k) for k in range(1, 5)]
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


def _taylor_safe_intervals(required_radius: float) -> tuple[tuple[float, float], ...]:
    """Full real-line Taylor convergence domain, without a leverage clip."""
    if required_radius <= TOL:
        return ((0.0, 0.0),)
    if required_radius <= pi:
        middle = ((0.0, 1.0),)
    else:
        offset = sqrt(required_radius * required_radius - pi * pi)
        left = 1.0 / (1.0 + exp(min(700.0, offset)))
        right = 1.0 - left
        middle = ((0.0, left), (right, 1.0))
    exp_radius = exp(min(700.0, required_radius))
    negative_edge = -1.0 / (exp_radius - 1.0) if exp_radius > 1 else float("-inf")
    positive_edge = 1.0 / (1.0 - exp(-required_radius)) if required_radius > 0 else float("inf")
    return ((negative_edge, 0.0), *middle, (1.0, positive_edge))


def _objective_and_roots(model_id: str, m: MomentEstimate, sample: np.ndarray, weights: np.ndarray):
    weighted = lambda values: float(np.dot(weights, values))
    aliases = {
        "EWMA_M2_LOG": "M2_LOG",
        "EWMA_M4_LOG_ZERO": "M4_LOG_ZERO",
        "EWMA_EMPIRICAL_EXACT": "EMPIRICAL_EXACT",
    }
    base_model_id = aliases.get(model_id, model_id)
    if float(np.max(np.abs(sample))) <= TOL:
        return lambda _f: 0.0, (0.0,), "flat"
    if base_model_id == "M2_LOG":
        objective = lambda f: f * m.m1 + 0.5 * f * (1 - f) * m.m2
        roots = () if abs(m.m2) <= TOL else (0.5 + m.m1 / m.m2,)
        return objective, roots, "global" if roots else "flat"
    if base_model_id == "M4_LOG_ZERO":
        objective = lambda f: (
            f * m.m1 + 0.5 * f * (1 - f) * m.m2
            + f * (1 - f) * (1 - 2 * f) * m.m3 / 6
            + f * (1 - f) * (1 - 6 * f + 6 * f * f) * m.m4 / 24
        )
        roots = _real_roots((-m.m4, m.m3 + 1.5 * m.m4, -(m.m2 + m.m3 + 7 * m.m4 / 12), m.m1 + m.m2 / 2 + m.m3 / 6 + m.m4 / 24))
        return objective, roots, "global" if roots else "flat"
    if base_model_id == "EMPIRICAL_EXACT":
        def objective(f: float) -> float:
            values = 1 + f * sample
            return weighted(np.log(values)) if np.all(values > 0) else -inf

        positive, negative = sample[sample > 0], sample[sample < 0]
        if not positive.size and not negative.size:
            return objective, (0.0,), "flat"
        if not positive.size or not negative.size:
            return objective, (), "unavailable"
        lo = max(-1 / positive)
        hi = min(-1 / negative)
        left, right = np.nextafter(lo, inf), np.nextafter(hi, -inf)
        slope = lambda f: weighted(sample / (1 + f * sample))
        root = _bisect_root(slope, left, right)
        return objective, (root,), "global"
    raise ValueError(f"unsupported model_id: {model_id}")


def _bounded_candidates(model_id: str, roots: tuple[float, ...], sample: np.ndarray, lower: float, upper: float, weights: np.ndarray) -> tuple[float, ...]:
    candidates = [lower, upper, 0.0]
    candidates.extend(f for f in roots if lower <= f <= upper)
    if model_id in {"EMPIRICAL_EXACT", "EWMA_EMPIRICAL_EXACT"}:
        lo, hi = _wealth_interval(sample, lower, upper, np.finfo(float).eps)
        candidates = [lo, hi, 0.0]
        slope = lambda f: float(np.dot(weights, sample / (1 + f * sample)))
        if lo < hi and slope(lo) > 0 > slope(hi):
            candidates.append(_bisect_root(slope, lo, hi))
    return tuple(candidates)


def solve_model(
    model_id: str,
    simple_returns: Iterable[float],
    *,
    lower: float = -1.0,
    upper: float = 1.0,
    wealth_floor: float = 1e-12,
    weights: Iterable[float] | None = None,
) -> ModelDecision:
    if model_id not in MODEL_IDS:
        raise ValueError(f"unsupported model_id: {model_id}")
    sample = np.asarray(tuple(float(v) for v in simple_returns), dtype=float)
    weight_array = np.full(sample.size, 1.0 / sample.size) if weights is None else np.asarray(tuple(weights), dtype=float)
    moments = estimate_moments(sample, weight_array)
    objective, raw_roots, raw_type = _objective_and_roots(model_id, moments, sample, weight_array)

    if raw_type == "flat":
        raw, raw_value = 0.0, objective(0.0)
    elif raw_roots:
        raw, raw_value = _choose(raw_roots, objective)
    else:
        raw, raw_value = None, None

    bounded, bounded_value = _choose(
        _bounded_candidates(model_id, raw_roots, sample, lower, upper, weight_array), objective
    )
    if raw_type == "flat":
        safe_intervals = ((0.0, 0.0),)
        safe_type = "EXACT_DOMAIN" if model_id in {"EMPIRICAL_EXACT", "EWMA_EMPIRICAL_EXACT"} else "LOG_TAYLOR"
    elif model_id in {"EMPIRICAL_EXACT", "EWMA_EMPIRICAL_EXACT"}:
        safe_lo, safe_hi = _wealth_interval(sample, -inf, inf, 1e-6)
        safe_type = "EXACT_DOMAIN"
        if isfinite(safe_lo) and isfinite(safe_hi):
            safe_intervals = ((safe_lo, safe_hi),)
        else:
            # With one-sided returns the empirical exact objective has no finite
            # maximizer on this half-line; do not invent a fallback SAFE value.
            safe_intervals = ()
    else:
        dmax = max(abs(log1p(float(value))) for value in sample)
        # The spec uses strict Taylor convergence with a numeric inward margin,
        # not a tunable kappa multiplier.
        required_radius = dmax / (1.0 - 1e-12)
        safe_intervals = _taylor_safe_intervals(required_radius)
        safe_type = "LOG_TAYLOR"
    safe = None
    safe_value = None
    if safe_intervals:
        safe_candidates = [point for interval in safe_intervals for point in interval]
        safe_candidates.extend(
            f for f in raw_roots if any(a - TOL <= f <= b + TOL for a, b in safe_intervals)
        )
        if safe_type == "EXACT_DOMAIN":
            slope = lambda f: float(np.dot(weight_array, sample / (1 + f * sample)))
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
        status="ok" if safe is not None else "no_finite_safe_optimum",
        moments=moments,
    )


def solve_all_models(simple_returns: Iterable[float], **kwargs: object) -> tuple[ModelDecision, ...]:
    sample = tuple(simple_returns)
    return tuple(solve_model(model_id, sample, **kwargs) for model_id in MODEL_IDS)


# Compatibility helpers retained for callers that used the original MVP module.
raw_moments = estimate_moments


def objective(position: float, moments: MomentEstimate) -> float:
    return moments.q1 * position - moments.q2 * position**2 / 2 + moments.q3 * position**3 / 3 - moments.q4 * position**4 / 4


def derivative(position: float, moments: MomentEstimate) -> float:
    return moments.q1 - moments.q2 * position + moments.q3 * position**2 - moments.q4 * position**3


def empirical_objective(position: float, returns: Iterable[float], weights: Iterable[float] | None = None) -> float:
    sample = np.asarray(tuple(returns), dtype=float)
    values = 1 + position * sample
    if not sample.size or not np.all(values > 0):
        return -inf
    weight_array = np.full(sample.size, 1.0 / sample.size) if weights is None else np.asarray(tuple(weights), dtype=float)
    if weight_array.shape != sample.shape or not np.isfinite(weight_array).all() or np.any(weight_array <= 0) or not np.isclose(weight_array.sum(), 1.0, atol=1e-12):
        raise ValueError("weights must be positive, normalized and match returns")
    return float(np.dot(weight_array, np.log(values)))


def choose_exact_position(returns: Iterable[float], lower: float = -1.0, upper: float = 1.0) -> float:
    return solve_model("EMPIRICAL_EXACT", returns, lower=lower, upper=upper).bounded_position
