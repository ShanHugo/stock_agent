"""本機 Ollama 講評：只把已算好的提醒文字濃縮成一兩句，不得產生新數字。

模型寫出的每個數字都必須出現在原始提醒文字中，否則整段丟棄（避免模型自己算錯或編造）。
"""
from __future__ import annotations
import re
import requests

PROMPT = (
    "以下是股票持股提醒，所有數字都已由程式算好。\n"
    "請用繁體中文寫 1–2 句總結今天最需要注意的事。\n"
    "規則：只能原樣引用下面出現過的數字，不要加總、換算或推測任何數字；"
    "「接近停損/停利」代表尚未觸及，不可寫成已觸及或已跌破；不要給買賣建議，也不要預測漲跌；新聞只能轉述標題，不要推測內容；不要加標題。\n\n{text}"
)

# 只有真的觸發時才允許的字眼（「接近」提醒若被寫成觸發，意思就錯了）
TRIGGER_WORDS = ('觸及', '觸發', '跌破', '突破', '已達', '達到')

_NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')


def _numbers(text: str) -> set[str]:
    return {n.replace(',', '').rstrip('.') for n in _NUM.findall(text)}


# 否定詞與觸發字眼之間可隔幾個字（例「沒有接近或觸發」），但不跨標點
_NEGATED = re.compile(r'(尚未|還沒|沒有|未|無)[^，。；、,.;]{0,6}?(' + '|'.join(TRIGGER_WORDS) + ')')


def wrong_trigger_words(narration: str, has_triggered: bool) -> list[str]:
    if has_triggered:
        return []
    plain = _NEGATED.sub('', narration)  # 「尚未觸及」是正確描述，不算
    return [w for w in TRIGGER_WORDS if w in plain]


def unsupported_numbers(narration: str, source: str) -> set[str]:
    allowed = _numbers(source)
    # 1~2 位小整數（例如「2 檔」「1 筆」）容易是計數，不檢查
    return {n for n in _numbers(narration) if n not in allowed and not (n.isdigit() and len(n) <= 2)}


class OllamaNarrator:
    def __init__(self, model: str = 'qwen3.5:9b', url: str = 'http://localhost:11434', timeout: int = 180):
        self.model, self.url, self.timeout = model, url.rstrip('/'), timeout

    def chat(self, prompt: str) -> str:
        r = requests.post(f'{self.url}/api/chat', timeout=self.timeout, json={
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'stream': False,
            'think': False,
            'options': {'temperature': 0.2},
        })
        r.raise_for_status()
        out = r.json()['message']['content'].strip()
        return re.sub(r'<think>.*?</think>', '', out, flags=re.S).strip().replace('\n', ' ')

    def summarize(self, text: str, has_triggered: bool = True, prompt: str = PROMPT) -> tuple[str | None, str | None]:
        """回傳 (講評, 錯誤原因)。prompt 需含 {text}。"""
        try:
            out = self.chat(prompt.format(text=text))
        except Exception as e:
            return None, f'Ollama 呼叫失敗: {e}'
        bad = unsupported_numbers(out, text)
        if bad:
            return None, f'講評含有提醒中沒有的數字 {sorted(bad)}，已捨棄：{out}'
        words = wrong_trigger_words(out, has_triggered)
        if words:
            return None, f'今天沒有觸發，講評卻寫了 {words}，已捨棄：{out}'
        return out or None, None
