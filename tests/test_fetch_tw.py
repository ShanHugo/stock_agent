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
            df = fetch.fetch_ohlcv_tw('VOO', '2026-10-01', '2026-10-21', quotes=lambda m, d: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(df.iloc[-1]['close'], 706.8)
        q = official({('twse', date(2026, 10, 21)): [('0050', 108.8, 108.15, 5)]})
        with mock.patch.object(fetch, 'fetch_ohlcv_yf', side_effect=ValueError('no data')):
            df = fetch.fetch_ohlcv_tw('0050.TW', '2026-10-01', '2026-10-21', quotes=q, today=date(2026, 10, 21))
        self.assertEqual(df.iloc[-1]['close'], 108.15)                                # Yahoo 整個失敗也有官方收盤


if __name__ == '__main__':
    unittest.main()
