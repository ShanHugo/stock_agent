"""組合盤前簡報：隔夜行情、行事曆、族群雷達與新聞。只陳述資訊，不產生買賣建議。

持股狀態與停損提醒由 14:30 的持股提醒負責，模擬帳戶的單由模擬帳戶頻道推送，簡報不重複列出。
"""
from __future__ import annotations
from datetime import date, datetime, timedelta

from ..alerts.portfolio import Portfolio
import json
from pathlib import Path

import requests

from . import sources as src
from . import news as news_mod
from . import sector_news

WEEKDAYS = '一二三四五六日'
LOOKAHEAD_DAYS = 7       # 美國行事曆往後看幾天
EXDIV_LOOKAHEAD = 14     # 除權息往後看幾天
DISCLAIMER = '_整理公開資訊，非投資建議；是否交易請自行判斷。持股狀態見 14:30 持股提醒，模擬帳戶見專用頻道。_'
US_MARKETS = ('^GSPC', '^IXIC', '^SOX', '^VIX', '^TNX', 'TSM')   # 美股收盤時間相同；匯率另計
LIMITATIONS = ['CPI、非農就業等 BLS 發布日無法自動取得（BLS 封鎖程式存取），請以 bls.gov 行事曆為準']


def build_briefing(portfolio: Portfolio, *, now: datetime, price_fetcher,
                   fetch_taifex=src.fetch_taifex, fetch_html=src.fetch_html,
                   fetch_twse=src.fetch_twse_openapi, fetch_feed=news_mod.fetch_feed,
                   fetch_cnyes=requests.get, include_news: bool = True,
                   radar_path: Path | None = Path('state/radar/latest.json'),
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
        b['taifex'] = src.taifex_night(fetch_taifex(today))
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
    errors.extend(scheduler_problems(scheduler_dir, news_mod.news_since(now).date()))
    b['sector_news'] = None
    if include_news:
        from ..lab.themes import load_code_themes
        b['sector_news'] = sector_news.collect(now, load_code_themes(), errors, get=fetch_cnyes)
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
        # 各檔最新日期可能不同（2026-10-23 早上 TSM 還停在 10/21，卻被當成 10/22 收盤），舊的要標出來
        us_date = max((v['date'] for k, v in m.items() if k in US_MARKETS), default='')

        def stale(sym):
            return f"（資料停在 {m[sym]['date'][5:].replace('-', '/')}，不是最新）" if m[sym]['date'] < us_date else ''
        lines += ['', f'**隔夜美股（{us_date} 收盤）**']
        for sym in ('^GSPC', '^IXIC', '^SOX', '^VIX'):
            if sym in m:
                lines.append(f"- {m[sym]['name']} {_num(m[sym]['close'])}（{_p(m[sym]['change_pct'])}）{stale(sym)}")
        if '^TNX' in m:
            t = m['^TNX']
            lines.append(f"- {t['name']} {t['close']:.2f}%（{(t['close'] - t['prev']) * 100:+.0f} 個基點）{stale('^TNX')}")
        if 'TWD=X' in m:
            lines.append(f"- 美元兌台幣 {m['TWD=X']['close']:.3f}（{_p(m['TWD=X']['change_pct'])}）")
        if 'TSM' in m:
            lines.append(f"- 台積電 ADR {_num(m['TSM']['close'])} USD（{_p(m['TSM']['change_pct'])}）{stale('TSM')}")
    if b.get('adr'):
        a = b['adr']
        lines.append(f"- ADR 換算每股 {a['per_share_twd']:,.1f} 台幣，比台股 2330 收盤 {a['tw_close']:,.0f} "
                     f"{'貴' if a['premium'] >= 0 else '便宜'} {abs(a['premium']) * 100:.1f}%（ADR 溢價，不是漲跌幅）"
                     f"{stale('TSM') if 'TSM' in m else ''}")

    tx = b.get('taifex')
    if tx and tx.get('day_close'):
        td = tx['date']
        if tx.get('night_close'):
            lines += ['', f"**台指期近月（{tx['contract']}，{td[4:6]}/{td[6:]} 日盤與之後的夜盤）**",
                      f"- 日盤收 {tx['day_close']:,.0f}，夜盤收 {tx['night_close']:,.0f}（相對日盤 {_p(tx['night_vs_day'])}）"]
        else:
            lines += ['', f"**台指期近月（{tx['contract']}，{td[4:6]}/{td[6:]} 日盤）**",
                      f"- 日盤收 {tx['day_close']:,.0f}；之後的夜盤資料期交所尚未公布"]

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

    lines += format_radar(b.get('radar'))
    lines += sector_news.format_section(b.get('sector_news'), src.TPE)
    if b.get('news'):
        lines += news_mod.format_news(b['news'], src.TPE)

    if b['errors']:
        lines += ['', '**⚠️ 抓取失敗**'] + [f'- {e}' for e in b['errors']]
    lines += ['', '_限制：' + '；'.join(b['limitations']) + '_', DISCLAIMER]
    return '\n'.join(lines)
