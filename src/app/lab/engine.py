"""投組回測引擎：每月最後一個交易日收盤依訊號調整持股，選中的股票等權重，沒有選中就持有現金（報酬 0）。

成本（保守、不含券商折扣）：買賣手續費各 0.1425%、賣出證交稅 0.3%、買賣滑價各 0.05%。
"""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np
import pandas as pd

FEE = 0.001425
TAX = 0.003
SLIPPAGE = 0.0005


@dataclass
class BacktestResult:
    returns: pd.Series          # 每日報酬（已扣成本）
    weights: pd.DataFrame       # 每次調整後的權重（index=調整日）
    turnover: pd.Series         # 每次調整的單邊換手（買進比例）
    costs: pd.Series            # 每次調整的成本（占組合比例）


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period('M')).max().values)


def run_backtest(prices: pd.DataFrame, signal_fn, start: pd.Timestamp, end: pd.Timestamp,
                 *, fee=FEE, tax=TAX, slippage=SLIPPAGE, rebalance_dates=None) -> BacktestResult:
    """signal_fn(d) -> 當日收盤後要持有的代號 list（只能用 d 以前的資訊）。
    rebalance_dates 未給時，每月最後一個交易日調整。"""
    px = prices.loc[:end]
    rets = px.pct_change(fill_method=None).fillna(0.0)
    days = px.loc[start:end].index
    rebal = set(pd.DatetimeIndex(rebalance_dates)) if rebalance_dates is not None else set(month_ends(days))
    w = pd.Series(0.0, index=px.columns)
    out, wlog, tlog, clog = [], {}, {}, {}
    for d in days:
        # 先吃當日報酬（持有前一日決定的部位），權重隨價格漂移
        r = float((w * rets.loc[d]).sum())
        if w.sum() > 0:
            # 持股市值 w_i(1+r_i)，組合總值 1+r（現金部分不變），重新換算成權重
            w = w * (1 + rets.loc[d]) / (1 + r)
        cost = 0.0
        if d in rebal:
            picks = [c for c in signal_fn(d) if c in px.columns and pd.notna(px.at[d, c])]
            target = pd.Series(0.0, index=px.columns)
            if picks:
                target[picks] = 1.0 / len(picks)
            buy = (target - w).clip(lower=0).sum()
            sell = (w - target).clip(lower=0).sum()
            cost = buy * (fee + slippage) + sell * (fee + tax + slippage)
            w = target
            wlog[d], tlog[d], clog[d] = target[target > 0], buy, cost
        out.append((1 + r) * (1 - cost) - 1)
    return BacktestResult(pd.Series(out, index=days), pd.DataFrame(wlog).T.fillna(0.0),
                          pd.Series(tlog), pd.Series(clog))


def buy_and_hold(prices: pd.Series, start, end) -> pd.Series:
    p = prices.loc[start:end].dropna()
    return p.pct_change().fillna(0.0)


def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    if r.empty:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(252)
    return {
        'cagr': float(cagr),
        'vol': float(vol),
        'sharpe': float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0,
        'max_drawdown': float((eq / eq.cummax() - 1).min()),
        'total_return': float(eq.iloc[-1] - 1),
        'years': round(years, 2),
    }
