import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.app.alerts.portfolio import Holding, Portfolio, load_portfolio
from src.app.alerts.rules import evaluate_holding, drawdown_status, allocation_status
from src.app.alerts.runner import run_alerts, update_streaks, new_drawdown_alerts, drop_unconfirmed_bar
from src.app.alerts.notify import build_message, _chunks
from src.app.alerts.narrate import unsupported_numbers, wrong_trigger_words
from src.app.alerts.trades import apply_trade

TPE = ZoneInfo('Asia/Taipei')
FEES = dict(fee_bps=14.25, tax_bps=30.0)


def closes(values, end='2026-10-20'):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def ohlcv(values, end='2026-10-20'):
    c = closes(values, end)
    return pd.DataFrame({'open': c, 'high': c * 1.01, 'low': c * 0.99, 'close': c, 'volume': 1000.0})


def holding(**kw):
    base = dict(symbol='2330', name='台積電', shares=2000, cost=1000.0, entry_date='2026-01-01')
    base.update(kw)
    return Holding(**base)


class TestHoldingMarket(unittest.TestCase):
    def test_market_detection(self):
        self.assertEqual((holding(symbol='0050').yf_symbol, holding(symbol='0050').is_etf), ('0050.TW', True))
        self.assertEqual((holding(symbol='2330').is_etf, holding(symbol='2330').currency), (False, 'TWD'))
        self.assertEqual(holding(symbol='6488.TWO').yf_symbol, '6488.TWO')
        voo = holding(symbol='VOO')
        self.assertEqual((voo.yf_symbol, voo.currency, voo.alloc_group), ('VOO', 'USD', 'US'))

    def test_sell_costs(self):
        pf = Portfolio(holdings=[], fees={'US': {'fee_bps': 10}})
        self.assertEqual(pf.sell_costs_bps(holding(symbol='0050'), 2.8), (2.8, 10.0))   # ETF 證交稅 0.1%
        self.assertEqual(pf.sell_costs_bps(holding(symbol='2330'), 2.8), (2.8, 30.0))   # 股票 0.3%
        self.assertEqual(pf.sell_costs_bps(holding(symbol='VOO'), 2.8), (10.0, 0.0))    # 美股無證交稅


class TestRules(unittest.TestCase):
    def ev(self, h, vals, near=0.02, **fees):
        return evaluate_holding(h, closes(vals), near_threshold=near, **(fees or FEES))

    def test_stop_loss_triggers_sell_all(self):
        r = self.ev(holding(stop_loss_pct=-0.08), [1000, 950, 915])
        self.assertEqual(r['kind'], 'STOP_LOSS')
        self.assertAlmostEqual(r['stop_level'], 920)
        a = r['action']
        self.assertEqual((a['qty'], a['lots']), (2000, 2))
        self.assertAlmostEqual(a['est_fees'], 2000 * 915 * 44.25 / 10000)
        self.assertAlmostEqual(a['est_pnl'], (915 - 1000) * 2000 - a['est_fees'])

    def test_take_profit_partial(self):
        r = self.ev(holding(target_price=1200, take_profit_sell_ratio=0.5), [1100, 1210])
        self.assertEqual((r['kind'], r['action']['qty']), ('TAKE_PROFIT', 1000))

    def test_us_fractional_shares(self):
        voo = holding(symbol='VOO', shares=2.0592, cost=652.32, stop_price=700)
        r = self.ev(voo, [710, 690], fee_bps=0, tax_bps=0)
        self.assertEqual(r['action']['qty'], 2.0592)
        self.assertAlmostEqual(r['action']['est_pnl'], (690 - 652.32) * 2.0592)
        r = self.ev(Holding(symbol='VOO', shares=2.0592, cost=652.32, target_price=680, take_profit_sell_ratio=0.5),
                    [690], fee_bps=0, tax_bps=0)
        self.assertEqual(r['action']['qty'], 1.0296)

    def test_trailing_stop_beats_fixed_stop(self):
        r = self.ev(holding(stop_loss_pct=-0.08, trailing_stop_pct=0.10), [1000, 1300, 1160])
        self.assertEqual(r['kind'], 'TRAILING_STOP')
        self.assertAlmostEqual(r['stop_level'], 1170)

    def test_trailing_ignores_prices_before_entry(self):
        r = self.ev(holding(trailing_stop_pct=0.10, entry_date='2026-10-19'), [2000, 1000, 1000])
        self.assertEqual((r['peak_close'], r['kind']), (1000, 'OK'))

    def test_near_stop_and_ok(self):
        self.assertEqual(self.ev(holding(stop_price=900), [930, 915])['kind'], 'NEAR_STOP')
        self.assertEqual(self.ev(holding(stop_price=900, target_price=1200), [1000])['kind'], 'OK')
        self.assertEqual(self.ev(holding(), [1000])['kind'], 'OK')  # 沒設規則也能評估

    def test_odd_lot_shares(self):
        r = self.ev(holding(shares=1500, stop_price=900), [890])
        self.assertEqual((r['action']['lots'], r['action']['odd_shares']), (1, 500))

    def test_drawdown_status(self):
        h = holding(drawdown_alert_pcts=[0.1, 0.2])
        d = drawdown_status(h, closes([100, 120, 100]), window=252)
        self.assertAlmostEqual(d['drawdown'], 1 - 100 / 120)
        self.assertEqual(d['level'], 0.1)
        self.assertIsNone(drawdown_status(holding(), closes([100]), 252))
        # 只看最近 window 天的高點
        self.assertIsNone(drawdown_status(h, closes([200, 100, 100, 100]), window=3)['level'])

    def test_allocation_status(self):
        al = allocation_status({'0050': 60, 'VOO': 40}, {'0050': 'TW', 'VOO': 'US'},
                               {'TW': 0.5, 'US': 0.5}, 0.05)
        self.assertTrue(al['out_of_band'])
        self.assertAlmostEqual(al['drift']['TW'], 0.1)
        self.assertAlmostEqual(al['adjust_twd']['US'], 10)
        self.assertAlmostEqual(al['adjust_twd']['TW'], -10)


