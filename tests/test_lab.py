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


class TestConstituents(unittest.TestCase):
    HTML = ('<h1>臺灣證券交易所與富時國際有限公司合編之臺灣指數系列成分股定期審核結果</h1><p>2026/09/04 17:00</p>'
            '<p>富時羅素公布下列有關臺灣50指數、臺灣中型100指數審核結果。相關成分股納入及刪除之變動將自2026年9月18日'
            '（星期五）交易結束後生效（亦即自2026年9月21日（星期一）起生效）。</p>'
            '<p>臺灣50指數</p><p>成分股納入：<span>藥華藥(6446)</span>。</p><p>成分股刪除：世芯-KY(3661)。</p>'
            '<p>候補名單：台塑(1301)、群創(3481)。</p><p>臺灣中型100指數</p><p>成分股納入：晶豪科(3006)。</p>')

    def test_parse(self):
        from src.app.lab.constituents import parse_announcement
        a = parse_announcement(self.HTML)
        self.assertEqual(a['effective'], '2026-09-21')
        self.assertEqual(a['added'], [('6446', '藥華藥')])
        self.assertEqual(a['removed'], [('3661', '世芯-KY')])   # 中型100 的納入不會混進來

    def test_no_change_announcement(self):
        from src.app.lab.constituents import parse_announcement
        html = self.HTML.replace('成分股納入：<span>藥華藥(6446)</span>。</p><p>成分股刪除：世芯-KY(3661)。', '成分股無增刪。')
        a = parse_announcement(html)
        self.assertEqual((a['added'], a['removed']), ([], []))

    def test_membership_walk_back(self):
        from src.app.lab.constituents import membership_history, members_on
        changes = [{'effective': '2025-09-22', 'added': [('B', '')], 'removed': [('A', '')]},
                   {'effective': '2026-09-21', 'added': [('C', '')], 'removed': [('B', '')]}]
        h = membership_history({'C', 'X'}, changes)
        self.assertEqual(members_on(h, pd.Timestamp('2026-10-01')), {'C', 'X'})
        self.assertEqual(members_on(h, pd.Timestamp('2026-01-01')), {'B', 'X'})
        self.assertEqual(members_on(h, pd.Timestamp('2024-01-01')), {'A', 'X'})
        self.assertEqual(members_on(h, pd.Timestamp('2025-09-22')), {'B', 'X'})  # 生效日當天起算新名單


class TestShortTerm(unittest.TestCase):
    def test_pipeline_runs_and_finds_planted_signal(self):
        from src.app.lab.features import raw_panels, cross_section, FEATURES
        from src.app.lab.shortterm import run_shortterm
        idx = pd.bdate_range('2018-01-01', '2021-12-31')
        codes = [f'S{i:02d}' for i in range(20)]
        rng = np.random.default_rng(1)
        n = len(idx)
        # 投信買超（trust）提前一段時間反映未來報酬：模型應該學到正係數、IC > 0
        drift = rng.normal(0, 0.002, (n, len(codes)))
        rets = drift + rng.normal(0, 0.01, (n, len(codes)))
        px = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=codes)
        px['0050'] = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n))
        fwd_drift = pd.DataFrame(drift, index=idx, columns=codes).rolling(20).sum().shift(-20)
        trust = (fwd_drift * 1e6 + rng.normal(0, 1e3, (n, len(codes)))).fillna(0)
        vol = pd.DataFrame(1e6, index=idx, columns=codes)
        chips = {'foreign': pd.DataFrame(0.0, index=idx, columns=codes), 'trust': trust / 20,
                 'margin': pd.DataFrame(1000.0, index=idx, columns=codes)}
        rev = pd.DataFrame(5.0, index=pd.period_range('2017-12', '2021-12', freq='M'), columns=codes)
        pe = pd.DataFrame(15.0, index=pd.date_range('2017-01-31', '2021-12-31', freq='ME'), columns=codes)
        ds = make_ds(px, revenue=rev, pe=pe)
        panels = raw_panels(ds, vol, chips, codes)
        cs = cross_section(panels, idx[200], codes)
        self.assertEqual(list(cs.columns), FEATURES + ['fwd'])
        self.assertTrue((cs[FEATURES].abs() <= 0.5).all().all())
        r = run_shortterm(panels, px, lambda d: set(codes), train_start='2018-04-01', oos_years=[2020, 2021],
                          end=idx[-1])
        self.assertGreater(r.coefs[2021]['trust20'], 0)
        self.assertGreater(r.ic_mean, 0.05)
        self.assertEqual(sorted(r.yearly.index), [2020, 2021])

    def test_training_cutoff_has_no_label_leak(self):
        # 2021 年模型只能用「目標在 2021 年前已實現」的樣本：最後一個樣本日 + 20 個交易日 < 2021-01-01
        days = pd.bdate_range('2020-01-01', '2021-03-31')
        cutoff = days[days.searchsorted(pd.Timestamp('2021-01-01')) - 20 - 1]
        self.assertLess(days[days.get_loc(cutoff) + 20], pd.Timestamp('2021-01-01'))


