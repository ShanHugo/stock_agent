"""交易計畫與部位控管：買前先決定理由、出場條件與最多能虧多少，系統據此算出最多能買幾股。

風險參數放在 portfolio.json 的 "risk"（全部由你自己填）：
  capital             總資金（台幣），用來計算風險預算
  max_risk_per_trade  單筆最多虧損占總資金比例，例 0.01 = 1%
  max_position_pct    單一持股市值上限占總資金比例，例 0.2（選填）
  max_total_risk      所有持股同時跌到停損時，合計虧損上限占總資金比例（選填）
  review_days         每隔幾天提醒你重新確認買進理由，預設 30

計畫的失效條件（invalidation，目前只支援價格/時間條件）：
  {"type": "close_below", "price": 900}           收盤跌破某價位
  {"type": "below_ma", "days": 60}                收盤跌破 N 日均線
  {"type": "time_stop", "days": 40, "min_gain": 0.05}  持有 N 個日曆天仍未漲到 min_gain
  {"type": "revenue_yoy_below", "threshold": 0, "months": 2}  最近 N 個月營收年增率都低於 threshold（%）
"""
from __future__ import annotations
import json
import math
import uuid
from datetime import date
from pathlib import Path

import pandas as pd

from .portfolio import Holding

INVALIDATION_TYPES = {'close_below', 'below_ma', 'time_stop', 'revenue_yoy_below'}
FUNDAMENTAL_TYPES = {'revenue_yoy_below'}


def validate_risk(risk: dict | None) -> list[str]:
    if not risk:
        return ['portfolio.json 尚未設定 "risk"（capital、max_risk_per_trade），無法計算部位']
    errs = []
    if not risk.get('capital') or risk['capital'] <= 0:
        errs.append('risk.capital 必須 > 0')
    if not (0 < risk.get('max_risk_per_trade', 0) < 1):
        errs.append('risk.max_risk_per_trade 應介於 0~1，例如 0.01')
    for k in ('max_position_pct', 'max_total_risk'):
        if k in risk and not (0 < risk[k] <= 1):
            errs.append(f'risk.{k} 應介於 0~1')
    return errs


def validate_invalidation(rules: list[dict]) -> list[str]:
    errs = []
    for r in rules:
        t = r.get('type')
        if t not in INVALIDATION_TYPES:
            errs.append(f'未知的失效條件 {t}，可用: {sorted(INVALIDATION_TYPES)}')
        elif t == 'close_below' and not r.get('price', 0) > 0:
            errs.append('close_below 需要 price > 0')
        elif t == 'below_ma' and not int(r.get('days', 0)) >= 2:
            errs.append('below_ma 需要 days >= 2')
        elif t == 'time_stop' and not (int(r.get('days', 0)) > 0 and 'min_gain' in r):
            errs.append('time_stop 需要 days > 0 與 min_gain')
        elif t == 'revenue_yoy_below' and not ('threshold' in r and int(r.get('months', 0)) >= 1):
            errs.append('revenue_yoy_below 需要 threshold 與 months >= 1')
    return errs


def size_position(*, capital: float, max_risk_per_trade: float, entry: float, stop: float,
                  buy_fee_bps: float, sell_fee_bps: float, sell_tax_bps: float,
                  max_position_pct: float | None = None) -> dict:
    """依風險預算算出最多可買股數（整股，含零股）。

    每股風險 = (買價 - 停損價) + 買進手續費 + 停損賣出的手續費與證交稅。
    """
    if stop >= entry:
        raise ValueError(f'停損價 {stop} 必須低於買價 {entry}')
    budget = capital * max_risk_per_trade
    per_share = (entry - stop) + entry * buy_fee_bps / 10000 + stop * (sell_fee_bps + sell_tax_bps) / 10000
    by_risk = math.floor(budget / per_share)
    shares, limited_by = by_risk, 'risk'
    if max_position_pct:
        by_size = math.floor(capital * max_position_pct / entry)
        if by_size < shares:
            shares, limited_by = by_size, 'position'
    return {
        'risk_budget': budget,
        'risk_per_share': per_share,
        'max_shares': shares,
        'limited_by': limited_by,
        'position_value': shares * entry,
        'position_pct': shares * entry / capital,
        'risk_at_stop': shares * per_share,
        'risk_pct': shares * per_share / capital,
        'stop_distance_pct': 1 - stop / entry,
    }


# --- 計畫檔（state/plans.json）---

