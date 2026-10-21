"""個股研究卡的資料來源（目前只支援上市公司）。全部有磁碟快取並節流，避免被證交所封鎖。

來源（2026-10 實測可用）：
  月營收      公開資訊觀測站 mopsov  /nas/t21/sii/t21sc03_{民國年}_{月}_0.html（每月一頁、全部上市公司）
  季度損益    公開資訊觀測站 mopsov  /mops/web/ajax_t163sb04（每季一頁、累計數）
  估值        證交所 /rwd/zh/afterTrading/BWIBBU（每檔每月：殖利率、本益比、股價淨值比）
  三大法人    證交所 /rwd/zh/fund/T86（每日、全部上市股票）
  融資融券    證交所 /rwd/zh/marginTrading/MI_MARGN（每日、全部上市股票）
"""
from __future__ import annotations
import io
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

CACHE = Path('data/raw/research')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) stock-agent research'}
MIN_INTERVAL = 3.0  # 同一主機兩次請求最少間隔（秒）
HOST_INTERVAL = {'mopsov': 4.0}  # 公開資訊觀測站較容易逾時，放慢一點
RETRY_WAITS = (10, 20, 40)       # 網路錯誤或 5xx 時的重試等待（秒）
_last_call: dict[str, float] = {}


class NoData(Exception):
    """來源回應正常但沒有資料（休市、尚未公布）。"""


