import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook

from kelly_mvp.data import PriceRow, aggregate_prices, daily_prices_to_csv, load_price_workbook, parse_daily_prices


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

    def test_excel_keeps_provider_frequencies_separate(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prices.xlsx"
            workbook = Workbook()
            workbook.remove(workbook.active)
            for frequency, observed in (("daily", date(2026, 8, 28)), ("weekly", date(2026, 8, 29)), ("monthly", date(2026, 8, 31))):
                sheet = workbook.create_sheet(frequency)
                sheet.append(("date", "symbol", "adjusted_close"))
                sheet.append((observed, "X", 100))
            workbook.save(path)
            bundle = load_price_workbook(path)
        self.assertEqual(set(bundle), {"daily", "weekly", "monthly"})
        self.assertEqual(bundle["weekly"][0].date, date(2026, 8, 29))


if __name__ == "__main__":
    unittest.main()
