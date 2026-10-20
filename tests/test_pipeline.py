import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.app.pipeline import run_pipeline
from src.app.pipeline.qa import run_quality_checks
from src.app.pipeline.risk import realized_state
from src.app.pipeline.execution import run_execution
from src.app.agents import registry


def make_ohlcv(closes, start='2024-01-01'):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({'open': closes, 'high': closes * 1.01, 'low': closes * 0.99,
                         'close': closes, 'volume': 1000.0}, index=idx)


COSTS = dict(tx_fee_bps=2.8, tx_tax_bps=30.0, slippage_bps=5.0)


class TestDataQA(unittest.TestCase):
    def test_clean_data_passes(self):
        qa = run_quality_checks(make_ohlcv(np.linspace(100, 130, 40)))
        self.assertEqual(qa['status'], 'PASS')

    def test_missing_column_fails(self):
        qa = run_quality_checks(make_ohlcv(range(100, 140)).drop(columns='volume'))
        self.assertEqual(qa['status'], 'FAIL')

    def test_gap_and_zero_volume_warn(self):
        df = make_ohlcv(np.r_[np.full(20, 100.0), np.full(20, 130.0)])
        df.iloc[:15, df.columns.get_loc('volume')] = 0
        qa = run_quality_checks(df)
        names = {c['name']: c['status'] for c in qa['checks']}
        self.assertEqual(names['price_gap'], 'WARN')
        self.assertEqual(names['zero_volume'], 'WARN')
        self.assertEqual(qa['status'], 'WARN')

    def test_non_positive_price_fails(self):
        df = make_ohlcv(np.linspace(100, 130, 40))
        df.iloc[5, df.columns.get_loc('close')] = 0
        self.assertEqual(run_quality_checks(df)['status'], 'FAIL')


class TestRisk(unittest.TestCase):
    def test_realized_state_ignores_unrealized_last_bar(self):
        bt = pd.DataFrame({'ret': [0.1, -0.5, 0.0], 'equity': [1.1, 0.55, 0.55]})
        st = realized_state(bt)
        self.assertAlmostEqual(st['pnl_today'], -0.5)
        self.assertAlmostEqual(st['drawdown'], 0.55 / 1.1 - 1)


class TestExecution(unittest.TestCase):
    def _risk(self, cur, tgt, halt=False, price=500.0):
        return {'last_close': price, 'current_weight': cur, 'target_weight': tgt,
                'kill_switch': {'halt': halt, 'reasons': ['test'] if halt else []}}

    def test_buy_whole_lots_no_tax(self):
        exe = run_execution(self._risk(0, 1), symbol='2330', capital=1_200_000, **COSTS)
        o = exe['orders'][0]
        self.assertEqual((o['side'], o['qty']), ('BUY', 2000))
        self.assertAlmostEqual(o['est_cost'], 2000 * 500 * (2.8 + 5.0) / 10000)

    def test_sell_includes_tax(self):
        exe = run_execution(self._risk(1, 0), symbol='2330', capital=1_000_000, **COSTS)
        o = exe['orders'][0]
        self.assertEqual(o['side'], 'SELL')
        self.assertAlmostEqual(o['est_cost'], 2000 * 500 * (2.8 + 5.0 + 30.0) / 10000)

    def test_halt_blocks_orders(self):
        exe = run_execution(self._risk(0, 1, halt=True), symbol='2330', **COSTS)
        self.assertEqual(exe['status'], 'HALTED')
        self.assertEqual(exe['orders'], [])

    def test_capital_too_small(self):
        exe = run_execution(self._risk(0, 1, price=1500.0), symbol='2330', capital=1_000_000, **COSTS)
        self.assertEqual(exe['status'], 'NO_ACTION')


class FakeNarrator:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def narrate(self, agent_name, stage, payload):
        self.calls.append((agent_name, stage))
        if stage == self.fail_on:
            raise RuntimeError('boom')
        return f'{agent_name} ok'


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run_writes_audit_and_calls_agents_in_order(self):
        df = make_ohlcv(np.linspace(100, 130, 60))
        nar = FakeNarrator()
        res = run_pipeline(df, '2330', lookback=5, narrator=nar, audit_dir=self.audit)
        self.assertEqual(res.summary['status'], 'COMPLETED')
        self.assertEqual([c[0] for c in nar.calls], ['data_qa_agent', 'pm_risk_agent', 'execution_agent'])
        saved = json.loads(res.audit_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['data_fingerprint'], res.summary['data_fingerprint'])
        self.assertIn('execution', saved['stages'])

    def test_long_only_by_default(self):
        df = make_ohlcv(np.linspace(130, 100, 60))  # 一路下跌 → 動能訊號為空方
        res = run_pipeline(df, '2330', lookback=5, audit_dir=None)
        self.assertEqual(res.summary['stages']['pm_risk']['target_weight'], 0.0)
        res = run_pipeline(df, '2330', lookback=5, long_only=False, audit_dir=None)
        self.assertEqual(res.summary['stages']['pm_risk']['target_weight'], -1.0)

    def test_qa_fail_stops_pipeline(self):
        df = make_ohlcv(np.linspace(100, 130, 60))
        df.iloc[3, df.columns.get_loc('close')] = -1
        res = run_pipeline(df, '2330', audit_dir=None)
        self.assertEqual(res.summary['status'], 'STOPPED_AT_DATA_QA')
        self.assertNotIn('pm_risk', res.summary['stages'])

    def test_narrator_failure_does_not_change_results(self):
        df = make_ohlcv(np.linspace(100, 130, 60))
        base = run_pipeline(df, '2330', lookback=5, audit_dir=None)
        res = run_pipeline(df, '2330', lookback=5, narrator=FakeNarrator(fail_on='pm_risk'), audit_dir=None)
        self.assertEqual(res.summary['stages']['pm_risk'], base.summary['stages']['pm_risk'])
        self.assertTrue(res.narrations['pm_risk'].startswith('[講評失敗]'))

    def test_deterministic(self):
        df = make_ohlcv(100 + np.sin(np.arange(80) / 5) * 10)
        a = run_pipeline(df, '2330', lookback=5, audit_dir=None).summary['stages']
        b = run_pipeline(df, '2330', lookback=5, audit_dir=None).summary['stages']
        self.assertEqual(a, b)


class FakeAgentsClient:
    def __init__(self, existing):
        self.existing = existing
        self.created, self.updated = [], []

    def enable_auto_function_calls(self, toolset):
        pass

    def list_agents(self):
        return list(self.existing)

    def create_agent(self, model, name, instructions, toolset):
        self.created.append(name)
        return SimpleNamespace(id=f'new-{name}', name=name, model=model, instructions=instructions)

    def update_agent(self, agent_id, model, instructions, toolset):
        self.updated.append(agent_id)
        return SimpleNamespace(id=agent_id, model=model, instructions=instructions)


class TestRegistry(unittest.TestCase):
    def test_reuses_existing_agents(self):
        model = registry.settings.model_deployment or 'gpt-4o-mini'
        name, instr = registry.AGENT_SPECS[0]
        existing = [
            SimpleNamespace(id='a1', name=name, model=model, instructions=instr),
            SimpleNamespace(id='a2', name='pm_risk_agent', model=model, instructions='old'),
        ]
        client = FakeAgentsClient(existing)
        agents = registry.ensure_agents(client)
        self.assertEqual(agents[name].id, 'a1')
        self.assertEqual(client.updated, ['a2'])
        self.assertEqual(client.created, ['execution_agent'])


if __name__ == '__main__':
    unittest.main()
