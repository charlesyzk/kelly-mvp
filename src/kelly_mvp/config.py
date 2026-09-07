"""Versioned research configuration for the six-model study."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite


FREQUENCIES = ("daily", "weekly", "monthly")
MODEL_IDS = (
    "M2_LOG",
    "M3_LOG",
    "M4_LOG_ZERO",
    "M4_SIMPLE",
    "M4_LOG_MEAN",
    "EMPIRICAL_EXACT",
)
POSITION_TYPES = ("RAW", "BOUNDED", "SAFE")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    windows: dict[str, int] = field(
        default_factory=lambda: {"daily": 252, "weekly": 104, "monthly": 60}
    )
    minimum_matches: dict[str, int] = field(
        default_factory=lambda: {"daily": 252, "weekly": 52, "monthly": 24}
    )
    lower_bound: float = -1.0
    upper_bound: float = 1.0
    convergence_kappa: float = 0.8
    wealth_floor: float = 1e-12
    bootstrap_repetitions: int = 2000
    bootstrap_seed: int = 20260904
    bootstrap_blocks: dict[str, int] = field(
        default_factory=lambda: {"daily": 20, "weekly": 8, "monthly": 6}
    )
    formal_fdr: float = 0.05
    exploratory_fdr: float = 0.10
    transaction_cost_bps: float = 0.0

    def __post_init__(self) -> None:
        for name, mapping in (
            ("windows", self.windows),
            ("minimum_matches", self.minimum_matches),
            ("bootstrap_blocks", self.bootstrap_blocks),
        ):
            if set(mapping) != set(FREQUENCIES):
                raise ValueError(f"{name} must define exactly {FREQUENCIES}")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in mapping.values()):
                raise ValueError(f"each {name} value must be a positive integer")
        numeric = (
            self.lower_bound,
            self.upper_bound,
            self.convergence_kappa,
            self.wealth_floor,
            self.formal_fdr,
            self.exploratory_fdr,
            self.transaction_cost_bps,
        )
        if not all(isfinite(v) for v in numeric):
            raise ValueError("configuration values must be finite")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be less than upper_bound")
        if not 0 < self.convergence_kappa < 1:
            raise ValueError("convergence_kappa must be strictly between 0 and 1")
        if not 0 < self.wealth_floor < 1:
            raise ValueError("wealth_floor must be strictly between 0 and 1")
        if isinstance(self.bootstrap_repetitions, bool) or self.bootstrap_repetitions < 1:
            raise ValueError("bootstrap_repetitions must be a positive integer")
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise ValueError("bootstrap_seed must be an integer")
        if not 0 < self.formal_fdr <= self.exploratory_fdr < 1:
            raise ValueError("FDR levels must satisfy 0 < formal <= exploratory < 1")
        if self.transaction_cost_bps != 0:
            raise ValueError("the frozen study does not enable transaction costs")
