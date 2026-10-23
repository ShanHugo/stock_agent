#!/usr/bin/env python3
"""籌碼面「起漲／起跌」訊號研究，分兩階段（避免在同一段資料上反覆調整）：

  研究期 2018-06 ～ 2021-12：12 個訊號全部檢驗，通過標準的才進入考卷期。
  考卷期 2022-01 ～ 現在：只考研究期通過的訊號，而且只考一次（結果存檔，之後只顯示存檔結果）。

訊號定義在 src/app/lab/chips_signals.py（2026-10-23 看到結果前訂定，不調整）。
需要開高低價（scripts/prefetch_ohlc.py）與 2018 年起的法人、融資資料（第一次會自動回補）。

使用範例:
  python scripts/chips_lab.py --stage dev        # 研究期 → reports/chips_lab_dev_YYYYMMDD.md
  python scripts/chips_lab.py --stage holdout    # 考卷期 → reports/chips_lab_holdout_YYYYMMDD.md
"""
from __future__ import annotations
import argparse, json, os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import numpy as np
import pandas as pd

from technical_lab import field_panel, ohlc_ready
from src.app.lab.data import BENCHMARK
from src.app.lab.market import UNIVERSES, build_market_dataset, describe, ohlc_day, raw_panels
from src.app.lab import technical as tech
from src.app.lab.chips_signals import SIGNALS

DATA_START = '2015-01-01'
CHIPS_START = '2018-01-01'
DEV_START, DEV_END = '2018-06-01', '2021-12-31'
HOLDOUT_START = '2022-01-01'
HOLDOUT_T = 2.0               # 考卷期只考少數訊號、只考一次，門檻用 2
STATE = _ROOT / 'state' / 'research'
DEV_FILE = STATE / 'chips_dev.json'
HOLDOUT_FILE = STATE / 'chips_holdout.json'


def pct(v):
    return '—' if v is None or v != v else f'{v:+.2%}'


def load(log):
    markets = UNIVERSES['electronics']
    built = build_market_dataset(markets, start=DATA_START, chips_start=CHIPS_START, today=date.today(), log=log)
    ds, history, codes = built['ds'], built['history'], built['codes']
    px = ds.prices
    days = px.index
    cols = [c for c in codes if c in px.columns]
    ready = ohlc_ready(markets, days)
    if ready < 0.99:
        raise SystemExit(f'[error] 開高低價只下載了 {ready:.0%}，先跑 scripts/prefetch_ohlc.py')
    raw_close, _ = raw_panels(markets, days)
    factor = px[cols] / raw_close.reindex(index=days, columns=cols)
    open_ = field_panel(markets, days, 'open', ohlc_day).reindex(index=days, columns=cols) * factor
    volume = built['volume'].reindex(index=days, columns=cols)
    chips = {k: v.reindex(index=days, columns=cols) for k, v in built['chips'].items()}
    member = pd.DataFrame(False, index=days, columns=cols)
    for (start, m), nxt in zip(history, history[1:] + [(days[-1] + pd.Timedelta(days=1), None)]):
        member.loc[(days >= start) & (days < nxt[0]), [c for c in m if c in cols]] = True
    from src.app.lab.chips_signals import signal_panels
    signals = signal_panels(px[cols], volume, chips)
    coverage = {p: float(chips['foreign'].loc[a:b].notna().any(axis=1).mean())
                for p, (a, b) in {'研究期': (DEV_START, DEV_END), '考卷期': (HOLDOUT_START, None)}.items()}
    _, scope = describe(markets, built)
    return signals, px[cols], open_.where(open_ > 0), member, px[BENCHMARK], coverage, scope


