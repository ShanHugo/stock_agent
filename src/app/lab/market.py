"""上市（證交所）＋上櫃（櫃買中心）全市場逐日資料，含之後下市的公司。用於產業別選股範圍的回測。

來源（2026-10 實測可查歷史日期）：
  每日行情      證交所 MI_INDEX type=ALLBUT0999；櫃買 /www/zh-tw/afterTrading/otc type=EW
  產業別名單    同上，type=24～31（電子 8 類），每月最後一個交易日取一次 → 當時的產業分類，含之後下市者
  除權息        證交所 TWT49U；櫃買 /www/zh-tw/bulletin/exDailyQ（除權息前收盤價、參考價）
  減資          證交所 TWTAUU；櫃買 /www/zh-tw/bulletin/revivt（停止買賣前收盤價、恢復買賣參考價）
  本益比        證交所 BWIBBU_d（lab.data.valuation_day）；櫃買 /www/zh-tw/afterTrading/peQryDate
  三大法人      證交所 T86（research.sources）；櫃買 /www/zh-tw/insti/dailyTrade
  融資          證交所 MI_MARGN（research.sources）；櫃買 /www/zh-tw/margin/balance

還原股價：收盤價 × 之後所有除權息／減資事件的（參考價 ÷ 事件前收盤價）連乘，與 Yahoo 還原價同一概念。
已下市公司的月營收、財報在公開資訊觀測站查不到（「不繼續公開發行」），這兩項特徵對它們是缺值。
"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..research import sources as rs

CACHE = Path('data/raw/lab/market')
MARKETS = ('twse', 'tpex')
ELECTRONICS = {24: '半導體業', 25: '電腦及週邊設備業', 26: '光電業', 27: '通信網路業',
               28: '電子零組件業', 29: '電子通路業', 30: '資訊服務業', 31: '其他電子業'}
TPEX = 'https://www.tpex.org.tw/www/zh-tw'
_lock = threading.Lock()


def _roc(s: str) -> pd.Timestamp:
    """'108/01/30'、'108年01月30日'、'1080130' → Timestamp。"""
    s = s.strip().replace('年', '/').replace('月', '/').replace('日', '')
    if '/' not in s:
        s = f'{s[:-4]}/{s[-4:-2]}/{s[-2:]}'
    y, m, d = s.split('/')
    return pd.Timestamp(int(y) + 1911, int(m), int(d))


def _past(d: date) -> bool:
    return d < date.today()


def _empty(cols):
    return pd.DataFrame(columns=cols)


# ---------- 每日行情 ----------

def _twse_table(js: dict, first_field: str = '證券代號'):
    for t in js.get('tables', []):
        if t.get('fields') and t['fields'][0] == first_field and t.get('data'):
            return t
    return None


def parse_twse_quotes(js: dict) -> pd.DataFrame:
    t = _twse_table(js)
    if not t:
        return _empty(['code', 'open', 'close', 'volume'])
    f = t['fields']
    io_, ic, iv = f.index('開盤價'), f.index('收盤價'), f.index('成交股數')
    return pd.DataFrame([{'code': r[0].strip(), 'open': rs._num(r[io_]), 'close': rs._num(r[ic]), 'volume': rs._num(r[iv])}
                         for r in t['data']])


def parse_tpex_quotes(js: dict) -> pd.DataFrame:
    tables = js.get('tables') or []
    if not tables or not tables[0].get('data'):
        return _empty(['code', 'open', 'close', 'volume'])
    t = tables[0]
    f = [x.strip() for x in t['fields']]
    io_, ic, iv = f.index('開盤'), f.index('收盤'), f.index('成交股數')
    return pd.DataFrame([{'code': r[0].strip(), 'open': rs._num(r[io_]), 'close': rs._num(r[ic]), 'volume': rs._num(r[iv])}
                         for r in t['data']])


def quotes_day(market: str, d: date) -> pd.DataFrame:
    """某日全部股票收盤價與成交股數。休市回傳空表（過去日期的空表也會快取）。"""
    path = CACHE / market / 'quotes' / f'{d:%Y%m%d}.json'

    def load():
        if market == 'twse':
            js = rs._get('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX', host='twse',
                         params={'date': f'{d:%Y%m%d}', 'type': 'ALLBUT0999', 'response': 'json'}).json()
            return parse_twse_quotes(js)
        js = rs._get(f'{TPEX}/afterTrading/otc', host='tpex',
                     params={'date': f'{d:%Y/%m/%d}', 'type': 'EW', 'response': 'json'}).json()
        return parse_tpex_quotes(js)

    return rs._cached(path, _past(d), load)


def industry_day(market: str, d: date, code: int) -> list[str]:
    """某日某產業（代碼 24～31）的股票代號。"""
    path = CACHE / market / 'industry' / f'{d:%Y%m%d}_{code}.json'

    def load():
        if market == 'twse':
            js = rs._get('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX', host='twse',
                         params={'date': f'{d:%Y%m%d}', 'type': str(code), 'response': 'json'}).json()
            df = parse_twse_quotes(js)
        else:
            js = rs._get(f'{TPEX}/afterTrading/otc', host='tpex',
                         params={'date': f'{d:%Y/%m/%d}', 'type': str(code), 'response': 'json'}).json()
            df = parse_tpex_quotes(js)
        return df[['code']]

    return list(rs._cached(path, _past(d), load)['code'].astype(str))


# ---------- 除權息、減資 ----------

def _events_year(market: str, kind: str, year: int) -> pd.DataFrame:
    """kind = 'ex'（除權息）或 'cr'（減資）。欄位：date, code, prev_close, ref。"""
    path = CACHE / market / kind / f'{year}.json'
    s, e = date(year, 1, 1), min(date(year, 12, 31), date.today())

    def load():
        if market == 'twse':
            url = ('https://www.twse.com.tw/rwd/zh/exRight/TWT49U' if kind == 'ex'
                   else 'https://www.twse.com.tw/rwd/zh/reducation/TWTAUU')
            js = rs._get(url, host='twse', params={'startDate': f'{s:%Y%m%d}', 'endDate': f'{e:%Y%m%d}',
                                                    'response': 'json'}).json()
            f, data = js.get('fields') or [], js.get('data') or []
            names = (['資料日期', '股票代號', '除權息前收盤價', '除權息參考價'] if kind == 'ex'
                     else ['恢復買賣日期', '股票代號', '停止買賣前收盤價格', '恢復買賣參考價'])
        else:
            url = f'{TPEX}/bulletin/exDailyQ' if kind == 'ex' else f'{TPEX}/bulletin/revivt'
            js = rs._get(url, host='tpex', params={'startDate': f'{s:%Y/%m/%d}', 'endDate': f'{e:%Y/%m/%d}',
                                                   'response': 'json'}).json()
            t = (js.get('tables') or [{}])[0]
            f, data = t.get('fields') or [], t.get('data') or []
            names = (['除權息日期', '代號', '除權息前收盤價', '除權息參考價'] if kind == 'ex'
                     else ['恢復買賣日期', '股票代號', '最後交易日之收盤價格', '減資恢復買賣開始日參考價格'])
        if not data:
            return _empty(['date', 'code', 'prev_close', 'ref'])
        i = [f.index(n) for n in names]
        rows = [{'date': _roc(str(r[i[0]])).strftime('%Y-%m-%d'), 'code': str(r[i[1]]).strip(),
                 'prev_close': rs._num(r[i[2]]), 'ref': rs._num(r[i[3]])} for r in data]
        return pd.DataFrame(rows)

    return rs._cached(path, e < date.today() - timedelta(days=1) and e.month == 12, load)


def events(markets, first_year: int, last_year: int) -> pd.DataFrame:
    frames = [_events_year(m, k, y) for m in markets for k in ('ex', 'cr') for y in range(first_year, last_year + 1)]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else _empty([])
    if df.empty:
        return _empty(['date', 'code', 'prev_close', 'ref'])
    df['date'] = pd.to_datetime(df['date'])
    return df.dropna(subset=['prev_close', 'ref'])


# ---------- 本益比、法人、融資（櫃買；證交所沿用既有函式） ----------

def tpex_pe_day(d: date) -> pd.DataFrame:
    path = CACHE / 'tpex' / 'pe' / f'{d:%Y%m%d}.json'

    def load():
        js = rs._get(f'{TPEX}/afterTrading/peQryDate', host='tpex',
                     params={'date': f'{d:%Y/%m/%d}', 'response': 'json'}).json()
        t = (js.get('tables') or [{}])[0]
        if not t.get('data'):
            return _empty(['code', 'pe'])
        i = t['fields'].index('本益比')
        return pd.DataFrame([{'code': str(r[0]).strip(), 'pe': rs._num(r[i])} for r in t['data']])

    return rs._cached(path, _past(d), load)


def tpex_institutional_day(d: date) -> pd.DataFrame:
    path = CACHE / 'tpex' / 'institutional' / f'{d:%Y%m%d}.json'

    def load():
        js = rs._get(f'{TPEX}/insti/dailyTrade', host='tpex',
                     params={'type': 'Daily', 'sect': 'EW', 'date': f'{d:%Y/%m/%d}', 'response': 'json'}).json()
        t = (js.get('tables') or [{}])[0]
        if not t.get('data') or len(t['fields']) != 24:
            # 24 欄：外資(不含自營商)、外資自營商、外資合計、投信、自營商自行、自營商避險、自營商合計 各「買/賣/超」
            return _empty(['code', 'foreign', 'trust'])
        return pd.DataFrame([{'code': str(r[0]).strip(), 'foreign': rs._num(r[4]), 'trust': rs._num(r[13])}
                             for r in t['data']])

    return rs._cached(path, _past(d), load)


def tpex_margin_day(d: date) -> pd.DataFrame:
    path = CACHE / 'tpex' / 'margin' / f'{d:%Y%m%d}.json'

    def load():
        js = rs._get(f'{TPEX}/margin/balance', host='tpex', params={'date': f'{d:%Y/%m/%d}', 'response': 'json'}).json()
        t = (js.get('tables') or [{}])[0]
        if not t.get('data'):
            return _empty(['code', 'margin_balance'])
        i = t['fields'].index('資餘額')
        return pd.DataFrame([{'code': str(r[0]).strip(), 'margin_balance': rs._num(r[i])} for r in t['data']])

    return rs._cached(path, _past(d), load)


# ---------- 回補（每個主機一條執行緒，各自節流） ----------

def weekdays(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, end)]


def prefetch(markets, start: date, end: date, chips_start: date | None, log=print) -> None:
    """把回測需要的逐日資料都抓進快取。證交所與櫃買各一條執行緒，同主機仍維持 3 秒間隔。"""
    from .data import valuation_day

    def run(market):
        days = weekdays(start, end)
        open_days = []
        for i, d in enumerate(days):
            if not quotes_day(market, d).empty:
                open_days.append(d)
            if i % 100 == 0:
                with _lock:
                    log(f'[{market}] 行情 {d} ({i + 1}/{len(days)})')
        for y in range(start.year, end.year + 1):
            for k in ('ex', 'cr'):
                _events_year(market, k, y)
        s = pd.Series(open_days, index=pd.to_datetime(open_days))
        month_ends = list(s.groupby(s.index.to_period('M')).max())
        for i, d in enumerate(month_ends):
            for code in ELECTRONICS:
                industry_day(market, d, code)
            if market == 'twse':
                valuation_day(d)
            else:
                tpex_pe_day(d)
            if i % 12 == 0:
                with _lock:
                    log(f'[{market}] 產業名單與本益比 {d} ({i + 1}/{len(month_ends)})')
        chip_days = [d for d in open_days if d >= chips_start] if chips_start else []
        for i, d in enumerate(chip_days):
            if market == 'twse':
                rs.institutional_day(d)
                rs.margin_day(d)
            else:
                tpex_institutional_day(d)
                tpex_margin_day(d)
            if i % 100 == 0:
                with _lock:
                    log(f'[{market}] 法人與融資 {d} ({i + 1}/{len(chip_days)})')

    with ThreadPoolExecutor(len(markets)) as ex:
        for f in [ex.submit(run, m) for m in markets]:
            f.result()


# ---------- 組成面板 ----------

def trading_days(markets, start: date, end: date) -> pd.DatetimeIndex:
    days = [d for d in weekdays(start, end) if any(not quotes_day(m, d).empty for m in markets)]
    return pd.DatetimeIndex(pd.to_datetime(days))


def raw_panels(markets, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(收盤價, 成交股數)，index=交易日，columns=代號。同一代號兩邊都有（轉上市當天）時以證交所為準。"""
    close, vol = {}, {}
    for d in days:
        parts = [quotes_day(m, d.date()) for m in reversed(markets)]   # 後面的覆蓋前面的 → 證交所優先
        df = pd.concat([p for p in parts if not p.empty]).drop_duplicates('code', keep='last').set_index('code')
        close[d], vol[d] = df['close'], df['volume']
    return pd.DataFrame(close).T.sort_index(), pd.DataFrame(vol).T.sort_index()


