#!/usr/bin/env python3
"""Re-grade every event against the frozen composite and diff v2 -> v3.

Scratch tool, NOT part of `make test`.

The LINE geometry fix changes only how the TCF product is parsed. The MRMS
composite depends on the valid time and cadence, neither of which moved, so
arrays.npz is bit-identical before and after -- this reuses the frozen arrays
rather than re-downloading 30 files per event, which makes the forecast geometry
the only thing that differs between the two runs.

(capture.py would re-fetch the TCF text from IEM, which is unreachable from this
container. tcf_raw.txt is frozen per event and is the input to the parser under
test, so reading it from disk is what we want regardless.)

With --write it rewrites baseline/<event>/expected.json in place; without, it
only reports.

Usage:
    python scratch/line_fix_report.py [--write] [--old-dir baseline_v2_line_closed]
"""

import argparse
import json
import os
import re
import sys

import numpy as np
from shapely.geometry import LineString, Polygon

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(1, os.path.join(REPO_ROOT, "baseline"))

import tcf_pipeline  # noqa: E402
from baseline import capture  # noqa: E402

BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")


def parse_old(text_data):
    """parse_iem_cow_text exactly as it was before the fix.

    Kept here, in a scratch file, purely so the before/after geometry can be
    compared in one process. It is not importable from tcf_pipeline any more and
    must never be used for anything but this report.
    """
    records = []
    text_data = re.sub(r'<[^>]+>', ' ', text_data)
    for feat_type, block in re.findall(r'(AREA|LINE)\s+([\d\s]+)', text_data):
        parts = block.split()
        try:
            cov_val = int(parts[0])
            if feat_type == 'LINE':
                num_points, idx = int(parts[1]), 2
            else:
                num_points, idx = int(parts[6]), 7
            coords = []
            for _ in range(num_points):
                if idx + 1 < len(parts):
                    lat = float(parts[idx]) / 10.0
                    lon = float(parts[idx + 1]) / 10.0
                    if lon > 0:
                        lon = -lon
                    coords.append((lon, lat))
                    idx += 2
            # THE BUG: branch on point count only, so a 3+ point LINE closes.
            if len(coords) >= 3:
                poly = Polygon(coords).buffer(0)
                if not poly.is_empty:
                    records.append({'geometry': poly, 'coverage': cov_val,
                                    'feat_type': feat_type})
            elif len(coords) >= 2:
                poly = LineString(coords).buffer(0.15)
                records.append({'geometry': poly, 'coverage': cov_val,
                                'feat_type': feat_type})
        except Exception:
            continue
    import geopandas as gpd
    return (gpd.GeoDataFrame(records, crs="EPSG:4326") if records
            else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"))


def grade(gdf_forecast, event, arrays, gdf_artcc):
    valid_dt = tcf_pipeline.compute_valid_dt(
        event["date"], event["issuance_hour"], event["lead_time"])
    results = tcf_pipeline.run_verification_legacy_independent_max(
        gdf_forecast, arrays["max_tops"], arrays["max_refl"],
        arrays["lons"], arrays["lats"],
        valid_dt, event["issuance_hour"], event["lead_time"], gdf_artcc)
    return valid_dt, results, capture.build_expected(event, valid_dt, results, gdf_artcc)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--old-dir", default="baseline_v2_line_closed")
    args = ap.parse_args()

    gdf_artcc = tcf_pipeline.load_artccs()
    counts, line_rows = [], []

    for event in capture.EVENTS:
        ev = event["event_id"]
        ev_dir = os.path.join(BASELINE_DIR, ev)
        with open(os.path.join(ev_dir, "tcf_raw.txt"), encoding="utf-8") as f:
            raw = f.read()
        with np.load(os.path.join(ev_dir, "arrays.npz")) as npz:
            arrays = {k: npz[k] for k in ("max_tops", "max_refl", "lons", "lats")}

        gdf_old = parse_old(raw)
        gdf_new = tcf_pipeline.parse_iem_cow_text(raw)

        _, res_old, exp_old = grade(gdf_old, event, arrays, gdf_artcc)
        _, res_new, exp_new = grade(gdf_new, event, arrays, gdf_artcc)

        counts.append((ev, exp_old["counts"], exp_new["counts"]))

        # Per LINE feature: area and coverage fraction, before and after. Matched
        # by position in the parsed frame, which is source order -- idx in the
        # report is assigned after an east-to-west sort and is not stable.
        for i, feat in enumerate(gdf_new["feat_type"]):
            if feat != "LINE":
                continue
            geom_old = gdf_old.geometry.iloc[i]
            geom_new = gdf_new.geometry.iloc[i]
            cf_old = next((r["coverage_fraction"] for r in res_old["graded_forecasts"]
                           if r["geometry"].equals(geom_old)), None)
            cf_new = next((r["coverage_fraction"] for r in res_new["graded_forecasts"]
                           if r["geometry"].equals(geom_new)), None)
            cat_old = next((r["category"] for r in res_old["graded_forecasts"]
                            if r["geometry"].equals(geom_old)), None)
            cat_new = next((r["category"] for r in res_new["graded_forecasts"]
                            if r["geometry"].equals(geom_new)), None)
            line_rows.append((ev, i, geom_old, geom_new, cf_old, cf_new, cat_old, cat_new))

        if args.write:
            with open(os.path.join(ev_dir, "expected.json"), "w", encoding="utf-8") as f:
                json.dump(exp_new, f, indent=2, sort_keys=False)
                f.write("\n")

    k = ("verified_well", "verified_close", "overforecasted", "misses")
    print(f"{'event':<20} | {'Well':>4}{'Close':>6}{'Over':>5}{'Missed':>7}"
          f" | {'Well':>4}{'Close':>6}{'Over':>5}{'Missed':>7} | delta")
    print(f"{'':<20} | {'v2 (LINE closed)':^22} | {'v3 (LINE buffered)':^22} |")
    print("-" * 88)
    tot = [[0] * 4, [0] * 4]
    for ev, a, b in counts:
        va, vb = [a[x] for x in k], [b[x] for x in k]
        for i in range(4):
            tot[0][i] += va[i]
            tot[1][i] += vb[i]
        d = " ".join(f"{vb[i] - va[i]:+d}" for i in range(4))
        f = lambda v: f"{v[0]:>4}{v[1]:>6}{v[2]:>5}{v[3]:>7}"  # noqa: E731
        print(f"{ev:<20} | {f(va)} | {f(vb)} | {d}")
    print("-" * 88)
    f = lambda v: f"{v[0]:>4}{v[1]:>6}{v[2]:>5}{v[3]:>7}"  # noqa: E731
    print(f"{'TOTAL':<20} | {f(tot[0])} | {f(tot[1])} | "
          + " ".join(f"{tot[1][i] - tot[0][i]:+d}" for i in range(4)))

    print(f"\n{'=' * 88}\nLINE features, before and after\n{'=' * 88}")
    if not line_rows:
        print("  none in the event set")
    for ev, i, go_, gn, cfo, cfn, cato, catn in line_rows:
        print(f"\n  {ev}  (feature #{i} in source order)")
        print(f"    geometry           closed polygon -> buffered line "
              f"({tcf_pipeline.LINE_BUFFER_DEG} deg half-width)")
        print(f"    area (deg^2)       {go_.area:.5f} -> {gn.area:.5f} "
              f"({(gn.area / go_.area - 1) * 100:+.1f}%)")
        print(f"    coverage_fraction  {cfo:.4f} -> {cfn:.4f} "
              f"({(cfn - cfo):+.4f})")
        print(f"    category           {cato} -> {catn}")


if __name__ == "__main__":
    main()
