"""互動 Plotly 報表：包含
1. Equity Curve
2. Drawdown
3. Rolling 20d Sharpe (簡化: 20d mean(ret)/std(ret) * sqrt(252))
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from math import sqrt


def rolling_sharpe(returns: pd.Series, window: int = 20) -> pd.Series:
    r = returns.fillna(0)
    mean = r.rolling(window).mean()
    std = r.rolling(window).std(ddof=0)
    sharpe = (mean / std.replace(0, np.nan)) * sqrt(252)
    return sharpe


def build_interactive_report(bt: pd.DataFrame, out_dir: str | Path) -> str:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    equity = bt['equity']
    ret = bt['ret']
    roll_max = equity.cummax()
    dd = equity/roll_max - 1
    rsh = rolling_sharpe(ret)

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        subplot_titles=('Equity Curve', 'Drawdown', 'Rolling Sharpe (20d)'))
    fig.add_trace(go.Scatter(x=equity.index, y=equity, name='Equity', line=dict(color='blue')), row=1, col=1)
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name='Drawdown', line=dict(color='red')), row=2, col=1)
    fig.add_trace(go.Scatter(x=rsh.index, y=rsh, name='Rolling Sharpe', line=dict(color='orange')), row=3, col=1)
    fig.update_yaxes(title_text='Equity', row=1, col=1)
    fig.update_yaxes(title_text='Drawdown', row=2, col=1)
    fig.update_yaxes(title_text='Sharpe', row=3, col=1)
    fig.update_layout(title='Interactive Performance Report', template='plotly_white', hovermode='x unified', legend=dict(orientation='h', y=-0.1))

    out_file = out_dir / 'interactive_report.html'
    fig.write_html(out_file, include_plotlyjs='cdn')
    return str(out_file)
