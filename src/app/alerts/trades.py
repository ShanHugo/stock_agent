"""記錄買賣並更新 portfolio.json 的股數與成交均價（對照券商 App 的「成交均價」，不含手續費）。"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

from .portfolio import holding_from_dict


def apply_trade(raw: dict, side: str, symbol: str, qty: float, price: float, *,
                name: str | None = None, trade_date: str | None = None) -> tuple[dict, str]:
    """回傳 (更新後的 portfolio dict, 說明文字)。不修改傳入的 dict。"""
    if qty <= 0 or price <= 0:
        raise ValueError('股數與價格必須 > 0')
    raw = json.loads(json.dumps(raw))  # deep copy
    holdings = raw.setdefault('holdings', [])
    symbol = symbol.strip().upper() if not symbol[:1].isdigit() else symbol.strip()
    item = next((h for h in holdings if str(h['symbol']) == symbol), None)
    trade_date = trade_date or date.today().isoformat()

    if side == 'buy':
        if item is None:
            item = {'symbol': symbol, 'name': name or '', 'shares': qty, 'cost': price, 'entry_date': trade_date}
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
    elif side == 'sell':
        if item is None:
            raise ValueError(f'持股中沒有 {symbol}')
        old_sh = float(item['shares'])
        if qty > old_sh + 1e-9:
            raise ValueError(f'{symbol} 只有 {old_sh:g} 股，不能賣 {qty:g} 股')
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
    return raw, msg


def record_trade(portfolio_path: Path, log_path: Path, side: str, symbol: str, qty: float, price: float,
                 **kw) -> str:
    raw = json.loads(portfolio_path.read_text(encoding='utf-8')) if portfolio_path.exists() else {'holdings': []}
    new_raw, msg = apply_trade(raw, side, symbol, qty, price, **kw)
    portfolio_path.write_text(json.dumps(new_raw, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'date': kw.get('trade_date') or date.today().isoformat(), 'side': side,
                            'symbol': symbol, 'qty': qty, 'price': price}, ensure_ascii=False) + '\n')
    return msg
