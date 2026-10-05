import unittest
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from kelly_mvp.data import PriceRow, parse_daily_prices
from kelly_mvp.demo import generate_demo_csv
from kelly_mvp.market_store import MarketStore
from kelly_mvp.web import STATIC_DIR, calculate_payload, fetch_eodhd_payload, strategy_catalog_payload


class WebTests(unittest.TestCase):
    def test_interface_exposes_kelly_2_0_paths_and_three_positions(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("RAW · BOUNDED · SAFE", html)
        self.assertIn("止损路径", html)
        self.assertIn("EODHD 三频", html)
        self.assertIn(".xlsx", html)
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("workbook_b64", script)
        self.assertNotIn("convergence_kappa", script)
        self.assertIn("模拟逐笔买卖操作", html)
        self.assertIn("下载调仓流水 CSV", html)
        self.assertIn("策略净值 / 买入持有", html)
        self.assertIn("每个频率的比较起止日不同", html)
        self.assertIn("Kelly 六模型策略", html)
        self.assertIn("上传 Python 策略", html)
        self.assertIn("/strategy-template.py", html)
        self.assertIn("一个目标仓位，同一套验证链路", script)
        self.assertIn("买入持有（与当前策略同期）", script)
        self.assertIn("evaluationPeriod", script)
        self.assertNotIn("kelly-fraction-field", script)
        self.assertIn("运行 Kelly 2.0", script)

    @patch("kelly_mvp.web.fetch_price_bundle")
    def test_eodhd_payload_keeps_provider_frequencies(self, fetch):
        rows = [PriceRow(date(2024,1,2), "X.US", 10), PriceRow(date(2024,1,3), "X.US", 11)]
        fetch.return_value = {name: rows for name in ("daily","weekly","monthly")}
        with tempfile.TemporaryDirectory() as temp:
            store = MarketStore(Path(temp) / "market.sqlite3")
            usage = {"apiRequests":"0","apiRequestsDate":date.today().isoformat(),"dailyRateLimit":"100","extraLimit":"0"}
            with patch("kelly_mvp.web.market_store", return_value=store), patch("kelly_mvp.web.fetch_account_usage", return_value=usage):
                result = fetch_eodhd_payload({"symbol":"X.US","start_date":"2024-01-02","end_date":"2024-01-03"})
        self.assertEqual(set(result["price_series"]), {"daily","weekly","monthly"})
        self.assertEqual(parse_daily_prices(result["price_series"]["weekly"])[-1].adjusted_close, 11)

    def test_eodhd_payload_uses_covered_local_history_without_quota_calls(self):
        rows = [PriceRow(date(2024,1,2), "X.US", 10), PriceRow(date(2024,1,3), "X.US", 11)]
        with tempfile.TemporaryDirectory() as temp:
            store = MarketStore(Path(temp) / "market.sqlite3")
            store.ensure_instrument("X.US")
            for frequency in ("daily","weekly","monthly"):
                store.upsert_prices(rows, frequency)
                store.record_coverage("X.US", frequency, "2024-01-02", "2024-01-03", rows)
            with patch("kelly_mvp.web.market_store", return_value=store), \
                 patch("kelly_mvp.web.fetch_account_usage") as usage, \
                 patch("kelly_mvp.web.fetch_price_bundle") as fetch:
                result = fetch_eodhd_payload({"symbol":"X.US","start_date":"2024-01-02","end_date":"2024-01-03"})
            usage.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual(result["source"], "SQLite本地行情库")

    def test_eodhd_payload_preserves_three_calls_when_daily_budget_too_low(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MarketStore(Path(temp) / "market.sqlite3")
            usage = {"apiRequests":"98","apiRequestsDate":date.today().isoformat(),"dailyRateLimit":"100"}
            with patch("kelly_mvp.web.market_store", return_value=store), \
                 patch("kelly_mvp.web.fetch_account_usage", return_value=usage), \
                 patch("kelly_mvp.web.fetch_price_bundle") as fetch:
                with self.assertRaisesRegex(ValueError, "额度不足"):
                    fetch_eodhd_payload({"symbol":"X.US","start_date":"2024-01-02","end_date":"2024-01-03"})
            fetch.assert_not_called()

    def test_demo_payload_runs_kelly_2_0_models_and_three_positions(self):
        result = calculate_payload({"csv_text":generate_demo_csv()})
        self.assertEqual({row["model_id"] for row in result["summaries"]}, {"M2_LOG","M4_LOG_ZERO","EMPIRICAL_EXACT","EWMA_M2_LOG","EWMA_M4_LOG_ZERO","EWMA_EMPIRICAL_EXACT"})
        self.assertEqual({row["position_type"] for row in result["summaries"]}, {"RAW","BOUNDED","SAFE"})
        self.assertTrue(result["statistics"])
        self.assertTrue(result["trades"])

    def test_uploaded_strategy_uses_target_and_skips_kelly_statistics(self):
        source = '''
STRATEGY_META = {"id": "quarter", "name": "Quarter"}
def decide(context):
    return {"position": 0.25, "diagnostics": {"visible": context.prices[-1]}}
'''
        result = calculate_payload({
            "csv_text": generate_demo_csv(),
            "strategy_id": "uploaded",
            "strategy_source": source,
            "strategy_filename": "quarter.py",
        })
        self.assertEqual(result["strategy"]["id"], "quarter")
        self.assertEqual({row["model_id"] for row in result["summaries"]}, {"quarter"})
        self.assertEqual({row["position_type"] for row in result["summaries"]}, {"TARGET"})
        self.assertEqual(result["statistics"], [])

    def test_strategy_catalog_has_one_kelly_module(self):
        catalog = strategy_catalog_payload()["strategies"]
        self.assertEqual([row["id"] for row in catalog], ["KELLY_SIX_MODEL"])


if __name__ == "__main__":
    unittest.main()
