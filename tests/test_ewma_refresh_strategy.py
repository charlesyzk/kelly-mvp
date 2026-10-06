from __future__ import annotations

import math
import unittest
import tempfile
from datetime import date, timedelta
from pathlib import Path

from kelly_mvp.data import PriceRow, parse_daily_prices
from kelly_mvp.module_strategy.ewma_refresh_strategy import (
    Approval,
    Calibration,
    EWMAEvent,
    EWMAFeature,
    EWMARefreshConfig,
    ExitResult,
    FixedPathLabel,
    _build_approvals,
    _account_run,
    _can_buy,
    _can_sell,
    _mark_events,
    _simulate_exit,
    _state,
    compute_ewma_features,
    run_ewma_refresh_backtest,
)
from kelly_mvp.ewma_cli import write_ewma_outputs
from kelly_mvp.web import ewma_refresh_payload


def price_rows(closes: list[float], *, symbol: str = "600000.SHG") -> list[PriceRow]:
    start = date(2020, 1, 1)
    rows = []
    for i, close in enumerate(closes):
        rows.append(PriceRow(start + timedelta(days=i), symbol, close,
                             adjusted_open=close, adjusted_high=close,
                             adjusted_low=close, raw_close=close,
                             raw_open=close, raw_high=close, raw_low=close))
    return rows


class EWMARefreshFeatureTests(unittest.TestCase):
    def test_log_return_seed_and_today_next_day_volatility_have_distinct_timing(self):
        returns = [0.01, -0.02, 0.03, 0.04, -0.01, 0.02, 0.015]
        closes = [100.0]
        for value in returns:
            closes.append(closes[-1] * math.exp(value))
        cfg = EWMARefreshConfig(initial_variance_window=3, ranking_window=2)
        features = compute_ewma_features(price_rows(closes), cfg)
        seed_variance = sum((r - sum(returns[:3]) / 3) ** 2 for r in returns[:3]) / 2
        signal_sigma = math.sqrt(seed_variance)
        self.assertAlmostEqual(features[4].sigma_signal, signal_sigma)
        self.assertAlmostEqual(features[4].standardized_log_return, returns[3] / signal_sigma)
        next_sigma = math.sqrt(0.9 * seed_variance + 0.1 * returns[3] ** 2)
        self.assertAlmostEqual(features[4].sigma_next, next_sigma)
        self.assertNotAlmostEqual(features[4].sigma_signal, features[4].sigma_next)
        self.assertIsNone(features[2].raw_rank)  # current return is not enough history yet
        self.assertIsNotNone(features[5].raw_rank)
        self.assertIsNotNone(features[6].raw_rank)
        self.assertIsNotNone(features[6].standardized_rank)

    def test_state_boundaries_are_exact_and_mutually_exclusive(self):
        self.assertEqual(_state(0.05), "extreme_left")
        self.assertEqual(_state(0.050001), "ordinary_left")
        self.assertEqual(_state(0.20), "ordinary_left")
        self.assertEqual(_state(0.80), "middle")
        self.assertEqual(_state(0.95), "ordinary_right")
        self.assertEqual(_state(1.0), "extreme_right")
        self.assertIsNone(_state(0.0))

    def test_state_event_cooldown_is_six_rows_between_kept_events(self):
        start = date(2024, 1, 1)
        ranks = [0.50, 0.03, 0.50, 0.50, 0.50, 0.50, 0.03, 0.50, 0.03]
        rows = price_rows([100.0] * len(ranks))
        features = tuple(EWMAFeature("600000.SHG", start + timedelta(days=i), 0.0, 0.01,
                                     0.0, 0.01, 0.5, rank) for i, rank in enumerate(ranks))
        events = _mark_events(features, rows, EWMARefreshConfig(ranking_window=2, initial_variance_window=2))
        self.assertEqual([event.row_index for event in events], [1, 2, 8])
        self.assertEqual(events[0].state, "extreme_left")
        self.assertEqual(events[1].state, "middle")
        self.assertEqual(events[2].state, "extreme_left")


