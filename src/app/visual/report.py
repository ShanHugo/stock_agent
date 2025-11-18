"""簡易視覺化：輸出權益曲線與回撤圖"""
from __future__ import annotations
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path


def plot_equity(df: pd.DataFrame, out_dir: str | Path) -> str:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(2, 1, figsize=(10,6), sharex=True, gridspec_kw={'height_ratios':[3,1]})
    equity = df['equity']
    ax[0].plot(equity.index, equity.values, label='Equity')
    ax[0].set_title('Equity Curve')
    ax[0].legend()
    # Drawdown
    roll_max = equity.cummax()
    dd = equity/roll_max - 1
    ax[1].fill_between(dd.index, dd.values, 0, color='red', alpha=0.4)
    ax[1].set_title('Drawdown')
    ax[1].set_ylim(dd.min()*1.1, 0)
    fig.tight_layout()
    out_path = out_dir / 'equity_curve.png'
    fig.savefig(out_path)
    plt.close(fig)
    return str(out_path)
