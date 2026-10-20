#!/usr/bin/env python3
"""記錄一筆買賣，自動更新 portfolio.json 的股數與成交均價。

使用範例:
  python scripts/record_trade.py buy 0050 12 108.5            # 定期定額買進 12 股
  python scripts/record_trade.py buy VOO 0.0712 701.3          # 美股碎股（美元；VOO 已在 portfolio.json 標 etf）
  python scripts/record_trade.py buy QQQM 0.5 230 --etf        # 新的美股 ETF
  python scripts/record_trade.py buy 006208 100 120 --name 富邦台50   # 新的持股
  python scripts/record_trade.py buy 2330 50 2390               # 個股：自動套用 plan_trade 建立的計畫
  python scripts/record_trade.py sell 00881 100 50.2
個股（非 ETF）買進需先用 plan_trade.py 建立計畫。交易紀錄存於 state/trades.jsonl，
賣出另寫入 state/journal.jsonl 供 review_trades.py 檢討。
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
    p.add_argument('--plan', help='指定計畫 ID（同一檔有多份計畫時）')
    p.add_argument('--no-plan', action='store_true', help='個股買進不套用計畫（不建議）')
    p.add_argument('--etf', action='store_true', help='新的美股持股是 ETF（定期定額不需計畫）')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    args = p.parse_args()
    try:
        res = record_trade(pathlib.Path(args.portfolio), _ROOT / 'state', args.side,
                           args.symbol, args.qty, args.price, name=args.name, trade_date=args.date,
                           plan_id=args.plan, no_plan=args.no_plan, etf=True if args.etf else None)
    except ValueError as e:
        print(f'[error] {e}')
        return 1
    print(f"[ok] {res['message']}")
    for w in res['warnings']:
        print(f'[warn] {w}')
    j = res['journal']
    if j and j['had_plan']:
        r = f"，{j['r_multiple']:+.2f}R" if j['r_multiple'] is not None else ''
        print(f"[journal] 損益 {j['pnl']:+,.0f}（{j['return_pct']:+.1%}{r}），出場方式 {j['exit_type']}"
              + ('，⚠️ 停損執行偏慢' if j['late_stop'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
