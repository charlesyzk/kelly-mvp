"""Top-level metadata for the complete higher-moment Kelly module."""

from .contract import StrategyDefinition


KELLY_STRATEGY_ID = "KELLY_SIX_MODEL"
BUILTIN_STRATEGY_IDS = (KELLY_STRATEGY_ID,)

BUILTIN_STRATEGIES = {
    KELLY_STRATEGY_ID: StrategyDefinition(
        id=KELLY_STRATEGY_ID,
        name="Kelly 六模型策略",
        version="2.0",
        description="完整运行六个 Kelly 模型及 RAW、BOUNDED、SAFE 三类独立仓位。",
        kind="builtin_kelly_suite",
        supports_kappa=True,
        decide=None,
    )
}
