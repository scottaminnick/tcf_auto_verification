"""Independent analytic tests for established Methodology Specification 0.1 rules.

Every test docstring records (1) the requirement, (2) its independent oracle,
(3) current conformance, and (4) the responsible production function on failure.
Open methodology decisions are intentionally absent.
"""

from datetime import date, datetime
import unittest
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiPolygon, Polygon, box

import tcf_pipeline


EMPTY = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def _gdf(geometry):
    """One-geometry WGS84 frame, or a fresh empty frame."""
    if geometry is None or geometry.is_empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:4326")


def _run_with_analytic_truth(forecast_geometry, coverage_code, sparse_truth,
                             medium_truth, *, tops=None, refl=None,
                             lons=None, lats=None, return_result=False,
                             feature_type="AREA"):
    """Run production grading while replacing raster truth with analytic geometry.

    This does not derive an expected value from production. It isolates the
    production overlap/category/echo-top calculation so tests can compare it to
    independently constructed geometric oracles.
    """
    forecast = gpd.GeoDataFrame(
        [{"geometry": forecast_geometry, "coverage": coverage_code,
          "feat_type": feature_type}], crs="EPSG:4326")
    if lons is None:
        lons = np.linspace(-120, -60, 7)
    if lats is None:
        lats = np.linspace(20, 55, 8)
    shape = (len(lats), len(lons))
    if tops is None:
        tops = np.zeros(shape, dtype=float)
    if refl is None:
        refl = np.zeros(shape, dtype=float)

    # Scoring Sparse, scoring Medium, then independently filtered Sparse
    # Candidate Miss triage geometry.
    truth_results = [_gdf(sparse_truth), _gdf(medium_truth), _gdf(sparse_truth)]
    with patch.object(tcf_pipeline, "extract_tcf_polygons",
                      side_effect=truth_results), \
         patch.object(tcf_pipeline, "verification_domain",
                      return_value=box(-180, -90, 180, 90)):
        result = tcf_pipeline.run_verification(
            forecast, tops, refl, lons, lats,
            datetime(2026, 5, 24, 23), 19, 4, EMPTY,
            qualifying_mask=((refl >= 40.0) & (tops >= 25.0)))
    return result if return_result else result["graded_forecasts"][0]


class ForecastThresholdTests(unittest.TestCase):
    """Approved AREA overlap math and category boundaries (Spec §§12–14)."""

    FORECAST = box(-100, 35, -90, 45)

    def _grade(self, fraction):
        # Independently solve for the longitude whose EPSG:5070 intersection is
        # the requested fraction. This keeps boundary tests about >= comparisons
        # after physical-area correction rather than assuming degree-space width.
        if fraction == 0:
            truth = Polygon()
        elif fraction == 1:
            truth = self.FORECAST
        else:
            forecast_m = gpd.GeoSeries(
                [self.FORECAST], crs="EPSG:4326").to_crs("EPSG:5070").iloc[0]
            low, high = -100.0, -90.0
            for _ in range(60):
                split = (low + high) / 2
                candidate = box(-100, 35, split, 45)
                candidate_m = gpd.GeoSeries(
                    [candidate], crs="EPSG:4326").to_crs("EPSG:5070").iloc[0]
                ratio = forecast_m.intersection(candidate_m).area / forecast_m.area
                if ratio < fraction:
                    low = split
                else:
                    high = split
            # `high` is the first representable split whose independently
            # projected ratio is >= the requested cutoff, which makes the exact
            # inclusive-boundary oracle robust to the final floating operation.
            truth = box(-100, 35, high, 45)
        return _run_with_analytic_truth(self.FORECAST, 3, truth, Polygon())

    def test_zero_and_full_overlap(self):
        """Requirement: 0% is Overforecasted and 100% is Verified Well.

        Oracle: intersections of identical/disjoint rectangles are exactly the
        full/zero 100-square-unit forecast. Current implementation: PASS.
        """
        zero = self._grade(0.0)
        full = self._grade(1.0)
        self.assertEqual(zero["coverage_fraction"], 0.0)
        self.assertEqual(zero["category"], "Overforecasted")
        self.assertEqual(full["coverage_fraction"], 1.0)
        self.assertEqual(full["category"], "Verified Well")

    def test_twenty_percent_exact_and_near_boundary(self):
        """Requirement: >=20% is Close; below 20% is Overforecasted.

        Oracle: an independent EPSG:5070 bisection constructs intersections at
        0.2±1e-6. Current implementation: PASS (`run_verification`).
        """
        below = self._grade(0.2 - 1e-6)
        exact = self._grade(0.2)
        above = self._grade(0.2 + 1e-6)
        self.assertEqual(below["category"], "Overforecasted")
        self.assertEqual(exact["category"], "Verified Close")
        self.assertEqual(above["category"], "Verified Close")

    def test_fifty_percent_exact_and_near_boundary(self):
        """Requirement: >=50% is Well; just below is Close.

        Oracle: an independent EPSG:5070 bisection constructs intersections at
        0.5±1e-6. Current implementation: PASS (`run_verification`).
        """
        below = self._grade(0.5 - 1e-6)
        exact = self._grade(0.5)
        above = self._grade(0.5 + 1e-6)
        self.assertEqual(below["category"], "Verified Close")
        self.assertEqual(exact["category"], "Verified Well")
        self.assertEqual(above["category"], "Verified Well")


