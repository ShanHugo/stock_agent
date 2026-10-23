#!/usr/bin/env python3
"""排程包裝程式（run_scheduled.cmd）每次執行完都呼叫：記錄結果到 state/scheduler/<名稱>.json；失敗時推送 Discord。

用法：python scripts/task_status.py <排程名稱> <結束代碼>
"""
from __future__ import annotations
import json, os, re, sys, pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv

STATE = _ROOT / 'state' / 'scheduler'
TAIL = 12
SECRET = re.compile(r'https?://\S*(webhook|token|key)\S*', re.I)


def last_run_lines(name: str) -> list[str]:
    """這次執行寫進 logs/<名稱>.log 的最後幾行（從最後一個 ===== 開始）。"""
    path = _ROOT / 'logs' / f'{name}.log'
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    start = max((i for i, l in enumerate(lines) if l.startswith('=====')), default=0)
    block = [l for l in lines[start + 1:] if l.strip() and not l.startswith('----- exit')]
    return [SECRET.sub('[網址已隱藏]', l)[:200] for l in block[-TAIL:]]


def main() -> int:
    if len(sys.argv) < 3:
        print('用法：task_status.py <排程名稱> <結束代碼>')
        return 2
    name, rc = sys.argv[1], int(sys.argv[2])
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f'{name}.json').write_text(json.dumps({'name': name, 'time': now.isoformat(timespec='seconds'), 'rc': rc},
                                                   ensure_ascii=False), encoding='utf-8')
    if rc == 0:
        return 0
    load_dotenv(_ROOT / '.env')
    # 測試時設 TASK_STATUS_NO_SEND=1 就只印出、不推送
    webhook = '' if os.getenv('TASK_STATUS_NO_SEND') == '1' else os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    tail = last_run_lines(name)
    text = (f'⚠️ **排程失敗：{name}**（{now:%m/%d %H:%M}，結束代碼 {rc}）\n'
            '最後幾行紀錄：\n```\n' + '\n'.join(tail or ['（沒有紀錄）']) + '\n```\n'
            f'完整紀錄：logs\\{name}.log。這次沒完成的工作不會自動補做（模擬帳戶例外：下次執行會補跑漏掉的交易日）。')
    print(text)
    if webhook:
        from src.app.alerts.notify import send_discord
        send_discord(webhook, text)
        print('[sent] 已推送失敗通知')
    return 0


if __name__ == '__main__':
    sys.exit(main())