def _throttle(host: str) -> None:
    wait = HOST_INTERVAL.get(host, MIN_INTERVAL) - (time.monotonic() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _get(url: str, *, params=None, data=None, host: str, encoding: str | None = None) -> requests.Response:
    fn = requests.post if data is not None else requests.get
    for attempt, wait in enumerate((0,) + RETRY_WAITS):
        if wait:
            time.sleep(wait)
        _throttle(host)
        try:
            r = fn(url, params=params, data=data, headers=HEADERS, timeout=30)
            if r.status_code >= 500:
                raise requests.HTTPError(f'{r.status_code} server error', response=r)
            r.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            transient = not isinstance(e, requests.HTTPError) or (e.response is not None and e.response.status_code >= 500)
            if not transient or attempt == len(RETRY_WAITS):
                raise
    if encoding:
        r.encoding = encoding
    return r


def _cached(path: Path, fresh: bool, loader):
    """fresh=True 表示快取永久有效（已公布、不會再變的資料）；否則一天內有效。"""
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if fresh or age < 86400:
            return pd.read_json(io.StringIO(path.read_text(encoding='utf-8')), orient='split', dtype=False)
    df = loader()
    if df.empty and not fresh:
        return df  # 可能只是尚未公布，不快取
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(df.to_json(orient='split', force_ascii=False), encoding='utf-8')
    return df


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(',', '').strip()
    if s in ('', '--', '-', 'N/A', 'nan', 'None'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _months_ago(d: date, n: int) -> tuple[int, int]:
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m


# ---------------- 月營收 ----------------

def parse_revenue_html(text: str) -> pd.DataFrame:
    """月營收頁面依「產業別：XX」分段，每段一張表。"""
    rows = []
    parts = re.split(r'產業別：([^<（(]+)', text)
    for industry, seg in zip(parts[1::2], parts[2::2]):
        try:
            tables = pd.read_html(io.StringIO(seg), flavor='lxml')
        except ValueError:
            continue
        for t in tables:
            if t.shape[1] < 10:
                continue
            for _, row in t.iterrows():
                code = str(row.iloc[0]).strip()
                if not (code.isdigit() and len(code) == 4):
                    continue
                vals = [_num(x) for x in row.iloc[2:10]]
                if vals[0] is None:
                    continue
                note = str(row.iloc[10]).strip() if t.shape[1] > 10 else ''
                rows.append({'code': code, 'name': str(row.iloc[1]).strip(), 'industry': industry.strip(),
                             'revenue': vals[0], 'prev_month': vals[1], 'last_year': vals[2], 'mom_pct': vals[3],
                             'yoy_pct': vals[4], 'cum': vals[5], 'cum_last': vals[6], 'cum_yoy_pct': vals[7],
                             'note': '' if note in ('-', 'nan') else note})
    return pd.DataFrame(rows).drop_duplicates('code') if rows else pd.DataFrame()


def monthly_revenue(year: int, month: int, *, today: date | None = None, market: str = 'sii') -> pd.DataFrame:
    """某月全部上市（market='sii'）或上櫃（'otc'）公司營收，含 KY 等外國企業。
    欄位：code, name, industry, revenue, prev_month, last_year, mom_pct, yoy_pct, cum, cum_last, cum_yoy_pct, note
    觀測站把本國企業（..._0.html）與外國企業（..._1.html，KY 公司）分成兩頁，分別快取。"""
    today = today or date.today()
    roc = year - 1911
    # 兩個月前以前的資料視為定案（之後偶有更正，影響小）
    settled = (year, month) <= _months_ago(today, 2)
    base = 'revenue' if market == 'sii' else f'revenue_{market}'

    def page(kind: int):
        def load():
            url = f'https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_{kind}.html'
            r = _get(url, host='mopsov', encoding='big5')
            df = parse_revenue_html(r.text)
            if df.empty and kind == 0:
                raise NoData(f'{year}-{month:02d} 月營收尚未公布')
            return df
        return load

    path = CACHE / base / f'{year}{month:02d}.json'
    df = _cached(path, settled, page(0))
    if 'industry' not in df.columns:   # 舊版快取沒有產業別，重抓一次
        path.unlink(missing_ok=True)
        df = _cached(path, settled, page(0))
    try:
        foreign = _cached(CACHE / f'{base}_foreign' / f'{year}{month:02d}.json', settled, page(1))
    except requests.RequestException:
        foreign = pd.DataFrame()     # 外國企業頁抓不到時，至少回傳本國企業
    if not foreign.empty:
        df = pd.concat([df, foreign], ignore_index=True).drop_duplicates('code')
    return df


def revenue_history(code: str, months: int = 25, *, today: date | None = None) -> pd.DataFrame:
    """某檔最近 months 個已公布月份的營收。index = 'YYYY-MM'。"""
    today = today or date.today()
    out = []
    for n in range(1, months + 3):  # 本月一定還沒公布；多往前抓兩個月以補足
        y, m = _months_ago(today, n)
        try:
            df = monthly_revenue(y, m, today=today)
        except NoData:
            continue
        row = df[df['code'] == code]
        if not row.empty:
            out.append({'month': f'{y}-{m:02d}', **row.iloc[0].to_dict()})
        if len(out) >= months:
            break
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).set_index('month').sort_index()


# ---------------- 季度損益 ----------------

_INCOME_COLS = {
    'revenue': ('營業收入',),
    'gross_profit': ('營業毛利（毛損）淨額', '營業毛利（毛損）'),
    'op_income': ('營業利益（損失）',),
    'net_income': ('淨利（淨損）歸屬於母公司業主', '本期淨利（淨損）'),
    'eps': ('基本每股盈餘（元）',),
}


def quarterly_income(year: int, season: int, *, today: date | None = None, market: str = 'sii') -> pd.DataFrame:
    """某季全部上市公司綜合損益（當年累計數，單位：千元；EPS 為元）。"""
    today = today or date.today()
    path = CACHE / ('income' if market == 'sii' else f'income_{market}') / f'{year}Q{season}.json'
    settled = date(year, season * 3, 1) + timedelta(days=200) < today

    def load():
        r = _get('https://mopsov.twse.com.tw/mops/web/ajax_t163sb04', host='mopsov', encoding='utf-8',
                 data={'encodeURIComponent': 1, 'step': 1, 'firstin': 1, 'off': 1, 'isQuery': 'Y',
                       'TYPEK': market, 'year': str(year - 1911), 'season': f'{season:02d}'})
        if '<table' not in r.text:
            raise NoData(f'{year}Q{season} 財報尚未公布')
        rows = []
        for t in pd.read_html(io.StringIO(r.text), flavor='lxml'):
            cols = [''.join(str(c).split()) for c in t.columns]  # 表頭含換行，例如「公司 代號」
            if '公司代號' not in cols:
                continue
            t.columns = cols
            pick = {}
            for key, names in _INCOME_COLS.items():
                pick[key] = next((n for n in names if n in cols), None)
            if not pick['revenue'] or not pick['eps']:
                continue  # 金融、保險業格式不同，第一版略過
            for _, row in t.iterrows():
                code = str(row['公司代號']).strip()
                if not code.isdigit():
                    continue
                rows.append({'code': code, **{k: (_num(row[c]) if c else None) for k, c in pick.items()}})
        if not rows:
            raise NoData(f'{year}Q{season} 解析不到損益資料')
        return pd.DataFrame(rows).drop_duplicates('code')

    return _cached(path, settled, load)


def income_history(code: str, quarters: int = 9, *, today: date | None = None) -> pd.DataFrame:
    """單季損益（由累計數相減）。index = 'YYYYQn'；欄位 revenue, gross_profit, op_income, net_income, eps。"""
    today = today or date.today()
    cum = {}
    y, q = today.year, (today.month - 1) // 3 + 1
    tries = 0
    while len(cum) < quarters + 4 and tries < quarters + 8:  # 多抓以便相減與計算年增
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        tries += 1
        try:
            df = quarterly_income(y, q, today=today)
        except NoData:
            continue
        row = df[df['code'] == code]
        if not row.empty:
            cum[(y, q)] = row.iloc[0].to_dict()
    single = {}
    for (y, q), vals in cum.items():
        if q == 1:
            single[(y, q)] = vals
        elif (y, q - 1) in cum:
            prev = cum[(y, q - 1)]
            single[(y, q)] = {k: (vals[k] - prev[k] if vals.get(k) is not None and prev.get(k) is not None else None)
                              for k in _INCOME_COLS}
    if not single:
        return pd.DataFrame()
    df = pd.DataFrame([{'quarter': f'{y}Q{q}', **{k: v.get(k) for k in _INCOME_COLS}}
                       for (y, q), v in sorted(single.items())]).set_index('quarter')
    return df.iloc[-quarters:]


# ---------------- 估值 ----------------

def valuation_month(code: str, year: int, month: int, *, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    path = CACHE / 'valuation' / code / f'{year}{month:02d}.json'
    settled = (year, month) < (today.year, today.month)

    def load():
        js = _get('https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU', host='twse',
                  params={'date': f'{year}{month:02d}01', 'stockNo': code, 'response': 'json'}).json()
        if js.get('stat') != 'OK' or not js.get('data'):
            return pd.DataFrame(columns=['date', 'dividend_yield', 'pe', 'pb'])
        f = js['fields']
        i_d, i_y, i_pe, i_pb = f.index('日期'), f.index('殖利率(%)'), f.index('本益比'), f.index('股價淨值比')
        rows = []
        for r in js['data']:
            y_, m_, d_ = [int(x) for x in r[i_d].replace('年', '/').replace('月', '/').replace('日', '').split('/')]
            rows.append({'date': f'{y_ + 1911}-{m_:02d}-{d_:02d}', 'dividend_yield': _num(r[i_y]),
                         'pe': _num(r[i_pe]), 'pb': _num(r[i_pb])})
        return pd.DataFrame(rows)

    return _cached(path, settled, load)


def valuation_history(code: str, months: int = 36, *, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    frames = []
    for n in range(months - 1, -1, -1):
        y, m = _months_ago(today, n)
        frames.append(valuation_month(code, y, m, today=today))
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df
    return df.drop_duplicates('date').set_index('date').sort_index()


# ---------------- 籌碼 ----------------

def institutional_day(d: date) -> pd.DataFrame:
    """某日三大法人買賣超（股）。休市回傳空表。"""
    path = CACHE / 'institutional' / f'{d:%Y%m%d}.json'

    def load():
        js = _get('https://www.twse.com.tw/rwd/zh/fund/T86', host='twse',
                  params={'date': f'{d:%Y%m%d}', 'selectType': 'ALLBUT0999', 'response': 'json'}).json()
        if js.get('stat') != 'OK':
            return pd.DataFrame(columns=['code', 'foreign', 'trust', 'dealer', 'total'])
        f = js['fields']
        idx = {k: f.index(n) for k, n in [('foreign', '外陸資買賣超股數(不含外資自營商)'), ('trust', '投信買賣超股數'),
                                          ('dealer', '自營商買賣超股數'), ('total', '三大法人買賣超股數')]}
        return pd.DataFrame([{'code': r[0].strip(), **{k: _num(r[i]) for k, i in idx.items()}} for r in js['data']])

    # 今天的資料可能尚未公布，只快取過去的日子
    return _cached(path, d < date.today(), load)


def margin_day(d: date) -> pd.DataFrame:
    """某日融資融券餘額（融資單位：張；融券單位：張）。"""
    path = CACHE / 'margin' / f'{d:%Y%m%d}.json'

    def load():
        js = _get('https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN', host='twse',
                  params={'date': f'{d:%Y%m%d}', 'selectType': 'STOCK', 'response': 'json'}).json()
        if js.get('stat') != 'OK':
            return pd.DataFrame(columns=['code', 'margin_balance', 'short_balance'])
        table = next((t for t in js.get('tables', []) if t.get('fields') and '代號' in t['fields']), None)
        if not table:
            return pd.DataFrame(columns=['code', 'margin_balance', 'short_balance'])
        # 欄位：代號,名稱, 融資(買進,賣出,現金償還,前日餘額,今日餘額,限額), 融券(買進,賣出,現券償還,前日餘額,今日餘額,限額),...
        return pd.DataFrame([{'code': r[0].strip(), 'margin_balance': _num(r[6]), 'short_balance': _num(r[12])}
                             for r in table['data']])

    return _cached(path, d < date.today(), load)


def trading_days_back(today: date, n: int, *, probe=institutional_day) -> list[date]:
    """往回找最近 n 個有資料的交易日（以三大法人資料判斷是否開市）。"""
    days, d, checked = [], today, 0
    while len(days) < n and checked < n * 2 + 10:
        if d.weekday() < 5:
            if not probe(d).empty:
                days.append(d)
        d -= timedelta(days=1)
        checked += 1
    return sorted(days)