class CoverageFieldSelectionTests(unittest.TestCase):
    """Established Sparse and Medium truth selection (Spec §11)."""

    def test_sparse_selects_twenty_five_percent_truth(self):
        """Requirement: Sparse (code 3) uses the 25% truth field.

        Oracle: sparse truth equals the forecast while medium truth is empty, so
        the only approved result is 100%/Well. Current implementation: PASS.
        """
        forecast = box(0, 0, 10, 10)
        grade = _run_with_analytic_truth(forecast, 3, forecast, Polygon())
        self.assertEqual(grade["coverage_fraction"], 1.0)
        self.assertEqual(grade["category"], "Verified Well")

    def test_medium_selects_forty_percent_truth(self):
        """Requirement: Medium (code 2) uses the 40% truth field.

        Oracle: sparse truth is empty and medium truth equals the forecast, so
        the only approved result is 100%/Well. Current implementation: PASS.
        """
        forecast = box(0, 0, 10, 10)
        grade = _run_with_analytic_truth(forecast, 2, Polygon(), forecast)
        self.assertEqual(grade["coverage_fraction"], 1.0)
        self.assertEqual(grade["category"], "Verified Well")


class Decision1ATests(unittest.TestCase):
    """Approved same-pair qualification and Boolean window union."""

    @staticmethod
    def union(pairs):
        result = None
        for refl, tops in pairs:
            current = tcf_pipeline._pair_qualifying_mask(refl, tops)
            result = current.copy() if result is None else (result | current)
        return result

    def test_same_pair_qualifies(self):
        result = self.union([(np.array([[45.0]]), np.array([[30.0]]))])
        self.assertTrue(result[0, 0])

    def test_false_temporal_conjunction_is_rejected(self):
        pairs = [
            (np.array([[45.0]]), np.array([[22.0]])),
            (np.array([[30.0]]), np.array([[30.0]])),
        ]
        paired = self.union(pairs)
        independent = ((np.maximum.reduce([p[0] for p in pairs]) >= 40.0)
                       & (np.maximum.reduce([p[1] for p in pairs]) >= 25.0))
        self.assertTrue(independent[0, 0])
        self.assertFalse(paired[0, 0])

    def test_one_qualifying_pair_anywhere_in_window(self):
        pairs = [
            (np.array([[20.0]]), np.array([[20.0]])),
            (np.array([[45.0]]), np.array([[30.0]])),
            (np.array([[50.0]]), np.array([[20.0]])),
        ]
        self.assertTrue(self.union(pairs)[0, 0])

    def test_different_locations_and_times_survive_union(self):
        pairs = [
            (np.array([[45.0, 20.0]]), np.array([[30.0, 20.0]])),
            (np.array([[20.0, 45.0]]), np.array([[20.0, 30.0]])),
        ]
        np.testing.assert_array_equal(self.union(pairs), [[True, True]])

    def test_duplicate_pairs_and_order_do_not_change_union(self):
        a = (np.array([[45.0, 20.0]]), np.array([[30.0, 20.0]]))
        b = (np.array([[20.0, 45.0]]), np.array([[20.0, 30.0]]))
        np.testing.assert_array_equal(self.union([a, b]), self.union([a, a, b]))
        np.testing.assert_array_equal(self.union([a, b]), self.union([b, a]))

    def test_methods_agree_when_joint_criteria_coexist(self):
        pairs = [
            (np.array([[45.0, 20.0]]), np.array([[30.0, 20.0]])),
            (np.array([[20.0, 50.0]]), np.array([[20.0, 35.0]])),
        ]
        paired = self.union(pairs)
        independent = ((np.maximum.reduce([p[0] for p in pairs]) >= 40.0)
                       & (np.maximum.reduce([p[1] for p in pairs]) >= 25.0))
        np.testing.assert_array_equal(paired, independent)

    def test_pair_first_is_subset_of_independent_conjunction(self):
        rng = np.random.default_rng(20260818)
        pairs = [(rng.uniform(0, 70, (8, 9)), rng.uniform(0, 50, (8, 9)))
                 for _ in range(7)]
        paired = self.union(pairs)
        independent = ((np.maximum.reduce([p[0] for p in pairs]) >= 40.0)
                       & (np.maximum.reduce([p[1] for p in pairs]) >= 25.0))
        self.assertFalse(np.any(paired & ~independent))

    def test_run_verification_uses_supplied_pair_mask_not_maxima(self):
        """Load-bearing regression: synthetic conjunction cannot seed truth."""
        refl = np.array([[45.0, 30.0], [0.0, 0.0]])
        tops = np.array([[22.0, 30.0], [0.0, 0.0]])
        paired = np.zeros((2, 2), dtype=bool)
        captured = []

        def capture(mask, *_args, **_kwargs):
            captured.append(np.asarray(mask).copy())
            return EMPTY.copy()

        params = tcf_pipeline.GradingParams(
            dilation_iterations=0, smoothing_size=1, min_area_m2=0,
            apply_domain_mask=False)
        with patch.object(tcf_pipeline, "extract_tcf_polygons", side_effect=capture):
            tcf_pipeline.run_verification(
                EMPTY, tops, refl, np.array([-100.0, -99.0]),
                np.array([40.0, 41.0]), datetime(2026, 5, 24, 23),
                19, 4, EMPTY, params, qualifying_mask=paired)
        self.assertEqual(len(captured), 3)
        self.assertFalse(captured[0].any())
        self.assertFalse(captured[1].any())
        self.assertFalse(captured[2].any())

    def test_missing_required_mask_is_a_type_error(self):
        with self.assertRaises(TypeError):
            tcf_pipeline.run_verification(
                EMPTY, np.zeros((1, 1)), np.zeros((1, 1)),
                np.array([0.0]), np.array([0.0]), datetime(2026, 1, 1),
                1, 4, EMPTY)

    def test_mismatched_qualifying_grid_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "qualifying_mask"):
            tcf_pipeline.run_verification(
                EMPTY, np.zeros((2, 2)), np.zeros((2, 2)),
                np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                datetime(2026, 1, 1), 1, 4, EMPTY,
                qualifying_mask=np.zeros((1, 1), dtype=bool))


