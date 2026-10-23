"""每日持股檢查：抓資料 → Data QA → 停損/停利、計畫失效、回落、配置、部位風險 → 組訊息 →（講評）→ 交給呼叫端推送。"""
from __future__ import annotations
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from ..data.qa import run_quality_checks
from .portfolio import Portfolio, Holding
from .rules import evaluate_holding, drawdown_status, allocation_status, OK, TRIGGERED
from .plans import check_invalidation, thesis_review_due, portfolio_risk, validate_risk, FUNDAMENTAL_TYPES
from .notify import build_message

Fetcher = Callable[[str, str, str], pd.DataFrame]
RevenueLookup = Callable[[str], pd.DataFrame]  # 代號 → 月營收歷史（index=YYYY-MM）

FX_SYMBOL = 'TWD=X'  # 1 美元 = ? 台幣
HISTORY_DAYS = 400   # 回落要看近一年高點，多抓一些
REVIEW_REMIND_EVERY = 7  # 理由確認逾期後，每隔幾天再提醒一次

# 各市場「當天 K 棒可視為收盤價」的時間（當地時間）；之前的當天 K 棒只是盤中價
MARKET_CLOSE = {
    'TW': (ZoneInfo('Asia/Taipei'), time(14, 0)),
    'TWO': (ZoneInfo('Asia/Taipei'), time(14, 0)),
    'US': (ZoneInfo('America/New_York'), time(16, 30)),
}


