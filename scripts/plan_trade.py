#!/usr/bin/env python3
"""交易計畫：買個股前先寫理由、停損與失效條件，系統依你的風險參數算出最多能買幾股。

使用範例:
  python scripts/plan_trade.py new 2330 --stop 2200 --thesis "AI 需求帶動先進製程滿載" \\
      --target 2800 --invalid below_ma:60 --invalid time_stop:60:0.05
  python scripts/plan_trade.py new 2330 --entry 2400 --stop 2200 --thesis "..."   # 指定預計買價
  python scripts/plan_trade.py list
  python scripts/plan_trade.py cancel <計畫ID>
  python scripts/plan_trade.py review 2330          # 確認持股的買進理由仍成立

失效條件格式：close_below:價格、below_ma:天數、time_stop:天數:最低報酬（例 time_stop:40:0.05）
"""
from __future__ import annotations
import argparse, json, sys, pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.app.config.settings import settings
from src.app.data.fetch import fetch_ohlcv_yf
from src.app.alerts.portfolio import Holding, load_portfolio
from src.app.alerts.plans import (size_position, new_plan, load_plans, save_plans, validate_risk)
from src.app.alerts.trades import mark_reviewed

PLANS = _ROOT / 'state' / 'plans.json'


def parse_invalid(spec: str) -> dict:
    parts = spec.split(':')
    t = parts[0]
    try:
        if t == 'close_below':
            return {'type': t, 'price': float(parts[1])}
        if t == 'below_ma':
            return {'type': t, 'days': int(parts[1])}
        if t == 'time_stop':
            return {'type': t, 'days': int(parts[1]), 'min_gain': float(parts[2])}
    except (IndexError, ValueError):
        pass
    raise argparse.ArgumentTypeError(f'失效條件格式錯誤：{spec}')


def cmd_new(args, portfolio_path: pathlib.Path) -> int:
    pf = load_portfolio(portfolio_path)
    errs = validate_risk(pf.risk)
    if errs:
        print('[error] ' + '\n[error] '.join(errs))
        print('請在 portfolio.json 加上例如 "risk": {"capital": 總資金, "max_risk_per_trade": 單筆最多虧損比例}')
        return 1
    h = Holding(symbol=args.symbol, shares=1, cost=1)
    entry = args.entry
    if entry is None:
        df = fetch_ohlcv_yf(h.yf_symbol, _recent(), _today())
        entry = float(df['close'].iloc[-1])
        print(f'[info] 未指定 --entry，以最近收盤 {entry:g}（{df.index[-1].date()}）估算')
    fee, tax = pf.sell_costs_bps(h, settings.tx_fee_bps)
    try:
        sizing = size_position(capital=pf.risk['capital'], max_risk_per_trade=pf.risk['max_risk_per_trade'],
                               entry=entry, stop=args.stop, buy_fee_bps=fee, sell_fee_bps=fee, sell_tax_bps=tax,
                               max_position_pct=pf.risk.get('max_position_pct'))
        plan = new_plan(h.symbol, entry=entry, stop=args.stop, thesis=args.thesis, sizing=sizing,
                        target=args.target, invalidation=args.invalid or [])
    except ValueError as e:
        print(f'[error] {e}')
        return 1
    if sizing['max_shares'] < 1:
        print(f"[error] 風險預算 {sizing['risk_budget']:,.0f} 不足以買 1 股（每股風險 {sizing['risk_per_share']:,.2f}），"
              '請把停損設近一點或放棄這筆交易')
        return 1
    plans = load_plans(PLANS)
    plans.append(plan)
    save_plans(PLANS, plans)
    lots, odd = divmod(sizing['max_shares'], 1000)
    print(f"[ok] 計畫 {plan['id']} 已建立：{h.symbol}")
    print(f"  預計買價 {entry:g}，停損 {args.stop:g}（距離 {sizing['stop_distance_pct']:.1%}）"
          + (f"，停利 {args.target:g}" if args.target else ''))
    print(f"  風險預算 {sizing['risk_budget']:,.0f}，每股風險（含費用）{sizing['risk_per_share']:,.2f}")
    print(f"  最多買 {sizing['max_shares']:,} 股（{lots} 張 + {odd} 股）"
          + ('，受單一持股上限限制' if sizing['limited_by'] == 'position' else ''))
    print(f"  部位 {sizing['position_value']:,.0f}（占資金 {sizing['position_pct']:.1%}），"
          f"跌到停損約虧 {sizing['risk_at_stop']:,.0f}（{sizing['risk_pct']:.2%}）")
    if plan['invalidation']:
        print('  失效條件：' + json.dumps(plan['invalidation'], ensure_ascii=False))
    print(f'  實際買進後執行：python scripts/record_trade.py buy {h.symbol} 股數 成交價')
    return 0


def _today() -> str:
    from datetime import date, timedelta
    return (date.today() + timedelta(days=1)).isoformat()


def _recent() -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=14)).isoformat()


def cmd_list(args) -> int:
    plans = load_plans(PLANS)
    if not plans:
        print('（沒有計畫）')
    for p in plans:
        if p['status'] == 'cancelled' and not args.all:
            continue
        print(f"{p['id']}  {p['status']:<9} {p['symbol']:<6} 買 {p['entry']:g} 停損 {p['stop']:g} "
              f"上限 {p['max_shares']} 股  {p['created']}  {p['thesis']}")
    return 0


def cmd_cancel(args) -> int:
    plans = load_plans(PLANS)
    for p in plans:
        if p['id'] == args.plan_id and p['status'] == 'pending':
            p['status'] = 'cancelled'
            save_plans(PLANS, plans)
            print(f"[ok] 已取消計畫 {p['id']}（{p['symbol']}）")
            return 0
    print(f'[error] 找不到待執行的計畫 {args.plan_id}')
    return 1


def cmd_review(args, portfolio_path: pathlib.Path) -> int:
    raw = json.loads(portfolio_path.read_text(encoding='utf-8'))
    try:
        raw, msg = mark_reviewed(raw, args.symbol)
    except ValueError as e:
        print(f'[error] {e}')
        return 1
    portfolio_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[ok] {msg}')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='交易計畫與部位計算')
    p.add_argument('--portfolio', default=str(_ROOT / 'portfolio.json'))
    sub = p.add_subparsers(dest='cmd', required=True)
    n = sub.add_parser('new', help='建立計畫並計算可買股數')
    n.add_argument('symbol')
    n.add_argument('--stop', type=float, required=True, help='停損價')
    n.add_argument('--thesis', required=True, help='買進理由')
    n.add_argument('--entry', type=float, help='預計買價（預設最近收盤）')
    n.add_argument('--target', type=float, help='停利價（選填）')
    n.add_argument('--invalid', type=parse_invalid, action='append', help='失效條件，可重複')
    ls = sub.add_parser('list', help='列出計畫')
    ls.add_argument('--all', action='store_true', help='包含已取消')
    c = sub.add_parser('cancel', help='取消待執行計畫')
    c.add_argument('plan_id')
    r = sub.add_parser('review', help='確認持股理由仍成立')
    r.add_argument('symbol')
    args = p.parse_args()
    path = pathlib.Path(args.portfolio)
    if args.cmd == 'new':
        return cmd_new(args, path)
    if args.cmd == 'list':
        return cmd_list(args)
    if args.cmd == 'cancel':
        return cmd_cancel(args)
    return cmd_review(args, path)


if __name__ == '__main__':
    sys.exit(main())
