"""模擬帳戶評估成績單：事先訂好的照抄標準，與「歷史模擬中同樣長度期間」的正常範圍比較。

標準在 2026-10-22（模擬帳戶第一筆成交當天、還沒有任何結果前）訂定並提交到 git；之後不得為了配合結果而修改。
規則若再修改，評估期從修改日重新起算（見 RULE_HISTORY 與 evaluation_start）。
這是模擬帳戶的評估，不是買賣建議。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CRITERIA_DECIDED_ON = '2026-10-22'
CRITERIA = {
    'min_trading_days': 120,          # 約 6 個月
    'min_closed_trades': 20,
    'must_beat_0050': True,           # 扣成本後總報酬高於同期 0050
    'mdd_percentile_floor': 0.10,     # 最大回撤不得比歷史同長度期間最差的 10% 還差
}
WARNING_PERCENTILE = 0.05             # 報酬或贏 0050 的幅度低於歷史同長度期間的 5% → 警訊

# 規則版本（日期＝開始生效的收盤；評估期從最新版本起算）
RULE_HISTORY = [
    {'version': 'v1', 'date': '2026-10-21', 'desc': '營收動能進場；停損 10%、跌破 120 日線、營收連 2 月年減出場'},
    {'version': 'v2', 'date': '2026-10-21', 'desc': '加入方案 E：收盤漲到成本 +20% 後停損移到成本價'},
    {'version': 'v3', 'date': '2026-10-21', 'desc': '09:30 依零股五檔成交（漲跌停無法成交）；同一族群最多 2 檔'
                                                     '（10/22 以 10/21 收盤重新排單後生效）'},
]


def evaluation_start(account_start: str) -> str:
    return max(account_start, RULE_HISTORY[-1]['date'])


def _mdd(s: pd.Series) -> float:
    return float((s / s.cummax() - 1).min()) if len(s) else 0.0


def window_stats(curve: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """歷史模擬中所有長度為 n_days 個交易日（含起點，共 n_days 筆淨值）的期間：報酬、0050 報酬、超額、最大回撤。"""
    eq, b = curve['equity'].to_numpy(), curve['bench'].to_numpy()
    if n_days < 2 or n_days > len(eq):
        return pd.DataFrame(columns=['ret', 'bench', 'excess', 'mdd'])
    step = max(1, n_days // 20)                        # 期間很長時抽樣起點，避免算太久
    rows = []
    for i in range(0, len(eq) - n_days + 1, step):
        e, bb = eq[i:i + n_days], b[i:i + n_days]
        rows.append({'ret': e[-1] / e[0] - 1, 'bench': bb[-1] / bb[0] - 1,
                     'mdd': float((e / np.maximum.accumulate(e) - 1).min())})
    df = pd.DataFrame(rows)
    df['excess'] = df['ret'] - df['bench']
    return df


def evaluate(view: dict, curve: pd.DataFrame, trades: list[dict]) -> dict:
    """view：state/paper_bot/latest.json；curve：歷史模擬每日淨值（date, equity, bench）；trades：帳戶全部交易。"""
    start = evaluation_start(view['start'])
    live = pd.DataFrame(view['curve'])
    live = live[live['date'] >= start].reset_index(drop=True)
    n = len(live)
    sells = [t for t in trades if t['side'] == 'sell' and t['date'] >= start]
    out = {'start': start, 'end': view['last_date'], 'trading_days': n, 'closed_trades': len(sells),
           'criteria': CRITERIA, 'criteria_decided_on': CRITERIA_DECIDED_ON, 'rule_version': RULE_HISTORY[-1]}
    if n >= 1:
        eq, b = live['equity'].astype(float), live['bench'].astype(float)
        out.update(ret=float(eq.iloc[-1] / eq.iloc[0] - 1), bench=float(b.iloc[-1] / b.iloc[0] - 1),
                   mdd=_mdd(eq), equity=float(eq.iloc[-1]))
        out['excess'] = out['ret'] - out['bench']
    if sells:
        rets = [t['return'] for t in sells]
        out.update(win_rate=float(np.mean([r > 0 for r in rets])), avg_win=float(np.mean([r for r in rets if r > 0] or [0])),
                   avg_loss=float(np.mean([r for r in rets if r <= 0] or [0])))
    hist = window_stats(curve, n) if n >= 2 else pd.DataFrame()
    if not hist.empty:
        q = lambda col: {p: float(hist[col].quantile(p)) for p in (0.05, 0.1, 0.5, 0.9)}
        out['history'] = {'windows': len(hist), 'ret': q('ret'), 'excess': q('excess'), 'mdd': q('mdd'),
                          'beat_0050_share': float((hist['excess'] > 0).mean())}
        out['percentile'] = {'ret': float((hist['ret'] < out['ret']).mean()),
                             'excess': float((hist['excess'] < out['excess']).mean()),
                             'mdd': float((hist['mdd'] < out['mdd']).mean())}   # 越大越好（回撤越淺）
    # 照抄條件
    c = CRITERIA
    checks = [
        ('time', f"模擬滿 {c['min_trading_days']} 個交易日（約 6 個月）", n >= c['min_trading_days'],
         f"{n}／{c['min_trading_days']}"),
        ('trades', f"完成交易至少 {c['min_closed_trades']} 筆", len(sells) >= c['min_closed_trades'],
         f"{len(sells)}／{c['min_closed_trades']}"),
        ('beat', '扣成本後總報酬贏過同期 0050', n >= 2 and out.get('excess', 0) > 0,
         f"{out.get('excess', 0):+.1%}" if n >= 2 else '—'),
        ('mdd', f"最大回撤不比歷史同長度期間最差的 {c['mdd_percentile_floor']:.0%} 還差",
         'percentile' in out and out['percentile']['mdd'] >= c['mdd_percentile_floor'],
         f"{out.get('mdd', 0):.1%}" + (f"（歷史第 {out['percentile']['mdd']:.0%} 百分位）" if 'percentile' in out else '')),
    ]
    out['checks'] = [{'key': k, 'label': lab, 'ok': bool(ok), 'value': v} for k, lab, ok, v in checks]
    warn = []
    if 'percentile' in out and n >= 20:
        if out['percentile']['excess'] < WARNING_PERCENTILE:
            warn.append(f"贏 0050 的幅度低於歷史同長度期間的 {WARNING_PERCENTILE:.0%}（第 {out['percentile']['excess']:.0%} 百分位）")
        if out['percentile']['mdd'] < WARNING_PERCENTILE:
            warn.append(f"回撤比歷史同長度期間 {1 - WARNING_PERCENTILE:.0%} 的情況還深")
    out['warnings'] = warn
    if warn:
        out['verdict'], out['verdict_text'] = 'warning', '警訊：表現明顯低於歷史預期，建議檢討規則是否失效'
    elif all(x['ok'] for x in out['checks']):
        out['verdict'], out['verdict_text'] = 'pass', '達標：可以考慮用小額資金照抄'
    else:
        out['verdict'], out['verdict_text'] = 'accumulating', '還在累積：樣本或時間不足，現在的成績主要是運氣，不要據此判斷'
    return out


def pct(v, d=1):
    return '—' if v is None or v != v else f'{v:+.{d}%}'


def render(sc: dict, month: str | None = None) -> str:
    title = f'模擬帳戶成績單（{month}）' if month else '模擬帳戶成績單'
    L = [f"## {title}", f"評估期 {sc['start']} ～ {sc['end']}，{sc['trading_days']} 個交易日；規則 {sc['rule_version']['version']}",
         '', f"**結論：{sc['verdict_text']}**"]
    L += [f'- ⚠️ {w}' for w in sc['warnings']]
    if 'ret' in sc:
        L += ['', f"帳戶 {pct(sc['ret'])}，同期 0050 {pct(sc['bench'])}，差距 {pct(sc['excess'])}；最大回撤 {sc['mdd']:.1%}"]
    if sc.get('win_rate') is not None:
        L.append(f"完成交易 {sc['closed_trades']} 筆，勝率 {sc['win_rate']:.0%}，賺的平均 {pct(sc['avg_win'])}、賠的平均 {pct(sc['avg_loss'])}")
    h = sc.get('history')
    if h:
        p = sc['percentile']
        L += ['', f"**跟歷史比**（2018 年起歷史模擬中所有同樣 {sc['trading_days']} 個交易日的期間，共 {h['windows']} 段）",
              f"- 報酬：正常範圍 {pct(h['ret'][0.1])} ～ {pct(h['ret'][0.9])}（中位數 {pct(h['ret'][0.5])}），這次落在第 {p['ret']:.0%} 百分位",
              f"- 贏 0050：正常範圍 {pct(h['excess'][0.1])} ～ {pct(h['excess'][0.9])}，歷史上 {h['beat_0050_share']:.0%} 的期間有贏；這次第 {p['excess']:.0%} 百分位",
              f"- 最大回撤：歷史中位數 {h['mdd'][0.5]:.1%}、最差 10% 約 {h['mdd'][0.1]:.1%}；這次第 {p['mdd']:.0%} 百分位（越高越好）"]
    L += ['', f"**照抄條件**（{sc['criteria_decided_on']} 事先訂定，四項都要達成）"]
    L += [f"- {'✅' if c['ok'] else '⬜'} {c['label']}：{c['value']}" for c in sc['checks']]
    L.append('_模擬帳戶的評估，不是買賣建議。規則若修改，評估期從修改日重新起算。_')
    return '\n'.join(L)
