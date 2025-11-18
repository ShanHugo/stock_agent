# 單元測試：engine.py
import unittest
import pandas as pd
from src.app.backtest.engine import backtest_engine

class TestBacktestEngine(unittest.TestCase):
    def test_backtest(self):
        df = pd.DataFrame({
            'close': [100, 102, 101, 103, 104]
        }, index=pd.date_range('2024-01-01', periods=5))
        pos = pd.Series([1, 1, 0, -1, -1], index=df.index)
        result = backtest_engine(df, pos)
        self.assertIn('ret', result.columns)
        self.assertIn('equity', result.columns)

if __name__ == "__main__":
    unittest.main()
