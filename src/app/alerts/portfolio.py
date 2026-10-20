"""持股設定：讀取 portfolio.json（你的真實持股與提醒規則）。所有門檻都由你自己填。

每筆持股（holdings）：
  symbol            台股上市 "0050"、上櫃 "6488.TWO"、美股 "VOO"
  name              顯示用名稱（選填）
  shares            持有股數，可為零股或碎股（例 204、2.0592）
  cost              成交均價（美股為美元）
  entry_date        首次買進日 YYYY-MM-DD（只有移動停損需要）
  group             配置分組（選填，預設台股 "TW"、美股 "US"）
  etf               是否為 ETF（台股 00 開頭自動判定；美股請自行標 true，否則視為個股）
  --- 停損 / 停利（全部選填）---
  stop_loss_pct     例 -0.08 → 跌破成本 8%（與 stop_price 擇一，stop_price 優先）
  stop_price        固定停損價
  take_profit_pct   例 0.20 → 漲 20%（與 target_price 擇一，target_price 優先）
  target_price      固定停利價
  trailing_stop_pct 例 0.10 → 從買進後最高收盤回落 10%
  take_profit_sell_ratio  停利時賣出比例，預設 1.0
  --- 從高點回落（選填）---
  drawdown_alert_pcts  例 [0.1, 0.2] → 從近一年高點回落 10%、20% 各通知一次
  plan              交易計畫（由 record_trade 依 plan_trade 建立的計畫自動填入；見 plans.py）

整體設定：
  near_threshold         距離停損/停利價多少以內發「接近」提醒，預設 0.02
  drawdown_window_days   回落的「高點」取最近幾個交易日，預設 252（約一年）
  allocation             {"targets": {"TW": 0.5, "US": 0.5}, "threshold": 0.05}
  fees                   覆寫賣出費用（bps），例 {"TW": {"fee_bps": 2.8}, "US": {"fee_bps": 10}}
  risk                   部位控管參數（見 plans.py）
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FEES = {
    # 證交稅：一般股票 0.3%，ETF 0.1%；手續費預設取 .env 的 TX_FEE_BPS
    'TW': {'fee_bps': None, 'tax_bps_stock': 30.0, 'tax_bps_etf': 10.0},
    # 美股沒有證交稅；複委託手續費各券商不同，請依你的券商設定
    'US': {'fee_bps': 0.0, 'tax_bps': 0.0},
}


@dataclass
class Holding:
    symbol: str
    shares: float
    cost: float
    name: str = ''
    entry_date: str | None = None
    group: str | None = None
    stop_loss_pct: float | None = None
    stop_price: float | None = None
    take_profit_pct: float | None = None
    target_price: float | None = None
    trailing_stop_pct: float | None = None
    take_profit_sell_ratio: float = 1.0
    drawdown_alert_pcts: list[float] | None = None
    plan: dict | None = None
    etf: bool | None = None

    @property
    def market(self) -> str:
        if self.symbol.upper().endswith('.TWO'):
            return 'TWO'
        return 'TW' if self.symbol[:1].isdigit() else 'US'

    @property
    def is_us(self) -> bool:
        return self.market == 'US'

    @property
    def is_etf(self) -> bool:
        if self.etf is not None:
            return bool(self.etf)
        # 台股 ETF 代號以 00 開頭；美股無法從代號判斷，需在 portfolio.json 標 "etf": true
        return not self.is_us and self.symbol.startswith('00')

    @property
    def currency(self) -> str:
        return 'USD' if self.is_us else 'TWD'

    @property
    def yf_symbol(self) -> str:
        return f'{self.symbol}.TW' if self.market == 'TW' else self.symbol

    @property
    def alloc_group(self) -> str:
        return self.group or ('US' if self.is_us else 'TW')

    @property
    def label(self) -> str:
        return f'{self.symbol} {self.name}'.strip()

    @property
    def needs_plan(self) -> bool:
        """個股需要交易計畫；ETF 定期定額不需要。"""
        return not self.is_etf

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
    near_threshold: float = 0.02
    drawdown_window_days: int = 252
    allocation: dict | None = None
    fees: dict = field(default_factory=dict)
    risk: dict | None = None
    errors: list[str] = field(default_factory=list)

    def buy_fee_bps(self, h: Holding, default_tw_fee_bps: float) -> float:
        return self.sell_costs_bps(h, default_tw_fee_bps)[0]

    def sell_costs_bps(self, h: Holding, default_tw_fee_bps: float) -> tuple[float, float]:
        """回傳 (手續費 bps, 交易稅 bps)，用於賣出估算。"""
        key = 'US' if h.is_us else 'TW'
        cfg = {**DEFAULT_FEES[key], **self.fees.get(key, {})}
        if h.is_us:
            return float(cfg['fee_bps']), float(cfg['tax_bps'])
        fee = cfg['fee_bps'] if cfg['fee_bps'] is not None else default_tw_fee_bps
        tax = cfg['tax_bps_etf'] if h.is_etf else cfg['tax_bps_stock']
        return float(fee), float(tax)


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
    if h.trailing_stop_pct is not None:
        if not (0 < h.trailing_stop_pct < 1):
            errs.append(f'{h.symbol}: trailing_stop_pct 應介於 0~1，例如 0.1')
        if not h.entry_date:
            errs.append(f'{h.symbol}: 使用 trailing_stop_pct 需要填 entry_date')
    if not (0 < h.take_profit_sell_ratio <= 1):
        errs.append(f'{h.symbol}: take_profit_sell_ratio 應介於 0~1')
    if h.drawdown_alert_pcts is not None and not all(0 < x < 1 for x in h.drawdown_alert_pcts):
        errs.append(f'{h.symbol}: drawdown_alert_pcts 每個值應介於 0~1，例如 [0.1, 0.2]')
    return errs


def _validate_allocation(alloc: dict | None, groups: set[str]) -> list[str]:
    if not alloc:
        return []
    targets = alloc.get('targets') or {}
    errs = []
    if abs(sum(targets.values()) - 1) > 1e-6:
        errs.append(f'allocation.targets 加總應為 1，目前為 {sum(targets.values()):g}')
    missing = groups - set(targets)
    if missing:
        errs.append(f'allocation.targets 缺少分組 {sorted(missing)}')
    if not (0 < alloc.get('threshold', 0) < 1):
        errs.append('allocation.threshold 應介於 0~1，例如 0.05')
    return errs


def holding_from_dict(item: dict) -> tuple[Holding | None, list[str]]:
    known = set(Holding.__dataclass_fields__)
    errs = []
    unknown = set(item) - known
    if unknown:
        errs.append(f"{item.get('symbol', '?')}: 未知欄位 {sorted(unknown)}")
    h = Holding(**{k: v for k, v in item.items() if k in known})
    h.symbol = str(h.symbol).strip()
    h.shares = float(h.shares)
    e = _validate(h)
    return (None if e else h), errs + e


def load_portfolio(path: str | Path) -> Portfolio:
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    holdings, errors = [], []
    for item in raw.get('holdings', []):
        h, errs = holding_from_dict(item)
        errors.extend(errs)
        if h:
            holdings.append(h)
    alloc = raw.get('allocation')
    errors.extend(_validate_allocation(alloc, {h.alloc_group for h in holdings}))
    return Portfolio(
        holdings=holdings,
        near_threshold=raw.get('near_threshold', 0.02),
        drawdown_window_days=raw.get('drawdown_window_days', 252),
        allocation=alloc if alloc and not any(e.startswith('allocation') for e in errors) else None,
        fees=raw.get('fees', {}),
        risk=raw.get('risk'),
        errors=errors,
    )
