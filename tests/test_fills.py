import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import paper_bot as pb  # noqa: E402


def q(asks=(), bids=(), last=100.0, up=110.0, down=90.0):
    return {'date': '20261022', 'last': last, 'asks': list(asks), 'bids': list(bids), 'limit_up': up, 'limit_down': down}


class TestFillFromBook(unittest.TestCase):
    def test_walks_the_book(self):
        p, note = pb.fill_from_book(q(asks=[(100.0, 30), (100.5, 30), (101.0, 100)]), 'buy', 50)
        self.assertAlmostEqual(p, (30 * 100 + 20 * 100.5) / 50)
        self.assertEqual(note, '零股五檔')
        p, _ = pb.fill_from_book(q(bids=[(99.5, 10), (99.0, 100)]), 'sell', 20)
        self.assertAlmostEqual(p, (10 * 99.5 + 10 * 99.0) / 20)

    def test_not_enough_volume_uses_last_level(self):
        p, note = pb.fill_from_book(q(asks=[(100.0, 5), (101.0, 5)]), 'buy', 20)
        self.assertAlmostEqual(p, (5 * 100 + 15 * 101) / 20)
        self.assertIn('量不足', note)

    def test_limit_locked(self):
        self.assertEqual(pb.fill_from_book(q(last=110.0), 'buy', 10)[0], None)          # 漲停沒有賣單
        self.assertEqual(pb.fill_from_book(q(last=90.0), 'sell', 10)[0], None)          # 跌停沒有買單
        self.assertEqual(pb.fill_from_book(q(last=105.0), 'buy', 10)[0], 105.0)         # 沒掛單但沒漲停：用最新價
        self.assertEqual(pb.fill_from_book(q(last=None), 'buy', 10)[0], None)

    def test_limit_gate_without_book(self):
        price = {'UP': 109.6, 'DN': 90.4, 'OK': 103.0}
        prev = {'UP': 100.0, 'DN': 100.0, 'OK': 100.0}
        g = pb.limit_gate(price.get, prev.get, {'UP': 'buy', 'DN': 'sell', 'OK': 'buy'})
        self.assertIsNone(g('UP'))
        self.assertIsNone(g('DN'))
        self.assertEqual(g('OK'), 103.0)
        g2 = pb.limit_gate(price.get, prev.get, {'UP': 'sell', 'DN': 'buy'})   # 漲停賣、跌停買都可以成交
        self.assertEqual((g2('UP'), g2('DN')), (109.6, 90.4))


if __name__ == '__main__':
    unittest.main()
