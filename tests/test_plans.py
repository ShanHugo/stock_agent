import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.app.alerts.portfolio import Holding, Portfolio
from src.app.alerts.plans import (size_position, new_plan, check_invalidation, thesis_review_due,
                                  portfolio_risk, validate_risk, validate_invalidation)
from src.app.alerts.trades import apply_trade, mark_reviewed
from src.app.alerts.journal import summarize, format_summary
from src.app.alerts.runner import run_alerts


def closes(values, end='2026-10-20'):
    return pd.Series(np.asarray(values, dtype=float), index=pd.bdate_range(end=end, periods=len(values)))


def ohlcv(values, end='2026-10-20'):
    c = closes(values, end)
    return pd.DataFrame({'open': c, 'high': c * 1.01, 'low': c * 0.99, 'close': c, 'volume': 1000.0})


SIZE = dict(buy_fee_bps=0, sell_fee_bps=0, sell_tax_bps=0)


class TestSizing(unittest.TestCase):
    def test_risk_based_size(self):
        s = size_position(capital=500_000, max_risk_per_trade=0.01, entry=100, stop=90, **SIZE)
        self.assertEqual(s['max_shares'], 500)          # 5,000 / 10
        self.assertAlmostEqual(s['risk_at_stop'], 5000)
        self.assertEqual(s['limited_by'], 'risk')

    def test_fees_reduce_size(self):
        s = size_position(capital=500_000, max_risk_per_trade=0.01, entry=100, stop=90,
                          buy_fee_bps=14.25, sell_fee_bps=14.25, sell_tax_bps=30)
        per = 10 + 100 * 14.25e-4 + 90 * 44.25e-4
        self.assertAlmostEqual(s['risk_per_share'], per)
        self.assertEqual(s['max_shares'], int(5000 // per))

    def test_position_cap(self):
        s = size_position(capital=500_000, max_risk_per_trade=0.01, entry=100, stop=99,
                          max_position_pct=0.1, **SIZE)
        self.assertEqual((s['max_shares'], s['limited_by']), (500, 'position'))  # 風險允許 5000 股，上限 5 萬元

    def test_stop_must_be_below_entry(self):
        with self.assertRaises(ValueError):
            size_position(capital=1, max_risk_per_trade=0.01, entry=100, stop=100, **SIZE)

    def test_validation(self):
        self.assertTrue(validate_risk(None))
        self.assertEqual(validate_risk({'capital': 1e6, 'max_risk_per_trade': 0.01}), [])
        self.assertTrue(validate_risk({'capital': 1e6, 'max_risk_per_trade': 1.5}))
        self.assertTrue(validate_invalidation([{'type': 'foo'}]))
        with self.assertRaises(ValueError):
            new_plan('2330', entry=100, stop=90, thesis='  ',
                     sizing={'max_shares': 1, 'risk_per_share': 1, 'risk_budget': 1})


def make_plan(**kw):
    s = size_position(capital=500_000, max_risk_per_trade=0.01, entry=100, stop=90, **SIZE)
    return new_plan('2330', entry=100, stop=90, thesis='營收成長', sizing=s, created='2026-09-01', **kw)


class TestTradesWithPlan(unittest.TestCase):
    def test_individual_stock_requires_plan(self):
        with self.assertRaises(ValueError):
            apply_trade({'holdings': []}, 'buy', '2330', 100, 100)
        apply_trade({'holdings': []}, 'buy', '2330', 100, 100, no_plan=True)
        apply_trade({'holdings': []}, 'buy', '0050', 10, 100)   # ETF 不需要
        with self.assertRaises(ValueError):
            apply_trade({'holdings': []}, 'buy', 'AAPL', 1, 200)  # 美股預設視為個股
        apply_trade({'holdings': []}, 'buy', 'VOO', 0.1, 700, etf=True)
        apply_trade({'holdings': [{'symbol': 'VOO', 'shares': 2, 'cost': 650, 'etf': True}]}, 'buy', 'VOO', 0.1, 700)

    def test_buy_attaches_plan_and_warns(self):
        plan = make_plan(target=130, invalidation=[{'type': 'below_ma', 'days': 20}])
        res = apply_trade({'holdings': []}, 'buy', '2330', 600, 101, plans=[plan], trade_date='2026-09-02')
        h = res['portfolio']['holdings'][0]
        self.assertEqual((h['stop_price'], h['target_price']), (90, 130))
        self.assertEqual(h['plan']['initial_risk_per_share'], 11)
        self.assertEqual(res['plans'][0]['status'], 'filled')
        self.assertEqual(plan['status'], 'pending')  # 原資料不變
        self.assertEqual(len(res['warnings']), 2)      # 超過股數上限 + 超過風險預算

    def test_sell_journal(self):
        plan = make_plan(target=130)
        raw = apply_trade({'holdings': []}, 'buy', '2330', 500, 100, plans=[plan],
                          trade_date='2026-09-02')['portfolio']
        j = apply_trade(raw, 'sell', '2330', 500, 85, trade_date='2026-10-01')['journal']
        self.assertEqual((j['exit_type'], j['late_stop'], j['r_multiple']), ('stop', True, -1.5))
        j = apply_trade(raw, 'sell', '2330', 500, 130)['journal']
        self.assertEqual((j['exit_type'], j['r_multiple']), ('target', 3.0))
        j = apply_trade(raw, 'sell', '2330', 200, 110)['journal']
        self.assertEqual(j['exit_type'], 'discretionary')

    def test_mark_reviewed(self):
        raw = apply_trade({'holdings': []}, 'buy', '2330', 500, 100, plans=[make_plan()],
                          trade_date='2026-09-02')['portfolio']
        raw, _ = mark_reviewed(raw, '2330', '2026-10-15')
        self.assertEqual(raw['holdings'][0]['plan']['last_reviewed'], '2026-10-15')


class TestPlanChecks(unittest.TestCase):
    def h(self, invalidation, opened='2026-09-01', cost=100.0):
        return Holding(symbol='2330', shares=500, cost=cost, stop_price=80, entry_date=opened,
                       plan={'thesis': 'x', 'invalidation': invalidation, 'opened': opened, 'last_reviewed': opened})

    def test_invalidation_rules(self):
        c = closes([100] * 19 + [95])
        self.assertTrue(check_invalidation(self.h([{'type': 'below_ma', 'days': 20}]), c, date(2026, 10, 20)))
        self.assertFalse(check_invalidation(self.h([{'type': 'close_below', 'price': 90}]), c, date(2026, 10, 20)))
        self.assertTrue(check_invalidation(self.h([{'type': 'close_below', 'price': 96}]), c, date(2026, 10, 20)))
        ts = [{'type': 'time_stop', 'days': 40, 'min_gain': 0.05}]
        self.assertTrue(check_invalidation(self.h(ts), c, date(2026, 10, 20)))   # 49 天、-5%
        self.assertFalse(check_invalidation(self.h(ts), c, date(2026, 10, 1)))   # 才 30 天

    def test_review_due(self):
        h = self.h([])
        self.assertEqual(thesis_review_due(h, date(2026, 10, 1), 30), 30)
        self.assertIsNone(thesis_review_due(h, date(2026, 9, 20), 30))

    def test_portfolio_risk(self):
        a = Holding(symbol='2330', shares=500, cost=100, stop_price=90)
        b = Holding(symbol='VOO', shares=2, cost=650, etf=True)  # 沒停損 → 不計入曝險；ETF 不受單一持股上限
        r = portfolio_risk([(a, 100, 1.0), (b, 700, 32.0)],
                           {'capital': 100_000, 'max_total_risk': 0.04, 'max_position_pct': 0.4})
        self.assertAlmostEqual(r['open_risk'], 5000)
        self.assertTrue(r['total_risk_exceeded'])
        self.assertEqual([p['symbol'] for p in r['oversized']], ['2330'])  # 50,000 > 40%

    def test_etf_with_stop_not_in_total_risk(self):
        # risk.capital 是個股資金：0050 有停損也不計入總曝險
        etf = Holding(symbol='0050', shares=200, cost=90, stop_loss_pct=-0.2)
        stock = Holding(symbol='2317', shares=10, cost=200, stop_price=190)
        r = portfolio_risk([(etf, 107, 1.0), (stock, 200, 1.0)], {'capital': 12_000, 'max_total_risk': 0.08})
        self.assertAlmostEqual(r['open_risk'], 100)
        self.assertFalse(r['total_risk_exceeded'])


class TestRunnerWithPlans(unittest.TestCase):
    def test_plan_invalid_and_review_and_risk(self):
        h = Holding(symbol='2330', name='台積電', shares=500, cost=100, stop_price=80, entry_date='2026-08-01',
                    plan={'thesis': '營收成長', 'invalidation': [{'type': 'close_below', 'price': 96}],
                          'opened': '2026-08-01', 'last_reviewed': '2026-08-01'})
        pf = Portfolio(holdings=[h], risk={'capital': 100_000, 'max_risk_per_trade': 0.01, 'max_total_risk': 0.05})
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 's.json'
            res = run_alerts(pf, lambda *a: ohlcv([100] * 10 + [95]), today=date(2026, 10, 20),
                             default_tw_fee_bps=14.25, state_path=state)
            msg = res['message']
            self.assertIn('買進理由失效', msg)
            self.assertIn('跌破 96', msg)
            self.assertIn('請重新確認買進理由', msg)   # 80 天未確認
            self.assertIn('部位風險超限', msg)         # (95-80)*500 = 7,500 > 5%
            res = run_alerts(pf, lambda *a: ohlcv([100] * 10 + [95, 95], end='2026-10-21'),
                             today=date(2026, 10, 21), default_tw_fee_bps=14.25, state_path=state)
            self.assertNotIn('部位風險超限', res['message'])        # 已通知過
            self.assertNotIn('請重新確認買進理由', res['message'])  # 7 天內不重複


class TestJournal(unittest.TestCase):
    def test_summary(self):
        def row(d, sym, pnl, r, exit_type, late=False, plan=True):
            return {'date': d, 'symbol': sym, 'pnl': pnl, 'r_multiple': r, 'exit_type': exit_type,
                    'late_stop': late, 'had_plan': plan}
        rows = [
            row('2026-10-01', 'A', 3000, 2.0, 'target'),
            row('2026-10-05', 'B', -1500, -1.0, 'stop'),
            row('2026-10-09', 'C', -2400, -1.6, 'stop', late=True),
            row('2026-10-10', '0050', 100, None, 'discretionary', plan=False),
            row('2026-09-10', 'D', 500, 0.5, 'discretionary'),
        ]
        s = summarize(rows, '2026-10')
        self.assertEqual(s['trades'], 3)
        self.assertAlmostEqual(s['win_rate'], 1 / 3)
        self.assertAlmostEqual(s['payoff_ratio'], 3000 / 1950)
        self.assertEqual((s['late_stops'], s['beyond_1r']), (['C'], ['C']))
        self.assertIn('停損執行偏慢', format_summary(s))
        self.assertEqual(summarize([], None)['trades'], 0)


if __name__ == '__main__':
    unittest.main()
