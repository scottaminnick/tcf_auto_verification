#!/usr/bin/env python3
"""Read-only sensitivity study for the observational truth-area floor.

Historical results use frozen maxima-only arrays and are explicitly legacy
independent-max evidence. Synthetic experiments use same-pair Boolean seeds.
"""

import argparse
import csv
import json
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
from scipy.ndimage import binary_dilation, label, uniform_filter
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline  # noqa: E402

ARTIFACT_METHOD = "legacy independent-max frozen truth"
THRESHOLDS_KM2 = (0, 5_000, 10_000, 15_000, 20_000, 25_000, 30_000)


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


def clipped_components(mask, lons, lats, domain, event_id, truth_class):
    complete = tcf_pipeline._mask_cell_union(mask, lons, lats)
    rows = []
    geometries = {}
    for component_id, observed in enumerate(parts(complete), start=1):
        clipped = observed.intersection(domain).buffer(0)
        preclip_area = physical(observed).area / 1e6
        area = physical(clipped).area / 1e6 if not clipped.is_empty else 0.0
        relation = ("outside" if clipped.is_empty else "inside"
                    if abs(area - preclip_area) <= max(1e-6, preclip_area * 1e-9)
                    else "crossing")
        min_lon, min_lat, max_lon, max_lat = observed.bounds
        rows.append({
            "event_id": event_id,
            "truth_class": truth_class,
            "component_id": component_id,
            "area_km2": area,
            "preclip_area_km2": preclip_area,
            "domain_status": relation,
            "retained_at_15000": area >= 15_000,
            "centroid_lon": observed.centroid.x,
            "centroid_lat": observed.centroid.y,
            "min_lon": min_lon, "min_lat": min_lat,
            "max_lon": max_lon, "max_lat": max_lat,
            "artifact_method": ARTIFACT_METHOD,
        })
        geometries[component_id] = clipped
    return rows, geometries


def retained_union(component_rows, geometries, threshold_km2):
    selected = [geometries[row["component_id"]] for row in component_rows
                if row["area_km2"] > 0 and row["area_km2"] >= threshold_km2]
    return unary_union(selected) if selected else Polygon()


def grade_forecasts(forecasts, sparse, medium, params, event_id, threshold_km2):
    sparse_m, medium_m = physical(sparse), physical(medium)
    rows = []
    for source_number, feature in enumerate(forecasts.itertuples(), start=1):
        forecast_m = physical(feature.geometry)
        truth_m = sparse_m if feature.coverage == 3 else medium_m
        fraction = (forecast_m.intersection(truth_m).area / forecast_m.area
                    if forecast_m.area else 0.0)
        rows.append({
            "event_id": event_id,
            "source_feature_number": source_number,
            "feat_type": feature.feat_type,
            "coverage_code": int(feature.coverage),
            "threshold_km2": threshold_km2,
            "verification_fraction": fraction,
            "category": category(fraction, params),
            "artifact_method": ARTIFACT_METHOD,
        })
    return rows


def miss_rows(sparse, forecasts, params, event_id, threshold_km2):
    forecast_union_m = physical(forecasts.union_all())
    rows = []
    for component_id, geometry in enumerate(parts(sparse), start=1):
        geometry_m = physical(geometry)
        captured = (geometry_m.intersection(forecast_union_m).area / geometry_m.area
                    if geometry_m.area else 0.0)
        if captured < params.miss_capture_threshold:
            rows.append({
                "event_id": event_id,
                "threshold_km2": threshold_km2,
                "truth_component": component_id,
                "captured_fraction": captured,
                "area_km2": geometry_m.area / 1e6,
                "bounds": [round(value, 6) for value in geometry.bounds],
            })
    return rows


def distribution(values):
    array = np.asarray(values, dtype=float)
    bands = {
        "lt_5000": int(np.sum(array < 5_000)),
        "5000_to_10000": int(np.sum((array >= 5_000) & (array < 10_000))),
        "10000_to_15000": int(np.sum((array >= 10_000) & (array < 15_000))),
        "15000_to_20000": int(np.sum((array >= 15_000) & (array < 20_000))),
        "20000_to_25000": int(np.sum((array >= 20_000) & (array < 25_000))),
        "ge_25000": int(np.sum(array >= 25_000)),
    }
    return {
        "count": len(array), "minimum": float(np.min(array)),
        "p10": float(np.percentile(array, 10)),
        "p25": float(np.percentile(array, 25)),
        "median": float(np.median(array)),
        "p75": float(np.percentile(array, 75)),
        "p90": float(np.percentile(array, 90)),
        "maximum": float(np.max(array)), "bands": bands,
    }


