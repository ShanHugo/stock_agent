# 回測引擎與成本計算
# engine.py

import pandas as pd
import numpy as np

def backtest_engine(df: pd.DataFrame, positions: pd.Series, tx_fee_bps=2, tx_tax_bps=3, slippage_bps=1):
    """
    簡化版 bar-based 回測引擎，計算損益、權益、換手、成本。
    df: 必須包含 'close' 欄位
    positions: 部位序列（index 與 df 對齊）
    """
    df = df.copy()
    positions = positions.reindex(df.index).fillna(0)
    df['position'] = positions
    df['ret'] = df['close'].pct_change().shift(-1).fillna(0) * df['position']
    df['turnover'] = positions.diff().abs().fillna(0)
    df['cost'] = df['turnover'] * (tx_fee_bps + tx_tax_bps + slippage_bps) / 10000 * df['close']
    df['ret'] = df['ret'] - df['cost'] / df['close']
    df['equity'] = (1 + df['ret']).cumprod()
    return df[['ret', 'equity', 'turnover', 'cost']]
