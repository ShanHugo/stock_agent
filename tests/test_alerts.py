import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.app.alerts.portfolio import Holding, Portfolio, load_portfolio
from src.app.alerts.rules import evaluate_holding
from src.app.alerts.runner import run_alerts, update_streaks
from src.app.alerts.notify import build_message, _chunks
from src.app.alerts.narrate import unsupported_numbers, wrong_trigger_words

FEES = dict(tx_fee_bps=14.25, tx_tax_bps=30.0)


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


class TestRules(unittest.TestCase):
    def ev(self, h, vals, near=0.02):
        return evaluate_holding(h, closes(vals), near_threshold=near, **FEES)

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
        self.assertEqual(r['kind'], 'TAKE_PROFIT')
        self.assertEqual(r['action']['qty'], 1000)

    def test_trailing_stop_beats_fixed_stop(self):
        # 最高 1300，回落 10% → 1170；固定停損 920 → 以移動停損為準
        r = self.ev(holding(stop_loss_pct=-0.08, trailing_stop_pct=0.10), [1000, 1300, 1160])
        self.assertEqual(r['kind'], 'TRAILING_STOP')
        self.assertAlmostEqual(r['stop_level'], 1170)
        self.assertGreater(r['action']['est_pnl'], 0)

    def test_trailing_ignores_prices_before_entry(self):
        h = holding(trailing_stop_pct=0.10, entry_date='2026-10-19')
        r = self.ev(h, [2000, 1000, 1000])  # 2000 在買進日前，不能算進最高價
        self.assertEqual(r['peak_close'], 1000)
        self.assertEqual(r['kind'], 'OK')

    def test_near_stop_and_ok(self):
        self.assertEqual(self.ev(holding(stop_price=900), [930, 915])['kind'], 'NEAR_STOP')
        self.assertEqual(self.ev(holding(stop_price=900, target_price=1200), [1000])['kind'], 'OK')
        self.assertIsNone(self.ev(holding(stop_price=900), [915])['action'])

    def test_odd_lot_shares(self):
        r = self.ev(holding(shares=1500, stop_price=900), [890])
        self.assertEqual((r['action']['lots'], r['action']['odd_shares']), (1, 500))


class TestPortfolio(unittest.TestCase):
    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p.json'
            p.write_text(json.dumps({'holdings': [
                {'symbol': 2330, 'shares': 1000, 'cost': 100, 'entry_date': '2026-01-01', 'stop_loss_pct': -0.1},
                {'symbol': '2317', 'shares': 1000, 'cost': 100, 'entry_date': '2026-01-01', 'stop_loss_pct': 0.1},
                {'symbol': '2454', 'shares': 1000, 'cost': 100, 'entry_date': '2026-01-01'},
            ]}), encoding='utf-8')
            pf = load_portfolio(p)
        self.assertEqual([h.symbol for h in pf.holdings], ['2330'])
        self.assertEqual(pf.holdings[0].yf_symbol, '2330.TW')
        self.assertEqual(len(pf.errors), 2)

    def test_example_file_is_valid(self):
        pf = load_portfolio(Path(__file__).resolve().parents[1] / 'portfolio.example.json')
        self.assertEqual(pf.errors, [])
        self.assertEqual(len(pf.holdings), 2)


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

    def run_once(self, data, holdings, today=date(2026, 10, 20), **kw):
        pf = Portfolio(holdings=holdings)
        return run_alerts(pf, lambda sym, s, e: data[sym], today=today, state_path=self.state, **FEES, **kw)

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

    def test_skip_when_no_data_for_today(self):
        data = {'2330.TW': ohlcv([1000] * 10 + [900], end='2026-10-16')}
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)])
        self.assertIsNotNone(res['skipped'])
        self.assertIsNone(res['message'])
        self.assertIsNotNone(self.run_once(data, [holding(stop_loss_pct=-0.08)], force=True)['message'])

    def test_intraday_bar_ignored_before_close(self):
        from datetime import datetime
        data = {'2330.TW': ohlcv([1000] * 10 + [900])}  # 今天盤中跌破停損
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], now=datetime(2026, 10, 20, 11, 0))
        self.assertIsNotNone(res['skipped'])
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], now=datetime(2026, 10, 20, 14, 30))
        self.assertIn('停損觸發', res['message'])

    def test_fetch_error_and_bad_data_are_reported(self):
        bad = ohlcv([1000] * 10 + [900]); bad.iloc[3, bad.columns.get_loc('close')] = -1

        def fetcher(sym, s, e):
            if sym == '2317.TW':
                raise ConnectionError('timeout')
            return bad
        pf = Portfolio(holdings=[holding(stop_loss_pct=-0.08), holding(symbol='2317', name='鴻海', stop_price=1)])
        res = run_alerts(pf, fetcher, today=date(2026, 10, 20), force=True, **FEES)
        self.assertEqual(len(res['errors']), 2)
        self.assertIn('無法判斷', res['message'])

    def test_narration_included(self):
        data = {'2330.TW': ohlcv([1000] * 10 + [900])}
        nar = FakeNarrator('台積電觸發停損。')
        res = self.run_once(data, [holding(stop_loss_pct=-0.08)], narrator=nar)
        self.assertIn('> 台積電觸發停損。', res['message'])
        self.assertTrue(nar.has_triggered)


class TestHelpers(unittest.TestCase):
    def test_streak_same_day_rerun_does_not_increase(self):
        a = [{'symbol': '2330', 'kind': 'STOP_LOSS', 'date': '2026-10-20'}]
        st, _ = update_streaks({}, a)
        st, s = update_streaks(st, a)
        self.assertEqual(s['2330'], 1)
        st, s = update_streaks(st, [{**a[0], 'kind': 'NEAR_STOP', 'date': '2026-10-21'}])
        self.assertEqual(s['2330'], 1)

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
