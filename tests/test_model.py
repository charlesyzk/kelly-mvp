import unittest

from kelly_mvp.config import MODEL_IDS
from kelly_mvp.model import derivative, estimate_moments, solve_all_models, solve_model


class ModelTests(unittest.TestCase):
    def test_all_six_2_0_models_return_three_independent_positions(self):
        sample = [0.03, -0.02, 0.01, 0.015, -0.01] * 24
        decisions = solve_all_models(sample)
        self.assertEqual(tuple(row.model_id for row in decisions), MODEL_IDS)
        for row in decisions:
            self.assertEqual(tuple(name for name, _ in row.positions()), ("RAW", "BOUNDED", "SAFE"))
            self.assertLessEqual(abs(row.bounded_position), 1)
            self.assertTrue(row.safe_position is None or isinstance(row.safe_position, float))

    def test_flat_sample_uses_zero_convention(self):
        for model_id in MODEL_IDS:
            decision = solve_model(model_id, [0.0] * 60)
            self.assertEqual(decision.raw_position, 0)
            self.assertEqual(decision.bounded_position, 0)
            self.assertEqual(decision.safe_position, 0)

    def test_ewma_moments_use_positive_normalized_newest_first_weights(self):
        sample = [0.1, -0.02, 0.03]
        weights = [0.2, 0.3, 0.5]
        estimate = estimate_moments(sample, weights)
        self.assertAlmostEqual(estimate.m1, sum(w * __import__("math").log1p(r) for w, r in zip(weights, sample)))
        decision = solve_model("EWMA_M2_LOG", sample, weights=weights)
        self.assertEqual(decision.model_id, "EWMA_M2_LOG")
        self.assertEqual(decision.safe_domain_type, "LOG_TAYLOR")

    def test_exact_uses_exact_domain_safe_label(self):
        decision = solve_model("EMPIRICAL_EXACT", [2.0, -0.2] * 30)
        self.assertEqual(decision.safe_domain_type, "EXACT_DOMAIN")
        self.assertGreater(decision.bounded_position, -0.5)

    def test_safe_exact_is_not_clipped_to_bounded_interval(self):
        decision = solve_model("EMPIRICAL_EXACT", [0.10, 0.05, -0.01] * 30)
        self.assertIsNotNone(decision.safe_position)
        self.assertGreater(decision.safe_position, 1.0)

    def test_ewma_exact_matches_equal_weight_when_weights_are_equal(self):
        sample = [0.02, -0.01, 0.015, -0.005] * 15
        weights = [1 / len(sample)] * len(sample)
        equal = solve_model("EMPIRICAL_EXACT", sample)
        weighted = solve_model("EWMA_EMPIRICAL_EXACT", sample, weights=weights)
        self.assertAlmostEqual(equal.bounded_position, weighted.bounded_position, places=8)


if __name__ == "__main__":
    unittest.main()
