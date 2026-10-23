"""策略回測用的歷史資料（含公布時間延遲，避免偷看未來）。價格由 lab.market 自行還原（證交所／櫃買官方行情）。

  月營收    公開資訊觀測站（沿用 research.sources，有快取），第 M 月營收在 M+1 月 11 日起才可用
  估值      證交所 BWIBBU_d（每月最後交易日、全部上市股票的本益比/殖利率/淨值比）
  單季 EPS  公開資訊觀測站綜合損益彙總表（累計數相減），依法定期限設定可用日：
            Q1→5/16、Q2→8/15、Q3→11/15、Q4（年報）→次年 4/1
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..research import sources as rs

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


def load_revenue(codes: list[str], start: pd.Period, end: pd.Period, today: date, progress=None,
                 markets=('sii',)) -> pd.DataFrame:
    """markets：'sii' 上市、'otc' 上櫃（公開資訊觀測站的分類）。"""
    rows = {}
    m = start
    while m <= end:
        if progress:
            progress(f'月營收 {m}')
        parts = []
        for mk in markets:
            try:
                parts.append(rs.monthly_revenue(m.year, m.month, today=today, market=mk))
            except rs.NoData:
                pass
        if parts:
            df = pd.concat(parts).drop_duplicates('code')
            rows[m] = df[df['code'].isin(codes)].set_index('code')['yoy_pct']
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


def load_eps(codes: list[str], first_year: int, today: date, progress=None, markets=('sii',)) -> pd.DataFrame:
    cum = {}
    for y in range(first_year, today.year + 1):
        for q in range(1, 5):
            p = pd.Period(year=y, quarter=q, freq='Q')
            if Dataset.eps_available(p) > pd.Timestamp(today):
                continue
            if progress:
                progress(f'財報 {p}')
            parts = []
            for mk in markets:
                try:
                    parts.append(rs.quarterly_income(y, q, today=today, market=mk))
                except rs.NoData:
                    pass
            if parts:
                df = pd.concat(parts).drop_duplicates('code')
                cum[p] = df[df['code'].isin(codes)].set_index('code')['eps']
    cum_df = pd.DataFrame(cum).T.reindex(columns=codes).astype(float)
    cum_df.index = pd.PeriodIndex(cum_df.index, freq='Q')
    single = cum_df.copy()
    for p in cum_df.index:
        if p.quarter > 1:
            prev = p - 1
            single.loc[p] = cum_df.loc[p] - cum_df.loc[prev] if prev in cum_df.index else float('nan')
    return single
