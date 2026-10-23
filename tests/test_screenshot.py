import unittest

from src.app.alerts import screenshot as ss

ROW_0050 = {'code': '0050', 'name': '元大', 'price': 106.9, 'avg_cost': 87.76, 'shares': 204,
            'total_cost': 17919, 'market_value': 21799}


class TestScreenshot(unittest.TestCase):
    def test_real_screenshot_numbers_pass(self):
        # 2026-10-24 口袋證券截圖的實際數字
        self.assertEqual(ss.check(ROW_0050), [])
        self.assertEqual(ss.check({'code': '00881', 'price': 48.8, 'avg_cost': 52.15, 'shares': 252,
                                   'total_cost': 13148, 'market_value': 12296}), [])

    def test_misread_digit_is_caught(self):
        bad = dict(ROW_0050, shares=284)            # 204 看成 284
        self.assertTrue(any('總成本' in p for p in ss.check(bad)))
        self.assertTrue(ss.check(dict(ROW_0050, code='OO5O')))
        self.assertTrue(any('整數' in p for p in ss.check(dict(ROW_0050, shares=204.5, total_cost=17947, market_value=21861))))

    def test_us_fractional_shares_ok(self):
        self.assertEqual(ss.check({'code': 'VOO', 'price': 710.72, 'avg_cost': 652.32, 'shares': 2.0592,
                                   'total_cost': 1343.3, 'market_value': 1463.5}), [])

    def test_diff_kinds(self):
        holdings = [{'symbol': '0050', 'shares': 204, 'cost': 87.76}, {'symbol': '3105.TWO', 'shares': 5, 'cost': 40},
                    {'symbol': '2330', 'shares': 1, 'cost': 900}, {'symbol': 'VOO', 'shares': 2, 'cost': 600}]
        rows = [ROW_0050, {'code': '3105', 'price': 50, 'avg_cost': 45, 'shares': 10, 'total_cost': 450, 'market_value': 500},
                {'code': '2317', 'price': 200, 'avg_cost': 190, 'shares': 3, 'total_cost': 570, 'market_value': 600}]
        kinds = {d['code']: d['kind'] for d in ss.diff(holdings, rows)}
        # 美股不在這張台股截圖裡，不能列為「截圖沒有」
        self.assertEqual(kinds, {'0050': 'same', '3105': 'update', '2317': 'new', '2330': 'absent'})

    def test_apply_skips_problems_and_absent(self):
        raw = {'holdings': [{'symbol': '3105.TWO', 'shares': 5, 'cost': 40, 'stop_loss_pct': -0.1}]}
        notes = ss.apply(raw, [{'code': '3105', 'kind': 'update', 'shares': 10, 'cost': 45, 'problems': []},
                               {'code': '2317', 'kind': 'new', 'shares': 3, 'cost': 190, 'name': '鴻海', 'problems': []},
                               {'code': '2454', 'kind': 'new', 'shares': 1, 'cost': 1, 'problems': ['對不上']},
                               {'code': '2330', 'kind': 'absent'}], lambda c: c)
        self.assertEqual(len(notes), 2)
        self.assertEqual(raw['holdings'][0], {'symbol': '3105.TWO', 'shares': 10, 'cost': 45.0, 'stop_loss_pct': -0.1})
        self.assertEqual(raw['holdings'][1]['symbol'], '2317')


if __name__ == '__main__':
    unittest.main()
