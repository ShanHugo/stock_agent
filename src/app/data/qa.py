"""Data QA 階段：純 Python 規則檢查，結果決定流程能否往下走。

狀態等級：PASS < WARN < FAIL。任何 FAIL 會讓 orchestrator 停止後續階段。
"""
from __future__ import annotations
import pandas as pd

REQUIRED_COLS = ['open', 'high', 'low', 'close', 'volume']
_LEVEL = {'PASS': 0, 'WARN': 1, 'FAIL': 2}


def _worst(a: str, b: str) -> str:
    return a if _LEVEL[a] >= _LEVEL[b] else b


def run_quality_checks(
    df: pd.DataFrame,
    *,
    min_rows: int = 20,
    gap_threshold: float = 0.15,
    zero_volume_ratio: float = 0.2,
    expected_end: str | None = None,
    stale_days: int = 5,
) -> dict:
    """檢查 OHLCV 資料，回傳 {'status', 'rows', 'period', 'checks': [...]}。

    gap_threshold 預設 15%：台股漲跌幅上限 10%，超過 15% 多半是除權息/分割或資料錯誤。
    """
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, **extra):
        checks.append({'name': name, 'status': status, 'detail': detail, **extra})

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        add('required_columns', 'FAIL', f'缺少欄位: {missing}')
        return _finish(df, checks)
    add('required_columns', 'PASS', '欄位齊全')

    if len(df) < 2:
        add('row_count', 'FAIL', f'資料只有 {len(df)} 筆，無法回測')
        return _finish(df, checks)
    if len(df) < min_rows:
        add('row_count', 'WARN', f'資料 {len(df)} 筆，少於建議的 {min_rows} 筆')
    else:
        add('row_count', 'PASS', f'{len(df)} 筆')

    dup = int(df.index.duplicated().sum())
    if dup:
        add('duplicate_dates', 'FAIL', f'{dup} 個重複日期')
    else:
        add('duplicate_dates', 'PASS', '無重複日期')

    if not df.index.is_monotonic_increasing:
        add('date_order', 'WARN', '日期未排序（流程會先排序）')
    else:
        add('date_order', 'PASS', '日期已遞增')

    price = df[['open', 'high', 'low', 'close']]
    n_nan = int(price.isna().any(axis=1).sum())
    close_nan_ratio = float(df['close'].isna().mean())
    if close_nan_ratio > 0.05:
        add('missing_values', 'FAIL', f'收盤價缺值比例 {close_nan_ratio:.1%} 超過 5%')
    elif n_nan:
        add('missing_values', 'WARN', f'{n_nan} 筆 OHLC 有缺值')
    else:
        add('missing_values', 'PASS', '無缺值')

    n_nonpos = int((price <= 0).any(axis=1).sum())
    if n_nonpos:
        add('non_positive_price', 'FAIL', f'{n_nonpos} 筆價格 <= 0')
    else:
        add('non_positive_price', 'PASS', '價格皆為正')

    bad_ohlc = (
        (df['high'] < df[['open', 'close']].max(axis=1))
        | (df['low'] > df[['open', 'close']].min(axis=1))
        | (df['low'] > df['high'])
    )
    n_bad = int(bad_ohlc.sum())
    if n_bad:
        add('ohlc_consistency', 'WARN', f'{n_bad} 筆高低價與開收盤不一致',
            dates=[d.strftime('%Y-%m-%d') for d in df.index[bad_ohlc]][:10])
    else:
        add('ohlc_consistency', 'PASS', '高低價一致')

    chg = df['close'].sort_index().pct_change().abs()
    gaps = chg[chg > gap_threshold]
    if len(gaps):
        add('price_gap', 'WARN', f'{len(gaps)} 天收盤變動超過 ±{gap_threshold:.0%}',
            dates=[d.strftime('%Y-%m-%d') for d in gaps.index][:10])
    else:
        add('price_gap', 'PASS', f'無超過 ±{gap_threshold:.0%} 的跳動')

    zv = float((df['volume'].fillna(0) == 0).mean())
    if zv > zero_volume_ratio:
        add('zero_volume', 'WARN', f'零成交量比例 {zv:.1%} 超過 {zero_volume_ratio:.0%}')
    else:
        add('zero_volume', 'PASS', f'零成交量比例 {zv:.1%}')

    if expected_end:
        lag = (pd.to_datetime(expected_end) - pd.to_datetime(df.index.max())).days
        if lag > stale_days:
            add('freshness', 'WARN', f'最新資料比要求的結束日早 {lag} 天')
        else:
            add('freshness', 'PASS', f'最新資料落後 {max(lag, 0)} 天')

    return _finish(df, checks)


def _finish(df: pd.DataFrame, checks: list[dict]) -> dict:
    status = 'PASS'
    for c in checks:
        status = _worst(status, c['status'])
    period = None
    if len(df):
        period = {'start': pd.to_datetime(df.index.min()).strftime('%Y-%m-%d'),
                  'end': pd.to_datetime(df.index.max()).strftime('%Y-%m-%d')}
    return {'status': status, 'rows': int(len(df)), 'period': period, 'checks': checks}
