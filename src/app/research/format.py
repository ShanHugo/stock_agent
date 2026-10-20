"""研究卡輸出成文字（終端機與 Discord 共用的 Markdown）。只陳述數據，不給買賣結論。"""
from __future__ import annotations


def _p(v, digits=1, signed=True):
    if v is None:
        return '—'
    return f'{v:+.{digits}%}' if signed else f'{v:.{digits}%}'


def _n(v, digits=2):
    return '—' if v is None else f'{v:,.{digits}f}'


def _pp(v):
    return '—' if v is None else f'{v * 100:+.1f} 個百分點'


def _rank(p):
    return '—' if p is None else f'{p:.0%} 分位'


def format_card(c: dict) -> str:
    lines = [f"## {c['code']} {c.get('name', '')} 研究卡（{c['as_of']}）"]

    r = c.get('revenue')
    if r:
        streak = r['yoy_streak']
        capped = '至少' if abs(streak) >= r['months'] else ''  # 受回看月數限制
        streak_s = f"{capped}連續 {abs(streak)} 個月年{'增' if streak > 0 else '減'}" if streak else '年增率持平'
        lines += ['', f"**月營收｜{r['month']}**",
                  f"營收 {r['revenue_yi']:,.1f} 億，月增 {_n(r['mom_pct'], 1)}%，年增 {_n(r['yoy_pct'], 1)}%，"
                  f"累計年增 {_n(r['cum_yoy_pct'], 1)}%（{streak_s}）",
                  f"近 3 個月合計年增 {_p(r['yoy_3m'])}，前 3 個月 {_p(r['yoy_3m_prev'])}"
                  + ('｜創 12 個月新高' if r['is_12m_high'] else '') + ('｜12 個月新低' if r['is_12m_low'] else '')]
        if r['note']:
            lines.append(f"公司說明：{r['note']}")

    i = c.get('income')
    if i:
        lt = i['latest']
        lines += ['', f"**季度獲利｜{lt['quarter']}（單季）**",
                  f"營收 {_n(lt['revenue_yi'], 1)} 億（季增 {_p(i['qoq_revenue'])}、年增 {_p(i['yoy_revenue'])}），"
                  f"EPS {_n(lt['eps'])}（年增 {_p(i['yoy_eps'])}），近四季 EPS {_n(i['eps_ttm'])}",
                  f"毛利率 {_p(lt['gross_margin'], signed=False)}（較上季 {_pp(i['gm_change_qoq'])}、"
                  f"較去年同季 {_pp(i['gm_change_yoy'])}），營益率 {_p(lt['op_margin'], signed=False)}"
                  f"（較去年同季 {_pp(i['om_change_yoy'])}）",
                  '近五季：' + '｜'.join(f"{x['quarter']} 毛利 {_p(x['gross_margin'], signed=False)} EPS {_n(x['eps'])}"
                                       for x in i['rows'])]

    v = c.get('valuation')
    if v:
        lines += ['', f"**估值｜{v['date']}（相對 {v['from']} 以來 {v['days']} 個交易日）**",
                  f"本益比 {_n(v['pe'])}（{_rank(v['pe_pctile'])}），股價淨值比 {_n(v['pb'])}（{_rank(v['pb_pctile'])}），"
                  f"殖利率 {_n(v['dividend_yield'])}%（{_rank(v['dividend_yield_pctile'])}）"]
        if v.get('pe_range'):
            lo, med, hi = v['pe_range']
            lines.append(f"本益比區間：最低 {lo:.1f}、中位數 {med:.1f}、最高 {hi:.1f}")
        lines.append('_分位 = 目前數值在這段期間的相對位置，高不代表一定會跌，低也不代表一定會漲_')

    ch = c.get('chips')
    if ch:
        n = ch['days']
        lines += ['', f"**籌碼｜{ch['from']} ~ {ch['to']}（{n} 個交易日，單位：張）**",
                  f"外資 近5日 {ch['foreign_5d']:+,.0f}／近{n}日 {ch[f'foreign_{n}d']:+,.0f}"
                  f"（連續 {abs(ch['foreign_streak'])} 日{'買超' if ch['foreign_streak'] > 0 else '賣超'}）",
                  f"投信 近5日 {ch['trust_5d']:+,.0f}／近{n}日 {ch[f'trust_{n}d']:+,.0f}"
                  f"（連續 {abs(ch['trust_streak'])} 日{'買超' if ch['trust_streak'] > 0 else '賣超'}）",
                  f"自營商 近5日 {ch['dealer_5d']:+,.0f}／近{n}日 {ch[f'dealer_{n}d']:+,.0f}"]
        m = ch.get('margin')
        if m:
            lines.append(f"融資餘額 {m['balance']:,.0f} 張，{n} 日變化 {m['change']:+,.0f}（{_p(m['change_pct'])}）")
        lines.append('_法人買賣超不等於之後一定漲跌；自營商部分含避險部位_')

    pr = c.get('price')
    if pr:
        ma = '、'.join(f"{k} 日線 {_p(v)}" for k, v in pr['vs_ma'].items())
        lines += ['', f"**價格｜{pr['date']}**",
                  f"收盤 {pr['close']:,.2f}，相對均線：{ma}",
                  f"52 週高 {pr['high_52w']:,.2f}（距離 {_p(pr['from_high'])}）、低 {pr['low_52w']:,.2f}（距離 {_p(pr['from_low'])}）"]

    if c.get('errors'):
        lines += ['', '**⚠️ 缺少的資料**'] + [f'- {e}' for e in c['errors']]
    lines += ['', '_資料：公開資訊觀測站、臺灣證券交易所、Yahoo Finance。僅整理數據，非投資建議。_']
    return '\n'.join(lines)
