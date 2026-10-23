#!/usr/bin/env python3
"""族群雷達：記憶體、面板、IC 設計…各族群的營收、動能、量能、籌碼，並依驗證過的特徵排序。

使用範例:
  python scripts/sector_radar.py              # 計算最新一個交易日的族群排名（排程：平日 16:30 前瞻測試之後）
  python scripts/sector_radar.py --backtest   # 歷史驗證：每 20 天買排名前 3 族群 vs 0050（報告輸出到 reports/）
結果：state/radar/latest.json（盤前簡報與操作台讀取）、state/radar/backtest.json
這是資料整理與歷史驗證，不是買賣建議。
"""
from __future__ import annotations
import argparse, json, os, sys, pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import numpy as np
import pandas as pd

from src.app.lab import market as mk
from src.app.lab import radar
from src.app.lab.constituents import members_on
from src.app.lab.features import raw_panels
from src.app.lab.flags import stock_flags
from src.app.lab.themes import load_code_themes, refresh_chains

STATE = _ROOT / 'state' / 'radar'
NAMES = {'rev_yoy': '營收年增（中位數）', 'rev_breadth': '連 2 月營收年增比例', 'r20': '近 20 日漲跌', 'r60': '近 60 日漲跌',
         'above_ma': '站上 120 日線比例', 'vratio': '量能比', 'foreign20': '外資 20 日買超', 'margin20': '融資 20 日變化'}


def pct(v, d=1):
    return '—' if v is None or v != v else f'{v:+.{d}%}'


def build(start: str, chips_start: str, end, log):
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    built = mk.build_market_dataset(mk.MARKETS, start=start, chips_start=chips_start, today=now.date(), end=end, log=log)
    ds = built['ds']
    panels = radar.extra_panels(ds, raw_panels(ds, built['volume'], built['chips'], built['codes']))
    return built, ds, panels


