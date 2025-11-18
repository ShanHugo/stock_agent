# 資料處理與回測用 data.py
import pandas as pd

def load_ohlcv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    return df
