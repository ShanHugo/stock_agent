"""單一特徵分組：每 HORIZON 個交易日（不重疊），把當時的選股範圍依某個特徵由低到高分成 5 組，
看各組未來 HORIZON 日報酬比同期全體平均高或低多少。回答「哪一類股票之後比較會漲」，不含交易成本。
"""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import HORIZON

GROUPS = 5
MIN_STOCKS = GROUPS * 6       # 有效股票太少的日子不分組


@dataclass
class FactorResult:
    feature: str
    group_excess: list[float]     # 第 1（最低）～ 5（最高）組平均超額報酬
    spread: float                 # 第 5 組 - 第 1 組
    spread_t: float
    hit_rate: float               # 第 5 組勝過第 1 組的期數比例
    ic_mean: float
    periods: int
    spread_first_half: float
    spread_second_half: float


def factor_groups(panels: dict, prices: pd.DataFrame, universe_fn, features: list[str], *,
                  start: str, end: pd.Timestamp) -> list[FactorResult]:
    days = prices.loc[start:end].index
    days = days[:len(days) - HORIZON][::HORIZON]   # 只取未來報酬已實現、且彼此不重疊的日子
    out = []
    for f in features:
        rows, ics = [], []
        for d in days:
            members = [c for c in universe_fn(d) if c in prices.columns and pd.notna(prices.at[d, c])]
            x = panels[f].loc[d].reindex(members)
            fwd = panels['target'].loc[d].reindex(members)
            ok = x.notna() & fwd.notna()
            if ok.sum() < MIN_STOCKS:
                continue
            x, fwd = x[ok], fwd[ok]
            g = pd.qcut(x.rank(method='first'), GROUPS, labels=False)
            excess = fwd - fwd.mean()
            rows.append(excess.groupby(g).mean().reindex(range(GROUPS)).to_numpy())
            ics.append(x.rank().corr(fwd.rank()))
        if not rows:
            continue
        M = np.vstack(rows)
        spread = M[:, -1] - M[:, 0]
        half = len(spread) // 2
        sd = spread.std(ddof=1) if len(spread) > 1 else float('nan')
        out.append(FactorResult(
            feature=f, group_excess=[float(v) for v in np.nanmean(M, axis=0)],
            spread=float(spread.mean()), spread_t=float(spread.mean() / sd * np.sqrt(len(spread))) if sd else 0.0,
            hit_rate=float((spread > 0).mean()), ic_mean=float(np.nanmean(ics)), periods=len(spread),
            spread_first_half=float(spread[:half].mean()) if half else float('nan'),
            spread_second_half=float(spread[half:].mean())))
    return out
