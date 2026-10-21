"""族群（記憶體、面板、IC 設計…）：依櫃買中心「產業價值鏈資訊平台」每家公司所屬的產業鏈分類。

來源：https://ic.tpex.org.tw/company_chain.php?stk_code=XXXX（上市、上櫃都有；資料由公司自行填報）
每家公司可屬於多個產業鏈節點，例如南亞科：半導體 > 記憶體IC、半導體 > DRAM製造、電腦及週邊設備 > 記憶體。
快取 90 天（data/raw/lab/ic_chain/）。
"""
from __future__ import annotations
import json
import re
import time
from html import unescape
from pathlib import Path

from ..research import sources as rs

CACHE = Path('data/raw/lab/ic_chain')

# 族群定義：產業鏈節點規則（chains = 整條產業鏈都算；nodes = 節點名稱含有任一字串；exclude = 排除含有的字串）
# 與新聞關鍵字（標題或鉅亨網關鍵字含有任一字串就歸到這個族群）
THEMES: dict[str, dict] = {
    '記憶體': {'nodes': ['記憶體', 'DRAM', 'Flash', 'NAND', '快閃'],
               'keywords': ['記憶體', 'DRAM', 'NAND', 'HBM', 'DDR5', 'DDR4', '快閃', '美光', '海力士', 'Flash', 'SSD', '模組廠']},
    '晶圓代工': {'nodes': ['晶圓製造', '晶圓代工'],
                 'keywords': ['晶圓代工', '先進製程', '成熟製程', '2奈米', '3奈米', '台積電', '聯電', '力積電', '世界先進', '格羅方德']},
    'IC設計': {'chain_nodes': {'半導體': ['IC', 'IP設計']}, 'exclude': ['記憶體', '封裝', '測試', '通路', '製造', '模組'],
               'keywords': ['IC設計', 'ASIC', '矽智財', '聯發科', '瑞昱', '聯詠', '晶片設計']},
    '封測': {'chain_nodes': {'半導體': ['封裝', '測試']},
             'keywords': ['封測', 'CoWoS', '先進封裝', 'SoIC', 'CoPoS', '日月光', '面板級封裝']},
    '半導體設備材料': {'chain_nodes': {'半導體': ['設備', '化學品', '光罩', '矽晶圓', '材料', '導線架', '基板']},
                       'keywords': ['半導體設備', '設備商', '矽晶圓', '光阻', '特化', '濕製程', '量測']},
    '面板': {'chain_nodes': {'平面顯示器': ['面板', '顯示器模組', '背光', '擴散膜', '稜鏡', 'ITO', '監視器'],
                            '觸控面板': ['觸控面板', '玻璃基板', 'ITO']},
             'keywords': ['面板', 'LCD', 'OLED', '顯示器', '群創', '友達', '彩晶', '觸控']},
    'PCB載板': {'chain_nodes': {'印刷電路板': ['印刷電路板', '硬板', '銅箔', '玻璃纖維', '聚亞醯胺']}, 'nodes': ['載板'],
                'keywords': ['PCB', '載板', 'ABF', 'BT載板', '銅箔基板', 'CCL', '電路板', '玻纖布']},
    '被動元件': {'chains': ['被動元件'], 'keywords': ['被動元件', 'MLCC', '電阻', '電容', '電感', '國巨', '華新科']},
    '伺服器電腦': {'nodes': ['伺服器', '筆記型電腦', '主機板', '工業電腦', '桌上型', '平板電腦', '系統組裝'],
                   'keywords': ['伺服器', 'GB200', 'GB300', 'Rubin', 'NVL', '筆電', '主機板', '工業電腦', '機櫃']},
    '散熱': {'nodes': ['散熱', '冷卻'], 'keywords': ['散熱', '液冷', '水冷', '均熱片', '風扇', '冷卻', '氣冷']},
    '網通': {'chain_nodes': {'通信網路': ['網路設備', '無線通訊設備', '有線通訊設備', '電信服務', '網路IC']},
             'keywords': ['網通', '交換器', 'Wi-Fi', '5G', '路由器', '低軌衛星', '衛星', '乙太網路']},
    '光通訊': {'nodes': ['光通訊', '光纖', '光收發', '光主動', '光被動', '雷射'],
               'keywords': ['光通訊', 'CPO', '矽光子', '光模組', '光收發', '800G', '1.6T', '光纖']},
    '連接器線材': {'nodes': ['連接器', '線材', '連接線', '電線電纜'], 'keywords': ['連接器', '線材', '高速傳輸線', '銅纜']},
    '電源': {'nodes': ['電源'], 'exclude': ['IC'], 'keywords': ['電源供應器', 'BBU', '備援電池', '高壓直流']},
    '機殼機構': {'nodes': ['機殼', '機構', '樞紐', '滑軌'], 'keywords': ['機殼', '機構件', '滑軌', '軸承']},
    'LED光電': {'chains': ['LED照明產業'], 'nodes': ['LED'], 'exclude': ['IC'], 'keywords': ['LED', 'Mini LED', 'Micro LED'],
                'not_keywords': ['OLED']},
    '光學鏡頭': {'nodes': ['鏡頭', '光學鏡片', '光學元件'], 'keywords': ['鏡頭', '光學', '大立光', '玉晶光']},
    '電子通路': {'nodes': ['IC通路', '通路經銷', '電子零組件通路'], 'keywords': ['通路商', '電子通路', '大聯大', '文曄']},
    '軟體資安': {'chains': ['軟體服務', '資通訊安全', '大數據', '電子商務'],
                 'keywords': ['資安', '軟體', 'SaaS', '系統整合', '雲端服務']},
    '綠能電池': {'chains': ['太陽能產業', '能源元件', '電動車輛產業', '智慧電網'], 'nodes': ['電池'],
                 'keywords': ['太陽能', '儲能', '電池', '充電樁', '電動車', '綠能']},
}


