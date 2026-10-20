import unittest

import numpy as np
import pandas as pd

from src.app.lab.data import Dataset
from src.app.lab.engine import run_backtest, metrics, buy_and_hold, FEE, TAX, SLIPPAGE
from src.app.lab.rules import trend_following, revenue_momentum, value_growth
from src.app.lab.universe import parse_moneydj_holdings, name_to_code_map
from src.app.lab.walkforward import evaluate_family


def make_ds(prices: pd.DataFrame, revenue=None, pe=None, eps=None) -> Dataset:
    return Dataset(prices=prices,
                   revenue_yoy=revenue if revenue is not None else pd.DataFrame(),
                   pe=pe if pe is not None else pd.DataFrame(),
                   eps=eps if eps is not None else pd.DataFrame())


class TestPublicationLag(unittest.TestCase):
    def test_revenue_and_eps_availability(self):
        self.assertEqual(Dataset.revenue_available(pd.Period('2026-09', 'M')), pd.Timestamp('2026-10-11'))
        self.assertEqual(Dataset.eps_available(pd.Period('2026Q1', 'Q')), pd.Timestamp('2026-05-16'))
        self.assertEqual(Dataset.eps_available(pd.Period('2025Q4', 'Q')), pd.Timestamp('2026-04-01'))

    def test_revenue_asof_never_sees_unpublished_month(self):
        rev = pd.DataFrame({'A': [1.0, 2.0, 3.0]}, index=pd.period_range('2026-07', periods=3, freq='M'))
        ds = make_ds(pd.DataFrame(), revenue=rev)
        self.assertEqual(list(ds.revenue_asof(pd.Timestamp('2026-09-30'), 5).index.astype(str)), ['2026-07', '2026-08'])
        self.assertEqual(len(ds.revenue_asof(pd.Timestamp('2026-10-11'), 5)), 3)


class TestEngine(unittest.TestCase):
    def setUp(self):
        idx = pd.bdate_range('2024-01-01', '2024-03-29')
        self.px = pd.DataFrame({'A': np.linspace(100, 130, len(idx)), 'B': 100.0, '0050': 100.0}, index=idx)

    def test_costs_on_entry_and_exit(self):
        res = run_backtest(self.px, lambda d: ['A'] if d.month == 1 else [], self.px.index[0], self.px.index[-1])
        jan, feb = res.costs.index[0], res.costs.index[1]
        self.assertAlmostEqual(res.costs[jan], FEE + SLIPPAGE)              # 買進 100%
        self.assertAlmostEqual(res.costs[feb], FEE + TAX + SLIPPAGE)        # 全部賣出
        self.assertEqual(res.returns.loc[:jan].iloc[:-1].abs().sum(), 0.0)  # 第一次調整前空手

    def test_no_lookahead_return_starts_next_day(self):
        res = run_backtest(self.px, lambda d: ['A'], self.px.index[0], self.px.index[-1])
        first = res.costs.index[0]
        nxt = self.px.index[self.px.index.get_loc(first) + 1]
        expected = self.px.at[nxt, 'A'] / self.px.at[first, 'A'] - 1
        self.assertAlmostEqual(res.returns[nxt], expected)

    def test_weight_drift_and_rebalance(self):
        res = run_backtest(self.px, lambda d: ['A', 'B'], self.px.index[0], self.px.index[-1])
        second = res.turnover.index[1]
        self.assertGreater(res.turnover[second], 0)     # A 漲多了，再平衡要買回 B

    def test_metrics(self):
        r = pd.Series([0.01, -0.02, 0.03])
        m = metrics(r)
        self.assertAlmostEqual(m['total_return'], 1.01 * 0.98 * 1.03 - 1)
        self.assertLess(m['max_drawdown'], 0)
        self.assertEqual(len(buy_and_hold(self.px['A'], self.px.index[0], self.px.index[-1])), len(self.px))


