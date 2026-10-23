import unittest

import pandas as pd

from src.app.us import universe as uv
from src.app.us import prices as pr
from src.app.us import sec

CHANGES_HTML = """<table>
<tr><th rowspan=2>Effective Date</th><th colspan=2>Added</th><th colspan=2>Removed</th><th rowspan=2>Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>February 1, 2024</td><td>DAY</td><td>Dayforce</td><td>CDAY</td><td>Ceridian</td><td>Ceridian changed its ticker symbol from CDAY to DAY.</td></tr>
<tr><td>March 2, 2020</td><td>IR</td><td>Ingersoll Rand</td><td>XEC</td><td>Cimarex Energy</td><td>Gardner Denver acquired Ingersoll Rand's businesses then changed its name to Ingersoll Rand.</td></tr>
<tr><td>June 1, 2015</td><td>BRK.B</td><td>Berkshire</td><td>OLD</td><td>Old Co</td><td>Market capitalization changes.[3]</td></tr>
</table>"""


class TestUniverse(unittest.TestCase):
    def setUp(self):
        self.ch = uv.parse_changes(CHANGES_HTML)

    def test_parse_changes_and_yahoo_symbols(self):
        self.assertEqual(list(self.ch['added']), ['DAY', 'IR', 'BRK-B'])
        self.assertEqual(self.ch['date'].iloc[0], pd.Timestamp('2024-02-01'))
        self.assertNotIn('[3]', self.ch['reason'].iloc[2])

    def test_walk_back(self):
        h = uv.membership_history({'DAY', 'IR', 'BRK-B', 'AAPL'}, self.ch, '2010-01-01')
        self.assertEqual(uv.members_at(h, '2024-02-01'), {'DAY', 'IR', 'BRK-B', 'AAPL'})
        self.assertEqual(uv.members_at(h, '2023-01-01'), {'CDAY', 'IR', 'BRK-B', 'AAPL'})
        self.assertEqual(uv.members_at(h, '2012-01-01'), {'CDAY', 'XEC', 'OLD', 'AAPL'})

    def test_renames_need_both_companies_mentioned(self):
        self.assertEqual(uv.renames(self.ch), {'CDAY': 'DAY'})     # XEC→IR 是不同公司，不能當改名

    def test_resolve_chain(self):
        self.assertEqual(pr.resolve('DOW', {'DOW': 'DWDP', 'DWDP': 'DD'}), 'DD')
        self.assertEqual(pr.resolve('A', {'A': 'B', 'B': 'A'}), 'A')    # 循環也要停


class TestCoverage(unittest.TestCase):
    def test_price_must_exist_while_member(self):
        days = pd.bdate_range('2020-01-01', periods=4)
        close = pd.DataFrame({'A': [1, 1, 1, 1], 'NEW': [None, None, 5, 5]}, index=days, dtype=float)
        hist = [(days[0], {'A', 'NEW'})]
        cov = pr.coverage(hist, close, {}, days)
        self.assertEqual(list(cov['priced']), [1, 1, 2, 2])


class TestSecRevenue(unittest.TestCase):
    def fact(self, start, end, val, filed):
        return {'start': start, 'end': end, 'val': val, 'filed': filed}

    def test_quarters_q4_derived_and_first_filing_kept(self):
        usd = [self.fact('2023-01-01', '2023-03-31', 10, '2023-05-01'),
               self.fact('2023-04-01', '2023-06-30', 11, '2023-08-01'),
               self.fact('2023-07-01', '2023-09-30', 12, '2023-11-01'),
               self.fact('2023-01-01', '2023-12-31', 50, '2024-02-15'),     # 全年 → 第 4 季 = 17
               self.fact('2023-01-01', '2023-03-31', 99, '2024-05-01')]     # 之後的重述，不能用
        facts = {'facts': {'us-gaap': {'Revenues': {'units': {'USD': usd}}}}}
        q = sec.extract_quarterly(facts)
        self.assertEqual(list(q['val']), [10, 11, 12, 17])
        self.assertEqual(q['filed'].iloc[-1], pd.Timestamp('2024-02-15'))

    def test_no_revenue(self):
        self.assertTrue(sec.extract_quarterly({'facts': {}}).empty)


if __name__ == '__main__':
    unittest.main()
