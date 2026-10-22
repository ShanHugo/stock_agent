import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from src.app.lab import scorecard as sc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import paper_bot as pb  # noqa: E402


def hist_curve(n=1500, seed=0):
    """歷史模擬：每天平均 +0.1%（贏 0050 的 +0.05%），有波動。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2018-06-01', periods=n).strftime('%Y-%m-%d')
    eq = 10_000 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    bench = 10_000 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    return pd.DataFrame({'date': dates, 'equity': eq, 'bench': bench})


def live_view(n, daily, bench_daily=0.0005, start='2026-10-21'):
    dates = pd.bdate_range(start, periods=n).strftime('%Y-%m-%d')
    eq = 12_376 * np.cumprod(np.r_[1, np.full(n - 1, 1 + daily)])
    b = 12_376 * np.cumprod(np.r_[1, np.full(n - 1, 1 + bench_daily)])
    return {'start': start, 'last_date': dates[-1],
            'curve': [{'date': d, 'equity': e, 'bench': x} for d, e, x in zip(dates, eq, b)]}


def sells(k, ret=0.1, date='2026-12-01'):
    return [{'side': 'sell', 'date': date, 'return': ret if i % 2 else -0.08} for i in range(k)]


class TestScorecard(unittest.TestCase):
    def test_accumulating_early(self):
        r = sc.evaluate(live_view(15, 0.004), hist_curve(), sells(3))
        self.assertEqual(r['verdict'], 'accumulating')
        self.assertEqual([c['ok'] for c in r['checks']], [False, False, True, True])
        self.assertEqual(r['checks'][0]['value'], '15／120')
        self.assertIn('history', r)

    def test_pass_when_all_criteria_met(self):
        r = sc.evaluate(live_view(130, 0.001), hist_curve(), sells(24))
        self.assertTrue(all(c['ok'] for c in r['checks']), r['checks'])
        self.assertEqual(r['verdict'], 'pass')
        txt = sc.render(r, '2027-04')
        self.assertIn('達標', txt)
        self.assertIn('✅ 模擬滿 120 個交易日', txt)

    def test_warning_when_far_below_history(self):
        r = sc.evaluate(live_view(60, -0.01), hist_curve(), sells(10))
        self.assertEqual(r['verdict'], 'warning')
        self.assertTrue(r['warnings'])
        self.assertFalse(next(c for c in r['checks'] if c['key'] == 'beat')['ok'])

    def test_window_stats_and_rule_versions(self):
        w = sc.window_stats(hist_curve(300), 21)
        self.assertGreater(len(w), 10)
        self.assertTrue((w['mdd'] <= 0).all())
        self.assertEqual(sc.evaluation_start('2026-10-01'), sc.RULE_HISTORY[-1]['date'])
        self.assertEqual(sc.CRITERIA['min_closed_trades'], 20)

    def test_trades_before_evaluation_start_not_counted(self):
        r = sc.evaluate(live_view(30, 0.001), hist_curve(), sells(5, date='2026-10-01'))
        self.assertEqual(r['closed_trades'], 0)


class TestMonthlyTrigger(unittest.TestCase):
    def test_due_once_per_new_month(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pb, 'SCORE_STATE', Path(d) / 's.json'):
            self.assertIsNone(pb.monthly_scorecard_due('2026-10-30', '2026-10-21'))
            self.assertEqual(pb.monthly_scorecard_due('2026-11-02', '2026-10-21'), '2026-10')
            self.assertIsNone(pb.monthly_scorecard_due('2026-11-03', '2026-10-21'))
            self.assertEqual(pb.monthly_scorecard_due('2026-12-01', '2026-10-21'), '2026-11')
            self.assertEqual(json.loads((Path(d) / 's.json').read_text())['last_month'], '2026-12')


if __name__ == '__main__':
    unittest.main()
