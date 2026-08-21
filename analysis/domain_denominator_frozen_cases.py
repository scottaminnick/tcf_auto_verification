#!/usr/bin/env python3
"""Read-only forecast-denominator audit for the six frozen TCF cases.

The frozen MRMS arrays contain independent temporal maxima, not the paired mask
required by Decision 1A.  Consequently the scores in this audit are explicitly
legacy-replay scores.  That limitation does not affect the geometric comparison
between the full-issued and in-domain denominators for a fixed truth field.
"""

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline  # noqa: E402


FIELDS = (
    "event_id", "source_feature_number", "report_feature_number", "feat_type",
    "coverage_code", "geometry_type", "full_area_km2", "in_domain_area_km2",
    "in_domain_fraction", "out_of_domain_fraction", "current_fraction",
    "in_domain_denominator_fraction", "fraction_difference", "current_category",
    "candidate_category", "min_lon", "min_lat", "max_lon", "max_lat",
    "artifact_method",
)


def _physical(geometry):
    return gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
        tcf_pipeline.PHYSICAL_AREA_CRS).iloc[0]


def _category(fraction, params):
    if fraction is None:
        return "Unscorable"
    if fraction >= params.verified_well_cutoff:
        return "Verified Well"
    if fraction >= params.verified_close_cutoff:
        return "Verified Close"
    return "Overforecasted"


def audit_rows(baseline_dir=REPO_ROOT / "baseline"):
    params = tcf_pipeline.GradingParams()
    domain_m = _physical(tcf_pipeline.verification_domain())
    artcc = tcf_pipeline.load_artccs()
    rows = []

    for event_dir in sorted(baseline_dir.glob("20*")):
        raw_path = event_dir / "tcf_raw.txt"
        expected_path = event_dir / "expected.json"
        arrays_path = event_dir / "arrays.npz"
        if not (raw_path.exists() and expected_path.exists() and arrays_path.exists()):
            continue
        metadata = json.loads(expected_path.read_text(encoding="utf-8"))
        forecasts = tcf_pipeline.parse_iem_cow_text(
            raw_path.read_text(encoding="utf-8"))
        with np.load(arrays_path) as arrays:
            tops, refl, lons, lats = [arrays[key] for key in
                                      ("max_tops", "max_refl", "lons", "lats")]
        result = tcf_pipeline.run_verification_legacy_independent_max(
            forecasts, tops, refl, lons, lats,
            datetime.fromisoformat(metadata["valid_dt"]), metadata["issuance_hour"],
            metadata["lead_time"], artcc, params=params)

        # Production explodes components before scoring. Frozen source features are
        # all single polygons, so geometry equality gives an unambiguous report id.
        for source_number, feature in enumerate(forecasts.itertuples(), start=1):
            geometry = feature.geometry
            geometry_m = _physical(geometry)
            eligible_m = geometry_m.intersection(domain_m)
            full_area = geometry_m.area
            eligible_area = eligible_m.area
            in_fraction = (min(1.0, max(0.0, eligible_area / full_area))
                           if full_area else None)
            match = next(item for item in result["graded_forecasts"]
                         if item["feat_type"] == feature.feat_type
                         and item["coverage"] == feature.coverage
                         and item["geometry"].equals(geometry))
            current = float(match["coverage_fraction"])
            # Truth is already a subset of D. The numerator therefore does not
            # change when F is replaced by F intersection D.
            candidate = ((current / in_fraction)
                         if in_fraction is not None and in_fraction > 0 else None)
            if candidate is not None:
                candidate = min(1.0, max(0.0, candidate))
            min_lon, min_lat, max_lon, max_lat = geometry.bounds
            rows.append({
                "event_id": event_dir.name,
                "source_feature_number": source_number,
                "report_feature_number": int(match["idx"]),
                "feat_type": feature.feat_type,
                "coverage_code": int(feature.coverage),
                "geometry_type": geometry.geom_type,
                "full_area_km2": full_area / 1e6,
                "in_domain_area_km2": eligible_area / 1e6,
                "in_domain_fraction": in_fraction,
                "out_of_domain_fraction": (1 - in_fraction
                                           if in_fraction is not None else None),
                "current_fraction": current,
                "in_domain_denominator_fraction": candidate,
                "fraction_difference": (candidate - current
                                        if candidate is not None else None),
                "current_category": match["category"],
                "candidate_category": _category(candidate, params),
                "min_lon": min_lon, "min_lat": min_lat,
                "max_lon": max_lon, "max_lat": max_lat,
                "artifact_method": "legacy independent-max frozen truth",
            })
    return rows


def synthetic_cases():
    """Exact Cartesian illustrations; coordinates are arbitrary equal-area units."""
    domain = box(0, 0, 10, 10)
    examples = {
        "inside": box(1, 1, 9, 9),
        "outside": box(11, 1, 19, 9),
        "half_inside": box(5, 1, 15, 9),
        "ninety_percent_inside": box(1, 1, 11, 9),
        "ten_percent_inside": box(9, 1, 19, 9),
    }
    output = []
    for name, forecast in examples.items():
        truth = forecast.intersection(domain)  # perfect truth wherever eligible
        eligible = forecast.intersection(domain)
        score_a = forecast.intersection(truth).area / forecast.area
        score_b = (eligible.intersection(truth).area / eligible.area
                   if eligible.area else None)
        output.append({"case": name, "candidate_a": score_a,
                       "candidate_b": score_b})
    return output


def summarize(rows):
    ratios = np.array([row["in_domain_fraction"] for row in rows], dtype=float)
    partial = (ratios < 1 - 1e-9) & (ratios > 1e-12)
    summary = {
        "artifact_method": "legacy independent-max frozen truth",
        "feature_count": len(rows),
        "fully_inside": int(np.sum(ratios >= 1 - 1e-9)),
        "partially_outside": int(np.sum(partial)),
        "fully_outside": int(np.sum(ratios <= 1e-12)),
        "minimum_in_domain_fraction": float(np.min(ratios)),
        "median_in_domain_fraction": float(np.median(ratios)),
        "outside_threshold_counts": {
            str(threshold): int(np.sum((1 - ratios) > threshold))
            for threshold in (0.01, 0.05, 0.10, 0.25, 0.50)
        },
        "category_changes": sum(row["current_category"] != row["candidate_category"]
                                for row in rows),
        "by_feature_type": {},
        "synthetic_cases": synthetic_cases(),
    }
    for feature_type in sorted({row["feat_type"] for row in rows}):
        subset = [row for row in rows if row["feat_type"] == feature_type]
        values = np.array([row["in_domain_fraction"] for row in subset])
        summary["by_feature_type"][feature_type] = {
            "count": len(subset),
            "fully_inside": int(np.sum(values >= 1 - 1e-9)),
            "partially_outside": int(np.sum((values < 1 - 1e-9) & (values > 1e-12))),
            "fully_outside": int(np.sum(values <= 1e-12)),
        }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    rows = audit_rows()
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    payload = json.dumps(summarize(rows), indent=2) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