def _load_state(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        st = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return st if 'price' in st else {}  # 舊格式直接重置


def drop_unconfirmed_bar(df: pd.DataFrame, market: str, now: datetime | None) -> pd.DataFrame:
    """若該市場今天還沒收盤，丟掉日期為「當地今天」的 K 棒。now 需帶時區。"""
    if now is None or df.empty:
        return df
    tz, close_at = MARKET_CLOSE[market]
    local = now.astimezone(tz)
    if local.time() >= close_at:
        return df
    return df[pd.to_datetime(df.index).date < local.date()]


def update_streaks(prev: dict, alerts: list[dict]) -> tuple[dict, dict]:
    """同一檔、同一種停損/停利提醒連續出現時累計天數；回傳 (新 state, {symbol: streak})。"""
    new_state, streaks = {}, {}
    for a in alerts:
        p = prev.get(a['symbol'])
        if p and p['kind'] == a['kind']:
            count = p['count'] + (1 if p['last_date'] != a['date'] else 0)
        else:
            count = 1
        new_state[a['symbol']] = {'kind': a['kind'], 'count': count, 'last_date': a['date']}
        streaks[a['symbol']] = count
    return new_state, streaks


def new_drawdown_alerts(prev: dict, statuses: list[dict]) -> tuple[dict, list[dict]]:
    """只有跨過比上次更深的門檻才通知；回升到所有門檻之上就重置。"""
    new_state, alerts = {}, []
    for d in statuses:
        if d['level'] is None:
            continue
        new_state[d['symbol']] = d['level']
        if d['level'] > prev.get(d['symbol'], 0):
            alerts.append(d)
    return new_state, alerts


def _risk_flags(pr: dict) -> list[str]:
    flags = ['total'] if pr['total_risk_exceeded'] else []
    return flags + [f"size:{p['symbol']}" for p in pr['oversized']]


def run_alerts(
    portfolio: Portfolio,
    fetcher: Fetcher,
    *,
    today: date,
    default_tw_fee_bps: float,
    state_path: Path | None = None,
    narrator=None,
    force: bool = False,
    now: datetime | None = None,
    revenue_lookup: RevenueLookup | None = None,
) -> dict:
    """回傳 {'skipped', 'message', 'alerts', 'drawdowns', 'allocation', 'risk', 'evaluations', 'errors', ...}；不負責推送。

    沒有任何持股出現比上次檢查更新的收盤資料時（休市、重複執行）略過，除非 force。
    """
    state = _load_state(state_path)
    errors = list(portfolio.errors)
    evaluations, dd_statuses, latest = [], [], {}
    end = (today + timedelta(days=1)).strftime('%Y-%m-%d')
    for h in portfolio.holdings:
        start = today - timedelta(days=HISTORY_DAYS)
        if h.entry_date:
            start = min(start, pd.to_datetime(h.entry_date).date())
        try:
            df = fetcher(h.yf_symbol, start.strftime('%Y-%m-%d'), end)
        except Exception as e:
            errors.append(f'{h.label}: 抓資料失敗（{e}）')
            continue
        df = drop_unconfirmed_bar(df, h.market, now)
        qa = run_quality_checks(df, min_rows=5)
        if qa['status'] == 'FAIL':
            bad = '; '.join(c['detail'] for c in qa['checks'] if c['status'] == 'FAIL')
            errors.append(f'{h.label}: 資料異常未判斷（{bad}）')
            continue
        fee, tax = portfolio.sell_costs_bps(h, default_tw_fee_bps)
        ev = evaluate_holding(h, df['close'], near_threshold=portfolio.near_threshold, fee_bps=fee, tax_bps=tax)
        if ev['kind'] not in TRIGGERED and h.plan:
            revenue = None
            if any(r['type'] in FUNDAMENTAL_TYPES for r in h.plan.get('invalidation', [])):
                revenue = _revenue(revenue_lookup, h.symbol, h.market, errors)
            hits = check_invalidation(h, df['close'], today, revenue=revenue)
            if hits:
                ev = _as_plan_invalid(ev, h, hits, fee, tax)
        evaluations.append(ev)
        latest[h.symbol] = (h, ev)
        dd = drawdown_status(h, df['close'], portfolio.drawdown_window_days)
        if dd:
            dd_statuses.append(dd)

    last_dates = state.get('last_dates', {})
    has_new = any(ev['date'] > last_dates.get(sym, '') for sym, (_, ev) in latest.items())
    if not force and latest and not has_new:
        return {'skipped': '沒有比上次檢查更新的收盤資料（休市或已檢查過）', 'message': None,
                'alerts': [], 'drawdowns': [], 'allocation': None, 'risk': None, 'evaluations': evaluations,
                'errors': errors, 'narration_error': None}

    alerts = sorted([e for e in evaluations if e['kind'] != OK],
                    key=lambda e: (e['kind'] not in TRIGGERED, e['symbol']))
    price_state, streaks = update_streaks(state.get('price', {}), alerts)
    dd_state, dd_alerts = new_drawdown_alerts(state.get('drawdown', {}), dd_statuses)

    # 美元匯率：配置與部位風險都需要換算台幣
    fx, fx_err = None, None
    needs_fx = any(h.is_us for h, _ in latest.values()) and (portfolio.allocation or portfolio.risk)
    if needs_fx:
        try:
            fx_df = fetcher(FX_SYMBOL, (today - timedelta(days=10)).strftime('%Y-%m-%d'), end)
            fx = float(fx_df['close'].dropna().iloc[-1])
        except Exception as e:
            fx_err = f'抓美元匯率失敗（{e}），略過配置與部位風險檢查'
            errors.append(fx_err)

    alloc, alloc_alert = None, None
    if portfolio.allocation and latest and not fx_err:
        if len(latest) < len(portfolio.holdings):
            errors.append('有持股抓不到價格，略過配置偏離檢查')
        else:
            alloc = _allocation(portfolio, latest, fx)
            if alloc['out_of_band'] and not state.get('alloc_out'):
                alloc_alert = alloc  # 從正常變偏離才通知

    risk, risk_alert = None, None
    risk_flags = state.get('risk_flags', [])
    if portfolio.risk and latest and not fx_err:
        errs = validate_risk(portfolio.risk)
        if errs:
            errors.extend(errs)
        else:
            risk = portfolio_risk([(h, ev['close'], fx if h.is_us else 1.0) for h, ev in latest.values()],
                                  portfolio.risk)
            new_flags = _risk_flags(risk)
            if set(new_flags) - set(risk_flags):
                risk_alert = risk  # 出現新的超限項目才通知
            risk_flags = new_flags

    # 月營收公布：觀察清單 + 持有的上市個股；第一次只記錄、不通知
    revenues, revenue_seen = [], dict(state.get('revenue_seen', {}))
    if revenue_lookup:
        codes = list(dict.fromkeys(portfolio.watchlist + [h.symbol for h in portfolio.holdings
                                                          if h.market == 'TW' and not h.is_etf]))
        for code in codes:
            hist = _revenue(revenue_lookup, code, 'TW', errors)
            if hist is None or hist.empty:
                continue
            month = hist.index[-1]
            if revenue_seen.get(code) and month > revenue_seen[code]:
                last = hist.iloc[-1]
                revenues.append({'code': code, 'name': last.get('name', ''), 'month': month,
                                 'revenue_yi': last['revenue'] / 1e5, 'mom_pct': last['mom_pct'],
                                 'yoy_pct': last['yoy_pct'], 'cum_yoy_pct': last['cum_yoy_pct'],
                                 'note': last.get('note') or '',
                                 'held': any(h.symbol == code for h in portfolio.holdings)})
            revenue_seen[code] = month

    reviews, reminded = [], dict(state.get('review_reminded', {}))
    review_days = (portfolio.risk or {}).get('review_days', 30)
    for h, ev in latest.values():
        days = thesis_review_due(h, today, review_days)
        last = reminded.get(h.symbol)
        if days and (not last or (today - date.fromisoformat(last)).days >= REVIEW_REMIND_EVERY):
            reviews.append({'symbol': h.symbol, 'label': h.label, 'days': days, 'thesis': h.plan['thesis']})
            reminded[h.symbol] = today.isoformat()
        elif not days:
            reminded.pop(h.symbol, None)

    def build(narration=None):
        return build_message(alerts, streaks, errors, today.strftime('%Y-%m-%d'), narration=narration,
                             drawdowns=dd_alerts, allocation=alloc_alert, fx=fx, risk=risk_alert, reviews=reviews,
                             revenues=revenues)

    message = build()
    narration_error = None
    if message and narrator is not None and (alerts or dd_alerts or alloc_alert or risk_alert):
        has_triggered = any(a['kind'] in TRIGGERED for a in alerts)
        narration, narration_error = narrator.summarize(message, has_triggered=has_triggered)
        if narration:
            message = build(narration)

    if state_path:
        new_state = {
            'price': price_state,
            'drawdown': dd_state,
            'alloc_out': bool(alloc['out_of_band']) if alloc else state.get('alloc_out', False),
            'risk_flags': risk_flags,
            'review_reminded': reminded,
            'revenue_seen': revenue_seen,
            'last_dates': {**last_dates, **{sym: ev['date'] for sym, (_, ev) in latest.items()}},
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding='utf-8')

    return {'skipped': None, 'message': message, 'alerts': alerts, 'drawdowns': dd_alerts,
            'allocation': alloc, 'risk': risk, 'reviews': reviews, 'revenues': revenues, 'fx': fx, 'evaluations': evaluations,
            'errors': errors, 'narration_error': narration_error,
            'run_at': datetime.now().isoformat(timespec='seconds')}


def _revenue(lookup: RevenueLookup | None, code: str, market: str, errors: list[str]) -> pd.DataFrame | None:
    if lookup is None:
        return None
    if market != 'TW':
        errors.append(f'{code}: 月營收目前只支援上市公司，略過營收相關檢查')
        return None
    try:
        return lookup(code)
    except Exception as e:
        errors.append(f'{code}: 抓月營收失敗（{e}），略過營收相關檢查')
        return None


def _as_plan_invalid(ev: dict, h: Holding, hits: list[str], fee: float, tax: float) -> dict:
    """計畫失效條件成立：依計畫出場（全部賣出）。"""
    qty = h.shares
    notional = qty * ev['close']
    fees = notional * (fee + tax) / 10000
    whole = int(qty) if not h.is_us else 0
    return {**ev, 'kind': 'PLAN_INVALID', 'invalid_reasons': hits, 'thesis': h.plan.get('thesis'),
            'action': {'side': 'SELL', 'qty': qty, 'lots': whole // 1000, 'odd_shares': whole % 1000,
                       'est_notional': notional, 'est_fees': fees,
                       'est_pnl': (ev['close'] - h.cost) * qty - fees}}


def _allocation(portfolio: Portfolio, latest: dict[str, tuple[Holding, dict]], fx: float | None) -> dict:
    values = {sym: h.shares * ev['close'] * (fx if h.is_us else 1.0) for sym, (h, ev) in latest.items()}
    groups = {sym: h.alloc_group for sym, (h, _) in latest.items()}
    a = portfolio.allocation
    return allocation_status(values, groups, a['targets'], a['threshold'])
