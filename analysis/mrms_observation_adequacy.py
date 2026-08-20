#!/usr/bin/env python3
"""Reproduce analysis-only MRMS adequacy artifacts without fetching MRMS data.

Frozen arrays contain temporal maxima, not scan-level provenance.  Consequently
this utility records only the reconstructable nominal schedule and marks every
actual-source field unavailable.  It also evaluates transparent synthetic
missingness patterns; it never assigns an operational quality state.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFSETS = list(range(-14, 15, 2))
EVENTS = {
    "20260324_13Z_F04": "2026-03-24T17:00:00",
    "20260403_21Z_F04": "2026-04-04T01:00:00",
    "20260524_13Z_F04": "2026-05-24T17:00:00",
    "20260524_19Z_F04": "2026-05-24T23:00:00",
    "20260524_19Z_F06": "2026-05-25T01:00:00",
    "20260728_19Z_F04": "2026-07-28T23:00:00",
}


def longest_run(missing: set[int]) -> int:
    best = run = 0
    for offset in OFFSETS:
        run = run + 1 if offset in missing else 0
        best = max(best, run)
    return best


def metrics(usable: list[int], pair_ids: list[str] | None = None) -> dict:
    usable = sorted(usable)
    gaps = [b - a for a, b in zip(usable, usable[1:])]
    pair_ids = pair_ids or [str(x) for x in usable]
    return {
        "usable_pair_count": len(usable),
        "usable_fraction": len(usable) / len(OFFSETS),
        "unique_pair_count": len(set(pair_ids)),
        "duplicate_nominal_slot_count": len(usable) - len(set(pair_ids)),
        "earliest_usable_offset_min": min(usable) if usable else None,
        "latest_usable_offset_min": max(usable) if usable else None,
        "temporal_span_min": max(usable) - min(usable) if usable else None,
        "nearest_pair_to_valid_time_min": min(map(abs, usable)) if usable else None,
        "largest_gap_between_usable_pairs_min": max(gaps) if gaps else None,
        "pre_valid_pair_count": sum(x < 0 for x in usable),
        "at_valid_pair_count": sum(x == 0 for x in usable),
        "post_valid_pair_count": sum(x > 0 for x in usable),
        "missing_slot_count": len(OFFSETS) - len(usable),
        "longest_missing_run_slots": longest_run(set(OFFSETS) - set(usable)),
    }


def synthetic_cases() -> dict[str, dict]:
    cases = {
        "all_15_usable": OFFSETS,
        "one_isolated_missing": [x for x in OFFSETS if x != 10],
        "valid_time_missing": [x for x in OFFSETS if x != 0],
        "central_five_missing": [x for x in OFFSETS if x not in {-4, -2, 0, 2, 4}],
        "pre_valid_only": [x for x in OFFSETS if x < 0],
        "post_valid_only": [x for x in OFFSETS if x > 0],
        "one_at_valid_time": [0],
        "one_at_window_edge": [-14],
        "alternating": OFFSETS[::2],
        "sparse_echo_top": [-14, -8, -2, 4, 10],
        "two_grid_exclusions": [x for x in OFFSETS if x not in {0, 2}],
    }
    out = {name: metrics(values) for name, values in cases.items()}
    # All nominal slots resolve, but adjacent slots reuse eight actual pairs.
    out["all_slots_eight_unique_pairs"] = metrics(
        OFFSETS, [str((i + 1) // 2) for i in range(len(OFFSETS))])
    return out


def temporal_union_experiment() -> dict:
    # Sets stand in for qualifying raster cells at each time. They make loss
    # from omitted observations exact and dependency-free.
    scans = {}
    for i, offset in enumerate(OFFSETS):
        cells = {f"persistent-{j}" for j in range(4)}
        cells.add(f"moving-{i}")
        if i <= 4:
            cells.add(f"decaying-{i}")
        if i >= 10:
            cells.add(f"developing-{i}")
        if offset == 0:
            cells.add("short-lived")
        scans[offset] = cells
    reference = set().union(*scans.values())
    patterns = {
        "one_slot_removed": [x for x in OFFSETS if x != 10],
        "valid_time_removed": [x for x in OFFSETS if x != 0],
        "central_three_gap": [x for x in OFFSETS if x not in {-2, 0, 2}],
        "central_five_gap": [x for x in OFFSETS if x not in {-4, -2, 0, 2, 4}],
        "every_other": OFFSETS[::2],
        "all_pre_valid_removed": [x for x in OFFSETS if x >= 0],
        "all_post_valid_removed": [x for x in OFFSETS if x <= 0],
        "only_nearest_to_valid": [0],
    }
    result = {}
    for name, retained in patterns.items():
        union = set().union(*(scans[x] for x in retained))
        result[name] = {
            "retained_slots": retained,
            "reference_qualifying_cells": len(reference),
            "degraded_qualifying_cells": len(union),
            "qualifying_cells_lost": len(reference - union),
            "intersection_over_union": len(reference & union) / len(reference | union),
            "fraction_reference_lost": len(reference - union) / len(reference),
        }
    return result


def main() -> None:
    csv_path = ROOT / "analysis/mrms_observation_provenance.csv"
    fields = ["event_id", "valid_time", "nominal_time", "nominal_offset_min",
              "reflectivity_actual_time", "echo_top_actual_time",
              "reflectivity_nominal_offset_sec", "echo_top_nominal_offset_sec",
              "pair_separation_sec", "usable_pair", "unique_pair_id",
              "exclusion_reason", "grid_compatible", "evidence_status"]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for event, valid_text in EVENTS.items():
            valid = datetime.fromisoformat(valid_text)
            for offset in OFFSETS:
                row = {field: "" for field in fields}
                row.update(event_id=event, valid_time=valid.isoformat(),
                           nominal_time=(valid + timedelta(minutes=offset)).isoformat(),
                           nominal_offset_min=offset,
                           evidence_status="nominal_schedule_only_actual_provenance_not_committed")
                writer.writerow(row)

    summary = {
        "scope": "analysis_only_no_production_thresholds_or_quality_labels",
        "historical_evidence": {
            "events": len(EVENTS), "nominal_slots": 90,
            "paired_observations_reported_by_external_colab_evidence": 90,
            "reported_pair_separation_range_seconds": [0, 1],
            "per_slot_actual_timestamps_available_in_repository": False,
            "per_event_usable_unique_gap_and_missingness_metrics": None,
            "degradation_experiment_available": False,
            "limitation": "Frozen arrays are independent temporal maxima and cannot support scan leave-out experiments."
        },
        "nominal_schedule": {"offsets_minutes": OFFSETS, "slot_count": len(OFFSETS),
                             "span_minutes": 28, "nearest_file_limit_seconds": 300},
        "synthetic_missingness": synthetic_cases(),
        "synthetic_temporal_union": temporal_union_experiment(),
        "recommendation": "OPTION_C_three_state_reviewer_framework_preferred_exact_rules_unresolved",
        "decision_1b": "separately_unresolved_no_pair_separation_threshold_selected",
    }
    (ROOT / "analysis/mrms_observation_adequacy_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