def component_areas_cells(mask, cell_area_km2):
    labels, count = label(mask, structure=np.array([[0, 1, 0],
                                                    [1, 1, 1],
                                                    [0, 1, 0]]))
    return sorted([float(np.sum(labels == index) * cell_area_km2)
                   for index in range(1, count + 1)], reverse=True)


def process_synthetic(seed, cell_area_km2=25.0):
    params = tcf_pipeline.GradingParams()
    dilated = binary_dilation(seed, iterations=params.dilation_iterations)
    field = uniform_filter(dilated.astype(float), size=params.smoothing_size)
    sparse = field >= params.sparse_truth_threshold
    medium = field >= params.medium_truth_threshold
    return {
        "raw_area_km2": float(np.sum(seed) * cell_area_km2),
        "dilated_area_km2": float(np.sum(dilated) * cell_area_km2),
        "sparse_component_areas_km2": component_areas_cells(sparse, cell_area_km2),
        "medium_component_areas_km2": component_areas_cells(medium, cell_area_km2),
    }


def synthetic_experiments():
    size, center = 301, 150
    yy, xx = np.ogrid[:size, :size]
    output = []
    for radius in (2, 4, 6, 8, 10, 12, 15, 20):
        seed = (xx - center) ** 2 + (yy - center) ** 2 <= radius ** 2
        output.append({"morphology": "compact_circle", "parameter": radius,
                       **process_synthetic(seed)})

    # Similar raw area, unlike morphology.
    compact = (xx - center) ** 2 + (yy - center) ** 2 <= 10 ** 2
    line_seed = np.zeros((size, size), dtype=bool)
    line_seed[center - 1:center + 2, center - 52:center + 53] = True
    thin = np.zeros((size, size), dtype=bool)
    thin[center, center - 157:center + 158] = True
    distant = np.zeros((size, size), dtype=bool)
    for offset in (-80, 80):
        distant[(xx - (center + offset)) ** 2 + (yy - center) ** 2 <= 5 ** 2] = True
    holed = (xx - center) ** 2 + (yy - center) ** 2 <= 20 ** 2
    holed &= (xx - center) ** 2 + (yy - center) ** 2 >= 8 ** 2
    for name, seed in (("compact_reference", compact), ("long_narrow_line", line_seed),
                       ("thin_elongated", thin), ("two_distant_clusters", distant),
                       ("large_with_hole", holed)):
        output.append({"morphology": name, "parameter": None,
                       **process_synthetic(seed)})
    for spacing in (8, 12, 16, 20, 24):
        broken = np.zeros((size, size), dtype=bool)
        for offset in (-spacing, 0, spacing):
            broken[(xx - (center + offset)) ** 2 + (yy - center) ** 2 <= 5 ** 2] = True
        output.append({"morphology": "broken_cluster_spacing_cells",
                       "parameter": spacing, **process_synthetic(broken)})
    output.extend([
        {"morphology": "hard_cutoff", "processed_area_km2": 14_999,
         "retained_at_15000": False},
        {"morphology": "hard_cutoff", "processed_area_km2": 15_001,
         "retained_at_15000": True},
    ])
    return output


