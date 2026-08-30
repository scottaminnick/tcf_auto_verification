#!/usr/bin/env python3
"""Read-only sensitivity audit of paired truth-transformation artifacts.

Only artifacts containing a stored Decision 1A ``qualifying_mask`` are
accepted.  Independent temporal maxima are loaded solely because the production
verification function retains them for reviewer echo-top diagnostics; they are
never used to reconstruct the truth seed.
"""

import argparse
import csv
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely import union_all
from shapely.geometry import Polygon

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline as pipeline  # noqa: E402

DILATIONS = (0, 1)
SMOOTHING_SIZES = (5, 10, 15, 20)
CURRENT = (1, 15)


def load_paired_event(event_dir):
    """Load and validate one fresh paired-evidence directory."""
    event_dir = Path(event_dir)
    required = ("arrays.npz", "tcf_raw.txt", "mrms_provenance.json",
                "validation.json")
    missing = [name for name in required if not (event_dir / name).is_file()]
    if missing:
        raise ValueError(f"{event_dir}: incomplete paired artifact; missing {', '.join(missing)}")
    with np.load(event_dir / "arrays.npz") as arrays:
        if "qualifying_mask" not in arrays.files:
            raise ValueError(
                f"{event_dir}: legacy maxima-only artifact rejected: "
                "arrays.npz has no qualifying_mask")
        needed = {"max_tops", "max_refl", "qualifying_mask", "lons", "lats"}
        absent = sorted(needed - set(arrays.files))
        if absent:
            raise ValueError(f"{event_dir}: arrays.npz missing {', '.join(absent)}")
        data = {key: arrays[key].copy() for key in needed}
    with open(event_dir / "validation.json", encoding="utf-8") as stream:
        validation = json.load(stream)
    with open(event_dir / "mrms_provenance.json", encoding="utf-8") as stream:
        provenance = json.load(stream)
    if validation.get("artifact_state") != "paired-validation-evidence-not-baseline":
        raise ValueError(f"{event_dir}: artifact is not identified as fresh paired validation evidence")
    if int(provenance.get("observations_used", provenance.get("usable_pair_count", 0))) < 1:
        raise ValueError(f"{event_dir}: paired provenance records zero usable pairs")
    return data, validation


def _components(gdf):
    return pipeline._individual_geometries(gdf)


def _area_km2(geometry):
    return (gpd.GeoSeries([geometry], crs="EPSG:4326")
            .to_crs(pipeline.PHYSICAL_AREA_CRS).area.iloc[0] / 1_000_000.0)


def _spatial_fields(row, geometry):
    centroid = geometry.centroid
    bounds = geometry.bounds
    row.update({
        "centroid_lon": centroid.x, "centroid_lat": centroid.y,
        "min_lon": bounds[0], "min_lat": bounds[1],
        "max_lon": bounds[2], "max_lat": bounds[3],
    })


