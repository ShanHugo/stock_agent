"""資料抓取模組：提供最小 yfinance 介面 + 後續可擴充 TWSE API。
輸出皆為 pandas.DataFrame，index=DatetimeIndex (UTC naive / localizable)
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf
from typing import List
from datetime import datetime, timedelta


def fetch_ohlcv_yf(symbol: str, start: str, end: str) -> pd.DataFrame:
    """以 yfinance 抓取日線 OHLCV。
    Parameters
    ----------
    symbol: e.g. '2330.TW'
    start, end: 'YYYY-MM-DD'
    Returns: DataFrame columns=[open, high, low, close, volume]
    """
    # yfinance end 參數為「非包含」；為確保包含 end 當日，向後加 1 天
    end_inclusive = (pd.to_datetime(end) + timedelta(days=1)).strftime('%Y-%m-%d')
    data = yf.download(symbol, start=start, end=end_inclusive, progress=False, auto_adjust=False)
    if data.empty:
        raise ValueError(f"No data fetched for {symbol} {start}~{end}")
    if isinstance(data.columns, pd.MultiIndex):
        # 單一 symbol 時取第二層欄位（symbol 名）
        sym = data.columns.levels[1][0]
        data = data.xs(sym, axis=1, level=1)
    data = data.rename(columns={c: c.lower() for c in data.columns})
    # 移除不必要欄位（如 adj close）
    keep = [c for c in ['open','high','low','close','volume'] if c in data.columns]
    data = data[keep]
    data.index.name = 'date'
    return data


def fetch_multi(symbols: List[str], start: str, end: str) -> pd.DataFrame:
    frames = []
    for sym in symbols:
        df = fetch_ohlcv_yf(sym, start, end)
        df['symbol'] = sym
        frames.append(df.reset_index())
    merged = pd.concat(frames, ignore_index=True)
    merged['date'] = pd.to_datetime(merged['date'])
    merged = merged.set_index(['date','symbol']).sort_index()
    return merged
