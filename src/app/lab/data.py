"""策略回測用的歷史資料（含公布時間延遲，避免偷看未來）。

  價格      Yahoo Finance 還原權值收盤價（含配息），基準 0050 同樣用還原價
  月營收    公開資訊觀測站（沿用 research.sources，有快取），第 M 月營收在 M+1 月 11 日起才可用
  估值      證交所 BWIBBU_d（每月最後交易日、全部上市股票的本益比/殖利率/淨值比）
  單季 EPS  公開資訊觀測站綜合損益彙總表（累計數相減），依法定期限設定可用日：
            Q1→5/16、Q2→8/15、Q3→11/15、Q4（年報）→次年 4/1
"""
from __future__ import annotations
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..research import sources as rs

CACHE = Path('data/raw/lab')
BENCHMARK = '0050'


@dataclass
class Dataset:
    prices: pd.DataFrame                  # index=交易日, columns=代號（含 0050），還原收盤價
    revenue_yoy: pd.DataFrame             # index=營收月份 Period('M'), columns=代號，年增率(%)
    pe: pd.DataFrame                      # index=月底交易日, columns=代號，本益比（虧損為 NaN）
    eps: pd.DataFrame                     # index=Period('Q'), columns=代號，單季 EPS
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def revenue_available(month: pd.Period) -> pd.Timestamp:
        return (month + 1).to_timestamp() + pd.Timedelta(days=10)  # 次月 11 日

    @staticmethod
    def eps_available(q: pd.Period) -> pd.Timestamp:
        y, n = q.year, q.quarter
        return pd.Timestamp({1: date(y, 5, 16), 2: date(y, 8, 15), 3: date(y, 11, 15), 4: date(y + 1, 4, 1)}[n])

    def revenue_asof(self, d: pd.Timestamp, months: int) -> pd.DataFrame:
        """截至 d 已公布的最近 months 個月營收年增率。"""
        avail = [m for m in self.revenue_yoy.index if self.revenue_available(m) <= d]
        return self.revenue_yoy.loc[avail[-months:]] if avail else self.revenue_yoy.iloc[0:0]

    def eps_asof(self, d: pd.Timestamp) -> pd.DataFrame:
        avail = [q for q in self.eps.index if self.eps_available(q) <= d]
        return self.eps.loc[avail] if avail else self.eps.iloc[0:0]


def _cache_frame(name: str, loader, refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f'{name}.json'
    if path.exists() and not refresh:
        # convert_axes=False：否則欄名 "0050" 會被轉成數字 50
        df = pd.read_json(io.StringIO(path.read_text(encoding='utf-8')), orient='split', convert_axes=False)
        df.index = pd.to_datetime(df.index)
        return df
    df = loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(df.to_json(orient='split', date_format='iso'), encoding='utf-8')
    return df


def load_prices(codes: list[str], start: str, end: str, refresh: bool = False, field: str = 'Close') -> pd.DataFrame:
    """field='Close' 還原收盤價；field='Volume' 成交量（股）。"""
    import yfinance as yf

    def load():
        tickers = [f'{c}.TW' for c in codes + [BENCHMARK]]
        raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False, group_by='column')
        close = raw[field] if isinstance(raw.columns, pd.MultiIndex) else raw[[field]]
        close.columns = [str(c).replace('.TW', '') for c in close.columns]
        return close.sort_index()

    import hashlib
    tag = hashlib.md5(','.join(sorted(codes)).encode()).hexdigest()[:8]  # 股票清單不同就用不同快取
    name = f'prices_{start}_{end}_{tag}' if field == 'Close' else f'{field.lower()}_{start}_{end}_{tag}'
    df = _cache_frame(name, load, refresh)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.columns = [str(c) for c in df.columns]
    return df


def load_revenue(codes: list[str], start: pd.Period, end: pd.Period, today: date, progress=None) -> pd.DataFrame:
    rows = {}
    m = start
    while m <= end:
        if progress:
            progress(f'月營收 {m}')
        try:
            df = rs.monthly_revenue(m.year, m.month, today=today)
            sub = df[df['code'].isin(codes)].set_index('code')['yoy_pct']
            rows[m] = sub
        except rs.NoData:
            pass
        m += 1
    out = pd.DataFrame(rows).T.reindex(columns=codes)
    out.index = pd.PeriodIndex(out.index, freq='M')
    return out.astype(float)


