#!/usr/bin/env python3
"""記錄一筆買賣，自動更新 portfolio.json 的股數與成交均價。

使用範例:
  python scripts/record_trade.py buy 0050 12 108.5            # 定期定額買進 12 股
  python scripts/record_trade.py buy VOO 0.0712 701.3          # 美股碎股（美元）
  python scripts/record_trade.py buy 006208 100 120 --name 富邦台50   # 新的持股
  python scripts/record_trade.py sell 00881 100 50.2
交易紀錄另存於 state/trades.jsonl。
"""
from __future__ import annotations
import argparse, sys, pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.app.alerts.trades import record_trade


def main() -> int:
    p = argparse.ArgumentParser(description='記錄買賣並更新持股')
    p.add_argument('side', choices=['buy', 'sell'])
    p.add_argument('symbol')
    p.add_argument('qty', type=float, help='股數（可為零股/碎股）')
    p.add_argument('price', type=float, help='成交價（美股為美元）')
    p.add_argument('--name', help='新持股的顯示名稱')
    p.add_argument('--date', help='成交日 YYYY-MM-DD，預設今天')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    args = p.parse_args()
    try:
        msg = record_trade(pathlib.Path(args.portfolio), _ROOT / 'state' / 'trades.jsonl', args.side,
                           args.symbol, args.qty, args.price, name=args.name, trade_date=args.date)
    except ValueError as e:
        print(f'[error] {e}')
        return 1
    print(f'[ok] {msg}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
