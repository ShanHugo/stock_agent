"""盤前簡報的資料來源（2026-10 實測可用）。

  隔夜市場   Yahoo Finance（S&P 500、那斯達克、費城半導體、VIX、美 10 年債殖利率、美元兌台幣、台積電 ADR）
  台指期     期交所 OpenAPI /v1/DailyMarketReportFut（含夜盤「盤後」交易時段）
  FOMC      聯準會 fomccalendars.htm
  BEA       美國經濟分析局 news/schedule（GDP、個人所得與支出 / PCE 等）
  除權息     證交所 OpenAPI /v1/exchangeReport/TWT48U_ALL
  重大訊息   證交所 OpenAPI /v1/opendata/t187ap04_L（含法說會公告）

無法自動取得：CPI、非農就業等 BLS 發布日（BLS 對程式存取回應 403）。
"""
from __future__ import annotations
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}
ET = ZoneInfo('America/New_York')
TPE = ZoneInfo('Asia/Taipei')
MONTHS = {m: i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                                       'September', 'October', 'November', 'December'], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

# Yahoo 代號 → 顯示名稱
OVERNIGHT = {
    '^GSPC': 'S&P 500',
    '^IXIC': '那斯達克',
    '^SOX': '費城半導體',
    '^VIX': 'VIX',
    '^TNX': '美 10 年債殖利率',
    'TWD=X': '美元兌台幣',
    'TSM': '台積電 ADR',
}
TSM_ADR_RATIO = 5  # 1 股台積電 ADR = 5 股普通股


def _roc(d: str) -> date:
    """民國日期 '1151021' → date。"""
    return date(int(d[:-4]) + 1911, int(d[-4:-2]), int(d[-2:]))


# ---------------- 隔夜市場 ----------------

def last_two_closes(fetcher, symbol: str, today: date) -> dict | None:
    df = fetcher(symbol, (today - timedelta(days=14)).isoformat(), today.isoformat())
    c = df['close'].dropna()
    if len(c) < 2:
        return None
    return {'date': pd.to_datetime(c.index[-1]).strftime('%Y-%m-%d'), 'close': float(c.iloc[-1]),
            'prev': float(c.iloc[-2]), 'change_pct': float(c.iloc[-1] / c.iloc[-2] - 1)}


def overnight_markets(fetcher, today: date, errors: list[str]) -> dict:
    out = {}
    for sym, name in OVERNIGHT.items():
        try:
            q = last_two_closes(fetcher, sym, today)
            if q:
                out[sym] = {'name': name, **q}
        except Exception as e:
            errors.append(f'{name}: 抓資料失敗（{e}）')
    return out


def adr_premium(markets: dict, tw_close: float | None) -> dict | None:
    """台積電 ADR 換算每股台幣，相對台股 2330 最近收盤的溢價。"""
    if 'TSM' not in markets or 'TWD=X' not in markets or not tw_close:
        return None
    per_share = markets['TSM']['close'] * markets['TWD=X']['close'] / TSM_ADR_RATIO
    return {'per_share_twd': per_share, 'tw_close': tw_close, 'premium': per_share / tw_close - 1}


# ---------------- 台指期 ----------------

def taifex_night(rows: list[dict]) -> dict | None:
    """從期交所日報取近月台指期（TX）日盤與夜盤。夜盤漲跌改以「相對同日日盤收盤」計算。"""
    tx = [r for r in rows if r.get('Contract') == 'TX' and '/' not in r.get('ContractMonth(Week)', '')
          and len(r.get('ContractMonth(Week)', '')) == 6]
    if not tx:
        return None
    near = min(r['ContractMonth(Week)'] for r in tx)
    day = next((r for r in tx if r['ContractMonth(Week)'] == near and r['TradingSession'] == '一般'), None)
    night = next((r for r in tx if r['ContractMonth(Week)'] == near and r['TradingSession'] == '盤後'), None)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    d, n = (num(day['Last']) if day else None), (num(night['Last']) if night else None)
    return {'date': (day or night)['Date'], 'contract': near, 'day_close': d, 'night_close': n,
            'night_vs_day': (n / d - 1) if d and n else None}


