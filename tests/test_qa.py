import unittest

import numpy as np
import pandas as pd

from src.app.data.qa import run_quality_checks


def make_ohlcv(closes, start='2024-01-01'):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({'open': closes, 'high': closes * 1.01, 'low': closes * 0.99,
                         'close': closes, 'volume': 1000.0}, index=idx)


class TestDataQA(unittest.TestCase):
    def test_clean_data_passes(self):
        qa = run_quality_checks(make_ohlcv(np.linspace(100, 130, 40)))
        self.assertEqual(qa['status'], 'PASS')

    def test_missing_column_fails(self):
        qa = run_quality_checks(make_ohlcv(range(100, 140)).drop(columns='volume'))
        self.assertEqual(qa['status'], 'FAIL')

    def test_gap_and_zero_volume_warn(self):
        df = make_ohlcv(np.r_[np.full(20, 100.0), np.full(20, 130.0)])
        df.iloc[:15, df.columns.get_loc('volume')] = 0
        qa = run_quality_checks(df)
        names = {c['name']: c['status'] for c in qa['checks']}
        self.assertEqual(names['price_gap'], 'WARN')
        self.assertEqual(names['zero_volume'], 'WARN')
        self.assertEqual(qa['status'], 'WARN')

    def test_non_positive_price_fails(self):
        df = make_ohlcv(np.linspace(100, 130, 40))
        df.iloc[5, df.columns.get_loc('close')] = 0
        self.assertEqual(run_quality_checks(df)['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
