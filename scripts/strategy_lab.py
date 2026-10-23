#!/usr/bin/env python3
"""規則驗證：在選定的選股範圍上，逐年前進驗證三類選股規則，與買進持有 0050 比較。

使用範例:
  python scripts/strategy_lab.py                       # 三類規則全部驗證
  python scripts/strategy_lab.py --families trend_following
  python scripts/strategy_lab.py --universe electronics-twse  # 只看上市電子
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


from src.app.lab.engine import FEE, TAX, SLIPPAGE
from src.app.lab.rules import GRID, NAMES
from src.app.lab.walkforward import evaluate_family, MDD_TOLERANCE
from src.app.lab.constituents import members_on
from src.app.lab.market import UNIVERSES, build_market_dataset, describe

DATA_START = '2015-01-01'
TRADE_START = '2017-01-01'       # 前兩年留給均線與本益比歷史暖身
OOS_YEARS = list(range(2020, date.today().year + 1))


def pct(v):
    return f'{v:+.1%}'


def _beats_ew(r):
    return r.oos['cagr'] > r.equal_weight['cagr'] and r.oos['sharpe'] > r.equal_weight['sharpe']


def render(results, ds, today, *, scope) -> str:
    L = [f"# 選股規則驗證報告（{today}，{scope[0]}時點名單）", '', '## 結論', '']
    for r in results:
        verdict = '✅ 通過' if r.passed else '❌ 未通過'
        L.append(f"- **{NAMES[r.family]}**：{verdict}（對 0050）"
                 + ('' if r.passed else '：' + '；'.join(r.reasons))
                 + f"；{'勝過' if _beats_ew(r) else '沒有勝過'}同範圍等權重持有")
    L += ['', f'原定標準：樣本外年化報酬與 Sharpe 都高於買進持有 0050，且最大回撤不比 0050 差超過 {MDD_TOLERANCE:.0%}。',
          '補充標準（第一次驗證後補上）：也要勝過「同一選股範圍等權重持有、不用任何規則」，'
          '否則勝過 0050 的原因是選股範圍而不是規則本身。', '']
    good = [r for r in results if r.passed and _beats_ew(r)]
    if good:
        L.append('**同時通過兩項標準：' + '、'.join(NAMES[r.family] for r in good) + '。'
                 '仍需注意下方限制，建議先以小額或模擬方式觀察。**')
    else:
        L.append('**沒有任何規則同時通過兩項標準：這些規則扣除交易成本後，沒有證明比直接持有同一群股票更好，'
                 '不建議接上每日提醒拿真錢操作。**')

    L += ['', '## 測試設定', '']
    L += [f'- 選股範圍：{scope[1]}']
    L += [f'- 期間：{TRADE_START} 起交易，樣本外 {OOS_YEARS[0]} 年至今；每年只用之前的資料挑參數',
          '- 每月最後一個交易日收盤依訊號調整，選中的股票等權重，沒選中的部分持有現金（報酬以 0 計）',
          f'- 成本：手續費買賣各 {FEE:.4%}、證交稅賣出 {TAX:.1%}、滑價買賣各 {SLIPPAGE:.2%}（未計券商折扣，偏保守）',
          '- 價格使用還原權值（含配息），基準 0050 同樣用還原價（自行由證交所／櫃買每日收盤價與除權息、減資紀錄還原）',
          '- 月營收於次月 11 日起才使用；季報依法定期限（Q1 5/16、Q2 8/15、Q3 11/15、年報次年 4/1）起才使用']
    L += [f'- 資料備註：{n}' for n in ds.notes]

    ew_label = '同範圍等權重持有（每月再平衡）'
    for r in results:
        L += ['', f'## {NAMES[r.family]}', '',
              '| 樣本外 | 年化報酬 | 波動 | Sharpe | 最大回撤 |', '|---|---|---|---|---|']
        for label, m in [('規則', r.oos), ('買進持有 0050', r.bench), (ew_label, r.equal_weight)]:
            L.append(f"| {label} | {pct(m['cagr'])} | {m['vol']:.1%} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
        L += ['', f"平均持股 {r.avg_holdings:.1f} 檔，每年換手約 {r.turnover_per_year:.0%}（單邊），"
                  f"交易成本每年約吃掉 {r.cost_per_year:.2%}", '',
              '| 年度 | 規則 | 0050 | 當年採用參數 |', '|---|---|---|---|']
        for y, row in r.yearly.iterrows():
            L.append(f"| {y} | {pct(row['策略'])} | {pct(row['0050'])} | {row['採用參數']} |")

    L += ['', '## 這份報告的限制', '']
    L.append('- 已納入期間內下市的公司；但它們沒有月營收與財報資料，營收、估值類規則選不到它們'
             '（等權重持有則會持有），這部分仍可能讓營收、估值類規則略為偏樂觀。')
    L += [f"- **多重測試**：共比較 {sum(len(GRID[r.family]) for r in results)} 組參數；比越多組，越容易碰巧出現好看的結果。",
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
    p.add_argument('--universe', default='electronics', choices=list(UNIVERSES),
                   help='electronics = 上市＋上櫃電子 8 類當時的公司（含下市）；electronics-twse / electronics-tpex = 只看上市 / 上櫃')
    args = p.parse_args()
    today = date.today()
    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr)
    built = build_market_dataset(UNIVERSES[args.universe], start=DATA_START, chips_start=None, today=today, log=log)
    ds, codes, history = built['ds'], built['codes'], built['history']
    universe_fn = lambda d: members_on(history, d)
    scope = describe(UNIVERSES[args.universe], built)
    end = ds.prices.index[-1]
    results = []
    for fam in args.families:
        print(f'[test] {NAMES[fam]}', file=sys.stderr)
        results.append(evaluate_family(ds, codes, fam, start=TRADE_START, oos_years=OOS_YEARS, end=end,
                                       universe_fn=universe_fn))
    report = render(results, ds, today, scope=scope)
    out = _ROOT / 'reports' / f'strategy_lab_{today:%Y%m%d}_{args.universe}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding='utf-8')
    print(report)
    print(f'[saved] {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
