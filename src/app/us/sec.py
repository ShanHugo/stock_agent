"""美國證管會（SEC EDGAR）季營收，含實際申報日期（避免偷看未來）。

  代號 → CIK   https://www.sec.gov/files/company_tickers.json（只有目前仍掛牌的代號）＋ Wikipedia S&P 500 名單的 CIK
  營收         https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json（每家約 4MB，只保留抽出的季營收）

營收的 XBRL 名稱因年代、產業不同（2018 年起多為 RevenueFromContractWithCustomerExcludingAssessedTax），
依序嘗試多個名稱；同一季多個名稱都有時取最早申報的。第 4 季常只申報全年數字，以「全年 − 前三季」推算，
可用日取年報申報日。SEC 要求每秒不超過 10 次請求。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import pandas as pd
import requests

CACHE = Path('data/raw/us/sec')
UA = {'User-Agent': 'StockRobot research script'}
CONCEPTS = ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet',
            'RevenueFromContractWithCustomerIncludingAssessedTax', 'RevenuesNetOfInterestExpense',
            'SalesRevenueGoodsNet', 'TotalRevenuesAndOtherIncome']
_last = [0.0]


def _get(url: str) -> requests.Response:
    wait = 0.15 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    return requests.get(url, headers=UA, timeout=60)


def ticker_ciks() -> dict[str, int]:
    path = CACHE / 'company_tickers.json'
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        r = _get('https://www.sec.gov/files/company_tickers.json')
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text, encoding='utf-8')
    js = json.loads(path.read_text(encoding='utf-8'))
    return {v['ticker'].replace('.', '-').upper(): int(v['cik_str']) for v in js.values()}


def extract_quarterly(facts: dict) -> pd.DataFrame:
    """companyfacts → 每季一列：end（季末）、val（單季營收）、filed（可用日）。"""
    g = (facts.get('facts') or {}).get('us-gaap') or {}
    rows = []
    for c in CONCEPTS:
        for x in ((g.get(c) or {}).get('units') or {}).get('USD', []):
            if not x.get('start') or not x.get('end') or not x.get('filed'):
                continue
            days = (pd.Timestamp(x['end']) - pd.Timestamp(x['start'])).days
            kind = 'Q' if 80 <= days <= 100 else ('FY' if 350 <= days <= 380 else None)
            if kind:
                rows.append({'concept': c, 'kind': kind, 'start': x['start'], 'end': x['end'],
                             'val': float(x['val']), 'filed': x['filed']})
    if not rows:
        return pd.DataFrame(columns=['end', 'val', 'filed'])
    df = pd.DataFrame(rows)
    for col in ('start', 'end', 'filed'):
        df[col] = pd.to_datetime(df[col])
    # 同一期間：取最早申報的那筆（之後的修正、比較期重述都較晚，不該用來回測）
    df = df.sort_values('filed').drop_duplicates(['kind', 'start', 'end'], keep='first')
    q = df[df['kind'] == 'Q'][['start', 'end', 'val', 'filed']]
    # 第 4 季：全年 − 同一會計年度內的三個單季
    for fy in df[df['kind'] == 'FY'].itertuples():
        if ((q['end'] - fy.end).abs() <= pd.Timedelta(days=7)).any():
            continue
        inside = q[(q['start'] >= fy.start - pd.Timedelta(days=7)) & (q['end'] < fy.end - pd.Timedelta(days=30))]
        if len(inside) == 3:
            q = pd.concat([q, pd.DataFrame([{'start': inside['end'].max() + pd.Timedelta(days=1), 'end': fy.end,
                                              'val': fy.val - inside['val'].sum(), 'filed': fy.filed}])])
    q = q.sort_values('end').drop_duplicates('end', keep='first')
    return q[['end', 'val', 'filed']].reset_index(drop=True)


def quarterly_revenue(cik: int) -> pd.DataFrame | None:
    """單季營收（有快取，7 天內重用）；查不到回傳 None。"""
    path = CACHE / 'revenue' / f'{cik:010d}.json'
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        df = pd.read_json(path, orient='records', convert_dates=['end', 'filed'])
        return df if not df.empty else None
    r = _get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json')
    if r.status_code == 404:
        df = pd.DataFrame(columns=['end', 'val', 'filed'])
    else:
        r.raise_for_status()
        df = extract_quarterly(r.json())
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(path, orient='records', date_format='iso')
    return df if not df.empty else None
