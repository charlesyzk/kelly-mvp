import hashlib
import json
import unittest
from pathlib import Path

from kelly_mvp import load_daily_prices, run_backtest


class PublicDataTests(unittest.TestCase):
    def test_committed_fred_data_matches_metadata_and_runs_all_frequencies(self):
        project = Path(__file__).resolve().parents[1]
        data_path = project / "test" / "DEXUSEU_FRED.csv"
        metadata = json.loads(
            (project / "test" / "DEXUSEU_FRED.metadata.json").read_text(
                encoding="utf-8"
            )
        )
        rows = load_daily_prices(data_path)
        self.assertEqual(len(rows), metadata["rows"])
        self.assertEqual(rows[0].date.isoformat(), metadata["first_date"])
        self.assertEqual(rows[-1].date.isoformat(), metadata["last_date"])
        self.assertEqual(
            hashlib.sha256(data_path.read_bytes()).hexdigest(),
            metadata["converted_sha256"],
        )
        result = run_backtest(rows)
        self.assertEqual(
            {summary.frequency for summary in result.summaries},
            {"daily", "weekly", "monthly"},
        )
        self.assertEqual(result.issues, ())


if __name__ == "__main__":
    unittest.main()
