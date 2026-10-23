"""個股參考標記（只是資訊，不影響模擬機器人的規則）：

  new_high      近 20 個交易日內收盤創 250 日新高。技術研究（2016～）：之後 20 天平均比同期電子股多約 1.5%。
  foreign_turn  近 5 個交易日內外資連買 5 天以上後轉賣超。籌碼研究：研究期與考卷期（2022～）都有效，
                之後 5 天平均落後約 0.24%（小於一次買賣成本，只適合當「先別追」的參考）。

把機器人加上這兩個條件的歷史回測都比現行差，所以只顯示、不交易。
"""
from __future__ import annotations

import pandas as pd

from .chips_signals import _streak_np

FLAG_NAMES = {'new_high': '一年新高', 'foreign_turn': '外資轉賣'}
NEW_HIGH_WINDOW = 20
FOREIGN_TURN_WINDOW = 5


def stock_flags(px: pd.DataFrame, foreign: pd.DataFrame | None, d: pd.Timestamp, codes) -> dict[str, list[str]]:
    """px：還原收盤（至少 250 個交易日）；foreign：外資買賣超股數（可為 None）。回傳 {代號: [標記代號]}。"""
    codes = [c for c in codes if c in px.columns]
    out: dict[str, list[str]] = {c: [] for c in codes}
    if not codes:
        return out
    p = px[codes].loc[:d]
    hi = p.rolling(250, min_periods=250).max()
    recent_high = (p >= hi).iloc[-NEW_HIGH_WINDOW:].any()
    turn = pd.Series(False, index=codes)
    if foreign is not None and not foreign.empty:
        f = foreign.reindex(columns=codes).loc[:d]
        run = _streak_np(f > 0)
        ev = (f < 0) & (run.shift(1) >= 5)
        turn = ev.iloc[-FOREIGN_TURN_WINDOW:].any()
    for c in codes:
        if bool(recent_high.get(c, False)):
            out[c].append('new_high')
        if bool(turn.get(c, False)):
            out[c].append('foreign_turn')
    return out
