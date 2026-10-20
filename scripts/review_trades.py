#!/usr/bin/env python3
"""交易檢討：統計附帶計畫的個股交易（勝率、賺賠比、R 倍數、是否照計畫出場）。

使用範例:
  python scripts/review_trades.py                  # 本月
  python scripts/review_trades.py --month 2026-09
  python scripts/review_trades.py --all
  python scripts/review_trades.py --send           # 推送到 Discord
"""
from __future__ import annotations
import argparse, os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.app.config.settings import settings  # noqa: F401  載入 .env
from src.app.alerts.journal import load_journal, summarize, format_summary
from src.app.alerts.notify import send_discord


def main() -> int:
    p = argparse.ArgumentParser(description='交易檢討')
    p.add_argument('--month', default=date.today().strftime('%Y-%m'), help='YYYY-MM，預設本月')
    p.add_argument('--all', action='store_true', help='統計全部紀錄')
    p.add_argument('--send', action='store_true', help='推送到 Discord')
    args = p.parse_args()
    text = format_summary(summarize(load_journal(_ROOT / 'state' / 'journal.jsonl'),
                                    None if args.all else args.month))
    print(text)
    if args.send:
        webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
        if not webhook:
            print('[error] .env 未設定 DISCORD_WEBHOOK_URL，未推送')
            return 1
        send_discord(webhook, text)
        print('[sent] 已推送到 Discord')
    return 0


if __name__ == '__main__':
    sys.exit(main())
