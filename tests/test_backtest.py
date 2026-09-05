import unittest
from datetime import date, timedelta

from kelly_mvp import PriceRow, StrategyConfig, run_backtest
from kelly_mvp.backtest import classify_trade


class BacktestTests(unittest.TestCase):
    def test_trade_actions_cover_open_adjust_reverse_and_close(self):
        cases = {
            (0.0, 0.4): "open_long",
            (0.0, -0.4): "open_short",
            (0.4, 0.6): "add_long",
            (0.6, 0.2): "reduce_long",
            (-0.4, -0.6): "add_short",
            (-0.6, -0.2): "cover_short",
            (0.4, -0.2): "reverse_to_short",
            (-0.4, 0.2): "reverse_to_long",
            (0.4, 0.0): "close_long",
            (-0.4, 0.0): "close_short",
        }
        for positions, expected in cases.items():
            self.assertEqual(classify_trade(*positions), expected)
        self.assertIsNone(classify_trade(0.2, 0.2))

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
        self.assertEqual(first.previous_position, 0)
        self.assertAlmostEqual(first.position_change, first.position)
        self.assertTrue(first.direction_correct)
        first_trade = next(row for row in result.trades if row.frequency == "daily")
        self.assertEqual(first_trade.action, "open_long")
        self.assertAlmostEqual(first_trade.target_position, first.position)
        daily_signals = [row for row in result.signals if row.frequency == "daily"]
        self.assertEqual(daily_signals[-1].signal_date, rows[-1].date)
        self.assertEqual(daily_signals[-1].evaluation_status, "pending")
        self.assertEqual(len(daily_signals), len(daily) + 1)

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
        self.assertGreaterEqual(summary.mean_abs_exact_kelly_gap, 0)
        self.assertGreaterEqual(summary.boundary_rate, 0)
        self.assertLessEqual(summary.boundary_rate, 1)

    def test_bankruptcy_does_not_turn_log_zero_into_a_finite_number(self):
        start = date(2020, 1, 1)
        rows = [PriceRow(start, "X", 100.0)]
        for index in range(1, 61):
            rows.append(PriceRow(start + timedelta(days=index), "X", rows[-1].adjusted_close * 0.99))
        rows.append(PriceRow(start + timedelta(days=61), "X", rows[-1].adjusted_close * 3))
        result = run_backtest(rows)
        daily = [row for row in result.periods if row.frequency == "daily"]
        self.assertEqual(len(daily), 1)
        self.assertTrue(daily[0].bankrupt)
        self.assertIsNone(daily[0].log_growth)
        summary = next(
            row for row in result.summaries if row.frequency == "daily" and row.segment == "all"
        )
        self.assertIsNone(summary.average_log_growth)
        self.assertEqual(summary.annualized_log_growth, -1.0)

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
