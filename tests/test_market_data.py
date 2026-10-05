import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from kelly_mvp.data import PriceRow
from kelly_mvp.eodhd import EODHDHTTPError
from kelly_mvp.market_jobs import create_fetch_job, pause_fetch_job, resume_fetch_job, run_fetch_job
from kelly_mvp.market_store import MarketStore
from kelly_mvp.universes import _global_index_members
from kelly_mvp.web import STATIC_DIR, market_catalog_payload, market_export_csv


class MarketStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MarketStore(Path(self.temp.name) / "market.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def import_group(self, collection_id, name, members):
        return self.store.import_snapshot(
            collection_id, name, "https://example.test/list.csv", "test mapping",
            f"sha-{collection_id}", "unknown", "test snapshot", members,
        )

    def test_snapshots_keep_membership_but_prices_are_symbol_frequency_deduplicated(self):
        shared = {"symbol":"SHARED.US","source_symbol":"SHARED","name":"Shared Corp","asset_type":"stock"}
        self.import_group("alpha", "Alpha", [shared, {"symbol":"A.US","source_symbol":"A","name":"A"}])
        self.import_group("beta", "Beta", [shared, {"symbol":"B.US","source_symbol":"B","name":"B"}])
        rows = self.store.list_instruments(query="SHARED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]["collection_ids"].split(",")), {"alpha", "beta"})
        self.store.upsert_prices([PriceRow(date(2024,1,2),"SHARED.US",10)], "daily")
        self.store.upsert_prices([PriceRow(date(2024,1,2),"SHARED.US",11)], "daily")
        self.assertEqual(len(self.store.get_price_series("SHARED.US","daily")), 1)
        self.assertEqual(self.store.get_price_series("SHARED.US","daily")[0].adjusted_close, 11)

    def test_catalog_uses_latest_membership_while_preserving_old_snapshot(self):
        self.import_group("alpha", "Alpha", [
            {"symbol":"A.US","source_symbol":"A","name":"A"},
            {"symbol":"B.US","source_symbol":"B","name":"B"},
        ])
        self.import_group("alpha", "Alpha", [
            {"symbol":"B.US","source_symbol":"B","name":"B"},
            {"symbol":"C.US","source_symbol":"C","name":"C"},
        ])
        self.assertEqual(self.store.collection_symbols(["alpha"]), ["B.US", "C.US"])
        self.assertEqual({row["symbol"] for row in self.store.list_instruments()}, {"B.US", "C.US"})
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots WHERE collection_id='alpha'").fetchone()[0], 2)

    def test_current_global_index_list_has_fifteen_codes_and_flags_two_unverified(self):
        source = Path.home() / "Downloads" / "全球15个大盘指数_EODHD代码.html"
        if not source.is_file():
            self.skipTest("user-provided global index HTML is unavailable")
        members = _global_index_members(source.read_bytes())
        self.assertEqual(len(members), 15)
        flagged = {row["symbol"] for row in members if row["verification_status"] == "needs_api_validation"}
        self.assertEqual(flagged, {"WISGP.INDX", "BVSP.INDX"})

    def test_catalog_reports_local_store_and_imported_current_snapshot(self):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        result = market_catalog_payload(store=self.store)
        self.assertTrue(result["database_path"].endswith("market.sqlite3"))
        self.assertEqual(result["collections"][0]["member_count"], 1)
        self.assertEqual(result["instruments"][0]["symbol"], "A.US")
        self.assertEqual(result["exchanges"], ["US"])

    def test_exchange_filter_and_export_include_full_matching_inventory(self):
        self.import_group("alpha", "Alpha", [
            {"symbol":"A.US","source_symbol":"A","name":"A"},
            {"symbol":"B.SHG","source_symbol":"B","name":"B"},
        ])
        rows = self.store.list_instruments(["alpha"], exchange="US")
        self.assertEqual([row["symbol"] for row in rows], ["A.US"])
        exported = market_export_csv(self.store, collections=["alpha"], exchange="SHG").decode("utf-8-sig")
        self.assertIn("B.SHG", exported)
        self.assertNotIn("A.US", exported)

    def test_job_can_be_limited_to_exchange_suffix(self):
        self.import_group("alpha", "Alpha", [
            {"symbol":"A.US","source_symbol":"A","name":"A"},
            {"symbol":"B.SHG","source_symbol":"B","name":"B"},
        ])
        job_id = create_fetch_job(self.store, ["alpha"], ["daily"], exchange_filter="shg")
        detail = self.store.job_detail(job_id)
        self.assertEqual({item["symbol"] for item in detail["items"]}, {"B.SHG"})

    @patch("kelly_mvp.market_jobs.fetch_prices")
    @patch("kelly_mvp.market_jobs.fetch_account_usage")
    def test_incremental_job_requests_only_recent_overlap(self, usage, fetch_prices):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        latest = date.today() - timedelta(days=10)
        self.store.upsert_prices([PriceRow(latest, "A.US", 10.0)], "daily")
        self.store.save_exchange_directory("US", {"A.US"})
        usage.return_value = {"apiRequests":"0","apiRequestsDate":date.today().isoformat(),"dailyRateLimit":"100"}
        fetch_prices.return_value = [PriceRow(date.today(), "A.US", 11.0)]
        with patch.dict(os.environ, {"EODHD_API_TOKEN":"test-environment-token"}):
            job_id = create_fetch_job(self.store, ["alpha"], ["daily"])
            result = run_fetch_job(self.store, job_id)
        self.assertEqual(result["job"]["status"], "completed")
        self.assertEqual(fetch_prices.call_args.args[1], latest - timedelta(days=45))

    def test_resume_requeues_stale_running_items_and_pause_is_visible_across_processes(self):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        job_id = create_fetch_job(self.store, ["alpha"], ["daily"])
        self.store.update_job(job_id, status="running")
        self.store.update_job_item(job_id, "A.US", "daily", status="running")
        pause_fetch_job(self.store, job_id)
        self.assertEqual(self.store.job_detail(job_id)["job"]["status"], "pause_requested")
        resumed = resume_fetch_job(self.store, job_id, run_async=False)
        self.assertEqual(resumed["items"][0]["status"], "pending")
        self.assertEqual(resumed["job"]["status"], "queued")

    @patch("kelly_mvp.market_jobs.fetch_prices")
    @patch("kelly_mvp.market_jobs.fetch_exchange_symbols")
    @patch("kelly_mvp.market_jobs.fetch_account_usage")
    def test_job_prevalidates_once_fetches_all_provider_frequencies_and_records_coverage(
        self, usage, exchange_symbols, fetch_prices
    ):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        today = date.today().isoformat()
        usage.return_value = {"apiRequests": "0", "apiRequestsDate": today, "dailyRateLimit": "100", "extraLimit": "0"}
        exchange_symbols.return_value = {"A.US"}
        fetch_prices.side_effect = lambda symbol, _start, _end, frequency: [
            PriceRow(date(1998,5,1), symbol, 10.0),
            PriceRow(date(1998,5,2), symbol, 11.0),
        ]
        with patch.dict(os.environ, {"EODHD_API_TOKEN":"test-environment-token"}):
            job_id = create_fetch_job(self.store, ["alpha"])
            result = run_fetch_job(self.store, job_id, batch_size=2)
        self.assertEqual(result["job"]["status"], "completed")
        self.assertEqual(fetch_prices.call_count, 3)
        self.assertEqual(exchange_symbols.call_count, 1)
        self.assertEqual({item["frequency"] for item in result["items"]}, {"daily","weekly","monthly"})
        for item in result["items"]:
            self.assertEqual(item["first_date"], "1998-05-01")
            self.assertEqual(item["last_date"], "1998-05-02")
        self.assertEqual(self.store.exchange_directory("US"), {"A.US"})
        self.assertEqual(self.store.price_status(["A.US"])["A.US"]["daily"]["row_count"], 2)

    @patch("kelly_mvp.market_jobs.fetch_account_usage")
    @patch("kelly_mvp.market_jobs.fetch_exchange_symbols")
    def test_zero_daily_quota_pauses_before_exchange_or_price_requests(self, exchange_symbols, usage):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        usage.return_value = {"apiRequests":"100","apiRequestsDate":date.today().isoformat(),"dailyRateLimit":"100"}
        with patch.dict(os.environ, {"EODHD_API_TOKEN":"test-environment-token"}):
            job_id = create_fetch_job(self.store, ["alpha"])
            result = run_fetch_job(self.store, job_id)
        self.assertEqual(result["job"]["status"], "paused_quota")
        exchange_symbols.assert_not_called()

    @patch("kelly_mvp.market_jobs.fetch_prices")
    @patch("kelly_mvp.market_jobs.fetch_exchange_symbols")
    @patch("kelly_mvp.market_jobs.fetch_account_usage")
    def test_prevalidation_reserves_one_call_for_price_data(self, usage, exchange_symbols, fetch_prices):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        usage.return_value = {"apiRequests":"99","apiRequestsDate":date.today().isoformat(),"dailyRateLimit":"100"}
        with patch.dict(os.environ, {"EODHD_API_TOKEN":"test-environment-token"}):
            job_id = create_fetch_job(self.store, ["alpha"], ["daily"])
            result = run_fetch_job(self.store, job_id)
        self.assertEqual(result["job"]["status"], "paused_quota")
        exchange_symbols.assert_not_called()
        fetch_prices.assert_not_called()

    @patch("kelly_mvp.market_jobs.fetch_exchange_symbols")
    @patch("kelly_mvp.market_jobs.fetch_account_usage", side_effect=EODHDHTTPError(429, 5))
    def test_usage_endpoint_rate_limit_pauses_without_retrying(self, usage, exchange_symbols):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        with patch.dict(os.environ, {"EODHD_API_TOKEN":"test-environment-token"}):
            job_id = create_fetch_job(self.store, ["alpha"])
            result = run_fetch_job(self.store, job_id)
        self.assertEqual(result["job"]["status"], "paused_rate_limit")
        self.assertEqual(usage.call_count, 1)
        exchange_symbols.assert_not_called()

    @patch("kelly_mvp.market_jobs.fetch_account_usage")
    def test_missing_token_records_needs_token_without_network_calls(self, usage):
        self.import_group("alpha", "Alpha", [{"symbol":"A.US","source_symbol":"A","name":"A"}])
        with patch.dict(os.environ, {}, clear=True):
            job_id = create_fetch_job(self.store, ["alpha"])
            result = run_fetch_job(self.store, job_id)
        self.assertEqual(result["job"]["status"], "needs_token")
        usage.assert_not_called()

    def test_management_page_and_research_selector_are_shipped(self):
        self.assertTrue((STATIC_DIR / "data.html").is_file())
        self.assertTrue((STATIC_DIR / "data.js").is_file())
        research = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("market-collection-select", research)
        self.assertIn("market-exchange-select", research)
        self.assertIn("本地清单", research)
        management = (STATIC_DIR / "data.html").read_text(encoding="utf-8")
        self.assertIn("export-inventory", management)
        self.assertIn("fetch-exchange", management)


if __name__ == "__main__":
    unittest.main()
