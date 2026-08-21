#!/usr/bin/env python3
"""Read-only comparison of post-clip and pre-clip truth-area filtering.

Frozen arrays lack Decision 1A paired masks, so observational results are
explicitly legacy independent-max reconstructions. No baseline is modified.
"""

import argparse
import csv
import json
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
from scipy.ndimage import binary_dilation, uniform_filter
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline  # noqa: E402

ARTIFACT_METHOD = "legacy independent-max frozen truth"
HISTORICAL_MIN_AREA_M2 = 15_000_000_000


def physical(geometry):
    return gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
        tcf_pipeline.PHYSICAL_AREA_CRS).iloc[0]


def parts(geometry):
    if geometry.is_empty:
        return []
    return list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]


def category(fraction, params):
    if fraction >= params.verified_well_cutoff:
        return "Verified Well"
    if fraction >= params.verified_close_cutoff:
        return "Verified Close"
    return "Overforecasted"


def component_analysis(mask, lons, lats, domain, minimum_m2, event_id, truth_class):
    complete = tcf_pipeline._mask_cell_union(mask, lons, lats)
    rows, current_geometries, candidate_geometries = [], [], []
    for component_id, observed in enumerate(parts(complete), start=1):
        clipped = observed.intersection(domain).buffer(0)
        observed_area = physical(observed).area
        clipped_area = physical(clipped).area if not clipped.is_empty else 0.0
        current = clipped_area >= minimum_m2
        candidate = observed_area >= minimum_m2 and not clipped.is_empty
        if current:
            current_geometries.append(clipped)
        if candidate:
            candidate_geometries.append(clipped)
        min_lon, min_lat, max_lon, max_lat = observed.bounds
        clipped_piece_count = len(parts(clipped))
        rows.append({
            "event_id": event_id,
            "truth_class": truth_class,
            "component_id": component_id,
            "preclip_area_km2": observed_area / 1e6,
            "indomain_area_km2": clipped_area / 1e6,
            "indomain_fraction": clipped_area / observed_area if observed_area else 0.0,
            "boundary_relation": ("outside" if clipped.is_empty else
                                  "inside" if abs(clipped_area - observed_area) <=
                                  max(1.0, observed_area * 1e-9) else "crossing"),
            "clipped_piece_count": clipped_piece_count,
            "current_postclip_retained": current,
            "candidate_preclip_retained": candidate,
            "retention_changed": current != candidate,
            "clipped_geometry_empty": clipped.is_empty,
            "centroid_lon": observed.centroid.x,
            "centroid_lat": observed.centroid.y,
            "min_lon": min_lon, "min_lat": min_lat,
            "max_lon": max_lon, "max_lat": max_lat,
            "artifact_method": ARTIFACT_METHOD,
        })
    current_union = unary_union(current_geometries) if current_geometries else Polygon()
    candidate_union = unary_union(candidate_geometries) if candidate_geometries else Polygon()
    return rows, current_union, candidate_union


def grade_forecasts(forecasts, sparse_current, sparse_candidate,
                    medium_current, medium_candidate, params, event_id):
    rows = []
    sparse_current_m, sparse_candidate_m = map(physical,
                                               (sparse_current, sparse_candidate))
    medium_current_m, medium_candidate_m = map(physical,
                                               (medium_current, medium_candidate))
    for source_number, feature in enumerate(forecasts.itertuples(), start=1):
        forecast_m = physical(feature.geometry)
        if feature.coverage == 3:
            current_truth, candidate_truth = sparse_current_m, sparse_candidate_m
        else:
            current_truth, candidate_truth = medium_current_m, medium_candidate_m
        area = forecast_m.area
        current = forecast_m.intersection(current_truth).area / area if area else 0.0
        candidate = forecast_m.intersection(candidate_truth).area / area if area else 0.0
        rows.append({
            "event_id": event_id,
            "source_feature_number": source_number,
            "feat_type": feature.feat_type,
            "coverage_code": int(feature.coverage),
            "current_fraction": current,
            "candidate_fraction": candidate,
            "fraction_difference": candidate - current,
            "current_category": category(current, params),
            "candidate_category": category(candidate, params),
            "category_changed": category(current, params) != category(candidate, params),
            "artifact_method": ARTIFACT_METHOD,
        })
    return rows


