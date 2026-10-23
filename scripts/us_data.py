#!/usr/bin/env python3
"""美股模擬機器人第一階段：資料品質報告（S&P 500 ∪ Nasdaq-100 當時的成分股，2010 年起）。

檢查三件事：
  1. 成分股名單：由目前名單與 Wikipedia 歷年變動往回推，各時點檔數是否接近 500／100
  2. 股價涵蓋率：Yahoo 沒有下市／被併購公司，缺多少、缺的是哪些（依剔除原因分類）
  3. 倖存者偏差有多大：用「查得到股價的當時成分股」自組等權重指數，與真實的等權重 ETF（RSP、QQQE）比較；
     另算「只用今天的名單往回算」的版本，看完全不處理會高估多少
  4. SEC 季營收涵蓋率：各時點有多少成分股能算出營收年增
報告輸出：reports/us_data_quality_YYYYMMDD.md
"""
from __future__ import annotations
import os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import numpy as np
import pandas as pd

from src.app.us import universe as uv
from src.app.us import prices as pr
from src.app.us import sec

START = '2010-01-01'
REASONS = [('acquired', r'acqui|merg|bought|purchas|takeover|taken private|buyout'),
           ('bankrupt', r'bankrupt|chapter 11|delist'),
           ('spun_off', r'spin|spun|split'),
           ('market_cap', r'market cap|capitali|no longer|eligib|rebalanc|reconstitut|annual|float|replaced')]
REASON_NAMES = {'acquired': '被併購／合併', 'bankrupt': '破產／下市', 'spun_off': '分拆', 'market_cap': '市值不足等例行調整',
                'other': '其他／未說明'}


def reason_kind(text: str) -> str:
    import re
    for k, pat in REASONS:
        if re.search(pat, str(text), re.I):
            return k
    return 'other'


def ew_index(history, adj: pd.DataFrame, renames, days) -> pd.Series:
    """每月第一個交易日依當時名單（只取有股價的）等權重再平衡，回傳日報酬。"""
    ret = adj.pct_change(fill_method=None)
    out, members = [], []
    month = None
    for d in days:
        if d.month != month:
            month = d.month
            ms = {pr.resolve(s, renames) for s in uv.members_at(history, d)}
            members = [s for s in ms if s in adj.columns and pd.notna(adj.at[d, s])]
        r = ret.loc[d, members].dropna() if members else pd.Series(dtype=float)
        out.append(r.mean() if len(r) else 0.0)
    return pd.Series(out, index=days)


def yearly(r: pd.Series) -> pd.Series:
    return (1 + r).groupby(r.index.year).prod() - 1


def pct(v, d=1):
    return '—' if v is None or v != v else f'{v:+.{d}%}'


