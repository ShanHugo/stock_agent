#!/usr/bin/env python3
"""2～4 週短期預測驗證：臺灣50「當時」成分股、線性排名模型、逐年重新訓練、每 10 個交易日調整。

使用範例:
  python scripts/shortterm_lab.py
報告輸出：reports/shortterm_lab_YYYYMMDD.md
需要先回補 2018 年起的三大法人與融資資料（有快取）。
"""
from __future__ import annotations
import os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import pandas as pd

from src.app.lab.universe import fetch_universe
from src.app.lab.data import build_dataset, load_prices, load_chips
from src.app.lab.engine import FEE, TAX, SLIPPAGE
from src.app.lab.features import FEATURES, HORIZON, raw_panels
from src.app.lab.shortterm import run_shortterm, REBALANCE_EVERY, TOP_N, MDD_TOLERANCE
from src.app.lab.constituents import scan_changes, membership_history, members_on

DATA_START = '2015-01-01'
CHIPS_START = '2018-01-01'
TRAIN_START = '2018-04-01'
OOS_YEARS = list(range(2020, date.today().year + 1))
FEATURE_NAMES = {'r5': '近5日報酬', 'r20': '近20日報酬', 'r60': '近60日報酬', 'vol20': '20日波動',
                 'vratio': '量能比', 'foreign20': '外資20日買賣超', 'trust20': '投信20日買賣超',
                 'margin20': '融資20日變化', 'rev_yoy': '營收年增率', 'pe_pct': '本益比分位'}


def pct(v):
    return f'{v:+.1%}'


def render(r, changes, chip_coverage, today) -> str:
    L = [f'# 短期（2～4 週）預測驗證報告（{today}）', '', '## 結論', '']
    L.append(f"**{'✅ 通過' if r.passed else '❌ 未通過'}**" + ('' if r.passed else '：' + '；'.join(r.reasons)))
    L += ['', '通過標準（看結果之前就定好）：', '',
          '1. 樣本外預測與實際未來 20 日報酬的排名相關（IC）平均 > 0，且 t 值 > 2（不重疊期間）',
          f'2. 扣除成本後，年化報酬與 Sharpe 同時高於買進持有 0050 與同範圍等權重持有；最大回撤不比 0050 差超過 {MDD_TOLERANCE:.0%}', '']
    if not r.passed:
        L.append('**這代表模型的預測值不足以當作選股依據；不會接上每日推送。**')
    L += ['', '## 預測能力（IC）', '',
          f'- 樣本外平均 IC：{r.ic_mean:+.3f}（{len(r.ic)} 個調整日），不重疊期間 t 值：{r.ic_t:.2f}',
          f'- IC 為正的比例：{(r.ic > 0).mean():.0%}',
          '- 參考：IC 0 代表和亂猜一樣；專業量化常見的有效訊號約在 +0.02～+0.05，而且很容易隨時間失效', '',
          '## 投組結果（樣本外）', '',
          '| | 年化報酬 | 波動 | Sharpe | 最大回撤 |', '|---|---|---|---|---|']
    for label, m in [(f'模型前 {TOP_N} 名', r.oos), ('買進持有 0050', r.bench), ('同範圍等權重', r.equal_weight)]:
        L.append(f"| {label} | {pct(m['cagr'])} | {m['vol']:.1%} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
    L += ['', f'每次調整平均換手 {r.avg_turnover:.0%}（單邊），交易成本每年約吃掉 {r.cost_per_year:.2%}', '',
          '| 年度 | 模型 | 0050 | 等權重 | 平均 IC |', '|---|---|---|---|---|']
    for y, row in r.yearly.iterrows():
        L.append(f"| {y} | {pct(row['模型'])} | {pct(row['0050'])} | {pct(row['等權重'])} | {row['平均IC']:+.3f} |")
    L += ['', '## 各年模型係數（正 = 該特徵排名越高，預測未來越強）', '',
          '| 年度 | ' + ' | '.join(FEATURE_NAMES[f] for f in FEATURES) + ' |',
          '|---|' + '---|' * len(FEATURES)]
    for y, c in r.coefs.items():
        L.append(f'| {y} | ' + ' | '.join(f'{c[f]:+.3f}' for f in FEATURES) + ' |')
    L += ['', '## 測試設定', '',
          f"- 選股範圍：臺灣50指數「當時」的成分股（由 {len(changes)} 次公告變動重建）",
          f'- 目標：未來 {HORIZON} 個交易日報酬；每 {REBALANCE_EVERY} 個交易日調整，持有預測前 {TOP_N} 名、等權重',
          f'- 模型：線性排名迴歸（ridge），每年用「當年開始前已實現結果」的資料重新訓練；訓練資料自 {TRAIN_START}',
          f'- 成本：手續費買賣各 {FEE:.4%}、證交稅賣出 {TAX:.1%}、滑價買賣各 {SLIPPAGE:.2%}',
          f'- 法人與融資資料涵蓋率：{chip_coverage:.0%} 的交易日', '',
          '## 限制', '',
          '- 新聞沒有包含在內：歷史新聞無法取得，新聞的效果要靠前瞻測試（每天記錄、事後比對）。',
          '- 下市股票若無價格資料無法被選入；金融股缺 EPS 不影響本模型（未使用 EPS）。',
          '- 過去有效不代表未來有效。本報告僅為驗證，非投資建議。']
    return '\n'.join(L) + '\n'


def main() -> int:
    today = date.today()
    universe = fetch_universe(['0050', '00881'])
    changes = [c for c in scan_changes(470) if c['effective']]
    history = membership_history(set(universe['etfs']['0050']['codes']), changes)
    codes = sorted(set().union(*[m for _, m in history]))
    ds = build_dataset(codes, start=DATA_START, today=today, progress=lambda m: print(f'[data] {m}', file=sys.stderr))
    end = ds.prices.index[-1]
    volume = load_prices(codes, DATA_START, (end + pd.Timedelta(days=1)).strftime('%Y-%m-%d'), field='Volume')
    chip_days = ds.prices.loc[CHIPS_START:].index
    chips = load_chips(codes, chip_days)
    coverage = chips['foreign'].dropna(how='all').shape[0] / max(len(chip_days), 1)
    print(f'[data] 法人資料涵蓋 {coverage:.0%} 交易日', file=sys.stderr)
    panels = raw_panels(ds, volume, chips, codes)
    r = run_shortterm(panels, ds.prices, lambda d: members_on(history, d), train_start=TRAIN_START,
                      oos_years=OOS_YEARS, end=end)
    report = render(r, changes, coverage, today)
    out = _ROOT / 'reports' / f'shortterm_lab_{today:%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding='utf-8')
    print(report)
    print(f'[saved] {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
