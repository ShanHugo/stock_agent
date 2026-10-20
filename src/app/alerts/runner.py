"""每日收盤後的持股檢查：抓資料 → Data QA → 停損/停利判斷 → 組訊息 →（講評）→ 推送。"""
from __future__ import annotations
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from ..pipeline.qa import run_quality_checks
from .portfolio import Portfolio
from .rules import evaluate_holding, OK, TRIGGERED
from .notify import build_message

Fetcher = Callable[[str, str, str], pd.DataFrame]

# 13:30 收盤；14:00 前當天的 K 棒可能只是盤中價，不當作收盤價
CLOSE_CONFIRMED_AT = time(14, 0)


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update_streaks(state: dict, alerts: list[dict]) -> tuple[dict, dict]:
    """同一檔、同一種提醒連續出現時累計天數；回傳 (新 state, {symbol: streak})。"""
    new_state, streaks = {}, {}
    for a in alerts:
        prev = state.get(a['symbol'])
        if prev and prev['kind'] == a['kind'] and prev['last_date'] != a['date']:
            count = prev['count'] + 1
        elif prev and prev['kind'] == a['kind']:
            count = prev['count']  # 同一天重跑不累計
        else:
            count = 1
        new_state[a['symbol']] = {'kind': a['kind'], 'count': count, 'last_date': a['date']}
        streaks[a['symbol']] = count
    return new_state, streaks


def run_alerts(
    portfolio: Portfolio,
    fetcher: Fetcher,
    *,
    today: date,
    tx_fee_bps: float,
    tx_tax_bps: float,
    state_path: Path | None = None,
    narrator=None,
    force: bool = False,
    now: datetime | None = None,
) -> dict:
    """回傳 {'skipped', 'message', 'alerts', 'evaluations', 'errors', 'narration_error'}；不負責推送。

    now（台北時間）早於 14:00 時，丟掉日期為今天的 K 棒，避免把盤中價當收盤價。
    """
    drop_today = now is not None and now.date() == today and now.time() < CLOSE_CONFIRMED_AT
    errors = list(portfolio.errors)
    evaluations, latest_dates = [], set()
    end = today.strftime('%Y-%m-%d')
    for h in portfolio.holdings:
        start = min(pd.to_datetime(h.entry_date).date(), today - timedelta(days=90)).strftime('%Y-%m-%d')
        try:
            df = fetcher(h.yf_symbol, start, end)
        except Exception as e:
            errors.append(f'{h.label}: 抓資料失敗（{e}）')
            continue
        if drop_today:
            df = df[pd.to_datetime(df.index).date < today]
        qa = run_quality_checks(df, min_rows=5)
        if qa['status'] == 'FAIL':
            bad = '; '.join(c['detail'] for c in qa['checks'] if c['status'] == 'FAIL')
            errors.append(f'{h.label}: 資料異常未判斷（{bad}）')
            continue
        ev = evaluate_holding(h, df['close'], near_threshold=portfolio.near_threshold,
                              tx_fee_bps=tx_fee_bps, tx_tax_bps=tx_tax_bps)
        evaluations.append(ev)
        latest_dates.add(ev['date'])

    # 沒有任何一檔有今天的資料 → 視為休市或資料尚未更新，不推送
    if not force and evaluations and end not in latest_dates:
        return {'skipped': f'今日 {end} 無收盤資料（休市或尚未更新），最新為 {max(latest_dates)}',
                'message': None, 'alerts': [], 'evaluations': evaluations, 'errors': errors,
                'narration_error': None}

    alerts = sorted([e for e in evaluations if e['kind'] != OK],
                    key=lambda e: (e['kind'] not in TRIGGERED, e['symbol']))
    state = _load_state(state_path) if state_path else {}
    new_state, streaks = update_streaks(state, alerts)

    message = build_message(alerts, streaks, errors, end)
    narration_error = None
    if message and narrator is not None and alerts:
        has_triggered = any(a['kind'] in TRIGGERED for a in alerts)
        narration, narration_error = narrator.summarize(message, has_triggered=has_triggered)
        if narration:
            message = build_message(alerts, streaks, errors, end, narration=narration)

    if state_path:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding='utf-8')

    return {'skipped': None, 'message': message, 'alerts': alerts, 'evaluations': evaluations,
            'errors': errors, 'narration_error': narration_error,
            'run_at': datetime.now().isoformat(timespec='seconds')}
