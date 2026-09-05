import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from kelly_mvp import PriceRow, StrategyConfig, run_backtest
from kelly_mvp.report import write_outputs


class ReportTests(unittest.TestCase):
    def test_writes_auditable_outputs(self):
        observed = date(2020, 1, 1)
        rows = []
        price = 100.0
        while len(rows) < 90:
            if observed.weekday() < 5:
                rows.append(PriceRow(observed, "X", price))
                price *= 1.001
            observed += timedelta(days=1)
        config = StrategyConfig(windows={"daily": 5, "weekly": 2, "monthly": 2})
        result = run_backtest(rows, config)
        with tempfile.TemporaryDirectory() as directory:
            summary, periods, trades, report = write_outputs(result, directory)
            self.assertTrue(summary.is_file())
            self.assertTrue(periods.is_file())
            self.assertTrue(trades.is_file())
            self.assertIn("Rolling-60 M4 Kelly", report.read_text(encoding="utf-8"))
            self.assertIn("direction_accuracy", summary.read_text(encoding="utf-8-sig"))
            self.assertIn("position_change", trades.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
