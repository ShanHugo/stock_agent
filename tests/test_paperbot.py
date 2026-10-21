import unittest

import pandas as pd

from src.app.lab.paperbot import Bot, Config, DayContext, fee, sell_tax, summarize


def ctx(closes, ma=None, down=(), cands=()):
    return DayContext(close=lambda c: closes.get(c), ma=lambda c: (ma or {}).get(c),
                      revenue_down=lambda c: c in down, candidates=list(cands))


class TestCosts(unittest.TestCase):
    def test_fee_discount_and_minimum(self):
        cfg = Config(capital=10_000)
        self.assertEqual(fee(cfg, 2_500), 1)              # 2500 × 0.0399% ≈ 1.0 → 最低 1 元
        self.assertEqual(fee(cfg, 100_000), 39)           # 無條件捨去
        self.assertEqual(sell_tax(cfg, '2330', 2_500), 7)
        self.assertEqual(sell_tax(cfg, '0050', 2_500), 2)


class TestBot(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(capital=12_376)
        self.bot = Bot.new(self.cfg)

    def test_sizing_respects_position_risk_and_cash(self):
        cands = [{'code': 'EXP', 'close': 3000.0, 'rev_yoy': 90},    # 一股就超過 20% → 跳過
                 {'code': 'A', 'close': 56.5, 'rev_yoy': 80},
                 {'code': 'B', 'close': 1185.0, 'rev_yoy': 50}]
        self.bot.mark('2026-10-20', lambda c: None)
        orders = self.bot.decide('2026-10-20', ctx({}, cands=cands))
        got = {o['code']: o['shares'] for o in orders}
        self.assertNotIn('EXP', got)
        self.assertEqual(got['A'], 43)                     # 12376 × 20% / 56.5 = 43.8
        self.assertEqual(got['B'], 2)

    def test_total_risk_cap_limits_positions(self):
        cands = [{'code': f'S{i}', 'close': 50.0, 'rev_yoy': 100 - i} for i in range(10)]
        self.bot.mark('2026-10-20', lambda c: None)
        orders = self.bot.decide('2026-10-20', ctx({}, cands=cands))
        # 每檔約 20% × 10% 停損 ≈ 2% 風險（含成本略高），總風險 8% → 最多 3 檔
        self.assertEqual(len(orders), 3)

    def test_execute_exit_and_pnl(self):
        self.bot.mark('2026-10-20', lambda c: None)
        self.bot.decide('2026-10-20', ctx({}, cands=[{'code': 'A', 'close': 100.0, 'rev_yoy': 50}]))
        buys = self.bot.execute('2026-10-21', lambda c: 101.0)
        self.assertEqual(buys[0]['shares'], 24)
        self.assertAlmostEqual(self.bot.positions['A']['stop'], 90.9)
        self.bot.mark('2026-10-21', lambda c: 101.0)
        # 跌破 120 日線 → 隔天賣出
        orders = self.bot.decide('2026-10-21', ctx({'A': 99.0}, ma={'A': 100.0}))
        self.assertEqual(orders[0]['side'], 'sell')
        self.assertIn('120 日線', orders[0]['reason'])
        sells = self.bot.execute('2026-10-22', lambda c: 98.0)
        t = sells[0]
        self.assertEqual(t['fees'], 1 + 7)                 # 手續費 1 元＋證交稅 floor(2352 × 0.3%)
        self.assertEqual(self.bot.positions, {})
        self.assertAlmostEqual(self.bot.cash, 12_376 - (24 * 101 + 1) + (24 * 98 - 8))

    def test_halted_stock_sell_waits_buy_cancels(self):
        self.bot.positions['H'] = {'shares': 10, 'entry': 50, 'entry_date': '2026-10-01', 'stop': 45, 'cost': 501}
        self.bot.pending = [{'side': 'sell', 'code': 'H', 'shares': 10, 'reason': 'x'},
                            {'side': 'buy', 'code': 'N', 'shares': 5, 'reason': 'y'}]
        self.bot.execute('2026-10-21', lambda c: None)
        self.assertEqual([o['code'] for o in self.bot.pending], ['H'])

    def test_max_per_theme(self):
        bot = Bot.new(Config(capital=100_000, max_total_risk=0.5, max_per_theme=2))
        self._hold(bot, code='M1', shares=10, entry=100.0)                      # 已持有一檔記憶體
        themes = {'M1': {'記憶體'}, 'M2': {'記憶體'}, 'M3': {'記憶體', '晶圓代工'}, 'P1': {'PCB載板'}, 'X': set()}.get
        cands = [{'code': c, 'close': 50.0, 'rev_yoy': 100 - i} for i, c in enumerate(['M2', 'M3', 'P1', 'X'])]
        c = ctx({'M1': 100.0}, ma={'M1': 90.0}, cands=cands)
        c.themes = lambda code: themes(code, set())
        got = [o['code'] for o in bot.decide('2026-10-20', c) if o['side'] == 'buy']
        self.assertEqual(got, ['M2', 'P1', 'X'])       # M3 屬於已滿 2 檔的記憶體 → 跳過；沒有族群的 X 不受限

    def test_unfilled_sell_not_duplicated(self):
        self._hold(self.bot, code='L', shares=10, entry=100.0)
        self.bot.decide('2026-10-20', ctx({'L': 85.0}, ma={'L': 100.0}))           # 跌破停損 → 賣單
        self.bot.execute('2026-10-21', lambda c: None)                               # 跌停賣不掉，賣單留著
        self.bot.mark('2026-10-21', lambda c: 80.0)
        self.bot.decide('2026-10-21', ctx({'L': 80.0}, ma={'L': 100.0}))           # 仍符合出場，但不重複排單
        self.assertEqual([o['code'] for o in self.bot.pending], ['L'])
        self.bot.execute('2026-10-22', lambda c: 79.0)
        self.assertEqual(self.bot.positions, {})
        self.assertEqual(len([t for t in self.bot.trades if t['side'] == 'sell']), 1)

    def test_dividend_cash_and_stop_adjust(self):
        self.bot.positions['D'] = {'shares': 100, 'entry': 50, 'entry_date': '2026-10-01', 'stop': 45, 'cost': 5001}
        cash0 = self.bot.cash
        self.bot.dividends('2026-10-21', pd.DataFrame([{'code': 'D', 'prev_close': 50.0, 'ref': 48.0}]))
        self.assertAlmostEqual(self.bot.cash - cash0, 200)
        self.assertAlmostEqual(self.bot.positions['D']['stop'], 43.2)

    def test_capital_reduction_keeps_value(self):
        # 彌補虧損減資：每 1000 股換 500 股，參考價從 10 變 20
        self.bot.positions['R'] = {'shares': 100, 'entry': 12, 'entry_date': '2026-10-01', 'stop': 10.8, 'cost': 1201}
        cash0 = self.bot.cash
        self.bot.dividends('2026-10-21', pd.DataFrame([{'code': 'R', 'prev_close': 10.0, 'ref': 20.0}]))
        p = self.bot.positions['R']
        self.assertEqual((p['shares'], p['entry'], p['stop']), (50, 24.0, 21.6))
        self.assertEqual(self.bot.cash, cash0)

    def _hold(self, bot, code='A', shares=20, entry=100.0):
        bot.positions[code] = {'shares': shares, 'entry': entry, 'entry_date': '2026-10-01', 'stop': entry * 0.9,
                               'init_stop': entry * 0.9, 'cost': shares * entry + 1}
        bot.mark('2026-10-20', lambda c: entry)

    def test_take_profit_full_with_cooldown(self):
        bot = Bot.new(Config(capital=12_376, take_profit_pct=0.2, rebuy_cooldown_days=30))
        self._hold(bot)
        cand = [{'code': 'A', 'close': 121.0, 'rev_yoy': 90}]
        orders = bot.decide('2026-10-20', ctx({'A': 121.0}, ma={'A': 100.0}, cands=cand))
        self.assertEqual([(o['side'], o['shares'], o['kind']) for o in orders], [('sell', 20, 'take_profit')])
        bot.execute('2026-10-21', lambda c: 121.0)
        self.assertNotIn('A', bot.positions)
        self.assertEqual(bot.cooldown['A'], '2026-11-20')
        bot.mark('2026-10-21', lambda c: 121.0)
        self.assertEqual(bot.decide('2026-10-21', ctx({'A': 121.0}, ma={'A': 100.0}, cands=cand)), [])   # 冷卻中不買回
        self.assertTrue(bot.decide('2026-11-21', ctx({'A': 121.0}, ma={'A': 100.0}, cands=cand)))        # 冷卻期過可再買

    def test_take_profit_rebuy_allowed_without_cooldown(self):
        bot = Bot.new(Config(capital=12_376, take_profit_pct=0.2))
        self._hold(bot)
        bot.decide('2026-10-20', ctx({'A': 121.0}, ma={'A': 100.0}))
        bot.execute('2026-10-21', lambda c: 121.0)
        bot.mark('2026-10-21', lambda c: 121.0)
        orders = bot.decide('2026-10-21', ctx({'A': 121.0}, ma={'A': 100.0}, cands=[{'code': 'A', 'close': 121.0, 'rev_yoy': 90}]))
        self.assertEqual(orders[0]['side'], 'buy')

    def test_partial_take_profit_only_once(self):
        bot = Bot.new(Config(capital=12_376, take_profit_pct=0.2, take_profit_fraction=0.5))
        self._hold(bot, shares=21)
        orders = bot.decide('2026-10-20', ctx({'A': 125.0}, ma={'A': 100.0}))
        self.assertEqual(orders[0]['shares'], 10)
        t = bot.execute('2026-10-21', lambda c: 125.0)[0]
        self.assertIn('部分', t['reason'])
        self.assertEqual(bot.positions['A']['shares'], 11)
        self.assertAlmostEqual(bot.positions['A']['cost'], (21 * 100 + 1) * 11 / 21)
        bot.mark('2026-10-21', lambda c: 130.0)
        self.assertEqual(bot.decide('2026-10-21', ctx({'A': 130.0}, ma={'A': 100.0})), [])    # 不重複停利

    def test_breakeven_stop(self):
        bot = Bot.new(Config(capital=12_376, breakeven_after=0.2))
        self._hold(bot)
        self.assertEqual(bot.decide('2026-10-20', ctx({'A': 121.0}, ma={'A': 100.0})), [])
        self.assertEqual(bot.positions['A']['stop'], 100.0)
        orders = bot.decide('2026-10-21', ctx({'A': 99.0}, ma={'A': 90.0}))
        self.assertIn('跌破停損', orders[0]['reason'])

    def test_state_roundtrip_and_summary(self):
        self.bot.mark('2026-10-20', lambda c: None)
        self.bot.decide('2026-10-20', ctx({}, cands=[{'code': 'A', 'close': 100.0, 'rev_yoy': 50}]))
        b2 = Bot.from_dict(self.bot.to_dict())
        self.assertEqual(b2.pending, self.bot.pending)
        s = summarize(b2)
        self.assertEqual(s['equity'], 12_376)


if __name__ == '__main__':
    unittest.main()
