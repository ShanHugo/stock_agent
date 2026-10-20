"""短期預測的特徵與目標。每個特徵只用 d 當天收盤以前（含當天）已知的資料。

特徵（每個日期在當時的成分股之間轉成排名分數 -0.5 ~ +0.5，缺值 = 0 中性）：
  r5 / r20 / r60      近 5 / 20 / 60 個交易日報酬
  vol20               近 20 日報酬波動
  vratio              近 5 日均量 / 近 60 日均量
  foreign20 / trust20 近 20 日外資 / 投信買賣超 ÷ 近 60 日均量
  margin20            近 20 日融資餘額變化 ÷ 近 60 日均量
  rev_yoy             最近一個「已公布」月份的營收年增率
  pe_pct              本益比在自身近 3 年月底資料中的分位
目標：未來 20 個交易日報酬的排名分數。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import Dataset

FEATURES = ['r5', 'r20', 'r60', 'vol20', 'vratio', 'foreign20', 'trust20', 'margin20', 'rev_yoy', 'pe_pct']
HORIZON = 20


def rank_score(row: pd.Series) -> pd.Series:
    """橫斷面排名 → -0.5 ~ +0.5；缺值補 0。"""
    valid = row.dropna()
    if len(valid) < 2:
        return pd.Series(0.0, index=row.index)
    r = valid.rank(pct=True) - 0.5 - 0.5 / len(valid)
    return r.reindex(row.index).fillna(0.0)


def raw_panels(ds: Dataset, volume: pd.DataFrame, chips: dict[str, pd.DataFrame], codes: list[str]) -> dict:
    px = ds.prices[codes]
    ret = px.pct_change(fill_method=None)
    vol = volume.reindex(index=px.index, columns=codes)
    avg60 = vol.rolling(60, min_periods=40).mean()

    def chip(name, n):
        c = chips[name].reindex(index=px.index, columns=codes)
        return c.rolling(n, min_periods=int(n * 0.7)).sum()

    margin = chips['margin'].reindex(index=px.index, columns=codes).ffill(limit=5)
    panels = {
        'r5': px / px.shift(5) - 1,
        'r20': px / px.shift(20) - 1,
        'r60': px / px.shift(60) - 1,
        'vol20': ret.rolling(20, min_periods=15).std(),
        'vratio': vol.rolling(5, min_periods=4).mean() / avg60,
        'foreign20': chip('foreign', 20) / avg60,
        'trust20': chip('trust', 20) / avg60,
        'margin20': (margin - margin.shift(20)) * 1000 / avg60,   # 融資單位：張 → 股
    }
    # 月營收年增率：每天取當天已公布的最新月份
    rev = pd.DataFrame(index=px.index, columns=codes, dtype=float)
    avail = {m: Dataset.revenue_available(m) for m in ds.revenue_yoy.index}
    for m, when in sorted(avail.items(), key=lambda x: x[1]):
        rev.loc[rev.index >= when] = ds.revenue_yoy.loc[m].reindex(codes).values
    panels['rev_yoy'] = rev
    # 本益比分位：月底資料，自身近 36 個月
    pe = ds.pe.reindex(columns=codes)
    pct = pe.rolling(36, min_periods=12).apply(lambda s: (s <= s[-1]).mean() if not np.isnan(s[-1]) else np.nan, raw=True)
    panels['pe_pct'] = pct.reindex(px.index, method='ffill')
    panels['target'] = px.shift(-HORIZON) / px - 1      # 未來 20 日報酬（只用於訓練與評估）
    return panels


def cross_section(panels: dict, d: pd.Timestamp, members: list[str]) -> pd.DataFrame:
    """某一天、當時成分股的特徵排名分數（與原始未來報酬）。"""
    X = pd.DataFrame({f: rank_score(panels[f].loc[d, members]) for f in FEATURES})
    X['fwd'] = panels['target'].loc[d, members]
    return X
