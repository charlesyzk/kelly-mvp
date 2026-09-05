import unittest
from datetime import date, timedelta

from kelly_mvp import PriceRow, StrategyConfig, run_backtest


class BacktestTests(unittest.TestCase):
    def test_signal_uses_prior_window_and_next_return(self):
        start = date(2020, 1, 1)
        rows = []
        observed = start
        price = 100.0
        while len(rows) < 90:
            if observed.weekday() < 5:
                rows.append(PriceRow(observed, "X", price))
                price *= 1.001
            observed += timedelta(days=1)
        result = run_backtest(rows, StrategyConfig(windows={"daily": 60, "weekly": 2, "monthly": 2}))
        daily = [row for row in result.periods if row.frequency == "daily"]
        self.assertTrue(daily)
        first = daily[0]
        self.assertEqual(first.signal_date, rows[60].date)
        self.assertEqual(first.return_date, rows[61].date)
        self.assertEqual(first.window_end_date, first.signal_date)
        self.assertGreater(first.position, 0)
        self.assertTrue(first.direction_correct)

    def test_zero_realized_return_is_not_forced_into_accuracy_denominator(self):
        start = date(2020, 1, 1)
        rows = []
        observed = start
        price = 100.0
        while len(rows) < 12:
            if observed.weekday() < 5:
                rows.append(PriceRow(observed, "X", price))
                if len(rows) < 11:
                    price *= 1.01
            observed += timedelta(days=1)
        config = StrategyConfig(windows={"daily": 5, "weekly": 2, "monthly": 2})
        result = run_backtest(rows, config)
        daily = [row for row in result.periods if row.frequency == "daily"]
        self.assertIsNone(daily[-1].direction_correct)
        summary = next(
            row for row in result.summaries if row.frequency == "daily" and row.segment == "all"
        )
        self.assertEqual(summary.direction_observations, summary.active_observations - 1)

    def test_summary_contains_development_holdout_and_all(self):
        start = date(2010, 1, 1)
        rows = []
        observed = start
        price = 100.0
        while len(rows) < 1800:
            if observed.weekday() < 5:
                rows.append(PriceRow(observed, "X", price))
                price *= 1.0002
            observed += timedelta(days=1)
        result = run_backtest(rows)
        segments = {(row.frequency, row.segment) for row in result.summaries}
        for frequency in ("daily", "weekly", "monthly"):
            self.assertIn((frequency, "development"), segments)
            self.assertIn((frequency, "holdout"), segments)
            self.assertIn((frequency, "all"), segments)


if __name__ == "__main__":
    unittest.main()
