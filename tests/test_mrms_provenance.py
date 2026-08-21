"""Synthetic tests for factual MRMS composite provenance (no S3/network)."""

from datetime import datetime, timedelta
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import tcf_pipeline


VALID = datetime(2026, 5, 24, 23, 0)


def _key(product, actual):
    return (f"CONUS/{product}_00.50/{actual:%Y%m%d}/"
            f"MRMS_{product}_00.50_{actual:%Y%m%d-%H%M%S}.grib2.gz")


class SyntheticComposite:
    """Drive build_composite with controlled resolution/download/read outcomes."""

    def __init__(self, resolutions=None, download_failures=None,
                 read_failures=None, incompatible_requests=None):
        self.resolutions = resolutions or {}
        self.download_failures = set(download_failures or ())
        self.read_failures = set(read_failures or ())
        self.incompatible_requests = set(incompatible_requests or ())
        self.base_lons = np.array([-100.0, -99.0])
        self.base_lats = np.array([40.0, 41.0])

    def resolve(self, product, requested, s3=None):
        value = self.resolutions.get((product, requested), 0)
        if value is None:
            return None
        return _key(product, requested + timedelta(seconds=value))

    def download(self, key, dest_dir="mrms_data", s3=None):
        return None if key in self.download_failures else key

    def read(self, tops_file, refl_file, step):
        if tops_file in self.read_failures or refl_file in self.read_failures:
            raise OSError("synthetic decode failure")
        actual = tcf_pipeline._timestamp_from_mrms_key(tops_file)
        value = float((actual - VALID).total_seconds() / 60 + 20)
        tops = np.full((2, 2), value)
        refl = np.full((2, 2), value + 10)
        # Identify the nominal request from its nearest two-minute slot. Tests
        # use exact keys except where time offsets are the subject under test.
        requested = min(
            (VALID + timedelta(minutes=m) for m in (-2, 0, 2)),
            key=lambda dt: abs((actual - dt).total_seconds()))
        lons = self.base_lons.copy()
        if requested in self.incompatible_requests:
            lons += 0.25
        return tops, refl, lons, self.base_lats.copy()

    def run(self, *, provenance=True, display=False):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(tcf_pipeline, "_s3_client", return_value=object()), \
             patch.object(tcf_pipeline, "_resolve_scan_key", side_effect=self.resolve), \
             patch.object(tcf_pipeline, "_download_key", side_effect=self.download), \
             patch.object(tcf_pipeline, "_read_scan_arrays", side_effect=self.read):
            return tcf_pipeline.build_composite(
                VALID, window_minutes=2, cadence_minutes=2, step=1,
                max_workers=1, dest_dir=tmp, log=lambda _msg: None,
                with_display=display, with_provenance=provenance)


