import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.app.briefing import sources as src
from src.app.briefing.compose import build_briefing, format_briefing, has_triggered
from src.app.briefing import news
from src.app.alerts.notify import _chunks
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


def rss(*items):
    from xml.sax.saxutils import escape
    body = ''.join(f'<item><title>{escape(t)}</title><link>{escape(l)}</link><pubDate>{d}</pubDate></item>'
                   for t, l, d in items)
    return f'<rss><channel>{body}</channel></rss>'.encode('utf-8')


RECENT = 'Thu, 22 Oct 2026 22:00:00 +0000'   # 台北 10/23 06:00
OLD = 'Mon, 19 Oct 2026 00:00:00 +0000'


def fake_feed(url, params=None, source=''):
    if 'tw.stock.yahoo' in url:
        return news.parse_feed(rss(('0050 規模創新高', 'https://tw.example/1', RECENT),
                                   ('台股盤後大跌', 'https://tw.example/2', RECENT)), source)
    if 'federalreserve' in url:
        return news.parse_feed(rss(('FOMC statement', 'https://www.federalreserve.gov/newsevents/pressreleases/monetary1.htm', RECENT),
                                   ('Enforcement action', 'https://www.federalreserve.gov/x/enforcement1.htm', RECENT)), source)
    if 'cna' in url:
        return news.parse_feed(rss(('台股期指跌385點', 'https://cna.example/1', RECENT),
                                   ('舊聞', 'https://cna.example/2', OLD)), source)
    return news.parse_feed(rss(('S&P 500 edges higher', 'https://us.example/1', RECENT)), source)


class TestNews(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(news.aliases('0050', '元大台灣50'), ['0050', '元大台灣50', '台灣50'])
        self.assertEqual(news.aliases('2317', '鴻海'), ['2317', '鴻海'])   # 不可產生空字串
        self.assertEqual(news.aliases('00881', ''), ['00881'])

    def test_collect_filters_and_dedupes(self):
        pf = Portfolio(holdings=[Holding(symbol='0050', name='元大台灣50', shares=1, cost=1),
                                 Holding(symbol='VOO', shares=1, cost=1, etf=True)])
        n = news.collect_news(pf, datetime(2026, 10, 23, 8, 30, tzinfo=TPE), [], fetch=fake_feed)
        self.assertEqual([i['title'] for i in n['stocks'][0]['items']], ['0050 規模創新高'])  # 無關的盤後新聞被濾掉
        self.assertEqual([i['title'] for i in n['fed']], ['FOMC statement'])                  # 只留貨幣政策
        self.assertEqual([i['title'] for i in n['tw']], ['台股期指跌385點'])                  # 舊聞被濾掉
        self.assertEqual(len(n['us']), 1)

    def test_errors_reported(self):
        errs = []

        def boom(*a, **k):
            raise ConnectionError('down')
        n = news.collect_news(Portfolio(holdings=[]), datetime(2026, 10, 23, 8, 30, tzinfo=TPE), errs, fetch=boom)
        self.assertTrue(errs)
        self.assertIn('沒有抓到相關新聞', '\n'.join(news.format_news(n, TPE)))

    def test_long_news_block_split_by_line(self):
        block = '\n'.join(f'- [標題{i}](<https://example.com/{"x" * 150}/{i}>)' for i in range(30))
        chunks = _chunks('## 標題\n\n' + block)
        self.assertTrue(all(len(c) <= 2000 for c in chunks))
        self.assertTrue(all(c.count('](<') == c.count('>)') for c in chunks))  # 沒有切斷連結
        self.assertNotIn('', chunks)


class TestCompose(unittest.TestCase):
    def build(self, holdings, **kw):
        pf = Portfolio(holdings=holdings)
        return build_briefing(
            pf, now=datetime(2026, 10, 23, 8, 30, tzinfo=TPE), price_fetcher=prices, default_tw_fee_bps=1,
            fetch_taifex=lambda: TAIFEX,
            fetch_html=lambda url: FOMC_HTML if 'federalreserve' in url else BEA_HTML,
            fetch_twse=lambda path: ([{'Date': '1151023', 'Code': '0050', 'Name': '元大台灣50', 'Exdividend': '息',
                                       'CashDividend': '1.0'}] if 'TWT48U' in path else []),
            fetch_feed=fake_feed, **kw)

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
        self.assertIn('[0050 規模創新高](<https://tw.example/1>)', txt)
        self.assertFalse(has_triggered(b))

    def test_source_failures_are_reported_not_fatal(self):
        pf = Portfolio(holdings=[])

        def boom(*a, **k):
            raise ConnectionError('down')
        b = build_briefing(pf, now=datetime(2026, 10, 21, 8, 30, tzinfo=TPE), price_fetcher=prices,
                           default_tw_fee_bps=1, fetch_taifex=boom, fetch_html=boom, fetch_twse=boom,
                           fetch_feed=boom)
        txt = format_briefing(b)
        self.assertIn('抓取失敗', txt)
        self.assertIn('沒有接近或觸發你設定的任何規則', txt)


if __name__ == '__main__':
    unittest.main()