class PhysicalGeometryTests(unittest.TestCase):
    """Established physical-area and topology requirements (Spec §§3, 12, 19)."""

    def test_minimum_area_is_candidate_miss_only(self):
        """Forecast truth has no area floor; Candidate Miss triage retains it."""
        minimums = []

        def capture(_mask, _lons, _lats, min_area_m2=0, **_kwargs):
            minimums.append(min_area_m2)
            return EMPTY.copy()

        params = tcf_pipeline.GradingParams(apply_domain_mask=False)
        with patch.object(tcf_pipeline, "extract_tcf_polygons", side_effect=capture):
            tcf_pipeline.run_verification(
                EMPTY, np.zeros((2, 2)), np.zeros((2, 2)),
                np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                datetime(2026, 1, 1), 1, 4, EMPTY, params,
                qualifying_mask=np.zeros((2, 2), dtype=bool))
        self.assertEqual(minimums, [0, 0, params.min_area_m2])

    def test_sub_15000_component_still_scores_forecast(self):
        """A small qualifying component is scoring truth, but not a candidate."""
        lons = np.array([-100.05, -100.00, -99.95])
        lats = np.array([39.95, 40.00, 40.05])
        qualifying = np.zeros((3, 3), dtype=bool)
        qualifying[1, 1] = True
        forecast = gpd.GeoDataFrame([{
            "geometry": box(-100.025, 39.975, -99.975, 40.025),
            "coverage": 3, "feat_type": "AREA",
        }], crs="EPSG:4326")
        params = tcf_pipeline.GradingParams(
            dilation_iterations=0, smoothing_size=1, apply_domain_mask=False)
        result = tcf_pipeline.run_verification(
            forecast, np.full((3, 3), 30.0), np.full((3, 3), 45.0),
            lons, lats, datetime(2026, 1, 1), 1, 4, EMPTY, params,
            qualifying_mask=qualifying)
        self.assertAlmostEqual(
            result["graded_forecasts"][0]["coverage_fraction"], 1.0, places=6)
        self.assertEqual(result["graded_misses"], [])

    def test_overlap_fraction_uses_physical_area(self):
        """Requirement: AREA numerator/denominator use physical-area projection.

        Oracle: transform a 30-degree north/south forecast and its northern half
        independently to EPSG:5070, then divide Shapely areas. Degree-space gives
        exactly 0.5 but physical area does not. Current implementation: PASS in
        `run_verification`, which measures projected EPSG:5070 copies.
        """
        forecast = box(-110, 20, -90, 50)
        truth = box(-110, 35, -90, 50)
        pair = gpd.GeoSeries([forecast, truth], crs="EPSG:4326").to_crs("EPSG:5070")
        expected = pair.iloc[1].area / pair.iloc[0].area
        self.assertGreater(abs(expected - 0.5), 0.01)  # proves oracle discriminates

        grade = _run_with_analytic_truth(forecast, 3, truth, Polygon())
        self.assertAlmostEqual(grade["coverage_fraction"], expected, places=10)

    def test_physical_ratio_tracks_latitude_dependent_area(self):
        """Requirement: overlap uses physical area at substantially different latitudes.

        Oracle: each northern-half ratio is independently projected to EPSG:5070;
        the ratios differ because equal latitude spans do not have equal physical
        area. Production must match each oracle, not return degree-space 0.5.
        """
        cases = [
            (box(-110, 20, -90, 40), box(-110, 30, -90, 40)),
            (box(-110, 35, -90, 55), box(-110, 45, -90, 55)),
        ]
        actual = []
        expected = []
        for forecast, truth in cases:
            projected = gpd.GeoSeries(
                [forecast, truth], crs="EPSG:4326").to_crs("EPSG:5070")
            expected.append(projected.iloc[1].area / projected.iloc[0].area)
            actual.append(_run_with_analytic_truth(
                forecast, 3, truth, Polygon())["coverage_fraction"])
        self.assertGreater(abs(expected[0] - expected[1]), 0.01)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)

    def test_known_projected_intersection_ratio(self):
        """Requirement: numerator is the physical forecast/truth intersection.

        Oracle: project both rectangles, intersect them independently, and divide
        by the independently projected forecast area.
        """
        forecast = box(-105, 25, -85, 48)
        truth = box(-100, 32, -80, 42)
        projected = gpd.GeoSeries(
            [forecast, truth], crs="EPSG:4326").to_crs("EPSG:5070")
        expected = projected.iloc[0].intersection(projected.iloc[1]).area / projected.iloc[0].area
        grade = _run_with_analytic_truth(forecast, 3, truth, Polygon())
        self.assertAlmostEqual(grade["coverage_fraction"], expected, places=12)

    def test_missed_event_capture_uses_physical_area(self):
        """Requirement: miss capture uses physical intersection/truth area.

        Oracle: the forecast covers exactly 20% of the truth in degree latitude
        but less than 20% after EPSG:5070 projection, so the unchanged strict
        miss threshold must identify it as missed.
        """
        truth = box(-110, 20, -90, 50)
        forecast = box(-110, 44, -90, 50)
        projected = gpd.GeoSeries(
            [truth, forecast], crs="EPSG:4326").to_crs("EPSG:5070")
        expected = projected.iloc[0].intersection(projected.iloc[1]).area / projected.iloc[0].area
        self.assertLess(expected, 0.20)
        result = _run_with_analytic_truth(
            forecast, 3, truth, Polygon(), return_result=True)
        self.assertEqual(len(result["graded_misses"]), 1)

    def test_multipart_truth_components_all_contribute(self):
        """Requirement: physical overlap includes every multipart component.

        Oracle: independently project a two-component truth MultiPolygon and
        divide its intersection with the forecast by forecast physical area.
        """
        forecast = box(-110, 25, -80, 50)
        truth = MultiPolygon([
            box(-108, 27, -102, 35),
            box(-90, 38, -82, 48),
        ])
        projected = gpd.GeoSeries(
            [forecast, truth], crs="EPSG:4326").to_crs("EPSG:5070")
        expected = projected.iloc[0].intersection(projected.iloc[1]).area / projected.iloc[0].area
        grade = _run_with_analytic_truth(forecast, 3, truth, Polygon())
        self.assertAlmostEqual(grade["coverage_fraction"], expected, places=12)

    def test_forecast_hole_excluded_from_overlap(self):
        """Requirement: physical forecast geometry, including holes, is respected.

        Oracle: truth lying wholly in an interior ring has empty intersection
        with the donut forecast. Current implementation: PASS through Shapely in
        `run_verification`.
        """
        forecast = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(3, 3), (7, 3), (7, 7), (3, 7)]])
        truth_in_hole = box(4, 4, 6, 6)
        grade = _run_with_analytic_truth(forecast, 3, truth_in_hole, Polygon())
        self.assertEqual(grade["coverage_fraction"], 0.0)

    def test_forecast_hole_excluded_from_echo_top(self):
        """Requirement: echo-top samples respect polygon holes (Spec §19).

        Oracle: all valid exterior samples are 30 kft; only samples in the hole
        are 60 kft, so every percentile of included samples is 30. Current
        implementation: PASS in `run_verification`.
        """
        forecast = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(3, 3), (7, 3), (7, 7), (3, 7)]])
        lons = np.arange(0.5, 10, 1.0)
        lats = np.arange(0.5, 10, 1.0)
        tops = np.full((10, 10), 30.0)
        refl = np.full((10, 10), 40.0)
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        tops[(lon_grid > 3) & (lon_grid < 7) &
             (lat_grid > 3) & (lat_grid < 7)] = 60.0
        grade = _run_with_analytic_truth(
            forecast, 3, forecast, Polygon(), tops=tops, refl=refl,
            lons=lons, lats=lats)
        self.assertEqual(grade["top"], 30.0)

    def test_unavailable_echo_top_is_not_numeric_zero(self):
        """Requirement: unavailable echo-top data must not be represented as zero.

        Oracle: neither all-NaN observations nor a finite field with zero
        qualifying samples supplies the six observations required for P90.
        Both are unavailable, never a fabricated meteorological zero.
        """
        forecast = box(0, 0, 10, 10)
        lons = np.arange(0.5, 10, 1.0)
        lats = np.arange(0.5, 10, 1.0)
        unavailable = _run_with_analytic_truth(
            forecast, 3, Polygon(), Polygon(),
            tops=np.full((10, 10), np.nan),
            refl=np.full((10, 10), np.nan),
            lons=lons, lats=lats)
        no_qualifying_samples = _run_with_analytic_truth(
            forecast, 3, Polygon(), Polygon(),
            tops=np.zeros((10, 10)), refl=np.zeros((10, 10)),
            lons=lons, lats=lats)
        self.assertIsNone(no_qualifying_samples["top"])
        self.assertIsNone(unavailable["top"])

    def test_echo_top_minimum_sample_semantics(self):
        """Fewer than six qualifying cells is unavailable; six preserves P90."""
        for count, expected in ((0, None), (1, None), (5, None),
                                (6, 30.0), (7, 30.0)):
            width = max(count, 1)
            forecast = box(0, 0, width, 2)
            lons = np.arange(0.5, width, 1.0)
            lats = np.array([0.5])
            tops = np.full((1, width), 30.0)
            if count == 1:
                tops[:] = 45.0
            refl = np.full((1, width), 45.0)
            if count == 0:
                refl[:] = 0.0
            grade = _run_with_analytic_truth(
                forecast, 3, Polygon(), Polygon(), tops=tops, refl=refl,
                lons=lons, lats=lats)
            with self.subTest(valid_cell_count=count):
                if expected is None:
                    self.assertIsNone(grade["top"])
                else:
                    self.assertEqual(grade["top"], expected)

    def test_missing_echo_top_cells_do_not_satisfy_minimum(self):
        """NaNs do not turn five qualifying samples into the required six."""
        forecast = box(0, 0, 6, 2)
        lons = np.arange(0.5, 6, 1.0)
        lats = np.array([0.5])
        tops = np.array([[30.0, 30.0, 30.0, 30.0, 30.0, np.nan]])
        refl = np.full((1, 6), 45.0)
        grade = _run_with_analytic_truth(
            forecast, 3, Polygon(), Polygon(), tops=tops, refl=refl,
            lons=lons, lats=lats)
        self.assertIsNone(grade["top"])

    def test_simple_polygon_known_echo_top_samples(self):
        """Requirement: complete polygon membership retains the existing statistic.

        Oracle: ten interior qualifying cells all contain 35 kft, so the unchanged
        90th percentile is exactly 35 kft.
        """
        forecast = box(0, 0, 10, 2)
        lons = np.arange(0.5, 10, 1.0)
        lats = np.array([0.5])
        tops = np.full((1, 10), 35.0)
        refl = np.full((1, 10), 45.0)
        grade = _run_with_analytic_truth(
            forecast, 3, forecast, Polygon(), tops=tops, refl=refl,
            lons=lons, lats=lats)
        self.assertEqual(grade["top"], 35.0)

    def test_multipart_echo_top_uses_all_components_and_their_holes(self):
        """Requirement: every MultiPolygon component contributes, holes do not.

        Oracle: component one contains 30-kft cells and component two contains
        60-kft cells; 100-kft cells lie only in component two's hole. The combined
        90th percentile is 60 kft and is attached to both exploded grade records.
        """
        component_one = box(0, 0, 4, 4)
        component_two = Polygon(
            [(6, 0), (10, 0), (10, 4), (6, 4)],
            holes=[[(7, 1), (9, 1), (9, 3), (7, 3)]])
        forecast = MultiPolygon([component_one, component_two])
        lons = np.arange(0.5, 10, 1.0)
        lats = np.arange(0.5, 4, 1.0)
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        tops = np.full(lon_grid.shape, 30.0)
        tops[lon_grid > 6] = 60.0
        tops[(lon_grid > 7) & (lon_grid < 9) &
             (lat_grid > 1) & (lat_grid < 3)] = 100.0
        refl = np.full(lon_grid.shape, 45.0)
        result = _run_with_analytic_truth(
            forecast, 3, forecast, Polygon(), tops=tops, refl=refl,
            lons=lons, lats=lats, return_result=True)
        self.assertEqual(len(result["graded_forecasts"]), 2)
        self.assertEqual([record["top"] for record in result["graded_forecasts"]],
                         [60.0, 60.0])

    def test_echo_top_membership_keeps_boundary_points_outside(self):
        """Requirement: correcting holes must not silently change boundary policy.

        Oracle: strict Shapely `contains` excludes points on x=0/x=1 and includes
        the interior point x=0.5.
        """
        mask = tcf_pipeline._geometry_point_mask(
            box(0, 0, 1, 1),
            np.array([[0.0, 0.5, 1.0]]),
            np.array([[0.5, 0.5, 0.5]]))
        np.testing.assert_array_equal(mask, [[False, True, False]])

    def test_nullable_review_table_and_unavailable_report_top(self):
        """Requirement: reviewer data distinguishes unavailable from numeric zero.

        Oracle: nullable Float64 preserves NA and 0 as distinct values; neither
        should fabricate a positive FAA top annotation.
        """
        records = [
            {"geometry": box(0, 0, 1, 1), "category": "Verified Well",
             "color": "lime", "idx": 1, "top": None, "coverage": 3,
             "feat_type": "AREA", "coverage_fraction": 1.0},
            {"geometry": box(2, 0, 3, 1), "category": "Overforecasted",
             "color": "orange", "idx": 2, "top": 0.0, "coverage": 3,
             "feat_type": "AREA", "coverage_fraction": 0.0},
        ]
        graded = gpd.GeoDataFrame(records, crs="EPSG:4326")
        table = tcf_pipeline.build_review_table(graded, EMPTY, EMPTY)
        self.assertEqual(str(table["top_kft"].dtype), "Float64")
        self.assertTrue(pd.isna(table.loc[0, "top_kft"]))
        self.assertEqual(table.loc[1, "top_kft"], 0.0)
        round_trip = table.copy().astype(tcf_pipeline.REVIEW_COLUMNS)
        self.assertTrue(pd.isna(round_trip.loc[0, "top_kft"]))
        self.assertEqual(round_trip.loc[1, "top_kft"], 0.0)
        report = tcf_pipeline.build_report(
            round_trip, datetime(2026, 5, 24, 23), 19, 4)
        self.assertNotIn("[Top:", report)

    def test_candidate_miss_requires_explicit_report_approval(self):
        """Automated candidates cannot become FAA-facing Missed by default."""
        candidate = gpd.GeoDataFrame([{
            "geometry": box(0, 0, 2, 2), "category": "Candidate Miss",
            "color": "red", "idx": 1,
        }], crs="EPSG:4326")
        table = tcf_pipeline.build_review_table(EMPTY, candidate, EMPTY)
        self.assertEqual(table.loc[0, "kind"], "candidate_miss")
        self.assertFalse(table.loc[0, "approved_for_report"])
        report = tcf_pipeline.build_report(
            table, datetime(2026, 5, 24, 23), 19, 4)
        self.assertNotIn(" - Missed (", report)
        approved = table.copy()
        approved.loc[0, "approved_for_report"] = True
        report = tcf_pipeline.build_report(
            approved.astype(tcf_pipeline.REVIEW_COLUMNS),
            datetime(2026, 5, 24, 23), 19, 4)
        self.assertIn(" - Missed (Area M1)", report)
        approved.loc[0, "approved_for_report"] = False
        revoked = tcf_pipeline.build_report(
            approved.astype(tcf_pipeline.REVIEW_COLUMNS),
            datetime(2026, 5, 24, 23), 19, 4)
        self.assertNotIn(" - Missed (", revoked)

    def test_forecasts_default_approved_and_candidate_gate_is_independent(self):
        forecast = gpd.GeoDataFrame([{
            "geometry": box(0, 0, 2, 2), "category": "Verified Well",
            "color": "lime", "idx": 1, "top": None, "coverage": 3,
            "feat_type": "AREA", "coverage_fraction": 1.0,
        }], crs="EPSG:4326")
        candidate = gpd.GeoDataFrame([{
            "geometry": box(3, 0, 5, 2), "category": "Candidate Miss",
            "color": "red", "idx": 1,
        }], crs="EPSG:4326")
        table = tcf_pipeline.build_review_table(forecast, candidate, EMPTY)
        self.assertTrue(table.loc[table["kind"] == "forecast",
                                  "approved_for_report"].iloc[0])
        self.assertFalse(table.loc[table["kind"] == "candidate_miss",
                                   "approved_for_report"].iloc[0])