def valuation_day(d: date) -> pd.DataFrame:
    path = rs.CACHE / 'valuation_all' / f'{d:%Y%m%d}.json'

    def load():
        js = rs._get('https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d', host='twse',
                     params={'date': f'{d:%Y%m%d}', 'selectType': 'ALL', 'response': 'json'}).json()
        if js.get('stat') != 'OK':
            return pd.DataFrame(columns=['code', 'pe', 'dividend_yield', 'pb'])
        f = js['fields']
        i = {k: f.index(n) for k, n in [('code', '證券代號'), ('pe', '本益比'), ('dividend_yield', '殖利率(%)'),
                                        ('pb', '股價淨值比')]}
        return pd.DataFrame([{'code': r[i['code']].strip(), 'pe': rs._num(r[i['pe']]),
                              'dividend_yield': rs._num(r[i['dividend_yield']]), 'pb': rs._num(r[i['pb']])}
                             for r in js['data']])

    return rs._cached(path, True, load)


def load_pe(codes: list[str], trading_days: pd.DatetimeIndex, progress=None) -> pd.DataFrame:
    month_ends = pd.Series(trading_days, index=trading_days).groupby(trading_days.to_period('M')).max()
    rows = {}
    for d in month_ends:
        if progress:
            progress(f'估值 {d:%Y-%m-%d}')
        df = valuation_day(d.date())
        if not df.empty:
            rows[d] = df[df['code'].isin(codes)].set_index('code')['pe']
    out = pd.DataFrame(rows).T.reindex(columns=codes)
    return out.astype(float)


def load_eps(codes: list[str], first_year: int, today: date, progress=None) -> pd.DataFrame:
    cum = {}
    for y in range(first_year, today.year + 1):
        for q in range(1, 5):
            p = pd.Period(year=y, quarter=q, freq='Q')
            if Dataset.eps_available(p) > pd.Timestamp(today):
                continue
            if progress:
                progress(f'財報 {p}')
            try:
                df = rs.quarterly_income(y, q, today=today)
                cum[p] = df[df['code'].isin(codes)].set_index('code')['eps']
            except rs.NoData:
                pass
    cum_df = pd.DataFrame(cum).T.reindex(columns=codes).astype(float)
    cum_df.index = pd.PeriodIndex(cum_df.index, freq='Q')
    single = cum_df.copy()
    for p in cum_df.index:
        if p.quarter > 1:
            prev = p - 1
            single.loc[p] = cum_df.loc[p] - cum_df.loc[prev] if prev in cum_df.index else float('nan')
    return single


def build_dataset(codes: list[str], *, start: str = '2015-01-01', today: date | None = None,
                  progress=None) -> Dataset:
    today = today or date.today()
    end = pd.Timestamp(today) + pd.Timedelta(days=1)
    prices = load_prices(codes, start, end.strftime('%Y-%m-%d'))
    missing = [c for c in codes if c not in prices.columns or prices[c].dropna().empty]
    notes = [f'Yahoo Finance 無價格資料：{missing}'] if missing else []
    first = pd.Period(start, freq='M')
    last = pd.Period(today, freq='M') - 1
    revenue = load_revenue(codes, first, last, today, progress)
    pe = load_pe(codes, prices.index, progress)
    eps = load_eps(codes, int(start[:4]), today, progress)
    no_eps = [c for c in codes if eps[c].dropna().empty]
    if no_eps:
        notes.append(f'沒有 EPS（金融保險業財報格式不同，第一版未支援）：{no_eps}')
    return Dataset(prices=prices, revenue_yoy=revenue, pe=pe, eps=eps, notes=notes)


def load_chips(codes: list[str], days: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """每日三大法人買賣超（股）與融資餘額（張）。只讀已回補的快取資料，沒有的日子留空。"""
    fields = {'foreign': {}, 'trust': {}, 'margin': {}}
    for d in days:
        inst = rs.institutional_day(d.date())
        if not inst.empty:
            sub = inst[inst['code'].isin(codes)].set_index('code')
            fields['foreign'][d] = sub['foreign']
            fields['trust'][d] = sub['trust']
        mg = rs.margin_day(d.date())
        if not mg.empty:
            fields['margin'][d] = mg[mg['code'].isin(codes)].set_index('code')['margin_balance']
    return {k: pd.DataFrame(v).T.reindex(columns=codes).astype(float).sort_index() for k, v in fields.items()}
