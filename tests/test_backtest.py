import unittest
from datetime import date, timedelta

from kelly_mvp import PriceRow, StrategyConfig, classify_trade, run_backtest


def prices(count=90, growth=1.001):
    return [PriceRow(date(2020, 1, 1) + timedelta(days=i), "X", 100 * growth**i) for i in range(count)]


class BacktestTests(unittest.TestCase):
    def test_trade_actions_cover_open_adjust_reverse_and_close(self):
        cases = {
            (0.0, 0.4): "open_long", (0.4, 0.7): "add_long",
            (0.7, 0.2): "reduce_long", (0.2, 0.0): "close_long",
            (0.0, -0.4): "open_short", (-0.4, -0.7): "add_short",
            (-0.7, -0.2): "cover_short", (-0.2, 0.0): "close_short",
            (-0.4, 0.3): "reverse_to_long", (0.4, -0.3): "reverse_to_short",
        }
        for positions, expected in cases.items():
            self.assertEqual(classify_trade(*positions), expected)
        self.assertIsNone(classify_trade(0.2, 0.2))
        self.assertIsNone(classify_trade(None, 0.2))

    def config(self):
        return StrategyConfig(
            windows={"daily": 5, "weekly": 3, "monthly": 2},
            minimum_matches={"daily": 3, "weekly": 2, "monthly": 1},
            bootstrap_blocks={"daily": 2, "weekly": 2, "monthly": 1},
            bootstrap_repetitions=20,
        )

    def test_generates_six_models_and_three_positions(self):
        result = run_backtest(prices(), self.config())
        daily = [row for row in result.periods if row.frequency == "daily"]
        self.assertEqual({row.model_id for row in daily}, {"M2_LOG","M3_LOG","M4_LOG_ZERO","M4_SIMPLE","M4_LOG_MEAN","EMPIRICAL_EXACT"})
        self.assertEqual({row.position_type for row in daily}, {"RAW","BOUNDED","SAFE"})
        first = daily[0]
        self.assertEqual(first.signal_date, date(2020, 1, 6))
        self.assertEqual(first.return_date, date(2020, 1, 7))
        self.assertTrue(all(hasattr(first, name) for name in ("m1","m2","m3","m4","mu","nu2","nu3","nu4","q1","q2","q3","q4")))
        self.assertTrue(result.trades)
        self.assertEqual({row.position_type for row in result.trades}, {"RAW","BOUNDED","SAFE"})

    def test_pending_signal_is_not_evaluated(self):
        direct_prices = {frequency: prices() for frequency in ("daily", "weekly", "monthly")}
        result = run_backtest(direct_prices, self.config())
        pending = [row for row in result.signals if row.evaluation_status == "pending"]
        self.assertEqual(len(pending), 6 * 3 * 3)
        self.assertTrue(any(row.evaluation_status == "pending" for row in result.trades))

    def test_zero_return_is_unsuccessful_for_nonzero_position(self):
        rows = prices(12)
        rows[-1] = PriceRow(rows[-1].date, "X", rows[-2].adjusted_close)
        result = run_backtest(rows, self.config())
        candidates = [row for row in result.periods if row.frequency == "daily" and row.return_date == rows[-1].date and row.position_value not in (None, 0)]
        self.assertTrue(candidates)
        self.assertTrue(all(row.direction_success is False for row in candidates))

    def test_bankruptcy_receives_wealth_floor_penalty(self):
        rows = [PriceRow(date(2020,1,1)+timedelta(days=i), "X", 100*(0.99**i)) for i in range(8)]
        rows.append(PriceRow(date(2020,1,9), "X", rows[-1].adjusted_close*3))
        result = run_backtest(rows, self.config())
        bankrupt = [row for row in result.periods if row.bankrupt]
        self.assertTrue(bankrupt)
        self.assertTrue(all(row.truncated_log_growth is not None for row in bankrupt))


if __name__ == "__main__":
    unittest.main()
