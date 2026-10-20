#!/usr/bin/env python3
"""規則驗證：在 0050 + 00881 成分股（上市部分）上，逐年前進驗證三類選股規則，與買進持有 0050 比較。

使用範例:
  python scripts/strategy_lab.py                       # 三類規則全部驗證
  python scripts/strategy_lab.py --families trend_following
報告輸出：reports/strategy_lab_YYYYMMDD.md
第一次執行需回補約 10 年資料（有快取，之後很快）。
"""
from __future__ import annotations
import argparse, os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import pandas as pd

from src.app.lab.universe import fetch_universe
from src.app.lab.data import build_dataset
from src.app.lab.engine import FEE, TAX, SLIPPAGE
from src.app.lab.rules import GRID, NAMES
from src.app.lab.walkforward import evaluate_family, MDD_TOLERANCE

ETFS = ['0050', '00881']
DATA_START = '2015-01-01'
TRADE_START = '2017-01-01'       # 前兩年留給均線與本益比歷史暖身
OOS_YEARS = list(range(2020, date.today().year + 1))


def pct(v):
    return f'{v:+.1%}'


def render(results, ds, universe, today) -> str:
    L = [f'# 選股規則驗證報告（{today}）', '']
    L += ['## 結論', '']
    for r in results:
        verdict = '✅ 通過' if r.passed else '❌ 未通過'
        L.append(f"- **{NAMES[r.family]}**：{verdict}" + ('' if r.passed else '（' + '；'.join(r.reasons) + '）'))
    L += ['', f'通過標準（驗證前就定好）：樣本外年化報酬與 Sharpe 都高於買進持有 0050，'
              f'且最大回撤不比 0050 差超過 {MDD_TOLERANCE:.0%}。', '']
    # 事後補充（非原本標準）：規則是否勝過「同一群股票等權重持有、不用任何規則」
    L += ['### 重要補充：與「同一群股票直接持有」比較', '',
          '原本的標準只和 0050 比，漏掉了更公平的對照組：不用任何規則、57 檔成分股等權重持有。'
          '若規則連它都贏不了，代表勝過 0050 的原因是選股範圍（現在的成分股＝過去的贏家），而不是規則本身。', '']
    for r in results:
        beat = r.oos['cagr'] > r.equal_weight['cagr'] and r.oos['sharpe'] > r.equal_weight['sharpe']
        L.append(f"- **{NAMES[r.family]}**：{'勝過' if beat else '沒有勝過'}等權重持有"
                 f"（年化 {r.oos['cagr']:+.1%} vs {r.equal_weight['cagr']:+.1%}，"
                 f"Sharpe {r.oos['sharpe']:.2f} vs {r.equal_weight['sharpe']:.2f}）")
    if not any(r.oos['cagr'] > r.equal_weight['cagr'] and r.oos['sharpe'] > r.equal_weight['sharpe'] for r in results):
        L += ['', '**結論：沒有任何規則證明自己有選股能力；扣除交易成本後不如直接持有同一群股票。'
                  '不建議把這些規則接上每日提醒操作。**']
    L.append('')
    if not any(r.passed for r in results):
        L += ['**沒有任何規則通過。這代表在這段期間、這個選股範圍裡，這些規則扣掉成本後沒有比直接持有 0050 更好，'
              '不應該接上每日提醒拿真錢操作。**', '']

    L += ['## 測試設定', '',
          f"- 選股範圍：0050 與 00881 目前成分股中的上市公司，共 {len(universe['codes'])} 檔"
          f"（排除上櫃：{'、'.join(universe['unmatched']) or '無'}；資料日期 "
          f"{', '.join(v['date'] or '?' for v in universe['etfs'].values())}）",
          f'- 期間：{TRADE_START} 起交易，樣本外 {OOS_YEARS[0]} 年至今；每年只用之前的資料挑參數',
          '- 每月最後一個交易日收盤依訊號調整，選中的股票等權重，沒選中的部分持有現金（報酬以 0 計）',
          f'- 成本：手續費買賣各 {FEE:.4%}、證交稅賣出 {TAX:.1%}、滑價買賣各 {SLIPPAGE:.2%}（未計券商折扣，偏保守）',
          '- 價格使用還原權值（含配息），基準 0050 同樣用還原價',
          '- 月營收於次月 11 日起才使用；季報依法定期限（Q1 5/16、Q2 8/15、Q3 11/15、年報次年 4/1）起才使用', '']
    for n in ds.notes:
        L.append(f'- 資料備註：{n}')

    for r in results:
        L += ['', f'## {NAMES[r.family]}', '',
              '| 樣本外 | 年化報酬 | 波動 | Sharpe | 最大回撤 |', '|---|---|---|---|---|']
        for label, m in [('規則', r.oos), ('買進持有 0050', r.bench), ('成分股等權重（每月再平衡）', r.equal_weight)]:
            L.append(f"| {label} | {pct(m['cagr'])} | {m['vol']:.1%} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
        L += ['', f"平均持股 {r.avg_holdings:.1f} 檔，每年換手約 {r.turnover_per_year:.0%}（單邊），"
                  f"交易成本每年約吃掉 {r.cost_per_year:.2%}", '',
              '| 年度 | 規則 | 0050 | 當年採用參數 |', '|---|---|---|---|']
        for y, row in r.yearly.iterrows():
            L.append(f"| {y} | {pct(row['策略'])} | {pct(row['0050'])} | {row['採用參數']} |")

    L += ['', '## 這份報告的限制', '',
          '- **倖存者偏差**：用的是現在的成分股，回測期間被剔除的公司不在名單內，結果偏樂觀。',
          f"- **多重測試**：共比較 {sum(len(GRID[r.family]) for r in results)} 組參數；比越多組，越容易碰巧出現好看的結果。",
          '- 每年換參數時的額外交易成本未計入；現金報酬以 0 計；滑價為估計值。',
          '- 過去有效不代表未來有效。本報告僅為規則驗證，非投資建議。', '',
          '## 附錄：各參數全期間結果（含樣本內，僅供參考，不可當作預期報酬）', '',
          '| 規則 | 參數 | 年化報酬 | Sharpe | 最大回撤 |', '|---|---|---|---|---|']
    for r in results:
        for k, m in r.full_runs.items():
            L.append(f"| {NAMES[r.family]} | {k} | {pct(m['cagr'])} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
    return '\n'.join(L) + '\n'


def main() -> int:
    p = argparse.ArgumentParser(description='選股規則驗證')
    p.add_argument('--families', nargs='+', default=list(GRID), choices=list(GRID))
    args = p.parse_args()
    today = date.today()
    universe = fetch_universe(ETFS)
    codes = sorted(universe['codes'])
    ds = build_dataset(codes, start=DATA_START, today=today, progress=lambda m: print(f'[data] {m}', file=sys.stderr))
    end = ds.prices.index[-1]
    results = []
    for fam in args.families:
        print(f'[test] {NAMES[fam]}', file=sys.stderr)
        results.append(evaluate_family(ds, codes, fam, start=TRADE_START, oos_years=OOS_YEARS, end=end))
    report = render(results, ds, universe, today)
    out = _ROOT / 'reports' / f'strategy_lab_{today:%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding='utf-8')
    print(report)
    print(f'[saved] {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
