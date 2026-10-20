import os
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()

@dataclass
class Settings:
    tx_fee_bps: float = float(os.getenv("TX_FEE_BPS", 2.8))
    tx_tax_bps: float = float(os.getenv("TX_TAX_BPS", 30.0))
    slippage_bps: float = float(os.getenv("SLIPPAGE_BPS", 5.0))

settings = Settings()
