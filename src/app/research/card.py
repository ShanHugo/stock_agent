"""個股研究卡：把月營收、季度獲利、估值、籌碼、價格整理成同一張卡。全部由 Python 計算，不做買賣判斷。

金額單位：營收與損益來源為「千元」，卡片顯示為「億元」。
"""
from __future__ import annotations
from datetime import date, timedelta

import pandas as pd

from . import sources

K_TO_YI = 1e5  # 千元 → 億元


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b - 1


def revenue_section(hist: pd.DataFrame) -> dict | None:
    if hist.empty:
        return None
    last = hist.iloc[-1]
    yoy = hist['yoy_pct']
    sign = 1 if (yoy.iloc[-1] or 0) > 0 else -1
    streak = 0
    for v in reversed(yoy.tolist()):
        if v is not None and pd.notna(v) and (v > 0) == (sign > 0) and v != 0:
            streak += 1
        else:
            break
    rev = hist['revenue']

    def window_yoy(end_offset: int) -> float | None:
        # 最近 3 個月合計 vs 去年同期 3 個月合計（用各月的 last_year 欄位，免抓更早的資料）
        sl = hist.iloc[len(hist) - 3 - end_offset: len(hist) - end_offset] if end_offset else hist.iloc[-3:]
        if len(sl) < 3 or sl['last_year'].isna().any():
            return None
        return _pct(sl['revenue'].sum(), sl['last_year'].sum())

    recent12 = rev.iloc[-12:]
    return {
        'month': hist.index[-1],
        'revenue_yi': last['revenue'] / K_TO_YI,
        'mom_pct': last['mom_pct'],
        'yoy_pct': last['yoy_pct'],
        'cum_yoy_pct': last['cum_yoy_pct'],
        'note': last.get('note') or '',
        'yoy_streak': streak * sign,       # 正數 = 連續 N 個月年增；負數 = 連續 N 個月年減
        'yoy_3m': window_yoy(0),
        'yoy_3m_prev': window_yoy(3),
        'is_12m_high': len(recent12) >= 12 and last['revenue'] >= recent12.max(),
        'is_12m_low': len(recent12) >= 12 and last['revenue'] <= recent12.min(),
        'months': len(hist),
        'series': [{'month': m, 'revenue_yi': r / K_TO_YI, 'yoy_pct': y}
                   for m, r, y in zip(hist.index[-13:], rev.iloc[-13:], yoy.iloc[-13:])],
    }


def income_section(hist: pd.DataFrame) -> dict | None:
    if hist.empty:
        return None
    df = hist.copy()
    df['gross_margin'] = df['gross_profit'] / df['revenue']
    df['op_margin'] = df['op_income'] / df['revenue']
    df['net_margin'] = df['net_income'] / df['revenue']
    rows = [{'quarter': q, 'revenue_yi': r['revenue'] / K_TO_YI if pd.notna(r['revenue']) else None,
             'gross_margin': r['gross_margin'], 'op_margin': r['op_margin'], 'net_margin': r['net_margin'],
             'eps': r['eps']} for q, r in df.iterrows()]
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    yago = next((x for x in rows if x['quarter'] == f"{int(last['quarter'][:4]) - 1}{last['quarter'][4:]}"), None)

    def diff(a, b, k):
        return a[k] - b[k] if b and a.get(k) is not None and b.get(k) is not None else None

    eps4 = [x['eps'] for x in rows[-4:] if x['eps'] is not None]
    return {
        'latest': last,
        'qoq_revenue': _pct(last['revenue_yi'], prev['revenue_yi']) if prev else None,
        'yoy_revenue': _pct(last['revenue_yi'], yago['revenue_yi']) if yago else None,
        'yoy_eps': _pct(last['eps'], yago['eps']) if yago and yago['eps'] and yago['eps'] > 0 else None,
        'gm_change_qoq': diff(last, prev, 'gross_margin'),
        'gm_change_yoy': diff(last, yago, 'gross_margin'),
        'om_change_yoy': diff(last, yago, 'op_margin'),
        'eps_ttm': sum(eps4) if len(eps4) == 4 else None,
        'rows': rows[-5:],
    }


def _percentile(series: pd.Series, value: float | None) -> float | None:
    s = series.dropna()
    if value is None or s.empty:
        return None
    return float((s <= value).mean())


