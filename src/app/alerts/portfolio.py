"""持股設定：讀取 portfolio.json（你的真實持股與停損/停利規則）。

每筆持股：
  symbol            "2330"（上市自動加 .TW）或 "6488.TWO"（上櫃請寫完整代號）
  name              顯示用名稱（選填）
  shares            持有股數（零股也可以）
  cost              每股成本
  entry_date        買進日 YYYY-MM-DD（移動停損從這天起算最高收盤）
  stop_loss_pct     例 -0.08 → 跌破成本 8% 停損（與 stop_price 擇一，stop_price 優先）
  stop_price        固定停損價
  take_profit_pct   例 0.20 → 漲 20% 停利（與 target_price 擇一，target_price 優先）
  target_price      固定停利價
  trailing_stop_pct 例 0.10 → 從買進後最高收盤回落 10% 出場（選填）
  take_profit_sell_ratio  停利時賣出比例，預設 1.0（全部）
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Holding:
    symbol: str
    shares: int
    cost: float
    entry_date: str
    name: str = ''
    stop_loss_pct: float | None = None
    stop_price: float | None = None
    take_profit_pct: float | None = None
    target_price: float | None = None
    trailing_stop_pct: float | None = None
    take_profit_sell_ratio: float = 1.0

    @property
    def yf_symbol(self) -> str:
        return self.symbol if '.' in self.symbol else f'{self.symbol}.TW'

    @property
    def label(self) -> str:
        return f'{self.symbol} {self.name}'.strip()

    def stop_level(self) -> float | None:
        if self.stop_price is not None:
            return float(self.stop_price)
        if self.stop_loss_pct is not None:
            return self.cost * (1 + self.stop_loss_pct)
        return None

    def target_level(self) -> float | None:
        if self.target_price is not None:
            return float(self.target_price)
        if self.take_profit_pct is not None:
            return self.cost * (1 + self.take_profit_pct)
        return None


@dataclass
class Portfolio:
    holdings: list[Holding]
    near_threshold: float = 0.02  # 距離停損/停利價 2% 內發「接近」提醒
    errors: list[str] = field(default_factory=list)


def _validate(h: Holding) -> list[str]:
    errs = []
    if h.shares <= 0:
        errs.append(f'{h.symbol}: shares 必須 > 0')
    if h.cost <= 0:
        errs.append(f'{h.symbol}: cost 必須 > 0')
    if h.stop_loss_pct is not None and not (-1 < h.stop_loss_pct < 0):
        errs.append(f'{h.symbol}: stop_loss_pct 應為負數，例如 -0.08')
    if h.take_profit_pct is not None and h.take_profit_pct <= 0:
        errs.append(f'{h.symbol}: take_profit_pct 應為正數，例如 0.2')
    if h.trailing_stop_pct is not None and not (0 < h.trailing_stop_pct < 1):
        errs.append(f'{h.symbol}: trailing_stop_pct 應介於 0~1，例如 0.1')
    if not (0 < h.take_profit_sell_ratio <= 1):
        errs.append(f'{h.symbol}: take_profit_sell_ratio 應介於 0~1')
    if h.stop_level() is None and h.target_level() is None and h.trailing_stop_pct is None:
        errs.append(f'{h.symbol}: 至少要設定一種停損或停利規則')
    return errs


def load_portfolio(path: str | Path) -> Portfolio:
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    known = set(Holding.__dataclass_fields__)
    holdings, errors = [], []
    for item in raw.get('holdings', []):
        unknown = set(item) - known
        if unknown:
            errors.append(f"{item.get('symbol', '?')}: 未知欄位 {sorted(unknown)}")
        h = Holding(**{k: v for k, v in item.items() if k in known})
        h.symbol = str(h.symbol)
        errs = _validate(h)
        errors.extend(errs)
        if not errs:
            holdings.append(h)
    return Portfolio(holdings=holdings, near_threshold=raw.get('near_threshold', 0.02), errors=errors)
