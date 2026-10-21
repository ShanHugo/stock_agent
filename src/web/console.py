"""本機操作台：持股總覽、盤前簡報與提醒、交易紀錄與計畫、研究卡、選股研究報告、排程狀態。

啟動：python -m src.web.console（只聽 127.0.0.1，不對區網開放）→ http://127.0.0.1:8765
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .jobs import ROOT, BadRequest, JobManager, EXCHANGE_HEAVY, TW_CODE

load_dotenv(ROOT / '.env')
PORTFOLIO = ROOT / 'portfolio.json'
REPORTS = ROOT / 'reports'
LOGS = ROOT / 'logs'
BACKUPS = ROOT / 'state' / 'portfolio_backups'
REPORT_NAME = re.compile(r'^[\w\-]+\.md$')
PROGRESS = re.compile(r'^\[(twse|tpex)\] (\S+) (\S+) \((\d+)/(\d+)\)')

app = FastAPI(title='台股操作台', docs_url=None, redoc_url=None)
jobs = JobManager()
_overview_cache: dict = {}


def _no_window():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def external_lab_running() -> bool:
    """操作台以外（例如排程或命令列）是否有選股研究正在跑。"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                              "Where-Object { $_.CommandLine -match '_lab\\.py|forward_test\\.py|sector_radar\\.py|paper_bot\\.py' } | "
                              "Measure-Object | "
                              "Select-Object -ExpandProperty Count"],
                             capture_output=True, text=True, timeout=20, creationflags=_no_window())
        own = sum(1 for j in jobs.jobs.values() if j.status == 'running' and j.kind in EXCHANGE_HEAVY - {'research_card'})
        return int(out.stdout.strip() or 0) > own
    except Exception:       # noqa: BLE001 — 偵測失敗時不擋操作
        return False


# ---------- 頁面 ----------

@app.get('/', response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / 'templates' / 'console.html').read_text(encoding='utf-8')


# ---------- 持股總覽 ----------

@app.get('/api/overview')
def overview(refresh: bool = False):
    if not PORTFOLIO.exists():
        raise HTTPException(404, '找不到 portfolio.json，請先到「設定」建立')
    if not refresh and _overview_cache.get('at', 0) > time.time() - 600 \
            and _overview_cache.get('mtime') == PORTFOLIO.stat().st_mtime:
        return _overview_cache['data']
    from src.app.alerts.portfolio import load_portfolio
    from src.app.alerts.runner import run_alerts
    from src.app.config.settings import settings
    from src.app.data.fetch import fetch_ohlcv_yf
    portfolio = load_portfolio(PORTFOLIO)
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    # 不傳 state_path：只看狀態，不影響每日提醒的「是否已通知」紀錄
    res = run_alerts(portfolio, fetch_ohlcv_yf, today=now.date(), now=now, default_tw_fee_bps=settings.tx_fee_bps,
                     state_path=None, narrator=None, force=True, revenue_lookup=None)
    data = {k: res.get(k) for k in ('evaluations', 'allocation', 'risk', 'fx', 'errors', 'reviews', 'run_at')}
    data['watchlist'] = portfolio.watchlist
    if data['fx'] is None and any(e['is_us'] for e in data['evaluations'] or []):
        from src.app.alerts.runner import FX_SYMBOL
        from datetime import timedelta
        try:
            fx_df = fetch_ohlcv_yf(FX_SYMBOL, (now.date() - timedelta(days=10)).isoformat(),
                                   (now.date() + timedelta(days=1)).isoformat())
            data['fx'] = float(fx_df['close'].dropna().iloc[-1])
        except Exception as e:      # noqa: BLE001
            data['errors'] = (data['errors'] or []) + [f'抓美元匯率失敗（{e}），美股市值未換算台幣']
    _overview_cache.update(at=time.time(), mtime=PORTFOLIO.stat().st_mtime, data=data)
    return data


# ---------- portfolio.json ----------

class PortfolioText(BaseModel):
    text: str


def _check_portfolio(raw: dict) -> list[str]:
    from src.app.alerts.portfolio import load_portfolio
    from src.app.alerts.plans import validate_risk
    tmp = ROOT / 'state' / '_portfolio_check.json'
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    try:
        p = load_portfolio(tmp)
    except (TypeError, ValueError, KeyError) as e:
        return [f'持股欄位不完整或型別錯誤（每檔至少需要 symbol、shares、cost）：{e}']
    finally:
        tmp.unlink(missing_ok=True)
    return list(p.errors) + (validate_risk(raw['risk']) if raw.get('risk') else [])