def misses(truth, forecasts, params):
    forecast_union_m = physical(forecasts.union_all())
    output = []
    for index, geometry in enumerate(parts(truth), start=1):
        geometry_m = physical(geometry)
        captured = (geometry_m.intersection(forecast_union_m).area / geometry_m.area
                    if geometry_m.area else 0.0)
        if captured < params.miss_capture_threshold:
            output.append({"component": index, "captured": captured,
                           "bounds": list(geometry.bounds),
                           "area_km2": geometry_m.area / 1e6})
    return output


def synthetic_cases():
    minimum = 15_000.0
    cases = [
        ("inside_20000", 20_000, 20_000),
        ("inside_10000", 10_000, 10_000),
        ("crossing_20000_12000", 20_000, 12_000),
        ("large_parent_100000_sliver_2000", 100_000, 2_000),
        ("parent_14000_inside_13000", 14_000, 13_000),
        ("split_parent_15100_combined_8000", 15_100, 8_000),
        ("negligible_loss", 20_000, 19_999),
    ]
    output = [{
        "case": name,
        "preclip_area_km2": whole,
        "indomain_area_km2": inside,
        "candidate_a_postclip_retained": inside >= minimum,
        "candidate_b_preclip_retained": whole >= minimum and inside > 0,
    } for name, whole, inside in cases]

    # Exact Cartesian component/topology cases; one square unit represents km².
    separate = [box(0, 0, 100, 100), box(200, 0, 300, 100)]
    output.append({
        "case": "two_disconnected_10000_components",
        "combined_area_km2": sum(item.area for item in separate),
        "current_component_retention": [item.area >= minimum for item in separate],
        "note": "components are filtered separately before final union",
    })
    holed = Polygon([(0, 0), (200, 0), (200, 100), (0, 100)],
                     holes=[[(10, 10), (60, 10), (60, 30), (10, 30)]])
    output.append({
        "case": "polygon_with_1000_hole",
        "outer_area_km2": 20_000.0,
        "hole_area_km2": 1_000.0,
        "measured_area_km2": holed.area,
        "retained": holed.area >= minimum,
    })
    parent = box(0, 0, 200, 100)
    disconnected_domain = unary_union([box(0, 0, 40, 100),
                                       box(160, 0, 200, 100)])
    clipped = parent.intersection(disconnected_domain)
    output.append({
        "case": "parent_split_across_two_domain_pieces",
        "parent_area_km2": parent.area,
        "combined_clipped_area_km2": clipped.area,
        "clipped_piece_count": len(parts(clipped)),
        "candidate_a_postclip_retained": clipped.area >= minimum,
        "candidate_b_preclip_retained": parent.area >= minimum,
    })
    return output


def run_audit(baseline_dir=REPO_ROOT / "baseline"):
    params = tcf_pipeline.GradingParams()
    domain = tcf_pipeline.verification_domain()
    component_rows, forecast_rows, events = [], [], {}
    for event_dir in sorted(baseline_dir.glob("20*")):
        paths = [event_dir / name for name in ("tcf_raw.txt", "expected.json", "arrays.npz")]
        if not all(path.exists() for path in paths):
            continue
        forecasts = tcf_pipeline.parse_iem_cow_text(paths[0].read_text(encoding="utf-8"))
        metadata = json.loads(paths[1].read_text(encoding="utf-8"))
        with np.load(paths[2]) as arrays:
            tops, refl, lons, lats = [arrays[key] for key in
                                      ("max_tops", "max_refl", "lons", "lats")]
        seed = (refl >= 40.0) & (tops >= 25.0)
        coverage = uniform_filter(binary_dilation(
            seed, iterations=params.dilation_iterations).astype(float),
            size=params.smoothing_size)
        event_truth = {}
        for truth_class, threshold in (("Sparse", params.sparse_truth_threshold),
                                       ("Medium", params.medium_truth_threshold)):
            rows, current, candidate = component_analysis(
                coverage >= threshold, lons, lats, domain, HISTORICAL_MIN_AREA_M2,
                event_dir.name, truth_class)
            component_rows.extend(rows)
            event_truth[truth_class] = (current, candidate)
        forecast_rows.extend(grade_forecasts(
            forecasts, *event_truth["Sparse"], *event_truth["Medium"], params,
            event_dir.name))
        current_misses = misses(event_truth["Sparse"][0], forecasts, params)
        candidate_misses = misses(event_truth["Sparse"][1], forecasts, params)
        sparse_current_m = physical(event_truth["Sparse"][0])
        sparse_candidate_m = physical(event_truth["Sparse"][1])
        medium_current_m = physical(event_truth["Medium"][0])
        medium_candidate_m = physical(event_truth["Medium"][1])
        events[event_dir.name] = {
            "valid_time": metadata["valid_dt"],
            "current_miss_count": len(current_misses),
            "candidate_miss_count": len(candidate_misses),
            "current_misses": current_misses,
            "candidate_misses": candidate_misses,
            "current_medium_outside_sparse_km2": (
                medium_current_m.difference(sparse_current_m).area / 1e6),
            "candidate_medium_outside_sparse_km2": (
                medium_candidate_m.difference(sparse_candidate_m).area / 1e6),
        }
    return component_rows, forecast_rows, events


