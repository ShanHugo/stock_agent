"""提醒訊息格式化與 Discord webhook 推送。"""
from __future__ import annotations
import requests

from .rules import STOP_LOSS, TRAILING_STOP, TAKE_PROFIT, NEAR_STOP, NEAR_TARGET, PLAN_INVALID

DISCORD_LIMIT = 2000
NL = chr(10)
DISCLAIMER = '_依你設定的規則自動產生，非投資建議；是否下單請自行決定。_'

TITLES = {
    STOP_LOSS: '🔴 停損觸發',
    TRAILING_STOP: '🔴 移動停損觸發',
    TAKE_PROFIT: '🟢 停利觸發',
    NEAR_STOP: '🟡 接近停損',
    NEAR_TARGET: '🟡 接近停利',
    PLAN_INVALID: '🔴 買進理由失效',
}


def _p(v: float) -> str:
    return f'{v:,.2f}'.rstrip('0').rstrip('.')


def _money(v: float, currency: str, signed: bool = False) -> str:
    if currency == 'USD':
        return f'{v:+,.2f} USD' if signed else f'{v:,.2f} USD'
    return f'{v:+,.0f}' if signed else f'{v:,.0f}'


def _qty(a: dict, act: dict) -> str:
    if a['is_us']:
        return f"{act['qty']:g} 股"
    s = f"{act['qty']:,.0f} 股"
    if act['lots']:
        s += f"（{act['lots']} 張" + (f" + {act['odd_shares']} 股零股" if act['odd_shares'] else '') + '）'
    else:
        s += '（零股）'
    return s


def format_alert(a: dict, streak: int = 1) -> str:
    cur = a['currency']
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
    if a['kind'] == PLAN_INVALID:
        lines.append('失效條件：' + '；'.join(a['invalid_reasons']))
        if a.get('thesis'):
            lines.append(f"當初理由：{a['thesis']}")
    act = a.get('action')
    if act:
        lines.append(f'規則動作：**賣出 {_qty(a, act)}**')
        lines.append(f"預估成交 {_money(act['est_notional'], cur)}，手續費+稅約 {_money(act['est_fees'], cur)}，"
                     f"損益約 {_money(act['est_pnl'], cur, signed=True)}")
    return '\n'.join(lines)


def format_drawdown(d: dict) -> str:
    return '\n'.join([
        f"**📉 從高點回落 {d['level']:.0%}｜{d['label']}**",
        f"收盤 {_p(d['close'])}（{d['date']}），近 {d['window']} 個交易日高點 {_p(d['peak'])}（{d['peak_date']}），"
        f"目前回落 {d['drawdown']:.1%}",
        '（僅告知狀況，無對應買賣規則）',
    ])


def format_allocation(al: dict, fx: float | None) -> str:
    lines = ['**⚖️ 配置偏離**']
    for g, w in al['weights'].items():
        lines.append(f"{g}：{w:.1%}（目標 {al['targets'][g]:.0%}，偏離 {al['drift'][g]:+.1%}），"
                     f"市值 {al['values_twd'][g]:,.0f}")
    lines.append(f"總市值 {al['total_twd']:,.0f} 台幣" + (f"（美元匯率 {fx:.3f}）" if fx else '')
                 + f"，門檻 ±{al['threshold']:.0%}")
    moves = [f"{g} {'增加' if v > 0 else '減少'} {abs(v):,.0f}" for g, v in al['adjust_twd'].items() if abs(v) >= 1]
    if moves:
        lines.append('回到目標比例需要：' + '、'.join(moves) + ' 台幣')
    return '\n'.join(lines)


def format_risk(r: dict) -> str:
    lines = ['**🧯 部位風險超限**']
    if r['total_risk_exceeded']:
        lines.append(f"所有持股跌到停損時合計虧損約 {r['open_risk']:,.0f}，占資金 {r['open_risk_pct']:.1%}"
                     f"（上限 {r['max_total_risk']:.0%}）")
    for p in r['oversized']:
        lines.append(f"{p['label']} 市值 {p['value']:,.0f}，占資金 {p['pct']:.1%}（上限 {r['max_position_pct']:.0%}）")
    return '\n'.join(lines)


def format_reviews(reviews: list[dict]) -> str:
    lines = ['**📝 請重新確認買進理由**']
    for r in reviews:
        lines.append(f"- {r['label']}（{r['days']} 天未確認）：{r['thesis']}")
    lines.append('理由仍成立請執行 `python scripts/plan_trade.py review 代號`；不成立請依計畫處理。')
    return '\n'.join(lines)


def format_revenues(revs: list[dict]) -> str:
    lines = ['**📊 月營收公布**']
    for r in revs:
        tag = '持有' if r['held'] else '觀察'
        lines.append(f"- {r['code']} {r['name']}（{tag}）{r['month']}：{r['revenue_yi']:,.1f} 億，"
                     f"月增 {r['mom_pct']:+.1f}%、年增 {r['yoy_pct']:+.1f}%、累計年增 {r['cum_yoy_pct']:+.1f}%"
                     + (f"｜{r['note']}" if r['note'] else ''))
    lines.append('完整數據：`python scripts/research_card.py 代號`')
    return '\n'.join(lines)


def build_message(alerts: list[dict], streaks: dict, errors: list[str], date: str,
                  narration: str | None = None, drawdowns: list[dict] | None = None,
                  allocation: dict | None = None, fx: float | None = None,
                  risk: dict | None = None, reviews: list[dict] | None = None,
                  revenues: list[dict] | None = None) -> str | None:
    """沒有任何提醒或錯誤時回傳 None（不推送）。"""
    drawdowns, reviews, revenues = drawdowns or [], reviews or [], revenues or []
    if not any([alerts, errors, drawdowns, allocation, risk, reviews, revenues]):
        return None
    parts = [f'## 持股提醒 {date}']
    if narration:
        parts.append(f'> {narration}')
    parts += [format_alert(a, streaks.get(a['symbol'], 1)) for a in alerts]
    parts += [format_drawdown(d) for d in drawdowns]
    if allocation:
        parts.append(format_allocation(allocation, fx))
    if risk:
        parts.append(format_risk(risk))
    if reviews:
        parts.append(format_reviews(reviews))
    if revenues:
        parts.append(format_revenues(revenues))
    if errors:
        parts.append('**⚠️ 無法判斷**\n' + '\n'.join(f'- {e}' for e in errors))
    parts.append(DISCLAIMER)
    return '\n\n'.join(parts)


def format_status(evaluations: list[dict], date: str) -> str:
    """測試推送用：沒有提醒時列出每檔目前狀態。"""
    lines = [f'## 測試推送 {date}', '目前沒有觸發任何提醒，以下是各持股狀態：']
    for ev in evaluations:
        stop = ''
        if ev.get('stop_level'):
            stop = f"，停損價 {_p(ev['stop_level'])}（再跌 {1 - ev['stop_level'] / ev['close']:.1%} 會觸及）"
        lines.append(f"- {ev['label']}：收盤 {_p(ev['close'])}（{ev['date']}），未實現 {ev['unrealized_pct']:+.1%}{stop}")
    lines.append('_收到這則訊息代表 Discord 推送設定正常。_')
    return NL.join(lines)


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