class TestPortfolio(unittest.TestCase):
    def load(self, raw):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.json'
            p.write_text(json.dumps(raw), encoding='utf-8')
            return load_portfolio(p)

    def test_validation(self):
        pf = self.load({'holdings': [
            {'symbol': 2330, 'shares': 1000, 'cost': 100, 'stop_loss_pct': -0.1},
            {'symbol': '2317', 'shares': 1000, 'cost': 100, 'stop_loss_pct': 0.1},
            {'symbol': '2454', 'shares': 1000, 'cost': 100, 'trailing_stop_pct': 0.1},  # 缺 entry_date
            {'symbol': 'VOO', 'shares': 2.0592, 'cost': 652.32},                       # 沒規則也可以
        ]})
        self.assertEqual([h.symbol for h in pf.holdings], ['2330', 'VOO'])
        self.assertEqual(len(pf.errors), 2)

    def test_allocation_validation(self):
        base = [{'symbol': '0050', 'shares': 1, 'cost': 1}, {'symbol': 'VOO', 'shares': 1, 'cost': 1}]
        pf = self.load({'holdings': base, 'allocation': {'targets': {'TW': 0.6, 'US': 0.6}, 'threshold': 0.05}})
        self.assertIsNone(pf.allocation)
        pf = self.load({'holdings': base, 'allocation': {'targets': {'TW': 1.0}, 'threshold': 0.05}})
        self.assertTrue(any('缺少分組' in e for e in pf.errors))
        pf = self.load({'holdings': base, 'allocation': {'targets': {'TW': 0.5, 'US': 0.5}, 'threshold': 0.05}})
        self.assertIsNotNone(pf.allocation)

    def test_example_file_is_valid(self):
        pf = load_portfolio(Path(__file__).resolve().parents[1] / 'portfolio.example.json')
        self.assertEqual(pf.errors, [])
        self.assertIsNotNone(pf.allocation)


