import unittest

from kelly_mvp.demo import generate_demo_csv
from kelly_mvp.web import STATIC_DIR, calculate_payload


class WebTests(unittest.TestCase):
    def test_static_interface_contains_run_and_method_sections(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("开始计算", html)
        self.assertIn("计算逻辑", html)
        self.assertIn("数据只在本机计算", html)

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
