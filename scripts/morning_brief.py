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

from src.app.config.settings import settings  # noqa: F401  載入 .env
from src.app.data.fetch import fetch_ohlcv_tw
from src.app.alerts import load_portfolio
from src.app.alerts.narrate import OllamaNarrator
from src.app.alerts.notify import send_discord
from src.app.briefing.compose import build_briefing, format_briefing
from src.app.briefing import sector_news

BRIEF_PROMPT = (
    "以下是台股盤前簡報，所有數字都已由程式算好。\n"
    "請用繁體中文寫 1–2 句，總結今天開盤前最值得注意的市場資訊。\n"
    "規則：只能原樣引用下面出現過的數字，不要加總、換算或推測任何數字；不要給買賣建議，也不要預測漲跌；"
    "新聞只能轉述標題與摘要內容，不要推測；不要加標題；"
    "日期照抄（例如 10/29），不要寫「明日」「明天」「下週」等相對日期；ADR 溢價是價差，不是漲跌幅。\n\n{text}"
)
# 模型常把 6 天後的事件寫成「明日」（2026-10-23 實例），日期一律照抄
RELATIVE_DAYS = ('明日', '明天', '今晚', '今日稍晚', '下週', '下周', '後天')


def relative_day_words(narration: str) -> list[str]:
    return [w for w in RELATIVE_DAYS if w in narration]


def main() -> int:
    p = argparse.ArgumentParser(description='盤前簡報')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    p.add_argument('--dry-run', action='store_true', help='只印出，不推送')
    p.add_argument('--force', action='store_true', help='週末也執行')
    p.add_argument('--no-narrate', action='store_true', help='不使用 Ollama 摘要')
    p.add_argument('--no-news', action='store_true', help='不抓新聞標題')
    args = p.parse_args()

    now = datetime.now(ZoneInfo('Asia/Taipei'))
    if now.weekday() >= 5 and not args.force:
        print('[skip] 週末不發盤前簡報')
        return 0
    portfolio = load_portfolio(args.portfolio)
    b = build_briefing(portfolio, now=now, price_fetcher=fetch_ohlcv_tw,
                       include_news=not args.no_news)
    narrator = None if args.no_narrate else OllamaNarrator(
        model=os.getenv('OLLAMA_MODEL', 'qwen3.5:9b'), url=os.getenv('OLLAMA_URL', 'http://localhost:11434'))
    if narrator:
        warnings: list[str] = []
        sector_news.add_summaries(b.get('sector_news'), narrator.chat, warnings)
        for w in warnings:
            print(f'[warn] 族群新聞 {w}')
    text = format_briefing(b)
    if narrator:
        narration, err = narrator.summarize(text, has_triggered=False, prompt=BRIEF_PROMPT)
        if err:
            print(f'[warn] {err}')
        if narration and relative_day_words(narration):
            print(f'[warn] 講評用了相對日期 {relative_day_words(narration)}，已捨棄：{narration}')
            narration = None
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
