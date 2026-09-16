"""Coverage underforecast candidates: analytic oracles and owner-reported case."""
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import ast
import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon
import tcf_pipeline as p
from methodology_validation.test_methodology_v0_1 import (
    _gdf, _projected_rect, _run_with_analytic_truth, EMPTY)


class CoverageUnderforecastTests(unittest.TestCase):
    def setUp(self):
        self.truth = _projected_rect(0, 0, 200000, 100000)
        self.core = _projected_rect(20000, 10000, 150000, 80000)

    def forecasts(self, higher=None):
        rows = [dict(geometry=self.truth, feat_type='AREA', coverage=3)]
        if higher:
            rows.append(dict(geometry=self.core, feat_type=higher[0], coverage=higher[1]))
        return gpd.GeoDataFrame(rows, crs=4326)

    def candidates(self, forecasts=None, core=None, misses=None, params=None):
        return p._build_coverage_underforecasts(
            _gdf(self.core if core is None else core), _gdf(self.truth),
            self.forecasts() if forecasts is None else forecasts,
            misses or [], params or p.GradingParams())

    def test_sparse_can_verify_well_and_have_underforecast(self):
        result = _run_with_analytic_truth(
            self.truth, 3, self.truth, self.core, return_result=True)
        self.assertEqual(result['graded_forecasts'][0]['category'], 'Verified Well')
        self.assertEqual(result['graded_misses'], [])
        self.assertEqual(len(result['coverage_underforecasts']), 1)

    def test_medium_and_solid_line_each_capture_shared_truth(self):
        for higher in [('AREA', 2), ('LINE', 1)]:
            with self.subTest(higher=higher):
                self.assertEqual(self.candidates(self.forecasts(higher)), [])

    def test_no_sparse_or_substantial_core_no_candidate(self):
        self.assertEqual(self.candidates(EMPTY), [])
        self.assertEqual(self.candidates(core=_projected_rect(0, 0, 40000, 100000)), [])

    def test_no_duplicate_of_parent_candidate_miss(self):
        self.assertEqual(self.candidates(misses=[{'sparse_component_id': 1}]), [])

    def test_threshold_equality(self):
        c = self.candidates()[0]
        params = replace(p.GradingParams(),
                         medium_core_review_min_area_m2=c['medium_area_km2'] * 1e6,
                         miss_capture_threshold=c['sparse_forecast_capture_fraction'])
        self.assertEqual(len(self.candidates(params=params)), 1)
        # Higher capture is strict < threshold; equality suppresses.
        params = replace(p.GradingParams(), miss_capture_threshold=0)
        self.assertEqual(self.candidates(params=params), [])

    def test_approval_and_both_report_paths(self):
        candidates = gpd.GeoDataFrame(self.candidates(), crs=4326)
        table = p.build_review_table(EMPTY, EMPTY, EMPTY,
                                    gdf_coverage_underforecasts=candidates)
        self.assertFalse(table.iloc[0].approved_for_report)
        # Load pure report functions without importing the Streamlit runtime.
        tree = ast.parse(Path('dashboard_workstation.py').read_text())
        names = {'_default_review_result', '_review_result', '_build_reviewed_report'}
        module = ast.Module(body=[n for n in tree.body
                                 if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[])
        namespace = {'np': np, 'REVIEW_RESULTS': ['Verified Well','Verified Close','Overforecast','Missed']}
        exec(compile(module, 'dashboard_workstation.py', 'exec'), namespace)
        reporters = [p.build_report, lambda *args: namespace['_build_reviewed_report'](*args, pipeline=p)]
        for reporter in reporters:
            self.assertNotIn('underforecast', reporter(table, datetime(2026,9,16,9), 5, 4))
        table.loc[0, 'approved_for_report'] = True
        for label in ['Medium', 'Solid Line']:
            table.loc[0, 'underforecast_type'] = label
            for reporter in reporters:
                self.assertIn(label + ' underforecast', reporter(table, datetime(2026,9,16,9), 5, 4))
        with self.assertRaises(ValueError):
            p.underforecast_label('Sparse')

    def test_september16_replay(self):
        root = Path('tests/fixtures/20260916_05Z_F04')
        a = np.load(root / 'arrays.npz')
        forecasts = p.parse_iem_cow_text((root / 'tcf_raw.txt').read_text())
        r = p.run_verification(forecasts, a['max_tops'], a['max_refl'], a['lons'], a['lats'],
                               datetime(2026,9,16,9), 5, 4, EMPTY,
                               qualifying_mask=a['qualifying_mask'])
        self.assertEqual([x['category'] for x in r['graded_forecasts']],
                         ['Verified Well','Verified Well','Overforecasted','Overforecasted','Verified Close'])
        self.assertEqual(len(r['graded_misses']), 4)
        self.assertEqual(len(r['coverage_underforecasts']), 1)
        candidate = r['coverage_underforecasts'][0]
        self.assertAlmostEqual(candidate['medium_area_km2'], 57861.3, delta=1)
        self.assertAlmostEqual(candidate['sparse_forecast_capture_fraction'], .73255, places=4)
        self.assertEqual(candidate['higher_coverage_capture_fraction'], 0)
        self.assertEqual(r['medium_core_review_flags'], [])
        self.assertFalse(r['review_table'].query("kind == 'coverage_underforecast'").iloc[0].approved_for_report)
        self.assertNotIn('underforecast', r['report_text'])
        # Florida remains below the existing Candidate Miss floor.
        self.assertTrue(all(x['geometry'].centroid.y > 30 for x in r['graded_misses']))

if __name__ == '__main__': unittest.main()
