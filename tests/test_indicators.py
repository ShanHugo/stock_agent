import unittest
import pandas as pd
import numpy as np
from src.app.features.indicators import sma, rsi, volatility, zscore, momentum_signal, mean_reversion_signal

class TestIndicators(unittest.TestCase):
    def setUp(self):
        self.series = pd.Series(np.linspace(100, 110, 50))

    def test_sma(self):
        out = sma(self.series, 5)
        self.assertEqual(len(out), len(self.series))

    def test_rsi(self):
        out = rsi(self.series, 14)
        self.assertEqual(len(out), len(self.series))

    def test_volatility(self):
        out = volatility(self.series, 10)
        self.assertEqual(len(out), len(self.series))

    def test_zscore(self):
        out = zscore(self.series, 10)
        self.assertEqual(len(out), len(self.series))

    def test_momentum_signal(self):
        out = momentum_signal(self.series, 5)
        self.assertIn(out.dropna().unique()[0], [-1,0,1])

    def test_mean_reversion_signal(self):
        out = mean_reversion_signal(self.series, 5)
        self.assertIn(out.dropna().unique()[0], [-1,0,1])

if __name__ == '__main__':
    unittest.main()
