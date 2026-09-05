import unittest
from datetime import date, timedelta

from kelly_mvp.data import PriceRow, aggregate_prices


class DataTests(unittest.TestCase):
    def test_final_partial_week_is_excluded(self):
        monday = date(2026, 8, 24)
        rows = [PriceRow(monday + timedelta(days=i), "X", 100 + i) for i in range(4)]
        self.assertEqual(aggregate_prices(rows, "weekly"), [])

    def test_prior_week_is_complete_when_next_week_exists(self):
        rows = [
            PriceRow(date(2026, 8, 28), "X", 100),
            PriceRow(date(2026, 8, 31), "X", 101),
        ]
        weekly = aggregate_prices(rows, "weekly")
        self.assertEqual([row.date for row in weekly], [date(2026, 8, 28)])


if __name__ == "__main__":
    unittest.main()
