import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import paper_bot as pb  # noqa: E402
from src.app.lab.paperbot import Bot, Config  # noqa: E402

TPE = ZoneInfo('Asia/Taipei')


def at(y, m, d, hh, mm):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(y, m, d, hh, mm, tzinfo=TPE)
    return FakeDT


class TestExecuteNow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        bot = Bot.new(Config(capital=10_000))
        bot.pending = [{'side': 'buy', 'code': 'A', 'shares': 10, 'reason': 'x', 'signal_date': '2026-10-21', 'ref_price': 100.0},
                       {'side': 'buy', 'code': 'H', 'shares': 5, 'reason': 'y', 'signal_date': '2026-10-21', 'ref_price': 50.0}]
        bot.last_date = '2026-10-21'
        (self.state / 'state.json').write_text(json.dumps({**bot.to_dict(), 'start': '2026-10-21'}), encoding='utf-8')
        self.patches = [mock.patch.object(pb, 'STATE', self.state), mock.patch.object(pb, 'PAUSED', self.state / 'paused'),
                        mock.patch.dict('os.environ', {'PAPER_BOT_AUTO_SEND': '0'})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def saved(self):
        return json.loads((self.state / 'state.json').read_text(encoding='utf-8'))

    def run_at(self, fake, quotes):
        with mock.patch.object(pb, 'datetime', fake), mock.patch.object(pb, 'realtime_quotes', return_value=quotes):
            pb.run_execute(lambda m: None)

    def test_fills_from_odd_lot_book_and_skips_halted(self):
        q = {'A': {'date': '20261022', 'time': '09:30:00', 'last': 100.0, 'asks': [(101.0, 6), (102.0, 50)],
                   'bids': [(99.0, 10)], 'limit_up': 110.0, 'limit_down': 90.0},
             'H': {'date': '20261022', 'time': '09:30:00', 'last': None, 'asks': [], 'bids': [],
                   'limit_up': None, 'limit_down': None}}
        self.run_at(at(2026, 10, 22, 9, 30), q)
        s = self.saved()
        self.assertAlmostEqual(s['positions']['A']['entry'], round((6 * 101 + 4 * 102) / 10, 2))
        self.assertEqual(s['trades'][0]['fill'], '09:30 零股五檔')
        self.assertEqual([o['code'] for o in s['pending']], [])          # 沒報價的買單取消（與開盤成交規則一致）

    def test_limit_up_cannot_buy(self):
        q = {c: {'date': '20261022', 'time': '09:30:00', 'last': 110.0, 'asks': [], 'bids': [(110.0, 999)],
                 'limit_up': 110.0, 'limit_down': 90.0} for c in ('A', 'H')}
        self.run_at(at(2026, 10, 22, 9, 30), q)
        s = self.saved()
        self.assertEqual(s['positions'], {})
        self.assertEqual(s['cash'], 10_000)

    def test_guards(self):
        q = {'A': {'date': '20261022', 'time': '13:30:00', 'last': 100.0, 'asks': [(101.0, 99)], 'bids': [(99.0, 99)],
                   'limit_up': 110.0, 'limit_down': 90.0}}
        self.run_at(at(2026, 10, 21, 19, 30), q)          # 盤後：不執行
        self.run_at(at(2026, 10, 22, 8, 50), q)           # 開盤前：不執行
        self.run_at(at(2026, 10, 23, 9, 30), q)           # 報價日期不是今天（休市）：不執行
        self.assertEqual(len(self.saved()['pending']), 2)
        (self.state / 'paused').write_text('x', encoding='utf-8')
        self.run_at(at(2026, 10, 22, 9, 30), q)           # 暫停中：不執行
        self.assertEqual(len(self.saved()['pending']), 2)


if __name__ == '__main__':
    unittest.main()
