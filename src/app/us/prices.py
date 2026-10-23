"""美股日線（Yahoo Finance，批次下載，有快取）。

Yahoo 沒有已下市／被併購公司的資料，舊代號被新公司重新使用時還可能抓到別家公司的股價，
所以涵蓋率要搭配成分股期間檢查（見 coverage）。
"""
from __future__ import annotations
import pickle
import time
from pathlib import Path

import pandas as pd

CACHE = Path('data/raw/us/prices.pkl')
BENCHMARKS = ['VOO', 'SPY', 'QQQ', 'RSP', 'QQQE']   # RSP／QQQE：S&P 500／Nasdaq-100 等權重 ETF（檢查倖存者偏差用）
CHUNK = 100


def resolve(symbol: str, renames: dict[str, str]) -> str:
    """沿著改名鏈找到現在的代號（例 DOW → DWDP → DD）。"""
    seen = set()
    while symbol in renames and symbol not in seen:
        seen.add(symbol)
        symbol = renames[symbol]
    return symbol


def download(symbols: list[str], start: str, log=print, max_age_hours: float = 20) -> dict[str, pd.DataFrame]:
    """回傳 {'close': 未還原收盤, 'adj': 還原收盤（含配息）, 'volume': 成交量}，columns=代號。
    快取一天內、而且代號都已涵蓋時直接讀取；否則全部重抓（Yahoo 批次下載很快）。"""
    symbols = sorted(set(symbols) | set(BENCHMARKS))
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age_hours * 3600:
        data = pickle.loads(CACHE.read_bytes())
        if data['start'] <= start and set(symbols) <= set(data['symbols']):
            return data['panels']
    import yfinance as yf
    parts = {'close': [], 'adj': [], 'volume': []}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        log(f'[prices] Yahoo {i + 1}～{i + len(chunk)} / {len(symbols)}')
        raw = yf.download(chunk, start=start, auto_adjust=False, progress=False, threads=True, group_by='column')
        for k, f in (('close', 'Close'), ('adj', 'Adj Close'), ('volume', 'Volume')):
            parts[k].append(raw[f].reindex(columns=chunk))
    panels = {k: pd.concat(v, axis=1).sort_index() for k, v in parts.items()}
    for p in panels.values():
        p.index = pd.to_datetime(p.index).tz_localize(None)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps({'start': start, 'symbols': symbols, 'panels': panels}))
    return panels


def coverage(history: list[tuple[pd.Timestamp, set[str]]], close: pd.DataFrame, renames: dict[str, str],
             days: pd.DatetimeIndex) -> pd.DataFrame:
    """每個交易日：成分股數、有股價的檔數。股價要在「該股屬於成分股的那一天」有值才算
    （避免舊代號被新公司重新使用時，拿到別家公司的股價）。"""
    rows = []
    hist = sorted(history, key=lambda x: x[0])
    k = 0
    for d in days:
        while k + 1 < len(hist) and hist[k + 1][0] <= d:
            k += 1
        members = hist[k][1]
        sym = [resolve(s, renames) for s in members]
        row = close.loc[d] if d in close.index else pd.Series(dtype=float)
        have = sum(1 for s in sym if s in row.index and pd.notna(row[s]))
        rows.append({'date': d, 'members': len(members), 'priced': have})
    return pd.DataFrame(rows).set_index('date')
