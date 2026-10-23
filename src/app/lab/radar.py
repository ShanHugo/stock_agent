"""族群雷達：每個族群（記憶體、面板、IC 設計…）的營收、動能、量能、籌碼狀態，並依「單一特徵分組」驗證過
有規律的特徵排序。另附歷史回測：每 20 個交易日買進排名前 3 的族群，與同期 0050、全部電子股比較。

排序用的特徵（shortterm_lab 電子股報告中 |t|>2 且前後半期方向一致者）：營收年增率、近 60 日報酬、量能比、融資 20 日變化。
每個特徵先取族群內成分股的中位數，再在族群之間轉成百分位，四者平均＝族群分數。
這是資料整理與歷史驗證，不是買賣建議。
"""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import FEE, TAX, SLIPPAGE

SCORE_FEATURES = ['rev_yoy', 'r60', 'vratio', 'margin20']
SHOW_FEATURES = ['rev_yoy', 'rev_breadth', 'r20', 'r60', 'above_ma', 'vratio', 'foreign20', 'margin20']
MIN_MEMBERS = 5
TOP_K = 3
ROUND_TRIP = 2 * FEE + TAX + 2 * SLIPPAGE


def theme_members(code_themes: dict[str, set[str]], universe: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in universe:
        for t in code_themes.get(c, ()):
            out.setdefault(t, []).append(c)
    return {t: m for t, m in out.items() if len(m) >= MIN_MEMBERS}


def extra_panels(ds, panels: dict) -> dict:
    """在 features.raw_panels 之外，補上族群雷達要的：營收連 2 月年增、站上 120 日線。"""
    px = ds.prices[panels['r20'].columns]
    rev = ds.revenue_yoy.reindex(columns=px.columns)
    pos2 = ((rev > 0) & (rev.shift(1) > 0)).astype(float).where(rev.notna() & rev.shift(1).notna())
    breadth = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for m in pos2.index:
        breadth.loc[breadth.index >= ds.revenue_available(m)] = pos2.loc[m].values
    ma = px.rolling(120, min_periods=120).mean()
    return {**panels, 'rev_breadth': breadth, 'above_ma': (px > ma).astype(float).where(ma.notna())}


def snapshot(panels: dict, members: dict[str, list[str]], d: pd.Timestamp) -> pd.DataFrame:
    """某一天各族群的中位數（rev_breadth、above_ma 為比例）與分數。index=族群。"""
    rows = {}
    for t, codes in members.items():
        row = {'members': len(codes)}
        for f in SHOW_FEATURES:
            s = panels[f].loc[d].reindex(codes).dropna()
            row[f] = float(s.mean() if f in ('rev_breadth', 'above_ma') else s.median()) if len(s) else np.nan
        rows[t] = row
    df = pd.DataFrame(rows).T
    if df.empty:
        return df
    ranks = df[SCORE_FEATURES].rank(pct=True)
    df['score'] = ranks.mean(axis=1, skipna=True)
    return df.sort_values('score', ascending=False)


def stock_table(panels: dict, codes: list[str], d: pd.Timestamp) -> pd.DataFrame:
    """某一天每檔股票的指標與分數（四項排序特徵在全部 codes 之間的百分位平均，跨族群可比較）。
    rev_momentum = 最近 2 個已公布月份營收都年增、且站上 120 日線（與前瞻測試的條件相同）。"""
    df = pd.DataFrame({f: panels[f].loc[d].reindex(codes) for f in SHOW_FEATURES})
    df['score'] = df[SCORE_FEATURES].rank(pct=True).mean(axis=1, skipna=True)
    df['rev_momentum'] = (df['rev_breadth'] == 1) & (df['above_ma'] == 1)
    return df


@dataclass
class RotationResult:
    trades: pd.DataFrame          # 每期：前 K 族群、報酬、0050、全部電子
    summary: dict


def backtest(panels: dict, prices: pd.DataFrame, members_fn, *, start: str, end: pd.Timestamp,
             horizon: int = 20, top_k: int = TOP_K) -> RotationResult:
    """每 horizon 個交易日（不重疊）：依分數取前 top_k 個族群，持有其成分股（各族群等權、族群內等權）horizon 天。
    members_fn(d) -> {族群: [代號]}（當天的選股範圍）。"""
    fwd = prices.shift(-horizon) / prices - 1
    days = prices.loc[start:end].index
    days = days[:len(days) - horizon][::horizon]
    rows = []
    for d in days:
        mem = members_fn(d)
        snap = snapshot(panels, mem, d)
        snap = snap.dropna(subset=['score'])
        if len(snap) < top_k * 2:
            continue
        def basket(themes):
            rets = [fwd.loc[d].reindex(mem[t]).dropna().mean() for t in themes]
            rets = [r for r in rets if r == r]
            return float(np.mean(rets)) if rets else np.nan
        top, bottom = list(snap.index[:top_k]), list(snap.index[-top_k:])
        all_codes = sorted({c for m in mem.values() for c in m})
        rows.append({'date': d, 'top': '、'.join(top),
                     'top_ret': basket(top) - ROUND_TRIP, 'bottom_ret': basket(bottom) - ROUND_TRIP,
                     'all_ret': float(fwd.loc[d].reindex(all_codes).dropna().mean()) - ROUND_TRIP,
                     'bench': float(fwd.at[d, '0050'])})
    t = pd.DataFrame(rows).set_index('date')
    ex = t['top_ret'] - t['bench']
    half = len(t) // 2
    s = {
        'periods': len(t), 'horizon': horizon,
        'top_avg': float(t['top_ret'].mean()), 'bottom_avg': float(t['bottom_ret'].mean()),
        'all_avg': float(t['all_ret'].mean()), 'bench_avg': float(t['bench'].mean()),
        'top_vs_bench': float(ex.mean()), 'top_vs_bench_median': float(ex.median()),
        'top_vs_bench_t': float(ex.mean() / ex.std() * np.sqrt(len(ex))) if len(ex) > 2 else 0.0,
        'top_beats_bench': float((ex > 0).mean()),
        'top_vs_bottom': float((t['top_ret'] - t['bottom_ret']).mean()),
        'top_vs_bottom_t': float((t['top_ret'] - t['bottom_ret']).mean() / (t['top_ret'] - t['bottom_ret']).std()
                                 * np.sqrt(len(t))) if len(t) > 2 else 0.0,
        'top_vs_all': float((t['top_ret'] - t['all_ret']).mean()),
        'first_half_vs_bench': float(ex.iloc[:half].mean()), 'second_half_vs_bench': float(ex.iloc[half:].mean()),
        'since_2024_vs_bench': float(ex.loc['2024':].mean()) if len(ex.loc['2024':]) else None,
    }
    return RotationResult(trades=t, summary=s)