def themes_for(pairs: list[tuple[str, str]]) -> set[str]:
    """一家公司的產業鏈節點 → 所屬族群（可多個）。"""
    out = set()
    for name, rule in THEMES.items():
        ex = rule.get('exclude', [])
        for chain, node in pairs:
            if any(x in node for x in ex):
                continue
            if chain in rule.get('chains', []) or any(k in node for k in rule.get('nodes', [])) \
                    or any(k in node for k in rule.get('chain_nodes', {}).get(chain, [])):
                out.add(name)
                break
    return out


def news_themes(title: str, keywords: list[str], stock_codes: list[str], code_themes: dict[str, set[str]]) -> set[str]:
    """新聞 → 族群：先看標題與關鍵字是否命中族群字詞；都沒有時，才用新聞標記的個股所屬族群
    （只採用屬於 3 個以下族群的個股，避免鴻海這類橫跨多族群的公司把新聞灑到每個族群）。"""
    hay = (title + ' ' + ' '.join(keywords)).lower()
    out = set()
    for name, rule in THEMES.items():
        text = hay
        for nk in rule.get('not_keywords', []):
            text = text.replace(nk.lower(), ' ')
        if any(k.lower() in text for k in rule['keywords']):
            out.add(name)
    if out or len(stock_codes) > 4:      # 盤勢總整理類新聞一次標很多檔，不用個股歸類
        return out
    for c in stock_codes:
        t = code_themes.get(c, set())
        if 0 < len(t) <= 3:
            out |= t
    return out
MAX_AGE_DAYS = 90
URL = 'https://ic.tpex.org.tw/company_chain.php'


def parse_company_chain(html: str) -> list[tuple[str, str]]:
    """回傳 [(產業鏈, 節點)]，例如 [('半導體', '記憶體IC'), ('半導體', 'DRAM製造')]。"""
    text = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' | ', html))).replace('\xa0', ' ')
    start = text.find('所屬產業鏈如下')
    if start < 0:
        return []
    end = text.find('使用條款', start)
    seg = text[start:end if end > 0 else None]
    out = []
    for chain, node in re.findall(r'►\s*\|\s*\|\s*([^|>]+?)\s*\|\s*>\s*([^|]+?)\s*\|', seg):
        pair = (chain.strip(), node.strip())
        if pair not in out:
            out.append(pair)
    return out


def company_chain(code: str, *, refresh: bool = False) -> list[tuple[str, str]]:
    path = CACHE / f'{code}.json'
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < MAX_AGE_DAYS * 86400:
        return [tuple(x) for x in json.loads(path.read_text(encoding='utf-8'))]
    r = rs._get(URL, host='tpex_ic', params={'stk_code': code}, encoding='utf-8')
    pairs = parse_company_chain(r.text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pairs, ensure_ascii=False), encoding='utf-8')
    return pairs


def refresh_chains(codes: list[str], *, max_fetch: int = 40, log=print) -> int:
    """每天補一點：先抓還沒有分類的公司（新上市），再更新超過 90 天的舊資料。回傳實際抓了幾家。"""
    now = time.time()
    missing = [c for c in codes if not (CACHE / f'{c}.json').exists()]
    stale = sorted((c for c in codes if (CACHE / f'{c}.json').exists()
                    and now - (CACHE / f'{c}.json').stat().st_mtime > MAX_AGE_DAYS * 86400),
                   key=lambda c: (CACHE / f'{c}.json').stat().st_mtime)
    todo = (missing + stale)[:max_fetch]
    for c in todo:
        try:
            company_chain(c, refresh=True)
        except Exception as e:      # noqa: BLE001
            log(f'[ic] {c} 失敗：{e}')
    if todo:
        log(f'[ic] 產業鏈分類：新增 {min(len(missing), len(todo))} 家、更新 {max(0, len(todo) - len(missing))} 家'
            f'（尚缺 {max(0, len(missing) - len(todo))} 家、待更新 {max(0, len(stale) - max(0, len(todo) - len(missing)))} 家）')
    return len(todo)


def load_code_themes() -> dict[str, set[str]]:
    """讀取所有已快取的公司產業鏈 → {代號: 族群}（只讀快取，不連網）。"""
    out = {}
    for path in CACHE.glob('*.json'):
        pairs = [tuple(x) for x in json.loads(path.read_text(encoding='utf-8'))]
        t = themes_for(pairs)
        if t:
            out[path.stem] = t
    return out


def all_chains(codes: list[str], log=print) -> dict[str, list[tuple[str, str]]]:
    out = {}
    for i, c in enumerate(codes):
        try:
            out[c] = company_chain(c)
        except Exception as e:      # noqa: BLE001 — 單一公司失敗不影響其他
            log(f'[ic] {c} 失敗：{e}')
            out[c] = []
        if i % 50 == 0:
            log(f'[ic] 產業鏈 {c} ({i + 1}/{len(codes)})')
    return out
