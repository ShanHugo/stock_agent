"""資料抓取模組：提供最小 yfinance 介面 + 後續可擴充 TWSE API。
輸出皆為 pandas.DataFrame，index=DatetimeIndex (UTC naive / localizable)
"""
from __future__ import annotations
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
    # 報價時間要在「同一天」正規盤收盤之後，才是收盤價
    if t_at.date() != t_end.date() or at < end - 60:
        return None
    return pd.Timestamp(t_at.date()), float(price)


def fill_missing_close(df: pd.DataFrame, symbol: str, get=None) -> pd.DataFrame:
    """最後一根日線收盤是空值（Yahoo 偶爾如此）時，用 chart API 的收盤價補上。"""
    if df.empty or pd.notna(df['close'].iloc[-1]):
        return df
    got = yahoo_official_close(symbol, get=get)
    if not got:
        return df
    day, price = got
    if pd.Timestamp(df.index[-1]).normalize() == day:
        df = df.copy()
        df.iloc[-1, df.columns.get_loc('close')] = price
    return df


def fetch_ohlcv_tw(symbol: str, start: str, end: str, *, quotes=None, today=None) -> pd.DataFrame:
    """yfinance 日線。台股（.TW／.TWO）最近幾個交易日改用證交所／櫃買中心的官方收盤行情補上或更正；
    其他（美股、匯率等）最後一天收盤是空值時，用 Yahoo chart API 收盤後的價格補上。

    Yahoo 的台股日線偶爾會延遲或把當天收盤變成空值（例如 2026-10-21 晚上 0050 的 10/21 收盤消失），
    官方行情則在收盤後就公布。非台股代號直接回傳 yfinance 的結果。
    quotes(market, date) -> DataFrame[code, open, close, volume]；預設用 lab.market.quotes_day（有快取）。
    """
    tw = symbol.upper().endswith(('.TW', '.TWO'))
    try:
        df = fetch_ohlcv_yf(symbol, start, end)
    except ValueError:
        if not tw:
            raise
        df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'], dtype=float)
        df.index = pd.DatetimeIndex([], name='date')
    if not tw:
        return fill_missing_close(df, symbol)        # 美股等：最後一天收盤空值時用 Yahoo chart API 補
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
