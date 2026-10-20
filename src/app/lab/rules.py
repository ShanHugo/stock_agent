"""三類待驗證的選股規則。每個規則：params -> signal_fn(d) -> 當日要持有的代號。只使用 d 當天以前已公布的資訊。"""
from __future__ import annotations
from itertools import product

import pandas as pd

from .data import Dataset


def trend_following(ds: Dataset, codes: list[str], ma: int):
    """收盤價在 N 日均線之上就持有。"""
    sma = ds.prices[codes].rolling(ma, min_periods=ma).mean()

    def signal(d):
        row, avg = ds.prices.loc[d, codes], sma.loc[d]
        return [c for c in codes if pd.notna(avg[c]) and row[c] > avg[c]]
    return signal


def revenue_momentum(ds: Dataset, codes: list[str], months: int, ma: int):
    """最近 months 個「已公布」月份營收年增率都 > 0，且收盤價在 N 日均線之上。"""
    sma = ds.prices[codes].rolling(ma, min_periods=ma).mean()

    def signal(d):
        rev = ds.revenue_asof(d, months)
        if len(rev) < months:
            return []
        ok_rev = (rev > 0).all()
        row, avg = ds.prices.loc[d, codes], sma.loc[d]
        return [c for c in codes if ok_rev.get(c, False) and pd.notna(avg[c]) and row[c] > avg[c]]
    return signal


def value_growth(ds: Dataset, codes: list[str], pe_pctile: float, lookback_months: int = 36):
    """本益比位於自身近 3 年月底本益比的 pe_pctile 分位以下，且最近一季（已公布）EPS 較去年同季成長。"""
    def signal(d):
        hist = ds.pe.loc[:d].iloc[-lookback_months:]
        if len(hist) < 12:
            return []
        eps = ds.eps_asof(d)
        picks = []
        for c in codes:
            s = hist[c].dropna()
            if len(s) < 12 or pd.isna(s.iloc[-1]):
                continue  # 虧損（本益比空白）或資料不足
            rank = float((s <= s.iloc[-1]).mean())
            e = eps[c].dropna()
            if len(e) < 5:
                continue
            last_q = e.index[-1]
            yago = last_q - 4
            if yago not in e.index or e[yago] <= 0:
                continue
            if rank <= pe_pctile and e[last_q] > e[yago]:
                picks.append(c)
        return picks
    return signal


# 每個規則要比較的參數（刻意保持少量，參數越多越容易「碰巧」配出好結果）
GRID = {
    'trend_following': [{'ma': m} for m in (60, 120, 200)],
    'revenue_momentum': [{'months': k, 'ma': m} for k, m in product((2, 3), (60, 120))],
    'value_growth': [{'pe_pctile': q} for q in (0.3, 0.5)],
}
BUILDERS = {'trend_following': trend_following, 'revenue_momentum': revenue_momentum, 'value_growth': value_growth}
NAMES = {'trend_following': '趨勢跟隨', 'revenue_momentum': '營收動能', 'value_growth': '低估值加獲利成長'}
