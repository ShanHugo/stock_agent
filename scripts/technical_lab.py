#!/usr/bin/env python3
"""技術面訊號研究：均線、RSI、MACD、布林通道、突破、量能、K 線型態，哪些訊號之後真的比較會漲（或跌）。

範圍：上市＋上櫃電子股「當時」的公司（含之後下市者），2016 年起。
需要開高低價：先跑 scripts/prefetch_ohlc.py 下載（舊行情快取只有收盤價與成交量）。
報告輸出：reports/technical_lab_YYYYMMDD.md。只是研究，沒有通過驗證的訊號不會接上每日推送。

使用範例:
  python scripts/technical_lab.py
"""
from __future__ import annotations
import argparse, os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import pandas as pd

from src.app.lab.constituents import members_on
from src.app.lab.data import BENCHMARK
from src.app.lab.market import UNIVERSES, CACHE, build_market_dataset, describe, ohlc_day, raw_panels
from src.app.lab import technical as tech

DATA_START = '2015-01-01'
EVAL_START = '2016-01-01'     # 前一年留給 250 日新高等指標暖身


def field_panel(markets, days: pd.DatetimeIndex, field: str, source) -> pd.DataFrame:
    """逐日行情的某個欄位 → 面板（同代號兩邊都有時以證交所為準，與 market.raw_panels 一致）。"""
    rows = {}
    for d in days:
        parts = [source(m, d.date()) for m in reversed(markets)]
        df = pd.concat([p for p in parts if not p.empty]).drop_duplicates('code', keep='last').set_index('code')
        rows[d] = df[field]
    return pd.DataFrame(rows).T.sort_index()


def ohlc_ready(markets, days) -> float:
    have = sum((CACHE / m / 'ohlc' / f'{d:%Y%m%d}.json').exists() for m in markets for d in days)
    return have / (len(markets) * len(days))


def pct(v):
    return f'{v:+.2%}'


def render(results, scope_detail, today, with_hl, notes) -> str:
    L = [f'# 技術面訊號研究（{today}）', '',
         '## 判定標準（看結果之前就定好）', '',
         f'- 事件數 ≥ {tech.MIN_EVENTS}、不重疊區塊 ≥ {tech.MIN_BLOCKS}',
         f'- **有效（偏多）**：t ≥ {tech.T_PASS:.0f}、前後半期方向一致，且扣掉一次來回成本（{tech.ROUND_TRIP:.2%}）後超額仍 > 0',
         f'- **有效（偏空）**：t ≤ -{tech.T_PASS:.0f} 且前後半期方向一致（不能放空，用途是「出現就避開或出場」）',
         f'- t 門檻用 {tech.T_PASS:.0f} 不用 2：同時檢驗 {len(set(r.signal for r in results))} 個訊號 × {len(tech.HORIZONS)} 種持有天數，'
         '用 2 的話光靠運氣就會有好幾個「顯著」', '']
    for h in tech.HORIZONS:
        rows = sorted([r for r in results if r.horizon == h], key=lambda r: -r.t)
        good = [r for r in rows if r.verdict.startswith('有效')]
        L += [f'## 持有 {h} 個交易日', '']
        if good:
            L.append('**通過：** ' + '；'.join(f"{tech.SIGNALS[r.signal][0]}（{r.verdict}，超額 {pct(r.excess)}、t {r.t:+.1f}）" for r in good))
        else:
            L.append('**沒有任何訊號通過。**')
        L += ['', '| 訊號 | 類別 | 事件數 | 平均超額 | 扣成本 | t 值 | 勝率 | 前半期 | 後半期 | 比 0050 | 隔天開盤漲停 | 判定 |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|']
        for r in rows:
            name, cat, _ = tech.SIGNALS[r.signal]
            L.append(f'| {name} | {cat} | {r.events:,} | {pct(r.excess)} | {pct(r.excess_net)} | {r.t:+.2f} | {r.hit:.0%} | '
                     f'{pct(r.first_half)} | {pct(r.second_half)} | {pct(r.vs_0050)} | {r.unbuyable:.1%} | {r.verdict} |')
        L.append('')
    L += ['## 怎麼讀', '',
          '- **平均超額**：出現訊號的股票，之後比同一段時間「範圍內全部股票平均」多漲（正）或少漲（負）多少。',
          '- **扣成本**：只對偏多訊號有意義（真的買賣一次要付的成本）。',
          '- **勝率**：個別事件中跑贏全體平均的比例；超額為正但勝率低於 50%，代表靠少數大漲撐起來。',
          '- **前半期 / 後半期**：把期間對半切，方向不一致代表規律不穩定。',
          '- **隔天開盤漲停**：這些事件實際上很可能買不到，研究結果會高估。', '',
          '## 訊號定義', '', '| 訊號 | 類別 | 定義 |', '|---|---|---|']
    for k, (name, cat, desc) in tech.SIGNALS.items():
        if k in tech.NEEDS_HL and not with_hl:
            continue
        L.append(f'| {name} | {cat} | {desc} |')
    L += ['', '## 測試設定', '',
          f'- 範圍：{scope_detail}',
          f'- 期間：{EVAL_START} 起（{DATA_START} 起的資料用來計算指標）',
          '- 進出場：訊號在當天收盤後才知道 → 隔天開盤進場，第 H 個交易日收盤出場；價格為自行還原權值後的價格',
          '- t 值：每天取訊號股平均超額，依持有天數切成不重疊區塊再做 t 檢定（避免重疊期間被重複計算）',
          f'- K 線型態（需要最高、最低價）：{"已納入" if with_hl else "未納入（最高、最低價尚未下載完成）"}']
    L += [f'- 資料備註：{n}' for n in notes]
    L += ['', '## 限制', '',
          '- 過去有效不代表未來有效；訊號被越多人使用，效果通常越弱。',
          '- 這是「出現訊號的股票平均表現」，不是完整的交易策略（沒有考慮資金分配、同時持有多檔）。',
          '- 本報告僅為研究，非投資建議。']
    return '\n'.join(L) + '\n'