def fetch_taifex() -> list[dict]:
    r = requests.get('https://openapi.taifex.com.tw/v1/DailyMarketReportFut', headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------- 美國行事曆 ----------------

def parse_fomc(html: str) -> list[dict]:
    """回傳每次會議的決議日（會議最後一天，美東 14:00 公布）。"""
    meetings = []
    for m in re.finditer(r'(\d{4}) FOMC Meetings', html):
        year = int(m.group(1))
        nxt = re.search(r'\d{4} FOMC Meetings', html[m.end():])
        seg = html[m.end(): m.end() + nxt.start()] if nxt else html[m.end():]
        for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?'
                                    r'fomc-meeting__date[^>]*>([^<]+)<', seg, re.S):
            mon, days = mon.strip(), days.strip()
            sep = '*' in days
            last_mon = mon.split('/')[-1].strip()
            last_day = re.findall(r'\d+', days)
            if last_mon[:3] not in MONTHS or not last_day:
                continue
            d = date(year, MONTHS[last_mon[:3]], int(last_day[-1]))
            meetings.append({'date': d, 'sep': sep, 'label': f'FOMC 利率決議{"（含經濟預測與點陣圖）" if sep else ""}',
                             'time_et': time(14, 0)})
    return meetings


# BEA 發布很多項目，只留會影響股市的全國性數據：GDP、個人所得與支出（含 PCE 物價）、國際貿易、企業利潤
BEA_KEEP = re.compile(r'\bGDP\b|Gross Domestic Product|Personal Income and Outlays|International Trade in Goods and Services'
                      r'|Corporate Profits', re.I)
BEA_SKIP = re.compile(r'\b(by State|States|State|County|Counties|Metropolitan|Puerto Rico|Territor\w*)\b', re.I)


def market_moving_bea(label: str) -> bool:
    return bool(BEA_KEEP.search(label)) and not BEA_SKIP.search(label)


def parse_bea(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    year, out = None, []
    for tr in soup.select('tr'):
        text = tr.get_text(' ', strip=True)
        y = re.match(r'Year (\d{4})', text)
        if y:
            year = int(y.group(1))
            continue
        d_td, t_td = tr.find('td', class_='scheduled-date'), tr.find('td', class_='release-title')
        if not (d_td and t_td and year):
            continue
        m = re.match(r'([A-Za-z]+) (\d{1,2})\s+(\d{1,2}:\d{2}) ([AP]M)', d_td.get_text(' ', strip=True))
        if not m or m.group(1)[:3] not in MONTHS:
            continue
        label = t_td.get_text(' ', strip=True)
        if not market_moving_bea(label):
            continue
        hh, mm = map(int, m.group(3).split(':'))
        hh = hh % 12 + (12 if m.group(4) == 'PM' else 0)
        out.append({'date': date(year, MONTHS[m.group(1)[:3]], int(m.group(2))), 'time_et': time(hh, mm),
                    'label': label})
    return out


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r.text


def to_taipei(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET).astimezone(TPE)


# ---------------- 台股事件 ----------------

def parse_exdividend(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            d = _roc(r['Date'])
        except (KeyError, ValueError):
            continue
        cash = r.get('CashDividend') or ''
        out.append({'date': d, 'code': r.get('Code', '').strip(), 'name': r.get('Name', '').strip(),
                    'kind': r.get('Exdividend', '').strip(), 'cash': float(cash) if cash.strip() else None})
    return out


def parse_announcements(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        subject = (r.get('主旨 ') or r.get('主旨') or '').replace('\r', '').replace('\n', '').strip()
        try:
            d = _roc(r['發言日期'])
        except (KeyError, ValueError):
            d = None
        out.append({'date': d, 'code': r.get('公司代號', '').strip(), 'name': r.get('公司名稱', '').strip(),
                    'subject': subject})
    return out


def fetch_twse_openapi(path: str) -> list[dict]:
    r = requests.get(f'https://openapi.twse.com.tw/v1{path}', headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()
