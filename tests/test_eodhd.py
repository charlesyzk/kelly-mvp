import os
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from kelly_mvp.eodhd import build_eodhd_url, fetch_daily_prices


class EODHDTests(unittest.TestCase):
    def test_url_matches_daily_strategy_input_contract(self):
        url = build_eodhd_url("300308.she", "2024-01-02", "2024-01-05", "private-token")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/eod/300308.SHE")
        self.assertEqual(query["period"], ["d"])
        self.assertEqual(query["order"], ["a"])
        self.assertEqual(query["fmt"], ["json"])
        self.assertEqual(query["from"], ["2024-01-02"])
        self.assertEqual(query["to"], ["2024-01-05"])

    def test_fetch_normalizes_real_response_shape(self):
        payload = [
            {"date": "2024-01-03", "adjusted_close": 10.5, "volume": 123},
            {"date": "2024-01-02", "adjusted_close": 10.0, "volume": 456},
        ]
        rows = fetch_daily_prices(
            "x.us", "2024-01-02", "2024-01-03", api_token="token",
            http_transport=lambda _url, _timeout: payload,
        )
        self.assertEqual([row.date.isoformat() for row in rows], ["2024-01-02", "2024-01-03"])
        self.assertEqual({row.symbol for row in rows}, {"X.US"})
        self.assertEqual(rows[-1].adjusted_close, 10.5)

    def test_token_can_come_from_environment(self):
        captured = {}

        def transport(url, _timeout):
            captured["query"] = parse_qs(urlparse(url).query)
            return [{"date": "2024-01-02", "adjusted_close": 10}]

        with patch.dict(os.environ, {"EODHD_API_TOKEN": "environment-token"}):
            fetch_daily_prices("X.US", "2024-01-02", "2024-01-02", http_transport=transport)
        self.assertEqual(captured["query"]["api_token"], ["environment-token"])

    def test_transport_error_never_leaks_token(self):
        def failed(url, _timeout):
            raise OSError(url)

        with self.assertRaisesRegex(RuntimeError, "EODHD请求失败") as caught:
            fetch_daily_prices(
                "X.US", "2024-01-02", "2024-01-03",
                api_token="do-not-leak", http_transport=failed,
            )
        self.assertNotIn("do-not-leak", str(caught.exception))

    def test_error_object_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "检查Token"):
            fetch_daily_prices(
                "X.US", "2024-01-02", "2024-01-03", api_token="token",
                http_transport=lambda _url, _timeout: {"message": "forbidden"},
            )

    def test_nonpositive_adjusted_close_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "大于零"):
            fetch_daily_prices(
                "X.US", "2024-01-02", "2024-01-03", api_token="token",
                http_transport=lambda _url, _timeout: [
                    {"date": "2024-01-02", "adjusted_close": 0},
                ],
            )


if __name__ == "__main__":
    unittest.main()
