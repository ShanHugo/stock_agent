"""提醒訊息格式化與 Discord webhook 推送。"""
from __future__ import annotations
import requests

from .rules import STOP_LOSS, TRAILING_STOP, TAKE_PROFIT, NEAR_STOP, NEAR_TARGET

DISCORD_LIMIT = 2000
DISCLAIMER = '_依你設定的規則自動產生，非投資建議；是否下單請自行決定。_'

TITLES = {
    STOP_LOSS: '🔴 停損觸發',
    TRAILING_STOP: '🔴 移動停損觸發',
    TAKE_PROFIT: '🟢 停利觸發',
    NEAR_STOP: '🟡 接近停損',
    NEAR_TARGET: '🟡 接近停利',
}


def _p(v: float) -> str:
    return f'{v:,.2f}'.rstrip('0').rstrip('.')


def format_alert(a: dict, streak: int = 1) -> str:
    lines = [f"**{TITLES[a['kind']]}｜{a['label']}**"
             + (f'（已連續 {streak} 個交易日）' if streak > 1 else '')]
    lines.append(f"收盤 {_p(a['close'])}（{a['date']}），成本 {_p(a['cost'])}，"
                 f"未實現 {a['unrealized_pct']:+.1%}")
    if a['kind'] in (STOP_LOSS, TRAILING_STOP, NEAR_STOP) and a['stop_level'] is not None:
        if a['stop_kind'] == TRAILING_STOP:
            lines.append(f"移動停損價 {_p(a['stop_level'])}（買進後最高收盤 {_p(a['peak_close'])}）")
        else:
            lines.append(f"停損價 {_p(a['stop_level'])}")
    if a['kind'] in (TAKE_PROFIT, NEAR_TARGET) and a['target_level'] is not None:
        lines.append(f"停利價 {_p(a['target_level'])}")
    act = a.get('action')
    if act:
        qty = f"{act['qty']:,} 股"
        if act['lots']:
            qty += f"（{act['lots']} 張" + (f" + {act['odd_shares']} 股零股" if act['odd_shares'] else '') + '）'
        lines.append(f'規則動作：**賣出 {qty}**')
        lines.append(f"預估成交 {act['est_notional']:,.0f}，手續費+證交稅約 {act['est_fees']:,.0f}，"
                     f"損益約 {act['est_pnl']:+,.0f}")
    return '\n'.join(lines)


def build_message(alerts: list[dict], streaks: dict, errors: list[str], date: str,
                  narration: str | None = None) -> str | None:
    """沒有任何提醒或錯誤時回傳 None（不推送）。"""
    if not alerts and not errors:
        return None
    parts = [f'## 持股提醒 {date}']
    if narration:
        parts.append(f'> {narration}')
    parts += [format_alert(a, streaks.get(a['symbol'], 1)) for a in alerts]
    if errors:
        parts.append('**⚠️ 無法判斷**\n' + '\n'.join(f'- {e}' for e in errors))
    parts.append(DISCLAIMER)
    return '\n\n'.join(parts)


def _chunks(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    out, cur = [], ''
    for block in text.split('\n\n'):
        while len(block) > limit:  # 單段過長時硬切
            out.append(block[:limit]); block = block[limit:]
        if len(cur) + len(block) + 2 > limit:
            out.append(cur); cur = block
        else:
            cur = f'{cur}\n\n{block}' if cur else block
    if cur:
        out.append(cur)
    return out


def send_discord(webhook_url: str, text: str, timeout: int = 10) -> None:
    for chunk in _chunks(text):
        r = requests.post(webhook_url, json={'content': chunk}, timeout=timeout)
        r.raise_for_status()
