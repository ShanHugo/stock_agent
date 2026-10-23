import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.web import console
from src.web.jobs import BadRequest, build_command, JobManager


class TestBuildCommand(unittest.TestCase):
    def test_preview_is_default_and_send_is_explicit(self):
        cmd, _ = build_command('morning_brief', {})
        self.assertIn('--dry-run', cmd)
        cmd, _ = build_command('check_alerts', {'send': True, 'force': True})
        self.assertNotIn('--dry-run', cmd)
        self.assertIn('--force', cmd)

    def test_record_trade_validation(self):
        cmd, _ = build_command('record_trade', {'side': 'buy', 'symbol': '0050', 'qty': '12', 'price': '108.5',
                                                'date': '2026-10-20'})
        self.assertEqual(cmd[1:6], ['buy', '0050', '12', '108.5', '--date'])
        for bad in [{'side': 'hold'}, {'side': 'buy', 'symbol': '0050; del', 'qty': 1, 'price': 1},
                    {'side': 'buy', 'symbol': '0050', 'qty': -1, 'price': 1},
                    {'side': 'buy', 'symbol': '0050', 'qty': 1, 'price': 'abc'},
                    {'side': 'buy', 'symbol': '0050', 'qty': 1, 'price': 1, 'date': '20261020'}]:
            with self.assertRaises(BadRequest):
                build_command('record_trade', bad)

    def test_plan_and_research_validation(self):
        cmd, _ = build_command('plan_new', {'symbol': '2317', 'stop': 190, 'thesis': 'AI 伺服器',
                                            'invalid': ['below_ma:60', 'revenue_yoy_below:0:2']})
        self.assertEqual(cmd.count('--invalid'), 2)
        with self.assertRaises(BadRequest):
            build_command('plan_new', {'symbol': '2317', 'stop': 190, 'thesis': 'x', 'invalid': ['rm -rf']})
        with self.assertRaises(BadRequest):
            build_command('plan_new', {'symbol': '2317', 'stop': 190, 'thesis': '多行\n理由'})
        cmd, _ = build_command('research_card', {'codes': '2330, 2317'})
        self.assertEqual(cmd[-2:], ['2330', '2317'])
        with self.assertRaises(BadRequest):
            build_command('research_card', {'codes': '2330 ../x'})
        with self.assertRaises(BadRequest):
            build_command('lab', {'script': 'shortterm', 'universe': 'everything'})
        with self.assertRaises(BadRequest):
            build_command('shell', {})

    def test_one_exchange_job_at_a_time(self):
        m = JobManager()
        with mock.patch('src.web.jobs.threading.Thread'):
            m.start('research_card', {'codes': '2330'})
            with self.assertRaises(BadRequest):
                m.start('lab', {'script': 'strategy', 'universe': 'electronics'})
            m.start('plan_list', {})   # 不碰交易所的工作不受影響


class TestConsoleApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.pf = d / 'portfolio.json'
        self.pf.write_text(json.dumps({'holdings': [{'symbol': '0050', 'shares': 10, 'cost': 100}]}), encoding='utf-8')
        self.patches = [mock.patch.object(console, 'PORTFOLIO', self.pf), mock.patch.object(console, 'BACKUPS', d / 'bk'),
                        mock.patch.object(console, 'REPORTS', d / 'reports'), mock.patch.object(console, 'ROOT', d)]
        for p in self.patches:
            p.start()
        (d / 'reports').mkdir()
        (d / 'reports' / 'a.md').write_text('# 報告', encoding='utf-8')
        self.client = TestClient(console.app, base_url='http://127.0.0.1:8765')

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_page_and_reports(self):
        self.assertIn('台股操作台', self.client.get('/').text)
        self.assertEqual([r['name'] for r in self.client.get('/api/reports').json()], ['a.md'])
        self.assertEqual(self.client.get('/api/reports/a.md').json()['text'], '# 報告')
        self.assertEqual(self.client.get('/api/reports/..%2Fportfolio.json').status_code, 404)

    def test_portfolio_save_validates_and_backs_up(self):
        r = self.client.put('/api/portfolio', json={'text': '{"holdings": [}'})
        self.assertEqual(r.status_code, 400)
        r = self.client.put('/api/portfolio', json={'text': json.dumps({'holdings': [{'symbol': '0050'}]})})
        self.assertEqual(r.status_code, 400)          # 缺股數、成本 → 不存
        self.assertIn('"shares": 10', self.pf.read_text(encoding='utf-8').replace(' ', ' '))
        good = {'holdings': [{'symbol': '0050', 'shares': 20, 'cost': 100}]}
        r = self.client.put('/api/portfolio', json={'text': json.dumps(good)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(json.loads(self.pf.read_text(encoding='utf-8'))['holdings'][0]['shares'], 20)
        self.assertEqual(len(list((Path(self.tmp.name) / 'bk').glob('*.json'))), 1)

    def test_watchlist(self):
        r = self.client.put('/api/watchlist', json={'codes': ['2330', '2317', '2330']})
        self.assertEqual(r.json()['watchlist'], ['2330', '2317'])
        self.assertEqual(self.client.put('/api/watchlist', json={'codes': ['abc;']}).status_code, 400)

    def test_host_allowlist(self):
        bad = TestClient(console.app, base_url='http://evil.example.com')
        self.assertEqual(bad.get('/api/portfolio').status_code, 403)
        ts = TestClient(console.app, base_url='https://desktop.tail1234.ts.net')
        self.assertEqual(ts.get('/api/portfolio').status_code, 200)

    def test_screenshot_read_then_apply(self):
        rows = [{'code': '0050', 'name': '元大', 'price': 106.9, 'avg_cost': 87.76, 'shares': 204,
                 'total_cost': 17919, 'market_value': 21799}]
        with mock.patch('src.app.alerts.screenshot.read_image', return_value=rows):
            r = self.client.post('/api/holdings/screenshot', json={'image': 'data:image/jpeg;base64,AAAA'})
        ch = r.json()['changes']
        self.assertEqual((ch[0]['kind'], ch[0]['old_shares'], ch[0]['shares'], ch[0]['problems']), ('update', 10, 204, []))
        self.assertEqual(json.loads(self.pf.read_text(encoding='utf-8'))['holdings'][0]['shares'], 10)   # 讀圖不寫入
        r = self.client.post('/api/holdings/apply', json={'changes': ch})
        self.assertTrue(r.json()['saved'])
        h = json.loads(self.pf.read_text(encoding='utf-8'))['holdings'][0]
        self.assertEqual((h['shares'], h['cost']), (204, 87.76))

    def test_task_action_whitelist(self):
        calls = []
        ok = mock.Mock(returncode=0, stdout='', stderr='')
        with mock.patch.object(console, '_ps', side_effect=lambda cmd, **k: calls.append(cmd) or ok):
            self.assertEqual(self.client.post('/api/tasks/console/stop').status_code, 400)       # 不能停掉操作台自己
            self.assertEqual(self.client.post('/api/tasks/evil;rm/run').status_code, 400)
            self.assertEqual(self.client.post('/api/tasks/check_alerts/delete').status_code, 400)
            self.assertEqual(self.client.post('/api/tasks/check_alerts/run').status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith('Start-ScheduledTask') and "-TaskName 'check_alerts'" in calls[0])

    def test_job_rejects_unknown_kind(self):
        with mock.patch.object(console, 'external_lab_running', return_value=False):
            self.assertEqual(self.client.post('/api/jobs', json={'kind': 'shell', 'params': {}}).status_code, 400)

    def test_paper_view_and_pause(self):
        d = Path(self.tmp.name) / 'paper'
        with mock.patch.object(console, 'PAPER', d):
            self.assertFalse(self.client.get('/api/paper').json()['exists'])
            d.mkdir()
            (d / 'latest.json').write_text(json.dumps({'last_date': '2026-10-21', 'curve': []}), encoding='utf-8')
            r = self.client.get('/api/paper').json()
            self.assertTrue(r['exists'])
            self.assertFalse(r['paused'])
            self.assertTrue(self.client.post('/api/paper/pause', json={'paused': True}).json()['paused'])
            self.assertTrue((d / 'paused').exists())
            self.assertIn('暫停', self.client.get('/api/paper').json()['paused_info'])
            self.assertFalse(self.client.post('/api/paper/pause', json={'paused': False}).json()['paused'])
            self.assertFalse((d / 'paused').exists())
        cmd, _ = build_command('paper_bot', {'send': True, 'reset': True})
        self.assertEqual(len(cmd), 1)                       # 操作台不能重設帳戶或另外加參數

    def test_parse_progress(self):
        p = console.parse_progress(['[twse] 行情 2016-03-01 (300/3078)', '[tpex] 法人與融資 2019-01-02 (101/2150)',
                                    '[twse] 行情 2016-08-01 (400/3078)'])
        self.assertEqual(p['twse']['done'], 400)
        self.assertEqual(p['tpex']['stage'], '法人與融資')


if __name__ == '__main__':
    unittest.main()
