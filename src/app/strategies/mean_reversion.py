import pandas as pd
from .base import Strategy
from ..features.indicators import mean_reversion_signal

class MeanReversionStrategy(Strategy):
    def __init__(self, lookback: int = 5):
        self.lookback = lookback

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        pos = mean_reversion_signal(df['close'], self.lookback)
        return pos.fillna(0)
