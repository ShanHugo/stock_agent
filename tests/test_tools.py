import unittest
import numpy as np
from src.app.agents.tools import ping, calc_sharpe, mean


class TestTools(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(ping(), 'pong')

    def test_calc_sharpe(self):
        rets = [0.01, 0.02, -0.01, 0.03]
        val = calc_sharpe(rets)
        self.assertIsInstance(val, float)

    def test_mean(self):
        self.assertAlmostEqual(mean([1,2,3]), 2.0)


if __name__ == '__main__':
    unittest.main()
