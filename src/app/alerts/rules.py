"""停損 / 停利 / 移動停損判斷：全部以 Python 計算，依最新收盤價。"""
from __future__ import annotations
import pandas as pd

from .portfolio import Holding

LOT_SIZE = 1000

# 嚴重度：觸發 > 接近 > 正常
STOP_LOSS = 'STOP_LOSS'
TRAILING_STOP = 'TRAILING_STOP'
TAKE_PROFIT = 'TAKE_PROFIT'
NEAR_STOP = 'NEAR_STOP'
NEAR_TARGET = 'NEAR_TARGET'
OK = 'OK'
TRIGGERED = {STOP_LOSS, TRAILING_STOP, TAKE_PROFIT}


def trailing_level(h: Holding, closes: pd.Series) -> tuple[float | None, float | None]:
    """回傳 (移動停損價, 買進後最高收盤)。"""
    if h.trailing_stop_pct is None:
        return None, None
    since = closes[closes.index >= pd.to_datetime(h.entry_date)]
    if since.empty:
        return None, None
    peak = float(since.max())
    return peak * (1 - h.trailing_stop_pct), peak


def evaluate_holding(h: Holding, closes: pd.Series, *, near_threshold: float,
                     tx_fee_bps: float, tx_tax_bps: float) -> dict:
    """依最新收盤判斷單一持股。closes 為 index=日期 的收盤價序列。"""
    closes = closes.dropna().sort_index()
    close = float(closes.iloc[-1])
    stop = h.stop_level()
    trail, peak = trailing_level(h, closes)
    target = h.target_level()

    # 固定停損與移動停損取較高者（較先觸發的那個）
    eff_stop, stop_kind = stop, STOP_LOSS
    if trail is not None and (eff_stop is None or trail > eff_stop):
        eff_stop, stop_kind = trail, TRAILING_STOP

    kind = OK
    if eff_stop is not None and close <= eff_stop:
        kind = stop_kind
    elif target is not None and close >= target:
        kind = TAKE_PROFIT
    elif eff_stop is not None and close <= eff_stop * (1 + near_threshold):
        kind = NEAR_STOP
    elif target is not None and close >= target * (1 - near_threshold):
        kind = NEAR_TARGET

    result = {
        'symbol': h.symbol,
        'label': h.label,
        'kind': kind,
        'date': pd.to_datetime(closes.index[-1]).strftime('%Y-%m-%d'),
        'close': close,
        'cost': h.cost,
        'shares': h.shares,
        'unrealized_pct': close / h.cost - 1,
        'stop_level': eff_stop,
        'stop_kind': stop_kind if eff_stop is not None else None,
        'fixed_stop': stop,
        'trailing_stop': trail,
        'peak_close': peak,
        'target_level': target,
        'action': None,
    }
    if kind in TRIGGERED:
        qty = h.shares if kind != TAKE_PROFIT else int(h.shares * h.take_profit_sell_ratio)
        qty = max(qty, 1)
        notional = qty * close
        fees = notional * (tx_fee_bps + tx_tax_bps) / 10000
        result['action'] = {
            'side': 'SELL',
            'qty': qty,
            'lots': qty // LOT_SIZE,
            'odd_shares': qty % LOT_SIZE,
            'est_notional': notional,
            'est_fees': fees,
            'est_pnl': (close - h.cost) * qty - fees,
        }
    return result
