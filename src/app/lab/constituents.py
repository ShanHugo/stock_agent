"""臺灣50指數歷年成分股：從臺灣指數公司新聞稿解析每次「成分股納入／刪除」，由現在的名單往回推算各時點的名單。

來源：https://taiwanindex.com.tw/news/{id}（伺服器端產生的頁面，逐篇讀取，有快取）
"""
from __future__ import annotations
import re
import time
from datetime import date
from html import unescape
from pathlib import Path

import pandas as pd
import requests

CACHE = Path('data/raw/lab/tip_news')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}


def page_text(html: str) -> str:
    t = unescape(re.sub(r'<[^>]+>', '', html))
    return re.sub(r'\s+', ' ', t)


def parse_announcement(html: str) -> dict | None:
    """回傳 {'published', 'effective', 'added': [(code,name)], 'removed': [...]}；沒有臺灣50指數變動段落則回傳 None。

    支援的格式（2016–2026 實際出現過）：
      臺灣50指數 (一)成分股納入：台灣高鐵(2633)。 (二)成分股刪除：儒鴻(1476)。
      一、臺灣50指數 (一) 成分股納入：南亞科 (2408)。（名稱與代號中間可能有斷行空白）
      臺灣50指數： (一)成分股納入中壽（股票代號：2823）。 (二)成分股刪除華亞科（股票代號：3474）。（臨時調整）
    生效日：「亦即自2019年12月23日起生效」或民國年「自106年06月19日（星期一）起生效」。
    """
    t = page_text(html)
    pub = re.search(r'(\d{4}/\d{2}/\d{2}) \d{2}:\d{2}', t)
    # 段落標題後面接「(一)」（2016–2025）或直接接「成分股」（2026 起）
    m0 = re.search(r'臺灣50指數\s*[：:]?\s*(?=[\(（]一[\)）]|成分股)', t)
    if not m0:
        return None
    seg = t[m0.end():]
    stop = re.search(r'[二三四五]、|臺灣中型100指數', seg)
    seg = re.sub(r'\s+', '', seg[: stop.start()] if stop else seg[:600])

    def codes_after(label):
        m = re.search(label + r'[：:]?(.*?)(?=[\(（][一二三四五六][\)）]|成分股|候補名單|$)', seg)
        if not m or m.group(1).startswith('無'):
            return []
        part = m.group(1)
        found = re.findall(r'([^、，,：:()（）。]+?)[\(（](?:股票代號[：:])?(\d{4})[\)）]', part)
        return [(c, n) for n, c in found]

    flat = re.sub(r'\s+', '', t)  # 生效日文字中間可能有斷行空白
    eff = (re.search(r'亦即自(\d{3,4})年(\d{1,2})月(\d{1,2})日', flat)
           or re.search(r'自(\d{3,4})年(\d{1,2})月(\d{1,2})日（[^）]*）起生效', flat))
    effective = None
    if eff:
        y, mo, d = map(int, eff.groups())
        effective = date(y + 1911 if y < 1911 else y, mo, d).isoformat()
    return {'published': pub.group(1).replace('/', '-') if pub else None, 'effective': effective,
            'added': codes_after('成分股納入'), 'removed': codes_after('成分股刪除')}


def fetch_news(nid: int, *, delay: float = 2.0) -> str | None:
    path = CACHE / f'{nid}.html'
    if path.exists():
        return path.read_text(encoding='utf-8')
    time.sleep(delay)
    r = requests.get(f'https://taiwanindex.com.tw/news/{nid}', headers=UA, timeout=30)
    if r.status_code == 404:
        html = ''
    else:
        r.raise_for_status()
        r.encoding = 'utf-8'
        html = r.text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding='utf-8')
    return html or None


def scan_changes(max_id: int, progress=None) -> list[dict]:
    out = []
    for nid in range(1, max_id + 1):
        if progress and nid % 25 == 0:
            progress(f'TIP 新聞稿 {nid}/{max_id}')
        html = fetch_news(nid)
        if not html:
            continue
        a = parse_announcement(html)
        if a and (a['added'] or a['removed']):
            out.append({'id': nid, **a})
    return sorted(out, key=lambda a: a['effective'] or a['published'] or '')


def membership_history(current: set[str], changes: list[dict]) -> list[tuple[pd.Timestamp, set[str]]]:
    """由現在的名單往回推：每個生效日之前的名單 = 之後的名單 - 納入 + 刪除。回傳 [(起始日, 名單)]，由舊到新。"""
    periods, members = [], set(current)
    for c in sorted(changes, key=lambda a: a['effective'], reverse=True):
        eff = pd.Timestamp(c['effective'])
        periods.append((eff, set(members)))
        members = (members - {code for code, _ in c['added']}) | {code for code, _ in c['removed']}
    periods.append((pd.Timestamp('1900-01-01'), set(members)))
    return sorted(periods, key=lambda p: p[0])


def members_on(history: list[tuple[pd.Timestamp, set[str]]], d: pd.Timestamp) -> set[str]:
    current = history[0][1]
    for start, m in history:
        if start <= d:
            current = m
        else:
            break
    return current