def load_plans(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_plans(path: Path, plans: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding='utf-8')


def new_plan(symbol: str, *, entry: float, stop: float, thesis: str, sizing: dict,
             target: float | None = None, invalidation: list[dict] | None = None,
             created: str | None = None) -> dict:
    if not thesis.strip():
        raise ValueError('請寫下買進理由（thesis）')
    invalidation = invalidation or []
    errs = validate_invalidation(invalidation)
    if errs:
        raise ValueError('; '.join(errs))
    return {
        'id': uuid.uuid4().hex[:8],
        'symbol': symbol,
        'status': 'pending',
        'created': created or date.today().isoformat(),
        'entry': entry,
        'stop': stop,
        'target': target,
        'thesis': thesis.strip(),
        'invalidation': invalidation,
        'max_shares': sizing['max_shares'],
        'risk_per_share': sizing['risk_per_share'],
        'risk_budget': sizing['risk_budget'],
    }


# --- 每日檢查用 ---

def check_invalidation(h: Holding, closes: pd.Series, today: date,
                       revenue: pd.DataFrame | None = None) -> list[str]:
    """回傳已成立的失效條件說明（空 list = 理由仍成立）。revenue 為月營收歷史（index=YYYY-MM，含 yoy_pct）。"""
    plan = h.plan or {}
    closes = closes.dropna().sort_index()
    close = float(closes.iloc[-1])
    hits = []
    for r in plan.get('invalidation', []):
        t = r['type']
        if t == 'close_below' and close < r['price']:
            hits.append(f"收盤 {close:g} 跌破 {r['price']:g}")
        elif t == 'below_ma':
            n = int(r['days'])
            if len(closes) >= n:
                ma = float(closes.iloc[-n:].mean())
                if close < ma:
                    hits.append(f'收盤 {close:g} 跌破 {n} 日均線 {ma:,.2f}')
        elif t == 'time_stop':
            opened = plan.get('opened') or h.entry_date
            if opened:
                held = (today - pd.to_datetime(opened).date()).days
                gain = close / h.cost - 1
                if held >= int(r['days']) and gain < r['min_gain']:
                    hits.append(f"持有 {held} 天，報酬 {gain:+.1%} 未達 {r['min_gain']:+.0%}")
        elif t == 'revenue_yoy_below' and revenue is not None and not revenue.empty:
            n = int(r['months'])
            recent = revenue['yoy_pct'].iloc[-n:]
            if len(recent) == n and recent.notna().all() and (recent < r['threshold']).all():
                detail = '、'.join(f'{m} {v:+.1f}%' for m, v in recent.items())
                hits.append(f"月營收連續 {n} 個月年增率低於 {r['threshold']:g}%（{detail}）")
    return hits


def thesis_review_due(h: Holding, today: date, review_days: int) -> int | None:
    """距離上次確認理由超過 review_days 時，回傳已經過的天數。"""
    plan = h.plan or {}
    last = plan.get('last_reviewed') or plan.get('opened')
    if not last or not plan.get('thesis'):
        return None
    days = (today - pd.to_datetime(last).date()).days
    return days if days >= review_days else None


def portfolio_risk(holdings_closes: list[tuple[Holding, float, float]], risk: dict) -> dict:
    """holdings_closes: [(holding, 收盤價, 換算台幣匯率)]。
    risk.capital 是「操作個股」的資金，所以總曝險只計入有停損價的個股；ETF（長期部位）不計入，也不受單一持股上限。"""
    capital = risk['capital']
    open_risk, positions = 0.0, []
    for h, close, fx in holdings_closes:
        value = h.shares * close * fx
        stop = h.stop_level()
        r = max(0.0, (close - stop) * h.shares * fx) if stop is not None and not h.is_etf else None
        if r is not None:
            open_risk += r
        positions.append({'symbol': h.symbol, 'label': h.label, 'value': value,
                          'pct': value / capital, 'risk': r})
    # 單一持股上限只管個股；分散型 ETF 不受限
    etfs = {h.symbol for h, _, _ in holdings_closes if h.is_etf}
    over = [p for p in positions if risk.get('max_position_pct') and p['symbol'] not in etfs
            and p['pct'] > risk['max_position_pct']]
    return {
        'capital': capital,
        'open_risk': open_risk,
        'open_risk_pct': open_risk / capital,
        'max_total_risk': risk.get('max_total_risk'),
        'total_risk_exceeded': bool(risk.get('max_total_risk') and open_risk / capital > risk['max_total_risk']),
        'oversized': over,
        'max_position_pct': risk.get('max_position_pct'),
    }