def valuation_section(hist: pd.DataFrame) -> dict | None:
    if hist.empty:
        return None
    last = hist.iloc[-1]
    out = {'date': hist.index[-1], 'days': len(hist),
           'from': hist.index[0]}
    for k in ('pe', 'pb', 'dividend_yield'):
        s = hist[k].dropna()
        v = last[k] if pd.notna(last[k]) else None
        out[k] = v
        out[f'{k}_pctile'] = _percentile(s, v)
        out[f'{k}_range'] = (float(s.min()), float(s.median()), float(s.max())) if not s.empty else None
    return out


def chips_section(code: str, days: list[date], fetch_inst=sources.institutional_day,
                  fetch_margin=sources.margin_day) -> dict | None:
    if not days:
        return None
    inst = []
    for d in days:
        df = fetch_inst(d)
        row = df[df['code'] == code]
        inst.append({'date': d, **(row.iloc[0].to_dict() if not row.empty else
                                   {'foreign': 0.0, 'trust': 0.0, 'dealer': 0.0, 'total': 0.0})})
    inst = pd.DataFrame(inst).set_index('date')

    def lots(s: pd.Series, n: int) -> float:
        return float(s.iloc[-n:].sum()) / 1000  # 股 → 張

    def streak(s: pd.Series) -> int:
        sign = 1 if s.iloc[-1] > 0 else (-1 if s.iloc[-1] < 0 else 0)
        n = 0
        for v in reversed(s.tolist()):
            if sign and (v > 0) == (sign > 0) and v != 0:
                n += 1
            else:
                break
        return n * sign

    m_first, m_last = fetch_margin(days[0]), fetch_margin(days[-1])
    mf, ml = m_first[m_first['code'] == code], m_last[m_last['code'] == code]
    margin = None
    if not mf.empty and not ml.empty:
        a, b = float(mf.iloc[0]['margin_balance']), float(ml.iloc[0]['margin_balance'])
        margin = {'balance': b, 'change': b - a, 'change_pct': _pct(b, a)}
    return {
        'days': len(days), 'from': days[0].isoformat(), 'to': days[-1].isoformat(),
        **{f'{k}_5d': lots(inst[k], 5) for k in ('foreign', 'trust', 'dealer', 'total')},
        **{f'{k}_{len(days)}d': lots(inst[k], len(days)) for k in ('foreign', 'trust', 'dealer', 'total')},
        'foreign_streak': streak(inst['foreign']),
        'trust_streak': streak(inst['trust']),
        'margin': margin,
    }


def price_section(df: pd.DataFrame) -> dict | None:
    if df is None or df.empty:
        return None
    c = df['close'].dropna()
    close = float(c.iloc[-1])
    yr = c.iloc[-252:]
    ma = {n: float(c.iloc[-n:].mean()) for n in (20, 60, 240) if len(c) >= n}
    return {
        'date': pd.to_datetime(c.index[-1]).strftime('%Y-%m-%d'),
        'close': close,
        'vs_ma': {n: close / v - 1 for n, v in ma.items()},
        'high_52w': float(yr.max()), 'low_52w': float(yr.min()),
        'from_high': close / float(yr.max()) - 1, 'from_low': close / float(yr.min()) - 1,
    }


def build_card(code: str, *, today: date | None = None, price_fetcher=None,
               revenue_months: int = 25, quarters: int = 9, valuation_months: int = 36, chip_days: int = 20,
               progress=None) -> dict:
    """組出研究卡（dict）。任何一個區塊抓不到資料就留 None 並記在 errors。"""
    today = today or date.today()
    say = progress or (lambda msg: None)
    card = {'code': code, 'as_of': today.isoformat(), 'errors': []}

    def section(name, fn):
        try:
            say(f'{code} {name}…')
            card[name] = fn()
        except Exception as e:  # 單一區塊失敗不影響其他區塊
            card[name] = None
            card['errors'].append(f'{name}: {e}')

    section('revenue', lambda: revenue_section(sources.revenue_history(code, revenue_months, today=today)))
    if card.get('revenue') is not None:
        rev = sources.revenue_history(code, 1, today=today)
        card['name'] = rev['name'].iloc[-1] if not rev.empty else ''
    section('income', lambda: income_section(sources.income_history(code, quarters, today=today)))
    section('valuation', lambda: valuation_section(sources.valuation_history(code, valuation_months, today=today)))
    section('chips', lambda: chips_section(code, sources.trading_days_back(today, chip_days)))
    if price_fetcher:
        section('price', lambda: price_section(price_fetcher(f'{code}.TW', (today - timedelta(days=400)).isoformat(),
                                                             today.isoformat())))
    return card
