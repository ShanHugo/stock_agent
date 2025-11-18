# 策略基底類別
import pandas as pd
from ..features.indicators import momentum_signal

class Strategy:
    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError("策略需實作 generate_positions 方法")

class MomentumStrategy(Strategy):
    def __init__(self, lookback: int = 5):
        self.lookback = lookback

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        pos = momentum_signal(df['close'], self.lookback)
        return pos.fillna(0)
