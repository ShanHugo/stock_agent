import unittest

import numpy as np
import pandas as pd

from src.app.lab import radar


def synthetic(n_themes=8, per=6, days=400, seed=0):
    """族群 T0 的營收年增最高、之後也真的漲最多；其他族群隨機。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range('2020-01-01', periods=days)
    codes = {f'T{t}': [f'T{t}_{i}' for i in range(per)] for t in range(n_themes)}
    allc = [c for m in codes.values() for c in m]
    drift = {c: (0.002 if c.startswith('T0_') else 0.0) for c in allc}
    rets = pd.DataFrame({c: rng.normal(drift[c], 0.01, days) for c in allc}, index=idx)
    px = (1 + rets).cumprod() * 100
    px['0050'] = (1 + pd.Series(rng.normal(0.0005, 0.01, days), index=idx)).cumprod() * 100
    feat = lambda f: pd.DataFrame({c: (5.0 if c.startswith('T0_') else rng.normal(0, 1)) for c in allc}, index=idx)
    panels = {f: feat(f) for f in radar.SHOW_FEATURES}
    return px, panels, codes


class TestRadar(unittest.TestCase):
    def test_theme_members_min_size(self):
        m = radar.theme_members({'A': {'記憶體'}, 'B': {'記憶體', '面板'}}, ['A', 'B'])
        self.assertEqual(m, {})                                  # 不足 5 檔的族群不列
        m = radar.theme_members({str(i): {'記憶體'} for i in range(5)}, [str(i) for i in range(5)])
        self.assertEqual(len(m['記憶體']), 5)

    def test_snapshot_ranks_strong_theme_first(self):
        px, panels, codes = synthetic()
        snap = radar.snapshot(panels, codes, px.index[100])
        self.assertEqual(snap.index[0], 'T0')
        self.assertEqual(snap.loc['T0', 'members'], 6)
        self.assertAlmostEqual(snap.loc['T0', 'score'], 1.0)

    def test_backtest_picks_up_real_edge(self):
        px, panels, codes = synthetic()
        r = radar.backtest(panels, px, lambda d: codes, start='2020-01-01', end=px.index[-1], top_k=1)
        self.assertEqual(r.summary['periods'], (400 - 20 + 19) // 20)
        self.assertTrue((r.trades['top'] == 'T0').all())
        self.assertGreater(r.summary['top_vs_bottom'], 0.02)     # 每 20 天約 +4%（扣成本後仍明顯）
        self.assertGreater(r.summary['top_vs_bottom_t'], 3)


class TestRefreshChains(unittest.TestCase):
    def test_missing_first_then_stale(self):
        import os, tempfile, time
        from pathlib import Path
        from unittest import mock
        from src.app.lab import themes
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            for c, age_days in (('OLD', 200), ('NEW', 1)):
                p = cache / f'{c}.json'
                p.write_text('[]', encoding='utf-8')
                t = time.time() - age_days * 86400
                os.utime(p, (t, t))
            fetched = []
            with mock.patch.object(themes, 'CACHE', cache), \
                    mock.patch.object(themes, 'company_chain', side_effect=lambda c, refresh=False: fetched.append(c)):
                n = themes.refresh_chains(['NEW', 'OLD', 'IPO1', 'IPO2'], max_fetch=3, log=lambda m: None)
        self.assertEqual(n, 3)
        self.assertEqual(fetched, ['IPO1', 'IPO2', 'OLD'])        # 先補沒分類的，再更新過期的；未過期的不動


if __name__ == '__main__':
    unittest.main()
