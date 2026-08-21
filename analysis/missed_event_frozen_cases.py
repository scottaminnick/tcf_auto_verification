#!/usr/bin/env python3
"""Read-only audit and sensitivity analysis of automated missed events."""

import argparse
import csv
import json
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ANALYSIS_DIR))

import minimum_area_threshold_sensitivity as area_analysis  # noqa: E402
import tcf_pipeline  # noqa: E402

ARTIFACT_METHOD = "legacy independent-max frozen truth"
AREA_FLOORS_KM2 = (0, 5_000, 10_000, 15_000, 20_000, 25_000)
CAPTURE_THRESHOLDS = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.0)


def physical(geometry):
    return gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
        tcf_pipeline.PHYSICAL_AREA_CRS).iloc[0]


def forecast_groups(forecasts):
    groups = {
        "Sparse AREA": forecasts[(forecasts.feat_type == "AREA") &
                                 (forecasts.coverage == 3)],
        "Medium AREA": forecasts[(forecasts.feat_type == "AREA") &
                                 (forecasts.coverage == 2)],
        "Solid LINE": forecasts[(forecasts.feat_type == "LINE") &
                                (forecasts.coverage == 1)],
    }
    return {name: (frame.union_all() if not frame.empty else Polygon())
            for name, frame in groups.items()}


def fraction(observed_m, forecast_m):
    return (observed_m.intersection(forecast_m).area / observed_m.area
            if observed_m.area else 0.0)


def component_rows(event_data, artccs):
    rows = []
    for event_id, (forecasts, classes) in event_data.items():
        groups = forecast_groups(forecasts)
        groups_m = {name: physical(geometry) for name, geometry in groups.items()}
        full_union = unary_union(list(groups.values()))
        full_union_m = physical(full_union)
        medium_rows, medium_geometries = classes["Medium"]
        retained_medium = area_analysis.retained_union(
            medium_rows, medium_geometries, 15_000)
        retained_medium_m = physical(retained_medium)
        sparse_rows, sparse_geometries = classes["Sparse"]
        for component in sparse_rows:
            geometry = sparse_geometries[component["component_id"]]
            if geometry.is_empty:
                continue
            observed_m = physical(geometry)
            capture = fraction(observed_m, full_union_m)
            class_capture = {name: fraction(observed_m, union_m)
                             for name, union_m in groups_m.items()}
            marginal = {}
            for name in groups:
                without = unary_union([geometry_ for other, geometry_ in groups.items()
                                       if other != name])
                marginal[name] = max(0.0, capture - fraction(observed_m, physical(without)))
            nearest_name, nearest_km = None, None
            for name, union_m in groups_m.items():
                if union_m.is_empty:
                    continue
                distance = observed_m.distance(union_m) / 1000
                if nearest_km is None or distance < nearest_km:
                    nearest_name, nearest_km = name, distance
            rows.append({
                "event_id": event_id,
                "component_id": component["component_id"],
                "truth_class": "Sparse",
                "area_km2": component["area_km2"],
                "preclip_area_km2": component["preclip_area_km2"],
                "domain_status": component["domain_status"],
                "retained_at_15000": component["area_km2"] >= 15_000,
                "capture_fraction": capture,
                "current_miss": component["area_km2"] >= 15_000 and capture < .20,
                "sparse_area_capture": class_capture["Sparse AREA"],
                "medium_area_capture": class_capture["Medium AREA"],
                "solid_line_capture": class_capture["Solid LINE"],
                "sparse_area_unique_capture": marginal["Sparse AREA"],
                "medium_area_unique_capture": marginal["Medium AREA"],
                "solid_line_unique_capture": marginal["Solid LINE"],
                "retained_medium_core_fraction": fraction(observed_m, retained_medium_m),
                "nearest_forecast_class": nearest_name,
                "nearest_forecast_distance_km": nearest_km,
                "artccs": tcf_pipeline.get_artccs(geometry, artccs),
                "min_lon": geometry.bounds[0], "min_lat": geometry.bounds[1],
                "max_lon": geometry.bounds[2], "max_lat": geometry.bounds[3],
                "artifact_method": ARTIFACT_METHOD,
            })
    # Production miss labels are east-to-west within each event.
    for event_id in {row["event_id"] for row in rows}:
        misses = [row for row in rows if row["event_id"] == event_id and row["current_miss"]]
        misses.sort(key=lambda row: (row["min_lon"] + row["max_lon"]) / 2, reverse=True)
        for index, row in enumerate(misses, start=1):
            row["report_label"] = f"{row['artccs']} - Missed (Area M{index})"
    for row in rows:
        row.setdefault("report_label", "")
    return rows


def sensitivity_rows(event_data):
    rows = []
    for floor in AREA_FLOORS_KM2:
        for threshold in CAPTURE_THRESHOLDS:
            event_counts, eligible_count, miss_count = {}, 0, 0
            for event_id, (forecasts, classes) in event_data.items():
                sparse_rows, sparse_geometries = classes["Sparse"]
                truth = area_analysis.retained_union(
                    sparse_rows, sparse_geometries, floor)
                truth_parts = area_analysis.parts(truth)
                eligible_count += len(truth_parts)
                forecast_union_m = physical(forecasts.union_all())
                count = sum(fraction(physical(geometry), forecast_union_m) < threshold
                            for geometry in truth_parts)
                event_counts[event_id] = count
                miss_count += count
            rows.append({
                "minimum_area_km2": floor,
                "capture_threshold": threshold,
                "eligible_components": eligible_count,
                "total_misses": miss_count,
                **{f"misses_{event_id}": count for event_id, count in event_counts.items()},
                "artifact_method": ARTIFACT_METHOD,
            })
    return rows


