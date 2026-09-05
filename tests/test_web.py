import unittest
from datetime import date
from unittest.mock import patch

from kelly_mvp.data import PriceRow, parse_daily_prices
from kelly_mvp.demo import generate_demo_csv
from kelly_mvp.web import STATIC_DIR, calculate_payload, fetch_eodhd_payload


class WebTests(unittest.TestCase):
    def test_static_interface_contains_run_and_method_sections(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("开始计算", html)
        self.assertIn("计算逻辑", html)
        self.assertIn("策略计算留在本机", html)
        self.assertIn("EODHD 取数", html)

    @patch("kelly_mvp.web.fetch_daily_prices")
    def test_eodhd_payload_becomes_strategy_csv(self, fetch):
        fetch.return_value = [
            PriceRow(date(2024, 1, 2), "300308.SHE", 35.5),
            PriceRow(date(2024, 1, 3), "300308.SHE", 36.0),
        ]
        result = fetch_eodhd_payload({
            "symbol": "300308.SHE",
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
        })
        parsed = parse_daily_prices(result["csv_text"])
        self.assertEqual(result["source"], "EODHD")
        self.assertEqual(result["rows"], 2)
        self.assertEqual(parsed[-1].adjusted_close, 36.0)

    def test_demo_payload_runs_all_three_frequencies(self):
        result = calculate_payload({
            "csv_text": generate_demo_csv(),
            "kelly_fraction": 0.5,
            "transaction_cost_bps": 10,
        })
        frequencies = {row["frequency"] for row in result["summaries"]}
        self.assertEqual(frequencies, {"daily", "weekly", "monthly"})
        self.assertTrue(result["periods"])


if __name__ == "__main__":
    unittest.main()
