"""資料抓取模組：提供最小 yfinance 介面 + 後續可擴充 TWSE API。
輸出皆為 pandas.DataFrame，index=DatetimeIndex (UTC naive / localizable)
"""
from __future__ import annotations
import re
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


def fetch_ohlcv_yf(symbol: str, start: str, end: str) -> pd.DataFrame:
    """以 yfinance 抓取日線 OHLCV。
    Parameters
    ----------
    symbol: e.g. '2330.TW'
    start, end: 'YYYY-MM-DD'
    Returns: DataFrame columns=[open, high, low, close, volume]
    """
    # yfinance end 參數為「非包含」；為確保包含 end 當日，向後加 1 天
    end_inclusive = (pd.to_datetime(end) + timedelta(days=1)).strftime('%Y-%m-%d')
    data = yf.download(symbol, start=start, end=end_inclusive, progress=False, auto_adjust=False)
    if data.empty:
        raise ValueError(f"No data fetched for {symbol} {start}~{end}")
    if isinstance(data.columns, pd.MultiIndex):
        # 單一 symbol 時取第二層欄位（symbol 名）
        sym = data.columns.levels[1][0]
        data = data.xs(sym, axis=1, level=1)
    data = data.rename(columns={c: c.lower() for c in data.columns})
    # 移除不必要欄位（如 adj close）
    keep = [c for c in ['open','high','low','close','volume'] if c in data.columns]
    data = data[keep]
    data.index.name = 'date'
    return data


PATCH_DAYS = 6       # 往回檢查幾個平日
YAHOO_CHART = 'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'


def yahoo_official_close(symbol: str, get=None) -> tuple[pd.Timestamp, float] | None:
    """Yahoo chart API 的 regularMarketPrice：只有在報價時間 ≥ 當天正規盤收盤時間時才當作收盤價。
    回傳 (交易日, 收盤價)，盤中或拿不到時回傳 None。"""
    import requests
    from zoneinfo import ZoneInfo
    get = get or requests.get
    try:
        js = get(YAHOO_CHART.format(symbol=symbol), params={'range': '5d', 'interval': '1d'},
                 headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).json()
        meta = js['chart']['result'][0]['meta']
        price, at = meta.get('regularMarketPrice'), meta.get('regularMarketTime')
        end = meta['currentTradingPeriod']['regular']['end']
        tz = ZoneInfo(meta.get('exchangeTimezoneName') or 'America/New_York')
    except Exception:       # noqa: BLE001
        return None
    if not price or not at:
        return None
    t_at, t_end = datetime.fromtimestamp(at, tz), datetime.fromtimestamp(end, tz)
    # 報價時刻要在該交易日正規盤收盤時刻（例如美東 16:00）之後，才是收盤價。
    # 不比對 currentTradingPeriod 的日期：過了交易所當地午夜，它會換成新的一天，但最新價仍是前一天的收盤。
    close_minutes = t_end.hour * 60 + t_end.minute
    if t_at.hour * 60 + t_at.minute < close_minutes - 1:
        return None
    return pd.Timestamp(t_at.date()), float(price)


def fill_missing_close(df: pd.DataFrame, symbol: str, get=None, today=None) -> pd.DataFrame:
    """用 chart API 收盤後的價格補 Yahoo 日線：最後一根收盤是空值時補上；
    最後一根停在前幾天、最近一個交易日整根不見時（2026-10-23 早上 TSM 停在 10/21），補一根只有收盤價的 K 棒。"""
    if df.empty:
        return df
    last = pd.Timestamp(df.index[-1]).normalize()
    today = pd.Timestamp(today or datetime.now().date())
    if pd.notna(df['close'].iloc[-1]) and last >= today - pd.offsets.BDay(1):
        return df                                    # 最後一天有收盤，且不可能還有更新的收盤
    got = yahoo_official_close(symbol, get=get)
    if not got:
        return df
    day, price = got
    if day == last and pd.isna(df['close'].iloc[-1]):
        df = df.copy()
        df.iloc[-1, df.columns.get_loc('close')] = price
    elif day > last:
        df = df.astype(float)
        df.loc[day] = {c: (price if c != 'volume' else float('nan')) for c in df.columns}
    return df


NASDAQ_HIST = 'https://api.nasdaq.com/api/quote/{symbol}/historical'
US_TICKER = re.compile(r'^[A-Z]{1,5}$')


def _num(s) -> float:
    return float(str(s).replace('$', '').replace(',', ''))


