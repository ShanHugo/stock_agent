"""記錄買賣並更新 portfolio.json 的股數與成交均價（對照券商 App 的「成交均價」，不含手續費）。

個股（非 ETF）買進必須對應一份 plan_trade 建立的計畫；賣出時寫一筆交易日誌供每月檢討。
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

from .portfolio import Holding, holding_from_dict
from .plans import load_plans, save_plans

EXIT_TOLERANCE = 0.005  # 成交價在停損/停利價 ±0.5% 內視為依計畫出場


def _norm(symbol: str) -> str:
    symbol = symbol.strip()
    return symbol if symbol[:1].isdigit() else symbol.upper()


def _pick_plan(plans: list[dict], symbol: str, plan_id: str | None) -> dict:
    pending = [p for p in plans if p['symbol'] == symbol and p['status'] == 'pending']
    if plan_id:
        match = [p for p in pending if p['id'] == plan_id]
        if not match:
            raise ValueError(f'找不到 {symbol} 的待執行計畫 {plan_id}')
        return match[0]
    if not pending:
        raise ValueError(f'{symbol} 是個股，買進前請先用 `python scripts/plan_trade.py new {symbol} ...` '
                         f'建立交易計畫（確定不需要可加 --no-plan）')
    if len(pending) > 1:
        raise ValueError(f'{symbol} 有多份待執行計畫，請用 --plan 指定：' + ', '.join(p['id'] for p in pending))
    return pending[0]


def apply_trade(raw: dict, side: str, symbol: str, qty: float, price: float, *,
                name: str | None = None, trade_date: str | None = None,
                plans: list[dict] | None = None, plan_id: str | None = None, no_plan: bool = False,
                etf: bool | None = None) -> dict:
    """回傳 {'portfolio', 'plans', 'message', 'warnings', 'journal'}。不修改傳入的資料。"""
    if qty <= 0 or price <= 0:
        raise ValueError('股數與價格必須 > 0')
    raw = json.loads(json.dumps(raw))
    plans = json.loads(json.dumps(plans or []))
    holdings = raw.setdefault('holdings', [])
    symbol = _norm(symbol)
    item = next((h for h in holdings if str(h['symbol']) == symbol), None)
    trade_date = trade_date or date.today().isoformat()
    warnings, journal = [], None

    if side == 'buy':
        plan = None
        is_etf = etf if etf is not None else (item or {}).get('etf')
        if Holding(symbol=symbol, shares=1, cost=1, etf=is_etf).needs_plan and not no_plan:
            plan = _pick_plan(plans, symbol, plan_id)
        if item is None:
            item = {'symbol': symbol, 'name': name or '', 'shares': qty, 'cost': price, 'entry_date': trade_date}
            if etf is not None:
                item['etf'] = etf
            holdings.append(item)
            msg = f'新增 {symbol}：{qty:g} 股 @ {price:g}'
        else:
            old_sh, old_cost = float(item['shares']), float(item['cost'])
            new_sh = old_sh + qty
            item['cost'] = round((old_sh * old_cost + qty * price) / new_sh, 4)
            item['shares'] = round(new_sh, 4)
            item.setdefault('entry_date', trade_date)
            msg = (f'{symbol} 買進 {qty:g} 股 @ {price:g}：股數 {old_sh:g} → {new_sh:g}，'
                   f'均價 {old_cost:g} → {item["cost"]:g}')
        if name:
            item['name'] = name
        if plan:
            if qty > plan['max_shares']:
                warnings.append(f"買進 {qty:g} 股超過計畫上限 {plan['max_shares']} 股")
            risk = qty * (price - plan['stop'])
            if risk > plan['risk_budget'] * 1.0001:
                warnings.append(f"以成交價 {price:g} 計算，跌到停損 {plan['stop']:g} 會虧約 {risk:,.0f}，"
                                f"超過風險預算 {plan['risk_budget']:,.0f}")
            item['stop_price'] = plan['stop']
            item.pop('stop_loss_pct', None)
            if plan.get('target'):
                item['target_price'] = plan['target']
                item.pop('take_profit_pct', None)
            item['plan'] = {
                'id': plan['id'],
                'thesis': plan['thesis'],
                'invalidation': plan['invalidation'],
                'opened': trade_date,
                'last_reviewed': trade_date,
                'initial_risk_per_share': round(price - plan['stop'], 4),
            }
            plan.update(status='filled', filled_date=trade_date, fill_price=price, fill_qty=qty)
            msg += f"（套用計畫 {plan['id']}，停損 {plan['stop']:g}）"
    elif side == 'sell':
        if item is None:
            raise ValueError(f'持股中沒有 {symbol}')
        old_sh = float(item['shares'])
        if qty > old_sh + 1e-9:
            raise ValueError(f'{symbol} 只有 {old_sh:g} 股，不能賣 {qty:g} 股')
        journal = _journal_entry(item, qty, price, trade_date)
        left = round(old_sh - qty, 4)
        if left <= 0:
            holdings.remove(item)
            msg = f'{symbol} 全部賣出 {qty:g} 股 @ {price:g}，已從持股移除'
        else:
            item['shares'] = left
            msg = f'{symbol} 賣出 {qty:g} 股 @ {price:g}：股數 {old_sh:g} → {left:g}（均價不變 {item["cost"]:g}）'
    else:
        raise ValueError("side 必須是 'buy' 或 'sell'")

    if item in holdings:
        _, errs = holding_from_dict(item)
        if errs:
            raise ValueError('更新後的持股設定有誤：' + '; '.join(errs))
    return {'portfolio': raw, 'plans': plans, 'message': msg, 'warnings': warnings, 'journal': journal}


def _journal_entry(item: dict, qty: float, price: float, trade_date: str) -> dict:
    cost = float(item['cost'])
    plan = item.get('plan') or {}
    stop, target = item.get('stop_price'), item.get('target_price')
    exit_type = 'discretionary'
    if stop is not None and price <= stop * (1 + EXIT_TOLERANCE):
        exit_type = 'stop'
    elif target is not None and price >= target * (1 - EXIT_TOLERANCE):
        exit_type = 'target'
    r_per_share = plan.get('initial_risk_per_share')
    return {
        'date': trade_date,
        'symbol': str(item['symbol']),
        'qty': qty,
        'price': price,
        'cost': cost,
        'pnl': round((price - cost) * qty, 2),
        'return_pct': price / cost - 1,
        'r_multiple': round((price - cost) / r_per_share, 2) if r_per_share else None,
        'exit_type': exit_type,
        # 停損出場但成交價比停損價低超過容忍範圍 → 執行慢了
        'late_stop': bool(stop is not None and price < stop * (1 - EXIT_TOLERANCE)),
        'had_plan': bool(plan),
        'plan_id': plan.get('id'),
        'opened': plan.get('opened') or item.get('entry_date'),
    }


def mark_reviewed(raw: dict, symbol: str, today: str | None = None) -> tuple[dict, str]:
    raw = json.loads(json.dumps(raw))
    symbol = _norm(symbol)
    item = next((h for h in raw.get('holdings', []) if str(h['symbol']) == symbol), None)
    if item is None or not item.get('plan'):
        raise ValueError(f'{symbol} 沒有附帶交易計畫的持股')
    item['plan']['last_reviewed'] = today or date.today().isoformat()
    return raw, f"{symbol} 已確認理由仍成立：{item['plan']['thesis']}"


def record_trade(portfolio_path: Path, state_dir: Path, side: str, symbol: str, qty: float, price: float,
                 **kw) -> dict:
    raw = json.loads(portfolio_path.read_text(encoding='utf-8')) if portfolio_path.exists() else {'holdings': []}
    plans_path = state_dir / 'plans.json'
    res = apply_trade(raw, side, symbol, qty, price, plans=load_plans(plans_path), **kw)
    portfolio_path.write_text(json.dumps(res['portfolio'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    save_plans(plans_path, res['plans'])
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'trades.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps({'date': kw.get('trade_date') or date.today().isoformat(), 'side': side,
                            'symbol': _norm(symbol), 'qty': qty, 'price': price}, ensure_ascii=False) + '\n')
    if res['journal']:
        with (state_dir / 'journal.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(res['journal'], ensure_ascii=False) + '\n')
    return res
