#!/usr/bin/env python3
"""每日 08:30 盤前簡報：隔夜美股、台指期夜盤、行事曆、持股狀態與「依你規則」需注意的事項。

使用範例:
  python scripts/morning_brief.py --dry-run     # 只印出
  python scripts/morning_brief.py               # 推送到 Discord（需 .env 的 DISCORD_WEBHOOK_URL）
  python scripts/morning_brief.py --no-narrate  # 不用 Ollama 摘要
週末不推送（加 --force 可強制）。只整理資訊，不產生買賣建議。
"""
from __future__ import annotations
import argparse, os, sys, pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from src.app.config.settings import settings
from src.app.data.fetch import fetch_ohlcv_yf
from src.app.alerts import load_portfolio
from src.app.alerts.narrate import OllamaNarrator
from src.app.alerts.notify import send_discord
from src.app.briefing.compose import build_briefing, format_briefing, has_triggered


def main() -> int:
    p = argparse.ArgumentParser(description='盤前簡報')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    p.add_argument('--dry-run', action='store_true', help='只印出，不推送')
    p.add_argument('--force', action='store_true', help='週末也執行')
    p.add_argument('--no-narrate', action='store_true', help='不使用 Ollama 摘要')
    args = p.parse_args()

    now = datetime.now(ZoneInfo('Asia/Taipei'))
    if now.weekday() >= 5 and not args.force:
        print('[skip] 週末不發盤前簡報')
        return 0
    portfolio = load_portfolio(args.portfolio)
    b = build_briefing(portfolio, now=now, price_fetcher=fetch_ohlcv_yf, default_tw_fee_bps=settings.tx_fee_bps)
    text = format_briefing(b)
    if not args.no_narrate:
        narrator = OllamaNarrator(model=os.getenv('OLLAMA_MODEL', 'qwen3.5:9b'),
                                  url=os.getenv('OLLAMA_URL', 'http://localhost:11434'))
        narration, err = narrator.summarize(text, has_triggered=has_triggered(b))
        if err:
            print(f'[warn] {err}')
        if narration:
            text = format_briefing(b, narration=narration)
    print(text)
    if args.dry_run:
        print('\n[dry-run] 未推送')
        return 0
    webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        print('[error] .env 未設定 DISCORD_WEBHOOK_URL，未推送')
        return 1
    send_discord(webhook, text)
    print('\n[sent] 已推送到 Discord')
    return 0


if __name__ == '__main__':
    sys.exit(main())
