"""TWSE (台灣證交所) 日線資料抓取與快取

使用公開 JSON API:
  https://www.twse.com.tw/rwd/zh/stock/day?date=YYYYMMDD&stockNo=2330&response=json

特性:
  - 以月份為單位抓取（API 依指定日期回傳該月份所有日資料）
  - backoff 重試網路與暫時性錯誤
  - parquet 快取: data/raw/twse/{symbol}.parquet

注意: 公開 API 有頻率限制，請避免高併發；此實作僅供研究用途。
"""
from __future__ import annotations
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
import backoff
from typing import Optional

BASE_URL_NEW = "https://www.twse.com.tw/rwd/zh/stock/day"
BASE_URL_LEGACY = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
CACHE_DIR = Path("data/raw/twse")


def _clean_num(val: str) -> Optional[float]:
    if val is None:
        return None
    v = str(val).strip().replace(',', '')
    if v in ('', '0', '--', 'null', 'None'):
        return None
    try:
        return float(v)
    except ValueError:
        return None


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/javascript,*/*;q=0.01",
    "Referer": "https://www.twse.com.tw/zh/trading/historical/daily-stock-prices.html",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"
}


def _request_json(params):
    # 先嘗試新版 rwd，失敗再試 legacy
    for base in (BASE_URL_NEW, BASE_URL_LEGACY):
        try:
            r = requests.get(base, params=params, headers=HEADERS, timeout=10)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            continue
    raise requests.RequestException("All TWSE endpoints failed (rwd + legacy)")


@backoff.on_exception(backoff.expo, (requests.RequestException,), max_tries=3, jitter=None)
def fetch_twse_month(symbol: str, year: int, month: int) -> pd.DataFrame:
    """抓取單一股票某年某月資料。回傳 index=date 的 DataFrame(columns=open,high,low,close,volume)。"""
    date_param = f"{year}{month:02d}01"  # 該月第一天
    params = {"date": date_param, "stockNo": symbol, "response": "json"}
    js = _request_json(params)
    if js.get('stat') and 'OK' not in js['stat']:
        raise ValueError(f"TWSE response not OK: {js.get('stat')}")
    data = js.get('data', [])
    if not data:
        return pd.DataFrame(columns=['open','high','low','close','volume'])
    rows = []
    for row in data:
        # 典型 row: ['2024/09/02','59,999,999','xxx','開','高','低','收','漲跌','筆數']
        if len(row) < 7:
            continue
        date_str = row[0].replace('/', '-')
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        open_p = _clean_num(row[3])
        high_p = _clean_num(row[4])
        low_p = _clean_num(row[5])
        close_p = _clean_num(row[6])
        volume = _clean_num(row[1])  # 股數
        if any(v is None for v in [open_p, high_p, low_p, close_p]):
            # 跳過無效交易日
            continue
        rows.append({
            'date': pd.to_datetime(dt),
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': volume or 0.0
        })
    if not rows:
        return pd.DataFrame(columns=['open','high','low','close','volume'])
    df = pd.DataFrame(rows).set_index('date').sort_index()
    return df


def _month_range(start: str, end: str):
    s = datetime.strptime(start, "%Y-%m-%d").date().replace(day=1)
    e_dt = datetime.strptime(end, "%Y-%m-%d").date().replace(day=1)
    cur = s
    while cur <= e_dt:
        yield cur.year, cur.month
        # 下個月
        if cur.month == 12:
            cur = cur.replace(year=cur.year+1, month=1)
        else:
            cur = cur.replace(month=cur.month+1)


def fetch_twse_range(symbol: str, start: str, end: str) -> pd.DataFrame:
    frames = []
    for y, m in _month_range(start, end):
        frames.append(fetch_twse_month(symbol, y, m))
    if not frames:
        return pd.DataFrame(columns=['open','high','low','close','volume'])
    df = pd.concat(frames).sort_index()
    mask = (df.index >= start) & (df.index <= end)
    return df.loc[mask]


def fetch_twse_range_cached(symbol: str, start: str, end: str, refresh: bool = False, fallback_yf: bool = True) -> pd.DataFrame:
    """帶 parquet 快取的範圍抓取。會增量更新檔案並回傳指定期間資料。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}.parquet"
    if cache_file.exists() and not refresh:
        cache_df = pd.read_parquet(cache_file)
    else:
        cache_df = pd.DataFrame(columns=['open','high','low','close','volume'])

    # 判斷缺失月份範圍
    existing_dates = pd.to_datetime(cache_df.index) if not cache_df.empty else pd.DatetimeIndex([])
    need_full_fetch = cache_df.empty or refresh
    try:
        if need_full_fetch:
            updated_df = fetch_twse_range(symbol, start, end)
        else:
            updated_df = fetch_twse_range(symbol, start, end)
    except Exception as e:
        if fallback_yf:
            # 轉為 yfinance 後綴 .TW
            import yfinance as yf
            alt_symbol = symbol if symbol.endswith('.TW') else f"{symbol}.TW"
            data = yf.download(alt_symbol, start=start, end=end, progress=False, auto_adjust=False)
            if data.empty:
                raise RuntimeError(f"TWSE & yfinance both failed for {symbol}: {e}") from e
            # 若是 MultiIndex，取第二層欄位對應 symbol
            if isinstance(data.columns, pd.MultiIndex):
                data = data.xs(alt_symbol, axis=1, level=1)
            data = data.rename(columns={c: c.lower() for c in data.columns})
            keep = [col for col in ["open","high","low","close","volume"] if col in data.columns]
            data = data[keep]
            data.index.name = 'date'
            updated_df = data
        else:
            raise
    if not updated_df.empty:
        merged = pd.concat([cache_df, updated_df])
    else:
        merged = cache_df
    # 去除重複日期，保留最新（後抓的覆蓋）
    if not merged.empty:
        merged = merged[~merged.index.duplicated(keep='last')]
    merged = merged.sort_index()
    merged.to_parquet(cache_file)
    mask = (merged.index >= start) & (merged.index <= end)
    return merged.loc[mask]
