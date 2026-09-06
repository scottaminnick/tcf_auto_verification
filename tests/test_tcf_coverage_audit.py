"""Reproducibility checks for the frozen TCF feature/code audit."""

import csv
from collections import Counter
from pathlib import Path
import unittest

from baseline.audit_tcf_coverage import audit_rows


class FrozenCoverageAuditTests(unittest.TestCase):
    def test_all_six_events_and_48_features_are_audited(self):
        rows = audit_rows()
        self.assertEqual(len({row["event_id"] for row in rows}), 6)
        self.assertEqual(len(rows), 48)
        self.assertTrue(all(row["parser_status"] == "accepted" for row in rows))

    def test_only_approved_feature_code_combinations_exist(self):
        counts = Counter((row["feat_type"], row["coverage_code"])
                         for row in audit_rows())
        self.assertEqual(counts, Counter({
            ("AREA", 3): 43,
            ("AREA", 2): 4,
            ("LINE", 1): 1,
        }))

    def test_committed_detail_matches_reproducible_audit(self):
        path = Path(__file__).resolve().parent.parent / "baseline" / \
            "tcf_coverage_feature_audit.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            committed = list(csv.DictReader(stream))
        generated = audit_rows()
        normalized = [{key: "" if value is None else str(value)
                       for key, value in row.items()} for row in generated]
        self.assertEqual(committed, normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
