"""模擬操作機器人：依「營收動能」規則與你的風險設定，用模擬帳戶逐日操作。歷史模擬與每日模擬共用同一套邏輯。

每個交易日 D 收盤後：
  1. 出場訊號（持股任一成立 → 次一交易日賣出）：收盤 ≤ 停損價、收盤 < 120 日線、最近 2 個已公布月份營收都年減
  2. 進場訊號：符合營收動能（最近 2 個已公布月份營收都年增、收盤 > 120 日線）的電子股，依最新月營收年增率由高到低，
     在「每筆最多虧 max_risk_per_trade、單一個股 ≤ max_position_pct、所有個股停損總虧損 ≤ max_total_risk」
     與現金限制內買進（整股、可零股），停損設在進場價下方 stop_pct
次一交易日：先賣後買，以 exec_price（每日模擬＝開盤價；歷史模擬沒有開盤價，用收盤價並加滑價）成交。
成本：手續費 = max(最低手續費, 成交金額 × 0.1425% × 折扣)，取整數；賣出另收證交稅 0.3%（ETF 0.1%）。
除權息：持股遇除權息日，以「除權息前收盤 − 參考價」× 股數 計入現金（股票股利以價值近似）。
這是模擬，不是買賣建議；真實下單由使用者自行決定。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd


@dataclass
class Config:
    capital: float
    max_risk_per_trade: float = 0.03
    max_position_pct: float = 0.20
    max_total_risk: float = 0.08
    stop_pct: float = 0.10
    fee_rate: float = 0.001425 * 0.28
    min_fee: float = 1.0
    tax_rate: float = 0.003
    etf_tax_rate: float = 0.001
    slippage: float = 0.0          # 每日模擬用真實開盤價，不另加；歷史模擬建議 0.001
    ma_days: int = 120
    rev_months: int = 2
    breakeven_after: float | None = None      # 收盤 ≥ 成本 ×(1+此值) 後，停損上移到成本價
    max_per_theme: int | None = None          # 同一族群最多同時持有幾檔（一檔屬於多個族群時，任一族群滿了就不買）


def fee(cfg: Config, amount: float) -> float:
    return max(cfg.min_fee, math.floor(amount * cfg.fee_rate)) if amount > 0 else 0.0


def sell_tax(cfg: Config, code: str, amount: float) -> float:
    rate = cfg.etf_tax_rate if code.startswith('00') else cfg.tax_rate
    return float(math.floor(amount * rate))


@dataclass
class Bot:
    cfg: Config
    cash: float = 0.0
    positions: dict = field(default_factory=dict)   # code → {shares, entry, entry_date, stop, cost}
    pending: list = field(default_factory=list)     # [{side, code, shares, reason, signal_date, ref_price, stop}]
    trades: list = field(default_factory=list)
    equity: list = field(default_factory=list)      # [{date, equity, cash, positions}]
    last_date: str | None = None

    @classmethod
    def new(cls, cfg: Config) -> 'Bot':
        return cls(cfg=cfg, cash=float(cfg.capital))

    # ---------- 狀態存取 ----------
    def to_dict(self) -> dict:
        return {'cfg': asdict(self.cfg), 'cash': self.cash, 'positions': self.positions, 'pending': self.pending,
                'trades': self.trades, 'equity': self.equity, 'last_date': self.last_date}

    @classmethod
    def from_dict(cls, d: dict) -> 'Bot':
        known = set(Config.__dataclass_fields__)          # 略過已移除的舊設定，舊存檔照樣讀得進來
        return cls(cfg=Config(**{k: v for k, v in d['cfg'].items() if k in known}), cash=d['cash'],
                   positions=d['positions'], pending=d['pending'], trades=d['trades'], equity=d['equity'],
                   last_date=d['last_date'])


    # ---------- 每日步驟 ----------
    def execute(self, d: str, exec_price) -> list[dict]:
        """以 exec_price(code) 執行昨天收盤後排定的單；先賣後買。價格缺（停牌）時：賣單留到下一天，買單取消。"""
        done, keep = [], []
        for o in sorted(self.pending, key=lambda o: o['side'] != 'sell'):
            p = exec_price(o['code'])
            if p is None or not p > 0:
                if o['side'] == 'sell':
                    keep.append(o)
                continue
            if o['side'] == 'sell':
                if o['code'] not in self.positions:
                    continue                  # 已經賣掉了（保險：避免重複賣單）
                p *= 1 - self.cfg.slippage
                pos = self.positions.pop(o['code'])       # 出場一律全部賣出
                qty = pos['shares']
                amt = qty * p
                cost = fee(self.cfg, amt) + sell_tax(self.cfg, o['code'], amt)
                self.cash += amt - cost
                pnl = amt - cost - pos['cost']
                init_stop = pos.get('init_stop', pos['stop'])
                t = {'date': d, 'side': 'sell', 'code': o['code'], 'shares': qty, 'price': round(p, 2),
                     'amount': round(amt, 0), 'fees': cost, 'pnl': round(pnl, 0), 'return': pnl / pos['cost'],
                     'entry_date': pos['entry_date'], 'reason': o['reason'],
                     'r_multiple': pnl / max(1e-9, (pos['entry'] - init_stop) * qty)}
            else:
                p *= 1 + self.cfg.slippage
                shares = o['shares']
                while shares > 0 and shares * p + fee(self.cfg, shares * p) > self.cash:
                    shares -= 1       # 開盤價比預期高、現金不夠時少買
                if shares <= 0:
                    continue
                amt = shares * p
                c = fee(self.cfg, amt)
                self.cash -= amt + c
                stop = round(p * (1 - self.cfg.stop_pct), 2)
                self.positions[o['code']] = {'shares': shares, 'entry': round(p, 2), 'entry_date': d,
                                             'stop': stop, 'init_stop': stop, 'cost': amt + c}
                t = {'date': d, 'side': 'buy', 'code': o['code'], 'shares': shares, 'price': round(p, 2),
                     'amount': round(amt, 0), 'fees': c, 'stop': stop, 'reason': o['reason']}
            self.trades.append(t)
            done.append(t)
        self.pending = keep
        return done

    def dividends(self, d: str, events: pd.DataFrame) -> None:
        """events：當天的除權息／減資（code, prev_close, ref）。
        參考價 < 前收盤（除權息、退還股款減資）：差價 × 股數計入現金。
        參考價 > 前收盤（彌補虧損減資）：股數依比例減少（市值不變），成本價與停損價同比例上調。"""
        for e in events.itertuples():
            pos = self.positions.get(e.code)
            if pos and e.ref > e.prev_close > 0:
                ratio = e.prev_close / e.ref
                new_shares = max(0, int(round(pos['shares'] * ratio)))
                self.trades.append({'date': d, 'side': 'reduction', 'code': e.code, 'shares': new_shares,
                                    'amount': 0, 'from_shares': pos['shares']})
                pos['shares'] = new_shares
                pos['stop'] = round(pos['stop'] / ratio, 2)
                pos['entry'] = round(pos['entry'] / ratio, 2)
                continue
            if pos and e.prev_close > e.ref > 0:
                cash = (e.prev_close - e.ref) * pos['shares']
                self.cash += cash
                self.trades.append({'date': d, 'side': 'dividend', 'code': e.code, 'shares': pos['shares'],
                                    'amount': round(cash, 0)})
                # 停損價跟著參考價下調，避免除息當天被誤判跌破
                pos['stop'] = round(pos['stop'] * e.ref / e.prev_close, 2)
                pos['entry'] = round(pos['entry'] * e.ref / e.prev_close, 2)

    def mark(self, d: str, close) -> float:
        value = sum(pos['shares'] * (close(c) or pos.get('last', pos['entry'])) for c, pos in self.positions.items())
        for c, pos in self.positions.items():
            if close(c):
                pos['last'] = close(c)
                pos['last_date'] = d          # 最後一個有報價的日子（停牌、下市時不會更新）
        eq = self.cash + value
        self.equity.append({'date': d, 'equity': round(eq, 2), 'cash': round(self.cash, 2), 'n': len(self.positions)})
        self.last_date = d
        return eq

    def decide(self, d: str, ctx: 'DayContext') -> list[dict]:
        """收盤後決定次一交易日的單。回傳新排定的單。"""
        orders = []
        eq = self.equity[-1]['equity'] if self.equity else self.cash
        # 出場（已經有待執行賣單的持股——例如跌停或停牌沒賣掉的——不再重複排單）
        waiting = {o['code'] for o in self.pending if o['side'] == 'sell'}
        for code, pos in self.positions.items():
            if code in waiting:
                continue
            c = ctx.close(code)
            cfg = self.cfg
            if c is not None and cfg.breakeven_after and c >= pos['entry'] * (1 + cfg.breakeven_after):
                pos['stop'] = max(pos['stop'], pos['entry'])          # 漲夠了，停損上移到成本
            reason = None
            if c is not None and c <= pos['stop']:
                reason = f"跌破停損 {pos['stop']}"
            elif c is not None and ctx.ma(code) is not None and c < ctx.ma(code):
                reason = f'跌破 {cfg.ma_days} 日線'
            elif ctx.revenue_down(code):
                reason = f'營收連 {cfg.rev_months} 個月年減'
            if reason:
                orders.append({'side': 'sell', 'code': code, 'shares': pos['shares'], 'reason': reason,
                               'signal_date': d, 'ref_price': c})
        selling = {o['code'] for o in orders}
        kept = {c: p for c, p in self.positions.items() if c not in selling}
        # 進場
        open_risk = sum(max(0.0, (ctx.close(c) or p['entry']) - p['stop']) * p['shares'] for c, p in kept.items())
        # 族群持股數（不含明天要賣出的持股）
        theme_of = ctx.themes or (lambda c: set())
        theme_count: dict[str, int] = {}
        for c in kept:
            for t in theme_of(c):
                theme_count[t] = theme_count.get(t, 0) + 1
        # 可用現金 = 現有現金 + 明天賣出的預估所得（先賣後買）
        cash = self.cash + sum((ctx.close(o['code']) or 0) * o['shares'] * (1 - self.cfg.tax_rate) for o in orders)
        for cand in ctx.candidates:
            code, price = cand['code'], cand['close']
            if code in self.positions or not price or price <= 0:
                continue
            cap = self.cfg.max_per_theme
            if cap and any(theme_count.get(t, 0) >= cap for t in theme_of(code)):
                continue                       # 這檔所屬的族群已經持滿，看下一檔
            stop = price * (1 - self.cfg.stop_pct)
            per_share = (price - stop) + price * self.cfg.fee_rate + stop * (self.cfg.fee_rate + self.cfg.tax_rate)
            by_risk = math.floor(eq * self.cfg.max_risk_per_trade / per_share)
            by_size = math.floor(eq * self.cfg.max_position_pct / price)
            by_cash = math.floor(max(0.0, cash - self.cfg.min_fee) / (price * (1 + self.cfg.fee_rate)))
            shares = min(by_risk, by_size, by_cash)
            if shares < 1:
                if by_cash < 1 and cash < eq * 0.02:
                    break                      # 現金用完
                continue                       # 這檔太貴（一股就超過單一個股上限），看下一檔
            if open_risk + shares * per_share > eq * self.cfg.max_total_risk:
                break                          # 總風險滿了
            open_risk += shares * per_share
            cash -= shares * price * (1 + self.cfg.fee_rate) + self.cfg.min_fee
            for t in theme_of(code):
                theme_count[t] = theme_count.get(t, 0) + 1
            orders.append({'side': 'buy', 'code': code, 'shares': shares, 'signal_date': d, 'ref_price': price,
                           'reason': f"營收動能：最新月營收年增 {cand['rev_yoy']:+.1f}%", 'stop_est': round(stop, 2)})
        self.pending.extend(orders)
        return orders


@dataclass
class DayContext:
    """某一天收盤後做決定需要的資料（由呼叫端組好）。"""
    close: object               # code -> 收盤價或 None
    ma: object                  # code -> 120 日均價或 None
    revenue_down: object        # code -> bool（最近 N 個已公布月份營收都年減）
    candidates: list            # [{code, close, rev_yoy}] 依營收年增由高到低，已符合營收動能
    themes: object = None       # code -> 所屬族群 set（族群集中度限制用；沒給就不限制）


def exit_kind(reason: str) -> str:
    if reason.startswith('跌破停損'):
        return '跌破停損'
    if '日線' in reason:
        return '跌破 120 日線'
    if reason.startswith('營收'):
        return '營收連 2 月年減'
    return reason


def summarize(bot: Bot, bench: pd.Series | None = None) -> dict:
    """bench：與 equity 同日期的 0050 還原價（可選）。"""
    eq = pd.Series({e['date']: e['equity'] for e in bot.equity}, dtype=float)
    eq.index = pd.to_datetime(eq.index)
    out = {'start': str(eq.index[0].date()) if len(eq) else None, 'end': str(eq.index[-1].date()) if len(eq) else None,
           'capital': bot.cfg.capital, 'equity': float(eq.iloc[-1]) if len(eq) else bot.cash}
    if len(eq) > 1:
        r = eq.pct_change().dropna()
        years = len(r) / 252
        out.update(total_return=float(eq.iloc[-1] / eq.iloc[0] - 1),
                   cagr=float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) if years > 0.25 else None,
                   max_drawdown=float((eq / eq.cummax() - 1).min()),
                   sharpe=float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0)
        if bench is not None:
            b = bench.reindex(eq.index).ffill()
            out.update(bench_total_return=float(b.iloc[-1] / b.iloc[0] - 1),
                       bench_cagr=float((b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1) if years > 0.25 else None,
                       bench_max_drawdown=float((b / b.cummax() - 1).min()))
    sells = [t for t in bot.trades if t['side'] == 'sell']
    if sells:
        rets = [t['return'] for t in sells]
        days = [(pd.Timestamp(t['date']) - pd.Timestamp(t['entry_date'])).days for t in sells]
        out.update(closed_trades=len(sells), win_rate=float(np.mean([x > 0 for x in rets])),
                   avg_return=float(np.mean(rets)), avg_win=float(np.mean([x for x in rets if x > 0] or [0])),
                   avg_loss=float(np.mean([x for x in rets if x <= 0] or [0])),
                   avg_hold_days=float(np.mean(days)),
                   total_fees=float(sum(t.get('fees', 0) for t in bot.trades)),
                   exit_reasons=pd.Series([exit_kind(t['reason']) for t in sells]).value_counts().to_dict())
    return out
