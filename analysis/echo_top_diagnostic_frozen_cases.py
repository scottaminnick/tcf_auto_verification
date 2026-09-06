#!/usr/bin/env python3
"""Audit the legacy temporal-max echo-top diagnostic without changing policy."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tcf_pipeline  # noqa: E402


def statistics(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {k: None for k in ("minimum", "median", "mean", "p75", "p90", "p95", "maximum")}
    return {
        "minimum": float(np.min(values)), "median": float(np.median(values)),
        "mean": float(np.mean(values)), "p75": float(np.percentile(values, 75)),
        "p90": float(np.percentile(values, 90)), "p95": float(np.percentile(values, 95)),
        "maximum": float(np.max(values)),
    }


def sample(geometry, tops, refl, lons, lats) -> tuple[np.ndarray, int]:
    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    lat_sel = (lats >= min_lat) & (lats <= max_lat)
    lon_sel = (lons >= min_lon) & (lons <= max_lon)
    top = tops[lat_sel][:, lon_sel]
    ref = refl[lat_sel][:, lon_sel]
    lon_grid, lat_grid = np.meshgrid(lons[lon_sel], lats[lat_sel])
    interior = tcf_pipeline._geometry_point_mask(geometry, lon_grid, lat_grid)
    available = interior & np.isfinite(ref) & np.isfinite(top)
    valid = interior & (ref >= 40) & (top >= 25)
    return top[valid], int(np.count_nonzero(available))


def frozen_rows() -> tuple[list[dict], int]:
    rows = []
    report_changes = 0
    artcc = tcf_pipeline.load_artccs()
    for event_dir in sorted((ROOT / "baseline").glob("20*")):
        expected = json.loads((event_dir / "expected.json").read_text())
        forecasts = tcf_pipeline.parse_iem_cow_text((event_dir / "tcf_raw.txt").read_text())
        with np.load(event_dir / "arrays.npz") as arrays:
            tops, refl, lons, lats = [arrays[k] for k in ("max_tops", "max_refl", "lons", "lats")]
        result = tcf_pipeline.run_verification_legacy_independent_max(
            forecasts, tops, refl, lons, lats,
            datetime.fromisoformat(expected["valid_dt"]), expected["issuance_hour"],
            expected["lead_time"], artcc)
        previous_table = result["review_table"].copy()
        forecast_rows = previous_table["kind"] == "forecast"
        previous_table.loc[forecast_rows, "top_kft"] = previous_table.loc[
            forecast_rows, "top_kft"].fillna(0.0)
        previous_report = tcf_pipeline.build_report(
            previous_table, datetime.fromisoformat(expected["valid_dt"]),
            expected["issuance_hour"], expected["lead_time"])
        report_changes += previous_report != result["report_text"]
        graded = result["graded_forecasts"]
        for i, feature in forecasts.reset_index(drop=True).iterrows():
            values, available_count = sample(feature.geometry, tops, refl, lons, lats)
            stats = statistics(values)
            current = next(item for item in graded
                           if item["geometry"].equals(feature.geometry)
                           and item["coverage"] == feature.coverage
                           and item["feat_type"] == feature.feat_type)
            row = {
                "event_id": event_dir.name, "feature": i + 1,
                "feat_type": feature.feat_type, "coverage_code": int(feature.coverage),
                "verification_category": current["category"],
                "interior_available_cell_count": available_count,
                "valid_cell_count": len(values), **stats,
                "current_reported_echo_top_kft": (float(current["top"]) if current["top"] is not None else None),
                "current_small_sample_result_kft": None if len(values) < 6 else stats["p90"],
                "artifact_method": "legacy independent temporal-max max_tops/max_refl",
            }
            rows.append(row)
    return rows, report_changes


def synthetic() -> dict:
    cases = {
        "uniform_fl300": [30] * 10,
        "nine_fl250_one_fl500": [25] * 9 + [50],
        "broad_fl200_to_fl450": [20, 25, 30, 35, 40, 45],
        "bimodal": [25] * 5 + [40] * 5,
        "exactly_six": [25, 27, 29, 31, 33, 45],
        "five_valid": [30] * 5,
        "one_fl450": [45],
        "large_small_high_core": [25] * 95 + [45] * 5,
        "large_mostly_high": [40] * 95 + [25] * 5,
        "hole_high_values_excluded": [30] * 6,
    }
    return {name: {"valid_cell_count": len(vals), **statistics(np.array(vals)),
                   "current_result": statistics(np.array(vals))["p90"] if len(vals) >= 6 else None}
            for name, vals in cases.items()}


def cell_areas() -> dict:
    output = {}
    for lat in (30, 40, 50):
        geom = box(-100.025, lat - 0.025, -99.975, lat + 0.025)
        area = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(tcf_pipeline.PHYSICAL_AREA_CRS).area.iloc[0] / 1e6
        output[str(lat)] = {"one_cell_km2": float(area), "six_cells_km2": float(area * 6)}
    return output


def main() -> None:
    rows, report_changes = frozen_rows()
    csv_path = ROOT / "analysis/echo_top_diagnostic_features.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    counts = np.array([r["valid_cell_count"] for r in rows])
    affected = [r for r in rows if r["valid_cell_count"] < 6]
    positive = [r for r in rows if r["p90"] is not None]
    summary = {
        "scope": "read_only_legacy_independent_temporal_max_characterization",
        "feature_count": len(rows),
        "sample_counts": {
            "minimum": int(counts.min()), "p10": float(np.percentile(counts, 10)),
            "p25": float(np.percentile(counts, 25)), "median": float(np.median(counts)),
            "p75": float(np.percentile(counts, 75)), "p90": float(np.percentile(counts, 90)),
            "maximum": int(counts.max()),
            "bands": {"0": int(sum(counts == 0)), "1_to_5": int(sum((counts >= 1) & (counts <= 5))),
                      "6_to_10": int(sum((counts >= 6) & (counts <= 10))),
                      "11_to_25": int(sum((counts >= 11) & (counts <= 25))),
                      "26_to_50": int(sum((counts >= 26) & (counts <= 50))),
                      "over_50": int(sum(counts > 50))},
            "six_cell_rule_features": len(affected),
        },
        "statistic_differences_kft": {
            "median_max_minus_p90": float(np.median([r["maximum"] - r["p90"] for r in positive])),
            "maximum_max_minus_p90": float(max(r["maximum"] - r["p90"] for r in positive)),
            "median_p95_minus_p90": float(np.median([r["p95"] - r["p90"] for r in positive])),
            "median_p90_minus_median": float(np.median([r["p90"] - r["median"] for r in positive])),
            "largest_max_minus_p90_features": [
                {"event_id": r["event_id"], "feature": r["feature"],
                 "feat_type": r["feat_type"], "valid_cell_count": r["valid_cell_count"],
                 "max_minus_p90": r["maximum"] - r["p90"]}
                for r in sorted(positive, key=lambda x: x["maximum"] - x["p90"], reverse=True)[:5]],
        },
        "small_sample_features": [
            {"event_id": r["event_id"], "feature": r["feature"],
             "feat_type": r["feat_type"], "valid_cell_count": r["valid_cell_count"],
             "observed_p90_if_computed": r["p90"], "production_result": r["current_reported_echo_top_kft"]}
            for r in affected],
        "one_and_six_cell_physical_area_by_latitude": cell_areas(),
        "synthetic_cases": synthetic(),
        "miss_echo_top_behavior": "not_calculated; production review rows set miss top_kft unavailable",
        "historical_semantic_change": {
            "features_from_zero_to_unavailable": len(affected),
            "verification_fraction_or_category_changes": 0,
            "faa_text_changes": report_changes,
        },
        "recommendation": "OPTION_C_retain_full_geometry_p90_provisionally_but_resolve_intent_sample_domain_and_minimum",
    }
    (ROOT / "analysis/echo_top_diagnostic_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
