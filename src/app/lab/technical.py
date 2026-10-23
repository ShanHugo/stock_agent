"""技術面訊號（均線、RSI、MACD、布林通道、突破、量能、K 線型態）的事件研究。

每個訊號在 d 日收盤後才知道（只用 d 日以前的資料）。驗證方式：
  進場  d+1 日開盤（還原後）；出場 d+H 日收盤。H = 5、20 個交易日。
  超額  同一進出場時間，選股範圍內全部股票等權重平均的報酬 → 「訊號股 − 同期全體」。
  t 值  每天先取訊號股平均超額，再依 H 天切成不重疊區塊、取區塊平均，對區塊做 t 檢定
        （同一區塊內的日子報酬重疊，不能當成獨立樣本）。
  成本  扣一次來回成本（手續費 2.8 折、證交稅、滑價）。

K 線型態需要最高價、最低價（ohlc_day 快取）；沒有時只研究其他訊號。
"""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import FEE, TAX, SLIPPAGE

HORIZONS = (5, 20)
FEE_DISCOUNT = 0.28
ROUND_TRIP = FEE * FEE_DISCOUNT * 2 + TAX + SLIPPAGE * 2
MIN_EVENTS = 200
MIN_BLOCKS = 30
T_PASS = 3.0          # 同時檢驗約 30 個訊號，|t| > 2 碰巧出現的機會太高，門檻提高到 3

# 訊號代號 → (中文名稱, 類別, 說明)
SIGNALS = {
    'ma_golden':     ('5 日線上穿 20 日線', '均線', '黃金交叉'),
    'ma_death':      ('5 日線下穿 20 日線', '均線', '死亡交叉'),
    'cross_up_60':   ('收盤站上 60 日線', '均線', '前一天在線下'),
    'cross_dn_60':   ('收盤跌破 60 日線', '均線', '前一天在線上'),
    'bull_align':    ('均線多頭排列成形', '均線', '5 > 20 > 60 日線，前一天還不是'),
    'high_60':       ('創 60 日新高', '突破', '收盤為近 60 日最高，前 5 天沒有創高'),
    'high_250':      ('創 250 日新高', '突破', '收盤為近一年最高，前 5 天沒有創高'),
    'low_60':        ('創 60 日新低', '突破', '收盤為近 60 日最低，前 5 天沒有創低'),
    'rsi_oversold':  ('RSI 跌破 30', '擺盪', 'RSI(14) 由 30 以上跌到 30 以下'),
    'rsi_overbought': ('RSI 突破 70', '擺盪', 'RSI(14) 由 70 以下升到 70 以上'),
    'macd_golden':   ('MACD 黃金交叉', '擺盪', 'DIF 上穿 MACD 訊號線（12, 26, 9）'),
    'macd_death':    ('MACD 死亡交叉', '擺盪', 'DIF 下穿 MACD 訊號線'),
    'bb_up':         ('突破布林上軌', '擺盪', '收盤由上軌以下升到上軌以上（20 日、2 倍標準差）'),
    'bb_dn':         ('跌破布林下軌', '擺盪', '收盤由下軌以上跌到下軌以下'),
    'vol_up':        ('爆量上漲', '量能', '量 ≥ 20 日均量 3 倍，且收盤漲 ≥ 3%'),
    'vol_dn':        ('爆量下跌', '量能', '量 ≥ 20 日均量 3 倍，且收盤跌 ≥ 3%'),
    'long_red':      ('長紅 K', 'K 線', '收盤比開盤高 ≥ 5%'),
    'long_black':    ('長黑 K', 'K 線', '收盤比開盤低 ≥ 5%'),
    'gap_up':        ('跳空上漲且守住', 'K 線', '開盤比前一天收盤高 ≥ 2%，收盤 ≥ 開盤'),
    'gap_dn':        ('跳空下跌且收黑', 'K 線', '開盤比前一天收盤低 ≥ 2%，收盤 ≤ 開盤'),
    'limit_up':      ('漲停', 'K 線', '收盤漲 ≥ 9.5%'),
    'limit_dn':      ('跌停', 'K 線', '收盤跌 ≥ 9.5%'),
    # 以下需要最高價、最低價
    'upper_shadow':  ('長上影線', 'K 線型態', '上影線 ≥ 實體 2 倍且 ≥ 股價 3%'),
    'hammer':        ('長下影線（錘子）', 'K 線型態', '下影線 ≥ 實體 2 倍且 ≥ 股價 3%，上影線很短'),
    'bull_engulf':   ('多頭吞噬', 'K 線型態', '前一天收黑，今天收紅且實體包住前一天實體'),
    'bear_engulf':   ('空頭吞噬', 'K 線型態', '前一天收紅，今天收黑且實體包住前一天實體'),
    'doji_after_up': ('上漲後十字線', 'K 線型態', '近 10 日漲 ≥ 10%，今天實體 ≤ 全日振幅 10%'),
    'wide_range_up': ('長紅且收最高', 'K 線型態', '振幅 ≥ 6%，收盤在全日最高 10% 範圍內'),
}
NEEDS_HL = {'upper_shadow', 'hammer', 'bull_engulf', 'bear_engulf', 'doji_after_up', 'wide_range_up'}