class TruthPolygonizationTests(unittest.TestCase):
    """Established geometry preservation and physical minimum-area requirements."""

    @staticmethod
    def _block_fixture():
        lons = np.linspace(-105, -95, 30)
        lats = np.linspace(30, 40, 30)
        mask = np.zeros((30, 30), dtype=int)
        mask[5:25, 5:25] = 1
        dx = lons[1] - lons[0]
        dy = lats[1] - lats[0]
        footprint = box(lons[5] - dx / 2, lats[5] - dy / 2,
                        lons[24] + dx / 2, lats[24] + dy / 2)
        physical_area = gpd.GeoSeries(
            [footprint], crs="EPSG:4326").to_crs("EPSG:5070").iloc[0].area
        return mask, lons, lats, footprint, physical_area

    def test_minimum_area_retains_feature_just_above_floor(self):
        """Requirement: a physical feature above the configured floor survives.

        Oracle: the explicit 20x20 cell footprint is projected to EPSG:5070; a
        floor one square metre below that independent area must retain it. No
        smoothing/domain/order policy is selected because this directly tests
        polygonization plus the final filter. Current implementation: PASS.
        """
        mask, lons, lats, _, area = self._block_fixture()
        result = tcf_pipeline.extract_tcf_polygons(
            mask, lons, lats, min_area_m2=area - 1)
        self.assertFalse(result.empty)

    def test_minimum_area_rejects_feature_just_below_floor(self):
        """Requirement: a physical feature below the configured floor is removed.

        Oracle: the explicit projected cell footprint is one square metre below
        the supplied floor. No unresolved processing-order choice is involved.
        Current implementation: PASS in `extract_tcf_polygons`.
        """
        mask, lons, lats, _, area = self._block_fixture()
        result = tcf_pipeline.extract_tcf_polygons(
            mask, lons, lats, min_area_m2=area + 1)
        self.assertTrue(result.empty)

    def test_polygonization_preserves_cell_edges(self):
        """Requirement: representative raster geometry preserves physical topology.

        Oracle: a filled raster cell block occupies the independently derived
        outer cell-edge box, not a box of sample centers. Current implementation:
        PASS in `extract_tcf_polygons`.
        """
        mask, lons, lats, footprint, _ = self._block_fixture()
        result = tcf_pipeline.extract_tcf_polygons(mask, lons, lats)
        self.assertFalse(result.empty)
        actual = result.geometry.iloc[0]
        self.assertTrue(actual.equals(footprint))

    def test_polygonization_preserves_hole(self):
        """Requirement: representative truth features retain interior holes.

        Oracle: a binary outer block with a zero inner block is a polygon with
        exactly one interior ring. Current implementation: PASS in
        `extract_tcf_polygons`.
        """
        lons = np.linspace(-105, -95, 40)
        lats = np.linspace(30, 40, 40)
        mask = np.zeros((40, 40), dtype=int)
        mask[5:35, 5:35] = 1
        mask[15:25, 15:25] = 0
        result = tcf_pipeline.extract_tcf_polygons(mask, lons, lats)
        self.assertFalse(result.empty)
        geometry = result.geometry.iloc[0]
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        self.assertEqual(sum(len(poly.interiors) for poly in polygons), 1)

    def test_single_cell_is_its_full_footprint(self):
        """Requirement: one True center represents its complete cell footprint.

        Oracle: midpoint edges around center (-99, 41) are [-99.5, -98.5] and
        [40.5, 41.5].
        """
        lons = np.array([-100.0, -99.0, -98.0])
        lats = np.array([40.0, 41.0, 42.0])
        mask = np.zeros((3, 3), dtype=int)
        mask[1, 1] = 1
        actual = tcf_pipeline.extract_tcf_polygons(mask, lons, lats).geometry.iloc[0]
        self.assertTrue(actual.equals(box(-99.5, 40.5, -98.5, 41.5)))

    def test_two_by_two_block_is_expected_rectangle(self):
        """Requirement: edge-sharing cells dissolve without losing footprint area.

        Oracle: four unit cells centered at columns/rows 1–2 span midpoint edges
        0.5 through 2.5 in each dimension.
        """
        centers = np.arange(4.0)
        mask = np.zeros((4, 4), dtype=int)
        mask[1:3, 1:3] = 1
        actual = tcf_pipeline.extract_tcf_polygons(
            mask, centers, centers).geometry.iloc[0]
        self.assertTrue(actual.equals(box(0.5, 0.5, 2.5, 2.5)))

    def test_disconnected_components_remain_multipart(self):
        """Requirement: disconnected cell groups retain separate components.

        Oracle: two non-touching single-cell footprints have two components and
        total area two square coordinate units.
        """
        centers = np.arange(5.0)
        mask = np.zeros((5, 5), dtype=int)
        mask[1, 1] = 1
        mask[3, 3] = 1
        actual = tcf_pipeline.extract_tcf_polygons(
            mask, centers, centers).geometry.iloc[0]
        self.assertEqual(actual.geom_type, "MultiPolygon")
        self.assertEqual(len(actual.geoms), 2)
        self.assertEqual(actual.area, 2.0)

    def test_diagonal_contact_uses_four_neighbor_connectivity(self):
        """Requirement: corner-only connectivity is explicit and tested.

        Oracle/policy at polygonization: full closed cell footprints sharing only
        one point remain two polygon components; only shared edges dissolve.
        """
        centers = np.arange(3.0)
        mask = np.zeros((3, 3), dtype=int)
        mask[0, 0] = 1
        mask[1, 1] = 1
        actual = tcf_pipeline.extract_tcf_polygons(
            mask, centers, centers).geometry.iloc[0]
        self.assertEqual(actual.geom_type, "MultiPolygon")
        self.assertEqual(len(actual.geoms), 2)

    def test_raster_boundary_uses_half_cell_extrapolation(self):
        """Requirement: an edge cell has its proper outer footprint, no chord.

        Oracle: centers [10, 12, 14] imply edges [9, 11, 13, 15], including at
        the raster boundary.
        """
        centers = np.array([10.0, 12.0, 14.0])
        mask = np.zeros((3, 3), dtype=int)
        mask[0, 0] = 1
        actual = tcf_pipeline.extract_tcf_polygons(
            mask, centers, centers).geometry.iloc[0]
        self.assertTrue(actual.equals(box(9.0, 9.0, 11.0, 11.0)))

    def test_nonuniform_grid_uses_adjacent_midpoints(self):
        """Requirement: nonuniform monotonic centers use local midpoint edges.

        Oracle: longitude centers [0,1,3,6] give cell-2 edges [2,4.5]; descending
        latitude centers [15,12,10] give cell-1 edges [13.5,11]. This also
        exercises the orientation used by the frozen MRMS grids.
        """
        lons = np.array([0.0, 1.0, 3.0, 6.0])
        lats = np.array([15.0, 12.0, 10.0])
        mask = np.zeros((3, 4), dtype=int)
        mask[1, 2] = 1
        actual = tcf_pipeline.extract_tcf_polygons(mask, lons, lats).geometry.iloc[0]
        self.assertTrue(actual.equals(box(2.0, 11.0, 4.5, 13.5)))

    @staticmethod
    def _single_cell_near_area(target_m2):
        """Independent bisection for a one-degree-tall cell of target 5070 area."""
        low, high = 0.1, 5.0
        for _ in range(60):
            width = (low + high) / 2
            footprint = box(-100 - width / 2, 34.5,
                            -100 + width / 2, 35.5)
            area = gpd.GeoSeries(
                [footprint], crs="EPSG:4326").to_crs("EPSG:5070").iloc[0].area
            if area < target_m2:
                low = width
            else:
                high = width
        width = (low + high) / 2
        lons = np.array([-100 - width, -100.0, -100 + width])
        lats = np.array([34.0, 35.0, 36.0])
        mask = np.zeros((3, 3), dtype=int)
        mask[1, 1] = 1
        return mask, lons, lats

    def test_fixed_15000_km2_minimum_uses_corrected_footprints(self):
        """Requirement: existing 15,000 km² floor follows physical cell area.

        Oracle: independently construct one-cell footprints at 14,999 and 15,001
        km². The existing fixed threshold must reject and retain respectively.
        """
        below = self._single_cell_near_area(14_999_000_000)
        above = self._single_cell_near_area(15_001_000_000)
        rejected = tcf_pipeline.extract_tcf_polygons(
            *below, min_area_m2=15_000_000_000)
        retained = tcf_pipeline.extract_tcf_polygons(
            *above, min_area_m2=15_000_000_000)
        self.assertTrue(rejected.empty)
        self.assertFalse(retained.empty)


