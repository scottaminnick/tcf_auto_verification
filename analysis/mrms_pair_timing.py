#!/usr/bin/env python3
"""Generate analysis-only Decision 1B timing cases and compact summaries.

This utility deliberately implements no acceptance rule.  It describes timing
facts that any later fixed-threshold, cycle-based, or reviewer policy would
need, without importing production code or accessing the MRMS archive.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOMINAL = datetime.fromisoformat("2026-05-24T23:00:00")

CASES = [
    ("exact_match", "2026-05-24T23:00:00", "2026-05-24T23:00:00", "both products present"),
    ("one_second", "2026-05-24T23:00:00", "2026-05-24T23:00:01", "seconds-level difference observed in supplied six-event evidence"),
    ("thirty_seconds", "2026-05-24T23:00:00", "2026-05-24T23:00:30", "timestamp meaning needed"),
    ("sixty_seconds", "2026-05-24T23:00:00", "2026-05-24T23:01:00", "timestamp meaning needed"),
    ("one_hundred_nineteen_seconds", "2026-05-24T23:00:00", "2026-05-24T23:01:59", "cycle identity cannot be inferred safely"),
    ("one_hundred_twenty_one_seconds", "2026-05-24T23:00:00", "2026-05-24T23:02:01", "exceeds nominal product cadence but no policy follows"),
    ("mutually_close_far_from_nominal", "2026-05-24T23:04:00", "2026-05-24T23:04:01", "pair compatibility and nominal adequacy differ"),
    ("individually_close_cross_pair", "2026-05-24T22:58:30", "2026-05-24T23:01:30", "both pass current resolver but are three minutes apart"),
    ("preceding_following_candidates", "2026-05-24T22:59:00", "2026-05-24T23:01:00", "independent resolution can select neighboring source times"),
    ("echo_top_missing", "2026-05-24T23:00:00", None, "Decision 1A excludes; Decision 1B is not evaluated"),
]


def seconds(value: datetime | None, reference: datetime) -> float | None:
    return (value - reference).total_seconds() if value else None


def rows() -> list[dict]:
    output = []
    for name, refl_text, top_text, interpretation in CASES:
        refl = datetime.fromisoformat(refl_text) if refl_text else None
        top = datetime.fromisoformat(top_text) if top_text else None
        refl_offset = seconds(refl, NOMINAL)
        top_offset = seconds(top, NOMINAL)
        separation = abs((refl - top).total_seconds()) if refl and top else None
        output.append({
            "case": name,
            "nominal_time": NOMINAL.isoformat(),
            "reflectivity_filename_time": refl.isoformat() if refl else "",
            "echo_top_filename_time": top.isoformat() if top else "",
            "reflectivity_nominal_offset_sec": refl_offset if refl_offset is not None else "",
            "echo_top_nominal_offset_sec": top_offset if top_offset is not None else "",
            "pair_separation_sec": separation if separation is not None else "",
            "both_within_current_300_sec_resolver_limit": (
                abs(refl_offset) <= 300 and abs(top_offset) <= 300
                if refl_offset is not None and top_offset is not None else False),
            "same_analysis_cycle": "unknown",
            "decision_1b_accepted": "not_assigned",
            "interpretation": interpretation,
        })
    return output


def main() -> None:
    records = rows()
    csv_path = ROOT / "analysis/mrms_pair_timing_synthetic.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    summary = {
        "scope": "analysis_only_no_pair_acceptance_rule",
        "synthetic_case_count": len(records),
        "historical_evidence": {
            "events": 6,
            "nominal_pairs": 90,
            "separation_range_seconds": [0, 1],
            "zero_second_pairs": 88,
            "one_second_pairs": 2,
            "detail_source": "supplied externally for this task; per-slot manifest not committed",
        },
        "supplied_noaa_context": {
            "both_product_frequencies_minutes": 2,
            "both_described_as_derived_from_3d_reflectivity_cube": True,
            "filename_timestamp_resolution": "seconds",
            "filename_timestamp_semantics": "unresolved",
            "same_cycle_guarantee": "not supplied",
        },
        "recommendation": "OPTION_C_prefer_authoritative_same_cycle_identity_with_reviewer_fallback_but_evidence_required",
        "threshold_selected": None,
        "archive_collection_performed": False,
    }
    (ROOT / "analysis/mrms_pair_timing_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