def _save_portfolio(raw: dict) -> str | None:
    backup = None
    if PORTFOLIO.exists():
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / f'portfolio_{datetime.now():%Y%m%d_%H%M%S}.json'
        backup.write_text(PORTFOLIO.read_text(encoding='utf-8'), encoding='utf-8')
    PORTFOLIO.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    _overview_cache.clear()
    return str(backup.relative_to(ROOT)) if backup else None


@app.get('/api/portfolio')
def get_portfolio():
    src = PORTFOLIO if PORTFOLIO.exists() else ROOT / 'portfolio.example.json'
    return {'text': src.read_text(encoding='utf-8'), 'exists': PORTFOLIO.exists()}


@app.put('/api/portfolio')
def put_portfolio(body: PortfolioText):
    try:
        raw = json.loads(body.text)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f'JSON 格式錯誤：第 {e.lineno} 行第 {e.colno} 欄 {e.msg}')
    if not isinstance(raw, dict) or not isinstance(raw.get('holdings', []), list):
        raise HTTPException(400, '最外層必須是物件，holdings 必須是清單')
    errors = _check_portfolio(raw)
    if errors:
        raise HTTPException(400, '設定有誤，未儲存：' + '；'.join(errors))
    return {'saved': True, 'backup': _save_portfolio(raw)}


class Watchlist(BaseModel):
    codes: list[str]


@app.put('/api/watchlist')
def put_watchlist(body: Watchlist):
    codes = list(dict.fromkeys(c.strip() for c in body.codes if c.strip()))
    bad = [c for c in codes if not TW_CODE.fullmatch(c)]
    if bad:
        raise HTTPException(400, f'代號格式不正確：{bad}')
    raw = json.loads(PORTFOLIO.read_text(encoding='utf-8')) if PORTFOLIO.exists() else {'holdings': []}
    raw['watchlist'] = codes
    return {'saved': True, 'watchlist': codes, 'backup': _save_portfolio(raw)}


# ---------- 背景工作 ----------

class JobRequest(BaseModel):
    kind: str
    params: dict = {}


@app.post('/api/jobs')
def start_job(req: JobRequest):
    if req.kind in EXCHANGE_HEAVY and external_lab_running():
        raise HTTPException(409, '另一個選股研究正在下載證交所／櫃買資料；同時再查詢容易被封鎖，請等它跑完')
    try:
        job = jobs.start(req.kind, req.params)
    except BadRequest as e:
        raise HTTPException(400, str(e))
    if req.kind in ('record_trade', 'plan_new', 'plan_review', 'plan_cancel'):
        _overview_cache.clear()
    return job.public()


@app.get('/api/jobs')
def list_jobs():
    return sorted((j.public(with_output=False) for j in jobs.jobs.values()), key=lambda j: -j['started'])


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = jobs.jobs.get(job_id)
    if not job:
        raise HTTPException(404, '找不到這個工作')
    return job.public()


@app.post('/api/jobs/{job_id}/stop')
def stop_job(job_id: str):
    jobs.stop(job_id)
    return {'ok': True}


# ---------- 研究報告與進度 ----------

@app.get('/api/reports')
def list_reports():
    files = sorted(REPORTS.glob('*.md'), key=lambda p: -p.stat().st_mtime) if REPORTS.exists() else []
    return [{'name': p.name, 'mtime': p.stat().st_mtime, 'size': p.stat().st_size} for p in files]


@app.get('/api/reports/{name}')
def get_report(name: str):
    if not REPORT_NAME.fullmatch(name) or not (REPORTS / name).is_file():
        raise HTTPException(404, '找不到報告')
    return {'name': name, 'text': (REPORTS / name).read_text(encoding='utf-8')}


