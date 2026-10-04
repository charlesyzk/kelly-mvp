"""Load a trusted, user-supplied Python position strategy."""

from __future__ import annotations

import re
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from ..config import StrategyConfig
from .contract import DiagnosticValue, StrategyContext, StrategyDecision, StrategyDefinition


_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")


def _diagnostics(value: object) -> Mapping[str, DiagnosticValue]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("strategy diagnostics must be a dictionary with string keys")
    normalized: dict[str, DiagnosticValue] = {}
    for key, item in value.items():
        if not isinstance(item, (str, int, float, bool, type(None))):
            raise ValueError(f"strategy diagnostic {key!r} must be a scalar value")
        if isinstance(item, float) and not isfinite(item):
            raise ValueError(f"strategy diagnostic {key!r} must be finite")
        normalized[key] = item
    return MappingProxyType(normalized)


def load_user_strategy(source: str, filename: str = "uploaded_strategy.py") -> StrategyDefinition:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("uploaded strategy source is empty")
    namespace: dict[str, object] = {"__name__": "uploaded_strategy", "__file__": filename}
    try:
        exec(compile(source, filename, "exec"), namespace)
    except Exception as exc:
        raise ValueError(f"cannot load uploaded strategy: {exc}") from exc

    metadata = namespace.get("STRATEGY_META")
    if not isinstance(metadata, dict):
        raise ValueError("uploaded strategy must define a STRATEGY_META dictionary")
    strategy_id = metadata.get("id")
    name = metadata.get("name")
    if not isinstance(strategy_id, str) or not _ID_PATTERN.fullmatch(strategy_id):
        raise ValueError("STRATEGY_META.id must be 2-64 letters, numbers, dots, dashes or underscores")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("STRATEGY_META.name must be a nonempty string")
    decide_function = namespace.get("decide")
    if not callable(decide_function):
        raise ValueError("uploaded strategy must define decide(context)")

    def decide(context: StrategyContext, config: StrategyConfig) -> StrategyDecision:
        try:
            result = decide_function(context)
        except Exception as exc:
            raise ValueError(
                f"uploaded strategy {strategy_id} failed at {context.signal_date}: {exc}"
            ) from exc
        if isinstance(result, bool):
            raise ValueError("strategy position must be a finite number, not bool")
        diagnostics: Mapping[str, DiagnosticValue] = MappingProxyType({})
        if isinstance(result, dict):
            if "position" not in result:
                raise ValueError("strategy result dictionary must contain position")
            position_value = result["position"]
            diagnostics = _diagnostics(result.get("diagnostics"))
        else:
            position_value = result
        try:
            raw_position = float(position_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("strategy position must be a finite number") from exc
        if not isfinite(raw_position):
            raise ValueError("strategy position must be finite")
        lower, upper = config.lower_bound, config.upper_bound
        bounded = max(lower, min(upper, raw_position))
        location = (
            "lower_bound" if bounded == lower and raw_position <= lower
            else "upper_bound" if bounded == upper and raw_position >= upper
            else "interior"
        )
        return StrategyDecision(
            raw_position=raw_position,
            bounded_position=bounded,
            position=bounded,
            optimizer_location=location,
            diagnostics=diagnostics,
        )

    version = metadata.get("version", "1.0")
    description = metadata.get("description", "用户上传策略")
    return StrategyDefinition(
        id=strategy_id,
        name=name.strip(),
        version=str(version),
        description=str(description),
        kind="uploaded",
        supports_kappa=False,
        decide=decide,
    )
