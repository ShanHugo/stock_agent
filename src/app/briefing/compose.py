"""組合盤前簡報：整理影響因素與「依你自己規則」的狀態。只陳述資訊，不產生買賣建議。"""
from __future__ import annotations
from datetime import date, datetime, timedelta

from ..alerts.portfolio import Portfolio
from ..alerts.runner import run_alerts
from ..alerts.rules import TRIGGERED, NEAR_STOP, NEAR_TARGET
from ..alerts.notify import TITLES
import json
from pathlib import Path

import requests

from . import sources as src
from . import news as news_mod
from . import sector_news

WEEKDAYS = '一二三四五六日'
LOOKAHEAD_DAYS = 7       # 美國行事曆往後看幾天
EXDIV_LOOKAHEAD = 14     # 除權息往後看幾天
DISCLAIMER = '_整理公開資訊與你自己設定的規則狀態，非投資建議；是否交易請自行判斷。_'
LIMITATIONS = ['CPI、非農就業等 BLS 發布日無法自動取得（BLS 封鎖程式存取），請以 bls.gov 行事曆為準']


def build_briefing(portfolio: Portfolio, *, now: datetime, price_fetcher, default_tw_fee_bps: float,
                   fetch_taifex=src.fetch_taifex, fetch_html=src.fetch_html,
                   fetch_twse=src.fetch_twse_openapi, fetch_feed=news_mod.fetch_feed,
                   fetch_cnyes=requests.get, include_news: bool = True,
                   radar_path: Path | None = Path('state/radar/latest.json'),
                   paper_path: Path | None = Path('state/paper_bot/latest.json'),
                   scheduler_dir: Path | None = Path('state/scheduler')) -> dict:
    today = now.date()
    errors: list[str] = []
    b = {'date': today, 'errors': errors, 'limitations': list(LIMITATIONS)}

    b['markets'] = src.overnight_markets(price_fetcher, today, errors)
    try:
        tw = src.last_two_closes(price_fetcher, '2330.TW', today)
        b['adr'] = src.adr_premium(b['markets'], tw['close'] if tw else None)
    except Exception as e:
        b['adr'] = None
        errors.append(f'台積電 ADR 溢價: {e}')
    try:
        b['taifex'] = src.taifex_night(fetch_taifex())
    except Exception as e:
        b['taifex'] = None
        errors.append(f'台指期: {e}')

    events, b['next_fomc'] = [], None
    for name, url, parser in [('FOMC', 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm', src.parse_fomc),
                              ('BEA', 'https://www.bea.gov/news/schedule', src.parse_bea)]:
        try:
            for ev in parser(fetch_html(url)):
                at = src.to_taipei(ev['date'], ev['time_et'])
                if name == 'FOMC' and at.date() >= today and (b['next_fomc'] is None or at < b['next_fomc']['taipei']):
                    b['next_fomc'] = {'label': ev['label'], 'taipei': at}
                if today <= at.date() <= today + timedelta(days=LOOKAHEAD_DAYS):
                    events.append({'source': name, 'label': ev['label'], 'taipei': at})
        except Exception as e:
            errors.append(f'{name} 行事曆: {e}')
    b['us_events'] = sorted(events, key=lambda e: e['taipei'])

    codes = set(portfolio.watchlist) | {h.symbol for h in portfolio.holdings if not h.is_us}
    try:
        exdiv = src.parse_exdividend(fetch_twse('/exchangeReport/TWT48U_ALL'))
        b['exdividend'] = sorted([e for e in exdiv if e['code'] in codes
                                  and today <= e['date'] <= today + timedelta(days=EXDIV_LOOKAHEAD)],
                                 key=lambda e: e['date'])
    except Exception as e:
        b['exdividend'] = []
        errors.append(f'除權息預告: {e}')
    try:
        ann = src.parse_announcements(fetch_twse('/opendata/t187ap04_L'))
        b['announcements'] = [a for a in ann if a['code'] in codes]
    except Exception as e:
        b['announcements'] = []
        errors.append(f'重大訊息: {e}')

    b['news'] = news_mod.collect_news(portfolio, now, errors, fetch=fetch_feed) if include_news else None
    b['radar'] = load_radar(radar_path, today)
    b['paper_orders'] = load_paper_orders(paper_path)
    errors.extend(scheduler_problems(scheduler_dir, news_mod.news_since(now).date()))
    b['sector_news'] = None
    if include_news:
        from ..lab.themes import load_code_themes
        b['sector_news'] = sector_news.collect(now, load_code_themes(), errors, get=fetch_cnyes)

    # 依你自己的規則：沿用每日檢查的判斷，但不寫入 state（不影響 14:30 的檢查）
    res = run_alerts(portfolio, price_fetcher, today=today, default_tw_fee_bps=default_tw_fee_bps,
                     state_path=None, force=True, now=now)
    b['holdings'] = res['evaluations']
    b['rule_alerts'] = res['alerts']
    b['drawdowns'] = res['drawdowns']
    b['reviews'] = res.get('reviews', [])
    b['risk'] = res['risk'] if res.get('risk') and (res['risk']['total_risk_exceeded'] or res['risk']['oversized']) else None
    errors.extend(e for e in res['errors'] if e not in errors)
    return b


RADAR_SHOW = 5
RADAR_MAX_AGE_DAYS = 5


def load_radar(path: Path | None, today: date) -> dict | None:
    """前一個交易日收盤後算好的族群雷達（scripts/sector_radar.py）；太舊或不存在就不列。"""
    if not path or not path.exists():
        return None
    try:
        r = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (today - date.fromisoformat(r['date'])).days > RADAR_MAX_AGE_DAYS or not r.get('rows'):
        return None
    return r


DAILY_TASKS = {'paper_exec': '09:30 模擬帳戶成交', 'check_alerts': '14:30 持股提醒', 'forward_test': '16:30 前瞻測試',
               'sector_radar': '16:45 族群雷達', 'paper_bot': '16:50 模擬帳戶收盤'}


def scheduler_problems(state_dir: Path | None, prev_session: date) -> list[str]:
    """前一個交易日應該執行的排程：沒執行（例如電腦沒開）或執行失敗的，列出來。
    還沒有任何紀錄的排程（剛設定好）不算。"""
    if not state_dir or not state_dir.exists():
        return []
    out = []
    for name, label in DAILY_TASKS.items():
        path = state_dir / f'{name}.json'
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        ran = datetime.fromisoformat(s['time']).date()
        if ran < prev_session:
            out.append(f'排程沒有執行：{label}（最後一次 {ran:%m/%d}；電腦當時可能沒開）')
        elif s.get('rc'):
            out.append(f'排程失敗：{label}（{ran:%m/%d}，結束代碼 {s["rc"]}；詳見 logs\\{name}.log）')
    return out


def load_paper_orders(path: Path | None) -> list[dict]:
    """模擬帳戶前一天收盤後排定、今天 09:30 要執行的單（scripts/paper_bot.py 寫的 latest.json）。"""
    if not path or not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('pending', [])
    except (OSError, ValueError):
        return []


def format_paper_orders(orders: list[dict]) -> list[str]:
    if not orders:
        return []
    lines = ['', '**模擬帳戶今天 09:30 要執行（模擬單）**']
    for o in orders:
        side = '買進' if o['side'] == 'buy' else '賣出'
        extra = f"，停損約 {o['stop_est']:,.2f}" if o['side'] == 'buy' and o.get('stop_est') else ''
        lines.append(f"- {side} {o['code']} {o.get('name', '')} {o['shares']} 股（昨收 {o['ref_price']:,.2f}）：{o['reason']}{extra}")
    lines.append('_09:30 以即時報價成交後會推送到模擬帳戶頻道；模擬操作，不是買賣建議。_')
    return lines


def format_radar(r: dict | None) -> list[str]:
    if not r:
        return []
    d = r['date']
    lines = ['', f"**族群雷達（{d[5:7]}/{d[8:]} 收盤；依營收年增、近 60 日漲跌、量能、融資增減排序）**"]
    for row in r['rows'][:RADAR_SHOW]:
        lines.append(f"- {row['rank']}. {row['theme']}（{int(row['members'])} 檔）：營收年增中位數 {row['rev_yoy']:+.1f}%、"
                     f"連 2 月年增 {row['rev_breadth']:.0%}、近 60 日 {_p(row['r60'], 1)}、站上 120 日線 {row['above_ma']:.0%}")
    last = r['rows'][-1]
    lines.append(f"- 排名最後：{last['theme']}（營收年增中位數 {last['rev_yoy']:+.1f}%、近 60 日 {_p(last['r60'], 1)}）")
    bt = (r.get('backtest') or {}).get('20')
    if bt:
        lines.append(f"_歷史驗證（{bt['periods']} 期）：前 3 名族群每 20 天平均比 0050 {_p(bt['top_vs_bench'])}"
                     f"（t 值 {bt['top_vs_bench_t']:+.1f}，贏的期數 {bt['top_beats_bench']:.0%}）；"
                     f"比後 3 名 {_p(bt['top_vs_bottom'])}。排名是資料整理，不是買賣建議。_")
    else:
        lines.append('_排名是資料整理，尚未完成歷史驗證，不是買賣建議。_')
    return lines


def _p(v, digits=2):
    return '—' if v is None else f'{v:+.{digits}%}'


def _num(v):
    return f'{v:,.2f}'.rstrip('0').rstrip('.')


def format_briefing(b: dict, narration: str | None = None) -> str:
    d: date = b['date']
    lines = [f"## 盤前簡報 {d.isoformat()}（週{WEEKDAYS[d.weekday()]}）"]
    if narration:
        lines += [f'> {narration}']

    m = b['markets']
    if m:
        us_date = next((v['date'] for k, v in m.items() if k in ('^GSPC', '^IXIC')), '')
        lines += ['', f'**隔夜美股（{us_date} 收盤）**']
        for sym in ('^GSPC', '^IXIC', '^SOX', '^VIX'):
            if sym in m:
                lines.append(f"- {m[sym]['name']} {_num(m[sym]['close'])}（{_p(m[sym]['change_pct'])}）")
        if '^TNX' in m:
            t = m['^TNX']
            lines.append(f"- {t['name']} {t['close']:.2f}%（{(t['close'] - t['prev']) * 100:+.0f} 個基點）")
        if 'TWD=X' in m:
            lines.append(f"- 美元兌台幣 {m['TWD=X']['close']:.3f}（{_p(m['TWD=X']['change_pct'])}）")
        if 'TSM' in m:
            lines.append(f"- 台積電 ADR {_num(m['TSM']['close'])} USD（{_p(m['TSM']['change_pct'])}）")
    if b.get('adr'):
        a = b['adr']
        lines.append(f"- ADR 換算每股 {a['per_share_twd']:,.1f} 台幣，相對台股 2330 收盤 {a['tw_close']:,.0f}："
                     f"{_p(a['premium'], 1)}")

    tx = b.get('taifex')
    if tx and tx.get('night_close'):
        td = tx['date']
        lines += ['', f"**台指期近月（{tx['contract']}，{td[4:6]}/{td[6:]} 日盤與夜盤）**",
                  f"- 日盤收 {tx['day_close']:,.0f}，夜盤收 {tx['night_close']:,.0f}（相對日盤 {_p(tx['night_vs_day'])}）"]

    lines += ['', f'**行事曆（未來 {LOOKAHEAD_DAYS} 天，台北時間）**']
    def when(t):
        return f"{t:%m/%d}（{WEEKDAYS[t.weekday()]}）{t:%H:%M}"
    if b['us_events']:
        for e in b['us_events']:
            lines.append(f"- {when(e['taipei'])} {e['label']}")
    else:
        lines.append(f'- 未來 {LOOKAHEAD_DAYS} 天沒有 FOMC 或 BEA 發布')
    nf = b.get('next_fomc')
    if nf and not any(e['source'] == 'FOMC' for e in b['us_events']):
        lines.append(f"- 下一次 {nf['label']}：{when(nf['taipei'])}")
    if b.get('exdividend'):
        for e in b['exdividend']:
            cash = f"，現金 {e['cash']:g} 元" if e['cash'] else ''
            lines.append(f"- {e['date']:%m/%d} {e['code']} {e['name']} 除{e['kind']}{cash}（你的持股/觀察清單）")
    else:
        lines.append(f'- 你的台股持股與觀察清單未來 {EXDIV_LOOKAHEAD} 天沒有除權息')
    for a in b.get('announcements', []):
        lines.append(f"- 重大訊息 {a['code']} {a['name']}：{a['subject']}")

    lines += format_paper_orders(b.get('paper_orders', []))
    lines += format_radar(b.get('radar'))
    lines += sector_news.format_section(b.get('sector_news'), src.TPE)
    if b.get('news'):
        lines += news_mod.format_news(b['news'], src.TPE)

    if b['holdings']:
        lines += ['', '**你的持股（最近收盤）**']
        for ev in b['holdings']:
            extra = []
            if ev.get('stop_level'):
                extra.append(f"距停損 {1 - ev['stop_level'] / ev['close']:.1%}")
            if ev.get('target_level'):
                extra.append(f"距停利 {ev['target_level'] / ev['close'] - 1:.1%}")
            lines.append(f"- {ev['label']} {_num(ev['close'])}（{ev['date']}），未實現 {ev['unrealized_pct']:+.1%}"
                         + (f"，{'、'.join(extra)}" if extra else ''))

    lines += ['', '**依你設定的規則，今天要注意**']
    notes = []
    for a in b['rule_alerts']:
        title = TITLES.get(a['kind'], a['kind'])
        if a['kind'] in TRIGGERED:
            notes.append(f"- {title}｜{a['label']}：收盤 {_num(a['close'])}，依你的規則動作為賣出")
        elif a['kind'] in (NEAR_STOP, NEAR_TARGET):
            lvl = a['stop_level'] if a['kind'] == NEAR_STOP else a['target_level']
            notes.append(f"- {title}｜{a['label']}：收盤 {_num(a['close'])}，規則價位 {_num(lvl)}")
    for dd in b['drawdowns']:
        notes.append(f"- 從高點回落｜{dd['label']}：已回落 {dd['drawdown']:.1%}（超過你設的 {dd['level']:.0%}）")
    for r in b['reviews']:
        notes.append(f"- 理由待確認｜{r['label']}：{r['days']} 天未確認（{r['thesis']}）")
    if b.get('risk'):
        notes.append('- 部位風險超過你設的上限（詳見收盤後提醒）')
    lines += notes or ['- 沒有接近或觸發你設定的任何規則']

    if b['errors']:
        lines += ['', '**⚠️ 抓取失敗**'] + [f'- {e}' for e in b['errors']]
    lines += ['', '_限制：' + '；'.join(b['limitations']) + '_', DISCLAIMER]
    return '\n'.join(lines)


def has_triggered(b: dict) -> bool:
    return any(a['kind'] in TRIGGERED for a in b['rule_alerts'])
