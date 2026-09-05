import unittest

from kelly_mvp.model import (
    choose_exact_position,
    choose_position,
    derivative,
    empirical_objective,
    raw_moments,
)


class ModelTests(unittest.TestCase):
    def test_flat_sample_chooses_zero_in_both_solvers(self):
        decision = choose_position([0.0] * 60)
        self.assertEqual(decision.raw_kelly, 0)
        self.assertEqual(decision.full_kelly, 0)
        self.assertEqual(decision.exact_full_kelly, 0)
        self.assertEqual(decision.position, 0)

    def test_exact_empirical_kelly_beats_other_bounded_candidates(self):
        sample = [0.08, 0.04, -0.03, 0.01, -0.02] * 12
        exact = choose_exact_position(sample)
        optimum = empirical_objective(exact, sample)
        self.assertGreaterEqual(optimum, empirical_objective(-1, sample))
        self.assertGreaterEqual(optimum, empirical_objective(0, sample))
        self.assertGreaterEqual(optimum, empirical_objective(1, sample))

    def test_exact_empirical_solver_respects_wealth_domain(self):
        sample = [2.0, -0.2] * 30
        exact = choose_exact_position(sample)
        self.assertGreater(exact, -0.5)
        self.assertGreater(empirical_objective(exact, sample), float("-inf"))

    def test_positive_constant_returns_choose_positive_position(self):
        decision = choose_position([0.01] * 60)
        self.assertGreater(decision.full_kelly, 0)
        self.assertGreaterEqual(decision.raw_kelly, decision.full_kelly)
        self.assertEqual(decision.position, 0.5)
        self.assertGreaterEqual(decision.exact_objective_loss, 0)

    def test_negative_constant_returns_choose_negative_position(self):
        decision = choose_position([-0.01] * 60)
        self.assertLess(decision.full_kelly, 0)
        self.assertEqual(decision.position, -0.5)

    def test_stationary_solution_has_small_derivative_when_interior(self):
        moments = raw_moments([0.02, -0.01, 0.015, -0.005] * 15)
        decision = choose_position([0.02, -0.01, 0.015, -0.005] * 15)
        if -1 < decision.full_kelly < 1:
            self.assertAlmostEqual(derivative(decision.full_kelly, moments), 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