def capture_sweep(event_data):
    current_eligible = []
    for event_id, (forecasts, classes) in event_data.items():
        sparse_rows, sparse_geometries = classes["Sparse"]
        truth = area_analysis.retained_union(sparse_rows, sparse_geometries, 15_000)
        forecast_union_m = physical(forecasts.union_all())
        for component_id, geometry in enumerate(area_analysis.parts(truth), start=1):
            geometry_m = physical(geometry)
            current_eligible.append({
                "event_id": event_id, "component_id": component_id,
                "area_km2": geometry_m.area / 1e6,
                "capture_fraction": fraction(geometry_m, forecast_union_m),
            })
    output = []
    for threshold in CAPTURE_THRESHOLDS:
        misses = [row for row in current_eligible if row["capture_fraction"] < threshold]
        output.append({
            "capture_threshold": threshold,
            "total_misses": len(misses),
            "misses_per_event": {
                event_id: sum(row["event_id"] == event_id for row in misses)
                for event_id in sorted(event_data)
            },
            "components": [{"event_id": row["event_id"],
                            "component_id": row["component_id"],
                            "area_km2": row["area_km2"],
                            "capture_fraction": row["capture_fraction"]}
                           for row in misses],
        })
    return output


def synthetic_cases():
    observed = box(0, 0, 100, 100)  # 10,000 arbitrary square units
    cases = []
    examples = {
        "unforecast": Polygon(),
        "fully_forecast": observed,
        "exactly_half": box(0, 0, 50, 100),
        "small_centered": box(40, 40, 60, 60),
        "large_barely_clips": box(90, -100, 300, 200),
    }
    for name, forecast in examples.items():
        capture = observed.intersection(forecast).area / observed.area
        cases.append({"case": name, "capture_fraction": capture,
                      "miss_at_current_20_percent": capture < .20})
    first, second = box(0, 0, 60, 100), box(40, 0, 100, 100)
    cases.append({
        "case": "overlapping_forecasts_union",
        "individual_capture_sum": (observed.intersection(first).area +
                                   observed.intersection(second).area) / observed.area,
        "union_capture": observed.intersection(unary_union([first, second])).area /
                         observed.area,
    })
    cases.extend([
        {"case": "sparse_forecast_covers_medium_core",
         "sparse_truth_capture": .25, "medium_core_capture": 1.0,
         "current_miss": False},
        {"case": "medium_forecast_covers_core_only",
         "broad_sparse_capture": .15, "medium_core_capture": 1.0,
         "current_miss": True},
        {"case": "area_just_below_floor_unforecast",
         "area_km2": 14_999, "eligible": False, "current_miss": False},
        {"case": "area_just_above_floor_unforecast",
         "area_km2": 15_001, "eligible": True, "current_miss": True},
    ])
    # Explicitly preserve the production angular LINE buffer for this diagnostic.
    observed_geo = box(-97, 34.8, -96, 35.2)
    line_corridor = LineString([(-97.2, 35), (-95.8, 35)]).buffer(
        tcf_pipeline.LINE_BUFFER_DEG)
    observed_m, corridor_m = physical(observed_geo), physical(line_corridor)
    cases.append({
        "case": "solid_line_0_15_degree_corridor_crosses_area",
        "line_buffer_degrees": tcf_pipeline.LINE_BUFFER_DEG,
        "capture_fraction": fraction(observed_m, corridor_m),
        "miss_at_current_20_percent": fraction(observed_m, corridor_m) < .20,
    })
    return cases


def summarize(components, sensitivity, event_data):
    current_misses = [row for row in components if row["current_miss"]]
    near_misses = [row for row in components if row["retained_at_15000"] and
                   not row["current_miss"] and row["capture_fraction"] < .30]
    below_floor_misses = [row for row in components if 0 < row["area_km2"] < 15_000
                          and row["capture_fraction"] < .20]
    line_components = [row for row in components if row["solid_line_capture"] > 0]
    current_line_misses = [row for row in current_misses if row["solid_line_capture"] > 0]
    return {
        "artifact_method": ARTIFACT_METHOD,
        "current_minimum_area_km2": 15_000,
        "current_capture_threshold": .20,
        "current_miss_count": len(current_misses),
        "current_misses": current_misses,
        "near_misses_20_to_30_percent_capture": near_misses,
        "below_floor_components_that_would_miss": below_floor_misses,
        "capture_threshold_sweep_at_15000": capture_sweep(event_data),
        "area_capture_matrix": sensitivity,
        "solid_line": {
            "components_with_any_line_overlap": len(line_components),
            "current_misses_with_line_overlap": len(current_line_misses),
            "maximum_component_capture_from_line": max(
                (row["solid_line_capture"] for row in components), default=0.0),
            "maximum_unique_capture_from_line": max(
                (row["solid_line_unique_capture"] for row in components), default=0.0),
        },
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
    parser.add_argument("--sensitivity", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    _, _, _, _, event_data = area_analysis.run_audit()
    components = component_rows(event_data, tcf_pipeline.load_artccs())
    sensitivity = sensitivity_rows(event_data)
    if args.components:
        write_csv(args.components, components)
    if args.sensitivity:
        write_csv(args.sensitivity, sensitivity)
    payload = json.dumps(summarize(components, sensitivity, event_data), indent=2) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
