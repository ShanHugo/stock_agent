import unittest
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

from src.app.data import fetch


def yahoo(rows):
    df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume']).set_index('date')
    df.index = pd.to_datetime(df.index)
    df.index.name = 'date'
    return df


def official(table):
    def quotes(market, d):
        rows = table.get((market, d), [])
        return pd.DataFrame(rows, columns=['code', 'open', 'close', 'volume'])
    return quotes


class TestFetchTw(unittest.TestCase):
    def test_fills_missing_close_and_adds_missing_day(self):
        y = yahoo([('2026-10-19', 108, 109, 107, 108.15, 1), ('2026-10-20', 108, 108, 107, 107.25, 1),
                   ('2026-10-21', np.nan, np.nan, np.nan, np.nan, np.nan)])          # Yahoo 當天收盤變空值
        q = official({('twse', date(2026, 10, 21)): [('0050', 108.8, 108.15, 5)],
                      ('twse', date(2026, 10, 22)): [('0050', 108.0, 109.0, 6)]})    # Yahoo 還沒有 10/22
        with mock.patch.object(fetch, 'fetch_ohlcv_yf', return_value=y):
            df = fetch.fetch_ohlcv_tw('0050.TW', '2026-10-01', '2026-10-22', quotes=q, today=date(2026, 10, 22))
        self.assertEqual(df.loc['2026-10-21', 'close'], 108.15)
        self.assertEqual(df.loc['2026-10-21', 'open'], 108.8)
        self.assertEqual(df.loc['2026-10-22', 'close'], 109.0)
        self.assertEqual(df.index[-1], pd.Timestamp('2026-10-22'))
        self.assertEqual(df.loc['2026-10-20', 'close'], 107.25)                       # 官方沒資料的日子不動

    def test_official_overrides_wrong_yahoo_close_and_otc(self):
        y = yahoo([('2026-10-21', 50, 51, 49, 50.5, 1)])
        q = official({('tpex', date(2026, 10, 21)): [('3105', 50, 49.55, 1)]})
        with mock.patch.object(fetch, 'fetch_ohlcv_yf', return_value=y):
            df = fetch.fetch_ohlcv_tw('3105.TWO', '2026-10-01', '2026-10-21', quotes=q, today=date(2026, 10, 21))
        self.assertEqual(df.loc['2026-10-21', 'close'], 49.55)

    def test_non_tw_passthrough_and_yahoo_failure(self):
        y = yahoo([('2026-10-20', 700, 710, 690, 706.8, 1)])
        calls = []
        with mock.patch.object(fetch, 'fetch_ohlcv_yf', return_value=y):
            df = fetch.fetch_ohlcv_tw('TWD=X', '2026-10-01', '2026-10-21', quotes=lambda m, d: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(df.iloc[-1]['close'], 706.8)
        q = official({('twse', date(2026, 10, 21)): [('0050', 108.8, 108.15, 5)]})
        with mock.patch.object(fetch, 'fetch_ohlcv_yf', side_effect=ValueError('no data')):
            df = fetch.fetch_ohlcv_tw('0050.TW', '2026-10-01', '2026-10-21', quotes=q, today=date(2026, 10, 21))
        self.assertEqual(df.iloc[-1]['close'], 108.15)                                # Yahoo 整個失敗也有官方收盤


def chart(price, at, end):
    js = {'chart': {'result': [{'meta': {'regularMarketPrice': price, 'regularMarketTime': at,
                                         'exchangeTimezoneName': 'America/New_York',
                                         'currentTradingPeriod': {'regular': {'end': end}}}}]}}
    return lambda *a, **k: mock.Mock(json=lambda: js)


class TestUsMissingClose(unittest.TestCase):
    CLOSE_1021 = 1792612800          # 2026-10-21 16:00 美東（收盤）

    def df(self):
        return yahoo([('2026-10-20', 704, 707, 703, 706.8, 1), ('2026-10-21', 708.8, 712, 707, np.nan, 1)])

    def test_fills_after_close(self):
        out = fetch.fill_missing_close(self.df(), 'VOO', get=chart(710.72, self.CLOSE_1021, self.CLOSE_1021), today=date(2026, 10, 22))
        self.assertEqual(out.iloc[-1]['close'], 710.72)

    def test_ignores_intraday_price(self):
        out = fetch.fill_missing_close(self.df(), 'VOO', get=chart(709.0, self.CLOSE_1021 - 3600, self.CLOSE_1021), today=date(2026, 10, 22))
        self.assertTrue(np.isnan(out.iloc[-1]['close']))

    def test_ignores_other_day(self):
        close_1022 = self.CLOSE_1021 + 86400
        out = fetch.fill_missing_close(self.df(), 'VOO', get=chart(715.0, close_1022, close_1022), today=date(2026, 10, 23))
        self.assertTrue(np.isnan(out.loc['2026-10-21', 'close']))     # 10/22 的收盤不能補到 10/21
        self.assertEqual(out.loc['2026-10-22', 'close'], 715.0)       # 而是補成 10/22 自己的 K 棒

    def test_adds_missing_latest_day(self):
        # 2026-10-23 08:30 實例：TSM 日線停在 10/21，10/22 整根不見
        df = yahoo([('2026-10-20', 1, 1, 1, 425.08, 1), ('2026-10-21', 1, 1, 1, 431.89, 1)])
        close_1022 = self.CLOSE_1021 + 86400
        out = fetch.fill_missing_close(df, 'TSM', get=chart(412.35, close_1022, close_1022), today=date(2026, 10, 23))
        self.assertEqual(out.index[-1], pd.Timestamp('2026-10-22'))
        self.assertEqual(out['close'].tolist(), [425.08, 431.89, 412.35])

    def test_no_request_when_up_to_date(self):
        df = yahoo([('2026-10-22', 1, 1, 1, 412.35, 1)])
        boom = mock.Mock(side_effect=AssertionError('不該連線'))
        fetch.fill_missing_close(df, 'TSM', get=boom, today=date(2026, 10, 23))
        boom.assert_not_called()

    def test_after_new_york_midnight(self):
        # 紐約過了午夜：交易時段已換成 10/22，但最新價仍是 10/21 16:00 的收盤
        out = fetch.fill_missing_close(self.df(), 'VOO', get=chart(710.72, self.CLOSE_1021, self.CLOSE_1021 + 86400))
        self.assertEqual(out.iloc[-1]['close'], 710.72)

    def test_no_request_when_close_present(self):
        df = yahoo([('2026-10-21', 708.8, 712, 707, 710.72, 1)])
        boom = mock.Mock(side_effect=AssertionError('不該連線'))
        self.assertEqual(fetch.fill_missing_close(df, 'VOO', get=boom, today=date(2026, 10, 22)).iloc[-1]['close'], 710.72)


def nasdaq(rows_by_asset):
    def get(url, params=None, **k):
        rows = rows_by_asset.get(params['assetclass'], [])
        return mock.Mock(json=lambda: {'data': {'tradesTable': {'rows': rows}} if rows else None})
    return get


def nrow(d, close):
    return {'date': d, 'open': '708.84', 'high': '711.83', 'low': '706.33', 'close': close, 'volume': '6,210,315'}


class TestUsPatch(unittest.TestCase):
    def test_adds_missing_day_and_fixes_close(self):
        # Yahoo 少了 10/21 整根，10/20 收盤空值
        df = yahoo([('2026-10-20', 704, 709, 702, np.nan, 1)])
        get = nasdaq({'etf': [nrow('10/21/2026', '710.72'), nrow('10/20/2026', '706.80')]})
        out = fetch.patch_us_daily(df, 'VOO', '2026-10-23', today=date(2026, 10, 22), get=get)
        self.assertEqual(list(out.index.strftime('%m-%d')), ['10-20', '10-21'])
        self.assertEqual(out['close'].tolist(), [706.80, 710.72])
        self.assertEqual(out.loc['2026-10-21', 'volume'], 6210315)

    def test_stock_assetclass_and_dollar_sign(self):
        df = yahoo([('2026-10-20', 1, 1, 1, 1, 1)])
        out = fetch.patch_us_daily(df, 'AAPL', '2026-10-21', today=date(2026, 10, 21),
                                   get=nasdaq({'stocks': [nrow('10/20/2026', '$250.10')]}))
        self.assertEqual(out.iloc[-1]['close'], 250.10)

    def test_keeps_yahoo_intraday_bar(self):
        # Nasdaq 只列已收盤的日子；Yahoo 當天盤中 K 棒保留，交給提醒的 drop_unconfirmed_bar 處理
        df = yahoo([('2026-10-22', 704, 705, 700, 700.9, 1)])
        out = fetch.patch_us_daily(df, 'VOO', '2026-10-23', today=date(2026, 10, 22),
                                   get=nasdaq({'etf': [nrow('10/21/2026', '710.72')]}))
        self.assertEqual(out['close'].tolist(), [710.72, 700.9])

    def test_falls_back_when_nasdaq_down(self):
        df = yahoo([('2026-10-21', 708.8, 712, 707, 710.72, 1)])
        boom = mock.Mock(side_effect=OSError('down'))
        out = fetch.patch_us_daily(df, 'VOO', '2026-10-22', today=date(2026, 10, 22), get=boom)
        self.assertEqual(out.iloc[-1]['close'], 710.72)

    def test_only_plain_us_tickers(self):
        for s, us in (('VOO', True), ('BRK.B', False), ('TWD=X', False), ('^GSPC', False), ('2330.TW', False)):
            self.assertEqual(bool(fetch.US_TICKER.match(s)), us, s)


if __name__ == '__main__':
    unittest.main()
