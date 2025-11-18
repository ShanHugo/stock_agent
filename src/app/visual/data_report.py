from __future__ import annotations
import pandas as pd
from pathlib import Path
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _compute_flip_signals(close: pd.Series, lookback: int, mode: str = 'meanrev'):
    """計算翻轉信號索引。

    mode = 'meanrev' (預設):
        Buy  = 正(>0) -> 負(<0)  (期待拉回後反彈)
        Sell = 負(<0) -> 正(>0)
    若未來需要趨勢版本，可傳 mode='trend'：
        Buy  = 負 -> 正
        Sell = 正 -> 負
    """
    mom_raw = close.pct_change(lookback)
    sign = mom_raw.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
    prev = sign.shift(1)
    if mode == 'trend':
        buy_idx = sign[(sign == 1) & (prev == -1)].index
        sell_idx = sign[(sign == -1) & (prev == 1)].index
    else:  # meanrev
        buy_idx = sign[(sign == -1) & (prev == 1)].index
        sell_idx = sign[(sign == 1) & (prev == -1)].index
    return mom_raw, buy_idx, sell_idx


def build_data_report(
    df: pd.DataFrame,
    indicators: dict[str, pd.Series],
    symbol: str,
    out_dir: Path,
    lookback: int = 5,
    buy_idx=None,
    sell_idx=None,
) -> Path:
    """建立資料/指標分析報表 (HTML) 並標註波段買賣點 (lookback 可調)。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 計算（若未外部提供）
    mom_raw = None
    if buy_idx is None or sell_idx is None:
        mom_raw, buy_idx, sell_idx = _compute_flip_signals(df['close'], lookback, mode='meanrev')
    else:
        # 仍需 mom_raw 以畫曲線
        mom_raw, _, _ = _compute_flip_signals(df['close'], lookback, mode='meanrev')

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.55, 0.25, 0.2], specs=[[{"secondary_y": False}], [{"secondary_y": False}], [{"secondary_y": True}]]
    )

    # Row 1: 價格 + 均線
    fig.add_trace(go.Scatter(x=df.index, y=df['close'], name='Close', line=dict(color='#1f77b4')), row=1, col=1)
    for k in ['sma20', 'sma60']:
        if k in indicators and indicators[k].notna().any():
            fig.add_trace(go.Scatter(x=indicators[k].index, y=indicators[k], name=k.upper(), line=dict(width=1)), row=1, col=1)

    # 買賣點
    if buy_idx is not None and len(buy_idx):
        fig.add_trace(go.Scatter(x=buy_idx, y=df.loc[buy_idx, 'close'], mode='markers', name='Buy',
                                 marker=dict(symbol='triangle-up', color='green', size=11, line=dict(width=1, color='darkgreen')),
                                 hovertemplate='Buy %{x|%Y-%m-%d}<br>Price=%{y:.2f}<extra></extra>'), row=1, col=1)
    if sell_idx is not None and len(sell_idx):
        fig.add_trace(go.Scatter(x=sell_idx, y=df.loc[sell_idx, 'close'], mode='markers', name='Sell',
                                 marker=dict(symbol='triangle-down', color='red', size=11, line=dict(width=1, color='darkred')),
                                 hovertemplate='Sell %{x|%Y-%m-%d}<br>Price=%{y:.2f}<extra></extra>'), row=1, col=1)

    # Row 2: RSI
    if 'rsi14' in indicators:
        fig.add_trace(go.Scatter(x=indicators['rsi14'].index, y=indicators['rsi14'], name='RSI14', line=dict(color='#ff7f0e')), row=2, col=1)
        fig.add_hrect(y0=30, y1=30, line_width=1, line_color='gray', row=2, col=1)
        fig.add_hrect(y0=70, y1=70, line_width=1, line_color='gray', row=2, col=1)

    # Row 3: Volume + Momentum / MeanRev
    if 'volume' in df.columns:
        fig.add_trace(go.Bar(x=df.index, y=df['volume'], name='Volume', marker_color='#888'), row=3, col=1, secondary_y=False)
    # 離散 momentum_signal 若提供（統一用 key 'momentum_sig'）
    if 'momentum_sig' in indicators:
        fig.add_trace(go.Scatter(x=indicators['momentum_sig'].index, y=indicators['momentum_sig'], name=f'MomentumSig({lookback})', line=dict(color='purple', width=1)), row=3, col=1, secondary_y=True)
    # 連續動能曲線 (百分比)
    if mom_raw is not None:
        fig.add_trace(go.Scatter(x=mom_raw.index, y=mom_raw * 100, name=f'Mom{lookback} %', line=dict(color='brown', dash='dot')), row=3, col=1, secondary_y=True)
    if 'meanrev_sig' in indicators:
        fig.add_trace(go.Scatter(x=indicators['meanrev_sig'].index, y=indicators['meanrev_sig'], name=f'MeanRevSig({lookback})', line=dict(color='green', dash='dot')), row=3, col=1, secondary_y=True)

    fig.update_layout(
        title=f"Data Report - {symbol} [MeanReversion] (Lkb={lookback} Buy={len(buy_idx)} / Sell={len(sell_idx)})",
        template='plotly_white', legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0)
    )
    fig.update_yaxes(title_text='Price', row=1, col=1)
    fig.update_yaxes(title_text='RSI', row=2, col=1)
    fig.update_yaxes(title_text='Volume', row=3, col=1, secondary_y=False)
    fig.update_yaxes(title_text='Signals / Momentum%', row=3, col=1, secondary_y=True)

    ts = datetime.now().strftime('%H%M%S')
    out_file = out_dir / f"data_report_{symbol.replace('.', '_')}_{ts}.html"
    fig.write_html(str(out_file), include_plotlyjs='cdn')
    return out_file
