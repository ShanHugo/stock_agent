"""前瞻測試：每天依回測驗證過的規則記下「當天符合條件的名單」，之後用真實的後續價格評估，不改規則、不回頭修改紀錄。

規則（與回測 scripts/strategy_lab.py、營收動能相同，再取營收年增最高的 N 檔）：
  1. 上市＋上櫃電子 8 類（當月名單）
  2. 最近 2 個「已公布」月份（次月 11 日起才算）營收年增率都 > 0
  3. 收盤價在 120 日均線之上
  4. 依最近一個月營收年增率由高到低，取前 N 檔，等權重
評估：以紀錄當天收盤進場，持有 5 / 20 個交易日，扣一次來回成本後，與同期 0050 比較。
這是記錄與驗證，不是買賣建議。
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import Dataset
from .engine import FEE, TAX, SLIPPAGE

RULE_ID = 'rev_momentum_top20_v1'
TOP_N = 20
MA = 120
REV_MONTHS = 2
HORIZONS = (5, 20)
ROUND_TRIP = 2 * FEE + TAX + 2 * SLIPPAGE
BENCHMARK = '0050'


def pick(ds: Dataset, members: list[str], d: pd.Timestamp, n: int = TOP_N) -> pd.DataFrame:
    """回傳符合條件的前 n 檔：code, rev_yoy, rev_yoy_prev, close, ma120, above_ma_pct。"""
    px = ds.prices
    sma = px[members].loc[:d].tail(MA)
    if len(sma) < MA:
        return pd.DataFrame(columns=['code', 'rev_yoy', 'rev_yoy_prev', 'close', 'ma120', 'above_ma_pct'])
    # 與回測一致：要有滿 120 天的價格才算均線（剛上市的股票不算）
    ma = sma.mean().where(sma.notna().sum() >= MA)
    close = px.loc[d, members]
    rev = ds.revenue_asof(d, REV_MONTHS)
    rows = []
    if len(rev) == REV_MONTHS:
        for c in members:
            if c not in rev.columns or pd.isna(close.get(c)) or pd.isna(ma.get(c)):
                continue
            r = rev[c]
            if r.notna().all() and (r > 0).all() and close[c] > ma[c]:
                rows.append({'code': c, 'rev_yoy': float(r.iloc[-1]), 'rev_yoy_prev': float(r.iloc[-2]),
                             'close': float(close[c]), 'ma120': float(ma[c]), 'above_ma_pct': float(close[c] / ma[c] - 1)})
    df = pd.DataFrame(rows, columns=['code', 'rev_yoy', 'rev_yoy_prev', 'close', 'ma120', 'above_ma_pct'])
    return df.sort_values('rev_yoy', ascending=False).head(n).reset_index(drop=True)


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def append_record(path: Path, rec: dict) -> bool:
    """同一天、同一規則只記一次（不覆寫既有紀錄）。回傳是否新增。"""
    if any(r['date'] == rec['date'] and r['rule'] == rec['rule'] for r in load_records(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return True


def _fwd(series: pd.Series, d: pd.Timestamp, h: int) -> float | None:
    """d 收盤到第 h 個交易日收盤的報酬；期間下市則用最後一個有價格的日子。尚未滿 h 天回傳 None。"""
    idx = series.index
    if d not in idx:
        return None
    i = idx.get_loc(d)
    if i + h >= len(idx):
        return None
    start, window = series.iloc[i], series.iloc[i + 1:i + h + 1].dropna()
    if pd.isna(start) or window.empty:
        return None
    return float(window.iloc[-1] / start - 1)


def evaluate(records: list[dict], prices: pd.DataFrame) -> pd.DataFrame:
    """每筆紀錄 × 每個持有天數：名單平均報酬（扣成本）、同期 0050、超額。尚未到期的為 NaN。"""
    rows = []
    for rec in records:
        d = pd.Timestamp(rec['date'])
        codes = [p['code'] for p in rec['picks'] if p['code'] in prices.columns]
        for h in HORIZONS:
            row = {'date': rec['date'], 'rule': rec['rule'], 'horizon': h, 'n': len(codes)}
            rets = [r for r in (_fwd(prices[c], d, h) for c in codes) if r is not None]
            bench = _fwd(prices[BENCHMARK], d, h) if BENCHMARK in prices.columns else None
            if codes and bench is not None and len(rets) == len(codes):
                row['ret'] = float(np.mean(rets)) - ROUND_TRIP
                row['bench'] = bench
                row['excess'] = row['ret'] - bench
            rows.append(row)
    return pd.DataFrame(rows, columns=['date', 'rule', 'horizon', 'n', 'ret', 'bench', 'excess'])


def summarize(ev: pd.DataFrame) -> list[dict]:
    """各持有天數：已到期筆數、平均、勝率；另以不重疊（每 h 筆取 1）計算 t 值，避免重疊樣本高估顯著性。"""
    out = []
    for h in HORIZONS:
        e = ev[(ev['horizon'] == h)].dropna(subset=['excess']).sort_values('date')
        non_overlap = e.iloc[::h]
        sd = non_overlap['excess'].std(ddof=1) if len(non_overlap) > 2 else float('nan')
        out.append({
            'horizon': h, 'matured': len(e), 'pending': int(ev[(ev['horizon'] == h)]['excess'].isna().sum()),
            'avg_ret': float(e['ret'].mean()) if len(e) else None,
            'avg_bench': float(e['bench'].mean()) if len(e) else None,
            'avg_excess': float(e['excess'].mean()) if len(e) else None,
            'win_rate': float((e['excess'] > 0).mean()) if len(e) else None,
            'non_overlap': len(non_overlap),
            't': float(non_overlap['excess'].mean() / sd * np.sqrt(len(non_overlap))) if sd and sd == sd else None,
        })
    return out
