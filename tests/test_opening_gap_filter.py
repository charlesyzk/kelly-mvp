import unittest
from datetime import date, timedelta

from kelly_mvp.data import PriceRow
from kelly_mvp.module_strategy.opening_gap_filter import (
    OpeningGapFilterConfig,
    compute_opening_gap_filter,
)


class OpeningGapFilterTests(unittest.TestCase):
    def test_uses_prior_completed_sessions_and_strict_threshold(self):
        rows = []
        start = date(2024, 1, 1)
        for index in range(33):
            close = 100.0 + index
            opening = close - 1 if index == 30 else close + 0.5
            rows.append(PriceRow(
                start + timedelta(days=index), "X", close,
                adjusted_open=opening, adjusted_high=opening + 1,
                adjusted_low=close - 1,
            ))
        result = compute_opening_gap_filter(rows, OpeningGapFilterConfig(window=30, minimum_gap_up_days=10))
        feature = result[31]
        self.assertEqual(feature.signal_date, rows[31].date)
        self.assertEqual(feature.gap_up_count, 29)
        self.assertTrue(feature.eligible)

    def test_missing_ohlc_is_not_treated_as_a_non_gap(self):
        start = date(2024, 1, 1)
        rows = [PriceRow(start + timedelta(days=i), "X", 100 + i) for i in range(35)]
        result = compute_opening_gap_filter(rows, OpeningGapFilterConfig(window=30, minimum_gap_up_days=10))
        self.assertIsNone(result[-1].gap_up_count)
        self.assertIsNone(result[-1].eligible)


if __name__ == "__main__":
    unittest.main()
