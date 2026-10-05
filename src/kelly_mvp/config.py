"""Versioned research configuration for the six-model study."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite


FREQUENCIES = ("daily", "weekly", "monthly")
MODEL_IDS = (
    "M2_LOG",
    "M4_LOG_ZERO",
    "EMPIRICAL_EXACT",
    "EWMA_M2_LOG",
    "EWMA_M4_LOG_ZERO",
    "EWMA_EMPIRICAL_EXACT",
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
    wealth_floor: float = 1e-12
    bootstrap_repetitions: int = 2000
    bootstrap_seed: int = 20260904
    bootstrap_blocks: dict[str, int] = field(
        default_factory=lambda: {"daily": 20, "weekly": 8, "monthly": 6}
    )
    formal_fdr: float = 0.05
    exploratory_fdr: float = 0.10
    transaction_cost_bps: float = 0.0
    ewma_half_lives: dict[str, int] = field(
        default_factory=lambda: {"daily": 84, "weekly": 35, "monthly": 20}
    )
    stop_enabled: bool = False
    long_stop_k: float = 2.5
    short_stop_k: float = 1.5
    stop_fill_mode: str = "stop_price"
    stop_monitor_price: str = "adjusted_close"

    def __post_init__(self) -> None:
        for name, mapping in (
            ("windows", self.windows),
            ("minimum_matches", self.minimum_matches),
            ("bootstrap_blocks", self.bootstrap_blocks),
            ("ewma_half_lives", self.ewma_half_lives),
        ):
            if set(mapping) != set(FREQUENCIES):
                raise ValueError(f"{name} must define exactly {FREQUENCIES}")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in mapping.values()):
                raise ValueError(f"each {name} value must be a positive integer")
        numeric = (
            self.lower_bound,
            self.upper_bound,
            self.wealth_floor,
            self.formal_fdr,
            self.exploratory_fdr,
            self.transaction_cost_bps,
        )
        if not all(isfinite(v) for v in numeric):
            raise ValueError("configuration values must be finite")
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be less than upper_bound")
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
        if not isfinite(self.long_stop_k) or self.long_stop_k <= 0:
            raise ValueError("long_stop_k must be positive and finite")
        if not isfinite(self.short_stop_k) or self.short_stop_k <= 0:
            raise ValueError("short_stop_k must be positive and finite")
        if self.stop_fill_mode != "stop_price" or self.stop_monitor_price != "adjusted_close":
            raise ValueError("2.0 supports close-monitored stop-price fills only")
