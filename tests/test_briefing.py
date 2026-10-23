import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.app.briefing import sources as src
from src.app.briefing.compose import build_briefing, format_briefing
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
<tr><td class="scheduled-date no-wrap">October 22 10:00 AM</td><td>News</td>
<td class="release-title">Experimental Research and Development Economic Statistics, U.S. and States, 2024</td></tr>
<tr><td class="scheduled-date no-wrap">October 30 10:00 AM</td><td>News</td>
<td class="release-title">GDP by State and Personal Income by State, 2nd Quarter 2026</td></tr>
<tr><td class="scheduled-date no-wrap">November 4 10:00 AM</td><td>News</td>
<td class="release-title">U.S. International Trade in Goods and Services, September 2026</td></tr></table>"""

TAIFEX = [
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202611', 'TradingSession': '一般', 'Last': '46948'},
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610', 'TradingSession': '一般', 'Last': '46779'},
    # 10/19 的「盤後」是 10/16 晚上的夜盤（在 10/19 日盤之前），不能用
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610', 'TradingSession': '盤後', 'Last': '47999'},
    # 10/19 日盤之後的夜盤，期交所歸在 10/20
    {'Date': '20261020', 'Contract': 'TX', 'ContractMonth(Week)': '202610', 'TradingSession': '盤後', 'Last': '46140'},
    {'Date': '20261020', 'Contract': 'TX', 'ContractMonth(Week)': '202611', 'TradingSession': '盤後', 'Last': '46300'},
    {'Date': '20261019', 'Contract': 'TX', 'ContractMonth(Week)': '202610/202611', 'TradingSession': '一般', 'Last': '175'},
    {'Date': '20261019', 'Contract': 'MTX', 'ContractMonth(Week)': '202610', 'TradingSession': '一般', 'Last': '1'},
]


class TestParsers(unittest.TestCase):
    def test_market_moving_bea(self):
        keep = ['GDP (Advance Estimate), 3rd Quarter 2026', 'Personal Income and Outlays, September 2026',
                'U.S. International Trade in Goods and Services, September 2026',
                'GDP (Third Estimate), GDP by Industry, and Corporate Profits (Revised), 2nd Quarter 2026']
        drop = ['Experimental Research and Development Economic Statistics, U.S. and States, 2024',
                'GDP by State and Personal Income by State, 2nd Quarter 2026', 'GDP by County and Metropolitan Area, 2025',
                'U.S. International Investment Position, 2nd Quarter 2026', 'Travel and Tourism Satellite Account, 2025']
        self.assertTrue(all(src.market_moving_bea(x) for x in keep))
        self.assertFalse(any(src.market_moving_bea(x) for x in drop))

    def test_fomc(self):
        m = src.parse_fomc(FOMC_HTML)
        self.assertEqual([x['date'] for x in m], [date(2026, 10, 28), date(2026, 12, 9), date(2027, 5, 1)])
        self.assertEqual([x['sep'] for x in m], [False, True, False])
        # 美東 10/28 14:00 = 台北 10/29 02:00（夏令時間）
        self.assertEqual(src.to_taipei(m[0]['date'], m[0]['time_et']), datetime(2026, 10, 29, 2, 0, tzinfo=TPE))

    def test_bea(self):
        ev = src.parse_bea(BEA_HTML)
        self.assertEqual(len(ev), 2)                       # 研發統計、州別 GDP 不影響股市，不列
        self.assertNotIn('Research', ' '.join(e['label'] for e in ev))
        self.assertEqual((ev[0]['date'], ev[0]['time_et'].hour), (date(2026, 10, 29), 8))
        self.assertEqual(ev[1]['time_et'].hour, 10)
        # 11/4 美國已回到標準時間：10:00 ET = 23:00 台北
        self.assertEqual(src.to_taipei(ev[1]['date'], ev[1]['time_et']).hour, 23)

    def test_taifex_near_month_night(self):
        t = src.taifex_night(TAIFEX)
        self.assertEqual((t['contract'], t['day_close'], t['night_close']), ('202610', 46779, 46140))
        self.assertAlmostEqual(t['night_vs_day'], 46140 / 46779 - 1)
        self.assertIsNone(src.taifex_night([]))
        # 昨晚夜盤還沒公布：不能拿日盤之前那一晚的夜盤頂替
        t = src.taifex_night([r for r in TAIFEX if r['Date'] == '20261019'])
        self.assertEqual((t['day_close'], t['night_close'], t['night_vs_day']), (46779, None, None))

    def test_parse_taifex_csv(self):
        csv_text = ('交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,'
                    '最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量\n'
                    '2026/10/22,TX,202611  ,47164,47337,46217,46272,-741,-1.58%,55226,46234,115258,46269,46278,50440,44435,,一般,,\n'
                    '2026/10/22,TX,202611  ,46991,47555,46983,47482,469,1.00%,35753,-,-,47470,47482,50440,44435,,盤後,,\n'
                    '2026/10/23,TX,202611  ,46270,46399,45523,45654,-580,-1.25%,39368,-,-,45652,45665,50440,44435,,盤後,,\n')
        t = src.taifex_night(src.parse_taifex_csv(csv_text))
        # 2026-10-23 實際案例：昨晚夜盤是 45,654（-1.33%），不是 10/22 列的 47,482（+2.61%）
        self.assertEqual((t['date'], t['day_close'], t['night_close']), ('20261022', 46272, 45654))
        self.assertAlmostEqual(t['night_vs_day'], 45654 / 46272 - 1)

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


def cnyes_item(i, title, ts, keywords=(), stocks=(), content='&lt;p&gt;內文&lt;/p&gt;'):
    return {'newsId': i, 'title': title, 'publishAt': ts, 'keyword': list(keywords), 'stock': list(stocks),
            'content': content}


def fake_cnyes(url, params=None, **kw):
    from unittest import mock
    t = int(datetime(2026, 10, 23, 7, 0, tzinfo=TPE).timestamp())
    data = [] if params['page'] > 1 else [
        cnyes_item(1, 'DRAM 合約價第四季再漲 15%', t, ['記憶體']),
        cnyes_item(2, '南亞科法說：HBM 需求強', t - 60, ['南亞科'], ['2408']),
        cnyes_item(3, '面板報價持平', t - 120, ['面板']),
        cnyes_item(5, '盤中速報 - 櫃買指數上漲，記憶體領漲', t - 30, ['記憶體']),      # 盤勢速報不列入族群
        cnyes_item(4, '很久以前的記憶體新聞', t - 86400 * 3, ['記憶體']),
    ]
    return mock.Mock(json=lambda: {'items': {'data': data}}, raise_for_status=lambda: None)


class TestSectorNews(unittest.TestCase):
    def test_collect_group_and_format(self):
        from src.app.briefing import sector_news as sn
        errs = []
        res = sn.collect(datetime(2026, 10, 23, 8, 30, tzinfo=TPE), {'2408': {'記憶體'}}, errs, get=fake_cnyes)
        self.assertEqual(res['total'], 4)                                  # 舊聞被濾掉
        self.assertEqual([g['theme'] for g in res['groups']], ['記憶體'])   # 面板只有 1 則，不列
        sn.add_summaries(res, lambda p: '記憶體合約價第四季再漲 15%，HBM 需求強。', errs)
        txt = '\n'.join(sn.format_section(res, TPE))
        self.assertIn('**記憶體**（2 則）：記憶體合約價', txt)
        self.assertIn('](<https://news.cnyes.com/news/id/1>)', txt)

    def test_summary_guards(self):
        from src.app.briefing import sector_news as sn
        g = {'theme': '記憶體', 'items': [{'title': 'DRAM 再漲 15%', 'excerpt': ''}]}
        self.assertEqual(sn.summarize_theme(g, lambda p: 'DRAM 再漲 15%。')[0], 'DRAM 再漲 15%。')
        self.assertIsNone(sn.summarize_theme(g, lambda p: 'DRAM 再漲 30%。')[0])      # 編造數字
        self.assertIsNone(sn.summarize_theme(g, lambda p: '建議逢低布局記憶體股。')[0])  # 買賣建議

    def test_theme_matching(self):
        from src.app.lab.themes import news_themes, themes_for
        self.assertEqual(news_themes('OLED 面板新品', [], [], {}), {'面板'})              # OLED 不算 LED
        self.assertEqual(news_themes('法說會', [], ['2408'], {'2408': {'記憶體'}}), {'記憶體'})
        self.assertEqual(news_themes('盤後總整理', [], [str(i) for i in range(6)], {'0': {'記憶體'}}), set())
        self.assertIn('記憶體', themes_for([('半導體', '記憶體IC'), ('半導體', 'DRAM製造')]))
        self.assertNotIn('IC設計', themes_for([('半導體', '記憶體IC')]))
        self.assertEqual(themes_for([('平面顯示器', '面板')]), {'面板'})


class TestCompose(unittest.TestCase):
    def build(self, holdings, **kw):
        pf = Portfolio(holdings=holdings)
        return build_briefing(
            pf, now=datetime(2026, 10, 23, 8, 30, tzinfo=TPE), price_fetcher=prices,
            fetch_taifex=lambda today: TAIFEX,
            fetch_html=lambda url: FOMC_HTML if 'federalreserve' in url else BEA_HTML,
            fetch_twse=lambda path: ([{'Date': '1151023', 'Code': '0050', 'Name': '元大台灣50', 'Exdividend': '息',
                                       'CashDividend': '1.0'}] if 'TWT48U' in path else []),
            fetch_feed=fake_feed, fetch_cnyes=fake_cnyes, radar_path=None, scheduler_dir=None, **kw)

    def test_full_briefing(self):
        b = self.build([Holding(symbol='0050', name='元大台灣50', shares=100, cost=95, stop_price=99.5)])
        txt = format_briefing(b)
        self.assertIn('盤前簡報 2026-10-23（週五）', txt)
        self.assertIn('費城半導體', txt)
        self.assertIn('夜盤收 46,140', txt)
        self.assertIn('FOMC 利率決議', txt)            # 10/29 02:00 在 10/23 起的 7 天內
        self.assertIn('GDP (Advance Estimate)', txt)
        self.assertIn('10/23 0050 元大台灣50 除息', txt)
        self.assertNotIn('你的持股（最近收盤）', txt)   # 持股狀態交給 14:30 持股提醒（除權息事件仍保留）
        self.assertNotIn('接近停損', txt)
        self.assertNotIn('模擬帳戶今天', txt)          # 模擬單交給模擬帳戶頻道
        self.assertIn('非投資建議', txt)
        self.assertIn('[0050 規模創新高](<https://tw.example/1>)', txt)
        self.assertIn('**記憶體**（', txt)                # 族群新聞（未經模型整理時只列標題）

    def test_source_failures_are_reported_not_fatal(self):
        pf = Portfolio(holdings=[])

        def boom(*a, **k):
            raise ConnectionError('down')
        b = build_briefing(pf, now=datetime(2026, 10, 21, 8, 30, tzinfo=TPE), price_fetcher=prices,
                           fetch_taifex=boom, fetch_html=boom, fetch_twse=boom,
                           fetch_feed=boom, fetch_cnyes=boom, radar_path=None, scheduler_dir=None)
        txt = format_briefing(b)
        self.assertIn('抓取失敗', txt)
        self.assertIn('鉅亨網新聞', txt)
        self.assertIn('非投資建議', txt)


class TestRadarSection(unittest.TestCase):
    def test_load_and_format(self):
        import json
        import tempfile
        from datetime import date
        from pathlib import Path
        from src.app.briefing.compose import load_radar, format_radar
        row = lambda i, t: {'theme': t, 'rank': i, 'members': 20, 'rev_yoy': 30.5 - i, 'rev_breadth': 0.6, 'r60': 0.12,
                            'above_ma': 0.7, 'score': 1 - i / 10}
        data = {'date': '2026-10-20', 'rows': [row(1, '記憶體'), row(2, '面板'), row(3, 'IC設計')],
                'backtest': {'20': {'periods': 100, 'top_vs_bench': 0.004, 'top_vs_bench_t': 0.8, 'top_beats_bench': 0.48,
                                    'top_vs_bottom': 0.012}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'latest.json'
            p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            self.assertIsNone(load_radar(p, date(2026, 11, 30)))          # 太舊不列
            txt = '\n'.join(format_radar(load_radar(p, date(2026, 10, 21))))
        self.assertIn('1. 記憶體（20 檔）：營收年增中位數 +29.5%', txt)
        self.assertIn('排名最後：IC設計', txt)
        self.assertIn('t 值 +0.8', txt)
        self.assertIn('不是買賣建議', txt)


class TestSchedulerProblems(unittest.TestCase):
    def test_missed_and_failed(self):
        import json
        import tempfile
        from datetime import date
        from pathlib import Path
        from src.app.briefing.compose import scheduler_problems
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            w = lambda n, t, rc: (p / f'{n}.json').write_text(json.dumps({'name': n, 'time': t, 'rc': rc}), encoding='utf-8')
            w('paper_exec', '2026-10-22T09:30:05+08:00', 0)        # 正常
            w('check_alerts', '2026-10-20T14:30:05+08:00', 0)      # 前一個交易日（10/22）沒執行
            w('forward_test', '2026-10-22T16:30:09+08:00', 1)      # 失敗
            out = scheduler_problems(p, date(2026, 10, 22))
        self.assertEqual(len(out), 2)
        self.assertIn('沒有執行：14:30 持股提醒（最後一次 10/20', out[0])
        self.assertIn('失敗：16:30 前瞻測試', out[1])
        self.assertEqual(scheduler_problems(None, date(2026, 10, 22)), [])


class TestNewsWindow(unittest.TestCase):
    def test_since_previous_trading_day(self):
        from src.app.briefing.news import news_since
        weekend = lambda d: d.weekday() >= 5
        wed = news_since(datetime(2026, 10, 21, 8, 30, tzinfo=TPE), weekend)
        self.assertEqual(wed, datetime(2026, 10, 20, 14, 30, tzinfo=TPE))
        mon = news_since(datetime(2026, 10, 26, 8, 30, tzinfo=TPE), weekend)
        self.assertEqual(mon, datetime(2026, 10, 23, 14, 30, tzinfo=TPE))           # 週一 → 週五收盤後
        lny = {datetime(2027, 2, d).date() for d in range(4, 13)}                    # 假設的春節連假 2/4～2/12
        after = news_since(datetime(2027, 2, 15, 8, 30, tzinfo=TPE), lambda d: d.weekday() >= 5 or d in lny)
        self.assertEqual(after, datetime(2027, 2, 3, 14, 30, tzinfo=TPE))


if __name__ == '__main__':
    unittest.main()
