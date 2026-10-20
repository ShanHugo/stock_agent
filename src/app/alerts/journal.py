"""交易日誌檢討：從 state/journal.jsonl 統計勝率、賺賠比、R 倍數與是否照計畫出場。"""
from __future__ import annotations
import json
from pathlib import Path


def load_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def summarize(entries: list[dict], month: str | None = None) -> dict:
    """month 例 '2026-10'；None = 全部。只統計附帶計畫的個股交易，ETF 定期定額不列入。"""
    rows = [e for e in entries if e.get('had_plan') and (not month or e['date'].startswith(month))]
    n = len(rows)
    if not n:
        return {'month': month, 'trades': 0}
    wins = [e for e in rows if e['pnl'] > 0]
    losses = [e for e in rows if e['pnl'] <= 0]
    rs = [e['r_multiple'] for e in rows if e.get('r_multiple') is not None]
    avg_win = sum(e['pnl'] for e in wins) / len(wins) if wins else 0.0
    avg_loss = sum(e['pnl'] for e in losses) / len(losses) if losses else 0.0
    return {
        'month': month,
        'trades': n,
        'win_rate': len(wins) / n,
        'total_pnl': sum(e['pnl'] for e in rows),
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'payoff_ratio': (avg_win / abs(avg_loss)) if wins and losses and avg_loss else None,
        'avg_r': sum(rs) / len(rs) if rs else None,
        'worst_r': min(rs) if rs else None,
        'exits': {k: sum(1 for e in rows if e['exit_type'] == k) for k in ('stop', 'target', 'discretionary')},
        'late_stops': [e['symbol'] for e in rows if e.get('late_stop')],
        # 虧損超過 1R = 跌破停損還沒賣
        'beyond_1r': [e['symbol'] for e in rows if e.get('r_multiple') is not None and e['r_multiple'] < -1.05],
    }


def format_summary(s: dict) -> str:
    title = f"## 交易檢討 {s['month'] or '全部'}"
    if not s['trades']:
        return f'{title}\n沒有附帶計畫的個股賣出紀錄。'
    lines = [title,
             f"交易 {s['trades']} 筆，勝率 {s['win_rate']:.0%}，合計損益 {s['total_pnl']:+,.0f}",
             f"平均獲利 {s['avg_win']:+,.0f}／平均虧損 {s['avg_loss']:+,.0f}"
             + (f"，賺賠比 {s['payoff_ratio']:.2f}" if s['payoff_ratio'] else '')]
    if s['avg_r'] is not None:
        lines.append(f"平均 {s['avg_r']:+.2f}R，最差 {s['worst_r']:+.2f}R")
    ex = s['exits']
    lines.append(f"出場方式：停損 {ex['stop']}、停利 {ex['target']}、計畫外 {ex['discretionary']}")
    if s['late_stops']:
        lines.append('⚠️ 停損執行偏慢（成交價明顯低於停損價）：' + '、'.join(s['late_stops']))
    if s['beyond_1r']:
        lines.append('⚠️ 虧損超過 1R（超出原本可承受範圍）：' + '、'.join(s['beyond_1r']))
    return '\n'.join(lines)
