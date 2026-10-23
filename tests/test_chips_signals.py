import unittest

import numpy as np
import pandas as pd

from src.app.lab import chips_signals as cs


def panel(values):
    return pd.DataFrame({'A': values}, index=pd.bdate_range('2024-01-01', periods=len(values)), dtype=float)


class TestChipSignals(unittest.TestCase):
    def setUp(self):
        self.n = 90
        self.px = panel(np.full(self.n, 100.0))
        self.vol = panel(np.full(self.n, 1000.0))          # 60 日均量 1000 股

    def chips(self, foreign=None, trust=None, margin=None):
        z = np.zeros(self.n)
        return {'foreign': panel(foreign if foreign is not None else z),
                'trust': panel(trust if trust is not None else z),
                'margin': panel(margin if margin is not None else np.full(self.n, 10.0))}

    def test_streak_counts(self):
        p = panel([1, 1, 0, 1, 1, 1]).astype(bool)
        self.assertEqual(cs._streak_np(p)['A'].tolist(), [1, 2, 0, 1, 2, 3])

    def test_trust_start_needs_quiet_20_days(self):
        t = np.zeros(self.n)
        t[70] = 50                         # 前 20 天都沒買 → 開始買
        t[80] = 50                         # 前 20 天內有買過 → 不算
        s = cs.signal_panels(self.px, self.vol, self.chips(trust=t))['trust_start']['A']
        self.assertEqual(list(np.flatnonzero(s.to_numpy())), [70])

    def test_foreign_turn_after_five_day_streak(self):
        f = np.zeros(self.n)
        f[70:75] = 100                     # 連 5 天買
        f[75] = -100                       # 轉賣
        s = cs.signal_panels(self.px, self.vol, self.chips(foreign=f))
        self.assertEqual(list(np.flatnonzero(s['foreign_turn']['A'].to_numpy())), [75])
        self.assertEqual(list(np.flatnonzero(s['foreign_streak5']['A'].to_numpy())), [74])

    def test_big_buy_relative_to_volume(self):
        f = np.zeros(self.n)
        f[70], f[71] = 250, 150            # 均量 25% 算、15% 不算
        s = cs.signal_panels(self.px, self.vol, self.chips(foreign=f))['foreign_big_buy']['A']
        self.assertTrue(s.iloc[70])
        self.assertFalse(s.iloc[71])

    def test_margin_chase_needs_price_up(self):
        m = np.full(self.n, 10.0)
        m[75:] = 11.0                      # 融資 +1 張 = 1000 股 = 均量 100%
        px = self.px.copy()
        px.iloc[75:] = 106.0               # 5 天漲 6%
        s = cs.signal_panels(px, self.vol, self.chips(margin=m))['margin_chase']['A']
        self.assertTrue(s.iloc[75])
        s2 = cs.signal_panels(self.px, self.vol, self.chips(margin=m))['margin_chase']['A']
        self.assertFalse(s2.any())         # 價格沒漲就不算追價

    def test_missing_chips_no_signal(self):
        f = np.full(self.n, np.nan)
        s = cs.signal_panels(self.px, self.vol, self.chips(foreign=f, trust=np.full(self.n, 500.0)))
        self.assertFalse(s['trust_big']['A'].any())


if __name__ == '__main__':
    unittest.main()
