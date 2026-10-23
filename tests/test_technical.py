import unittest

import numpy as np
import pandas as pd

from src.app.lab import technical as tech


def panel(values, code='A'):
    idx = pd.bdate_range('2024-01-01', periods=len(values))
    return pd.DataFrame({code: values}, index=idx, dtype=float)


class TestSignals(unittest.TestCase):
    def test_cross_only_on_crossing_day(self):
        a, b = panel([1, 2, 3, 4]), panel([2, 2.5, 2.5, 2.5])
        self.assertEqual(tech._cross_up(a, b)['A'].tolist(), [False, False, True, False])

    def test_first_in_suppresses_repeats(self):
        sig = panel([0, 1, 1, 1, 0, 0, 0, 0, 0, 1]).astype(bool)
        self.assertEqual(tech._first_in(sig, 5)['A'].tolist(),
                         [False, True, False, False, False, False, False, False, False, True])

    def test_candle_signals(self):
        close = panel([100, 100, 106, 110])
        open_ = panel([100, 100, 100, 112.5])              # 第 3 天長紅、第 4 天跳空 ≥ 2% 但收黑
        vol = panel([1] * 4)
        s = tech.signal_panels(close, open_, vol)
        self.assertEqual(s['long_red']['A'].tolist(), [False, False, True, False])
        self.assertFalse(s['gap_up']['A'].iloc[-1])          # 跳空但收在開盤之下，不算守住

    def test_engulf_needs_high_low(self):
        close, open_ = panel([100, 98, 103]), panel([100, 101, 97])
        hl = panel([105, 105, 105]), panel([95, 95, 95])
        self.assertNotIn('bull_engulf', tech.signal_panels(close, open_, panel([1] * 3)))
        s = tech.signal_panels(close, open_, panel([1] * 3), *hl)
        self.assertEqual(s['bull_engulf']['A'].tolist(), [False, False, True])

    def test_forward_return_enters_next_open(self):
        px, op = panel([10, 11, 12, 13]), panel([10, 10, 12, 13])
        f = tech.forward_returns(px, op, 2)
        self.assertAlmostEqual(f['A'].iloc[0], 12 / 10 - 1)    # d+1 開盤 10 → d+2 收盤 12


class TestEvaluate(unittest.TestCase):
    def test_signal_stock_outperforms(self):
        n = 400
        idx = pd.bdate_range('2020-01-01', periods=n)
        rng = np.random.default_rng(0)
        rets = pd.DataFrame(rng.normal(0, 0.01, (n, 20)), index=idx, columns=[f'S{i}' for i in range(20)])
        sig = pd.DataFrame(False, index=idx, columns=rets.columns)
        sig.iloc[::5, 0] = True
        rets.iloc[1:, 0] += 0.004                       # S0 每天多 0.4%：訊號股應有明顯正超額
        px = (1 + rets).cumprod() * 100
        res = tech.evaluate({'x': sig}, px, px.shift(1).fillna(100), pd.DataFrame(True, index=idx, columns=px.columns),
                            px['S1'], start='2020-01-01', horizons=(5,))
        r = res[0]
        self.assertGreater(r.excess, 0.01)
        self.assertGreater(r.t, 3)
        self.assertEqual(r.events, sig.iloc[:n - 6].values.sum())
        self.assertGreater(r.hit, 0.6)                  # 勝率只算事件本身，不能把非事件當成輸


if __name__ == '__main__':
    unittest.main()
