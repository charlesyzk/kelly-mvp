import unittest
from datetime import date, timedelta

from kelly_mvp.data import PriceRow, aggregate_prices, daily_prices_to_csv, parse_daily_prices


class DataTests(unittest.TestCase):
    def test_price_rows_round_trip_through_public_csv_contract(self):
        rows = [PriceRow(date(2026, 8, 24), "X", 100.25)]
        self.assertEqual(parse_daily_prices(daily_prices_to_csv(rows)), rows)

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
