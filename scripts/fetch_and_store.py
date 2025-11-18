#!/usr/bin/env python3
"""批次抓取指定股票代號區間資料並存入 data/ 目錄。

功能:
  - 支援來源: yfinance (預設), twse (含 fallback)
  - 參數: --symbol 2330 --start 2024-01-01 --end 2024-06-30 --source yf --format parquet
  - 產出: data/clean/{symbol}_{start}_{end}.parquet (或 csv)
  - 額外建立: data/raw/ 不動 (沿用 twse 快取)，此腳本聚焦乾淨輸出
  - 可加 --force 重新抓取 (忽略既有輸出)

使用範例:
  python scripts/fetch_and_store.py --symbol 2330 --start 2024-05-01 --end 2024-05-20
  python scripts/fetch_and_store.py --symbol 2330 --source twse --format csv
"""
from __future__ import annotations
import argparse, sys, pathlib
from datetime import datetime, timedelta
import pandas as pd

# 確保可匯入 src
_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.app.data.fetch import fetch_ohlcv_yf
from src.app.data.twse import fetch_twse_range_cached


def fetch(symbol: str, start: str, end: str, source: str, refresh: bool=False) -> pd.DataFrame:
    if source == 'twse':
        core = symbol.replace('.TW','')
        df = fetch_twse_range_cached(core, start, end, refresh=refresh)
    else:
        yf_symbol = symbol if symbol.endswith('.TW') else f"{symbol}.TW"
        # 包含 end 當日
        end_inc = (pd.to_datetime(end) + timedelta(days=1)).strftime('%Y-%m-%d')
        df = fetch_ohlcv_yf(yf_symbol, start, end_inc)
    return df


def main():
    p = argparse.ArgumentParser(description='抓取股票 OHLCV 並存檔')
    p.add_argument('--symbol', required=True, help='股票代碼 (例 2330 或 2330.TW)')
    p.add_argument('--start', default='2024-01-01')
    p.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'))
    p.add_argument('--source', default='yf', choices=['yf','twse'])
    p.add_argument('--format', default='parquet', choices=['parquet','csv'])
    p.add_argument('--force', action='store_true', help='若輸出已存在仍覆寫')
    p.add_argument('--refresh', action='store_true', help='對 twse 強制重新抓 (忽略快取)')
    args = p.parse_args()

    out_dir = pathlib.Path('data/clean')
    out_dir.mkdir(parents=True, exist_ok=True)

    symbol_clean = args.symbol.replace('.TW','')
    fname = f"{symbol_clean}_{args.start}_{args.end}.{args.format}"
    out_path = out_dir / fname
    if out_path.exists() and not args.force:
        print(f"[skip] Output exists: {out_path} (use --force 覆寫)")
        return

    df = fetch(args.symbol, args.start, args.end, args.source, refresh=args.refresh)
    if df.empty:
        print('[warn] 無資料，未產生輸出')
        return

    # 基本欄位檢查 & 清理
    cols = [c for c in ['open','high','low','close','volume'] if c in df.columns]
    df = df[cols].sort_index()

    if args.format == 'parquet':
        df.to_parquet(out_path)
    else:
        tmp = df.reset_index().rename(columns={'index':'date'})
        tmp.to_csv(out_path, index=False)

    print(f"[done] Saved {out_path} rows={len(df)} range={df.index.min().date()}->{df.index.max().date()}")

if __name__ == '__main__':
    main()
