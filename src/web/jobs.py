"""網頁操作台的背景工作：一律呼叫 scripts/ 底下既有的指令（與命令列行為完全相同），參數在伺服器端逐一檢查。

不接受任意指令：只有 COMMANDS 列出的工作，每個參數都經過格式檢查後以 list 傳給 subprocess（不經過 shell）。
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable

SYMBOL = re.compile(r'^[0-9A-Za-z][0-9A-Za-z.\-]{0,11}$')
TW_CODE = re.compile(r'^\d{4,6}[A-Z]?$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MONTH = re.compile(r'^\d{4}-\d{2}$')
INVALID = re.compile(r'^[a-z_]+(:[0-9.\-]+){1,3}$')
PLAN_ID = re.compile(r'^[0-9A-Za-z_\-]{1,40}$')
LAB_SCRIPTS = {'shortterm': 'shortterm_lab.py', 'strategy': 'strategy_lab.py'}
LAB_UNIVERSES = ('electronics', 'electronics-twse', 'electronics-tpex', 'tw50-pit')


class BadRequest(ValueError):
    pass


def _num(v, name, *, positive=True) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise BadRequest(f'{name} 必須是數字')
    if positive and x <= 0:
        raise BadRequest(f'{name} 必須大於 0')
    return f'{x:g}'


def _match(v, pattern: re.Pattern, name: str) -> str:
    v = str(v or '').strip()
    if not pattern.fullmatch(v):
        raise BadRequest(f'{name} 格式不正確：{v!r}')
    return v


def _text(v, name, limit) -> str:
    v = str(v or '').strip()
    if not v:
        raise BadRequest(f'請填寫{name}')
    if len(v) > limit or any(c in v for c in '\r\n\0'):
        raise BadRequest(f'{name}過長或含換行')
    return v


def build_command(kind: str, p: dict) -> tuple[list[str], str]:
    """回傳 (argv, 顯示用標題)。p 為前端送來的參數。"""
    s = lambda name: str(ROOT / 'scripts' / name)
    send = bool(p.get('send'))
    if kind == 'morning_brief':
        cmd = [s('morning_brief.py')] + ([] if send else ['--dry-run'])
        cmd += ['--force'] * bool(p.get('force')) + ['--no-narrate'] * bool(p.get('no_narrate'))
        return cmd, '盤前簡報' + ('（推送）' if send else '（預覽）')
    if kind == 'check_alerts':
        cmd = [s('check_alerts.py')] + ([] if send else ['--dry-run'])
        cmd += ['--force'] * bool(p.get('force')) + ['--test'] * bool(p.get('test'))
        cmd += ['--no-narrate'] * bool(p.get('no_narrate'))
        return cmd, '持股提醒' + ('（推送）' if send else '（預覽）')
    if kind == 'research_card':
        if p.get('watchlist'):
            cmd, title = [s('research_card.py'), '--watchlist'], '研究卡：觀察清單'
        else:
            codes = [_match(c, TW_CODE, '股票代號') for c in re.split(r'[\s,，]+', str(p.get('codes', ''))) if c]
            if not codes or len(codes) > 10:
                raise BadRequest('請輸入 1～10 個股票代號')
            cmd, title = [s('research_card.py'), *codes], '研究卡：' + ' '.join(codes)
        return cmd + ['--send'] * send, title
    if kind == 'record_trade':
        side = p.get('side')
        if side not in ('buy', 'sell'):
            raise BadRequest('買賣別必須是 buy 或 sell')
        sym = _match(p.get('symbol'), SYMBOL, '代號')
        cmd = [s('record_trade.py'), side, sym, _num(p.get('qty'), '股數'), _num(p.get('price'), '成交價')]
        if p.get('name'):
            cmd += ['--name', _text(p['name'], '名稱', 30)]
        if p.get('date'):
            cmd += ['--date', _match(p['date'], DATE, '成交日')]
        if p.get('plan'):
            cmd += ['--plan', _match(p['plan'], PLAN_ID, '計畫 ID')]
        cmd += ['--etf'] * bool(p.get('etf')) + ['--no-plan'] * bool(p.get('no_plan'))
        return cmd, f"記錄{'買進' if side == 'buy' else '賣出'} {sym}"
    if kind == 'plan_new':
        sym = _match(p.get('symbol'), TW_CODE, '代號')
        cmd = [s('plan_trade.py'), 'new', sym, '--stop', _num(p.get('stop'), '停損價'),
               '--thesis', _text(p.get('thesis'), '買進理由', 200)]
        if p.get('entry'):
            cmd += ['--entry', _num(p['entry'], '預計買價')]
        if p.get('target'):
            cmd += ['--target', _num(p['target'], '停利價')]
        for rule in p.get('invalid') or []:
            cmd += ['--invalid', _match(rule, INVALID, '失效條件')]
        return cmd, f'建立交易計畫 {sym}'
    if kind == 'plan_list':
        return [s('plan_trade.py'), 'list'] + ['--all'] * bool(p.get('all')), '交易計畫清單'
    if kind == 'plan_review':
        sym = _match(p.get('symbol'), SYMBOL, '代號')
        return [s('plan_trade.py'), 'review', sym], f'確認持股理由 {sym}'
    if kind == 'plan_cancel':
        pid = _match(p.get('plan_id'), PLAN_ID, '計畫 ID')
        return [s('plan_trade.py'), 'cancel', pid], f'取消計畫 {pid}'
    if kind == 'review_trades':
        cmd = [s('review_trades.py')]
        if p.get('all'):
            cmd.append('--all')
        elif p.get('month'):
            cmd += ['--month', _match(p['month'], MONTH, '月份')]
        return cmd + ['--send'] * send, '交易檢討'
    if kind == 'sector_radar':
        return [s('sector_radar.py')] + ['--backtest'] * bool(p.get('backtest')), \
            '族群雷達' + ('歷史驗證' if p.get('backtest') else '')
    if kind == 'paper_bot':
        return [s('paper_bot.py')], '模擬帳戶：執行今天的流程'
    if kind == 'forward_test':
        return [s('forward_test.py')] + ['--send'] * send, '前瞻測試' + ('（推送）' if send else '')
    if kind == 'lab':
        script = LAB_SCRIPTS.get(p.get('script'))
        universe = p.get('universe', 'electronics')
        if not script or universe not in LAB_UNIVERSES:
            raise BadRequest('研究種類或範圍不正確')
        return [s(script), '--universe', universe], f"選股研究：{script.replace('_lab.py', '')} / {universe}"
    raise BadRequest(f'未知的工作：{kind}')


# 會打到證交所／櫃買的工作：同一時間只跑一個，避免請求過密被封鎖
EXCHANGE_HEAVY = {'research_card', 'lab', 'forward_test', 'sector_radar', 'paper_bot'}
# 會改寫 portfolio.json 的工作：同一時間只跑一個
WRITES_PORTFOLIO = {'record_trade', 'plan_new', 'plan_review', 'plan_cancel'}


@dataclass
class Job:
    id: str
    kind: str
    title: str
    argv: list[str]
    status: str = 'running'          # running / done / failed
    output: str = ''
    exit_code: int | None = None
    started: float = field(default_factory=time.time)
    ended: float | None = None
    _proc: subprocess.Popen | None = None

    def public(self, with_output=True) -> dict:
        d = {'id': self.id, 'kind': self.kind, 'title': self.title, 'status': self.status,
             'exit_code': self.exit_code, 'started': self.started, 'ended': self.ended}
        if with_output:
            d['output'] = self.output
        return d


class JobManager:
    def __init__(self, keep: int = 50):
        self.jobs: dict[str, Job] = {}
        self.keep = keep
        self.lock = threading.Lock()

    def running(self, kinds: set[str]) -> Job | None:
        return next((j for j in self.jobs.values() if j.status == 'running' and j.kind in kinds), None)

    def start(self, kind: str, params: dict) -> Job:
        argv, title = build_command(kind, params)
        with self.lock:
            for group in (EXCHANGE_HEAVY, WRITES_PORTFOLIO):
                if kind in group and (busy := self.running(group)):
                    raise BadRequest(f'「{busy.title}」還在執行，請等它結束再試')
            job = Job(id=uuid.uuid4().hex[:10], kind=kind, title=title, argv=argv)
            self.jobs[job.id] = job
            self._trim()
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
        try:
            proc = subprocess.Popen([PY, *job.argv], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            job._proc = proc
            for line in proc.stdout:
                job.output += line
            job.exit_code = proc.wait()
            job.status = 'done' if job.exit_code == 0 else 'failed'
        except Exception as e:      # noqa: BLE001
            job.output += f'\n[error] {e}'
            job.status = 'failed'
        job.ended = time.time()

    def stop(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job.status == 'running' and job._proc:
            job._proc.terminate()
            job.output += '\n[stopped] 已手動停止'

    def _trim(self) -> None:
        done = [j for j in self.jobs.values() if j.status != 'running']
        for j in sorted(done, key=lambda j: j.started)[:max(0, len(self.jobs) - self.keep)]:
            self.jobs.pop(j.id, None)
