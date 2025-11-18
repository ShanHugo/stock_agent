"""技術與統計指標工具集 (MA / RSI / Volatility / Z-Score / Momentum)
所有函式皆回傳與輸入等長的 pandas.Series，index 對齊。
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from math import sqrt


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def volatility(series: pd.Series, window: int = 20, annualize: bool = False) -> pd.Series:
    rets = series.pct_change()
    vol = rets.rolling(window).std()
    if annualize:
        vol = vol * sqrt(252)
    return vol


def zscore(series: pd.Series, window: int = 20) -> pd.Series:
    mean_ = series.rolling(window).mean()
    std_ = series.rolling(window).std(ddof=0)
    return (series - mean_) / std_.replace(0, np.nan)


def momentum_signal(series: pd.Series, lookback: int = 5) -> pd.Series:
    mom = series.pct_change(lookback)
    sig = (mom > 0).astype(int) - (mom < 0).astype(int)
    return sig


def mean_reversion_signal(series: pd.Series, window: int = 5) -> pd.Series:
    ma = sma(series, window)
    sig = (series < ma).astype(int) - (series > ma).astype(int)
    return sig

__all__ = [
    'sma', 'rsi', 'volatility', 'zscore', 'momentum_signal', 'mean_reversion_signal'
]
