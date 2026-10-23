"""美股選股範圍：S&P 500 ∪ Nasdaq-100「當時」的成分股。

來源（2026-10 實測）：
  目前名單   Wikipedia「List of S&P 500 companies」、「List of NASDAQ-100 companies」
  歷年變動   Wikipedia「Historical components of the S&P 500」（1976 起，標明為選錄）、
             「Historical components of the Nasdaq-100」（2007 起）
由目前名單依變動紀錄往回推算各時點的名單；推算出的檔數偏離 500／100 越多，代表變動紀錄越不完整。
代號一律轉成 Yahoo 格式（BRK.B → BRK-B）。
"""
from __future__ import annotations
import io
import re
import time
from pathlib import Path

import pandas as pd
import requests

CACHE = Path('data/raw/us/wiki')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) StockRobot research'}
WIKI = 'https://en.wikipedia.org/wiki/'
PAGES = {
    'sp500_current': 'List_of_S%26P_500_companies',
    'sp500_changes': 'Historical_components_of_the_S%26P_500',
    'ndx_current': 'List_of_NASDAQ-100_companies',
    'ndx_changes': 'Historical_components_of_the_Nasdaq-100',
}
INDEX_SIZE = {'sp500': 500, 'ndx': 100}
# 同一家公司只換代號（變動表記成「加入新代號、刪除舊代號」）：說明文字出現這些字眼且兩邊是同一列
RENAME_WORDS = re.compile(r'changed its (?:ticker|name)|ticker symbol from|renamed|changed to', re.I)


def yahoo_symbol(s) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = re.sub(r'\[.*?\]', '', str(s)).strip().upper()
    return s.replace('.', '-') or None


def fetch_page(key: str, max_age_hours: float = 24) -> str:
    """抓 Wikipedia 頁面並快取（一天內重用）。"""
    path = CACHE / f'{key}.html'
    if path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600:
        return path.read_text(encoding='utf-8')
    r = requests.get(WIKI + PAGES[key], headers=UA, timeout=60)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding='utf-8')
    return r.text


def parse_current(html: str) -> pd.DataFrame:
    """目前名單 → symbol, name, (cik)。取第一個有 Symbol／Ticker 欄的表格。"""
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        key = next((c for c in cols if c in ('Symbol', 'Ticker')), None)
        if key and len(t) >= 90:
            name = next(c for c in cols if c in ('Security', 'Company'))
            out = pd.DataFrame({'symbol': t[key].map(yahoo_symbol), 'name': t[name].astype(str)})
            if 'CIK' in cols:
                out['cik'] = pd.to_numeric(t['CIK'], errors='coerce').astype('Int64')
            return out.dropna(subset=['symbol']).reset_index(drop=True)
    raise ValueError('找不到目前成分股表格')


def parse_changes(html: str) -> pd.DataFrame:
    """歷年變動 → date, added, added_name, removed, removed_name, reason（新到舊）。"""
    t = pd.read_html(io.StringIO(html))[0]
    t = t.iloc[:, :6]
    t.columns = ['date', 'added', 'added_name', 'removed', 'removed_name', 'reason']
    t['date'] = pd.to_datetime(t['date'].astype(str).str.replace(r'\[.*?\]', '', regex=True).str.strip(),
                               format='mixed', errors='coerce')
    for c in ('added', 'removed'):
        t[c] = t[c].map(yahoo_symbol)
    t['reason'] = t['reason'].astype(str).str.replace(r'\[.*?\]', '', regex=True).str.strip()
    return t.dropna(subset=['date']).sort_values('date', ascending=False).reset_index(drop=True)


def membership_history(current: set[str], changes: pd.DataFrame, start: str) -> list[tuple[pd.Timestamp, set[str]]]:
    """[(生效日, 當天起的名單)]，由舊到新。生效日當天已是新名單。"""
    members = set(current)
    hist = []
    for d, grp in changes[changes['date'] >= pd.Timestamp(start)].groupby('date', sort=False):
        hist.append((d, set(members)))
        members -= set(grp['added'].dropna())
        members |= set(grp['removed'].dropna())
    hist.append((pd.Timestamp(start), set(members)))
    return sorted(hist, key=lambda x: x[0])


def _mentions(reason: str, ticker: str, name: str) -> bool:
    """說明文字是否提到這家公司（代號，或名稱的第一個字，例 Kraft Foods → Kraft）。"""
    words = [w for w in re.findall(r'[A-Za-z]+', str(name)) if w.lower() not in ('the', 'inc', 'co', 'corp')]
    return bool(re.search(rf'\b{re.escape(ticker)}\b', reason)) or (bool(words) and words[0].lower() in reason.lower())


def renames(changes: pd.DataFrame) -> dict[str, str]:
    """舊代號 → 新代號：同一列加入新代號、刪除舊代號，說明是改名／改代號，而且同時提到兩家公司
    （避免把同一天不相關的進出，例如 XEC 剔除、IR 納入，誤當成改名）。"""
    m = changes[changes['added'].notna() & changes['removed'].notna()
                & changes['reason'].str.contains(RENAME_WORDS.pattern, case=False, regex=True)]
    return {r.removed: r.added for r in m.itertuples() if r.removed != r.added
            and _mentions(r.reason, r.removed, r.removed_name) and _mentions(r.reason, r.added, r.added_name)}


def load_universe(start: str = '2010-01-01') -> dict:
    """回傳 {'sp500': history, 'ndx': history, 'union': history, 'current': DataFrame, 'changes': {...}, 'renames': {...}}。"""
    sp_cur = parse_current(fetch_page('sp500_current'))
    nd_cur = parse_current(fetch_page('ndx_current'))
    sp_ch = parse_changes(fetch_page('sp500_changes'))
    nd_ch = parse_changes(fetch_page('ndx_changes'))
    sp = membership_history(set(sp_cur['symbol']), sp_ch, start)
    nd = membership_history(set(nd_cur['symbol']), nd_ch, start)
    dates = sorted({d for d, _ in sp} | {d for d, _ in nd})
    union = [(d, members_at(sp, d) | members_at(nd, d)) for d in dates]
    current = pd.concat([sp_cur.assign(index='sp500'), nd_cur.assign(index='ndx')], ignore_index=True)
    return {'sp500': sp, 'ndx': nd, 'union': union, 'current': current,
            'changes': {'sp500': sp_ch, 'ndx': nd_ch}, 'renames': {**renames(sp_ch), **renames(nd_ch)}}


def members_at(history: list[tuple[pd.Timestamp, set[str]]], d) -> set[str]:
    d = pd.Timestamp(d)
    current = history[0][1]
    for start, m in history:
        if start <= d:
            current = m
        else:
            break
    return current
