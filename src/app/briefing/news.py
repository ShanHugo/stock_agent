"""新聞標題整理（2026-10 實測可用的 RSS）。只收標題、來源、時間與連結，不轉載內文，也不評利多利空。

  美股/總經   Yahoo Finance headline RSS（依代號）、聯準會新聞稿 RSS（只取貨幣政策類）
  台股        中央社財經 RSS
  個別持股    Yahoo 奇摩股市個股 RSS（只留標題含代號或名稱的）
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from ..alerts.portfolio import Portfolio

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}
# 新聞時間窗：前一個交易日 14:30 之後（見 news_since），涵蓋台股收盤後、整個美股交易時段，以及週末與連假
PER_SECTION = 5
PER_STOCK = 3
ETF_ISSUERS = ('元大', '國泰', '富邦', '群益', '中信', '復華', '永豐', '凱基', '統一', '台新', '第一金', '兆豐', '野村')

FEEDS = {
    'yahoo_us': 'https://feeds.finance.yahoo.com/rss/2.0/headline',
    'fed': 'https://www.federalreserve.gov/feeds/press_all.xml',
    'cna': 'https://feeds.feedburner.com/rsscna/finance',
    'yahoo_tw': 'https://tw.stock.yahoo.com/rss',
}


def parse_feed(xml: bytes, source: str) -> list[dict]:
    items = []
    for it in ET.fromstring(xml).findall('.//item'):
        title = re.sub(r'\s+', ' ', it.findtext('title') or '').strip()
        link = (it.findtext('link') or '').strip()
        try:
            published = parsedate_to_datetime(it.findtext('pubDate') or '')
        except (TypeError, ValueError):
            published = None
        if title and link:
            items.append({'title': title, 'link': link, 'published': published, 'source': source})
    return items


def fetch_feed(url: str, params: dict | None = None, source: str = '') -> list[dict]:
    r = requests.get(url, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return parse_feed(r.content, source)


def aliases(code: str, name: str) -> list[str]:
    """標題比對用的關鍵字：代號、全名、去掉 ETF 發行商前綴的名稱（至少 2 個字）。"""
    out = [code]
    if name:
        out.append(name)
        for p in ETF_ISSUERS:
            if name.startswith(p) and len(name) - len(p) >= 2:
                out.append(name[len(p):])
    return out


QUOTES_CACHE = Path('data/raw/lab/market/twse/quotes')
SESSION_CLOSE = time(14, 30)
MAX_LOOKBACK_DAYS = 16      # 春節連假加前後週末可能超過 10 天


def _is_holiday(d: date) -> bool:
    """週末，或行情快取顯示當天休市（有快取且沒有資料）。沒有快取時視為交易日。"""
    if d.weekday() >= 5:
        return True
    path = QUOTES_CACHE / f'{d:%Y%m%d}.json'
    try:
        return path.exists() and not json.loads(path.read_text(encoding='utf-8')).get('data')
    except (OSError, ValueError):
        return False


def news_since(now: datetime, is_holiday=_is_holiday) -> datetime:
    """前一個交易日 14:30（台股收盤後）。週一往回到週五、連假後往回到假期前最後一個交易日，
    這樣週末與連假期間的新聞（含週五晚上的美股）都會收進來。"""
    d = now.date() - timedelta(days=1)
    for _ in range(MAX_LOOKBACK_DAYS):
        if not is_holiday(d):
            break
        d -= timedelta(days=1)
    return datetime.combine(d, SESSION_CLOSE, tzinfo=now.tzinfo)


def _recent(items: list[dict], since: datetime) -> list[dict]:
    return [i for i in items if i['published'] and i['published'] >= since]


def _dedupe(items: list[dict], seen: set[str]) -> list[dict]:
    out = []
    for i in sorted(items, key=lambda x: x['published'], reverse=True):
        key = re.sub(r'\W', '', i['title'])[:40]
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def collect_news(portfolio: Portfolio, now: datetime, errors: list[str], fetch=fetch_feed) -> dict:
    since = news_since(now)
    seen: set[str] = set()

    def get(name, *args, **kw):
        try:
            return fetch(*args, **kw)
        except Exception as e:
            errors.append(f'新聞 {name}: {e}')
            return []

    # 個別持股先處理，避免同一則新聞在大盤區重複出現
    stocks = []
    names = {h.symbol: h.name for h in portfolio.holdings}
    tw_codes = list(dict.fromkeys([h.symbol for h in portfolio.holdings if not h.is_us] + portfolio.watchlist))
    for code in tw_codes:
        sym = code if '.' in code else f'{code}.TW'
        keys = aliases(code, names.get(code, ''))
        items = _recent(get(f'Yahoo 奇摩 {code}', FEEDS['yahoo_tw'], {'s': sym}, source='Yahoo奇摩股市'), since)
        hits = [i for i in items if any(k in i['title'] for k in keys)]
        picked = _dedupe(hits, seen)[:PER_STOCK]
        if picked:
            stocks.append({'code': code, 'name': names.get(code, ''), 'items': picked})

    us_syms = ['^GSPC', '^IXIC'] + [h.symbol for h in portfolio.holdings if h.is_us]
    us = _recent(get('Yahoo Finance', FEEDS['yahoo_us'], {'s': ','.join(us_syms), 'region': 'US', 'lang': 'en-US'},
                     source='Yahoo Finance'), since)
    fed = [i for i in _recent(get('Fed', FEEDS['fed'], source='Federal Reserve'), since - timedelta(days=2))
           if '/monetary' in i['link']]  # 只取貨幣政策新聞稿；放寬到近 3 天
    tw = _recent(get('中央社', FEEDS['cna'], source='中央社'), since)
    return {
        'since': since,
        'fed': _dedupe(fed, seen),
        'us': _dedupe(us, seen)[:PER_SECTION],
        'tw': _dedupe(tw, seen)[:PER_SECTION],
        'stocks': stocks,
    }


def _line(i: dict, tz) -> str:
    t = i['published'].astimezone(tz).strftime('%m/%d %H:%M') if i['published'] else ''
    # <連結> 讓 Discord 不展開預覽，避免一則簡報出現一堆預覽卡片
    return f"- [{i['title']}](<{i['link']}>)｜{i['source']} {t}"


def format_news(n: dict, tz) -> list[str]:
    lines = ['', f"**新聞標題（{n['since'].astimezone(tz):%m/%d %H:%M} 之後）**"]
    if n['fed']:
        lines.append('聯準會：')
        lines += [_line(i, tz) for i in n['fed']]
    if n['us']:
        lines.append('美股：')
        lines += [_line(i, tz) for i in n['us']]
    if n['tw']:
        lines.append('台股：')
        lines += [_line(i, tz) for i in n['tw']]
    for s in n['stocks']:
        lines.append(f"{s['code']} {s['name']}：".replace(' ：', '：'))
        lines += [_line(i, tz) for i in s['items']]
    if len(lines) == 2:
        lines.append('- 這段時間沒有抓到相關新聞')
    lines.append('_僅列標題與連結，內容請點連結閱讀；新聞不等於買賣訊號。_')
    return lines
