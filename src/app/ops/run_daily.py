"""每日例行：抓取資料 -> 產生部位 -> 回測 -> 報表與圖表
若本地有 sample_data.csv 亦可改為讀檔。
"""
from pathlib import Path
import sys, pathlib, argparse
import pandas as pd
from datetime import datetime

# 動態確保專案根目錄在 sys.path（允許以 `python src/app/ops/run_daily.py` 直接執行）
_THIS_FILE = pathlib.Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parents[3]  # project root containing 'src'
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from src.app.data.fetch import fetch_ohlcv_yf
    from src.app.data.twse import fetch_twse_range_cached
    from src.app.backtest.engine import backtest_engine
    from src.app.strategies.base import MomentumStrategy
    from src.app.performance.metrics import basic_report
    from src.app.visual.report import plot_equity
    from src.app.visual.interactive_report import build_interactive_report
    from src.app.config.settings import settings
except ImportError as e:  # 最後退回相對匯入（理論上不會再需要）
    raise RuntimeError(f"匯入模組失敗，請確認目錄結構與 __init__.py：{e}")


def _fetch_from_source(symbol: str, start: str, end: str, source: str, refresh: bool = False) -> pd.DataFrame:
    """根據 source 從遠端抓資料；twse 支援 refresh。"""
    if source == 'twse':
        core_sym = symbol.replace('.TW', '')
        return fetch_twse_range_cached(core_sym, start, end, refresh=refresh)
    # yfinance
    yf_symbol = symbol if symbol.endswith('.TW') else f"{symbol}.TW"
    return fetch_ohlcv_yf(yf_symbol, start, end)


def load_local_or_fetch(symbol: str, start: str, end: str, source: str = 'yf', ignore_local: bool = False) -> pd.DataFrame:
    """優先讀取 sample_data.csv (除非 ignore_local)，否則從指定來源抓取。"""
    csv_path = Path('sample_data.csv')
    if not ignore_local and csv_path.exists():
        from src.app.backtest.data import load_ohlcv_csv  # 延遲匯入避免循環
        print(f"[info] 使用本地檔案 {csv_path} (可用 --ignore-local 跳過)")
        return load_ohlcv_csv(str(csv_path))
    print(f"[info] 從來源抓取 source={source} symbol={symbol} range={start}->{end}")
    return _fetch_from_source(symbol, start, end, source, refresh=False)


def validate_date_range(df: pd.DataFrame, symbol: str, start: str, end: str, source: str) -> pd.DataFrame:
    """驗證資料是否涵蓋要求日期；若不涵蓋則強制重抓（twse refresh=True）。

    規則：若 df 空、或最小日期 > start、或最大日期 < end，則重抓。
    容忍週末/假日：若最大日期距離 end <= 2 天則視為可接受。
    """
    if df.empty:
        print(f"[warn] 初次資料為空，改為強制重抓 source={source}")
        return _fetch_from_source(symbol, start, end, source, refresh=True)
    start_req = pd.to_datetime(start)
    end_req = pd.to_datetime(end)
    data_min = pd.to_datetime(df.index.min())
    data_max = pd.to_datetime(df.index.max())
    need = False
    reasons = []
    if data_min > start_req:
        need = True; reasons.append(f"data_min {data_min.date()} > requested_start {start_req.date()}")
    # 容忍結束差 2 天（週末/假日）
    if data_max < end_req and (end_req - data_max).days > 2:
        need = True; reasons.append(f"data_max {data_max.date()} < requested_end {end_req.date()} (gap={(end_req-data_max).days}d)")
    if need:
        print(f"[warn] 資料日期區間不足: {', '.join(reasons)} -> 重新抓取 (refresh)")
        refreshed = _fetch_from_source(symbol, start, end, source, refresh=True)
        if refreshed.empty:
            print("[error] 重抓後仍無資料，保留原資料")
            return df
        return refreshed
    return df

def main(symbol: str | None = None, start: str | None = None, end: str | None = None, source: str = 'yf', ignore_local: bool = False, lookback: int = 20):
    symbol = symbol or '2330'
    start = start or '2024-01-01'
    end = end or datetime.now().strftime('%Y-%m-%d')
    df = load_local_or_fetch(symbol, start, end, source=source, ignore_local=ignore_local)
    df = validate_date_range(df, symbol, start, end, source)
    # Debug: 檢視抓回資料
    print(f"[debug] fetched df shape={df.shape} cols={list(df.columns)} head=\n{df.head()}\n...")
    # 動態調整 lookback：若資料長度不足則縮短避免全 0 部位
    eff_lookback = min(lookback, max(1, len(df)//3)) if len(df) < lookback + 2 else lookback
    if eff_lookback != lookback:
        print(f"[warn] 資料筆數 {len(df)} 不足原 lookback={lookback}，調整為 {eff_lookback}")
    strat = MomentumStrategy(lookback=eff_lookback)
    positions = strat.generate_positions(df)
    bt = backtest_engine(df, positions, settings.tx_fee_bps, settings.tx_tax_bps, settings.slippage_bps)
    rpt = basic_report(bt)
    out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
    chart_path = plot_equity(bt, out_dir)
    interactive_path = build_interactive_report(bt, out_dir)
    print('=== Daily Run Report ===')
    print('Symbol:', symbol)
    print('Period:', df.index.min().date(), '->', df.index.max().date())
    print('Metrics:', rpt)
    print('Chart:', chart_path)
    print('Interactive:', interactive_path)
    print(bt.tail())

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Daily pipeline run')
    parser.add_argument('--symbol', default='2330', help='股票代碼 (不加 .TW 也可)')
    parser.add_argument('--start', default=None, help='開始日期 YYYY-MM-DD')
    parser.add_argument('--end', default=None, help='結束日期 YYYY-MM-DD')
    parser.add_argument('--source', default='yf', choices=['yf','twse'], help='資料來源: yf 或 twse')
    parser.add_argument('--lookback', type=int, default=20, help='策略 lookback')
    parser.add_argument('--ignore-local', action='store_true', help='忽略本地 sample_data.csv 強制重新抓取')
    args = parser.parse_args()
    main(symbol=args.symbol, start=args.start, end=args.end, source=args.source, ignore_local=args.ignore_local, lookback=args.lookback)
