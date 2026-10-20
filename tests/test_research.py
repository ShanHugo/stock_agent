import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from src.app.research import sources
from src.app.research.card import revenue_section, income_section, valuation_section, chips_section, price_section
from src.app.research.format import format_card
from src.app.alerts.portfolio import Holding, Portfolio
from src.app.alerts.plans import check_invalidation, validate_invalidation
from src.app.alerts.runner import run_alerts


def revenue_hist(yoys, base=100_000.0):
    months = pd.period_range(end='2026-09', periods=len(yoys), freq='M').strftime('%Y-%m')
    rows = []
    for i, (m, y) in enumerate(zip(months, yoys)):
        rev = base * (1 + i * 0.01)
        rows.append({'month': m, 'name': '測試', 'revenue': rev, 'prev_month': rev / 1.01,
                     'last_year': rev / (1 + y / 100), 'mom_pct': 1.0, 'yoy_pct': y, 'cum': rev * 9,
                     'cum_last': rev * 8, 'cum_yoy_pct': 12.5, 'note': ''})
    return pd.DataFrame(rows).set_index('month')


class TestRevenueSection(unittest.TestCase):
    def test_streak_and_windows(self):
        r = revenue_section(revenue_hist([-5, -3, 10, 12, 15, 20]))
        self.assertEqual(r['yoy_streak'], 4)
        self.assertAlmostEqual(r['yoy_3m'], sum(revenue_hist([-5, -3, 10, 12, 15, 20])['revenue'].iloc[-3:])
                               / sum(revenue_hist([-5, -3, 10, 12, 15, 20])['last_year'].iloc[-3:]) - 1)
        self.assertIsNotNone(r['yoy_3m_prev'])
        self.assertEqual(revenue_section(revenue_hist([5, -1, -2]))['yoy_streak'], -2)
        self.assertIsNone(revenue_section(pd.DataFrame()))

    def test_12m_high(self):
        self.assertTrue(revenue_section(revenue_hist([5] * 13))['is_12m_high'])  # 營收逐月遞增


class TestIncomeSection(unittest.TestCase):
    def test_margins_and_changes(self):
        df = pd.DataFrame({
            'revenue': [100, 110, 120, 130, 150], 'gross_profit': [50, 55, 62, 70, 84],
            'op_income': [30, 33, 38, 45, 57], 'net_income': [25, 27, 31, 37, 47], 'eps': [1, 1.1, 1.3, 1.5, 2.0],
        }, index=['2025Q2', '2025Q3', '2025Q4', '2026Q1', '2026Q2'])
        i = income_section(df)
        self.assertAlmostEqual(i['latest']['gross_margin'], 84 / 150)
        self.assertAlmostEqual(i['yoy_revenue'], 0.5)
        self.assertAlmostEqual(i['yoy_eps'], 1.0)
        self.assertAlmostEqual(i['gm_change_yoy'], 84 / 150 - 0.5)
        self.assertAlmostEqual(i['eps_ttm'], 1.1 + 1.3 + 1.5 + 2.0)


class TestValuationAndChips(unittest.TestCase):
    def test_percentile(self):
        hist = pd.DataFrame({'pe': [10, 12, 14, 16, 18.0], 'pb': [1, 1, 1, 1, 1.0],
                             'dividend_yield': [5, 4, 3, 2, 1.0]},
                            index=[f'2026-10-0{i}' for i in range(1, 6)])
        v = valuation_section(hist)
        self.assertEqual((v['pe'], v['pe_pctile']), (18, 1.0))
        self.assertEqual(v['dividend_yield_pctile'], 0.2)
        self.assertEqual(v['pe_range'], (10.0, 14.0, 18.0))

    def test_loss_company_pe_blank(self):
        hist = pd.DataFrame({'pe': [10, None], 'pb': [1, 1.0], 'dividend_yield': [1, 1.0]}, index=['a', 'b'])
        v = valuation_section(hist)
        self.assertIsNone(v['pe'])
        self.assertIsNone(v['pe_pctile'])

    def test_chips(self):
        days = list(pd.bdate_range(end='2026-10-19', periods=6).date)
        inst = {d: pd.DataFrame([{'code': '2330', 'foreign': -1000.0 * (k + 1), 'trust': 2000.0,
                                  'dealer': 0.0, 'total': 0.0}]) for k, d in enumerate(days)}
        margin = {days[0]: pd.DataFrame([{'code': '2330', 'margin_balance': 100.0, 'short_balance': 1.0}]),
                  days[-1]: pd.DataFrame([{'code': '2330', 'margin_balance': 120.0, 'short_balance': 1.0}])}
        c = chips_section('2330', days, fetch_inst=lambda d: inst[d], fetch_margin=lambda d: margin[d])
        self.assertAlmostEqual(c['trust_5d'], 10)          # 5 天 × 2000 股 = 10 張
        self.assertAlmostEqual(c['foreign_6d'], -21)       # (1+…+6)×1000 股
        self.assertEqual((c['foreign_streak'], c['trust_streak']), (-6, 6))
        self.assertAlmostEqual(c['margin']['change_pct'], 0.2)

    def test_price(self):
        c = pd.Series(np.linspace(100, 200, 300), index=pd.bdate_range(end='2026-10-20', periods=300))
        p = price_section(pd.DataFrame({'close': c}))
        self.assertEqual(p['from_high'], 0)
        self.assertGreater(p['vs_ma'][60], 0)


