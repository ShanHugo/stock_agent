"""族群新聞：鉅亨網台股新聞與頭條，依族群（記憶體、面板、IC 設計…）歸類，再由本機模型為每個族群寫 1–2 句整理。

- 族群歸類：標題／鉅亨網關鍵字命中族群字詞，或新聞標記的個股屬於該族群（lab.themes）。
- 整理只能根據新聞標題與內文摘錄；整理中出現新聞裡沒有的數字、或出現買賣建議字眼時整段捨棄，只列標題。
- 每個族群附原文連結；內文不轉載。
"""
from __future__ import annotations
import ast
import html
import re
from datetime import datetime, timedelta, timezone

import requests

from ..alerts.narrate import unsupported_numbers
from ..lab.themes import news_themes

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}
CNYES = 'https://api.cnyes.com/media/api/v1/newslist/category/{cat}'
CATEGORIES = ('tw_stock', 'headline')
MAX_PAGES = 15           # 週一要涵蓋整個週末，頁數放寬（遇到早於時間窗的新聞就停）
MAX_THEMES = 6           # 簡報最多列幾個族群
MIN_ITEMS = 2            # 族群至少要有幾則新聞才列出
PER_THEME_LINKS = 3
PROMPT_ITEMS = 6         # 每個族群最多拿幾則新聞給模型
EXCERPT = 220            # 每則內文摘錄字數
ADVICE_WORDS = ('建議', '買進', '賣出', '加碼', '減碼', '布局', '進場', '出場', '值得投資', '可望大漲', '看好')

THEME_PROMPT = (
    "以下是「{theme}」族群最近的新聞標題與內文摘錄。\n"
    "請用繁體中文寫 1–2 句（合計 80 字以內），整理這個族群發生了什麼事。\n"
    "規則：只能根據下面的新聞內容；數字只能原樣引用下面出現過的；不要預測股價漲跌；不要給任何買賣建議；"
    "不要加標題或條列。\n\n{text}"
)


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.startswith('['):
        try:
            return [str(x) for x in ast.literal_eval(v)]
        except (ValueError, SyntaxError):
            return []
    return []


def parse_item(d: dict) -> dict:
    text = re.sub(r'<[^>]+>', ' ', html.unescape(html.unescape(d.get('content') or '')))
    text = re.sub(r'\s+', ' ', text).strip()
    stocks = []
    for s in _as_list(d.get('stock')):
        m = re.match(r'^(?:TWS:|TWG:)?(\d{4,6})', s)
        if m:
            stocks.append(m.group(1))
    return {
        'id': d.get('newsId'),
        'title': re.sub(r'\s+', ' ', d.get('title') or '').strip(),
        'link': f"https://news.cnyes.com/news/id/{d.get('newsId')}",
        'published': datetime.fromtimestamp(int(d.get('publishAt') or 0), tz=timezone.utc),
        'keywords': _as_list(d.get('keyword')),
        'stocks': stocks,
        'excerpt': text[:EXCERPT],
        'source': '鉅亨網',
    }


def fetch_cnyes(since: datetime, get=requests.get) -> list[dict]:
    seen, out = set(), []
    for cat in CATEGORIES:
        for page in range(1, MAX_PAGES + 1):
            r = get(CNYES.format(cat=cat), params={'limit': 30, 'page': page}, headers=UA, timeout=30)
            r.raise_for_status()
            data = r.json().get('items', {}).get('data', [])
            items = [parse_item(d) for d in data]
            for i in items:
                if i['published'] >= since and i['id'] not in seen:
                    seen.add(i['id'])
                    out.append(i)
            if not items or min(i['published'] for i in items) < since:
                break
    return out


# 盤勢速報、盤前／盤後彙整：一則講很多事，歸到單一族群會誤導，不列入族群新聞
DIGEST = re.compile(r'盤中速報|盤前要聞|盤後要聞|開盤〉|收盤〉|盤後〉|〈盤前|〈盤後|盤勢|早盤|尾盤|今日焦點|焦點新聞')


