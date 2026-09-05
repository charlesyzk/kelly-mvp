import unittest

from kelly_mvp.model import choose_position, derivative, raw_moments


class ModelTests(unittest.TestCase):
    def test_positive_constant_returns_choose_positive_position(self):
        decision = choose_position([0.01] * 60)
        self.assertGreater(decision.full_kelly, 0)
        self.assertEqual(decision.position, 0.5)

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