def run_backtest(log) -> int:
    built, ds, panels = build('2015-01-01', '2018-01-01', None, log)
    code_themes = load_code_themes()
    hist = built['history']
    members_fn = lambda d: radar.theme_members(code_themes, [c for c in members_on(hist, d)
                                                             if c in ds.prices.columns and pd.notna(ds.prices.at[d, c])])
    end = ds.prices.index[-1]
    results = {h: radar.backtest(panels, ds.prices, members_fn, start='2018-04-01', end=end, horizon=h) for h in (20, 60)}
    tagged = sum(1 for c in built['codes'] if c in code_themes)
    delisted_tagged = sum(1 for c in built['delisted'] if c in code_themes)
    today = datetime.now(ZoneInfo('Asia/Taipei')).date()
    L = [f'# 族群雷達歷史驗證（{today}）', '',
         f'每 N 個交易日（期間不重疊），依「營收年增、近 60 日漲跌、量能比、融資 20 日變化」四項族群中位數的跨族群排名平均，'
         f'取前 {radar.TOP_K} 名族群，持有其成分股 N 天（族群等權、族群內等權），扣一次來回成本 {radar.ROUND_TRIP:.3%}，'
         '與同期 0050、全部族群、排名最後 3 名比較。', '']
    for h, r in results.items():
        s = r.summary
        verdict = ('**有統計意義地贏過 0050**' if s['top_vs_bench_t'] > 2 else
                   '**沒有證明能贏過 0050**' if s['top_vs_bench_t'] > -2 else '**明顯輸給 0050**')
        L += [f'## 持有 {h} 個交易日（{s["periods"]} 期）', '', verdict, '',
              '| | 每期平均（扣成本） |', '|---|---|',
              f"| 前 {radar.TOP_K} 名族群 | {pct(s['top_avg'], 2)} |", f"| 後 {radar.TOP_K} 名族群 | {pct(s['bottom_avg'], 2)} |",
              f"| 全部族群成分股 | {pct(s['all_avg'], 2)} |", f"| 0050 | {pct(s['bench_avg'], 2)} |", '',
              f"- 前 3 名 vs 0050：平均 {pct(s['top_vs_bench'], 2)}、中位數 {pct(s['top_vs_bench_median'], 2)}、"
              f"t 值 {s['top_vs_bench_t']:+.2f}、贏的期數 {s['top_beats_bench']:.0%}",
              f"- 前 3 名 vs 後 3 名：平均 {pct(s['top_vs_bottom'], 2)}，t 值 {s['top_vs_bottom_t']:+.2f}（排名本身有沒有用）",
              f"- 前 3 名 vs 全部族群：平均 {pct(s['top_vs_all'], 2)}",
              f"- 穩定度（vs 0050）：前半期 {pct(s['first_half_vs_bench'], 2)}、後半期 {pct(s['second_half_vs_bench'], 2)}、"
              f"2024 年後 {pct(s['since_2024_vs_bench'], 2)}", '']
        top_counts = pd.Series([t for x in r.trades['top'] for t in x.split('、')]).value_counts()
        L.append('入選次數最多的族群：' + '、'.join(f'{k} {v} 次' for k, v in top_counts.head(8).items()))
        L.append('')
    L += ['## 限制', '',
          f'- 族群分類取自櫃買中心產業價值鏈平台的「目前」填報；期間曾入選電子股 {len(built["codes"])} 檔中 {tagged} 檔有分類，'
          f'已下市的 {len(built["delisted"])} 檔中只有 {delisted_tagged} 檔有分類（有倖存者偏差，結果偏樂觀）。',
          '- 一家公司可屬於多個族群；族群之間成分股會重疊。',
          '- 排序特徵是先在同一份資料上看過才選的（電子股單一特徵分組報告），有資料探勘的風險；前瞻紀錄才是真正的檢驗。',
          '- 過去有效不代表未來有效。本報告僅為驗證，非投資建議。']
    out = _ROOT / 'reports' / f'sector_radar_{today:%Y%m%d}.md'
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'backtest.json').write_text(json.dumps({'date': str(today), 'report': out.name,
                                                     **{str(h): r.summary for h, r in results.items()}},
                                                    ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n'.join(L))
    print(f'[saved] {out}', file=sys.stderr)
    return 0


def stock_names(ds, today) -> dict[str, str]:
    """最近幾個月的月營收表（上市＋上櫃）取公司簡稱。"""
    from src.app.research import sources as rs
    out = {}
    for m in reversed(ds.revenue_yoy.index[-3:]):
        for market in ('sii', 'otc'):
            try:
                df = rs.monthly_revenue(m.year, m.month, today=today, market=market)
            except rs.NoData:
                continue
            for c, n in zip(df['code'], df['name']):
                out.setdefault(c, n)
    return out


def forward_codes(date_str: str) -> set[str]:
    """前瞻測試在同一天記下的名單（沒有就是空集合）。"""
    path = _ROOT / 'state' / 'forward' / 'records.jsonl'
    if not path.exists():
        return set()
    for line in reversed(path.read_text(encoding='utf-8').splitlines()):
        if line.strip():
            rec = json.loads(line)
            if rec['date'] == date_str:
                return {p['code'] for p in rec['picks']}
    return set()


def run_daily(log) -> int:
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    today = now.date()
    end = today - timedelta(days=1)
    if now.hour * 60 + now.minute >= 15 * 60 + 30 and today.weekday() < 5 \
            and all(not mk.quotes_day(m, today).empty for m in mk.MARKETS):
        end = today
    built, ds, panels = build('2025-01-01', '2025-06-01', end, log)
    d = ds.prices.index[-1]
    universe = [c for c in members_on(built['history'], d) if c in ds.prices.columns and pd.notna(ds.prices.at[d, c])]
    refresh_chains(universe, log=log)          # 新上市補分類、舊分類每 90 天更新
    code_themes = load_code_themes()
    members = radar.theme_members(code_themes, universe)
    snap = radar.snapshot(panels, members, d)
    stocks = radar.stock_table(panels, universe, d)
    names = stock_names(ds, today)
    forward = forward_codes(d.strftime('%Y-%m-%d'))
    flags = stock_flags(ds.prices, built['chips'].get('foreign'), d, universe)
    clean = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else v

    def stock_rows(codes):
        sub = stocks.loc[codes].sort_values('score', ascending=False)
        return [{'code': c, 'name': names.get(c, ''), 'market': mk.MARKET_NAME[built['info'][c][0]],
                 'close': clean(round(float(ds.prices.at[d, c]), 2)),
                 **{k: clean(float(r[k])) for k in radar.SHOW_FEATURES + ['score']},
                 'rev_momentum': bool(r['rev_momentum']), 'forward': c in forward,
                 'flags': flags.get(c, [])} for c, r in sub.iterrows()]

    rows = []
    for t, r in snap.iterrows():
        rows.append({'theme': t, 'rank': len(rows) + 1, **{k: clean(float(v)) for k, v in r.items()},
                     'stocks': stock_rows(members[t])})
    bt_path = STATE / 'backtest.json'
    bt = json.loads(bt_path.read_text(encoding='utf-8')) if bt_path.exists() else None
    data = {'date': d.strftime('%Y-%m-%d'), 'updated': now.isoformat(timespec='seconds'), 'rows': rows,
            'score_features': radar.SCORE_FEATURES, 'backtest': bt}
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'latest.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"族群雷達 {data['date']}（分數 = 營收年增、近 60 日、量能、融資 4 項的跨族群排名平均）")
    for r in rows:
        print(f"{r['rank']:>2}. {r['theme']:<8} 分數 {r['score']:.2f}｜{int(r['members'])} 檔｜營收年增 {r['rev_yoy']:+.1f}%｜"
              f"連 2 月年增 {r['rev_breadth']:.0%}｜60 日 {pct(r['r60'])}｜量能比 {r['vratio']:.2f}｜融資 {r['margin20']:+.2f}")
    print(f'[saved] {STATE / "latest.json"}', file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='族群雷達')
    p.add_argument('--backtest', action='store_true', help='歷史驗證並輸出報告')
    args = p.parse_args()
    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr)
    return run_backtest(log) if args.backtest else run_daily(log)


if __name__ == '__main__':
    sys.exit(main())