def summarize(component_rows, forecast_rows, events):
    by_class = {}
    for truth_class in ("Sparse", "Medium"):
        subset = [row for row in component_rows if row["truth_class"] == truth_class]
        current_area = sum(row["indomain_area_km2"] for row in subset
                           if row["current_postclip_retained"])
        candidate_area = sum(row["indomain_area_km2"] for row in subset
                             if row["candidate_preclip_retained"])
        candidate_slivers = [row["indomain_area_km2"] for row in subset
                             if row["candidate_preclip_retained"] and
                             not row["current_postclip_retained"]]
        crossings = [row for row in subset if row["boundary_relation"] == "crossing"]
        largest_crossing = (max(crossings, key=lambda row: row["preclip_area_km2"])
                            if crossings else None)
        by_class[truth_class] = {
            "component_count": len(subset),
            "inside": sum(row["boundary_relation"] == "inside" for row in subset),
            "crossing": sum(row["boundary_relation"] == "crossing" for row in subset),
            "outside": sum(row["boundary_relation"] == "outside" for row in subset),
            "retention_differences": sum(row["retention_changed"] for row in subset),
            "current_retained_area_km2": current_area,
            "candidate_retained_area_km2": candidate_area,
            "added_indomain_area_km2": candidate_area - current_area,
            "smallest_candidate_only_sliver_km2": (
                min(candidate_slivers) if candidate_slivers else None),
            "largest_crossing_component": ({
                "event_id": largest_crossing["event_id"],
                "component_id": largest_crossing["component_id"],
                "preclip_area_km2": largest_crossing["preclip_area_km2"],
                "indomain_area_km2": largest_crossing["indomain_area_km2"],
            } if largest_crossing else None),
        }
    changes = [row for row in forecast_rows if abs(row["fraction_difference"]) > 1e-12]
    return {
        "artifact_method": ARTIFACT_METHOD,
        "minimum_area_km2": 15_000,
        "truth_classes": by_class,
        "forecast_count": len(forecast_rows),
        "forecast_score_changes": len(changes),
        "largest_absolute_forecast_change": (
            max((abs(row["fraction_difference"]) for row in forecast_rows), default=0.0)),
        "forecast_category_changes": sum(row["category_changed"] for row in forecast_rows),
        "current_total_misses": sum(event["current_miss_count"] for event in events.values()),
        "candidate_total_misses": sum(event["candidate_miss_count"] for event in events.values()),
        "nested_truth_violations_current": sum(
            event["current_medium_outside_sparse_km2"] > 1e-3 for event in events.values()),
        "nested_truth_violations_candidate": sum(
            event["candidate_medium_outside_sparse_km2"] > 1e-3 for event in events.values()),
        "events": events,
        "synthetic_cases": synthetic_cases(),
    }


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--components", type=Path)
    parser.add_argument("--forecasts", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    components, forecasts, events = run_audit()
    if args.components:
        write_csv(args.components, components)
    if args.forecasts:
        write_csv(args.forecasts, forecasts)
    payload = json.dumps(summarize(components, forecasts, events), indent=2) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