class TestRules(unittest.TestCase):
    def test_trend(self):
        idx = pd.bdate_range('2024-01-01', periods=30)
        px = pd.DataFrame({'UP': np.arange(30.0) + 100, 'DOWN': 130 - np.arange(30.0)}, index=idx)
        self.assertEqual(trend_following(make_ds(px), ['UP', 'DOWN'], ma=10)(idx[-1]), ['UP'])

    def test_revenue_momentum_uses_published_months_only(self):
        idx = pd.bdate_range('2026-08-01', '2026-10-30')
        px = pd.DataFrame({'A': np.linspace(100, 120, len(idx))}, index=idx)
        rev = pd.DataFrame({'A': [5.0, 5.0, -1.0]}, index=pd.period_range('2026-07', periods=3, freq='M'))
        sig = revenue_momentum(make_ds(px, revenue=rev), ['A'], months=2, ma=5)
        self.assertEqual(sig(pd.Timestamp('2026-10-09')), ['A'])   # 9 月營收還沒公布，看 7、8 月
        self.assertEqual(sig(pd.Timestamp('2026-10-30')), [])      # 9 月年減已公布

    def test_value_growth(self):
        months = pd.date_range('2023-01-31', periods=24, freq='ME')
        pe = pd.DataFrame({'CHEAP': list(np.linspace(20, 30, 23)) + [15.0],
                           'DEAR': list(np.linspace(10, 20, 23)) + [25.0]}, index=months)
        # 2024Q3（已公布）1.5 > 2023Q3 的 1；2024Q4 刻意設很差，若偷看未來就會被排除
        eps = pd.DataFrame({'CHEAP': [1.0, 1, 1, 1, 1, 1, 1.5, 0.1], 'DEAR': [1.0, 1, 1, 1, 1, 1, 1.5, 0.1]},
                           index=pd.period_range('2023Q1', periods=8, freq='Q'))
        sig = value_growth(make_ds(pd.DataFrame(), pe=pe, eps=eps), ['CHEAP', 'DEAR'], pe_pctile=0.3)
        self.assertEqual(sig(pd.Timestamp('2024-12-31')), ['CHEAP'])  # 2024Q4 EPS 在 2025/4/1 才公布，用 2024Q3


class TestUniverse(unittest.TestCase):
    def test_parse_holdings(self):
        # 與實際頁面去標籤後的格式相同：|名稱| |持股| |比例|
        html = ('<b>元大台灣50-持股明細</b> <i>資料日期：2026/09/30</i>'
                '<td>台積電</td> <td>562,112.00</td> <td>56.45</td>'
                '<td>國巨*</td> <td>36,051.00</td> <td>0.80</td>')
        date, names = parse_moneydj_holdings(html)
        self.assertEqual((date, names), ('2026/09/30', ['台積電', '國巨']))
        m = name_to_code_map([{'Code': '2327', 'Name': '國巨*'}, {'Code': '00881', 'Name': 'ETF'}])
        self.assertEqual(m, {'國巨': '2327'})


class TestWalkForward(unittest.TestCase):
    def test_selection_uses_only_past_data(self):
        idx = pd.bdate_range('2017-01-01', '2021-12-31')
        rng = np.random.default_rng(0)
        px = pd.DataFrame({c: 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx))) for c in ('A', 'B', '0050')},
                          index=idx)
        r = evaluate_family(make_ds(px), ['A', 'B'], 'trend_following', start='2017-01-01',
                            oos_years=[2020, 2021], end=idx[-1])
        self.assertEqual(set(r.chosen), {2020, 2021})
        self.assertEqual(r.oos_returns.index[0].year, 2020)
        self.assertIn('0050', r.yearly.columns)
        self.assertEqual(r.passed, not r.reasons)


class TestCache(unittest.TestCase):
    def test_leading_zero_columns_survive_cache(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from src.app.lab import data
        df = pd.DataFrame({'0050': [1.0, 2.0], '2330': [3.0, 4.0]}, index=pd.bdate_range('2024-01-01', periods=2))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(data, 'CACHE', Path(d)):
            data._cache_frame('x', lambda: df)
            back = data._cache_frame('x', lambda: None)
        self.assertEqual(list(back.columns), ['0050', '2330'])
        self.assertEqual(back.index[0], pd.Timestamp('2024-01-01'))


if __name__ == '__main__':
    unittest.main()