class FakeNarrator:
    def __init__(self, text):
        self.text = text

    def summarize(self, message, has_triggered=True):
        self.has_triggered = has_triggered
        return self.text, None


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'state.json'

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, data, holdings, today=date(2026, 10, 20), allocation=None, **kw):
        pf = Portfolio(holdings=holdings, allocation=allocation)
        return run_alerts(pf, lambda sym, s, e: data[sym], today=today, state_path=self.state,
                          default_tw_fee_bps=14.25, **kw)

    def test_triggered_alert_message_and_streak(self):
        data = {'2330.TW': ohlcv([1000] * 10 + [900])}
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)])
        self.assertIn('停損觸發', res['message'])
        self.assertIn('賣出 2,000 股（2 張）', res['message'])
        data = {'2330.TW': ohlcv([1000] * 10 + [900, 890], end='2026-10-21')}
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], today=date(2026, 10, 21))
        self.assertIn('已連續 2 個交易日', res['message'])

    def test_quiet_when_nothing_to_report(self):
        res = self.run_once({'2330.TW': ohlcv([1000] * 10)}, [holding(stop_loss_pct=-0.08)])
        self.assertIsNone(res['message'])

    def test_skip_when_no_new_data_since_last_run(self):
        data = {'2330.TW': ohlcv([1000] * 10 + [900])}
        self.assertIsNotNone(self.run_once(data, [holding(stop_loss_pct=-0.08)])['message'])
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], today=date(2026, 10, 21))  # 休市隔天
        self.assertIsNotNone(res['skipped'])
        self.assertIsNotNone(self.run_once(data, [holding(stop_loss_pct=-0.08)], force=True)['message'])

    def test_intraday_bar_ignored_before_close(self):
        data = {'2330.TW': ohlcv([1000] * 10 + [900])}  # 今天盤中跌破停損
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], now=datetime(2026, 10, 20, 11, 0, tzinfo=TPE))
        self.assertEqual(res['evaluations'][0]['date'], '2026-10-19')
        self.assertIsNone(res['message'])
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], now=datetime(2026, 10, 20, 14, 30, tzinfo=TPE))
        self.assertIn('停損觸發', res['message'])

    def test_us_bar_uses_new_york_close(self):
        df = ohlcv([700] * 5, end='2026-10-19')
        # 台北 10/20 02:00 = 紐約 10/19 14:00，美股還沒收盤 → 丟掉 10/19
        kept = drop_unconfirmed_bar(df, 'US', datetime(2026, 10, 20, 2, 0, tzinfo=TPE))
        self.assertEqual(kept.index[-1].strftime('%Y-%m-%d'), '2026-10-16')
        # 台北 10/20 14:30 = 紐約 10/20 02:30，10/19 已收盤 → 保留
        kept = drop_unconfirmed_bar(df, 'US', datetime(2026, 10, 20, 14, 30, tzinfo=TPE))
        self.assertEqual(kept.index[-1].strftime('%Y-%m-%d'), '2026-10-19')

    def test_drawdown_alert_only_on_deeper_level(self):
        h = [holding(drawdown_alert_pcts=[0.1, 0.2])]
        r = self.run_once({'2330.TW': ohlcv([120] * 10 + [105])}, h)
        self.assertIn('從高點回落 10%', r['message'])
        r = self.run_once({'2330.TW': ohlcv([120] * 10 + [105, 104], end='2026-10-21')}, h, today=date(2026, 10, 21))
        self.assertIsNone(r['message'])  # 還在 10%~20% 之間，不重複通知
        r = self.run_once({'2330.TW': ohlcv([120] * 10 + [105, 104, 95], end='2026-10-22')}, h, today=date(2026, 10, 22))
        self.assertIn('從高點回落 20%', r['message'])

    def test_allocation_with_fx_and_notify_once(self):
        tw = holding(symbol='0050', shares=200, cost=90)
        us = holding(symbol='VOO', shares=2, cost=650)
        alloc = {'targets': {'TW': 0.5, 'US': 0.5}, 'threshold': 0.05}

        def data(end, tw_price):
            return {'0050.TW': ohlcv([tw_price] * 10, end), 'VOO': ohlcv([700] * 10, end),
                    'TWD=X': ohlcv([32.0] * 10, end)}
        # 台股 200*300=60,000，美股 2*700*32=44,800 → 台股 57.3%，偏離 7.3%
        r = self.run_once(data('2026-10-20', 300), [tw, us], allocation=alloc)
        self.assertAlmostEqual(r['allocation']['values_twd']['US'], 44800)
        self.assertIn('配置偏離', r['message'])
        self.assertIn('US 增加', r['message'])
        r = self.run_once(data('2026-10-21', 301), [tw, us], allocation=alloc, today=date(2026, 10, 21))
        self.assertIsNone(r['message'])  # 仍偏離但已通知過

    def test_fetch_error_and_bad_data_are_reported(self):
        bad = ohlcv([1000] * 10 + [900]); bad.iloc[3, bad.columns.get_loc('close')] = -1

        def fetcher(sym, s, e):
            if sym == '2317.TW':
                raise ConnectionError('timeout')
            return bad
        pf = Portfolio(holdings=[holding(stop_loss_pct=-0.08), holding(symbol='2317', name='鴻海', stop_price=1)])
        res = run_alerts(pf, fetcher, today=date(2026, 10, 20), force=True, default_tw_fee_bps=14.25)
        self.assertEqual(len(res['errors']), 2)
        self.assertIn('無法判斷', res['message'])

    def test_narration_included(self):
        nar = FakeNarrator('台積電觸發停損。')
        res = self.run_once({'2330.TW': ohlcv([1000] * 10 + [900])}, [holding(stop_loss_pct=-0.08)], narrator=nar)
        self.assertIn('> 台積電觸發停損。', res['message'])
        self.assertTrue(nar.has_triggered)


