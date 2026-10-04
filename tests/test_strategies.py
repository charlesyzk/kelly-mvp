import unittest
from datetime import date

from kelly_mvp.config import StrategyConfig
from kelly_mvp.module_strategy import (
    BUILTIN_STRATEGY_IDS,
    KELLY_STRATEGY_ID,
    StrategyContext,
    get_builtin_strategy,
    load_user_strategy,
    strategy_catalog,
)


class StrategyModuleTests(unittest.TestCase):
    def setUp(self):
        self.config = StrategyConfig()
        sample = (0.02, -0.01, 0.015, -0.005) * 15
        self.context = StrategyContext(
            symbol="X",
            frequency="daily",
            signal_date=date(2024, 3, 29),
            window_start_date=date(2024, 1, 2),
            window_end_date=date(2024, 3, 29),
            prices=(100.0,) + tuple(100.0 for _ in sample),
            returns=sample,
        )

    def test_catalog_exposes_kelly_as_one_top_level_strategy(self):
        self.assertEqual(BUILTIN_STRATEGY_IDS, ("KELLY_SIX_MODEL",))
        strategy = get_builtin_strategy(KELLY_STRATEGY_ID)
        self.assertEqual(strategy.kind, "builtin_kelly_suite")
        self.assertTrue(strategy.supports_kappa)
        self.assertIsNone(strategy.decide)
        self.assertEqual([row["id"] for row in strategy_catalog()], [KELLY_STRATEGY_ID])

    def test_user_strategy_contract_loads_and_returns_diagnostics(self):
        source = '''
STRATEGY_META = {"id": "constant_quarter", "name": "固定四分之一仓位"}
def decide(context):
    return {"position": 0.25, "diagnostics": {"observations": len(context.returns)}}
'''
        strategy = load_user_strategy(source, filename="constant_quarter.py")
        decision = strategy.decide(self.context, self.config)
        self.assertEqual(strategy.kind, "uploaded")
        self.assertEqual(decision.position, 0.25)
        self.assertEqual(decision.diagnostics["observations"], 60)

    def test_user_strategy_rejects_missing_metadata_and_nonfinite_position(self):
        with self.assertRaisesRegex(ValueError, "STRATEGY_META"):
            load_user_strategy("def decide(context): return 0")
        strategy = load_user_strategy(
            'STRATEGY_META={"id":"bad","name":"Bad"}\ndef decide(context): return float("nan")'
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            strategy.decide(self.context, self.config)


if __name__ == "__main__":
    unittest.main()
