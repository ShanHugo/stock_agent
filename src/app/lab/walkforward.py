"""逐年前進驗證（anchored walk-forward）：每年只用該年以前的資料挑參數，套用到當年，串起來就是樣本外結果。

通過標準（看結果之前就定好）：樣本外的年化報酬與 Sharpe 都高於買進持有 0050，且最大回撤不比 0050 差超過 5 個百分點。
"""
from __future__ import annotations
from dataclasses import dataclass, field

import pandas as pd

from .data import Dataset, BENCHMARK
from .engine import run_backtest, buy_and_hold, metrics
from .rules import GRID, BUILDERS

MDD_TOLERANCE = 0.05


@dataclass
class FamilyResult:
    family: str
    oos_returns: pd.Series
    chosen: dict                       # 年份 → 當年採用的參數
    oos: dict
    bench: dict
    equal_weight: dict
    passed: bool
    reasons: list[str]
    yearly: pd.DataFrame
    full_runs: dict = field(default_factory=dict)   # 參數字串 → 全期間 metrics（樣本內參考）
    avg_holdings: float = 0.0
    turnover_per_year: float = 0.0
    cost_per_year: float = 0.0


def _key(p: dict) -> str:
    return ', '.join(f'{k}={v}' for k, v in p.items())


def evaluate_family(ds: Dataset, codes: list[str], family: str, *, start: str, oos_years: list[int],
                    end: pd.Timestamp) -> FamilyResult:
    runs = {}
    for params in GRID[family]:
        sig = BUILDERS[family](ds, codes, **params)
        runs[_key(params)] = (params, run_backtest(ds.prices, sig, pd.Timestamp(start), end))

    pieces, chosen = [], {}
    for y in oos_years:
        train_end = pd.Timestamp(f'{y - 1}-12-31')
        best = max(runs.values(), key=lambda pr: metrics(pr[1].returns.loc[:train_end]).get('sharpe', -9))
        chosen[y] = best[0]
        pieces.append(best[1].returns.loc[f'{y}-01-01':f'{y}-12-31'])
    oos = pd.concat(pieces)
    oos_start, oos_end = oos.index[0], oos.index[-1]

    bench_r = buy_and_hold(ds.prices[BENCHMARK], oos_start, oos_end)
    ew = run_backtest(ds.prices, lambda d: codes, pd.Timestamp(start), end).returns.loc[oos_start:oos_end]
    m, b, e = metrics(oos), metrics(bench_r), metrics(ew)

    reasons = []
    if m['cagr'] <= b['cagr']:
        reasons.append(f"年化報酬 {m['cagr']:.1%} 未高於 0050 的 {b['cagr']:.1%}")
    if m['sharpe'] <= b['sharpe']:
        reasons.append(f"Sharpe {m['sharpe']:.2f} 未高於 0050 的 {b['sharpe']:.2f}")
    if m['max_drawdown'] < b['max_drawdown'] - MDD_TOLERANCE:
        reasons.append(f"最大回撤 {m['max_drawdown']:.1%} 比 0050 的 {b['max_drawdown']:.1%} 差超過 5 個百分點")

    yearly = pd.DataFrame({
        '策略': oos.groupby(oos.index.year).apply(lambda r: (1 + r).prod() - 1),
        '0050': bench_r.groupby(bench_r.index.year).apply(lambda r: (1 + r).prod() - 1),
    })
    yearly['採用參數'] = [_key(chosen[y]) for y in yearly.index]

    # 樣本外期間實際採用參數的持股數、換手與成本
    hold, turn, cost = [], 0.0, 0.0
    for y, params in chosen.items():
        res = runs[_key(params)][1]
        w = res.weights.loc[f'{y}-01-01':f'{y}-12-31']
        hold += [(row > 0).sum() for _, row in w.iterrows()]
        turn += res.turnover.loc[f'{y}-01-01':f'{y}-12-31'].sum()
        cost += res.costs.loc[f'{y}-01-01':f'{y}-12-31'].sum()
    years = max(len(oos) / 252, 1e-9)
    return FamilyResult(
        family=family, oos_returns=oos, chosen=chosen, oos=m, bench=b, equal_weight=e,
        passed=not reasons, reasons=reasons, yearly=yearly,
        full_runs={k: metrics(r.returns) for k, (_, r) in runs.items()},
        avg_holdings=float(sum(hold) / len(hold)) if hold else 0.0,
        turnover_per_year=turn / years, cost_per_year=cost / years,
    )