def nasdaq_daily(symbol: str, start, end, get=None) -> pd.DataFrame | None:
    """Nasdaq 官方歷史日線（只列已收盤的交易日）。ETF 與個股的 assetclass 不同，兩個都試。抓不到回傳 None。"""
    import requests
    get = get or requests.get
    for asset in ('etf', 'stocks'):
        try:
            js = get(NASDAQ_HIST.format(symbol=symbol), timeout=20, headers={'User-Agent': 'Mozilla/5.0'},
                     params={'assetclass': asset, 'fromdate': str(start), 'todate': str(end), 'limit': 30}).json()
            rows = ((js.get('data') or {}).get('tradesTable') or {}).get('rows') or []
        except Exception:       # noqa: BLE001
            continue
        if rows:
            df = pd.DataFrame({
                'open': [_num(r['open']) for r in rows], 'high': [_num(r['high']) for r in rows],
                'low': [_num(r['low']) for r in rows], 'close': [_num(r['close']) for r in rows],
                'volume': [_num(r['volume']) for r in rows],
            }, index=pd.DatetimeIndex(pd.to_datetime([r['date'] for r in rows], format='%m/%d/%Y'), name='date'))
            return df.sort_index()
    return None


def patch_us_daily(df: pd.DataFrame, symbol: str, end: str, today=None, get=None) -> pd.DataFrame:
    """美股最近幾個交易日用 Nasdaq 官方收盤補上或更正。

    Yahoo 偶爾整根日線消失（例如 2026-10-22 查詢時 VOO 的 10/21 不見了），或收盤變成空值。"""
    today = today or datetime.now().date()
    last = min(pd.to_datetime(end).date(), today)
    first = (pd.Timestamp(last) - pd.Timedelta(days=PATCH_DAYS + 4)).date()
    official = nasdaq_daily(symbol, first, last, get=get)
    if official is None:
        return fill_missing_close(df, symbol, get=get, today=today)   # Nasdaq 抓不到時退回 Yahoo chart API
    df = df.astype(float)
    df.index = pd.to_datetime(df.index)
    for ts, row in official.iterrows():
        if ts not in df.index:
            df.loc[ts] = row
        else:
            df.loc[ts, 'close'] = row['close']
            for col in ('open', 'high', 'low', 'volume'):
                if pd.isna(df.at[ts, col]):
                    df.loc[ts, col] = row[col]
    # 美股收盤後幾小時內 Nasdaq 可能還沒有當天日線，再用 Yahoo 收盤報價補最後一天
    return fill_missing_close(df.sort_index(), symbol, get=get, today=today)


def fetch_ohlcv_tw(symbol: str, start: str, end: str, *, quotes=None, today=None) -> pd.DataFrame:
    """yfinance 日線。台股（.TW／.TWO）最近幾個交易日改用證交所／櫃買中心的官方收盤行情補上或更正；
    美股代號（例如 VOO）最近幾個交易日用 Nasdaq 官方收盤補上或更正；
    其他（匯率、指數等）最後一天收盤是空值時，用 Yahoo chart API 收盤後的價格補上。

    Yahoo 的台股日線偶爾會延遲或把當天收盤變成空值（例如 2026-10-21 晚上 0050 的 10/21 收盤消失），
    官方行情則在收盤後就公布。非台股代號直接回傳 yfinance 的結果。
    quotes(market, date) -> DataFrame[code, open, close, volume]；預設用 lab.market.quotes_day（有快取）。
    """
    tw = symbol.upper().endswith(('.TW', '.TWO'))
    us = bool(US_TICKER.match(symbol))
    try:
        df = fetch_ohlcv_yf(symbol, start, end)
    except ValueError:
        if not (tw or us):
            raise
        df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'], dtype=float)
        df.index = pd.DatetimeIndex([], name='date')
    if us:
        return patch_us_daily(df, symbol, end, today=today)
    if not tw:
        return fill_missing_close(df, symbol)        # 匯率、指數等：最後一天收盤空值時用 Yahoo chart API 補
    if quotes is None:
        from ..lab.market import quotes_day as quotes
    code, suffix = symbol.upper().rsplit('.', 1)
    markets = ['twse', 'tpex'] if suffix == 'TW' else ['tpex', 'twse']   # 轉上市櫃時另一邊也找
    today = today or datetime.now().date()
    last = min(pd.to_datetime(end).date(), today)
    days = [d.date() for d in pd.bdate_range(end=last, periods=PATCH_DAYS)]
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    for d in days:
        row = None
        for m in markets:
            try:
                q = quotes(m, d)
            except Exception:       # noqa: BLE001 — 官方行情抓不到時保留 Yahoo 的資料
                continue
            if q is not None and not q.empty and code in set(q['code']):
                row = q[q['code'] == code].iloc[0]
                break
        if row is None or pd.isna(row.get('close')):
            continue
        ts = pd.Timestamp(d)
        close = float(row['close'])
        opened = float(row['open']) if 'open' in row and pd.notna(row.get('open')) else close
        if ts not in df.index:
            df.loc[ts] = {'open': opened, 'high': max(opened, close), 'low': min(opened, close), 'close': close,
                          'volume': float(row.get('volume') or 0)}
        else:
            df.loc[ts, 'close'] = close
            for col, v in (('open', opened), ('high', max(opened, close)), ('low', min(opened, close))):
                if pd.isna(df.at[ts, col]):
                    df.loc[ts, col] = v
    return df.sort_index()