def audit_event(event_dir, artccs):
    data, metadata = load_paired_event(event_dir)
    event_id = metadata.get("event_id", Path(event_dir).name)
    with open(Path(event_dir) / "tcf_raw.txt", encoding="utf-8") as stream:
        forecasts = pipeline.parse_iem_cow_text(stream.read())
    valid_dt = datetime.fromisoformat(metadata["valid_dt"])
    base = pipeline.GradingParams()
    rows = {"event": [], "forecast": [], "candidate": [], "medium": []}

    for dilation in DILATIONS:
        for smoothing in SMOOTHING_SIZES:
            params = replace(base, dilation_iterations=dilation,
                             smoothing_size=smoothing)
            result = pipeline.run_verification(
                forecasts, data["max_tops"], data["max_refl"], data["lons"],
                data["lats"], valid_dt, int(metadata["issuance_hour"]),
                int(metadata["lead_time"]), artccs, params=params,
                qualifying_mask=data["qualifying_mask"])
            sparse = _components(result["gdf_sparse"])
            medium = _components(result["gdf_medium_truth"])
            forecast_union = forecasts.union_all()
            forecast_m = gpd.GeoSeries(
                [forecast_union], crs="EPSG:4326").to_crs(
                    pipeline.PHYSICAL_AREA_CRS).iloc[0]
            sparse_m = (list(gpd.GeoSeries(sparse, crs="EPSG:4326").to_crs(
                pipeline.PHYSICAL_AREA_CRS)) if sparse else [])
            sparse_m = [pipeline.validate_projected_polygonal(g) for g in sparse_m]
            medium_m_all = (list(gpd.GeoSeries(medium, crs="EPSG:4326").to_crs(
                pipeline.PHYSICAL_AREA_CRS)) if medium else [])
            medium_m_all = [pipeline.validate_projected_polygonal(g)
                            for g in medium_m_all]
            medium_union_m = union_all(medium_m_all) if medium_m_all else Polygon()
            low_capture_medium_count = sum(
                (geometry_m.intersection(forecast_m).area / geometry_m.area
                 if geometry_m.area else 0.0) < params.miss_capture_threshold
                for geometry_m in medium_m_all)
            low_capture = []
            for component_index, (geometry, geometry_m) in enumerate(
                    zip(sparse, sparse_m), 1):
                area = geometry_m.area
                capture = (geometry_m.intersection(forecast_m).area / area
                           if area else 0.0)
                if capture < params.miss_capture_threshold:
                    low_capture.append((component_index, geometry, geometry_m,
                                        area, capture))
            counts = (result["gdf_graded_fcst"]["category"].value_counts()
                      if "category" in result["gdf_graded_fcst"] else {})
            rows["event"].append({
                "event_id": event_id, "dilation_iterations": dilation,
                "smoothing_size": smoothing,
                "sparse_component_count": len(sparse),
                "total_sparse_area_km2": sum(map(_area_km2, sparse)),
                "medium_component_count": len(medium),
                "total_medium_area_km2": sum(map(_area_km2, medium)),
                "low_capture_sparse_component_count": len(low_capture),
                "candidate_miss_count": len(result["gdf_graded_miss"]),
                "low_capture_medium_component_count": low_capture_medium_count,
                "medium_core_review_flag_count": len(result["gdf_medium_core_flags"]),
                "verified_well_count": int(counts.get("Verified Well", 0)),
                "verified_close_count": int(counts.get("Verified Close", 0)),
                "overforecasted_count": int(counts.get("Overforecasted", 0)),
                "boundary_count": int(result["review_table"]["boundary"].fillna(False).sum()),
            })
            for item in result["graded_forecasts"]:
                rows["forecast"].append({
                    "event_id": event_id, "dilation_iterations": dilation,
                    "smoothing_size": smoothing, "forecast_index": item["idx"],
                    "feature_type": item["feat_type"],
                    "coverage_code": item["coverage"],
                    "coverage_fraction": item["coverage_fraction"],
                    "category": item["category"],
                    "boundary": pipeline.is_boundary(item["coverage_fraction"], params),
                })
            for component_index, geometry, geometry_m, area, capture in low_capture:
                core_area = geometry_m.intersection(medium_union_m).area
                row = {
                    "event_id": event_id, "dilation_iterations": dilation,
                    "smoothing_size": smoothing,
                    "sparse_component_index": component_index,
                    "area_km2": area / 1_000_000.0,
                    "forecast_capture_fraction": capture,
                    "medium_core_area_km2": core_area / 1_000_000.0,
                    "medium_core_fraction": core_area / area if area else 0.0,
                    "contains_medium_core": core_area > 0.0,
                    "eligible_candidate_miss": (
                        area >= params.candidate_miss_min_area_m2),
                }
                _spatial_fields(row, geometry)
                rows["candidate"].append(row)
            flag_geometries = {geom.wkb: flag for geom, flag in zip(
                result["gdf_medium_core_flags"].geometry,
                result["medium_core_review_flags"])}
            sparse_geoms = _components(result["gdf_sparse"])
            sparse_m = (list(gpd.GeoSeries(sparse_geoms, crs="EPSG:4326").to_crs(
                pipeline.PHYSICAL_AREA_CRS)) if sparse_geoms else [])
            sparse_m = [pipeline.validate_projected_polygonal(g) for g in sparse_m]
            sparse_is_candidate = {}
            for sparse_id, sparse_geometry_m in enumerate(sparse_m, 1):
                sparse_area = sparse_geometry_m.area
                sparse_capture = (
                    sparse_geometry_m.intersection(forecast_m).area / sparse_area
                    if sparse_area else 0.0)
                sparse_is_candidate[sparse_id] = (
                    sparse_capture < params.miss_capture_threshold
                    and sparse_area >= params.candidate_miss_min_area_m2)
            for component_index, geometry in enumerate(medium, 1):
                geometry_m = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
                    pipeline.PHYSICAL_AREA_CRS).iloc[0]
                area = geometry_m.area
                capture = geometry_m.intersection(forecast_m).area / area if area else 0.0
                parent_id = None
                best = 0.0
                for sparse_id, sparse_geometry_m in enumerate(sparse_m, 1):
                    overlap = geometry_m.intersection(sparse_geometry_m).area
                    if overlap > best:
                        parent_id, best = sparse_id, overlap
                flag = flag_geometries.get(geometry.wkb)
                capture_below = capture < params.miss_capture_threshold
                area_eligible = area >= params.medium_core_review_min_area_m2
                parent_candidate = bool(
                    parent_id is not None and sparse_is_candidate[parent_id])
                row = {
                    "event_id": event_id, "dilation_iterations": dilation,
                    "smoothing_size": smoothing, "medium_component_index": component_index,
                    "area_km2": area / 1_000_000.0,
                    "forecast_capture_fraction": capture,
                    "parent_sparse_component_id": parent_id,
                    "is_medium_core_review_flag": flag is not None,
                    "eligible_medium_core_review_flag": (
                        capture_below and area_eligible and not parent_candidate),
                    "below_medium_area_floor": not area_eligible,
                    "parent_is_candidate_miss": parent_candidate,
                    "capture_not_below_threshold": not capture_below,
                }
                _spatial_fields(row, geometry)
                rows["medium"].append(row)
    return rows


