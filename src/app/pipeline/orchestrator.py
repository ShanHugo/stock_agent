"""多代理流程：Data QA → PM/Risk → Execution（模擬）→ 稽核留痕。

每個階段的決策由 Python 規則計算；若提供 narrator，對應的代理會針對該階段結果寫講評。
"""
from __future__ import annotations
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config.settings import settings
from .qa import run_quality_checks
from .risk import run_pm_risk
from .execution import run_execution

STAGE_AGENTS = {
    'data_qa': 'data_qa_agent',
    'pm_risk': 'pm_risk_agent',
    'execution': 'execution_agent',
}


@dataclass
class PipelineResult:
    summary: dict
    backtest: pd.DataFrame | None = None
    audit_path: Path | None = None
    narrations: dict = field(default_factory=dict)


def _fingerprint(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv().encode('utf-8')).hexdigest()


def _git_rev() -> str | None:
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def run_pipeline(
    df: pd.DataFrame,
    symbol: str,
    *,
    strategy: str = 'momentum',
    lookback: int = 20,
    long_only: bool = True,
    capital: float = 1_000_000,
    expected_end: str | None = None,
    narrator=None,
    audit_dir: Path | None = Path('reports'),
) -> PipelineResult:
    costs = dict(tx_fee_bps=settings.tx_fee_bps, tx_tax_bps=settings.tx_tax_bps,
                 slippage_bps=settings.slippage_bps)
    started = datetime.now()
    summary: dict = {
        'symbol': symbol,
        'run_at': started.isoformat(timespec='seconds'),
        'code_version': _git_rev(),
        'params': {'strategy': strategy, 'lookback': lookback, 'long_only': long_only,
                   'capital': capital, 'expected_end': expected_end, **costs},
        'data_fingerprint': _fingerprint(df),
        'stages': {},
        'timings_sec': {},
    }
    result = PipelineResult(summary=summary)

    def narrate(stage: str, payload: dict):
        if narrator is None:
            return
        try:
            result.narrations[stage] = narrator.narrate(STAGE_AGENTS[stage], stage, payload)
        except Exception as e:  # 講評失敗不影響數值結果
            result.narrations[stage] = f'[講評失敗] {e}'

    t0 = time.perf_counter()
    qa = run_quality_checks(df, expected_end=expected_end)
    summary['stages']['data_qa'] = qa
    summary['timings_sec']['data_qa'] = round(time.perf_counter() - t0, 3)
    narrate('data_qa', qa)

    if qa['status'] == 'FAIL':
        summary['status'] = 'STOPPED_AT_DATA_QA'
        return _finalize(result, audit_dir)

    clean = df.sort_index()
    t0 = time.perf_counter()
    risk, bt = run_pm_risk(clean, strategy=strategy, lookback=lookback, long_only=long_only, **costs)
    summary['stages']['pm_risk'] = risk
    summary['timings_sec']['pm_risk'] = round(time.perf_counter() - t0, 3)
    result.backtest = bt
    narrate('pm_risk', risk)

    t0 = time.perf_counter()
    exe = run_execution(risk, symbol=symbol, capital=capital, **costs)
    summary['stages']['execution'] = exe
    summary['timings_sec']['execution'] = round(time.perf_counter() - t0, 3)
    narrate('execution', exe)

    summary['status'] = 'HALTED' if exe['status'] == 'HALTED' else 'COMPLETED'
    return _finalize(result, audit_dir)


def _finalize(result: PipelineResult, audit_dir: Path | None) -> PipelineResult:
    result.summary['narrations'] = result.narrations
    if audit_dir is not None:
        run_at = datetime.fromisoformat(result.summary['run_at'])
        out = Path(audit_dir) / run_at.strftime('%Y%m%d')
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"pipeline_{result.summary['symbol']}_{run_at.strftime('%H%M%S')}.json"
        path.write_text(json.dumps(result.summary, ensure_ascii=False, indent=2, default=str),
                        encoding='utf-8')
        result.audit_path = path
    return result