def group_by_theme(items: list[dict], code_themes: dict[str, set[str]]) -> list[dict]:
    """[{theme, items（新到舊、標題去重）}]，依新聞數由多到少。"""
    groups: dict[str, list[dict]] = {}
    for it in items:
        if DIGEST.search(it['title']):
            continue
        for t in news_themes(it['title'], it['keywords'], it['stocks'], code_themes):
            groups.setdefault(t, []).append(it)
    out = []
    for theme, its in groups.items():
        uniq, keys = [], set()
        for i in sorted(its, key=lambda x: x['published'], reverse=True):
            k = re.sub(r'\W', '', i['title'])[:30]
            if k not in keys:
                keys.add(k)
                uniq.append(i)
        out.append({'theme': theme, 'items': uniq})
    return sorted(out, key=lambda g: -len(g['items']))


_UNIT_NUM = re.compile(r'(\d[\d,]*(?:\.\d+)?)\s*(?=%|％|倍|億|萬|元|美元|奈米|點|成|個百分點)')


def unsupported_in_news(out: str, src: str) -> set[str]:
    """比一般講評更嚴：帶單位的數字（15%、3 倍、200 億…）即使只有 1～2 位也必須出現在新聞裡。"""
    norm = lambda s: s.replace(',', '')
    allowed = {norm(n) for n in re.findall(r'\d[\d,]*(?:\.\d+)?', src)}
    bad = unsupported_numbers(out, src)
    bad |= {norm(n) for n in _UNIT_NUM.findall(out) if norm(n) not in allowed}
    return bad


def theme_source_text(g: dict) -> str:
    return '\n'.join(f"- 標題：{i['title']}\n  摘錄：{i['excerpt']}" for i in g['items'][:PROMPT_ITEMS])


def summarize_theme(g: dict, chat) -> tuple[str | None, str | None]:
    """chat(prompt) -> str。回傳 (整理, 捨棄原因)。"""
    src = theme_source_text(g)
    try:
        out = chat(THEME_PROMPT.format(theme=g['theme'], text=src)).strip()
    except Exception as e:      # noqa: BLE001
        return None, f'{g["theme"]}：模型呼叫失敗（{e}）'
    if not out:
        return None, None
    bad = unsupported_in_news(out, src)
    if bad:
        return None, f'{g["theme"]}：整理含新聞沒有的數字 {sorted(bad)}，已改列標題'
    words = [w for w in ADVICE_WORDS if w in out]
    if words:
        return None, f'{g["theme"]}：整理出現建議字眼 {words}，已改列標題'
    return out, None


def collect(now: datetime, code_themes: dict[str, set[str]], errors: list[str], *, since: datetime | None = None,
            get=requests.get) -> dict | None:
    from .news import news_since
    since = since or news_since(now)          # 前一個交易日 14:30 之後（週一含整個週末）
    try:
        items = fetch_cnyes(since, get=get)
    except Exception as e:      # noqa: BLE001
        errors.append(f'鉅亨網新聞: {e}')
        return None
    groups = [g for g in group_by_theme(items, code_themes) if len(g['items']) >= MIN_ITEMS][:MAX_THEMES]
    return {'since': since, 'total': len(items), 'groups': groups}


def add_summaries(sn: dict | None, chat, warnings: list[str]) -> None:
    if not sn:
        return
    for g in sn['groups']:
        g['summary'], why = summarize_theme(g, chat)
        if why:
            warnings.append(why)


def format_section(sn: dict | None, tz) -> list[str]:
    if not sn or not sn['groups']:
        return []
    lines = ['', f"**族群新聞（鉅亨網 {sn['since'].astimezone(tz):%m/%d %H:%M} 之後，共 {sn['total']} 則）**"]
    for g in sn['groups']:
        head = f"- **{g['theme']}**（{len(g['items'])} 則）"
        lines.append(head + (f"：{g['summary']}" if g.get('summary') else ''))
        for i in g['items'][:PER_THEME_LINKS]:
            lines.append(f"  - [{i['title']}](<{i['link']}>) {i['published'].astimezone(tz):%m/%d %H:%M}")
    lines.append('_族群整理由本機模型依新聞內容摘要，可能不完整；新聞多不代表會漲，請點連結閱讀原文。_')
    return lines
