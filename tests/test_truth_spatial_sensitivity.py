"""Safety tests for the paired-artifact spatial sensitivity audit."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import geopandas as gpd

from analysis.truth_spatial_transform_sensitivity import audit_event, load_paired_event


class PairedArtifactIdentityTests(unittest.TestCase):
    def _artifact(self, root, *, paired):
        event = Path(root) / "event"
        event.mkdir()
        arrays = {
            "max_tops": np.zeros((2, 2)), "max_refl": np.zeros((2, 2)),
            "lons": np.array([-100.0, -99.0]),
            "lats": np.array([40.0, 41.0]),
        }
        if paired:
            arrays["qualifying_mask"] = np.ones((2, 2), dtype=bool)
        np.savez(event / "arrays.npz", **arrays)
        (event / "tcf_raw.txt").write_text("", encoding="utf-8")
        (event / "validation.json").write_text(json.dumps({
            "artifact_state": "paired-validation-evidence-not-baseline",
            "event_id": "event", "valid_dt": "2026-01-01T00:00:00",
            "issuance_hour": 20, "lead_time": 4,
        }), encoding="utf-8")
        (event / "mrms_provenance.json").write_text(
            json.dumps({"observations_used": 1}), encoding="utf-8")
        return event

    def test_accepts_stored_pair_mask(self):
        with tempfile.TemporaryDirectory() as root:
            arrays, metadata = load_paired_event(self._artifact(root, paired=True))
            self.assertIn("qualifying_mask", arrays)
            self.assertEqual(metadata["artifact_state"],
                             "paired-validation-evidence-not-baseline")

    def test_rejects_legacy_maxima_only_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "legacy maxima-only artifact rejected"):
                load_paired_event(self._artifact(root, paired=False))

    def test_audit_evaluates_complete_diagnostic_grid(self):
        with tempfile.TemporaryDirectory() as root:
            event = self._artifact(root, paired=True)
            rows = audit_event(
                event, gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"))
            self.assertEqual(len(rows["event"]), 8)
            self.assertEqual(
                {(row["dilation_iterations"], row["smoothing_size"])
                 for row in rows["event"]},
                {(dilation, smoothing) for dilation in (0, 1)
                 for smoothing in (5, 10, 15, 20)})
            self.assertTrue(all(
                "low_capture_sparse_component_count" in row
                for row in rows["event"]))
            self.assertTrue(rows["candidate"])
            self.assertTrue(all(
                "eligible_candidate_miss" in row
                for row in rows["candidate"]))


if __name__ == "__main__":
    unittest.main()