def main() -> int:
    log = lambda m: print(m, file=sys.stderr, flush=True)
    today = date.today()
    log('[universe] 讀取 Wikipedia 成分股與歷年變動')
    u = uv.load_universe(START)
    ren = u['renames']
    symbols = sorted({pr.resolve(s, ren) for _, m in u['union'] for s in m})
    log(f'[universe] 2010 年起曾為成分股：{len(symbols)} 檔')
    px = pr.download(symbols, START, log=log)
    close, adj = px['close'], px['adj']
    days = adj.loc[START:].dropna(subset=['SPY']).index

    # 1. 名單檔數
    yrs = list(range(2010, today.year + 1))
    counts = [(y, len(uv.members_at(u['sp500'], pd.Timestamp(f'{y}-06-30'))),
               len(uv.members_at(u['ndx'], pd.Timestamp(f'{y}-06-30'))),
               len(uv.members_at(u['union'], pd.Timestamp(f'{y}-06-30')))) for y in yrs]

    # 2. 股價涵蓋率
    cov = pr.coverage(u['union'], close, ren, days)
    cov['pct'] = cov['priced'] / cov['members']
    cov_y = cov.groupby(cov.index.year).agg(members=('members', 'mean'), priced=('priced', 'mean'), pct=('pct', 'mean'))
    ever = {s for _, m in u['union'] for s in m}
    missing = sorted(s for s in ever if pr.resolve(s, ren) not in close.columns or close[pr.resolve(s, ren)].dropna().empty)
    removed = pd.concat([u['changes']['sp500'], u['changes']['ndx']])
    removed = removed[removed['removed'].notna() & (removed['date'] >= START)]
    why = {r.removed: (r.date, r.removed_name, reason_kind(r.reason), r.reason) for r in removed.itertuples()}
    miss_kind = pd.Series([why.get(s, (None, '', 'other', ''))[2] for s in missing]).value_counts()
    # 被剔除但查得到股價（仍掛牌，例如市值不足被剔除）
    removed_syms = set(removed['removed'])
    removed_priced = [s for s in removed_syms if s not in missing]

    # 3. 倖存者偏差：自組等權重 vs 真實等權重 ETF
    log('[bias] 自組等權重指數')
    ret = adj.pct_change(fill_method=None)
    sp_pit = ew_index(u['sp500'], adj, ren, days)
    sp_now = ew_index([(days[0], set(u['current'].query("index == 'sp500'")['symbol']))], adj, ren, days)
    nd_pit = ew_index(u['ndx'], adj, ren, days)
    nd_now = ew_index([(days[0], set(u['current'].query("index == 'ndx'")['symbol']))], adj, ren, days)
    bench = {k: ret[k].reindex(days).fillna(0.0) for k in ('RSP', 'QQQE', 'VOO', 'SPY', 'QQQ') if k in ret}
    cmp = pd.DataFrame({'自組（當時名單）': yearly(sp_pit), '自組（今天名單）': yearly(sp_now), 'RSP 實際': yearly(bench['RSP']),
                        'NDX 自組（當時名單）': yearly(nd_pit), 'NDX 自組（今天名單）': yearly(nd_now),
                        'QQQE 實際': yearly(bench['QQQE'])})
    qqqe_start = adj['QQQE'].first_valid_index()
    cmp.loc[cmp.index < qqqe_start.year + (qqqe_start.month > 1), ['NDX 自組（當時名單）', 'NDX 自組（今天名單）', 'QQQE 實際']] = np.nan

    def cagr(r):
        r = r.dropna()
        return float((1 + r).prod() ** (252 / len(r)) - 1) if len(r) else float('nan')
    full = {'sp_pit': cagr(sp_pit), 'sp_now': cagr(sp_now), 'rsp': cagr(bench['RSP']),
            'nd_pit': cagr(nd_pit.loc[qqqe_start:]), 'nd_now': cagr(nd_now.loc[qqqe_start:]),
            'qqqe': cagr(bench['QQQE'].loc[qqqe_start:])}

    # 4. SEC 季營收
    log('[sec] 下載季營收（第一次約 10 分鐘，之後讀快取）')
    ciks = sec.ticker_ciks()
    for r in u['current'].dropna(subset=['cik']).itertuples():
        ciks.setdefault(r.symbol, int(r.cik))
    priced = [s for s in symbols if s in close.columns and close[s].dropna().size]
    rev, no_cik = {}, []
    for i, s in enumerate(priced):
        if s not in ciks:
            no_cik.append(s)
            continue
        try:
            rev[s] = sec.quarterly_revenue(ciks[s])
        except Exception as e:      # noqa: BLE001
            log(f'[sec] {s} 失敗：{e}')
        if i % 100 == 0:
            log(f'[sec] {i + 1}/{len(priced)}')

    def has_yoy(s, d):
        df = rev.get(s)
        if df is None:
            return False
        known = df[df['filed'] <= d]
        if known.empty:
            return False
        last = known.iloc[-1]
        prior = known[(known['end'] - (last['end'] - pd.DateOffset(years=1))).abs() <= pd.Timedelta(days=10)]
        return (d - last['filed']).days <= 200 and not prior.empty
    rev_y = []
    for y in yrs:
        d = pd.Timestamp(f'{y}-06-30')
        ms = [pr.resolve(s, ren) for s in uv.members_at(u['union'], d)]
        ok = [s for s in ms if s in close.columns and close[s].loc[:d].dropna().size]
        rev_y.append((y, len(ms), len(ok), sum(has_yoy(s, d) for s in ok)))

    # ---------- 報告 ----------
    L = [f'# 美股資料品質報告（{today}）', '',
         f'範圍：S&P 500 ∪ Nasdaq-100「當時」的成分股，{START} 起。這是美股模擬機器人第一階段，只檢查資料，不做任何策略。', '',
         '## 1. 成分股名單（由目前名單與 Wikipedia 歷年變動往回推）', '',
         f"S&P 500 變動 {(u['changes']['sp500']['date'] >= START).sum()} 筆、Nasdaq-100 變動 {(u['changes']['ndx']['date'] >= START).sum()} 筆；"
         f'2010 年起曾為成分股共 {len(ever)} 檔。檔數穩定接近 500／100，代表變動紀錄大致完整'
         '（S&P 500 實際約 503 檔，因為有雙重股權，例如 GOOG／GOOGL）。', '',
         '| 年（6/30） | S&P 500 | Nasdaq-100 | 合併（去重複） |', '|---|---|---|---|']
    L += [f'| {y} | {a} | {b} | {c} |' for y, a, b, c in counts]
    L += ['', f"改代號對照（同一家公司）：{'、'.join(f'{a}→{b}' for a, b in ren.items())}", '',
          '## 2. 股價涵蓋率（Yahoo）', '',
          '「有股價」= 該股在屬於成分股的那一天，Yahoo 查得到收盤價。', '',
          '| 年 | 平均成分股數 | 平均有股價 | 涵蓋率 |', '|---|---|---|---|']
    L += [f'| {y} | {r.members:.0f} | {r.priced:.0f} | {r.pct:.1%} |' for y, r in cov_y.iterrows()]
    L += ['', f'整段期間查不到股價的代號：**{len(missing)} 檔**（占曾為成分股的 {len(missing) / len(ever):.0%}）。依剔除原因分類：', '']
    L += [f'- {REASON_NAMES[k]}：{v} 檔' for k, v in miss_kind.items()]
    L += [f'- 被剔除但仍查得到股價（多為市值不足被剔除、仍掛牌）：{len(removed_priced)} 檔', '',
          '查不到股價的例子（最近剔除的 15 檔）：', '', '| 代號 | 公司 | 剔除日 | 原因 |', '|---|---|---|---|']
    ex = sorted((why[s] + (s,) for s in missing if s in why), key=lambda x: x[0], reverse=True)[:15]
    L += [f'| {s} | {n} | {d:%Y-%m-%d} | {r[:60]} |' for d, n, k, r, s in ex]
    L += ['', '## 3. 倖存者偏差有多大', '',
          '每月依名單等權重再平衡，用 Yahoo 還原價（含配息）自組指數，與真實的等權重 ETF 比較。',
          '「當時名單」只少了查不到股價的下市公司；「今天名單」完全不處理倖存者偏差（用今天的成分股往回算）。', '',
          '| 期間年化 | 自組（當時名單） | 自組（今天名單） | 真實 ETF |', '|---|---|---|---|',
          f"| S&P 500 等權重（2010 起，對 RSP） | {pct(full['sp_pit'])} | {pct(full['sp_now'])} | {pct(full['rsp'])} |",
          f"| Nasdaq-100 等權重（{qqqe_start:%Y-%m} 起，對 QQQE） | {pct(full['nd_pit'])} | {pct(full['nd_now'])} | {pct(full['qqqe'])} |", '',
          f"- 當時名單比真實 ETF 每年多 **{pct(full['sp_pit'] - full['rsp'])}**（S&P）、**{pct(full['nd_pit'] - full['qqqe'])}**（NDX）："
          '這大約是缺少下市公司造成的偏差，加上 ETF 本身的費用與再平衡時點差異。',
          f"- 今天名單比真實 ETF 每年多 **{pct(full['sp_now'] - full['rsp'])}**（S&P）、**{pct(full['nd_now'] - full['qqqe'])}**（NDX）："
          '完全不處理倖存者偏差時會高估這麼多。', '',
          '| 年 | 自組（當時） | 自組（今天） | RSP | NDX 自組（當時） | NDX 自組（今天） | QQQE |', '|---|---|---|---|---|---|---|']
    L += [f'| {y} | ' + ' | '.join(pct(v) for v in r.values) + ' |' for y, r in cmp.iterrows()]
    L += ['', '## 4. SEC 季營收涵蓋率', '',
          '「可算營收年增」= 當天以前已申報最新一季營收（200 天內），而且有去年同季可以比較。', '',
          '| 年（6/30） | 成分股 | 有股價 | 可算營收年增 | 比例（占有股價） |', '|---|---|---|---|---|']
    L += [f'| {y} | {a} | {b} | {c} | {c / b:.0%} |' for y, a, b, c in rev_y]
    L += ['', f'查不到 CIK（SEC 公司代碼）的有股價代號：{len(no_cik)} 檔' + (f"（{'、'.join(no_cik[:20])}{'…' if len(no_cik) > 20 else ''}）" if no_cik else ''), '',
          '## 限制', '',
          '- Wikipedia 的 S&P 500 變動表標明為「選錄」；檔數穩定代表大致完整，但仍可能漏掉少數變動。',
          '- 舊代號被新公司重新使用時，Yahoo 可能回傳別家公司的股價；只要該代號在成分股期間有值就會被算成「有股價」，無法完全排除。',
          '- 季營收取「最早申報」的數字（不使用之後的修正），避免偷看未來；第 4 季以全年減前三季推算。',
          '- 本報告只檢查資料，不是投資建議。']
    out = _ROOT / 'reports' / f'us_data_quality_{today:%Y%m%d}.md'
    out.parent.mkdir(exist_ok=True)
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print('\n'.join(L))
    print(f'[saved] {out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