def table(results) -> list[str]:
    L = ['| 訊號 | 預期 | 持有 | 事件數 | 平均超額 | 扣成本 | t 值 | 勝率 | 前半 | 後半 | 比 0050 | 判定 |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in sorted(results, key=lambda r: (r.horizon, -r.t)):
        name, _, _, expect = SIGNALS[r.signal]
        L.append(f'| {name} | {expect} | {r.horizon} 天 | {r.events:,} | {pct(r.excess)} | {pct(r.excess_net)} | {r.t:+.2f} | '
                 f'{r.hit:.0%} | {pct(r.first_half)} | {pct(r.second_half)} | {pct(r.vs_0050)} | {r.verdict} |')
    return L


def definitions() -> list[str]:
    L = ['## 訊號定義（2026-10-23 看結果前訂定）', '', '| 訊號 | 類別 | 定義 | 預期 |', '|---|---|---|---|']
    return L + [f'| {n} | {c} | {d} | {e} |' for n, c, d, e in SIGNALS.values()]


def run_dev(log) -> int:
    signals, px, open_, member, bench, coverage, scope = load(log)
    log('[test] 研究期事件研究')
    res = tech.evaluate(signals, px, open_, member, bench, start=DEV_START, end=DEV_END)
    passed = [r for r in res if r.verdict.startswith('有效')]
    STATE.mkdir(parents=True, exist_ok=True)
    DEV_FILE.write_text(json.dumps({
        'date': str(date.today()), 'period': [DEV_START, DEV_END],
        'selected': [{'signal': r.signal, 'horizon': r.horizon, 'sign': int(np.sign(r.t)), 'dev_t': r.t,
                      'dev_excess': r.excess, 'verdict': r.verdict} for r in passed]}, ensure_ascii=False, indent=1),
        encoding='utf-8')
    L = [f'# 籌碼面訊號研究：研究期（{date.today()}）', '',
         f'研究期 {DEV_START} ～ {DEV_END}；考卷期 {HOLDOUT_START} 起的結果在這個階段完全不計算。', '',
         '## 判定標準（看結果前訂定）', '',
         f'- 事件數 ≥ {tech.MIN_EVENTS}、不重疊區塊 ≥ {tech.MIN_BLOCKS}；|t| ≥ {tech.T_PASS:.0f}、研究期前後兩半方向一致；'
         f'偏多訊號扣一次來回成本（{tech.ROUND_TRIP:.2%}）後仍 > 0',
         f'- 通過的訊號進入考卷期：方向相同、|t| ≥ {HOLDOUT_T:.0f}、偏多扣成本後 > 0 才算真正有效', '',
         f"## 結果：{len(passed)} 個（訊號 × 持有天數）通過，進入考卷期", '']
    L += [f"- {SIGNALS[r.signal][0]}（{r.horizon} 天）：{r.verdict}，超額 {pct(r.excess)}、t {r.t:+.1f}" for r in passed] or ['- 無']
    L += [''] + table(res) + [''] + definitions()
    L += ['', '## 測試設定', '', f'- 範圍：{scope}',
          '- 隔天開盤進、第 H 個交易日收盤出；與同期全體電子股等權重平均比較',
          f"- 法人資料涵蓋率：研究期 {coverage['研究期']:.0%}、考卷期 {coverage['考卷期']:.0%} 的交易日",
          '- 本報告僅為研究，非投資建議。']
    out = _ROOT / 'reports' / f'chips_lab_dev_{date.today():%Y%m%d}.md'
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print('\n'.join(L))
    print(f'[saved] {out}', file=sys.stderr)
    return 0


def holdout_verdict(r, sign: int) -> str:
    if r.events < tech.MIN_EVENTS:
        return '樣本不足'
    ok = np.sign(r.t) == sign and abs(r.t) >= HOLDOUT_T and (sign < 0 or r.excess_net > 0)
    return '✅ 考卷期也有效' if ok else '❌ 考卷期無效'


def run_holdout(log) -> int:
    if HOLDOUT_FILE.exists():
        saved = json.loads(HOLDOUT_FILE.read_text(encoding='utf-8'))
        print(f"[info] 考卷期已在 {saved['date']} 考過，只顯示當時的結果（考卷只能考一次）")
        print(saved['report'])
        return 0
    if not DEV_FILE.exists():
        raise SystemExit('[error] 先跑研究期：python scripts/chips_lab.py --stage dev')
    dev = json.loads(DEV_FILE.read_text(encoding='utf-8'))
    sel = dev['selected']
    L = [f'# 籌碼面訊號研究：考卷期（{date.today()}）', '',
         f"只考研究期（{dev['date']} 跑出）通過的 {len(sel)} 個訊號；考卷期 {HOLDOUT_START} 起，只考一次。", '',
         f'判定：方向與研究期相同、|t| ≥ {HOLDOUT_T:.0f}、偏多訊號扣成本（{tech.ROUND_TRIP:.2%}）後超額 > 0。', '']
    if not sel:
        L.append('研究期沒有任何訊號通過，考卷期不需要考。')
    else:
        signals, px, open_, member, bench, _, _ = load(log)
        log('[test] 考卷期事件研究')
        L += ['| 訊號 | 持有 | 研究期 超額 / t | 考卷期 事件數 | 考卷期 超額 | 扣成本 | t 值 | 勝率 | 前半 | 後半 | 判定 |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
        for s in sel:
            r = next(x for x in tech.evaluate({s['signal']: signals[s['signal']]}, px, open_, member, bench,
                                              start=HOLDOUT_START, horizons=(s['horizon'],)))
            L.append(f"| {SIGNALS[s['signal']][0]} | {s['horizon']} 天 | {pct(s['dev_excess'])} / {s['dev_t']:+.1f} | {r.events:,} | "
                     f"{pct(r.excess)} | {pct(r.excess_net)} | {r.t:+.2f} | {r.hit:.0%} | {pct(r.first_half)} | {pct(r.second_half)} | "
                     f"{holdout_verdict(r, s['sign'])} |")
    L += ['', '- 本報告僅為研究，非投資建議。']
    report = '\n'.join(L) + '\n'
    STATE.mkdir(parents=True, exist_ok=True)
    HOLDOUT_FILE.write_text(json.dumps({'date': str(date.today()), 'report': report}, ensure_ascii=False, indent=1),
                            encoding='utf-8')
    out = _ROOT / 'reports' / f'chips_lab_holdout_{date.today():%Y%m%d}.md'
    out.write_text(report, encoding='utf-8')
    print(report)
    print(f'[saved] {out}', file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='籌碼面訊號研究（研究期 → 考卷期）')
    p.add_argument('--stage', choices=['dev', 'holdout'], required=True)
    args = p.parse_args()
    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr, flush=True)
    return run_dev(log) if args.stage == 'dev' else run_holdout(log)


if __name__ == '__main__':
    sys.exit(main())