class MRMSProvenanceTests(unittest.TestCase):
    def test_composite_rejects_false_temporal_conjunction(self):
        synth = SyntheticComposite()

        def read(tops_file, _refl_file, _step):
            actual = tcf_pipeline._timestamp_from_mrms_key(tops_file)
            offset = int((actual - VALID).total_seconds() / 60)
            values = {-2: (22.0, 45.0), 0: (30.0, 30.0), 2: (20.0, 20.0)}
            top, refl = values[offset]
            shape = (2, 2)
            return (np.full(shape, top), np.full(shape, refl),
                    synth.base_lons.copy(), synth.base_lats.copy())

        synth.read = read
        max_tops, max_refl, qualifying, _lons, _lats = synth.run(
            provenance=False)
        np.testing.assert_array_equal(max_tops, np.full((2, 2), 30.0))
        np.testing.assert_array_equal(max_refl, np.full((2, 2), 45.0))
        self.assertTrue(((max_refl >= 40) & (max_tops >= 25)).all())
        self.assertFalse(qualifying.any())

    def test_one_usable_pair_qualifies_entire_window_union(self):
        synth = SyntheticComposite()

        def read(tops_file, _refl_file, _step):
            actual = tcf_pipeline._timestamp_from_mrms_key(tops_file)
            offset = int((actual - VALID).total_seconds() / 60)
            top, refl = (30.0, 45.0) if offset == 0 else (20.0, 20.0)
            shape = (2, 2)
            return (np.full(shape, top), np.full(shape, refl),
                    synth.base_lons.copy(), synth.base_lats.copy())

        synth.read = read
        qualifying = synth.run(provenance=False)[2]
        self.assertTrue(qualifying.all())

    def test_missing_product_slot_cannot_contribute_qualification(self):
        resolutions = {(tcf_pipeline.REFL_PRODUCT, VALID): None}
        out = SyntheticComposite(resolutions).run()
        qualifying, provenance = out[2], out[-1]
        self.assertFalse(qualifying.any())
        self.assertFalse(provenance.observations[1].used)

    def test_all_requested_observations_available(self):
        out = SyntheticComposite().run()
        provenance = out[-1]
        self.assertEqual(provenance.total_requested, 3)
        self.assertEqual(provenance.reflectivity_resolved, 3)
        self.assertEqual(provenance.echo_top_resolved, 3)
        self.assertEqual(provenance.both_resolved, 3)
        self.assertEqual(provenance.observations_used, 3)
        self.assertTrue(provenance.all_used_grids_compatible)
        self.assertTrue(all(record.used for record in provenance.observations))

    def test_missing_reflectivity_only_is_explicit(self):
        requested = VALID
        synth = SyntheticComposite({(tcf_pipeline.REFL_PRODUCT, requested): None})
        provenance = synth.run()[-1]
        record = provenance.observations[1]
        self.assertFalse(record.reflectivity_resolved)
        self.assertTrue(record.echo_top_resolved)
        self.assertFalse(record.used)
        self.assertEqual(record.exclusion_reason, "reflectivity_unavailable")
        self.assertEqual(provenance.missing_reflectivity, 1)
        self.assertEqual(provenance.observations_used, 2)

    def test_missing_echo_top_only_is_explicit(self):
        requested = VALID
        synth = SyntheticComposite({(tcf_pipeline.TOPS_PRODUCT, requested): None})
        out = synth.run()
        provenance = out[-1]
        record = provenance.observations[1]
        self.assertTrue(record.reflectivity_resolved)
        self.assertFalse(record.echo_top_resolved)
        self.assertFalse(record.used)
        self.assertEqual(record.exclusion_reason, "echo_top_unavailable")
        self.assertEqual(provenance.missing_echo_top, 1)
        self.assertFalse(out[2].any())

    def test_both_products_missing_remain_available_on_error(self):
        resolutions = {
            (product, requested): None
            for requested in (VALID - timedelta(minutes=2), VALID, VALID + timedelta(minutes=2))
            for product in (tcf_pipeline.REFL_PRODUCT, tcf_pipeline.TOPS_PRODUCT)
        }
        with self.assertRaises(tcf_pipeline.MRMSCompositeUnavailableError) as caught:
            SyntheticComposite(resolutions).run()
        provenance = caught.exception.provenance
        self.assertEqual(provenance.total_requested, 3)
        self.assertEqual(provenance.observations_used, 0)
        self.assertEqual(provenance.missing_reflectivity, 3)
        self.assertEqual(provenance.missing_echo_top, 3)
        self.assertTrue(all(r.exclusion_reason == "both_unavailable"
                            for r in provenance.observations))

    def test_product_timestamps_and_pair_separation_are_distinct(self):
        resolutions = {
            (tcf_pipeline.REFL_PRODUCT, VALID): -22,
            (tcf_pipeline.TOPS_PRODUCT, VALID): 41,
        }
        record = SyntheticComposite(resolutions).run()[-1].observations[1]
        self.assertEqual(record.requested_time, VALID)
        self.assertEqual(record.reflectivity_time, VALID - timedelta(seconds=22))
        self.assertEqual(record.echo_top_time, VALID + timedelta(seconds=41))
        self.assertEqual(record.reflectivity_offset_seconds, -22)
        self.assertEqual(record.echo_top_offset_seconds, 41)
        self.assertEqual(record.product_separation_seconds, 63)
        self.assertTrue(record.used)

    def test_summary_preserves_maximum_nearest_file_offsets(self):
        resolutions = {
            (tcf_pipeline.REFL_PRODUCT, VALID - timedelta(minutes=2)): -18,
            (tcf_pipeline.REFL_PRODUCT, VALID): 78,
            (tcf_pipeline.TOPS_PRODUCT, VALID + timedelta(minutes=2)): -45,
        }
        summary = SyntheticComposite(resolutions).run()[-1]
        self.assertEqual(summary.max_reflectivity_offset_seconds, 78)
        self.assertEqual(summary.max_echo_top_offset_seconds, 45)
        self.assertEqual(summary.max_product_separation_seconds, 78)

    def test_download_failure_is_not_silently_dropped(self):
        missing_key = _key(tcf_pipeline.REFL_PRODUCT, VALID)
        provenance = SyntheticComposite(download_failures={missing_key}).run()[-1]
        record = provenance.observations[1]
        self.assertTrue(record.reflectivity_resolved)
        self.assertFalse(record.reflectivity_downloaded)
        self.assertFalse(record.used)
        self.assertEqual(record.exclusion_reason, "reflectivity_download_failed")

    def test_read_failure_is_not_silently_dropped(self):
        bad_key = _key(tcf_pipeline.TOPS_PRODUCT, VALID)
        provenance = SyntheticComposite(read_failures={bad_key}).run()[-1]
        record = provenance.observations[1]
        self.assertTrue(record.both_products_available)
        self.assertFalse(record.used)
        self.assertEqual(record.exclusion_reason, "read_failure:OSError")

    def test_incompatible_grid_is_recorded_and_excluded(self):
        provenance = SyntheticComposite(
            incompatible_requests={VALID}).run()[-1]
        record = provenance.observations[1]
        self.assertFalse(record.grid_compatible)
        self.assertFalse(record.used)
        self.assertEqual(record.exclusion_reason, "incompatible_grid")
        self.assertEqual(provenance.observations_used, 2)
        self.assertTrue(provenance.all_used_grids_compatible)

    def test_summary_counts_are_derived_from_detail(self):
        resolutions = {
            (tcf_pipeline.REFL_PRODUCT, VALID): None,
            (tcf_pipeline.TOPS_PRODUCT, VALID + timedelta(minutes=2)): None,
        }
        summary = SyntheticComposite(resolutions).run()[-1]
        records = summary.observations
        self.assertEqual(summary.reflectivity_resolved,
                         sum(r.reflectivity_resolved for r in records))
        self.assertEqual(summary.echo_top_resolved,
                         sum(r.echo_top_resolved for r in records))
        self.assertEqual(summary.both_resolved,
                         sum(r.reflectivity_resolved and r.echo_top_resolved for r in records))
        self.assertEqual(summary.observations_used, sum(r.used for r in records))

    def test_provenance_flag_does_not_change_fully_available_composite(self):
        without = SyntheticComposite().run(provenance=False)
        with_provenance = SyntheticComposite().run(provenance=True)
        self.assertEqual(len(without), 5)
        self.assertEqual(len(with_provenance), 6)
        for expected, actual in zip(without, with_provenance[:5]):
            np.testing.assert_array_equal(actual, expected)

        display_without = SyntheticComposite().run(provenance=False, display=True)
        display_with = SyntheticComposite().run(provenance=True, display=True)
        self.assertEqual(len(display_without), 6)
        self.assertEqual(len(display_with), 7)
        for expected, actual in zip(display_without[:5], display_with[:5]):
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(
            display_with[5].max_tops, display_without[5].max_tops)
        np.testing.assert_array_equal(
            display_with[5].max_refl, display_without[5].max_refl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