class TimeAndParserTests(unittest.TestCase):
    """Established time handling and visible input validation requirements."""

    def test_utc_date_rollover(self):
        """Requirement: IT + forecast hour explicitly crosses 00 UTC (Spec §6).

        Oracle: 21Z + 4 h is 01Z on the following calendar day. Current
        implementation: PASS (`compute_valid_dt`).
        """
        actual = tcf_pipeline.compute_valid_dt(date(2026, 4, 3), 21, 4)
        self.assertEqual(actual, datetime(2026, 4, 4, 1, 0))

    def test_unknown_coverage_code_is_rejected(self):
        """Requirement: unknown coverage meaning must not be inferred silently.

        Oracle: code 9 is outside the documented archived categories and must
        produce no accepted feature until its meaning is known. Current
        implementation: PASS in `parse_iem_cow_text`.
        """
        raw = "AREA 9 1 1 250 0 0 4 350 1000 350 990 360 990 360 1000 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertTrue(parsed.empty)

    def test_underfilled_coordinate_record_is_rejected(self):
        """Requirement: malformed TCF fields are surfaced, not silently repaired.

        Oracle: NPTS declares four pairs but only three exist, so the record is
        structurally incomplete and cannot be accepted as the declared feature.
        Current implementation: PASS in `parse_iem_cow_text`.
        """
        raw = "AREA 3 1 1 250 0 0 4 350 1000 350 990 360 990 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertTrue(parsed.empty)

    def test_valid_area_preserves_code_scaling_and_geometry(self):
        """Requirement: structural validation must not reinterpret valid AREA data.

        Oracle: tenths-degree coordinates form the rectangle [-100,-99] x [35,36]
        and raw coverage code 3 remains numeric 3.
        """
        raw = "AREA 3 1 1 250 0 0 4 350 1000 350 990 360 990 360 1000 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.iloc[0].coverage, 3)
        self.assertEqual(parsed.iloc[0].feat_type, "AREA")
        self.assertTrue(parsed.geometry.iloc[0].equals(box(-100, 35, -99, 36)))
        self.assertEqual(parsed.attrs["parse_diagnostics"], ())

    def test_valid_area_allows_archived_label_position_pair(self):
        """Requirement: preserve valid archived AREA syntax.

        Oracle: the declared four vertices define the rectangle; the one trailing
        pair is the archived label position and must not become a fifth vertex.
        """
        raw = ("AREA 2 1 1 250 0 0 4 350 1000 350 990 360 990 360 1000 "
               "355 995 END")
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.iloc[0].coverage, 2)
        self.assertTrue(parsed.geometry.iloc[0].equals(box(-100, 35, -99, 36)))

    def test_valid_line_retains_identity_and_buffer(self):
        """Requirement: valid LINE geometry and buffer policy remain unchanged.

        Oracle: the two scaled points define the source line; its expected result
        is the existing 0.15-degree Shapely buffer.
        """
        raw = "LINE 1 2 350 1000 360 990 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        expected = LineString([(-100, 35), (-99, 36)]).buffer(
            tcf_pipeline.LINE_BUFFER_DEG)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.iloc[0].coverage, 1)
        self.assertEqual(parsed.iloc[0].feat_type, "LINE")
        self.assertTrue(parsed.geometry.iloc[0].equals(expected))

    def test_underfilled_line_is_rejected(self):
        """Requirement: LINE NPTS must be satisfied by complete coordinate pairs."""
        raw = "LINE 1 3 350 1000 360 990 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertTrue(parsed.empty)
        diagnostic = parsed.attrs["parse_diagnostics"][0]
        self.assertEqual(diagnostic.reason, "coordinate_count_mismatch")
        self.assertEqual(diagnostic.declared_points, 3)
        self.assertEqual(diagnostic.available_coordinate_pairs, 2)

    def test_dangling_coordinate_is_rejected(self):
        """Requirement: a latitude without its longitude is structurally invalid."""
        raw = "LINE 1 2 350 1000 360 END"
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertTrue(parsed.empty)
        self.assertEqual(parsed.attrs["parse_diagnostics"][0].reason,
                         "coordinate_count_mismatch")

    def test_supported_feature_coverage_combinations_and_labels(self):
        """Requirement: AWC codes are valid and labeled only by feature type."""
        cases = (
            ("AREA", 2, "Medium"),
            ("AREA", 3, "Sparse"),
            ("LINE", 1, "Solid"),
        )
        for feature_type, code, label in cases:
            with self.subTest(feature_type=feature_type, code=code):
                if feature_type == "AREA":
                    raw = (f"AREA {code} 1 1 250 0 0 4 "
                           "350 1000 350 990 360 990 360 1000 END")
                else:
                    raw = f"LINE {code} 2 350 1000 360 990 END"
                parsed = tcf_pipeline.parse_iem_cow_text(raw)
                self.assertEqual(len(parsed), 1)
                self.assertEqual(parsed.iloc[0].coverage, code)
                self.assertEqual(tcf_pipeline._coverage_label(
                    feature_type, code), label)

        sparse = tcf_pipeline.get_tcf_coverage_semantics("AREA", 3)
        medium = tcf_pipeline.get_tcf_coverage_semantics("AREA", 2)
        solid = tcf_pipeline.get_tcf_coverage_semantics("LINE", 1)
        self.assertEqual((sparse.coverage_min, sparse.coverage_max, sparse.measure),
                         (0.25, 0.39, "areal"))
        self.assertEqual((medium.coverage_min, medium.coverage_max, medium.measure),
                         (0.40, 0.74, "areal"))
        self.assertEqual((solid.coverage_min, solid.coverage_max, solid.measure),
                         (0.75, 1.00, "linear"))

    def test_invalid_feature_coverage_combinations_are_rejected(self):
        """Requirement: AREA 1 and LINE 2/3 are not current AWC TCF records."""
        cases = (
            "AREA 1 1 1 250 0 0 4 350 1000 350 990 360 990 360 1000 END",
            "LINE 2 2 350 1000 360 990 END",
            "LINE 3 2 350 1000 360 990 END",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                parsed = tcf_pipeline.parse_iem_cow_text(raw)
                self.assertTrue(parsed.empty)
                diagnostic = parsed.attrs["parse_diagnostics"][0]
                self.assertEqual(diagnostic.reason,
                                 "unsupported_feature_coverage_combination")
                self.assertIn("feature/coverage combination", diagnostic.message)

    def test_unsupported_numeric_code_is_distinct_from_wrong_feature(self):
        parsed = tcf_pipeline.parse_iem_cow_text(
            "AREA 9 1 1 250 0 0 4 350 1000 350 990 360 990 360 1000 END")
        diagnostic = parsed.attrs["parse_diagnostics"][0]
        self.assertEqual(diagnostic.reason, "unsupported_coverage_code")
        self.assertEqual(diagnostic.message, "Unsupported TCF coverage code: 9")

    def test_coverage_code_alone_has_no_semantic_label(self):
        with self.assertRaises(TypeError):
            tcf_pipeline._coverage_label(1)
        with self.assertRaises(ValueError):
            tcf_pipeline.get_tcf_coverage_semantics("AREA", 1)
        with self.assertRaises(ValueError):
            tcf_pipeline.get_tcf_coverage_semantics("LINE", 3)

    def test_solid_line_retains_interim_medium_truth_scoring(self):
        """Semantics correction must not redesign buffered LINE verification."""
        forecast = LineString([(0, 5), (10, 5)]).buffer(
            tcf_pipeline.LINE_BUFFER_DEG)
        grade = _run_with_analytic_truth(
            forecast, 1, Polygon(), forecast, feature_type="LINE")
        self.assertAlmostEqual(grade["coverage_fraction"], 1.0, places=12)
        self.assertEqual(grade["category"], "Verified Well")

    def test_malformed_neighbor_does_not_remove_valid_feature(self):
        """Requirement: reject one bad record while preserving an adjacent valid one.

        Oracle: AREA declares four but provides three pairs; the following LINE
        independently provides two complete pairs.
        """
        raw = ("AREA 3 1 1 250 0 0 4 350 1000 350 990 360 990 END\n"
               "LINE 1 2 350 1000 360 990 END")
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed.iloc[0].feat_type, "LINE")
        diagnostics = parsed.attrs["parse_diagnostics"]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].feature_type, "AREA")
        self.assertEqual(diagnostics[0].reason, "coordinate_count_mismatch")

    def test_diagnostics_distinguish_coverage_and_coordinate_rejections(self):
        """Requirement: structured diagnostics retain distinct rejection reasons."""
        raw = ("LINE 9 2 350 1000 360 990 END\n"
               "LINE 1 2 350 1000 360 END\n"
               "LINE 1 2 350 1000 360 990 END")
        parsed = tcf_pipeline.parse_iem_cow_text(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            [diagnostic.reason for diagnostic in parsed.attrs["parse_diagnostics"]],
            ["unsupported_coverage_code", "coordinate_count_mismatch"])

    def test_report_labels_line_one_as_solid(self):
        """Requirement: FAA text calls valid LINE code 1 Solid, never Dense."""
        table = pd.DataFrame([{
            "idx": 4, "kind": "forecast", "category": "Overforecasted",
            "coverage_code": 1, "feat_type": "LINE", "artccs": "ZFW",
            "coverage_fraction": 0.0, "top_kft": 47.6, "boundary": False,
            "approved_for_report": True,
        }]).astype(tcf_pipeline.REVIEW_COLUMNS)
        report = tcf_pipeline.build_report(
            table, datetime(2026, 4, 4, 1), 21, 4)
        self.assertIn("ZFW - Solid (Line 4)", report)
        self.assertNotIn("Dense", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
