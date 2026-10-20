import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.app.briefing import sources as src
from src.app.briefing.compose import build_briefing, format_briefing, has_triggered
from src.app.alerts.portfolio import Holding, Portfolio

TPE = ZoneInfo('Asia/Taipei')

FOMC_HTML = """
<h4>2026 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>October</strong></div><div class="fomc-meeting__date">27-28</div>
<div class="fomc-meeting__month"><strong>December</strong></div><div class="fomc-meeting__date">8-9*</div>
<h4>2027 FOMC Meetings</h4>
<div class="fomc-meeting__month"><strong>Apr/May</strong></div><div class="fomc-meeting__date">30-1</div>
"""

BEA_HTML = """<table>
<tr><th>Year 2026</th><th></th><th>Release</th></tr>
<tr><td class="scheduled-date no-wrap">October 29 8:30 AM</td><td>News</td>
<td class="release-title">GDP (Advance Estimate), 3rd Quarter 2026</td></tr>
<tr><td class="scheduled-date no-wrap">November 4 10:00 AM</td><td>News</td>
<td class="release-title">Trade</td></tr></table>"""

TAIFEX = [
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202611', 'TradingSession': '一般', 'Last': '46948'},
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610', 'TradingSession': '一般', 'Last': '46779'},
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610', 'TradingSession': '盤後', 'Last': '46140'},
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610/202611', 'TradingSession': '一般', 'Last': '175'},
    {'Date': '20261019', 'Contract': 'MTX', 'ContractMonth(Week)': '202610', 'TradingSession': '一般', 'Last': '1'},
]


class TestParsers(unittest.TestCase):
    def test_fomc(self):
        m = src.parse_fomc(FOMC_HTML)
        self.assertEqual([x['date'] for x in m], [date(2026, 10, 28), date(2026, 12, 9), date(2027, 5, 1)])
        self.assertEqual([x['sep'] for x in m], [False, True, False])
        # 美東 10/28 14:00 = 台北 10/29 02:00（夏令時間）
        self.assertEqual(src.to_taipei(m[0]['date'], m[0]['time_et']), datetime(2026, 10, 29, 2, 0, tzinfo=TPE))

    def test_bea(self):
        ev = src.parse_bea(BEA_HTML)
        self.assertEqual(len(ev), 2)
        self.assertEqual((ev[0]['date'], ev[0]['time_et'].hour), (date(2026, 10, 29), 8))
        self.assertEqual(ev[1]['time_et'].hour, 10)
        # 11/4 美國已回到標準時間：10:00 ET = 23:00 台北
        self.assertEqual(src.to_taipei(ev[1]['date'], ev[1]['time_et']).hour, 23)

    def test_taifex_near_month_night(self):
        t = src.taifex_night(TAIFEX)
        self.assertEqual((t['contract'], t['day_close'], t['night_close']), ('202610', 46779, 46140))
        self.assertAlmostEqual(t['night_vs_day'], 46140 / 46779 - 1)
        self.assertIsNone(src.taifex_night([]))

    def test_adr_premium(self):
        m = {'TSM': {'close': 420.0}, 'TWD=X': {'close': 32.5}}
        a = src.adr_premium(m, 2400)
        self.assertAlmostEqual(a['per_share_twd'], 420 * 32.5 / 5)
        self.assertIsNone(src.adr_premium({}, 2400))

    def test_exdividend_and_announcements(self):
        ex = src.parse_exdividend([{'Date': '1151021', 'Code': '0050', 'Name': '元大台灣50', 'Exdividend': '息',
                                    'CashDividend': '1.000000'}, {'Date': 'bad'}])
        self.assertEqual((ex[0]['date'], ex[0]['cash']), (date(2026, 10, 21), 1.0))
        an = src.parse_announcements([{'發言日期': '1151019', '公司代號': '2330', '公司名稱': '台積電',
                                       '主旨 ': '本公司召開\r\n法人說明會'}])
        self.assertEqual(an[0]['subject'], '本公司召開法人說明會')


def prices(sym, start, end):
    base = {'^TNX': 4.0, 'TWD=X': 32.5, 'TSM': 420.0, '2330.TW': 2400.0}.get(sym, 100.0)
    c = pd.Series(np.linspace(base * 0.99, base, 10), index=pd.bdate_range(end='2026-10-20', periods=10))
    return pd.DataFrame({'open': c, 'high': c, 'low': c, 'close': c, 'volume': 1.0})


class TestCompose(unittest.TestCase):
    def build(self, holdings, **kw):
        pf = Portfolio(holdings=holdings)
        return build_briefing(
            pf, now=datetime(2026, 10, 23, 8, 30, tzinfo=TPE), price_fetcher=prices, default_tw_fee_bps=1,
            fetch_taifex=lambda: TAIFEX,
            fetch_html=lambda url: FOMC_HTML if 'federalreserve' in url else BEA_HTML,
            fetch_twse=lambda path: ([{'Date': '1151023', 'Code': '0050', 'Name': '元大台灣50', 'Exdividend': '息',
                                       'CashDividend': '1.0'}] if 'TWT48U' in path else []), **kw)

    def test_full_briefing(self):
        b = self.build([Holding(symbol='0050', name='元大台灣50', shares=100, cost=95, stop_price=99.5)])
        txt = format_briefing(b)
        self.assertIn('盤前簡報 2026-10-23（週五）', txt)
        self.assertIn('費城半導體', txt)
        self.assertIn('夜盤收 46,140', txt)
        self.assertIn('FOMC 利率決議', txt)            # 10/29 02:00 在 10/23 起的 7 天內
        self.assertIn('GDP (Advance Estimate)', txt)
        self.assertIn('10/23 0050 元大台灣50 除息', txt)
        self.assertIn('接近停損', txt)                 # 100 距 99.5 在 2% 內
        self.assertIn('非投資建議', txt)
        self.assertFalse(has_triggered(b))

    def test_source_failures_are_reported_not_fatal(self):
        pf = Portfolio(holdings=[])

        def boom(*a, **k):
            raise ConnectionError('down')
        b = build_briefing(pf, now=datetime(2026, 10, 21, 8, 30, tzinfo=TPE), price_fetcher=prices,
                           default_tw_fee_bps=1, fetch_taifex=boom, fetch_html=boom, fetch_twse=boom)
        txt = format_briefing(b)
        self.assertIn('抓取失敗', txt)
        self.assertIn('沒有接近或觸發你設定的任何規則', txt)


if __name__ == '__main__':
    unittest.main()
