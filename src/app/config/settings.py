import os
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()

@dataclass
class Settings:
    tx_fee_bps: float = float(os.getenv("TX_FEE_BPS", 3.99))   # 台股手續費（bps）：0.1425% × 2.8 折 ≈ 3.99；持股提醒估算賣出成本用

settings = Settings()
