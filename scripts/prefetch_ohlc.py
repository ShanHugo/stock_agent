#!/usr/bin/env python3
"""補下載上市＋上櫃每日開高低收（K 線型態研究需要最高價、最低價）。

只抓現有行情快取中「有開盤」的日子；證交所與櫃買各一條執行緒，同主機維持 3 秒間隔，全部約 2～3 小時。
已下載的日子會跳過，中斷後重跑會接著抓。

使用範例:
  python scripts/prefetch_ohlc.py                      # 2015-01-01 到昨天
  python scripts/prefetch_ohlc.py --start 2024-01-01
"""
from __future__ import annotations
import argparse, os, sys, pathlib, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from src.app.lab.market import MARKETS, CACHE, ohlc_day, quotes_day, weekdays

_lock = threading.Lock()


def log(msg: str) -> None:
    with _lock:
        print(msg, flush=True)


def run(market: str, start: date, end: date) -> int:
    days = [d for d in weekdays(start, end) if not quotes_day(market, d).empty]
    todo = [d for d in days if not (CACHE / market / 'ohlc' / f'{d:%Y%m%d}.json').exists()]
    log(f'[{market}] 交易日 {len(days)} 天，已有 {len(days) - len(todo)} 天，要下載 {len(todo)} 天')
    t0, failed = time.time(), 0
    for i, d in enumerate(todo, 1):
        try:
            if ohlc_day(market, d).empty:
                log(f'[{market}] {d} 沒有資料（不快取，之後會重試）')
        except Exception as e:      # noqa: BLE001 — 單日失敗不中斷，之後重跑會補
            failed += 1
            log(f'[{market}] {d} 失敗：{e}')
        if i % 50 == 0 or i == len(todo):
            left = (time.time() - t0) / i * (len(todo) - i)
            log(f'[{market}] {d} ({i}/{len(todo)}) 約剩 {left / 60:.0f} 分鐘')
    return failed


def main() -> int:
    p = argparse.ArgumentParser(description='補下載每日開高低收')
    p.add_argument('--start', default='2015-01-01')
    args = p.parse_args()
    start, end = date.fromisoformat(args.start), date.today() - timedelta(days=1)
    with ThreadPoolExecutor(len(MARKETS)) as ex:
        failed = sum(f.result() for f in [ex.submit(run, m, start, end) for m in MARKETS])
    log(f'[done] 完成，失敗 {failed} 天' + ('（重跑一次會補抓）' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