class TestMarket(unittest.TestCase):
    def test_roc_dates(self):
        from src.app.lab.market import _roc
        for s in ['108/01/30', '108年01月30日', '1080130']:
            self.assertEqual(_roc(s), pd.Timestamp('2019-01-30'))

    def test_parse_quotes(self):
        from src.app.lab.market import parse_twse_quotes, parse_tpex_quotes
        tw = {'tables': [{}, {'fields': ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價'],
                              'data': [['2448', '晶電', '14,222,527', '1', '1', '1', '1', '1', '25.95'],
                                       ['9999', '停牌', '0', '0', '0', '--', '--', '--', '--']]}]}
        df = parse_twse_quotes(tw)
        self.assertEqual(df.set_index('code').loc['2448', 'close'], 25.95)
        self.assertTrue(pd.isna(df.set_index('code').loc['9999', 'close']))
        tp = {'tables': [{'fields': ['代號', '名稱', '收盤 ', '漲跌', '開盤 ', '最高 ', '最低', '成交股數  '],
                          'data': [['3105', '穩懋', '149.50', '+1.50', '151', '151', '147', '8,284,000']]}]}
        self.assertEqual(parse_tpex_quotes(tp).iloc[0]['volume'], 8284000)
        self.assertTrue(parse_tpex_quotes({'tables': [{'data': []}]}).empty)

    def test_adjust_prices(self):
        from src.app.lab.market import adjust_prices
        idx = pd.bdate_range('2020-01-01', periods=10)
        close = pd.DataFrame({
            'DIV': [100, 101, 102, 98, 99, 100, 100, 100, 100, 100],      # 第 3 天除息 4 元（參考價 98）
            'CR':  [10, 10, 10, 10, 20, 20.5, 20, 20, 20, 20],            # 第 4 天減資恢復買賣，參考價 20
            'GAP': [50, 50, 50, 50, np.nan, 50, 50, 50, 50, 50],         # 停牌一天
            'DEL': [30, 30, 30, 30, 30, 30, np.nan, np.nan, np.nan, np.nan],   # 下市
            'ODD': [10, 10, 10, 10, 10, 10, 10, 30, 30, 30],              # 無紀錄的跳動
        }, index=idx, dtype=float)
        ev = pd.DataFrame({'date': [idx[3], idx[4]], 'code': ['DIV', 'CR'], 'prev_close': [102.0, 10.0], 'ref': [98.0, 20.0]})
        adj, notes = adjust_prices(close, ev)
        r = adj.pct_change(fill_method=None)
        self.assertAlmostEqual(r.loc[idx[3], 'DIV'], 0.0)                   # 98/98 - 1
        self.assertAlmostEqual(adj.loc[idx[9], 'DIV'], 100.0)               # 事件後價格不動
        self.assertAlmostEqual(r.loc[idx[4], 'CR'], 0.0)
        self.assertAlmostEqual(r.loc[idx[5], 'CR'], 0.025)
        self.assertEqual(adj.loc[idx[4], 'GAP'], 50)                        # 停牌日沿用前一日
        self.assertTrue(adj.loc[idx[6]:, 'DEL'].isna().all())               # 下市後保持缺值
        self.assertAlmostEqual(r.loc[idx[7], 'ODD'], 0.0)                   # 超過漲跌幅且無紀錄 → 歸零
        self.assertTrue(any('超過漲跌幅' in n for n in notes))

    def test_universe_includes_delisted_and_is_point_in_time(self):
        from unittest import mock
        from src.app.lab import market
        from src.app.lab.constituents import members_on
        days = pd.DatetimeIndex(['2019-01-30', '2019-01-31', '2019-02-27', '2019-02-28'])
        snap = {('twse', '20190131', 26): ['2448', '3008'], ('twse', '20190228', 26): ['3008'],
                ('tpex', '20190131', 24): ['3105'], ('tpex', '20190228', 24): ['3105', '6488']}
        with mock.patch.object(market, 'industry_day', side_effect=lambda m, d, c: snap.get((m, f'{d:%Y%m%d}', c), [])):
            hist, info = market.build_universe(('twse', 'tpex'), days)
        self.assertEqual(members_on(hist, pd.Timestamp('2019-01-31')), {'2448', '3008', '3105'})
        self.assertEqual(members_on(hist, pd.Timestamp('2019-02-28')), {'3008', '3105', '6488'})
        self.assertEqual(info['2448'], ('twse', '光電業'))
        self.assertEqual(info['6488'], ('tpex', '半導體業'))


class TestFactorGroups(unittest.TestCase):
    def test_predictive_feature_has_positive_spread(self):
        from src.app.lab.factors import factor_groups
        rng = np.random.default_rng(0)
        idx = pd.bdate_range('2020-01-01', periods=400)
        codes = [f'S{i}' for i in range(50)]
        px = pd.DataFrame(100.0, index=idx, columns=codes)
        good = pd.DataFrame(rng.normal(size=(len(idx), 50)), index=idx, columns=codes)
        noise = pd.DataFrame(rng.normal(size=(len(idx), 50)), index=idx, columns=codes)
        target = good * 0.02 + pd.DataFrame(rng.normal(scale=0.02, size=(len(idx), 50)), index=idx, columns=codes)
        res = {r.feature: r for r in factor_groups({'good': good, 'noise': noise, 'target': target}, px,
                                                   lambda d: set(codes), ['good', 'noise'], start='2020-01-01', end=idx[-1])}
        self.assertGreater(res['good'].spread, 0.03)
        self.assertGreater(res['good'].spread_t, 5)
        self.assertLess(abs(res['noise'].spread_t), 3)
        self.assertEqual(res['good'].periods, (400 - 20 + 19) // 20)
        self.assertAlmostEqual(sum(res['good'].group_excess), 0, places=6)   # 等量分組，超額報酬合計為 0


if __name__ == '__main__':
    unittest.main()
