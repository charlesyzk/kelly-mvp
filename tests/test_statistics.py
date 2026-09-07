import unittest
from datetime import date, timedelta

from kelly_mvp import PriceRow, StrategyConfig, run_backtest
from kelly_mvp.statistics import compare_models


class StatisticsTests(unittest.TestCase):
    def test_bootstrap_is_reproducible_and_uses_three_positions(self):
        rows = [
            PriceRow(date(2020, 1, 1) + timedelta(days=i), "X", 100 * (1.001 + (i % 5) * 0.0001) ** i)
            for i in range(45)
        ]
        config = StrategyConfig(
            windows={"daily": 5, "weekly": 3, "monthly": 2},
            minimum_matches={"daily": 3, "weekly": 2, "monthly": 1},
            bootstrap_blocks={"daily": 2, "weekly": 2, "monthly": 1},
            bootstrap_repetitions=30,
        )
        result = run_backtest(rows, config)
        first = compare_models(result, config)
        second = compare_models(result, config)
        self.assertEqual(first, second)
        self.assertEqual({row.position_type for row in first}, {"RAW", "BOUNDED", "SAFE"})
        self.assertTrue(all(row.model_id != "M2_LOG" for row in first))


if __name__ == "__main__":
    unittest.main()
