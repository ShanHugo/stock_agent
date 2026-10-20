"""選股範圍：ETF 目前的成分股（MoneyDJ 持股明細，名稱 → 代號用證交所上市公司清單對照）。

注意：用的是「現在」的成分股，回測期間被剔除的公司不在名單內（倖存者偏差，結果會偏樂觀）。
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

import requests

CACHE = Path('data/raw/lab')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}


def parse_moneydj_holdings(html: str) -> tuple[str | None, list[str]]:
    """回傳 (資料日期, 股票名稱清單)。"""
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '|', html))
    m = re.search(r'持股明細\|.*?資料日期：(\d{4}/\d{2}/\d{2})', text)
    date = m.group(1) if m else None
    start = text.find('持股明細')
    names = []
    # 每列格式：|名稱| |持股(千股)| |比例| ...；名稱後面緊接著帶千分位的持股數
    for name, _shares in re.findall(r'\|([^|\d][^|]{0,15}?)\| \|([\d,]+\.\d{2})\| \|\d+\.\d{2}\|', text[start:]):
        name = name.strip().rstrip('*').strip()
        if name and name not in ('股票名稱',) and name not in names:
            names.append(name)
    return date, names


def name_to_code_map(rows: list[dict]) -> dict[str, str]:
    """證交所 BWIBBU_ALL：Code/Name。只收 4 位數一般股票。"""
    # 名稱兩邊都去掉「*」再比對（例：國巨*）
    return {r['Name'].strip().rstrip('*').strip(): r['Code'].strip()
            for r in rows if re.fullmatch(r'\d{4}', r.get('Code', '').strip())}


def fetch_universe(etfs: list[str], *, refresh: bool = False) -> dict:
    path = CACHE / f"universe_{'_'.join(etfs)}.json"
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < 7 * 86400:
        return json.loads(path.read_text(encoding='utf-8'))
    rows = requests.get('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL', headers=UA, timeout=60).json()
    codes = name_to_code_map(rows)
    out = {'etfs': {}, 'codes': {}, 'unmatched': []}
    for etf in etfs:
        r = requests.get('https://www.moneydj.com/ETF/X/Basic/Basic0007a.xdjhtm', params={'etfid': f'{etf}.TW'},
                         headers=UA, timeout=60)
        r.encoding = 'utf-8'
        date, names = parse_moneydj_holdings(r.text)
        matched = []
        for n in names:
            code = codes.get(n)
            if code:
                matched.append(code)
                out['codes'][code] = n
            elif n not in out['unmatched']:
                out['unmatched'].append(n)
        out['etfs'][etf] = {'date': date, 'count': len(names), 'codes': matched}
        time.sleep(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    return out
