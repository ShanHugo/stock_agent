"""PM/Risk 階段：策略 → 風控限制 → 回測指標 → Kill-Switch 判斷。"""
from __future__ import annotations
import pandas as pd

from ..backtest.engine import backtest_engine
from ..performance.metrics import basic_report
from ..ops.kill_switch import should_halt
from ..strategies import MomentumStrategy, MeanReversionStrategy

STRATEGIES = {
    'momentum': MomentumStrategy,
    'mean_reversion': MeanReversionStrategy,
}


def build_positions(df: pd.DataFrame, strategy: str, lookback: int, long_only: bool) -> pd.Series:
    if strategy not in STRATEGIES:
        raise ValueError(f'未知策略: {strategy}，可用: {list(STRATEGIES)}')
    pos = STRATEGIES[strategy](lookback=lookback).generate_positions(df)
    if long_only:
        # 台股放空需融券，預設只做多：空方訊號改為空手
        pos = pos.clip(lower=0)
    return pos


def realized_state(bt: pd.DataFrame) -> dict:
    """由回測結果取出「截至最新收盤」已實現的當日損益與回撤。

    engine 的 ret[t] 是持有 t→t+1 的報酬（向前看），所以最新一根的 ret 尚未實現；
    已實現權益 = equity 往後移一格。
    """
    realized_equity = bt['equity'].shift(1).fillna(1.0)
    pnl_today = float(bt['ret'].iloc[-2]) if len(bt) >= 2 else 0.0
    drawdown = float(realized_equity.iloc[-1] / realized_equity.cummax().iloc[-1] - 1)
    return {'pnl_today': pnl_today, 'drawdown': drawdown}


def run_pm_risk(
    df: pd.DataFrame,
    *,
    strategy: str = 'momentum',
    lookback: int = 20,
    long_only: bool = True,
    tx_fee_bps: float,
    tx_tax_bps: float,
    slippage_bps: float,
    pnl_limit: float = -0.06,
    dd_limit: float = -0.15,
) -> tuple[dict, pd.DataFrame]:
    """回傳 (summary, backtest_df)。summary 只含可序列化的值。"""
    eff_lookback = lookback
    if len(df) < lookback + 2:
        eff_lookback = max(1, len(df) // 3)
    positions = build_positions(df, strategy, eff_lookback, long_only)
    bt = backtest_engine(df, positions, tx_fee_bps, tx_tax_bps, slippage_bps)
    metrics = basic_report(bt)
    state = realized_state(bt)
    halt = should_halt(state['pnl_today'], state['drawdown'], pnl_limit=pnl_limit, dd_limit=dd_limit)
    reasons = []
    if state['pnl_today'] <= pnl_limit:
        reasons.append(f"當日損益 {state['pnl_today']:.2%} <= 上限 {pnl_limit:.0%}")
    if state['drawdown'] <= dd_limit:
        reasons.append(f"回撤 {state['drawdown']:.2%} <= 上限 {dd_limit:.0%}")

    summary = {
        'strategy': strategy,
        'lookback': eff_lookback,
        'lookback_requested': lookback,
        'long_only': long_only,
        'as_of': pd.to_datetime(df.index[-1]).strftime('%Y-%m-%d'),
        'last_close': float(df['close'].iloc[-1]),
        'current_weight': float(positions.iloc[-2]) if len(positions) >= 2 else 0.0,
        'target_weight': float(positions.iloc[-1]),
        'metrics': metrics,
        'realized': state,
        'limits': {'pnl_limit': pnl_limit, 'dd_limit': dd_limit},
        'kill_switch': {'halt': bool(halt), 'reasons': reasons},
    }
    return summary, bt