class TestTrades(unittest.TestCase):
    RAW = {'holdings': [{'symbol': '0050', 'name': '元大台灣50', 'shares': 204, 'cost': 87.76}]}

    def test_buy_updates_weighted_average(self):
        raw, msg = apply_trade(self.RAW, 'buy', '0050', 12, 108.5)
        h = raw['holdings'][0]
        self.assertEqual(h['shares'], 216)
        self.assertAlmostEqual(h['cost'], round((204 * 87.76 + 12 * 108.5) / 216, 4))
        self.assertEqual(self.RAW['holdings'][0]['shares'], 204)  # 原 dict 不變

    def test_buy_new_us_fractional(self):
        raw, _ = apply_trade(self.RAW, 'buy', 'voo', 0.0712, 701.3, name='先鋒標普500', trade_date='2026-10-20')
        voo = raw['holdings'][1]
        self.assertEqual((voo['symbol'], voo['shares'], voo['entry_date']), ('VOO', 0.0712, '2026-10-20'))

    def test_sell(self):
        raw, _ = apply_trade(self.RAW, 'sell', '0050', 4, 110)
        self.assertEqual((raw['holdings'][0]['shares'], raw['holdings'][0]['cost']), (200, 87.76))
        raw, _ = apply_trade(self.RAW, 'sell', '0050', 204, 110)
        self.assertEqual(raw['holdings'], [])
        with self.assertRaises(ValueError):
            apply_trade(self.RAW, 'sell', '0050', 300, 110)
        with self.assertRaises(ValueError):
            apply_trade(self.RAW, 'sell', '2330', 1, 110)


class TestHelpers(unittest.TestCase):
    def test_streak_same_day_rerun_does_not_increase(self):
        a = [{'symbol': '2330', 'kind': 'STOP_LOSS', 'date': '2026-10-20'}]
        st, _ = update_streaks({}, a)
        st, s = update_streaks(st, a)
        self.assertEqual(s['2330'], 1)

    def test_drawdown_state_resets_after_recovery(self):
        st, al = new_drawdown_alerts({'2330': 0.1}, [{'symbol': '2330', 'level': None}])
        self.assertEqual((st, al), ({}, []))

    def test_unsupported_numbers(self):
        src = '收盤 915（2026-10-20），停損價 920，損益約 -178,100'
        self.assertEqual(unsupported_numbers('台積電收盤 915 跌破 920，損益 -178,100', src), set())
        self.assertEqual(unsupported_numbers('台積電收盤 915，跌幅 8.5%', src), {'8.5'})

    def test_wrong_trigger_words(self):
        self.assertEqual(wrong_trigger_words('台積電已觸及停損價 2,392', has_triggered=False), ['觸及'])
        self.assertEqual(wrong_trigger_words('台積電接近停損價 2,392', has_triggered=False), [])
        self.assertEqual(wrong_trigger_words('台積電觸發停損', has_triggered=True), [])
        self.assertEqual(wrong_trigger_words('0050 尚未觸及停利價 109', has_triggered=False), [])
        self.assertEqual(wrong_trigger_words('0050 尚未觸及停利，台積電已跌破', has_triggered=False), ['跌破'])

    def test_chunks_respect_limit(self):
        text = '\n\n'.join(['x' * 900] * 5)
        self.assertTrue(all(len(c) <= 2000 for c in _chunks(text)))
        self.assertEqual(''.join(_chunks(text)).count('x'), 4500)

    def test_no_message_without_alerts(self):
        self.assertIsNone(build_message([], {}, [], '2026-10-20'))


if __name__ == '__main__':
    unittest.main()
