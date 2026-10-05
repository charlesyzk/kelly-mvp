import unittest
from datetime import date, timedelta

from kelly_mvp.data import PriceRow
from kelly_mvp.module_strategy.ewma_return_position import (
    EWMARankFeature,
    EWMAReturnPositionConfig,
    compute_ewma_rank_features,
    analyze_ewma_state_events,
    mark_state_events,
    state_for_rank,
    _rank,
)


def price_series(count=100, multiplier=1.001):
    return [
        PriceRow(date(2020, 1, 1) + timedelta(days=i), "X", 100 * multiplier**i)
        for i in range(count)
    ]


class EwmaReturnPositionTests(unittest.TestCase):
    def test_causal_features_do_not_change_when_future_prices_change(self):
        config = EWMAReturnPositionConfig(ranking_window=20, initial_variance_window=5)
        original = price_series()
        changed = original[:80] + [PriceRow(row.date, row.symbol, row.adjusted_close * 3) for row in original[80:]]
        left = compute_ewma_rank_features(original, config)
        right = compute_ewma_rank_features(changed, config)
        self.assertEqual(left[:80], right[:80])

    def test_rank_warmup_and_tie_rule(self):
        config = EWMAReturnPositionConfig(ranking_window=20, initial_variance_window=5)
        features = compute_ewma_rank_features(price_series(), config)
        self.assertIsNone(features[20].raw_rank)
        self.assertIsNotNone(features[21].raw_rank)
        self.assertIsNone(features[25].volatility_adjusted_rank)
        self.assertIsNotNone(features[26].volatility_adjusted_rank)
        self.assertAlmostEqual(_rank(2.0, [1.0, 2.0, 2.0]), 0.875)

    def test_state_boundaries_are_mutually_exclusive(self):
        self.assertEqual(state_for_rank(0.05), "extreme_left")
        self.assertEqual(state_for_rank(0.20), "ordinary_left")
        self.assertEqual(state_for_rank(0.80), "middle")
        self.assertEqual(state_for_rank(0.95), "ordinary_right")
        self.assertEqual(state_for_rank(1.0), "extreme_right")
        self.assertIsNone(state_for_rank(0.0))

    def test_cooldown_requires_reentry_and_does_not_reissue_while_state_persists(self):
        start = date(2024, 1, 1)
        ranks = [0.03, 0.04, 0.50, 0.03, 0.50, 0.03, 0.50, 0.03, 0.50]
        features = [EWMARankFeature("X", start + timedelta(days=i), 0.0, 0.1, 0.0, None, rank) for i, rank in enumerate(ranks)]
        events = mark_state_events(features, cooldown_rows=5)
        self.assertEqual([row.retained_event for row in events], [False, False, True, True, False, False, False, False, True])

    def test_event_analysis_separates_pending_and_completed_horizons(self):
        start = date(2024, 1, 1)
        prices = []
        close = 100.0
        returns = [0.002] * 100
        for index in (20, 35, 50, 65, 80, 95):
            returns[index] = 0.15 if index % 2 == 0 else -0.15
        for index in range(101):
            if index:
                close *= 1 + returns[index - 1]
            prices.append(PriceRow(start + timedelta(days=index), "X", close))
        events, outcomes, summaries = analyze_ewma_state_events(
            prices,
            EWMAReturnPositionConfig(ranking_window=10, initial_variance_window=5),
            horizons=(1, 3), cooldown_rows=0,
        )
        retained_dates = {event.date for event in events if event.retained_event}
        self.assertTrue(retained_dates)
        self.assertTrue(all(row.event_date in retained_dates for row in outcomes))
        self.assertTrue(any(not row.complete for row in outcomes))
        self.assertTrue(any(row.completed_count < row.event_count for row in summaries))
        self.assertTrue(all(row.mean_return is None or row.completed_count > 0 for row in summaries))


if __name__ == "__main__":
    unittest.main()
