#!/usr/bin/env python3
"""前瞻測試：每個交易日收盤後記下「營收動能、營收年增最高 20 檔」的名單，並評估之前紀錄 5 / 20 個交易日後的真實結果。

使用範例:
  python scripts/forward_test.py              # 記錄今天（15:30 前或今天行情未公布時記錄前一個交易日）並評估
  python scripts/forward_test.py --dry-run    # 只顯示，不寫入紀錄
  python scripts/forward_test.py --send       # 另外推送到 Discord
紀錄：state/forward/records.jsonl（只新增、不修改）；最新評估：state/forward/latest.json
這是規則的前瞻驗證紀錄，不是買賣建議。
"""
from __future__ import annotations
import argparse, json, os, sys, pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import pandas as pd
from dotenv import load_dotenv

from src.app.lab import market as mk
from src.app.lab import forward as fw
from src.app.lab.constituents import members_on
from src.app.research import sources as rs

STATE = _ROOT / 'state' / 'forward'
RECORDS = STATE / 'records.jsonl'
LATEST = STATE / 'latest.json'
DATA_START = '2025-01-01'     # 120 日均線與營收只需要最近一年多


def names_for(codes: list[str], ds, today) -> dict[str, str]:
    out = {}
    for m in reversed(ds.revenue_yoy.index[-3:]):
        for market in ('sii', 'otc'):
            try:
                df = rs.monthly_revenue(m.year, m.month, today=today, market=market)
            except rs.NoData:
                continue
            for c, n in zip(df['code'], df['name']):
                out.setdefault(c, n)
    return {c: out.get(c, '') for c in codes}


def fmt_pct(v):
    return '—' if v is None or v != v else f'{v:+.2%}'


def message(rec: dict | None, summary: list[dict], signal_date: str) -> str:
    L = [f'## 前瞻測試 {signal_date}（營收動能・營收年增最高 {fw.TOP_N} 檔）']
    if rec:
        L.append(f"符合條件 {rec['qualified']} 檔／電子股 {rec['universe']} 檔，前 {len(rec['picks'])} 檔：")
        L += [f"- {p['code']} {p['name']}（{p['industry']}）營收年增 {p['rev_yoy']:+.1f}%、前月 {p['rev_yoy_prev']:+.1f}%，"
              f"高於 120 日線 {p['above_ma_pct']:+.1%}" for p in rec['picks']]
    else:
        L.append('今天沒有新紀錄（已記錄過或非交易日）。')
    L.append('\n**之前紀錄的實際結果（扣來回成本 {:.3%}，與同期 0050 比較）**'.format(fw.ROUND_TRIP))
    for s in summary:
        if not s['matured']:
            L.append(f"- 持有 {s['horizon']} 天：尚無到期紀錄（{s['pending']} 筆等待中）")
            continue
        L.append(f"- 持有 {s['horizon']} 天：{s['matured']} 筆到期，平均 {fmt_pct(s['avg_ret'])}，0050 {fmt_pct(s['avg_bench'])}，"
                 f"平均贏 0050 {fmt_pct(s['avg_excess'])}，贏的比例 {s['win_rate']:.0%}"
                 + (f"（不重疊 {s['non_overlap']} 筆 t 值 {s['t']:+.2f}）" if s['t'] is not None else '（樣本還太少，無法判斷）'))
    L.append('_規則的前瞻驗證紀錄，不是買賣建議；樣本少時結果主要是運氣。_')
    return '\n'.join(L)


def main() -> int:
    p = argparse.ArgumentParser(description='前瞻測試')
    p.add_argument('--dry-run', action='store_true', help='只顯示，不寫入紀錄')
    p.add_argument('--send', action='store_true', help='推送到 Discord')
    args = p.parse_args()
    load_dotenv(_ROOT / '.env')
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    today = now.date()
    end = today - timedelta(days=1)
    if now.time() >= datetime.strptime('15:30', '%H:%M').time() and today.weekday() < 5:
        if all(not mk.quotes_day(m, today).empty for m in mk.MARKETS):
            end = today          # 兩邊今天的行情都已公布

    log = lambda m: print(m if m.startswith('[') else f'[data] {m}', file=sys.stderr)
    built = mk.build_market_dataset(mk.MARKETS, start=DATA_START, chips_start=None, today=today, end=end, log=log)
    ds, hist = built['ds'], built['history']
    d = ds.prices.index[-1]
    signal_date = d.strftime('%Y-%m-%d')
    members = [c for c in members_on(hist, d) if c in ds.prices.columns]
    picks = fw.pick(ds, members, d)
    qualified = len(fw.pick(ds, members, d, n=10_000))
    names = names_for(list(picks['code']), ds, today)
    rec = {
        'date': signal_date, 'rule': fw.RULE_ID, 'created_at': now.isoformat(timespec='seconds'),
        'universe': len(members), 'qualified': qualified,
        'picks': [{'code': r.code, 'name': names.get(r.code, ''), 'market': mk.MARKET_NAME[built['info'][r.code][0]],
                   'industry': built['info'][r.code][1], 'rev_yoy': round(r.rev_yoy, 2), 'rev_yoy_prev': round(r.rev_yoy_prev, 2),
                   'close': round(r.close, 2), 'above_ma_pct': round(r.above_ma_pct, 4)} for r in picks.itertuples()],
    }
    added = False if args.dry_run else fw.append_record(RECORDS, rec)
    records = fw.load_records(RECORDS) + ([rec] if args.dry_run else [])
    ev = fw.evaluate(records, ds.prices)
    summary = fw.summarize(ev)
    shown = rec if (added or args.dry_run) else None
    text = message(shown, summary, signal_date)
    print(text)
    if not args.dry_run:
        STATE.mkdir(parents=True, exist_ok=True)
        LATEST.write_text(json.dumps({
            'updated': now.isoformat(timespec='seconds'), 'signal_date': signal_date, 'latest': rec,
            'summary': summary, 'rule': fw.RULE_ID, 'round_trip': fw.ROUND_TRIP,
            'evaluations': ev.astype(object).where(ev.notna(), None).to_dict('records'),   # 未到期 → null
        }, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"[saved] {'新增' if added else '已有'} {signal_date} 的紀錄；共 {len(records)} 筆", file=sys.stderr)
    if args.send:
        from src.app.alerts.notify import send_discord
        webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
        if not webhook:
            print('[error] .env 未設定 DISCORD_WEBHOOK_URL，未推送')
            return 1
        send_discord(webhook, text)
        print('[sent] 已推送到 Discord')
    return 0


if __name__ == '__main__':
    sys.exit(main())
