"""籌碼面「起漲／起跌」訊號（外資、投信買賣超、融資餘額）。定義與門檻在 2026-10-23 看到任何結果前訂定，之後不調整。

每個訊號在 d 日收盤後才知道（法人買賣超、融資餘額都在收盤後公布）；評估方式與 technical.evaluate 相同。
金額都換算成「相對 60 日均量」的比例，讓大小型股可以比較。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 代號 → (中文名稱, 類別, 定義, 預期方向)
SIGNALS = {
    'trust_start':   ('投信開始買', '投信', '今天買超，前 20 天都沒有買超', '起漲'),
    'trust_streak3': ('投信連 3 買', '投信', '連續 3 天買超（第 3 天算一次）', '起漲'),
    'trust_big':     ('投信大買', '投信', '單日買超 ≥ 60 日均量 10%', '起漲'),
    'trust_sell3':   ('投信連 3 賣', '投信', '連續 3 天賣超（第 3 天算一次）', '起跌'),
    'foreign_streak5': ('外資連 5 買', '外資', '連續 5 天買超（第 5 天算一次）', '起漲'),
    'foreign_turn':  ('外資轉賣', '外資', '連續 5 天以上買超後，今天轉賣超', '起跌'),
    'foreign_big_buy': ('外資大買', '外資', '單日買超 ≥ 60 日均量 20%', '起漲'),
    'foreign_big_sell': ('外資大賣', '外資', '單日賣超 ≥ 60 日均量 20%', '起跌'),
    'both_buy':      ('土洋同買', '土洋', '外資、投信同日買超，各 ≥ 均量 5%', '起漲'),
    'both_sell':     ('土洋同賣', '土洋', '外資、投信同日賣超，各 ≥ 均量 5%', '起跌'),
    'margin_chase':  ('融資追價', '融資', '5 天融資增加 ≥ 均量 50%，且 5 天漲 ≥ 5%', '起跌'),
    'margin_flush':  ('融資退場', '融資', '5 天融資減少 ≥ 均量 50%，且 5 天跌 ≥ 5%', '起漲'),
}


def _streak_np(p: pd.DataFrame) -> pd.DataFrame:
    """連續為 True 的天數（False 或缺值視為中斷）。"""
    a = p.fillna(False).astype(bool).to_numpy()
    out = np.zeros(a.shape, dtype=int)
    for i in range(a.shape[0]):
        out[i] = np.where(a[i], (out[i - 1] if i else 0) + 1, 0)
    return pd.DataFrame(out, index=p.index, columns=p.columns)


def signal_panels(px: pd.DataFrame, volume: pd.DataFrame, chips: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """px：還原收盤；volume：成交股數；chips：foreign / trust（股）、margin（融資餘額，張）。回傳 {代號: 布林面板}。
    法人資料缺值的日子（還沒有資料或該股沒有交易）不產生訊號。"""
    avg = volume.rolling(60, min_periods=40).mean().shift(1)
    f, t = chips['foreign'], chips['trust']
    margin = chips['margin'].ffill(limit=5) * 1000            # 張 → 股
    tb, ts, fb, fs = t > 0, t < 0, f > 0, f < 0
    t_run, ts_run, f_run = _streak_np(tb), _streak_np(ts), _streak_np(fb)
    chg5 = px / px.shift(5) - 1
    dmargin5 = (margin - margin.shift(5)) / avg
    prior_t = tb.astype(float).shift(1).rolling(20, min_periods=20).max()
    s = {
        'trust_start': tb & (prior_t == 0),
        'trust_streak3': t_run == 3,
        'trust_big': t >= 0.10 * avg,
        'trust_sell3': ts_run == 3,
        'foreign_streak5': f_run == 5,
        'foreign_turn': fs & (f_run.shift(1) >= 5),
        'foreign_big_buy': f >= 0.20 * avg,
        'foreign_big_sell': f <= -0.20 * avg,
        'both_buy': (f >= 0.05 * avg) & (t >= 0.05 * avg),
        'both_sell': (f <= -0.05 * avg) & (t <= -0.05 * avg),
        'margin_chase': (dmargin5 >= 0.5) & (chg5 >= 0.05),
        'margin_flush': (dmargin5 <= -0.5) & (chg5 <= -0.05),
    }
    has = f.notna() & t.notna()
    return {k: (v & has if not k.startswith('margin') else v & margin.notna()).fillna(False).astype(bool)
            for k, v in s.items()}
