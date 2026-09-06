#!/usr/bin/env python3
"""Capture pre-baseline evidence from real paired MRMS inputs.

This utility deliberately writes outside ``baseline/``.  Its output is evidence
for meteorologist review, not an approved regression baseline.  MRMS GRIB files
are removed by ``build_composite``; only compact arrays, source provenance, and
verification summaries remain.

Usage:
    python analysis/validate_methodology_1_0_paired.py --output DIR [EVENT_ID ...]
"""

import argparse
import json
import os
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import tcf_pipeline as pipeline  # noqa: E402
from baseline.capture import EVENTS, build_expected, provenance_payload  # noqa: E402


def validate_event(event, output_root, artccs):
    valid_dt = pipeline.compute_valid_dt(
        event["date"], event["issuance_hour"], event["lead_time"])
    raw = pipeline.fetch_iem_cow_raw(
        event["date"], event["issuance_hour"], event["lead_time"])
    forecasts = pipeline.parse_iem_cow_text(raw)
    if forecasts.empty:
        raise RuntimeError("forecast archive returned no supported features")

    (max_tops, max_refl, qualifying_mask, lons, lats,
     provenance) = pipeline.build_composite(valid_dt, with_provenance=True)
    if provenance.observations_used == 0 or not provenance.all_used_grids_compatible:
        raise RuntimeError("paired MRMS evidence has no compatible usable observations")
    results = pipeline.run_verification(
        forecasts, max_tops, max_refl, lons, lats, valid_dt,
        event["issuance_hour"], event["lead_time"], artccs,
        qualifying_mask=qualifying_mask)

    out = os.path.join(output_root, event["event_id"])
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "tcf_raw.txt"), "w", encoding="utf-8") as stream:
        stream.write(raw)
    np.savez_compressed(
        os.path.join(out, "arrays.npz"), max_tops=max_tops, max_refl=max_refl,
        qualifying_mask=qualifying_mask, lons=lons, lats=lats)
    with open(os.path.join(out, "mrms_provenance.json"), "w", encoding="utf-8") as stream:
        json.dump(provenance_payload(provenance), stream, indent=2)
        stream.write("\n")

    summary = build_expected(
        event, valid_dt, results, artccs,
        methodology_version=pipeline.METHODOLOGY_VERSION)
    summary["artifact_state"] = "paired-validation-evidence-not-baseline"
    summary["nominal_pair_count"] = provenance.total_requested
    summary["usable_pair_count"] = provenance.observations_used
    summary["candidate_misses_default_approved"] = False
    with open(os.path.join(out, "validation.json"), "w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("event_ids", nargs="*")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = os.path.abspath(args.output)
    baseline = os.path.join(REPO_ROOT, "baseline")
    if os.path.commonpath((output, baseline)) == baseline:
        parser.error("validation evidence must be written outside baseline/")
    selected = [event for event in EVENTS
                if not args.event_ids or event["event_id"] in args.event_ids]
    unknown = set(args.event_ids) - {event["event_id"] for event in EVENTS}
    if unknown:
        parser.error(f"unknown event ids: {', '.join(sorted(unknown))}")
    artccs = pipeline.load_artccs()
    for event in selected:
        summary = validate_event(event, output, artccs)
        print(f"{event['event_id']}: {summary['counts']}")


if __name__ == "__main__":
    main()
