"""Stable contract shared by built-in and uploaded position strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Mapping, TypeAlias

from ..config import StrategyConfig


DiagnosticValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Information visible to a strategy at one signal timestamp.

    ``prices`` contains one more item than ``returns`` and ends at
    ``signal_date``. No future price or realized next-period return is exposed.
    """

    symbol: str
    frequency: str
    signal_date: date
    window_start_date: date
    window_end_date: date
    prices: tuple[float, ...]
    returns: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    raw_position: float | None
    bounded_position: float
    position: float
    optimizer_location: str = "interior"
    objective: float | None = None
    exact_full_kelly: float | None = None
    exact_objective_loss: float | None = None
    moments: Mapping[str, float] = field(default_factory=dict)
    diagnostics: Mapping[str, DiagnosticValue] = field(default_factory=dict)


DecisionFunction: TypeAlias = Callable[[StrategyContext, StrategyConfig], StrategyDecision]


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    id: str
    name: str
    version: str
    description: str
    kind: str
    uses_kelly_fraction: bool
    decide: DecisionFunction

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "kind": self.kind,
            "uses_kelly_fraction": self.uses_kelly_fraction,
        }
