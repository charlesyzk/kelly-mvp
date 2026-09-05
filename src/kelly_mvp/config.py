"""Single source of truth for the simplified strategy assumptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite


FREQUENCIES = ("daily", "weekly", "monthly")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    windows: dict[str, int] = field(
        default_factory=lambda: {name: 60 for name in FREQUENCIES}
    )
    kelly_fraction: float = 0.5
    lower_bound: float = -1.0
    upper_bound: float = 1.0
    transaction_cost_bps: float = 0.0
    development_fraction: float = 0.7

    def __post_init__(self) -> None:
        if set(self.windows) != set(FREQUENCIES):
            raise ValueError(f"windows must define exactly {FREQUENCIES}")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 2 for v in self.windows.values()):
            raise ValueError("each rolling window must be an integer >= 2")
        numeric = (
            self.kelly_fraction,
            self.lower_bound,
            self.upper_bound,
            self.transaction_cost_bps,
            self.development_fraction,
        )
        if not all(isfinite(v) for v in numeric):
            raise ValueError("configuration values must be finite")
        if not 0 < self.kelly_fraction <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be less than upper_bound")
        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps cannot be negative")
        if not 0 < self.development_fraction < 1:
            raise ValueError("development_fraction must be in (0, 1)")
