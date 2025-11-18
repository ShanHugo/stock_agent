"""績效指標計算"""
from __future__ import annotations
import pandas as pd
import numpy as np


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    r = returns.dropna()
    if r.std() == 0:
        return 0.0
    excess = r - risk_free/252
    return float(np.sqrt(252) * excess.mean() / excess.std())


def max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = equity/roll_max - 1
    return float(dd.min())


def basic_report(df: pd.DataFrame) -> dict:
    eq = df['equity']
    rets = df['ret']
    out = {
        'cumulative_return': float(eq.iloc[-1] - 1),
        'sharpe': sharpe_ratio(rets),
        'max_drawdown': max_drawdown(eq),
        'turnover_sum': float(df['turnover'].sum()),
        'cost_sum': float(df['cost'].sum()),
        'periods': int(len(df))
    }
    return out
