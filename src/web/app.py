from __future__ import annotations
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
import pandas as pd
import time
from typing import Dict, Any, List, Optional

from src.app.agents.registry import ensure_agents, get_client

from src.app.data.fetch import fetch_ohlcv_yf
from src.app.features.indicators import momentum_signal, sma, rsi, zscore, mean_reversion_signal
from src.app.visual.data_report import build_data_report
from src.app.backtest.engine import backtest_engine
from src.app.performance.metrics import basic_report
from src.app.visual.interactive_report import build_interactive_report

app = FastAPI(title="TW Stock Multi-Agent UI", version="0.1")
base_path = Path(__file__).parent
templates = Jinja2Templates(directory=str(base_path / 'templates'))
app.mount('/static', StaticFiles(directory=str(base_path / 'static')), name='static')
# 掛載報表靜態檔案（若目錄存在），以便直接訪問 /reports/...html
reports_dir = Path('reports')
reports_dir.mkdir(exist_ok=True)
app.mount('/reports', StaticFiles(directory=str(reports_dir)), name='reports')


class BacktestRequest(BaseModel):
    symbol: str = '2330.TW'
    start: str = '2024-05-01'
    end: str = datetime.now().strftime('%Y-%m-%d')
    lookback: int = 5


class ResearchRequest(BaseModel):
    symbol: str = '2330.TW'
    start: str = '2024-05-01'
    end: str = datetime.now().strftime('%Y-%m-%d')
    preview_rows: int = 10

class DataReportRequest(BaseModel):
    symbol: str = '2330.TW'
    start: str = '2024-05-01'
    end: str = datetime.now().strftime('%Y-%m-%d')
    lookback: int = 5


class AgentMessageRequest(BaseModel):
    agent: str
    message: str
    thread_id: Optional[str] = None


# --- Agent Client 快取 ---
AGENT_STATE: Dict[str, Any] = {
    "agents": None,
}


def _ensure_agent_client():
    if not AGENT_STATE.get("client"):
        try:
            AGENT_STATE["client"] = get_client()
        except Exception as e:
            # 不中斷：記錄並回傳 None 供後備使用
            AGENT_STATE["client"] = None
            AGENT_STATE["client_error"] = str(e)
    if not AGENT_STATE.get("agents"):
        try:
            AGENT_STATE["agents"] = ensure_agents()
        except Exception as e:
            # 後備 mock 代理
            AGENT_STATE["agents"] = {
                "data_qa_agent": {"id": "mock-data-qa"},
                "pm_risk_agent": {"id": "mock-pm-risk"},
                "execution_agent": {"id": "mock-exec"},
            }
            AGENT_STATE["agent_error"] = str(e)
    return AGENT_STATE["client"], AGENT_STATE["agents"]


@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse('index.html', {"request": request})


@app.post('/api/backtest')
async def api_backtest(req: BacktestRequest):
    df = fetch_ohlcv_yf(req.symbol, req.start, req.end)
    pos = momentum_signal(df['close'], req.lookback)
    bt = backtest_engine(df, pos)
    rpt = basic_report(bt)
    out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
    html_path = build_interactive_report(bt, out_dir)
    return {"metrics": rpt, "report_html": html_path}


@app.post('/api/research')
async def api_research(req: ResearchRequest):
    df = fetch_ohlcv_yf(req.symbol, req.start, req.end)
    if df.empty:
        raise HTTPException(status_code=404, detail="無資料")
    # 指標計算（安全檢查資料長度）
    def safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            import pandas as _pd
            return _pd.Series(index=df.index, dtype='float64')
    ind = {}
    ind['sma20'] = safe(sma, df['close'], 20)
    ind['sma60'] = safe(sma, df['close'], 60)
    ind['rsi14'] = safe(rsi, df['close'], 14)
    ind['zscore20'] = safe(zscore, df['close'], 20)
    ind['momentum5'] = safe(momentum_signal, df['close'], 5)
    ind['meanrev5'] = safe(mean_reversion_signal, df['close'], 5)
    feat_df = df[['close']].copy()
    for k, s in ind.items():
        feat_df[k] = s
    latest = feat_df.iloc[-1].to_dict()
    # 簡單合成信號
    comp = 'NEUTRAL'
    reasons = []
    close = latest.get('close')
    sma20_v = latest.get('sma20')
    mom = latest.get('momentum5')
    rsi_v = latest.get('rsi14')
    if mom is not None and sma20_v and close:
        if mom > 0 and close > sma20_v and (rsi_v is None or 30 < rsi_v < 70):
            comp = 'LONG'; reasons.append('動能正向且站上SMA20')
        elif mom < 0 and close < sma20_v:
            comp = 'SHORT'; reasons.append('動能負向且跌破SMA20')
    if comp == 'NEUTRAL':
        reasons.append('條件未形成趨勢方向')
    latest['composite_signal'] = comp
    # 過去 N 筆
    preview = feat_df.tail(req.preview_rows)
    preview_records = [
        {**{'date': d.strftime('%Y-%m-%d')}, **{k: (None if pd.isna(v) else float(v)) for k, v in row.items()}}
        for d, row in preview.iterrows()
    ]
    return {
        'symbol': req.symbol,
        'period': {'start': feat_df.index.min().strftime('%Y-%m-%d'), 'end': feat_df.index.max().strftime('%Y-%m-%d')},
        'latest': {k: (None if pd.isna(v) else float(v) if isinstance(v, (int, float)) else v) for k, v in latest.items()},
        'preview': preview_records,
        'explanation': '; '.join(reasons)
    }

