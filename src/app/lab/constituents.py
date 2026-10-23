"""選股範圍的時點名單：history 為 [(生效日, 當時的名單)]，由 lab.market.build_universe 依每月產業別快照建立。"""
from __future__ import annotations

import pandas as pd


def members_on(history: list[tuple[pd.Timestamp, set[str]]], d: pd.Timestamp) -> set[str]:
    current = history[0][1]
    for start, m in history:
        if start <= d:
            current = m
        else:
            break
    return current
