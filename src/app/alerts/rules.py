"""提醒規則：停損 / 停利 / 移動停損、從高點回落、配置偏離。全部以 Python 計算。"""
from __future__ import annotations
import pandas as pd

from .portfolio import Holding

LOT_SIZE = 1000

STOP_LOSS = 'STOP_LOSS'
TRAILING_STOP = 'TRAILING_STOP'
TAKE_PROFIT = 'TAKE_PROFIT'
NEAR_STOP = 'NEAR_STOP'
NEAR_TARGET = 'NEAR_TARGET'
OK = 'OK'
PLAN_INVALID = 'PLAN_INVALID'
TRIGGERED = {STOP_LOSS, TRAILING_STOP, TAKE_PROFIT, PLAN_INVALID}


def trailing_level(h: Holding, closes: pd.Series) -> tuple[float | None, float | None]:
    """回傳 (移動停損價, 買進後最高收盤)。"""
    if h.trailing_stop_pct is None or not h.entry_date:
        return None, None
    since = closes[closes.index >= pd.to_datetime(h.entry_date)]
    if since.empty:
        return None, None
    peak = float(since.max())
    return peak * (1 - h.trailing_stop_pct), peak


def _sell_qty(h: Holding, ratio: float) -> float:
    if ratio >= 1:
        return h.shares
    if h.is_us:
        return round(h.shares * ratio, 4)  # 美股碎股
    return float(max(int(h.shares * ratio), 1))


def evaluate_holding(h: Holding, closes: pd.Series, *, near_threshold: float,
                     fee_bps: float, tax_bps: float) -> dict:
    """依最新收盤判斷停損/停利。closes 為 index=日期 的收盤價序列。"""
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
        'currency': h.currency,
        'is_us': h.is_us,
        'kind': kind,
        'date': pd.to_datetime(closes.index[-1]).strftime('%Y-%m-%d'),
        'close': close,
        'cost': h.cost,
        'shares': h.shares,
        'unrealized_pct': close / h.cost - 1,
        'stop_level': eff_stop,
        'stop_kind': stop_kind if eff_stop is not None else None,
        'trailing_stop': trail,
        'peak_close': peak,
        'target_level': target,
        'action': None,
    }
    if kind in TRIGGERED:
        qty = _sell_qty(h, h.take_profit_sell_ratio if kind == TAKE_PROFIT else 1.0)
        notional = qty * close
        fees = notional * (fee_bps + tax_bps) / 10000
        whole = int(qty) if not h.is_us else 0
        result['action'] = {
            'side': 'SELL',
            'qty': qty,
            'lots': whole // LOT_SIZE,
            'odd_shares': whole % LOT_SIZE,
            'est_notional': notional,
            'est_fees': fees,
            'est_pnl': (close - h.cost) * qty - fees,
        }
    return result


def drawdown_status(h: Holding, closes: pd.Series, window: int) -> dict | None:
    """從近 window 個交易日最高收盤回落的幅度，以及已跨過的最深門檻。"""
    if not h.drawdown_alert_pcts:
        return None
    recent = closes.dropna().sort_index().iloc[-window:]
    peak = float(recent.max())
    close = float(recent.iloc[-1])
    dd = 1 - close / peak
    crossed = [lv for lv in sorted(h.drawdown_alert_pcts) if dd >= lv]
    return {
        'symbol': h.symbol,
        'label': h.label,
        'currency': h.currency,
        'date': pd.to_datetime(recent.index[-1]).strftime('%Y-%m-%d'),
        'close': close,
        'peak': peak,
        'peak_date': pd.to_datetime(recent.idxmax()).strftime('%Y-%m-%d'),
        'drawdown': dd,
        'level': crossed[-1] if crossed else None,
        'window': len(recent),
    }


def allocation_status(values_twd: dict[str, float], groups: dict[str, str], targets: dict[str, float],
                      threshold: float) -> dict:
    """values_twd: {symbol: 台幣市值}；groups: {symbol: 分組}。回傳各組比例、偏離與調整金額。"""
    total = sum(values_twd.values())
    by_group = {g: 0.0 for g in targets}
    for sym, v in values_twd.items():
        by_group[groups[sym]] = by_group.get(groups[sym], 0.0) + v
    weights = {g: v / total for g, v in by_group.items()}
    drift = {g: weights[g] - targets[g] for g in targets}
    return {
        'total_twd': total,
        'values_twd': by_group,
        'weights': weights,
        'targets': targets,
        'drift': drift,
        'threshold': threshold,
        'out_of_band': any(abs(d) > threshold for d in drift.values()),
        # 正數 = 要增加多少台幣才回到目標，負數 = 超出多少
        'adjust_twd': {g: targets[g] * total - by_group[g] for g in targets},
    }