@app.post('/api/data_report')
async def api_data_report(req: DataReportRequest):
    df = fetch_ohlcv_yf(req.symbol, req.start, req.end)
    if df.empty:
        raise HTTPException(status_code=404, detail='無資料')
    lb = max(1, min(60, req.lookback))
    inds = {
        'sma20': sma(df['close'], 20),
        'sma60': sma(df['close'], 60),
        'rsi14': rsi(df['close'], 14),
        'momentum_sig': momentum_signal(df['close'], lb),
        'meanrev_sig': mean_reversion_signal(df['close'], lb),
    }
    # 計算買賣點列表
    mom_raw = df['close'].pct_change(lb)
    sign = mom_raw.apply(lambda v: 1 if v>0 else (-1 if v<0 else 0))
    prev = sign.shift(1)
    # Mean Reversion: 負<-正 (轉為 -1) 視為買點；正<-負 (轉為 +1) 視為賣點
    buys = sign[(sign==-1) & (prev==1)].index
    sells = sign[(sign==1) & (prev==-1)].index
    trades = []
    for ts in buys:
        trades.append({'type':'BUY','date': ts.strftime('%Y-%m-%d'), 'price': float(df.loc[ts,'close'])})
    for ts in sells:
        trades.append({'type':'SELL','date': ts.strftime('%Y-%m-%d'), 'price': float(df.loc[ts,'close'])})
    trades.sort(key=lambda x: x['date'])
    out_dir = Path('reports') / datetime.now().strftime('%Y%m%d')
    html_path = build_data_report(df, inds, req.symbol, out_dir, lookback=lb, buy_idx=buys, sell_idx=sells)
    rel_url = '/' + str(html_path).replace('\\', '/')
    return {"report": str(html_path), "url": rel_url, "lookback": lb, "trades": trades}

@app.get('/api/data_report')
async def api_data_report_get(symbol: str, start: str, end: str, lookback: int = 5):
    req = DataReportRequest(symbol=symbol, start=start, end=end, lookback=lookback)
    return await api_data_report(req)


@app.get('/api/agents/list')
async def list_agents():
    client, agents = _ensure_agent_client()
    resp = {"agents": list(agents.keys())}
    if client is None:
        resp["mock"] = True
        resp["warning"] = AGENT_STATE.get("client_error") or AGENT_STATE.get("agent_error") or "azure agents not configured"
    return resp


@app.post('/api/agents/message')
async def agent_message(req: AgentMessageRequest):
    client, agents = _ensure_agent_client()
    if req.agent not in agents:
        raise HTTPException(status_code=400, detail=f"未知代理: {req.agent}")
    # Mock path
    if client is None:
        thread_id = req.thread_id or 'mock-thread'
        mock_reply = f"[mock:{req.agent}] 收到訊息：{req.message[:120]}"  # echo effect
        return {
            "thread_id": thread_id,
            "run_status": "completed",
            "messages": [
                {"role": "user", "content": req.message},
                {"role": req.agent, "content": mock_reply}
            ]
        }
    # 真實 Azure Agents 流程
    if req.thread_id:
        thread_id = req.thread_id
    else:
        thread = client.create_thread()
        thread_id = getattr(thread, 'id', thread.get('id','unknown'))
    client.add_message(thread_id=thread_id, role="user", content=req.message)
    agent_obj = agents[req.agent]
    run = client.create_run(thread_id=thread_id, agent_id=getattr(agent_obj, 'id', agent_obj.get('id')))
    run_id = getattr(run, 'id', run.get('id'))
    started = time.time()
    status = getattr(run, 'status', run.get('status','running'))
    while status not in ("completed", "failed", "cancelled"):
        if time.time() - started > 30:
            break
        time.sleep(1.1)
        run = client.get_run(thread_id=thread_id, run_id=run_id)
        status = getattr(run, 'status', run.get('status','unknown'))
    msgs_resp = client.list_messages(thread_id=thread_id)
    messages: List[Dict[str, Any]] = []
    for m in getattr(msgs_resp, 'data', msgs_resp if isinstance(msgs_resp, list) else []):
        role = getattr(m, 'role', m.get('role'))
        content = getattr(m, 'content', m.get('content'))
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'output_text':
                    texts.append(c.get('text',''))
                elif isinstance(c, dict) and c.get('type') == 'output_image':
                    texts.append('[image]')
                elif isinstance(c, dict) and c.get('type') == 'input_text':
                    texts.append(c.get('text',''))
            content_str = '\n'.join(texts) if texts else str(content)
        else:
            content_str = str(content)
        messages.append({'role': role, 'content': content_str})
    return {"thread_id": thread_id, "run_status": status, "messages": messages[-25:]}


@app.get('/api/health')
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# 簡易嵌入互動報表: 讀取檔案內容（安全上僅供本地測試）
@app.get('/report', response_class=HTMLResponse)
async def report_page(request: Request, date: str | None = None):
    date = date or datetime.now().strftime('%Y%m%d')
    report_file = Path('reports') / date / 'interactive_report.html'
    if not report_file.exists():
        return HTMLResponse(f"<h3>Report not found for {date}</h3>")
    content = report_file.read_text(encoding='utf-8')
    return templates.TemplateResponse('report_wrapper.html', {"request": request, "embedded_html": content})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('src.web.app:app', host='0.0.0.0', port=8000, reload=True)
