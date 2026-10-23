import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.app.lab import forward as fw
from src.app.lab.data import Dataset


def make_ds(prices, rev):
    return Dataset(prices=prices, revenue_yoy=rev, pe=pd.DataFrame(), eps=pd.DataFrame())


class TestPick(unittest.TestCase):
    def setUp(self):
        idx = pd.bdate_range('2026-01-01', periods=200)
        self.d = idx[-1]
        self.px = pd.DataFrame({
            'UP': np.linspace(50, 100, 200),       # 在均線上
            'DOWN': np.linspace(100, 50, 200),     # 在均線下
            'BIG': np.linspace(50, 100, 200),
            'NEG': np.linspace(50, 100, 200),      # 營收衰退
            '0050': 100.0,
        }, index=idx)
        # 最後一天 2026-10-07：只有 8 月（9/11 起）以前的營收已公布
        self.rev = pd.DataFrame({'UP': [10.0, 20.0, 999.0], 'DOWN': [50.0, 50.0, 50.0], 'BIG': [5.0, 80.0, 1.0],
                                 'NEG': [-5.0, 30.0, 30.0]}, index=pd.period_range('2026-07', periods=3, freq='M'))

    def test_rules_and_ranking(self):
        self.assertEqual(self.d, pd.Timestamp('2026-10-07'))
        df = fw.pick(make_ds(self.px, self.rev), ['UP', 'DOWN', 'BIG', 'NEG'], self.d)
        self.assertEqual(list(df['code']), ['BIG', 'UP'])          # 依 8 月年增排序；9 月尚未公布不可用
        self.assertEqual(df.iloc[0]['rev_yoy'], 80.0)
        self.assertEqual(len(fw.pick(make_ds(self.px, self.rev), ['UP', 'DOWN', 'BIG', 'NEG'], self.d, n=1)), 1)

    def test_not_enough_history(self):
        self.assertTrue(fw.pick(make_ds(self.px.iloc[:50], self.rev), ['UP'], self.px.index[49]).empty)

    def test_new_listing_needs_full_ma_window(self):
        px = self.px.copy()
        px.loc[px.index[:150], 'BIG'] = np.nan          # 只有 50 天價格的新上市股
        df = fw.pick(make_ds(px, self.rev), ['UP', 'BIG'], self.d)
        self.assertEqual(list(df['code']), ['UP'])


class TestEvaluate(unittest.TestCase):
    def test_returns_costs_pending_and_delisting(self):
        idx = pd.bdate_range('2026-01-01', periods=30)
        px = pd.DataFrame({'A': 100.0, 'B': 100.0, '0050': 100.0}, index=idx)
        px.loc[idx[5]:, 'A'] = 110.0                       # +10%
        px.loc[idx[3]:, 'B'] = np.nan                      # 第 3 天後下市，以最後價格計（0%）
        recs = [{'date': idx[0].strftime('%Y-%m-%d'), 'rule': 'r', 'picks': [{'code': 'A'}, {'code': 'B'}]},
                {'date': idx[20].strftime('%Y-%m-%d'), 'rule': 'r', 'picks': [{'code': 'A'}]}]
        ev = fw.evaluate(recs, px).set_index(['date', 'horizon'])
        first = ev.loc[(recs[0]['date'], 5)]
        self.assertAlmostEqual(first['ret'], 0.05 - fw.ROUND_TRIP)
        self.assertAlmostEqual(first['excess'], 0.05 - fw.ROUND_TRIP)
        self.assertTrue(np.isnan(ev.loc[(recs[1]['date'], 20), 'excess']))     # 尚未滿 20 天
        s = {x['horizon']: x for x in fw.summarize(ev.reset_index())}
        self.assertEqual(s[5]['matured'], 2)
        self.assertEqual(s[20]['pending'], 1)

    def test_records_are_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'r.jsonl'
            rec = {'date': '2026-10-20', 'rule': 'r', 'picks': [{'code': 'A'}]}
            self.assertTrue(fw.append_record(path, rec))
            self.assertFalse(fw.append_record(path, {**rec, 'picks': [{'code': 'B'}]}))
            self.assertEqual(fw.load_records(path)[0]['picks'], [{'code': 'A'}])


if __name__ == '__main__':
    unittest.main()