def adjust_prices(close: pd.DataFrame, ev: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """還原股價。事件因子 = 參考價 ÷ 事件前收盤價，事件日以前的價格全部乘上該因子。
    另外：單日漲跌超過當時漲跌幅限制（2015-06-01 前 7%、之後 10%，容許 1.5 個百分點）又沒有事件可解釋時，
    視為未收錄的股權變動，把那一天的報酬歸零（上市前 5 個交易日沒有漲跌幅限制，不處理）。"""
    close = close.sort_index()
    adj = close.copy()
    notes = []
    ev = ev[ev['code'].isin(close.columns) & (ev['prev_close'] > 0) & (ev['ref'] > 0)]
    factor = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    for _, e in ev.iterrows():
        # 事件生效日當天及之後的價格不動，之前的價格乘上 ref/prev_close
        pos = close.index.searchsorted(e['date'])
        if 0 < pos <= len(close.index):
            factor.iloc[:pos, close.columns.get_loc(e['code'])] *= e['ref'] / e['prev_close']
    adj = close * factor
    # 上市期間內補值（停牌日沿用前一日），下市後保持缺值
    first, last = adj.apply(pd.Series.first_valid_index), adj.apply(pd.Series.last_valid_index)
    filled = adj.ffill()
    span = pd.DataFrame(False, index=adj.index, columns=adj.columns)
    for c in adj.columns:
        if first[c] is not None:
            span.loc[first[c]:last[c], c] = True
    adj = filled.where(span)
    # 未收錄股權變動：超過漲跌幅限制的單日報酬
    ret = adj.pct_change(fill_method=None)
    limit = pd.Series(np.where(adj.index < pd.Timestamp('2015-06-01'), 0.07, 0.10), index=adj.index) + 0.015
    young = adj.notna().cumsum() <= 5
    bad = ret.abs().gt(limit, axis=0) & ~young
    if bad.any().any():
        n = int(bad.sum().sum())
        notes.append(f'{n} 個單日報酬超過漲跌幅限制且無除權息／減資紀錄，已視為股權變動歸零')
        ret = ret.mask(bad, 0.0)
        growth = (1 + ret.fillna(0.0)).cumprod()
        base = adj.bfill().iloc[0]
        adj = (growth * base).where(span)
    return adj, notes


def build_universe(markets, days: pd.DatetimeIndex, industries=ELECTRONICS) -> tuple[list, dict]:
    """[(生效日, 名單)] 由舊到新，以及 {代號: (市場, 最後一次的產業別)}。名單取每月最後一個交易日。"""
    s = pd.Series(days, index=days)
    month_ends = list(s.groupby(days.to_period('M')).max())
    periods, info = [], {}
    for d in month_ends:
        members = set()
        for m in markets:
            for code, name in industries.items():
                codes = industry_day(m, d.date(), code)
                members |= set(codes)
                info.update({c: (m, name) for c in codes})
        if members:
            periods.append((d, members))
    if not periods:
        raise rs.NoData('沒有任何月份的產業名單')
    periods.insert(0, (pd.Timestamp('1900-01-01'), periods[0][1]))
    return periods, info


def pe_panel(markets, month_ends: list[pd.Timestamp], codes: list[str]) -> pd.DataFrame:
    from .data import valuation_day
    rows = {}
    for d in month_ends:
        parts = []
        if 'twse' in markets:
            parts.append(valuation_day(d.date())[['code', 'pe']])
        if 'tpex' in markets:
            parts.append(tpex_pe_day(d.date()))
        parts = [p for p in parts if not p.empty]
        if parts:                          # 當天本益比可能還沒公布（例如今天收盤後不久）
            rows[d] = pd.concat(parts).drop_duplicates('code').set_index('code')['pe']
    return pd.DataFrame(rows).T.reindex(columns=codes).astype(float)


MOPS_MARKET = {'twse': 'sii', 'tpex': 'otc'}
MARKET_NAME = {'twse': '上市', 'tpex': '上櫃'}
# 選股範圍名稱 → 市場
UNIVERSES = {'electronics': ('twse', 'tpex'), 'electronics-twse': ('twse',), 'electronics-tpex': ('tpex',)}


def describe(markets, built: dict) -> tuple[str, str]:
    """(簡稱, 報告用的完整說明)。"""
    mk = '＋'.join(MARKET_NAME[m] for m in markets)
    last = built['history'][-1][1]
    short = f'{mk}電子類股'
    detail = (f"{mk}「{'、'.join(ELECTRONICS.values())}」8 類，每月最後一個交易日依證交所／櫃買中心當時的產業分類重建名單，"
              f"**含之後下市的公司**。期間曾入選 {len(built['codes'])} 檔，最近一期 {len(last)} 檔；"
              f"期間內下市 {len(built['delisted'])} 檔。價格由每日收盤價自行還原除權息與減資")
    return short, detail


def build_market_dataset(markets, *, start: str, chips_start: str | None, today: date, industries=ELECTRONICS,
                         log=print, end: date | None = None):
    """回傳 dict：ds（Dataset，價格為自行還原的收盤價，含 0050）、history、info、volume、chips、codes、notes。"""
    from .data import Dataset, load_revenue, load_eps, BENCHMARK
    s, end = pd.Timestamp(start).date(), end or today - timedelta(days=1)   # 預設到昨天（今天的行情可能還沒公布）
    log('[data] 回補逐日資料（第一次需要數小時，之後讀快取）')
    prefetch(markets, s, end, pd.Timestamp(chips_start).date() if chips_start else None, log=log)
    days = trading_days(markets, s, end)
    history, info = build_universe(markets, days, industries)
    codes = sorted(set().union(*[m for _, m in history]))
    log(f'[data] 期間曾屬於選股範圍：{len(codes)} 檔；組合價格面板')
    close, volume = raw_panels(markets, days)
    cols = [c for c in codes + [BENCHMARK] if c in close.columns]
    ev = events(markets, s.year, end.year)
    prices, notes = adjust_prices(close[cols], ev)
    volume = volume.reindex(columns=cols)
    last_day = prices.index[-1]
    delisted = [c for c in codes if c in prices.columns and prices[c].last_valid_index() is not None
                and prices[c].last_valid_index() < last_day - pd.Timedelta(days=30)]
    mops = tuple(MOPS_MARKET[m] for m in markets)
    revenue = load_revenue(codes, pd.Period(start, 'M'), pd.Period(today, 'M') - 1, today, log, markets=mops)
    s_me = pd.Series(days, index=days)
    pe = pe_panel(markets, list(s_me.groupby(days.to_period('M')).max()), codes)
    eps = load_eps(codes, s.year, today, log, markets=mops)
    no_rev = [c for c in delisted if c not in revenue.columns or revenue[c].dropna().empty]
    notes.append(f'期間內下市（最後交易日早於資料末日 30 天以上）：{len(delisted)} 檔；'
                 f'其中 {len(no_rev)} 檔查不到月營收與財報（公開資訊觀測站不提供已下市公司），這兩項特徵對它們為缺值')
    chips = chips_panels(markets, days[days >= pd.Timestamp(chips_start)], codes) if chips_start else {}
    ds = Dataset(prices=prices, revenue_yoy=revenue, pe=pe, eps=eps, notes=notes)
    return {'ds': ds, 'history': history, 'info': info, 'volume': volume, 'chips': chips, 'codes': codes,
            'delisted': delisted, 'notes': notes}


def compare_with_yahoo(prices: pd.DataFrame, samples: dict[str, str], start: str) -> list[str]:
    """自行還原的價格 vs Yahoo 還原價：期間總報酬差異與日報酬相關係數。samples = {代號: Yahoo 代號}。"""
    import yfinance as yf
    out = []
    for code, ticker in samples.items():
        if code not in prices.columns:
            continue
        try:
            y = yf.download(ticker, start=start, end=(prices.index[-1] + pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
                            auto_adjust=True, progress=False)['Close'].squeeze().dropna()
        except Exception as e:      # noqa: BLE001 — Yahoo 偶爾失敗，不影響回測
            out.append(f'{code}：無法取得 Yahoo 資料（{e}）')
            continue
        y.index = pd.to_datetime(y.index).tz_localize(None)
        p = prices[code].dropna()
        idx = p.index.intersection(y.index)
        if len(idx) < 100:
            continue
        a, b = p.loc[idx], y.loc[idx]
        tr_a, tr_b = a.iloc[-1] / a.iloc[0] - 1, b.iloc[-1] / b.iloc[0] - 1
        corr = a.pct_change().corr(b.pct_change())
        out.append(f'{code}：本系統 {tr_a:+.1%}、Yahoo {tr_b:+.1%}（{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}），日報酬相關 {corr:.4f}')
    return out


def chips_panels(markets, days: pd.DatetimeIndex, codes: list[str]) -> dict[str, pd.DataFrame]:
    fields = {'foreign': {}, 'trust': {}, 'margin': {}}
    for d in days:
        inst, mg = [], []
        if 'twse' in markets:
            inst.append(rs.institutional_day(d.date())[['code', 'foreign', 'trust']])
            mg.append(rs.margin_day(d.date())[['code', 'margin_balance']])
        if 'tpex' in markets:
            inst.append(tpex_institutional_day(d.date()))
            mg.append(tpex_margin_day(d.date()))
        inst = pd.concat([x for x in inst if not x.empty]) if any(not x.empty for x in inst) else None
        if inst is not None:
            inst = inst.drop_duplicates('code').set_index('code')
            fields['foreign'][d], fields['trust'][d] = inst['foreign'], inst['trust']
        mg = pd.concat([x for x in mg if not x.empty]) if any(not x.empty for x in mg) else None
        if mg is not None:
            fields['margin'][d] = mg.drop_duplicates('code').set_index('code')['margin_balance']
    return {k: pd.DataFrame(v).T.reindex(index=days, columns=codes).astype(float) for k, v in fields.items()}
