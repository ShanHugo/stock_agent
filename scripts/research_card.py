#!/usr/bin/env python3
"""個股研究卡：月營收、季度獲利、估值位置、法人與融資、價格位置。目前支援上市公司。

使用範例:
  python scripts/research_card.py 2330
  python scripts/research_card.py 2330 2317 2454
  python scripts/research_card.py --watchlist          # portfolio.json 的 watchlist + 持有的個股
  python scripts/research_card.py 2330 --json          # 輸出 JSON
  python scripts/research_card.py 2330 --send          # 推送到 Discord
第一次查詢會回補歷史資料（每次請求間隔約 3 秒，約需數分鐘），之後讀快取。
"""
from __future__ import annotations
import argparse, json, os, sys, pathlib
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)  # 快取路徑以專案根目錄為準

from src.app.config.settings import settings  # noqa: F401  載入 .env
from src.app.data.fetch import fetch_ohlcv_yf
from src.app.research.card import build_card
from src.app.research.format import format_card
from src.app.alerts.notify import send_discord


def watchlist_codes(path: pathlib.Path) -> list[str]:
    from src.app.alerts.portfolio import load_portfolio
    raw = json.loads(path.read_text(encoding='utf-8'))
    pf = load_portfolio(path)
    held = [h.symbol for h in pf.holdings if h.market == 'TW' and not h.is_etf]
    return list(dict.fromkeys([str(s) for s in raw.get('watchlist', [])] + held))


def main() -> int:
    p = argparse.ArgumentParser(description='個股研究卡')
    p.add_argument('codes', nargs='*', help='上市股票代號')
    p.add_argument('--watchlist', action='store_true', help='使用 portfolio.json 的 watchlist 與持有個股')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    p.add_argument('--json', action='store_true', help='輸出 JSON')
    p.add_argument('--send', action='store_true', help='推送到 Discord')
    p.add_argument('--valuation-months', type=int, default=36, help='估值分位的回看月數')
    args = p.parse_args()

    codes = list(args.codes)
    if args.watchlist:
        codes += watchlist_codes(pathlib.Path(args.portfolio))
    codes = list(dict.fromkeys(codes))
    if not codes:
        p.error('請給股票代號，或在 portfolio.json 設定 "watchlist" 後使用 --watchlist')

    texts = []
    for code in codes:
        card = build_card(code, today=date.today(), price_fetcher=fetch_ohlcv_yf,
                          valuation_months=args.valuation_months,
                          progress=lambda m: print(f'[fetch] {m}', file=sys.stderr))
        if args.json:
            print(json.dumps(card, ensure_ascii=False, indent=2, default=str))
        else:
            text = format_card(card)
            texts.append(text)
            print(text + '\n')
    if args.send and texts:
        webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
        if not webhook:
            print('[error] .env 未設定 DISCORD_WEBHOOK_URL，未推送')
            return 1
        for t in texts:
            send_discord(webhook, t)
        print('[sent] 已推送到 Discord')
    return 0


if __name__ == '__main__':
    sys.exit(main())
