"""本地 FunctionTool 工具集合：提供研究/風控/資料/視覺化常用函式。
所有函式需保持 pure（副作用僅限檔案輸出），可被 Azure Agent Service 包裝。
"""
from __future__ import annotations
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

from ..data.fetch import fetch_ohlcv_yf
from ..data.twse import fetch_twse_range_cached
from ..backtest.engine import backtest_engine
from ..features.indicators import momentum_signal
from ..performance.metrics import basic_report
from ..visual.report import plot_equity
from ..visual.interactive_report import build_interactive_report


def ping() -> str:
    """健康檢查：回傳 'pong'。"""
    return "pong"


def mean(values: List[float]) -> float:
    arr = np.array(values, dtype=float)
    return float(arr.mean()) if arr.size else 0.0


def calc_sharpe(values: List[float]) -> float:
    arr = np.array(values, dtype=float)
    if arr.std() == 0:
        return 0.0
    return float(np.sqrt(252) * arr.mean() / arr.std())


def fetch_prices(symbol: str, start: str, end: str) -> Dict[str, Any]:
    df = fetch_ohlcv_yf(symbol, start, end)
    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "records": df.reset_index().to_dict(orient='records')
    }


def run_simple_backtest(records: List[Dict[str, Any]], lookback: int = 5) -> Dict[str, Any]:
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    # 簡易動能策略
    positions = momentum_signal(df['close'], lookback)
    bt = backtest_engine(df, positions)
    rpt = basic_report(bt)
    out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
    chart_path = plot_equity(bt, out_dir)
    return {
        "report": rpt,
        "chart": chart_path,
        "tail": bt.tail().reset_index().to_dict(orient='records')
    }


def generate_interactive_report(records: List[Dict[str, Any]], lookback: int = 5) -> Dict[str, Any]:
    """執行簡單回測並輸出互動報表，回傳報表路徑。"""
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    positions = momentum_signal(df['close'], lookback)
    bt = backtest_engine(df, positions)
    out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
    html_path = build_interactive_report(bt, out_dir)
    return {"interactive_report": html_path}


# 用於 registry 綁定的函式集合
user_functions = [
    ping,
    mean,
    calc_sharpe,
    fetch_prices,
    run_simple_backtest,
    # TWSE
]


def fetch_twse_price(symbol: str, start: str, end: str, refresh: bool = False):
    """抓取台灣證交所日線資料（有 parquet 快取）。"""
    df = fetch_twse_range_cached(symbol, start, end, refresh=refresh)
    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "records": df.reset_index().to_dict(orient='records')
    }

user_functions.append(fetch_twse_price)
user_functions.append(generate_interactive_report)

