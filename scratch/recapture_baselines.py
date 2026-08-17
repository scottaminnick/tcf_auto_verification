#!/usr/bin/env python3
"""Re-capture baseline/ at the current pipeline cadence, reusing the frozen TCF text.

Scratch tool, NOT part of `make test`.

baseline/capture.py fetches the TCF product from IEM and the scans from S3. This
does the same thing except it reads the TCF product from each event's existing
tcf_raw.txt instead of re-fetching it. Two reasons:

  * it isolates the change under test -- the forecast polygons are byte-for-byte
    the ones the v1 baselines were graded against, so every difference in the
    output comes from the composite and nothing else;
  * IEM is not reachable from this environment, while S3 is.

arrays.npz and expected.json are rewritten. tcf_raw.txt is left alone (it is the
input), and so is pass_a_report.txt -- that one comes out of the live app and
this script must not forge it.

Usage:
    python scratch/recapture_baselines.py [event_id ...] [--dry-run]
"""

import argparse
import json
import os
import sys
import time

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(1, os.path.join(REPO_ROOT, "baseline"))

import tcf_pipeline  # noqa: E402
from baseline import capture  # noqa: E402

BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")


def recapture(event, gdf_artcc, dry_run=False):
    event_dir = os.path.join(BASELINE_DIR, event["event_id"])
    with open(os.path.join(event_dir, "tcf_raw.txt"), encoding="utf-8") as f:
        raw_text = f.read()

    gdf_forecast = tcf_pipeline.parse_iem_cow_text(raw_text)
    if gdf_forecast.empty:
        raise RuntimeError("frozen tcf_raw.txt parsed to nothing")

    valid_dt = tcf_pipeline.compute_valid_dt(
        event["date"], event["issuance_hour"], event["lead_time"])

    t0 = time.perf_counter()
    max_tops, max_refl, lons, lats = tcf_pipeline.build_composite(
        valid_dt, log=lambda m: None)
    fetch_s = time.perf_counter() - t0

    results = tcf_pipeline.run_verification(
        gdf_forecast, max_tops, max_refl, lons, lats,
        valid_dt, event["issuance_hour"], event["lead_time"], gdf_artcc)
    expected = capture.build_expected(event, valid_dt, results, gdf_artcc)

    if not dry_run:
        np.savez_compressed(os.path.join(event_dir, "arrays.npz"),
                            max_tops=max_tops, max_refl=max_refl, lons=lons, lats=lats)
        with open(os.path.join(event_dir, "expected.json"), "w", encoding="utf-8") as f:
            json.dump(expected, f, indent=2, sort_keys=False)
            f.write("\n")

    c = expected["counts"]
    print(f"  {event['event_id']}  {fetch_s:6.1f}s  "
          f"well={c['verified_well']} close={c['verified_close']} "
          f"over={c['overforecasted']} missed={c['misses']}", flush=True)
    return expected


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("event_ids", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    events = [e for e in capture.EVENTS
              if not args.event_ids or e["event_id"] in args.event_ids]
    offsets = tcf_pipeline.scan_offsets()
    print(f"cadence {tcf_pipeline.COMPOSITE_CADENCE_MINUTES} min over "
          f"+/-{tcf_pipeline.COMPOSITE_WINDOW_MINUTES} min -> {len(offsets)} scans, "
          f"{len(offsets) * 2} files per event", flush=True)

    gdf_artcc = tcf_pipeline.load_artccs()
    failures = []
    for event in events:
        try:
            recapture(event, gdf_artcc, args.dry_run)
        except Exception as exc:
            failures.append(event["event_id"])
            print(f"  FAILED {event['event_id']}: {type(exc).__name__}: {exc}", flush=True)
    if failures:
        raise SystemExit(f"{len(failures)} event(s) failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()
