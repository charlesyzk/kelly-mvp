"""Fourth-order simple-return Kelly approximation."""

from __future__ import annotations

from dataclasses import dataclass
from math import acos, cos, inf, isfinite, log1p, nextafter, pi, sqrt
from typing import Iterable


@dataclass(frozen=True, slots=True)
class MomentEstimate:
    q1: float
    q2: float
    q3: float
    q4: float


@dataclass(frozen=True, slots=True)
class PositionDecision:
    raw_kelly: float
    full_kelly: float
    exact_full_kelly: float
    position: float
    objective: float
    exact_objective_loss: float | None
    optimizer_location: str
    moments: MomentEstimate


def raw_moments(values: Iterable[float]) -> MomentEstimate:
    sample = tuple(float(value) for value in values)
    if not sample or not all(isfinite(value) for value in sample):
        raise ValueError("returns must be a nonempty finite sample")
    n = len(sample)
    return MomentEstimate(*(sum(value**k for value in sample) / n for k in range(1, 5)))


def objective(position: float, moments: MomentEstimate) -> float:
    q1, q2, q3, q4 = moments.q1, moments.q2, moments.q3, moments.q4
    return q1 * position - q2 * position**2 / 2 + q3 * position**3 / 3 - q4 * position**4 / 4


def derivative(position: float, moments: MomentEstimate) -> float:
    q1, q2, q3, q4 = moments.q1, moments.q2, moments.q3, moments.q4
    return q1 - q2 * position + q3 * position**2 - q4 * position**3


def empirical_objective(position: float, returns: Iterable[float]) -> float:
    """Exact empirical mean log growth for a simple-return sample."""

    sample = tuple(float(value) for value in returns)
    if not sample or not all(isfinite(value) for value in sample):
        raise ValueError("returns must be a nonempty finite sample")
    multipliers = tuple(1 + position * value for value in sample)
    if any(value <= 0 for value in multipliers):
        return -inf
    return sum(log1p(position * value) for value in sample) / len(sample)


def choose_exact_position(
    returns: Iterable[float],
    lower: float = -1.0,
    upper: float = 1.0,
) -> float:
    """Maximize the concave empirical Kelly objective inside safe bounds."""

    sample = tuple(float(value) for value in returns)
    if not sample or not all(isfinite(value) for value in sample):
        raise ValueError("returns must be a nonempty finite sample")
    if not isfinite(lower) or not isfinite(upper) or lower >= upper:
        raise ValueError("lower must be less than upper")
    if all(value == 0 for value in sample):
        return max(lower, min(upper, 0.0))
    domain_lower = max((-1 / value for value in sample if value > 0), default=-inf)
    domain_upper = min((-1 / value for value in sample if value < 0), default=inf)
    lo = max(lower, domain_lower)
    hi = min(upper, domain_upper)
    if empirical_objective(lo, sample) == -inf:
        lo = nextafter(lo, inf)
    if empirical_objective(hi, sample) == -inf:
        hi = nextafter(hi, -inf)
    if lo > hi:
        raise ValueError("no wealth-admissible position exists inside bounds")

    def slope(position: float) -> float:
        return sum(value / (1 + position * value) for value in sample) / len(sample)

    slope_lo = slope(lo)
    slope_hi = slope(hi)
    if slope_lo <= 0:
        return lo
    if slope_hi >= 0:
        return hi
    for _ in range(100):
        middle = (lo + hi) / 2
        if slope(middle) > 0:
            lo = middle
        else:
            hi = middle
    return (lo + hi) / 2


def _cuberoot(value: float) -> float:
    return (1 if value >= 0 else -1) * abs(value) ** (1 / 3)


def _real_polynomial_roots(coefficients: tuple[float, float, float, float]) -> tuple[float, ...]:
    """Real roots of d + c*x + b*x^2 + a*x^3 = 0."""

    d, c, b, a = coefficients
    scale = max(abs(value) for value in coefficients)
    if scale == 0:
        return ()
    tolerance = scale * 1e-12
    if abs(a) <= tolerance:
        if abs(b) <= tolerance:
            return () if abs(c) <= tolerance else (-d / c,)
        discriminant = c * c - 4 * b * d
        if discriminant < -tolerance:
            return ()
        if abs(discriminant) <= tolerance:
            return (-c / (2 * b),)
        root = sqrt(discriminant)
        return tuple(sorted(((-c - root) / (2 * b), (-c + root) / (2 * b))))

    aa, bb, cc = b / a, c / a, d / a
    p = bb - aa * aa / 3
    q = 2 * aa**3 / 27 - aa * bb / 3 + cc
    discriminant = (q / 2) ** 2 + (p / 3) ** 3
    disc_tolerance = max(1.0, abs((q / 2) ** 2), abs((p / 3) ** 3)) * 1e-14
    if discriminant > disc_tolerance:
        root = _cuberoot(-q / 2 + sqrt(discriminant)) + _cuberoot(-q / 2 - sqrt(discriminant))
        return (root - aa / 3,)
    if discriminant >= -disc_tolerance:
        u = _cuberoot(-q / 2)
        return tuple(sorted({2 * u - aa / 3, -u - aa / 3}))
    radius = 2 * sqrt(-p / 3)
    angle = acos(max(-1.0, min(1.0, (3 * q / (2 * p)) * sqrt(-3 / p))))
    return tuple(sorted(radius * cos((angle + 2 * pi * k) / 3) - aa / 3 for k in range(3)))


def choose_position(
    returns: Iterable[float],
    lower: float = -1.0,
    upper: float = 1.0,
    fraction: float = 0.5,
) -> PositionDecision:
    sample = tuple(float(value) for value in returns)
    moments = raw_moments(sample)
    roots = _real_polynomial_roots((moments.q1, -moments.q2, moments.q3, -moments.q4))
    raw_candidates = list(roots) or [0.0]
    raw_best = max(objective(candidate, moments) for candidate in raw_candidates)
    raw_tied = [candidate for candidate in raw_candidates if raw_best - objective(candidate, moments) <= 1e-12]
    raw = min(raw_tied, key=lambda candidate: (abs(candidate), candidate))
    candidates = [lower, upper]
    if lower <= 0 <= upper:
        candidates.append(0.0)
    candidates.extend(root for root in roots if lower <= root <= upper)
    best_value = max(objective(candidate, moments) for candidate in candidates)
    tied = [candidate for candidate in candidates if best_value - objective(candidate, moments) <= 1e-12]
    full = min(tied, key=lambda candidate: (abs(candidate), candidate))
    position = max(lower, min(upper, fraction * full))
    exact = choose_exact_position(sample, lower, upper)
    full_exact_objective = empirical_objective(full, sample)
    exact_loss = (
        max(0.0, empirical_objective(exact, sample) - full_exact_objective)
        if isfinite(full_exact_objective) else None
    )
    location = "lower_bound" if abs(full - lower) <= 1e-12 else "upper_bound" if abs(full - upper) <= 1e-12 else "interior"
    return PositionDecision(raw, full, exact, position, objective(position, moments), exact_loss, location, moments)