def _write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path, event_rows, forecast_rows):
    current = {(r["event_id"], r["forecast_index"]): r for r in forecast_rows
               if (r["dilation_iterations"], r["smoothing_size"]) == CURRENT}
    changes = {}
    for row in forecast_rows:
        reference = current.get((row["event_id"], row["forecast_index"]))
        if reference and row["category"] != reference["category"]:
            key = (row["dilation_iterations"], row["smoothing_size"])
            changes[key] = changes.get(key, 0) + 1
    lines = [
        "# Truth spatial-transform sensitivity", "",
        "This is a diagnostic experiment, not a methodology recommendation. Truth",
        "was seeded only from stored paired `qualifying_mask` arrays.", "",
        "## Grade changes relative to production RC1 (dilation=1, smoothing=15)", "",
        "| dilation | smoothing | changed forecasts |", "|---:|---:|---:|",
    ]
    for dilation in DILATIONS:
        for smoothing in SMOOTHING_SIZES:
            lines.append(f"| {dilation} | {smoothing} | {changes.get((dilation, smoothing), 0)} |")
    lines.extend(["", "## Event topology and review cues", "",
                  "| event | dilation | smoothing | Sparse components | Medium components | Low-capture Sparse | Candidate Misses | Low-capture Medium | Medium flags |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in event_rows:
        lines.append(
            f"| {row['event_id']} | {row['dilation_iterations']} | "
            f"{row['smoothing_size']} | {row['sparse_component_count']} | "
            f"{row['medium_component_count']} | "
            f"{row['low_capture_sparse_component_count']} | "
            f"{row['candidate_miss_count']} | "
            f"{row['low_capture_medium_component_count']} | "
            f"{row['medium_core_review_flag_count']} |")
    lines.extend(["", "Large changes across adjacent rows identify topology or",
                  "small-object sensitivity. Compare each event's dilation=1,",
                  "smoothing=15 row with its other smoothing rows to assess whether",
                  "the current configuration is unusual. `candidate_miss_components.csv`",
                  "and `medium_components.csv` provide physical areas and spatial",
                  "coordinates; filter `event_id=20260403_21Z_F04` for the targeted",
                  "central Plains/Iowa/Illinois/Indiana/Ohio review.", "",
                  "No replacement parameter is recommended or approved by this audit.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root, output = Path(args.input_root), Path(args.output)
    event_dirs = sorted(path.parent for path in root.glob("*/arrays.npz"))
    if not event_dirs:
        parser.error(f"no paired event directories found under {root}")
    output.mkdir(parents=True, exist_ok=True)
    all_rows = {"event": [], "forecast": [], "candidate": [], "medium": []}
    artccs = pipeline.load_artccs()
    for event_dir in event_dirs:
        rows = audit_event(event_dir, artccs)
        for key in all_rows:
            all_rows[key].extend(rows[key])
    _write_csv(output / "event_summary.csv", all_rows["event"])
    _write_csv(output / "forecast_features.csv", all_rows["forecast"])
    _write_csv(output / "candidate_miss_components.csv", all_rows["candidate"])
    _write_csv(output / "medium_components.csv", all_rows["medium"])
    write_summary(output / "summary.md", all_rows["event"], all_rows["forecast"])


if __name__ == "__main__":
    main()
