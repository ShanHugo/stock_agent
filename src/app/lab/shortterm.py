"""2～4 週短期預測：線性排名模型 + 逐年重新訓練 + 每 10 個交易日調整持股。

通過標準（看結果之前就定好）：
  1. 樣本外預測與實際未來 20 日報酬的排名相關（IC）平均 > 0，且 t 值 > 2（用不重疊的期間計算）
  2. 扣除成本後，年化報酬與 Sharpe 同時高於買進持有 0050，以及同範圍等權重持有；
     最大回撤不比 0050 差超過 5 個百分點
"""
from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .engine import run_backtest, buy_and_hold, metrics
from .features import FEATURES, HORIZON, cross_section, rank_score
from .data import BENCHMARK

REBALANCE_EVERY = 10
TOP_N = 10
SAMPLE_EVERY = 5      # 訓練樣本每 5 個交易日取一次，降低重疊
RIDGE = 1.0
MDD_TOLERANCE = 0.05


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float = RIDGE) -> np.ndarray:
    k = X.shape[1]
    return np.linalg.solve(X.T @ X + lam * len(X) / 100 * np.eye(k), X.T @ y)


@dataclass
class ShortTermResult:
    oos_returns: pd.Series
    ic: pd.Series                      # 每個調整日的樣本外 IC
    ic_mean: float
    ic_t: float                        # 不重疊期間的 t 值
    coefs: dict                        # 年份 → 各特徵係數
    oos: dict
    bench: dict
    equal_weight: dict
    passed: bool
    reasons: list[str]
    yearly: pd.DataFrame
    avg_turnover: float = 0.0
    cost_per_year: float = 0.0
    predictions: dict = field(default_factory=dict)   # 調整日 → 預測排名（最後一期可做前瞻紀錄）


def run_shortterm(panels: dict, prices: pd.DataFrame, universe_fn, *, train_start: str, oos_years: list[int],
                  end: pd.Timestamp) -> ShortTermResult:
    days = prices.loc[train_start:end].index
    days = days[days >= panels['r60'].first_valid_index()]

    def members(d):
        return [c for c in universe_fn(d) if c in prices.columns and pd.notna(prices.at[d, c])]

    # 樣本：每 SAMPLE_EVERY 天一個橫斷面
    sample_days = days[::SAMPLE_EVERY]
    samples = {d: cross_section(panels, d, members(d)) for d in sample_days}

    coefs, preds = {}, {}
    rebal_days = []
    for y in oos_years:
        year_start = pd.Timestamp(f'{y}-01-01')
        # 目標要在當年開始前就已實現：樣本日 + 20 個交易日 < 當年第一天
        cutoff_idx = days.searchsorted(year_start) - HORIZON - 1
        if cutoff_idx <= 0:
            continue
        cutoff = days[cutoff_idx]
        train = [s.assign(y=rank_score(s['fwd'])) for d, s in samples.items() if d <= cutoff and s['fwd'].notna().sum() > 10]
        if not train:
            continue
        T = pd.concat(train)
        w = fit_ridge(T[FEATURES].to_numpy(), T['y'].to_numpy())
        coefs[y] = dict(zip(FEATURES, np.round(w, 4)))
        ydays = days[(days >= year_start) & (days <= pd.Timestamp(f'{y}-12-31'))]
        for d in ydays[::REBALANCE_EVERY]:
            cs = cross_section(panels, d, members(d))
            preds[d] = pd.Series(cs[FEATURES].to_numpy() @ w, index=cs.index).sort_values(ascending=False)
            rebal_days.append(d)

    def signal(d):
        p = preds.get(d)
        return list(p.index[:TOP_N]) if p is not None else []

    rebal = pd.DatetimeIndex(rebal_days)
    oos_start = rebal[0]
    res = run_backtest(prices, signal, oos_start, end, rebalance_dates=rebal)
    ew = run_backtest(prices, lambda d: members(d), oos_start, end, rebalance_dates=rebal)
    oos = res.returns
    bench_r = buy_and_hold(prices[BENCHMARK], oos_start, end)

    # IC：預測與實際未來 20 日報酬的排名相關
    ic = {}
    for d, p in preds.items():
        fwd = panels['target'].loc[d, p.index]
        ok = fwd.notna()
        if ok.sum() > 10:
            ic[d] = float(p[ok].rank().corr(fwd[ok].rank()))
    ic = pd.Series(ic).sort_index()
    non_overlap = ic.iloc[::max(1, HORIZON // REBALANCE_EVERY)]
    ic_t = float(non_overlap.mean() / non_overlap.std() * np.sqrt(len(non_overlap))) if len(non_overlap) > 2 else 0.0

    m, b, e = metrics(oos), metrics(bench_r), metrics(ew.returns)
    reasons = []
    if not (ic.mean() > 0 and ic_t > 2):
        reasons.append(f'預測能力不顯著：平均 IC {ic.mean():+.3f}，t 值 {ic_t:.2f}（需 > 2）')
    for label, ref in [('0050', b), ('同範圍等權重', e)]:
        if m['cagr'] <= ref['cagr']:
            reasons.append(f"年化報酬 {m['cagr']:.1%} 未高於{label}的 {ref['cagr']:.1%}")
        if m['sharpe'] <= ref['sharpe']:
            reasons.append(f"Sharpe {m['sharpe']:.2f} 未高於{label}的 {ref['sharpe']:.2f}")
    if m['max_drawdown'] < b['max_drawdown'] - MDD_TOLERANCE:
        reasons.append(f"最大回撤 {m['max_drawdown']:.1%} 比 0050 差超過 5 個百分點")

    yearly = pd.DataFrame({
        '模型': oos.groupby(oos.index.year).apply(lambda r: (1 + r).prod() - 1),
        '0050': bench_r.groupby(bench_r.index.year).apply(lambda r: (1 + r).prod() - 1),
        '等權重': ew.returns.groupby(ew.returns.index.year).apply(lambda r: (1 + r).prod() - 1),
        '平均IC': ic.groupby(ic.index.year).mean(),
    })
    years = max(len(oos) / 252, 1e-9)
    return ShortTermResult(
        oos_returns=oos, ic=ic, ic_mean=float(ic.mean()), ic_t=ic_t, coefs=coefs, oos=m, bench=b, equal_weight=e,
        passed=not reasons, reasons=reasons, yearly=yearly,
        avg_turnover=float(res.turnover.mean()), cost_per_year=float(res.costs.sum() / years), predictions=preds)