def run_audit(baseline_dir=REPO_ROOT / "baseline"):
    params = tcf_pipeline.GradingParams()
    domain = tcf_pipeline.verification_domain()
    components, forecast_rows, misses_by_threshold, stages = [], [], {}, []
    event_data = {}
    for event_dir in sorted(baseline_dir.glob("20*")):
        paths = [event_dir / name for name in ("tcf_raw.txt", "arrays.npz")]
        if not all(path.exists() for path in paths):
            continue
        forecasts = tcf_pipeline.parse_iem_cow_text(paths[0].read_text(encoding="utf-8"))
        with np.load(paths[1]) as arrays:
            tops, refl, lons, lats = [arrays[key] for key in
                                      ("max_tops", "max_refl", "lons", "lats")]
        raw = (refl >= 40.0) & (tops >= 25.0)
        dilated = binary_dilation(raw, iterations=params.dilation_iterations)
        coverage = uniform_filter(dilated.astype(float), size=params.smoothing_size)
        raw_area = physical(tcf_pipeline._mask_cell_union(raw, lons, lats)).area / 1e6
        dilated_area = physical(tcf_pipeline._mask_cell_union(dilated, lons, lats)).area / 1e6
        event_classes = {}
        for truth_class, threshold in (("Sparse", params.sparse_truth_threshold),
                                       ("Medium", params.medium_truth_threshold)):
            rows, geometries = clipped_components(
                coverage >= threshold, lons, lats, domain, event_dir.name, truth_class)
            components.extend(rows)
            event_classes[truth_class] = (rows, geometries)
            stages.append({
                "event_id": event_dir.name, "truth_class": truth_class,
                "raw_core_area_km2": raw_area,
                "dilated_core_area_km2": dilated_area,
                "processed_prefilter_indomain_area_km2": sum(row["area_km2"] for row in rows),
                "processed_component_count": len(rows),
                "artifact_method": ARTIFACT_METHOD,
            })
        event_data[event_dir.name] = (forecasts, event_classes)

    for threshold in THRESHOLDS_KM2:
        misses_by_threshold[str(threshold)] = []
        for event_id, (forecasts, classes) in event_data.items():
            sparse = retained_union(*classes["Sparse"], threshold)
            medium = retained_union(*classes["Medium"], threshold)
            forecast_rows.extend(grade_forecasts(
                forecasts, sparse, medium, params, event_id, threshold))
            misses_by_threshold[str(threshold)].extend(
                miss_rows(sparse, forecasts, params, event_id, threshold))
            # Record nesting in the summary later from these exact unions.
            classes.setdefault("nested", {})[threshold] = (
                physical(medium).difference(physical(sparse)).area / 1e6)
    current = {(row["event_id"], row["source_feature_number"]): row
               for row in forecast_rows if row["threshold_km2"] == 15_000}
    for row in forecast_rows:
        baseline = current[(row["event_id"], row["source_feature_number"])]
        row["current_15000_fraction"] = baseline["verification_fraction"]
        row["fraction_difference_from_15000"] = (
            row["verification_fraction"] - baseline["verification_fraction"])
        row["current_15000_category"] = baseline["category"]
        row["category_changed_from_15000"] = row["category"] != baseline["category"]
    return components, forecast_rows, misses_by_threshold, stages, event_data


