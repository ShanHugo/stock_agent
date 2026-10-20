"""Execution 階段（模擬）：目標權重 → 整張委託建議 + 預估成本。不會真的下單。"""
from __future__ import annotations

LOT_SIZE = 1000  # 台股一張 = 1000 股


def _shares_for(weight: float, capital: float, price: float) -> int:
    lots = int(abs(weight) * capital / price // LOT_SIZE)
    return lots * LOT_SIZE * (1 if weight >= 0 else -1)


def run_execution(
    risk: dict,
    *,
    symbol: str,
    capital: float = 1_000_000,
    tx_fee_bps: float,
    tx_tax_bps: float,
    slippage_bps: float,
) -> dict:
    """依 PM/Risk 的 current/target 權重產生模擬委託。

    假設帳戶規模固定為 capital，現有持股 = current_weight 對應的整張數。
    手續費、滑價買賣都收；證交稅只在賣出收。
    Kill-Switch 觸發時停止新單（不產生任何委託）。
    """
    price = risk['last_close']
    current = _shares_for(risk['current_weight'], capital, price)
    target = _shares_for(risk['target_weight'], capital, price)
    base = {
        'symbol': symbol,
        'capital': capital,
        'reference_price': price,
        'current_shares': current,
        'target_shares': target,
        'orders': [],
    }

    if risk['kill_switch']['halt']:
        return {**base, 'status': 'HALTED', 'detail': 'Kill-Switch 觸發，停止新單',
                'reasons': risk['kill_switch']['reasons']}

    diff = target - current
    if diff == 0:
        detail = '部位不變，無需委託'
        if target == 0 and risk['target_weight'] != 0:
            detail = f'資金不足以買進一張（{LOT_SIZE} 股 × {price:,.2f}）'
        return {**base, 'status': 'NO_ACTION', 'detail': detail}

    side = 'BUY' if diff > 0 else 'SELL'
    qty = abs(diff)
    notional = qty * price
    bps = tx_fee_bps + slippage_bps + (tx_tax_bps if side == 'SELL' else 0.0)
    order = {
        'side': side,
        'qty': qty,
        'lots': qty // LOT_SIZE,
        'type': 'MARKET_ON_OPEN',
        'est_price': price,
        'est_notional': notional,
        'est_cost': notional * bps / 10000,
    }
    if target < 0 or current < 0:
        order['note'] = '涉及空方部位（融券），實務需另確認券源與保證金'
    return {**base, 'status': 'ORDERS', 'detail': f'{side} {qty // LOT_SIZE} 張', 'orders': [order]}
