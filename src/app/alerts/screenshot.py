"""用券商 App 庫存截圖更新持股（口袋證券沒有程式 API）。

流程：本機 Ollama 看圖讀出每檔的代號、均價、股數、總成本、股價、市值 → 用截圖自己的數字交叉驗算
（均價 × 股數 ≈ 總成本、股價 × 股數 ≈ 市值；看錯任何一個數字就對不上）→ 與 portfolio.json 比對 →
使用者勾選確認後才寫入（寫入前自動備份）。截圖只在本機處理，不送到外部服務。

2026-10-24 以口袋證券台股庫存截圖實測：5 次辨識全部正確，每次約 5 秒。
"""
from __future__ import annotations
import json
import re

import requests

SCHEMA = {'type': 'object', 'required': ['holdings'], 'properties': {'holdings': {'type': 'array', 'items': {
    'type': 'object', 'required': ['code', 'price', 'avg_cost', 'shares', 'total_cost', 'market_value'],
    'properties': {'code': {'type': 'string'}, 'name': {'type': 'string'}, 'price': {'type': 'number'},
                   'avg_cost': {'type': 'number'}, 'shares': {'type': 'number'},
                   'total_cost': {'type': 'number'}, 'market_value': {'type': 'number'}}}}}}
PROMPT = ('這是券商 App 的股票庫存截圖，可能有很多檔。台股常見排版：每一檔兩列，第一列依序是 代號、股價、成交均價、市值、累積損益；'
          '第二列依序是 名稱、漲跌幅、持有股數、總成本、報酬率。美股代號是英文字母，股數可能有小數。'
          '把截圖中「每一檔」都列進 holdings：code、name（看得到的部分）、price（股價）、avg_cost（成交均價）、'
          'shares（持有股數）、total_cost（總成本）、market_value（市值）。數字去掉逗號，照抄，不要計算。')
TOLERANCE = 0.015          # 交叉驗算容許誤差（手續費、四捨五入）
TW_CODE = re.compile(r'^\d{4,6}[A-Z]?$')
US_CODE = re.compile(r'^[A-Z]{1,5}(-[A-Z])?$')


def read_image(image_b64: str, model: str, url: str, timeout: int = 180) -> list[dict]:
    r = requests.post(url.rstrip('/') + '/api/chat', timeout=timeout, json={
        'model': model, 'stream': False, 'think': False, 'format': SCHEMA, 'options': {'temperature': 0},
        'messages': [{'role': 'user', 'content': PROMPT, 'images': [image_b64]}]})
    r.raise_for_status()
    return json.loads(r.json()['message']['content']).get('holdings', [])


def _close(a: float, b: float) -> bool:
    return b > 0 and abs(a - b) / b <= TOLERANCE


def check(row: dict) -> list[str]:
    """回傳問題清單（空 = 通過）。"""
    code = str(row.get('code', '')).strip().upper()
    problems = []
    if not (TW_CODE.match(code) or US_CODE.match(code)):
        problems.append(f'代號「{code}」格式不對')
    try:
        price, avg, shares = float(row['price']), float(row['avg_cost']), float(row['shares'])
        total, mv = float(row['total_cost']), float(row['market_value'])
    except (KeyError, TypeError, ValueError):
        return problems + ['缺少數字欄位']
    if shares <= 0 or avg <= 0:
        problems.append('股數或均價不是正數')
    if TW_CODE.match(code) and shares != int(shares):
        problems.append('台股股數應為整數')
    if not _close(avg * shares, total):
        problems.append(f'均價 × 股數 = {avg * shares:,.0f}，與總成本 {total:,.0f} 對不上')
    if not _close(price * shares, mv):
        problems.append(f'股價 × 股數 = {price * shares:,.0f}，與市值 {mv:,.0f} 對不上')
    return problems


def base_code(symbol: str) -> str:
    return symbol.upper().split('.')[0]


def diff(holdings: list[dict], rows: list[dict]) -> list[dict]:
    """截圖 vs portfolio.json 的 holdings。kind：same / update / new / absent（截圖沒有，可能在另一張或已賣出）。"""
    by_code = {base_code(h['symbol']): h for h in holdings}
    out, seen = [], set()
    for r in rows:
        code = str(r.get('code', '')).strip().upper()
        seen.add(code)
        problems = check(r)
        h = by_code.get(code)
        item = {'code': code, 'name': r.get('name') or (h or {}).get('name', ''), 'shares': r.get('shares'),
                'cost': r.get('avg_cost'), 'problems': problems,
                'old_shares': h['shares'] if h else None, 'old_cost': h['cost'] if h else None,
                'symbol': h['symbol'] if h else None}
        if h is None:
            item['kind'] = 'new'
        elif float(h['shares']) == float(r['shares']) and abs(float(h['cost']) - float(r['avg_cost'])) < 0.005:
            item['kind'] = 'same'
        else:
            item['kind'] = 'update'
        out.append(item)
    same_market = {('US' if US_CODE.match(c) else 'TW') for c in seen}
    for code, h in by_code.items():
        market = 'US' if not code[:1].isdigit() else 'TW'
        if code not in seen and market in same_market:
            out.append({'code': code, 'name': h.get('name', ''), 'kind': 'absent', 'problems': [],
                        'old_shares': h['shares'], 'old_cost': h['cost'], 'symbol': h['symbol']})
    return out


def apply(raw: dict, accepted: list[dict], symbol_for_new) -> list[str]:
    """把勾選的 update / new 寫進 raw['holdings']（原地修改）；回傳變更說明。absent 不自動刪除。
    symbol_for_new(code) → 新持股的代號（上櫃要加 .TWO）。"""
    holdings = raw.setdefault('holdings', [])
    by_code = {base_code(h['symbol']): h for h in holdings}
    notes = []
    for a in accepted:
        if a.get('problems') or a.get('kind') not in ('update', 'new'):
            continue
        code, shares, cost = a['code'], float(a['shares']), round(float(a['cost']), 4)
        shares = int(shares) if shares == int(shares) else shares
        if a['kind'] == 'update' and code in by_code:
            h = by_code[code]
            notes.append(f"{code}：{h['shares']} 股 @ {h['cost']} → {shares} 股 @ {cost}")
            h['shares'], h['cost'] = shares, cost
        elif a['kind'] == 'new' and code not in by_code:
            h = {'symbol': symbol_for_new(code), 'name': a.get('name', ''), 'shares': shares, 'cost': cost}
            holdings.append(h)
            by_code[code] = h
            notes.append(f"新增 {h['symbol']}：{shares} 股 @ {cost}")
    return notes