def summarize(components, forecasts, misses_by_threshold, stages, event_data):
    current = {(row["event_id"], row["source_feature_number"]): row
               for row in forecasts if row["threshold_km2"] == 15_000}
    sweep = {}
    reference_misses = {
        (row["event_id"], tuple(row["bounds"])): row
        for row in misses_by_threshold["15000"]
    }
    for threshold in THRESHOLDS_KM2:
        threshold_rows = [row for row in forecasts if row["threshold_km2"] == threshold]
        differences = [abs(row["verification_fraction"] -
                           current[(row["event_id"], row["source_feature_number"])]["verification_fraction"])
                       for row in threshold_rows]
        affected = [value for value in differences if value > 1e-12]
        category_changes = sum(
            row["category"] != current[(row["event_id"], row["source_feature_number"])]["category"]
            for row in threshold_rows)
        truth = {}
        for truth_class in ("Sparse", "Medium"):
            values = [row["area_km2"] for row in components
                      if row["truth_class"] == truth_class and row["area_km2"] > 0]
            retained = [value for value in values if value >= threshold]
            truth[truth_class] = {
                "retained_count": len(retained), "removed_count": len(values) - len(retained),
                "retained_area_km2": sum(retained),
                "removed_area_km2": sum(values) - sum(retained),
            }
        event_misses = {event_id: sum(row["event_id"] == event_id
                                      for row in misses_by_threshold[str(threshold)])
                        for event_id in event_data}
        threshold_misses = {(row["event_id"], tuple(row["bounds"])): row
                            for row in misses_by_threshold[str(threshold)]}
        added_misses = [row for key, row in threshold_misses.items()
                        if key not in reference_misses]
        removed_misses = [row for key, row in reference_misses.items()
                          if key not in threshold_misses]
        sweep[str(threshold)] = {
            "truth": truth,
            "forecasts_with_fraction_change": len(affected),
            "largest_absolute_fraction_change": max(affected, default=0.0),
            "median_absolute_change_among_affected": (
                float(np.median(affected)) if affected else 0.0),
            "category_changes": category_changes,
            "crossed_20_percent": sum(
                (row["verification_fraction"] >= .2) !=
                (current[(row["event_id"], row["source_feature_number"])]["verification_fraction"] >= .2)
                for row in threshold_rows),
            "crossed_50_percent": sum(
                (row["verification_fraction"] >= .5) !=
                (current[(row["event_id"], row["source_feature_number"])]["verification_fraction"] >= .5)
                for row in threshold_rows),
            "total_misses": len(misses_by_threshold[str(threshold)]),
            "misses_per_event": event_misses,
            "added_misses_relative_15000": len(added_misses),
            "removed_misses_relative_15000": len(removed_misses),
            "added_miss_components": added_misses,
            "removed_miss_components": removed_misses,
            "nested_truth_violations": sum(
                classes["nested"][threshold] > 1e-3
                for _, classes in event_data.values()),
        }
    distributions = {
        truth_class: distribution([row["area_km2"] for row in components
                                   if row["truth_class"] == truth_class and row["area_km2"] > 0])
        for truth_class in ("Sparse", "Medium")
    }
    sensitive = [row for row in forecasts if row["threshold_km2"] != 15_000 and
                 abs(row["verification_fraction"] -
                     current[(row["event_id"], row["source_feature_number"])]["verification_fraction"]) > 1e-12]
    load_bearing = []
    params = tcf_pipeline.GradingParams()
    for event_id, (event_forecasts, classes) in event_data.items():
        forecast_union_m = physical(event_forecasts.union_all())
        for truth_class in ("Sparse", "Medium"):
            component_rows, geometries = classes[truth_class]
            for component in component_rows:
                area = component["area_km2"]
                if not (0 < area <= max(THRESHOLDS_KM2)):
                    continue
                geometry = geometries[component["component_id"]]
                geometry_m = physical(geometry)
                affected_forecasts = []
                for source_number, feature in enumerate(event_forecasts.itertuples(), start=1):
                    uses_class = ((feature.coverage == 3 and truth_class == "Sparse") or
                                  (feature.coverage != 3 and truth_class == "Medium"))
                    if not uses_class:
                        continue
                    feature_m = physical(feature.geometry)
                    contribution = (feature_m.intersection(geometry_m).area / feature_m.area
                                    if feature_m.area else 0.0)
                    if contribution > 1e-12:
                        affected_forecasts.append({
                            "source_feature_number": source_number,
                            "feat_type": feature.feat_type,
                            "coverage_code": int(feature.coverage),
                            "isolated_fraction_contribution": contribution,
                        })
                captured = (geometry_m.intersection(forecast_union_m).area / geometry_m.area
                            if geometry_m.area else 0.0)
                would_be_miss = (truth_class == "Sparse" and
                                 captured < params.miss_capture_threshold)
                if affected_forecasts or would_be_miss:
                    load_bearing.append({
                        "event_id": event_id, "truth_class": truth_class,
                        "component_id": component["component_id"], "area_km2": area,
                        "domain_status": component["domain_status"],
                        "centroid": [component["centroid_lon"], component["centroid_lat"]],
                        "bounds": [component["min_lon"], component["min_lat"],
                                   component["max_lon"], component["max_lat"]],
                        "affected_forecasts": affected_forecasts,
                        "captured_fraction": captured,
                        "would_be_miss_if_retained": would_be_miss,
                    })
    return {
        "artifact_method": ARTIFACT_METHOD,
        "thresholds_km2": list(THRESHOLDS_KM2),
        "component_distributions": distributions,
        "sweep": sweep,
        "sensitive_forecast_rows": sensitive,
        "load_bearing_components_up_to_30000_km2": load_bearing,
        "misses": misses_by_threshold,
        "stage_areas": stages,
        "synthetic_experiments": synthetic_experiments(),
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
    components, forecasts, misses_by_threshold, stages, event_data = run_audit()
    if args.components:
        write_csv(args.components, components)
    if args.forecasts:
        write_csv(args.forecasts, forecasts)
    payload = json.dumps(summarize(
        components, forecasts, misses_by_threshold, stages, event_data), indent=2) + "\n"
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