class TestFormat(unittest.TestCase):
    def test_format_handles_missing_sections(self):
        txt = format_card({'code': '2330', 'name': '台積電', 'as_of': '2026-10-20',
                           'revenue': revenue_section(revenue_hist([5] * 3)), 'income': None,
                           'valuation': None, 'chips': None, 'errors': ['income: 尚未公布']})
        self.assertIn('至少連續 3 個月年增', txt)
        self.assertIn('缺少的資料', txt)
        self.assertIn('非投資建議', txt)


class TestSourcesParsing(unittest.TestCase):
    HTML = """<table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th><th>上月營收</th><th>去年當月營收</th>
    <th>上月比較增減(%)</th><th>去年同月增減(%)</th><th>當月累計營收</th><th>去年累計營收</th><th>前期比較增減(%)</th><th>備註</th></tr>
    <tr><td>2330</td><td>台積電</td><td>511,857,399</td><td>514,805,337</td><td>330,980,920</td><td>-0.57</td>
    <td>54.64</td><td>3,898,726,974</td><td>2,762,963,851</td><td>41.10</td><td>因先進製程需求增加</td></tr>
    <tr><td>合計</td><td></td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>-</td></tr></table>"""

    def test_monthly_revenue_parse_and_cache(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sources, 'CACHE', Path(d)), \
                mock.patch.object(sources, '_get', return_value=mock.Mock(text=self.HTML)) as g:
            df = sources.monthly_revenue(2026, 9, today=date(2026, 12, 20))
            self.assertEqual(df.iloc[0]['revenue'], 511857399)
            self.assertEqual(df.iloc[0]['note'], '因先進製程需求增加')
            self.assertEqual(len(df), 1)  # 合計列被排除
            sources.monthly_revenue(2026, 9, today=date(2026, 12, 20))
            self.assertEqual(g.call_count, 1)  # 第二次讀快取

    def test_unpublished_month_not_cached(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sources, 'CACHE', Path(d)), \
                mock.patch.object(sources, '_get', return_value=mock.Mock(text='<table><tr><td>x</td></tr></table>')):
            with self.assertRaises(sources.NoData):
                sources.monthly_revenue(2026, 10, today=date(2026, 10, 20))


class TestRevenueInvalidation(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(validate_invalidation([{'type': 'revenue_yoy_below', 'threshold': 0, 'months': 2}]), [])
        h = Holding(symbol='2330', shares=1, cost=100,
                    plan={'thesis': 'x', 'invalidation': [{'type': 'revenue_yoy_below', 'threshold': 0, 'months': 2}]})
        c = pd.Series([100.0], index=pd.to_datetime(['2026-10-20']))
        self.assertTrue(check_invalidation(h, c, date(2026, 10, 20), revenue=revenue_hist([5, -1, -2])))
        self.assertFalse(check_invalidation(h, c, date(2026, 10, 20), revenue=revenue_hist([-5, -1, 2])))
        self.assertFalse(check_invalidation(h, c, date(2026, 10, 20), revenue=None))


class TestRunnerRevenueAlerts(unittest.TestCase):
    def test_new_month_alert_after_first_run(self):
        prices = pd.DataFrame({'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1.0},
                              index=pd.bdate_range(end='2026-10-20', periods=10))
        pf = Portfolio(holdings=[Holding(symbol='0050', shares=1, cost=100)], watchlist=['2317'])
        hist = {'v': revenue_hist([5, 6])}
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 's.json'
            kw = dict(default_tw_fee_bps=1, state_path=state, revenue_lookup=lambda code: hist['v'], force=True)
            r = run_alerts(pf, lambda *a: prices, today=date(2026, 10, 20), **kw)
            self.assertEqual(r['revenues'], [])          # 第一次只記錄
            r = run_alerts(pf, lambda *a: prices, today=date(2026, 10, 21), **kw)
            self.assertEqual(r['revenues'], [])          # 沒有新月份
            new = revenue_hist([5, 6, -3])
            new.index = ['2026-08', '2026-09', '2026-10']
            hist['v'] = new
            r = run_alerts(pf, lambda *a: prices, today=date(2026, 11, 10), **kw)
            self.assertEqual([x['code'] for x in r['revenues']], ['2317'])
            self.assertIn('月營收公布', r['message'])
            self.assertIn('（觀察）2026-10', r['message'])


if __name__ == '__main__':
    unittest.main()
