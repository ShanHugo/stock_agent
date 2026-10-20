#!/usr/bin/env python3
"""每日收盤後檢查持股的停損/停利，有狀況就推送到 Discord。

使用範例:
  python scripts/check_alerts.py --dry-run          # 只印出，不推送
  python scripts/check_alerts.py                    # 推送（需 .env 的 DISCORD_WEBHOOK_URL）
  python scripts/check_alerts.py --no-narrate       # 不用 Ollama 講評
  python scripts/check_alerts.py --force --dry-run  # 休市日也照樣判斷（測試用）
"""
from __future__ import annotations
import argparse, os, sys, pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)  # 排程執行時工作目錄可能不同；快取與 state 以專案根目錄為準

from src.app.config.settings import settings
from src.app.data.fetch import fetch_ohlcv_yf
from src.app.alerts import load_portfolio, run_alerts
from src.app.alerts.narrate import OllamaNarrator
from src.app.alerts.notify import send_discord
from src.app.research.sources import revenue_history


def main() -> int:
    p = argparse.ArgumentParser(description='持股停損/停利提醒')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    p.add_argument('--dry-run', action='store_true', help='只印出訊息，不推送')
    p.add_argument('--force', action='store_true', help='即使今天沒有收盤資料也判斷並推送')
    p.add_argument('--no-narrate', action='store_true', help='不使用 Ollama 講評')
    args = p.parse_args()

    if not pathlib.Path(args.portfolio).exists():
        print(f'[error] 找不到 {args.portfolio}，請複製 portfolio.example.json 為 portfolio.json 並填入持股')
        return 2
    portfolio = load_portfolio(args.portfolio)
    narrator = None if args.no_narrate else OllamaNarrator(
        model=os.getenv('OLLAMA_MODEL', 'qwen3.5:9b'), url=os.getenv('OLLAMA_URL', 'http://localhost:11434'))

    now = datetime.now(ZoneInfo('Asia/Taipei'))
    res = run_alerts(portfolio, fetch_ohlcv_yf, today=now.date(), now=now,
                     default_tw_fee_bps=settings.tx_fee_bps,
                     state_path=_ROOT / 'state' / 'alerts_state.json',
                     narrator=narrator, force=args.force,
                     revenue_lookup=lambda code: revenue_history(code, 13, today=now.date()))

    for ev in res['evaluations']:
        print(f"[check] {ev['label']:<14} {ev['date']} close={ev['close']:<9g} {ev['kind']}")
    if res.get('allocation'):
        al = res['allocation']
        print('[alloc] ' + ', '.join(f"{g} {w:.1%}" for g, w in al['weights'].items())
              + f"  out_of_band={al['out_of_band']}")
    for e in res['errors']:
        print(f'[error] {e}')
    if res['narration_error']:
        print(f"[warn] {res['narration_error']}")
    if res['skipped']:
        print(f"[skip] {res['skipped']}")
        return 0
    if not res['message']:
        print('[ok] 沒有需要提醒的持股')
        return 0

    print('\n' + res['message'] + '\n')
    if args.dry_run:
        print('[dry-run] 未推送')
        return 0
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print('[error] .env 未設定 DISCORD_WEBHOOK_URL，未推送')
        return 1
    send_discord(webhook, res['message'])
    print('[sent] 已推送到 Discord')
    return 0


if __name__ == '__main__':
    sys.exit(main())