class EWMARefreshExecutionTests(unittest.TestCase):
    def test_unverified_single_price_and_verified_limits_are_handled_conservatively(self):
        rows = price_rows([100, 110, 90])
        self.assertEqual(_can_buy(rows[1]), (False, "signal_cancelled_single_price_uncertain"))
        self.assertFalse(_can_sell(rows[2]))
        verified_up = PriceRow(rows[1].date, rows[1].symbol, 110, 110, 110, 110,
                               adjusted_limit_up=110, adjusted_limit_down=90,
                               price_limits_verified=True)
        verified_flat = PriceRow(rows[1].date, rows[1].symbol, 100, 100, 100, 100,
                                 adjusted_limit_up=110, adjusted_limit_down=90,
                                 price_limits_verified=True)
        self.assertFalse(_can_buy(verified_up)[0])
        self.assertTrue(_can_buy(verified_flat)[0])

    def test_csv_parser_preserves_only_explicitly_verified_adjusted_limits(self):
        csv_text = (
            "date,symbol,adjusted_close,open,high,low,close,adjusted_limit_up,adjusted_limit_down,price_limits_verified\n"
            "2024-01-02,600000.SHG,100,100,100,100,100,110,90,true\n"
        )
        row = parse_daily_prices(csv_text)[0]
        self.assertEqual(row.adjusted_limit_up, 110)
        self.assertEqual(row.adjusted_limit_down, 90)
        self.assertTrue(row.price_limits_verified)

    def test_csv_parser_rejects_invalid_volume(self):
        csv_text = (
            "date,symbol,adjusted_close,open,high,low,close,volume\n"
            "2024-01-02,600000.SHG,100,100,101,99,100,-1\n"
        )
        with self.assertRaisesRegex(ValueError, "volume must be finite and non-negative"):
            parse_daily_prices(csv_text)

    def test_t_plus_one_and_double_touch_stop_priority(self):
        rows = price_rows([100, 100, 100, 100, 100, 100, 100])
        # The entry bar reaches the take-profit line, but the new position is T+1 locked.
        rows[1] = PriceRow(rows[1].date, rows[1].symbol, 100, 100, 110, 99, raw_open=100,
                           raw_high=110, raw_low=99, raw_close=100)
        rows[2] = PriceRow(rows[2].date, rows[2].symbol, 100, 100, 102, 98, raw_open=100,
                           raw_high=102, raw_low=98, raw_close=100)
        features = tuple(EWMAFeature(row.symbol, row.date, 0.0, 0.01, 0.0, 0.01, 0.5, 0.5) for row in rows)
        event = EWMAEvent(rows[0].symbol, rows[0].date, 0, "middle", 1, 0.5, 0.5, 0.01)
        calibration = Calibration("middle", 0, 1.0, 1.0, 60, 30, -1, True)
        result = _simulate_exit(rows, event, calibration, "A", features,
                                EWMARefreshConfig(ranking_window=2, initial_variance_window=2))
        self.assertTrue(result.complete)
        self.assertEqual(result.exit_index, 2)
        self.assertEqual(result.exit_reason, "stop_double_touch")
        self.assertTrue(result.ambiguous_double_touch)
        self.assertAlmostEqual(result.exit_price, 100 * math.exp(-0.01))

    def test_account_posts_cost_once_and_uses_a_separate_equity_ledger(self):
        dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(4)]
        rows = [
            PriceRow(dates[0], "600000.SHG", 100, 100, 100.5, 99.5),
            PriceRow(dates[1], "600000.SHG", 100, 100, 100.5, 99.5),
            PriceRow(dates[2], "600000.SHG", 102, 102, 102.5, 101.5),
            PriceRow(dates[3], "600000.SHG", 102, 102, 102.5, 101.5),
        ]
        features = tuple(EWMAFeature("600000.SHG", day, 0.0, 0.01, 0.0, 0.01,
                                     0.5, 0.5) for day in dates)
        event = EWMAEvent("600000.SHG", dates[0], 0, "middle", 1, 0.5, 0.5, 0.01)
        calibration = Calibration("middle", 0, 3, 1, 60, 30, -1, True)
        planned = ExitResult("600000.SHG", dates[0], "middle", "A", dates[1], 1, 100,
                             dates[2], 2, 102, "take_profit_open", 1, 3, 1, 0.01,
                             0.02, 0.018, 0.002, True)
        approval = Approval(
            0, dates[0], -1, "initial", "A", "middle", 100, 100, 5,
            0.02, 0.01, 0.01, 0.01, -0.05, True, False, False, False, None, True,
        )
        signals, trades, ledger, summary = _account_run(
            rows, features, (event,), (calibration,), (planned,), (approval,),
            0, "A", True, EWMARefreshConfig(ranking_window=2, initial_variance_window=2),
        )
        self.assertEqual(trades[0].status, "completed")
        self.assertAlmostEqual(trades[0].cost, 0.0002)
        self.assertAlmostEqual(trades[0].net_return, 0.018)
        self.assertAlmostEqual(ledger[-1].equity, 1.0018)
        self.assertEqual(summary["completed_trades"], 1)
        self.assertEqual(signals[0].decision, "buy_pending")

    def test_end_of_data_keeps_open_position_unfinished(self):
        rows = [PriceRow(row.date, row.symbol, 100.0, 100.0, 100.5, 99.5,
                         raw_open=100.0, raw_high=100.5, raw_low=99.5, raw_close=100.0)
                for row in price_rows([100, 100, 100, 100])]
        features = tuple(EWMAFeature(row.symbol, row.date, 0.0, 0.01, 0.0, 0.01, 0.5, 0.5) for row in rows)
        event = EWMAEvent(rows[0].symbol, rows[0].date, 0, "middle", 1, 0.5, 0.5, 0.01)
        calibration = Calibration("middle", 0, 4.0, 4.0, 60, 30, -1, True)
        result = _simulate_exit(rows, event, calibration, "A", features,
                                EWMARefreshConfig(ranking_window=2, initial_variance_window=2))
        self.assertFalse(result.complete)
        self.assertEqual(result.exit_reason, "unfinished_mark_to_market")
        self.assertIsNone(result.net_return)

    def test_zero_volume_non_suspension_updates_trailing_line_but_cannot_fill(self):
        rows = price_rows([100, 100, 105, 104])
        rows[1] = PriceRow(rows[1].date, rows[1].symbol, 100, 100, 101, 99.5, volume=1000)
        rows[2] = PriceRow(rows[2].date, rows[2].symbol, 105, 100, 105, 100, volume=0)
        rows[3] = PriceRow(rows[3].date, rows[3].symbol, 104, 104, 105, 100.5, volume=1000)
        features = tuple(EWMAFeature(row.symbol, row.date, 0.0, 0.01, 0.0,
                                     0.01 if i != 2 else 0.02, 0.5, 0.5)
                         for i, row in enumerate(rows))
        event = EWMAEvent(rows[0].symbol, rows[0].date, 0, "middle", 1, 0.5, 0.5, 0.01)
        calibration = Calibration("middle", 0, 10.0, 2.0, 60, 30, -1, True)
        result = _simulate_exit(rows, event, calibration, "C", features,
                                EWMARefreshConfig(ranking_window=2, initial_variance_window=2))
        self.assertEqual(result.exit_reason, "stop_intraday")
        self.assertEqual(result.exit_index, 3)
        self.assertGreater(result.audit[1]["lower"], result.audit[0]["lower"])
        self.assertEqual(result.audit[0]["action"], "hold")
        self.assertTrue(_can_sell(rows[3]))
        self.assertFalse(_can_sell(rows[2]))

    def test_synthetic_runner_returns_separate_event_and_six_account_outputs(self):
        values = [100.0]
        for i in range(1, 150):
            values.append(values[-1] * math.exp(0.008 * math.sin(i * 1.7) + 0.001 * math.cos(i)))
        cfg = EWMARefreshConfig(initial_variance_window=4, ranking_window=12,
                                block_length=8, sensitivity_block_length=12,
                                bootstrap_repetitions=8)
        result = run_ewma_refresh_backtest(price_rows(values), cfg)
        self.assertEqual(result.strategy_id, "ewma_return_position_refresh_v1")
        self.assertEqual(len([item for item in result.summaries if "account" in item and item["account"] != "PASSIVE_BUY_HOLD"]), 6)
        self.assertEqual(sum(item.get("statistic_type") == "overlapping_event_conditional_not_account_return"
                             for item in result.summaries), 15)
        self.assertEqual(len(result.horizon_summaries), 30)
        self.assertEqual(len(result.path_quantiles), 5 * 21)
        self.assertEqual(len(result.ledger), 7 * (len(values) - result.test_start_index))
        self.assertTrue(all(row.symbol == "600000.SHG" for row in result.features))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ewma-run"
            paths = write_ewma_outputs(result, output)
            self.assertIn("approvals.csv", {path.name for path in paths})
            self.assertIn("path_quantiles_21d.csv", {path.name for path in paths})
            self.assertTrue((output / "run.json").is_file())

    def test_web_entry_uses_ohlc_csv_and_keeps_strategy_independent(self):
        values = [100.0]
        for i in range(1, 90):
            values.append(values[-1] * math.exp(0.006 * math.sin(i * 1.3)))
        start = date(2023, 1, 1)
        text = "date,symbol,adjusted_close,open,high,low,close\n" + "\n".join(
            f"{(start + timedelta(days=i)).isoformat()},600000.SHG,{value},{value},{value},{value},{value}"
            for i, value in enumerate(values))
        result = ewma_refresh_payload({"csv_text": text})
        self.assertEqual(result["strategy_id"], "ewma_return_position_refresh_v1")
        self.assertIn("ledger", result)

    def test_exploration_permission_is_not_strict_statistical_validation(self):
        rows = price_rows([100.0] * 106)
        labels = tuple(FixedPathLabel(
            "600000.SHG", rows[i].date, i, "middle", rows[i + 5].date,
            0.02, 0.018, 0.03, -0.01, 3.0, 1.0, True,
        ) for i in range(100))
        exits = tuple(ExitResult(
            "600000.SHG", rows[i].date, "middle", policy, rows[i + 1].date, i + 1, 100.0,
            rows[i + 2].date, i + 2, 102.0, "take_profit_intraday", 1, 3.0, 1.0, 0.01,
            0.02, 0.018, 0.002, True,
        ) for i in range(100) for policy in ("A", "B", "C"))
        increments = {("middle", policy): {i: 0.01 for i in range(100)} for policy in ("A", "B", "C")}
        cfg = EWMARefreshConfig(ranking_window=2, initial_variance_window=2,
                                block_length=1, sensitivity_block_length=1,
                                bootstrap_repetitions=16)
        approvals = _build_approvals(rows, (), labels, exits, increments, 0, 105, cfg)
        middle_a = next(item for item in approvals if item.state == "middle" and item.policy == "A")
        self.assertTrue(middle_a.exploration_allowed)
        self.assertFalse(middle_a.statistically_validated)
        self.assertFalse(middle_a.evidence_sufficient)


if __name__ == "__main__":
    unittest.main()
