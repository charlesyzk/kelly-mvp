import unittest
from datetime import date
from math import isfinite

from kelly_mvp.config import StrategyConfig
from kelly_mvp.model import choose_position
from kelly_mvp.module_strategy import (
    BUILTIN_STRATEGY_IDS,
    StrategyContext,
    get_builtin_strategy,
    load_user_strategy,
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

    def test_catalog_contains_the_complete_six_model_kelly_family(self):
        self.assertEqual(
            BUILTIN_STRATEGY_IDS,
            (
                "M2_LOG",
                "M3_LOG",
                "M4_LOG_ZERO",
                "M4_SIMPLE",
                "M4_LOG_MEAN",
                "EMPIRICAL_EXACT",
            ),
        )

    def test_every_builtin_model_produces_a_finite_bounded_position(self):
        for strategy_id in BUILTIN_STRATEGY_IDS:
            decision = get_builtin_strategy(strategy_id).decide(self.context, self.config)
            self.assertTrue(isfinite(decision.position), strategy_id)
            self.assertGreaterEqual(decision.position, -1.0, strategy_id)
            self.assertLessEqual(decision.position, 1.0, strategy_id)
            self.assertEqual(decision.diagnostics["kelly_model"], strategy_id)

    def test_m4_simple_wrapper_preserves_the_existing_strategy_result(self):
        expected = choose_position(self.context.returns)
        actual = get_builtin_strategy("M4_SIMPLE").decide(self.context, self.config)
        self.assertAlmostEqual(actual.position, expected.position)
        self.assertAlmostEqual(actual.raw_position, expected.raw_kelly)
        self.assertAlmostEqual(actual.bounded_position, expected.full_kelly)
        self.assertAlmostEqual(actual.exact_full_kelly, expected.exact_full_kelly)

    def test_user_strategy_template_contract_loads_and_returns_diagnostics(self):
        source = '''
STRATEGY_META = {
    "id": "constant_quarter",
    "name": "固定四分之一仓位",
    "version": "1.0",
    "description": "测试策略",
}

def decide(context):
    return {
        "position": 0.25,
        "diagnostics": {"observations": len(context.returns)},
    }
'''
        strategy = load_user_strategy(source, filename="constant_quarter.py")
        decision = strategy.decide(self.context, self.config)
        self.assertEqual(strategy.id, "constant_quarter")
        self.assertEqual(strategy.kind, "uploaded")
        self.assertEqual(decision.position, 0.25)
        self.assertEqual(decision.diagnostics["observations"], 60)

    def test_user_strategy_must_define_metadata_and_a_finite_position(self):
        with self.assertRaisesRegex(ValueError, "STRATEGY_META"):
            load_user_strategy("def decide(context): return 0")
        strategy = load_user_strategy(
            'STRATEGY_META={"id":"bad","name":"Bad"}\ndef decide(context): return float("nan")'
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            strategy.decide(self.context, self.config)


if __name__ == "__main__":
    unittest.main()
