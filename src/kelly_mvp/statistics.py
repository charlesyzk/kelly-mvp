"""Paired circular-block bootstrap and prespecified BH-FDR families."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from math import log

import numpy as np

from .backtest import BacktestResult, PeriodResult
from .config import MODEL_IDS, POSITION_TYPES, StrategyConfig


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    symbol: str
    frequency: str
    model_id: str
    position_type: str
    matched_observations: int
    minimum_sample_met: bool
    mean_growth: float | None
    mean_difference_vs_m2: float | None
    p_superior_to_m2: float | None
    q_superior_to_m2: float | None
    mean_difference_vs_buy_hold: float | None
    buy_hold_difference_lower95: float | None
    buy_hold_difference_upper95: float | None
    supported_at_05: bool
    exploratory_at_10: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _rng(seed: int, *parts: object) -> np.random.Generator:
    material = "|".join((str(seed), *(str(part) for part in parts)))
    digest = hashlib.blake2b(material.encode(), digest_size=8).digest()
    return np.random.default_rng(int.from_bytes(digest, "little"))


def _block_means(values: np.ndarray, block: int, repetitions: int, rng: np.random.Generator) -> np.ndarray:
    n = len(values)
    length = min(block, n)
    extended = np.concatenate((values, values[: max(0, length - 1)]))
    cumulative = np.concatenate(([0.0], np.cumsum(extended)))
    block_sums = cumulative[length:length + n] - cumulative[:n]
    full, remainder = divmod(n, length)
    draws = full + int(remainder > 0)
    starts = rng.integers(0, n, size=(repetitions, draws))
    totals = block_sums[starts[:, :full]].sum(axis=1)
    if remainder:
        partial = cumulative[remainder:remainder + n] - cumulative[:n]
        totals += partial[starts[:, -1]]
    return totals / n


def _bootstrap(values: list[float], config: StrategyConfig, frequency: str, *seed_parts: object):
    x = np.asarray(values, dtype=float)
    observed = float(x.mean())
    means = _block_means(
        x,
        config.bootstrap_blocks[frequency],
        config.bootstrap_repetitions,
        _rng(config.bootstrap_seed, *seed_parts),
    )
    lower, upper = np.quantile(means, [0.025, 0.975])
    centered = means - observed
    p = float((1 + np.count_nonzero(centered >= observed)) / (config.bootstrap_repetitions + 1))
    return observed, float(lower), float(upper), p


def _bh(
    rows: list[ComparisonResult],
    *,
    formal_level: float,
    exploratory_level: float,
    formal_family_size: int = 18,
) -> list[ComparisonResult]:
    valid = [(i, row.p_superior_to_m2) for i, row in enumerate(rows) if row.p_superior_to_m2 is not None]
    if not valid:
        return rows
    ordered = sorted(valid, key=lambda pair: pair[1])
    adjusted = [min(1.0, float(p) * formal_family_size / rank) for rank, (_, p) in enumerate(ordered, 1)]
    for i in range(len(adjusted) - 2, -1, -1):
        adjusted[i] = min(adjusted[i], adjusted[i + 1])
    q_by_index = {ordered[i][0]: adjusted[i] for i in range(len(ordered))}
    result = []
    for index, row in enumerate(rows):
        values = row.as_dict()
        values["q_superior_to_m2"] = q_by_index.get(index)
        q = values["q_superior_to_m2"]
        buy_ok = row.buy_hold_difference_lower95 is not None and row.buy_hold_difference_lower95 >= 0
        positive = row.mean_difference_vs_m2 is not None and row.mean_difference_vs_m2 > 0
        values["supported_at_05"] = bool(row.minimum_sample_met and positive and buy_ok and q is not None and q <= formal_level)
        values["exploratory_at_10"] = bool(row.minimum_sample_met and positive and buy_ok and q is not None and q <= exploratory_level)
        if values["supported_at_05"]:
            values["reason"] = "supported"
        elif not row.minimum_sample_met:
            values["reason"] = "insufficient_sample"
        elif not positive:
            values["reason"] = "mean_not_above_m2"
        elif q is None or q > formal_level:
            values["reason"] = "not_significant_after_fdr"
        elif not buy_ok:
            values["reason"] = "buy_hold_ci_below_zero"
        result.append(ComparisonResult(**values))
    return result


def compare_models(result: BacktestResult, config: StrategyConfig | None = None) -> tuple[ComparisonResult, ...]:
    active = config or StrategyConfig()
    groups: dict[tuple[str, str, str, str], list[PeriodResult]] = {}
    for row in result.periods:
        groups.setdefault((row.symbol, row.frequency, row.model_id, row.position_type), []).append(row)
    raw_rows: list[ComparisonResult] = []
    for (symbol, frequency, model_id, position_type), rows in sorted(groups.items()):
        if model_id == "M2_LOG":
            continue
        baseline = {
            (row.signal_date, row.return_date): row
            for row in groups.get((symbol, frequency, "M2_LOG", position_type), ())
        }
        differences: list[float] = []
        buy_differences: list[float] = []
        growth_values: list[float] = []
        for row in rows:
            base = baseline.get((row.signal_date, row.return_date))
            if row.truncated_log_growth is None or base is None or base.truncated_log_growth is None:
                continue
            growth_values.append(row.truncated_log_growth)
            differences.append(row.truncated_log_growth - base.truncated_log_growth)
            buy_growth = log(max(active.wealth_floor, 1 + row.next_return))
            buy_differences.append(row.truncated_log_growth - buy_growth)
        n = len(differences)
        eligible = n >= active.minimum_matches[frequency]
        if n:
            mean_diff, _, _, p = _bootstrap(differences, active, frequency, symbol, frequency, model_id, position_type, "m2")
            mean_buy, lower_buy, upper_buy, _ = _bootstrap(buy_differences, active, frequency, symbol, frequency, model_id, position_type, "buy")
            mean_growth = float(np.mean(growth_values))
        else:
            mean_diff = p = mean_buy = lower_buy = upper_buy = mean_growth = None
        raw_rows.append(ComparisonResult(
            symbol, frequency, model_id, position_type, n, eligible,
            mean_growth, mean_diff, p if eligible else None, None,
            mean_buy, lower_buy, upper_buy, False, False,
            "pending_fdr" if eligible else "insufficient_sample",
        ))

    final: list[ComparisonResult] = []
    for model_id in MODEL_IDS[1:]:
        for position_type in POSITION_TYPES:
            family = [row for row in raw_rows if row.model_id == model_id and row.position_type == position_type]
            final.extend(_bh(
                family,
                formal_level=active.formal_fdr,
                exploratory_level=active.exploratory_fdr,
                formal_family_size=18,
            ))
    return tuple(sorted(final, key=lambda row: (row.symbol, row.frequency, row.model_id, row.position_type)))