def _tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    with path.open('rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 200_000))
        lines = f.read().decode('utf-8', errors='replace').splitlines()
    return [l for l in lines if l.strip() and not l.lstrip().startswith(('+ ', 'At line', 'CategoryInfo',
                                                                        'FullyQualifiedErrorId'))][-n:]


def parse_progress(lines: list[str]) -> dict:
    """{市場: {'stage', 'at', 'done', 'total'}}，取每個市場最後一行進度。"""
    out = {}
    for line in lines:
        m = PROGRESS.match(line.strip())
        if m:
            out[m.group(1)] = {'stage': m.group(2), 'at': m.group(3), 'done': int(m.group(4)), 'total': int(m.group(5))}
    return out


@app.get('/api/lab/progress')
def lab_progress():
    logs = sorted(LOGS.glob('lab_*.log'), key=lambda p: -p.stat().st_mtime) if LOGS.exists() else []
    out = []
    for p in logs[:4]:
        lines = _tail(p, 400)
        out.append({'log': p.name, 'mtime': p.stat().st_mtime, 'progress': parse_progress(lines),
                    'tail': lines[-15:]})
    own = [j.public(with_output=False) for j in jobs.jobs.values() if j.kind == 'lab' and j.status == 'running']
    return {'logs': out, 'running_here': own, 'running_anywhere': bool(own) or external_lab_running()}


@app.get('/api/radar')
def radar_latest():
    path = ROOT / 'state' / 'radar' / 'latest.json'
    if not path.exists():
        return {'exists': False}
    return {'exists': True, **json.loads(path.read_text(encoding='utf-8'))}


PAPER = ROOT / 'state' / 'paper_bot'


def _read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


@app.get('/api/paper')
def paper():
    view = _read_json(PAPER / 'latest.json')
    paused = PAPER / 'paused'
    return {'exists': view is not None, 'view': view, 'backtest': _read_json(PAPER / 'backtest.json'),
            'luck': _read_json(PAPER / 'luck.json'),
            'paused': paused.exists(), 'paused_info': paused.read_text(encoding='utf-8').strip() if paused.exists() else None}


class PauseRequest(BaseModel):
    paused: bool


@app.post('/api/paper/pause')
def paper_pause(req: PauseRequest):
    """暫停：每日排程照常啟動，但模擬帳戶不交易、不推送；恢復後從上次處理的交易日接續（中間的交易日會補跑）。"""
    flag = PAPER / 'paused'
    if req.paused:
        PAPER.mkdir(parents=True, exist_ok=True)
        flag.write_text(f'{datetime.now():%Y-%m-%d %H:%M} 由操作台暫停', encoding='utf-8')
    else:
        flag.unlink(missing_ok=True)
    return {'paused': flag.exists()}


@app.get('/api/forward')
def forward():
    path = ROOT / 'state' / 'forward' / 'latest.json'
    if not path.exists():
        return {'exists': False}
    return {'exists': True, **json.loads(path.read_text(encoding='utf-8'))}


# ---------- 排程與環境 ----------

@app.get('/api/status')
def status():
    tasks = []
    try:
        ps = ("Get-ScheduledTask -TaskPath '\\stock_agent\\' -ErrorAction SilentlyContinue | ForEach-Object { "
              "$i = $_ | Get-ScheduledTaskInfo; [pscustomobject]@{name=$_.TaskName; state=[string]$_.State; "
              "last=[string]$i.LastRunTime; next=[string]$i.NextRunTime; result=$i.LastTaskResult} } | ConvertTo-Json")
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
                             timeout=20, encoding='utf-8', creationflags=_no_window()).stdout.strip()
        if out:
            tasks = json.loads(out)
            tasks = tasks if isinstance(tasks, list) else [tasks]
    except Exception:       # noqa: BLE001
        pass
    ollama = False
    try:
        import requests
        ollama = requests.get(os.getenv('OLLAMA_URL', 'http://localhost:11434') + '/api/tags', timeout=3).ok
    except Exception:       # noqa: BLE001
        pass
    return {
        'tasks': tasks,
        'logs': {n: _tail(LOGS / f'{n}.log', 30) for n in ('morning_brief', 'check_alerts')},
        'discord_configured': bool(os.getenv('DISCORD_WEBHOOK_URL', '').strip()),   # 只回報有沒有設定，不回傳網址
        'ollama': ollama,
        'ollama_model': os.getenv('OLLAMA_MODEL', 'qwen3.5:9b'),
    }


def main():
    import uvicorn
    port = int(os.getenv('CONSOLE_PORT', '8765'))
    if '--open' in sys.argv:
        import threading, webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