def main() -> int:
    argparse.ArgumentParser(description='技術面訊號研究').parse_args()
    today = date.today()
    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr, flush=True)
    markets = UNIVERSES['electronics']
    built = build_market_dataset(markets, start=DATA_START, chips_start=None, today=today, log=log)
    ds, history, codes = built['ds'], built['history'], built['codes']
    _, scope_detail = describe(markets, built)
    px = ds.prices
    days = px.index
    cols = [c for c in codes if c in px.columns]

    ready = ohlc_ready(markets, days)
    if ready < 0.99:
        log(f'[error] 開高低價只下載了 {ready:.0%}（舊行情快取沒有開盤價），先跑完 scripts/prefetch_ohlc.py')
        return 1
    log('[data] 組合開高低價面板（以還原係數調整）')
    raw_close, _ = raw_panels(markets, days)
    factor = px[cols] / raw_close.reindex(index=days, columns=cols)          # 還原係數
    open_, high, low = (field_panel(markets, days, f, ohlc_day).reindex(index=days, columns=cols) * factor
                        for f in ('open', 'high', 'low'))
    volume = built['volume'].reindex(index=days, columns=cols)

    log('[test] 計算訊號')
    close = px[cols]
    open_ = open_.where(open_ > 0)
    signals = tech.signal_panels(close, open_, volume, high.where(high > 0), low.where(low > 0))
    member = pd.DataFrame(False, index=days, columns=cols)
    for (start, m), nxt in zip(history, history[1:] + [(days[-1] + pd.Timedelta(days=1), None)]):
        idx = (days >= start) & (days < nxt[0])
        member.loc[idx, [c for c in m if c in cols]] = True
    log('[test] 事件研究')
    results = tech.evaluate(signals, close, open_, member, px[BENCHMARK], start=EVAL_START)
    report = render(results, scope_detail, today, True, ds.notes)
    out = _ROOT / 'reports' / f'technical_lab_{today:%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding='utf-8')
    print(report)
    print(f'[saved] {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