def _cross_up(a: pd.DataFrame, b) -> pd.DataFrame:
    return (a > b) & (a.shift(1) <= (b.shift(1) if isinstance(b, pd.DataFrame) else b))


def _cross_dn(a: pd.DataFrame, b) -> pd.DataFrame:
    return (a < b) & (a.shift(1) >= (b.shift(1) if isinstance(b, pd.DataFrame) else b))


def _first_in(sig: pd.DataFrame, n: int) -> pd.DataFrame:
    """只留前 n 天沒有出現過的訊號（避免連續創高天天算一次）。"""
    prev = sig.shift(1).fillna(False).astype(float).rolling(n, min_periods=1).max().astype(bool)
    return sig & ~prev


def rsi(px: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = px.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def signal_panels(px: pd.DataFrame, open_: pd.DataFrame, volume: pd.DataFrame,
                  high: pd.DataFrame | None = None, low: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """px 為還原收盤價；open_/high/low 需已用同一還原係數調整。回傳 {訊號代號: 布林面板}。"""
    ma5, ma20, ma60 = (px.rolling(n, min_periods=n).mean() for n in (5, 20, 60))
    chg = px / px.shift(1) - 1
    avgvol = volume.rolling(20, min_periods=15).mean().shift(1)
    ema12, ema26 = px.ewm(span=12, min_periods=26).mean(), px.ewm(span=26, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, min_periods=9).mean()
    sd20 = px.rolling(20, min_periods=20).std()
    r = rsi(px)
    body = px / open_ - 1
    gap = open_ / px.shift(1) - 1
    bull = (ma5 > ma20) & (ma20 > ma60)
    s = {
        'ma_golden': _cross_up(ma5, ma20), 'ma_death': _cross_dn(ma5, ma20),
        'cross_up_60': _cross_up(px, ma60), 'cross_dn_60': _cross_dn(px, ma60),
        'bull_align': bull & ~bull.shift(1, fill_value=False),
        'high_60': _first_in(px >= px.rolling(60, min_periods=60).max(), 5),
        'high_250': _first_in(px >= px.rolling(250, min_periods=250).max(), 5),
        'low_60': _first_in(px <= px.rolling(60, min_periods=60).min(), 5),
        'rsi_oversold': _cross_dn(r, 30), 'rsi_overbought': _cross_up(r, 70),
        'macd_golden': _cross_up(dif, dea), 'macd_death': _cross_dn(dif, dea),
        'bb_up': _cross_up(px, ma20 + 2 * sd20), 'bb_dn': _cross_dn(px, ma20 - 2 * sd20),
        'vol_up': (volume >= 3 * avgvol) & (chg >= 0.03), 'vol_dn': (volume >= 3 * avgvol) & (chg <= -0.03),
        'long_red': body >= 0.05, 'long_black': body <= -0.05,
        'gap_up': (gap >= 0.02) & (px >= open_), 'gap_dn': (gap <= -0.02) & (px <= open_),
        'limit_up': chg >= 0.095, 'limit_dn': chg <= -0.095,
    }
    if high is not None and low is not None:
        top, bot = np.maximum(px, open_), np.minimum(px, open_)
        real = (px - open_).abs()
        upper, lower, rng = high - top, bot - low, high - low
        prev_red, prev_black = px.shift(1) > open_.shift(1), px.shift(1) < open_.shift(1)
        s.update({
            'upper_shadow': (upper >= 2 * real) & (upper >= 0.03 * px),
            'hammer': (lower >= 2 * real) & (lower >= 0.03 * px) & (upper <= 0.5 * real.clip(lower=0.001 * px)),
            'bull_engulf': prev_black & (px > open_) & (px >= open_.shift(1)) & (open_ <= px.shift(1)),
            'bear_engulf': prev_red & (px < open_) & (px <= open_.shift(1)) & (open_ >= px.shift(1)),
            'doji_after_up': (px / px.shift(10) - 1 >= 0.10) & (real <= 0.1 * rng) & (rng > 0),
            'wide_range_up': (rng >= 0.06 * px.shift(1)) & (high - px <= 0.1 * rng) & (px > open_),
        })
    return {k: v.fillna(False).astype(bool) for k, v in s.items()}


def forward_returns(px: pd.DataFrame, open_: pd.DataFrame, h: int) -> pd.DataFrame:
    """以 d 為索引：d+1 開盤進、d+h 收盤出的報酬。"""
    return px.shift(-h) / open_.shift(-1) - 1


@dataclass
class SignalResult:
    signal: str
    horizon: int
    events: int
    days: int
    blocks: int
    excess: float           # 平均超額（未扣成本）
    excess_net: float       # 扣一次來回成本
    t: float
    hit: float              # 個別事件超額 > 0 的比例
    first_half: float
    second_half: float
    vs_0050: float          # 平均比 0050 多（未扣成本）
    unbuyable: float        # 隔天開盤就漲停、實際可能買不到的比例

    @property
    def stable(self) -> bool:
        return np.sign(self.first_half) == np.sign(self.second_half) != 0

    @property
    def verdict(self) -> str:
        if self.events < MIN_EVENTS or self.blocks < MIN_BLOCKS:
            return '樣本不足'
        if self.t >= T_PASS and self.stable and self.excess_net > 0:
            return '有效（偏多）'
        if self.t <= -T_PASS and self.stable:
            return '有效（偏空，可用來避開）'
        return '無效'


def evaluate(signals: dict[str, pd.DataFrame], px: pd.DataFrame, open_: pd.DataFrame, member: pd.DataFrame,
             benchmark: pd.Series, *, start: str, horizons=HORIZONS) -> list[SignalResult]:
    """member：布林面板，d 日該股是否在當時的選股範圍內。"""
    out = []
    raw_gap = open_.shift(-1) / px - 1          # 隔天開盤相對今天收盤
    for h in horizons:
        fwd = forward_returns(px, open_, h)
        valid = member & fwd.notna()
        base = fwd.where(valid).mean(axis=1)                     # 同期全體等權重
        excess = fwd.sub(base, axis=0)
        bench = benchmark.shift(-h) / benchmark.shift(-1) - 1     # 0050 同期（收盤到收盤近似）
        days = px.loc[start:].index[:-h - 1]
        for name, sig in signals.items():
            ev = (sig & valid).loc[days]
            n_day = ev.sum(axis=1)
            if n_day.sum() == 0:
                continue
            ex = excess.loc[days].where(ev)
            daily = ex.sum(axis=1)[n_day > 0] / n_day[n_day > 0]
            pos = pd.Series(np.arange(len(days)), index=days)[daily.index]
            blocks = daily.groupby(pos // h).mean()
            sd = blocks.std(ddof=1)
            half = len(blocks) // 2
            # stack 會保留空格（非事件），要先丟掉，否則勝率的分母會變成所有股票日
            vals = ex.stack().dropna()
            rel0050 = fwd.loc[days].where(ev).sub(bench.loc[days], axis=0).stack().dropna()
            gaps = raw_gap.loc[days].where(ev).stack().dropna()
            out.append(SignalResult(
                signal=name, horizon=h, events=int(n_day.sum()), days=int((n_day > 0).sum()), blocks=len(blocks),
                excess=float(blocks.mean()), excess_net=float(blocks.mean() - ROUND_TRIP),
                t=float(blocks.mean() / sd * np.sqrt(len(blocks))) if sd and len(blocks) > 1 else 0.0,
                hit=float((vals > 0).mean()),
                first_half=float(blocks.iloc[:half].mean()) if half else float('nan'),
                second_half=float(blocks.iloc[half:].mean()) if half else float('nan'),
                vs_0050=float(rel0050.mean()), unbuyable=float((gaps >= 0.095).mean())))
    return out
