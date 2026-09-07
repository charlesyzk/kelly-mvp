import unittest

from kelly_mvp.config import MODEL_IDS
from kelly_mvp.model import derivative, estimate_moments, solve_all_models, solve_model


class ModelTests(unittest.TestCase):
    def test_all_six_models_return_three_independent_positions(self):
        sample = [0.03, -0.02, 0.01, 0.015, -0.01] * 24
        decisions = solve_all_models(sample, kappa=0.8)
        self.assertEqual(tuple(row.model_id for row in decisions), MODEL_IDS)
        for row in decisions:
            self.assertEqual(tuple(name for name, _ in row.positions()), ("RAW", "BOUNDED", "SAFE"))
            self.assertLessEqual(abs(row.bounded_position), 1)
            self.assertLessEqual(abs(row.safe_position), 1)

    def test_flat_sample_uses_zero_convention(self):
        for model_id in MODEL_IDS:
            decision = solve_model(model_id, [0.0] * 60)
            self.assertEqual(decision.raw_position, 0)
            self.assertEqual(decision.bounded_position, 0)
            self.assertEqual(decision.safe_position, 0)

    def test_m4_simple_safe_position_satisfies_series_radius(self):
        sample = [0.8, -0.2, 0.05, -0.03] * 20
        decision = solve_model("M4_SIMPLE", sample, kappa=0.5)
        self.assertLessEqual(max(abs(decision.safe_position * value) for value in sample), 0.5 + 1e-10)
        self.assertEqual(decision.safe_domain_type, "SIMPLE_TAYLOR")

    def test_exact_uses_exact_domain_safe_label(self):
        decision = solve_model("EMPIRICAL_EXACT", [2.0, -0.2] * 30)
        self.assertEqual(decision.safe_domain_type, "EXACT_DOMAIN")
        self.assertGreater(decision.bounded_position, -0.5)

    def test_m4_simple_interior_root_has_small_derivative(self):
        sample = [0.02, -0.01, 0.015, -0.005] * 15
        decision = solve_model("M4_SIMPLE", sample)
        if -1 < decision.bounded_position < 1:
            self.assertAlmostEqual(derivative(decision.bounded_position, estimate_moments(sample)), 0, places=8)


if __name__ == "__main__":
    unittest.main()
