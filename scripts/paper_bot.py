#!/usr/bin/env python3
"""模擬操作機器人（營收動能規則＋你的風險設定＋你的券商手續費）。

使用範例:
  python scripts/paper_bot.py                 # 每日模擬：執行昨天排定的單（開盤價）、記帳、排定明天的單（排程：平日 16:50）
  python scripts/paper_bot.py --send          # 有成交或新單時推送 Discord
  python scripts/paper_bot.py --backtest      # 歷史模擬 2018 年至今（收盤價成交＋0.1% 滑價）
  python scripts/paper_bot.py --reset         # 重新開始模擬帳戶
狀態：state/paper_bot/state.json；歷史模擬報告：reports/paper_bot_backtest_YYYYMMDD.md
這是模擬，不是買賣建議；真實下單由你自行決定。
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
from dotenv import load_dotenv

from src.app.lab import market as mk
from src.app.lab.paperbot import Bot, Config, DayContext, summarize
from src.app.lab.constituents import members_on
from src.app.alerts.portfolio import load_portfolio

STATE = _ROOT / 'state' / 'paper_bot'
FEE_DISCOUNT = 0.28
MIN_FEE = 1.0
MAX_PER_THEME = 2           # 同一族群最多同時持有 2 檔（2026-10-22 依 3 種設定 × 8 起始日比較選定）
BREAKEVEN_AFTER = 0.20      # 方案 E：收盤漲到成本 +20% 後，停損上移到成本價（2026-10-21 依 5 方案 × 8 起始日比較選定）


def config_from_portfolio(slippage: float = 0.0) -> Config:
    risk = load_portfolio(_ROOT / 'portfolio.json').risk or {}
    if not risk.get('capital'):
        raise SystemExit('[error] portfolio.json 沒有 risk.capital，請先設定個股操作資金')
    return Config(capital=float(risk['capital']), max_risk_per_trade=risk.get('max_risk_per_trade', 0.03),
                  max_position_pct=risk.get('max_position_pct', 0.2), max_total_risk=risk.get('max_total_risk', 0.08),
                  fee_rate=0.001425 * FEE_DISCOUNT, min_fee=MIN_FEE, slippage=slippage,
                  breakeven_after=BREAKEVEN_AFTER, max_per_theme=MAX_PER_THEME)


def revenue_panels(ds, index, columns):
    """每天：最新已公布月營收年增、最近 2 個月都年增、最近 2 個月都年減。"""
    rev = ds.revenue_yoy.reindex(columns=columns)
    latest = pd.DataFrame(np.nan, index=index, columns=columns)
    pos2 = pd.DataFrame(False, index=index, columns=columns)
    neg2 = pd.DataFrame(False, index=index, columns=columns)
    months = list(rev.index)
    for i, m in enumerate(months):
        if i == 0:
            continue
        mask = index >= ds.revenue_available(m)
        cur, prev = rev.loc[m], rev.loc[months[i - 1]]
        latest.loc[mask] = cur.values
        pos2.loc[mask] = ((cur > 0) & (prev > 0)).values
        neg2.loc[mask] = ((cur < 0) & (prev < 0)).values
    return latest, pos2, neg2


_THEMES: dict | None = None


def theme_lookup():
    """代號 → 所屬族群（櫃買產業價值鏈分類，只讀快取；族群雷達每天會補抓新上市與更新舊資料）。"""
    global _THEMES
    if _THEMES is None:
        from src.app.lab.themes import load_code_themes
        _THEMES = load_code_themes()
    return lambda c: _THEMES.get(c, set())


def make_context(d, prices_close, ma, latest, pos2, neg2, members):
    row_c, row_ma = prices_close.loc[d], ma.loc[d]
    ok = [c for c in members if pd.notna(row_c.get(c)) and pd.notna(row_ma.get(c))
          and bool(pos2.at[d, c]) and row_c[c] > row_ma[c]]
    cands = sorted(({'code': c, 'close': float(row_c[c]), 'rev_yoy': float(latest.at[d, c])} for c in ok),
                   key=lambda x: -x['rev_yoy'])
    num = lambda s, c: (float(s[c]) if c in s.index and pd.notna(s[c]) else None)
    return DayContext(close=lambda c: num(row_c, c), ma=lambda c: num(row_ma, c),
                      revenue_down=lambda c: bool(neg2.at[d, c]) if c in neg2.columns else False, candidates=cands,
                      themes=theme_lookup())


def pct(v, d=1):
    return '—' if v is None or v != v else f'{v:+.{d}%}'


# ---------- 歷史模擬 ----------

def run_backtest(log) -> int:
    cfg = config_from_portfolio(slippage=0.001)
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    built = mk.build_market_dataset(mk.MARKETS, start='2015-01-01', chips_start=None, today=now.date(), log=log)
    ds, hist = built['ds'], built['history']
    px = ds.prices
    codes = [c for c in built['codes'] if c in px.columns]
    ma = px[codes].rolling(cfg.ma_days, min_periods=cfg.ma_days).mean()
    latest, pos2, neg2 = revenue_panels(ds, px.index, codes)
    days = px.loc['2018-06-01':].index
    bot = Bot.new(cfg)
    px_prev = px.shift(1)
    for i, d in enumerate(days):
        ds_ = d.strftime('%Y-%m-%d')
        row, prow = px.loc[d], px_prev.loc[d]
        val = lambda s: (lambda c: float(s[c]) if c in s.index and pd.notna(s[c]) else None)
        # 成交當天漲 ≥ 9.5% 的買單、跌 ≥ 9.5% 的賣單視為漲跌停無法成交
        bot.execute(ds_, limit_gate(val(row), val(prow), {o['code']: o['side'] for o in bot.pending}))
        bot.mark(ds_, lambda c: float(row[c]) if c in row.index and pd.notna(row[c]) else None)
        members = [c for c in members_on(hist, d) if c in codes]
        bot.decide(ds_, make_context(d, px, ma, latest, pos2, neg2, members))
        if i % 250 == 0:
            log(f'[sim] {ds_} 淨值 {bot.equity[-1]["equity"]:,.0f}')
    bench = px['0050']
    s = summarize(bot, bench)
    eq = pd.Series({pd.Timestamp(e['date']): e['equity'] for e in bot.equity})
    b = bench.reindex(eq.index).ffill()
    yearly = pd.DataFrame({'機器人': eq.groupby(eq.index.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1),
                           '0050': b.groupby(b.index.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1)})
    today = now.date()
    L = [f'# 模擬操作機器人：歷史模擬（{today}）', '',
         f"用 {cfg.capital:,.0f} 元、每筆最多虧 {cfg.max_risk_per_trade:.0%}、單一個股 ≤ {cfg.max_position_pct:.0%}、"
         f"總風險 ≤ {cfg.max_total_risk:.0%}、停損 {cfg.stop_pct:.0%}，手續費 {FEE_DISCOUNT * 10:g} 折（最低 {MIN_FEE:g} 元）、"
         '證交稅 0.3%，從 2018-06 起每天依營收動能規則自動操作上市＋上櫃電子股（含之後下市的公司）。', '',
         '## 結果', '',
         '| | 機器人 | 0050 |', '|---|---|---|',
         f"| 期末淨值（起始 {cfg.capital:,.0f}） | {s['equity']:,.0f} | {cfg.capital * (1 + s.get('bench_total_return', 0)):,.0f} |",
         f"| 年化報酬 | {pct(s.get('cagr'))} | {pct(s.get('bench_cagr'))} |",
         f"| 最大回撤 | {pct(s.get('max_drawdown'))} | {pct(s.get('bench_max_drawdown'))} |",
         f"| Sharpe | {s.get('sharpe', 0):.2f} | |", '',
         f"- 完成交易 {s.get('closed_trades', 0)} 筆，勝率 {s.get('win_rate', 0):.0%}，平均每筆 {pct(s.get('avg_return'))}"
         f"（賺的平均 {pct(s.get('avg_win'))}、賠的平均 {pct(s.get('avg_loss'))}），平均持有 {s.get('avg_hold_days', 0):.0f} 天",
         f"- 手續費與稅合計 {s.get('total_fees', 0):,.0f} 元",
         '- 出場原因：' + '、'.join(f'{k} {v} 次' for k, v in (s.get('exit_reasons') or {}).items()), '',
         '## 各年度', '', '| 年度 | 機器人 | 0050 |', '|---|---|---|']
    L += [f"| {y} | {pct(r['機器人'])} | {pct(r['0050'])} |" for y, r in yearly.iterrows()]
    L += ['', '## 限制', '',
          '- 歷史資料沒有開盤價，模擬用「訊號隔天的收盤價＋0.1% 滑價」成交；每日模擬則用真實開盤價。',
          '- 價格用還原權值股價（含配息），股數依還原價計算，早期年份的股數與實際略有差異。',
          '- 已下市公司沒有月營收資料，規則選不到它們；可能讓結果略為偏樂觀。',
          '- 規則是在同一段資料上研究出來的（營收動能在回測中表現最好），歷史模擬有資料探勘的風險；每日模擬才是真正的檢驗。',
          '- 這是模擬，不是買賣建議。']
    out = _ROOT / 'reports' / f'paper_bot_backtest_{today:%Y%m%d}.md'
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'backtest.json').write_text(json.dumps({'date': str(today), 'report': out.name, 'summary': s,
                                                     'yearly': {str(k): v for k, v in yearly.to_dict('index').items()}},
                                                    ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    # 每日淨值與同資金 0050：月成績單用來算「歷史上同樣長度期間」的正常範圍
    pd.DataFrame({'equity': eq, 'bench': b / b.iloc[0] * cfg.capital}).rename_axis('date').reset_index().assign(
        date=lambda x: x['date'].dt.strftime('%Y-%m-%d')).to_json(STATE / 'backtest_curve.json', orient='records')
    print('\n'.join(L))
    print(f'[saved] {out}', file=sys.stderr)
    return 0


# ---------- 每日模擬 ----------

def stock_names(ds, today) -> dict[str, str]:
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


def raw_quote(d, field):
    """當天原始（未還原）開盤或收盤價，兩個市場合併；缺欄位（舊快取沒有開盤價）回傳空。"""
    parts = []
    for m in mk.MARKETS:
        q = mk.quotes_day(m, d.date())
        if not q.empty and field in q.columns:
            parts.append(q[['code', field]])
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).drop_duplicates('code').set_index('code')[field].astype(float)


PAUSED = STATE / 'paused'
SCORE_STATE = STATE / 'scorecard_state.json'


def make_scorecard(month_label: str | None = None) -> tuple[dict, str] | None:
    """依目前帳戶與歷史模擬淨值產生成績單，存成 state/paper_bot/scorecard.{json,md}；月報另存 reports/。"""
    from src.app.lab import scorecard as scm
    view_p, curve_p, state_p = STATE / 'latest.json', STATE / 'backtest_curve.json', STATE / 'state.json'
    if not (view_p.exists() and curve_p.exists() and state_p.exists()):
        return None
    view = json.loads(view_p.read_text(encoding='utf-8'))
    curve = pd.read_json(curve_p, orient='records', convert_dates=False)
    trades = json.loads(state_p.read_text(encoding='utf-8'))['trades']
    sc = scm.evaluate(view, curve, trades)
    text = scm.render(sc, month_label)
    (STATE / 'scorecard.json').write_text(json.dumps(sc, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    (STATE / 'scorecard.md').write_text(text, encoding='utf-8')
    if month_label:
        (_ROOT / 'reports').mkdir(exist_ok=True)
        (_ROOT / 'reports' / f"paper_scorecard_{month_label.replace('-', '')}.md").write_text(text + '\n', encoding='utf-8')
    return sc, text


def monthly_scorecard_due(last_date: str, start: str) -> str | None:
    """新的月份第一次收盤記帳時，回傳要出成績單的月份（上個月，'YYYY-MM'）；其他時候回傳 None。"""
    st = json.loads(SCORE_STATE.read_text(encoding='utf-8')) if SCORE_STATE.exists() else {'last_month': start[:7]}
    month = last_date[:7]
    if month <= st['last_month']:
        return None
    prev = (pd.Period(month, 'M') - 1).strftime('%Y-%m')
    SCORE_STATE.write_text(json.dumps({'last_month': month}), encoding='utf-8')
    return prev
MIS_ODD = 'https://mis.twse.com.tw/stock/api/getOddInfo.jsp'   # 盤中零股即時報價（五檔價量、漲跌停價）
LIMIT_MOVE = 0.095          # 沒有五檔資料時（開盤價補成交、歷史模擬）：開盤／成交較前收 ≥ 9.5% 視為漲停買不到、≤ -9.5% 視為跌停賣不掉


def _levels(prices: str | None, vols: str | None) -> list[tuple[float, float]]:
    ps = [p for p in (prices or '').split('_') if p not in ('', '-')]
    vs = [v for v in (vols or '').split('_') if v not in ('', '-')]
    return [(float(p), float(v)) for p, v in zip(ps, vs) if float(p) > 0 and float(v) > 0]


def realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """證交所盤中零股即時報價（上市＋上櫃）：{代號: {date, time, last, asks, bids, limit_up, limit_down}}。
    asks／bids 為 [(價格, 股數)] 由近到遠。代號不知道是上市或上櫃，兩個都查。"""
    import requests
    out = {}
    num = lambda v: float(v) if v not in (None, '', '-') else None
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        ex_ch = '|'.join(f'{m}_{c}.tw' for c in chunk for m in ('tse', 'otc'))
        js = requests.get(MIS_ODD, params={'ex_ch': ex_ch, 'json': '1', 'delay': '0'},
                          headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        for m in js.get('msgArray', []):
            if m.get('c'):
                out[m['c']] = {'date': m.get('d'), 'time': m.get('t'), 'last': num(m.get('z')),
                               'asks': _levels(m.get('a'), m.get('f')), 'bids': _levels(m.get('b'), m.get('g')),
                               'limit_up': num(m.get('u')), 'limit_down': num(m.get('w'))}
    return out


def fill_from_book(q: dict, side: str, shares: int) -> tuple[float | None, str]:
    """依零股五檔模擬市價單：買進往上吃賣單、賣出往下吃買單，回傳（平均成交價, 說明）。
    沒有對手單時：鎖漲停（買）／鎖跌停（賣）→ 成交不了；其他情況用最新成交價。五檔量不夠時，不足的部分以第五檔價計。"""
    book = q['asks'] if side == 'buy' else q['bids']
    if not book:
        last = q.get('last')
        if side == 'buy' and last and q.get('limit_up') and last >= q['limit_up']:
            return None, '漲停鎖住，沒有賣單，買不到'
        if side == 'sell' and last and q.get('limit_down') and last <= q['limit_down']:
            return None, '跌停鎖住，沒有買單，賣不掉'
        return (last, '五檔沒有掛單，以最新成交價計') if last else (None, '沒有報價（可能停牌）')
    left, cost = shares, 0.0
    for p, v in book:
        take = min(left, v)
        cost += take * p
        left -= take
        if left <= 0:
            break
    note = '零股五檔'
    if left > 0:
        cost += left * book[-1][0]
        note = '零股五檔量不足，不足部分以第五檔價計'
    return cost / shares, note


def limit_gate(price_fn, prev_close_fn, sides: dict[str, str]):
    """沒有五檔資料時的漲跌停近似：成交價較前收漲 ≥ 9.5% 的買單、跌 ≥ 9.5% 的賣單視為無法成交。"""
    def gated(code):
        p, prev = price_fn(code), prev_close_fn(code)
        if p is None or not prev:
            return p
        if sides.get(code) == 'buy' and p >= prev * (1 + LIMIT_MOVE):
            return None
        if sides.get(code) == 'sell' and p <= prev * (1 - LIMIT_MOVE):
            return None
        return p
    return gated


def run_execute(log, dry_run: bool = False) -> int:
    """09:30：用即時報價執行昨天收盤後排定的模擬單（買進用最佳賣價、賣出用最佳買價，沒有五檔才用最新成交價）。
    沒有報價（停牌）時：賣單留著，等之後有報價再賣；買單取消（與開盤成交的規則相同）。
    09:30 沒執行到（電腦沒開、不在盤中）的單，當天 16:50 收盤後改用開盤價補成交。"""
    load_dotenv(_ROOT / '.env')
    if PAUSED.exists():
        print('[skip] 模擬帳戶已暫停')
        return 0
    path = STATE / 'state.json'
    if not path.exists():
        print('[skip] 還沒有模擬帳戶')
        return 0
    saved = json.loads(path.read_text(encoding='utf-8'))
    bot = Bot.from_dict(saved)
    if not bot.pending:
        print('[skip] 沒有待執行的模擬單')
        return 0
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    today = now.strftime('%Y%m%d')
    if not (9 * 60 + 5 <= now.hour * 60 + now.minute <= 13 * 60 + 25):
        print('[skip] 不在盤中（09:05～13:25），留給收盤後以開盤價補成交')
        return 0
    if any(o.get('signal_date', '') >= now.strftime('%Y-%m-%d') for o in bot.pending):
        print('[skip] 有今天收盤後才排定的單，要等下一個交易日才執行')
        return 0
    q = realtime_quotes([o['code'] for o in bot.pending])
    if not any(v['date'] == today for v in q.values()):
        print('[skip] 今天沒有即時報價（休市），模擬單留到下一個交易日')
        return 0
    orders = {o['code']: o for o in bot.pending}
    notes = {}

    def price(code):
        v = q.get(code)
        if not v or v['date'] != today:
            notes[code] = '沒有今天的報價（可能停牌）'
            return None
        p, notes[code] = fill_from_book(v, orders[code]['side'], orders[code]['shares'])
        return p
    executed = bot.execute(now.strftime('%Y-%m-%d'), price)
    for t in executed:
        t['fill'] = f"{now:%H:%M} {notes.get(t['code'], '')}"
    not_filled = [(c, notes.get(c, '')) for c, o in orders.items() if c not in {t['code'] for t in executed}]
    view_path = STATE / 'latest.json'
    view = json.loads(view_path.read_text(encoding='utf-8')) if view_path.exists() else {}
    names = {x['code']: x.get('name', '') for k in ('positions', 'pending', 'trades') for x in view.get(k, [])}
    nm = lambda c: f"{c} {names.get(c, '')}".strip()
    L = [f"## 模擬帳戶 {now:%m/%d %H:%M} 成交（即時報價）"]
    L += [f"- {'買進' if t['side'] == 'buy' else '賣出'} {nm(t['code'])} {t['shares']} 股 @ {t['price']:,.2f}"
          + (f"，停損 {t['stop']:,.2f}" if t['side'] == 'buy' else f"，損益 {t['pnl']:+,.0f} 元（{t['reason']}）")
          for t in executed] or ['- 沒有成交']
    if not_filled:
        L.append('\n**沒有成交**（買單取消；賣單留到下一個交易日再試）')
        L += [f"- {'買進' if orders[c]['side'] == 'buy' else '賣出'} {nm(c)} {orders[c]['shares']} 股：{why}" for c, why in not_filled]
    L.append(f"現金 {bot.cash:,.0f} 元。_模擬操作，不是買賣建議；依盤中零股五檔模擬市價單（買往上吃賣單、賣往下吃買單）。_")
    text = '\n'.join(L)
    print(text)
    if dry_run:
        print('[dry-run] 未寫入帳戶、未推送')
        return 0
    data = bot.to_dict()
    data['start'], data['updated'] = saved.get('start'), now.isoformat(timespec='seconds')
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    if view:
        # 只更新操作台會變的部分（淨值走勢等收盤後再算）
        named = lambda x: {**x, 'name': names.get(x['code'], '')}
        view.update(updated=data['updated'], cash=round(bot.cash, 2),
                    positions=[named({'code': c, **p}) for c, p in bot.positions.items()],
                    pending=[named(o) for o in bot.pending], trades=[named(t) for t in bot.trades[-200:]][::-1])
        view_path.write_text(json.dumps(view, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    if (executed or not_filled) and os.getenv('PAPER_BOT_AUTO_SEND', '').strip() == '1':
        from src.app.alerts.notify import send_discord
        webhook = (os.getenv('PAPER_BOT_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK_URL') or '').strip()
        if webhook:
            send_discord(webhook, text)
            print('[sent] 已推送到 Discord')
    return 0


def write_view(bot, cfg, s, start, bench, nm, now) -> None:
    """操作台「模擬帳戶」分頁讀的資料：淨值與同資金 0050 走勢、持股、待執行單、交易紀錄。"""
    b = bench.copy()
    b.index = b.index.strftime('%Y-%m-%d')
    b0 = b.get(bot.equity[0]['date']) if bot.equity else None
    curve = [{'date': e['date'], 'equity': e['equity'],
              'bench': round(cfg.capital * b[e['date']] / b0, 2) if b0 and e['date'] in b.index else None}
             for e in bot.equity]
    named = lambda x: {**x, 'name': nm(x['code']).split(' ', 1)[-1] if ' ' in nm(x['code']) else ''}
    view = {
        'updated': now.isoformat(timespec='seconds'), 'start': start, 'last_date': bot.last_date,
        'capital': cfg.capital, 'cash': round(bot.cash, 2), 'summary': s, 'curve': curve,
        'config': {'max_risk_per_trade': cfg.max_risk_per_trade, 'max_position_pct': cfg.max_position_pct,
                   'max_total_risk': cfg.max_total_risk, 'stop_pct': cfg.stop_pct, 'fee_discount': FEE_DISCOUNT,
                   'breakeven_after': cfg.breakeven_after,
                   'max_per_theme': cfg.max_per_theme,
                   'min_fee': MIN_FEE},
        'positions': [named({'code': c, **p}) for c, p in bot.positions.items()],
        'pending': [named(o) for o in bot.pending],
        'trades': [named(t) for t in bot.trades[-200:]][::-1],
    }
    (STATE / 'latest.json').write_text(json.dumps(view, ensure_ascii=False, indent=1, default=float), encoding='utf-8')


def run_daily(log, send: bool, reset: bool, dry_run: bool = False) -> int:
    load_dotenv(_ROOT / '.env')
    if PAUSED.exists() and not (reset or dry_run):
        print(f'[skip] 模擬帳戶已暫停（{PAUSED.read_text(encoding="utf-8").strip()}）；刪除 state/paper_bot/paused 或在操作台按「恢復」即可繼續')
        return 0
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    today = now.date()
    end = today - timedelta(days=1)
    if now.hour * 60 + now.minute >= 15 * 60 + 30 and today.weekday() < 5 \
            and all(not mk.quotes_day(m, today).empty for m in mk.MARKETS):
        end = today
    path = STATE / 'state.json'
    saved = None if reset or not path.exists() else json.loads(path.read_text(encoding='utf-8'))
    bot = Bot.from_dict(saved) if saved else None
    built = mk.build_market_dataset(mk.MARKETS, start='2025-01-01', chips_start=None, today=today, end=end, log=log)
    ds, hist = built['ds'], built['history']
    px = ds.prices
    codes = [c for c in built['codes'] if c in px.columns]
    cfg = bot.cfg if bot else config_from_portfolio()
    ma = px[codes].rolling(cfg.ma_days, min_periods=cfg.ma_days).mean()
    latest, pos2, neg2 = revenue_panels(ds, px.index, codes)
    ev = mk.events(mk.MARKETS, today.year - 1, today.year)
    days = px.index if bot is None else px.index[px.index > pd.Timestamp(bot.last_date)]
    if bot is None:
        bot = Bot.new(cfg)
        days = px.index[-1:]                       # 新帳戶：從最新一個交易日收盤後開始排單
    start_date = saved['start'] if saved else days[0].strftime('%Y-%m-%d')
    executed = []
    for d in days:
        ds_ = d.strftime('%Y-%m-%d')
        opens, closes = raw_quote(d, 'open'), raw_quote(d, 'close')
        get = lambda s: (lambda c: float(s[c]) if c in s.index and pd.notna(s[c]) and s[c] > 0 else None)
        i = px.index.get_loc(d)
        prev_closes = raw_quote(px.index[i - 1], 'close') if i > 0 else pd.Series(dtype=float)
        sides = {o['code']: o['side'] for o in bot.pending}
        # 09:30 沒執行到的單：以開盤價補成交，開盤漲停（≥ +9.5%）買不到、跌停（≤ -9.5%）賣不掉
        executed += bot.execute(ds_, limit_gate(get(opens), get(prev_closes), sides))
        bot.dividends(ds_, ev[ev['date'] == d])
        bot.mark(ds_, get(closes))
        members = [c for c in members_on(hist, d) if c in codes]
        # 停損、排單股數用當天原始收盤（與用原始開盤價設的停損一致）；均線用還原價
        raw = closes.reindex(codes).to_frame().T.set_axis([d])
        bot.decide(ds_, make_context(d, raw, ma, latest, pos2, neg2, members))
    data = bot.to_dict()
    data['start'] = start_date
    data['updated'] = now.isoformat(timespec='seconds')
    STATE.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    names = stock_names(ds, today)
    nm = lambda c: f"{c} {names.get(c, '')}".strip()
    s = summarize(bot, px['0050'])
    start = data['start']
    L = [f"## 模擬帳戶 {bot.last_date}（營收動能・自 {start} 起）",
         f"淨值 {s['equity']:,.0f} 元（起始 {cfg.capital:,.0f}，{pct(s.get('total_return'))}；同期 0050 {pct(s.get('bench_total_return'))}），"
         f"現金 {bot.cash:,.0f} 元"]
    if executed:
        L.append('\n**今天開盤已成交（模擬）**')
        L += [f"- {'買進' if t['side'] == 'buy' else '賣出'} {nm(t['code'])} {t['shares']} 股 @ {t['price']:,.2f}"
              + (f"，停損 {t['stop']:,.2f}" if t['side'] == 'buy' else f"，損益 {t['pnl']:+,.0f} 元（{t['reason']}）")
              for t in executed]
    if bot.pending:
        L.append('\n**下一個交易日 09:30 要執行（模擬單）**')
        L += [f"- {'買進' if o['side'] == 'buy' else '賣出'} {nm(o['code'])} {o['shares']} 股（參考收盤 {o['ref_price']:,.2f}）"
              f"：{o['reason']}" + (f"，停損約 {o['stop_est']:,.2f}" if o['side'] == 'buy' else '') for o in bot.pending]
    else:
        L.append('\n明天沒有要執行的單。')
    if bot.positions:
        L.append('\n**目前持股**')
        for c, p in bot.positions.items():
            last = p.get('last', p['entry'])
            L.append(f"- {nm(c)} {p['shares']} 股，成本 {p['entry']:,.2f}，現價 {last:,.2f}（{pct(last / p['entry'] - 1)}），停損 {p['stop']:,.2f}")
            seen = p.get('last_date') or p['entry_date']
            if (pd.Timestamp(bot.last_date) - pd.Timestamp(seen)).days >= 5:
                L.append(f"  ⚠️ 自 {seen} 起沒有成交價（停牌或下市），模擬帳戶無法賣出，請自行確認這檔的狀況")
    L.append('_模擬操作，不是買賣建議；真實下單請自行判斷。成交假設：隔天 09:30 依盤中零股五檔（漲停買不到、跌停賣不掉；09:30 沒執行到則以開盤價），手續費 2.8 折（最低 1 元）。'
             + (f'漲到成本 +{cfg.breakeven_after:.0%} 後停損移到成本價。' if cfg.breakeven_after else '')
             + (f'同一族群最多 {cfg.max_per_theme} 檔。' if cfg.max_per_theme else '') + '_')
    text = '\n'.join(L)
    print(text)
    if dry_run:
        print('[dry-run] 未寫入帳戶、未推送')
        return 0
    (STATE / 'latest.txt').write_text(text, encoding='utf-8')
    write_view(bot, cfg, s, start, px['0050'], nm, now)
    # 每日推送：--send 或 .env 的 PAPER_BOT_AUTO_SEND=1；優先送到模擬帳戶專用頻道 PAPER_BOT_WEBHOOK_URL
    if len(days) == 0:
        print('[skip] 沒有新的交易日（休市或今天已處理），不重複推送')
        return 0
    messages = [text]
    month = monthly_scorecard_due(bot.last_date, start)      # 每月第一個交易日收盤後：上個月的成績單
    made = make_scorecard(month) if month else make_scorecard()
    if month and made:
        messages.append(made[1])
        print(made[1])
    if send or os.getenv('PAPER_BOT_AUTO_SEND', '').strip() == '1':
        from src.app.alerts.notify import send_discord
        webhook = (os.getenv('PAPER_BOT_WEBHOOK_URL') or os.getenv('DISCORD_WEBHOOK_URL') or '').strip()
        if not webhook:
            print('[error] .env 未設定 PAPER_BOT_WEBHOOK_URL 或 DISCORD_WEBHOOK_URL，未推送')
            return 1
        for m in messages:
            send_discord(webhook, m)
        print('[sent] 已推送到 Discord')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='模擬操作機器人')
    p.add_argument('--backtest', action='store_true', help='歷史模擬 2018 年至今')
    p.add_argument('--send', action='store_true', help='有成交或新單時推送 Discord')
    p.add_argument('--reset', action='store_true', help='重新開始模擬帳戶')
    p.add_argument('--dry-run', action='store_true', help='只顯示，不寫入帳戶、不推送')
    p.add_argument('--execute-now', action='store_true', help='09:30：用即時報價執行待執行的模擬單')
    p.add_argument('--scorecard', action='store_true', help='產生目前的成績單（不推送；每月第一個交易日收盤後會自動產生並推送）')
    args = p.parse_args()
    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr)
    if args.execute_now:
        return run_execute(log, args.dry_run)
    if args.scorecard:
        made = make_scorecard()
        print(made[1] if made else '[skip] 需要先有模擬帳戶與歷史模擬淨值（python scripts/paper_bot.py --backtest）')
        return 0
    return run_backtest(log) if args.backtest else run_daily(log, args.send, args.reset, args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
