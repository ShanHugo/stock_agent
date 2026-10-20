"""每日例行：抓取資料 -> Data QA -> PM/Risk -> Execution（模擬）-> 稽核紀錄 + 圖表
若本地有 sample_data.csv 亦可改為讀檔。加 --narrate 會請 Foundry 代理對每個階段寫講評。
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
    from src.app.pipeline import run_pipeline
    from src.app.visual.report import plot_equity
    from src.app.visual.interactive_report import build_interactive_report
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

def main(symbol: str | None = None, start: str | None = None, end: str | None = None, source: str = 'yf', ignore_local: bool = False, lookback: int = 20,
         strategy: str = 'momentum', allow_short: bool = False, capital: float = 1_000_000, narrate: bool = False):
    symbol = symbol or '2330'
    start = start or '2024-01-01'
    end = end or datetime.now().strftime('%Y-%m-%d')
    df = load_local_or_fetch(symbol, start, end, source=source, ignore_local=ignore_local)
    used_local = not ignore_local and Path('sample_data.csv').exists()
    if not used_local:
        df = validate_date_range(df, symbol, start, end, source)

    narrator = None
    if narrate:
        try:
            from src.app.agents.narrator import FoundryNarrator
            narrator = FoundryNarrator.from_settings()
        except Exception as e:
            print(f"[warn] 無法連線 Foundry 代理，略過講評：{e}")

    result = run_pipeline(df, symbol, strategy=strategy, lookback=lookback, long_only=not allow_short,
                          capital=capital, expected_end=None if used_local else end, narrator=narrator)
    s = result.summary
    qa = s['stages']['data_qa']
    print('=== Daily Pipeline ===')
    print('Symbol:', symbol, '| Status:', s['status'])
    print(f"[Data QA] {qa['status']} rows={qa['rows']} period={qa['period']}")
    for c in qa['checks']:
        if c['status'] != 'PASS':
            print(f"  - {c['status']} {c['name']}: {c['detail']}")
    if 'pm_risk' in s['stages']:
        r = s['stages']['pm_risk']
        print(f"[PM/Risk] {r['strategy']} lookback={r['lookback']} weight {r['current_weight']:+.0f} -> {r['target_weight']:+.0f}")
        print('  Metrics:', r['metrics'])
        print('  Kill-Switch:', 'HALT ' + '; '.join(r['kill_switch']['reasons']) if r['kill_switch']['halt'] else 'OK')
    if 'execution' in s['stages']:
        e = s['stages']['execution']
        print(f"[Execution] {e['status']}: {e['detail']}")
        for o in e['orders']:
            print(f"  - {o['side']} {o['qty']} 股 @~{o['est_price']:,.2f} 預估成本 {o['est_cost']:,.0f}")
    for stage, text in result.narrations.items():
        print(f"\n--- {stage} 代理講評 ---\n{text}")
    if result.backtest is not None:
        out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
        print('Chart:', plot_equity(result.backtest, out_dir))
        print('Interactive:', build_interactive_report(result.backtest, out_dir))
    print('Audit:', result.audit_path)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Daily pipeline run')
    parser.add_argument('--symbol', default='2330', help='股票代碼 (不加 .TW 也可)')
    parser.add_argument('--start', default=None, help='開始日期 YYYY-MM-DD')
    parser.add_argument('--end', default=None, help='結束日期 YYYY-MM-DD')
    parser.add_argument('--source', default='yf', choices=['yf','twse'], help='資料來源: yf 或 twse')
    parser.add_argument('--lookback', type=int, default=20, help='策略 lookback')
    parser.add_argument('--ignore-local', action='store_true', help='忽略本地 sample_data.csv 強制重新抓取')
    parser.add_argument('--strategy', default='momentum', choices=['momentum', 'mean_reversion'], help='策略')
    parser.add_argument('--allow-short', action='store_true', help='允許空方部位（預設只做多）')
    parser.add_argument('--capital', type=float, default=1_000_000, help='模擬帳戶資金（台幣）')
    parser.add_argument('--narrate', action='store_true', help='請 Foundry 代理對每個階段寫講評')
    args = parser.parse_args()
    main(symbol=args.symbol, start=args.start, end=args.end, source=args.source, ignore_local=args.ignore_local, lookback=args.lookback,
         strategy=args.strategy, allow_short=args.allow_short, capital=args.capital, narrate=args.narrate)
