#!/usr/bin/env python3
"""Read-only characterization of the sole frozen Solid LINE forecast.

The frozen arrays predate Decision 1A, so every observational result produced by
this utility is explicitly a legacy independent-max characterization.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
from pyproj import Geod
from scipy.ndimage import binary_dilation, uniform_filter
from shapely.geometry import LineString

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline  # noqa: E402

EVENT_ID = "20260403_21Z_F04"
HISTORICAL_MIN_AREA_M2 = 15_000_000_000
BUFFER_WIDTHS_DEG = (0.03, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00)
DISTANCES_NM = (0, 10, 20, 30, 40, 50, 75, 100)


def source_line(raw_text):
    text = re.sub(r"<[^>]+>", " ", raw_text)
    blocks = [b.split() for kind, b in re.findall(
        r"(AREA|LINE)\s+([\d\s]+)", text) if kind == "LINE"]
    if len(blocks) != 1:
        raise ValueError(f"expected one LINE record, found {len(blocks)}")
    parts = blocks[0]
    if int(parts[0]) != 1:
        raise ValueError(f"expected Solid LINE code 1, found {parts[0]}")
    count = int(parts[1])
    values = parts[2:2 + count * 2]
    coordinates = [(-int(values[i + 1]) / 10, int(values[i]) / 10)
                   for i in range(0, len(values), 2)]
    return LineString(coordinates)


def physical(geometry):
    return gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
        tcf_pipeline.PHYSICAL_AREA_CRS).iloc[0]


def analyze():
    event_dir = REPO_ROOT / "baseline" / EVENT_ID
    raw_text = (event_dir / "tcf_raw.txt").read_text(encoding="utf-8")
    metadata = json.loads((event_dir / "expected.json").read_text(encoding="utf-8"))
    forecast = tcf_pipeline.parse_iem_cow_text(raw_text)
    line = source_line(raw_text)

    with np.load(event_dir / "arrays.npz") as arrays:
        tops, refl, lons, lats = [arrays[key] for key in
                                  ("max_tops", "max_refl", "lons", "lats")]

    params = tcf_pipeline.GradingParams()
    legacy_seed = (refl >= 40.0) & (tops >= 25.0)
    coverage = uniform_filter(binary_dilation(
        legacy_seed, iterations=params.dilation_iterations).astype(float),
        size=params.smoothing_size)
    domain = tcf_pipeline.verification_domain()
    sparse = tcf_pipeline.extract_tcf_polygons(
        (coverage >= params.sparse_truth_threshold).astype(int), lons, lats,
        min_area_m2=HISTORICAL_MIN_AREA_M2, domain=domain)
    medium = tcf_pipeline.extract_tcf_polygons(
        (coverage >= params.medium_truth_threshold).astype(int), lons, lats,
        min_area_m2=HISTORICAL_MIN_AREA_M2, domain=domain)
    sparse_union = sparse.union_all()
    medium_union = medium.union_all()
    line_m, sparse_m, medium_m = map(physical,
                                    (line, sparse_union, medium_union))

    artcc = tcf_pipeline.load_artccs()
    valid_dt = datetime.fromisoformat(metadata["valid_dt"])
    result = tcf_pipeline.run_verification_legacy_independent_max(
        forecast, tops, refl, lons, lats, valid_dt,
        metadata["issuance_hour"], metadata["lead_time"], artcc)
    line_result = next(item for item in result["graded_forecasts"]
                       if item["feat_type"] == "LINE")
    no_line_result = tcf_pipeline.run_verification_legacy_independent_max(
        forecast[forecast.feat_type != "LINE"].copy(), tops, refl, lons, lats,
        valid_dt, metadata["issuance_hour"], metadata["lead_time"], artcc)

    geod = Geod(ellps="WGS84")
    geodesic_length_m = sum(
        geod.inv(*start, *end)[2]
        for start, end in zip(line.coords, list(line.coords)[1:]))

    buffer_sensitivity = []
    for width in BUFFER_WIDTHS_DEG:
        corridor_m = physical(line.buffer(width))
        buffer_sensitivity.append({
            "half_width_degrees": width,
            "corridor_area_km2": corridor_m.area / 1e6,
            "medium_truth_area_overlap_fraction": (
                corridor_m.intersection(medium_m).area / corridor_m.area),
        })

    length_sensitivity = []
    for distance_nm in DISTANCES_NM:
        nearby_truth = medium_m.buffer(distance_nm * 1852)
        length_sensitivity.append({
            "analysis_distance_nm": distance_nm,
            "line_length_fraction_near_medium_truth": (
                line_m.intersection(nearby_truth).length / line_m.length),
        })

    payload = {
        "artifact_method": "legacy independent-max; Decision 1A mask unavailable",
        "event_id": EVENT_ID,
        "source_feature_number": 7,
        "report_feature_number": int(line_result["idx"]),
        "issuance_hour": metadata["issuance_hour"],
        "valid_time": metadata["valid_dt"],
        "centerline_coordinates": [list(point) for point in line.coords],
        "centerline_vertices": len(line.coords),
        "centerline_length_nm": geodesic_length_m / 1852,
        "centerline_bounds": list(line.bounds),
        "production_buffer_half_width_degrees": tcf_pipeline.LINE_BUFFER_DEG,
        "production_corridor_bounds": list(line_result["geometry"].bounds),
        "production_corridor_area_km2": physical(line_result["geometry"]).area / 1e6,
        "current_fraction": float(line_result["coverage_fraction"]),
        "current_category": line_result["category"],
        "echo_top_kft": float(line_result["top"]),
        "medium_truth_count": len(medium),
        "medium_truth_area_km2": medium_m.area / 1e6,
        "medium_truth_bounds": [list(item.bounds) for item in medium.geometry],
        "distance_centerline_to_medium_truth_km": line_m.distance(medium_m) / 1000,
        "sparse_truth_count": len(sparse),
        "sparse_truth_area_km2": sparse_m.area / 1e6,
        "centerline_fraction_intersecting_sparse_truth": (
            line_m.intersection(sparse_m).length / line_m.length),
        "miss_count_with_line": len(result["graded_misses"]),
        "miss_count_without_line": len(no_line_result["graded_misses"]),
        "buffer_sensitivity": buffer_sensitivity,
        "medium_truth_distance_sensitivity": length_sensitivity,
    }
    return payload, line, sparse, medium


def plot_case(path, line, sparse, medium):
    figure, axis = plt.subplots(figsize=(9, 6))
    if not sparse.empty:
        sparse.plot(ax=axis, facecolor="#66c2a5", alpha=0.25,
                    edgecolor="#238b45", label="Sparse legacy truth")
    if not medium.empty:
        medium.plot(ax=axis, facecolor="#fc8d62", alpha=0.35,
                    edgecolor="#d7301f", label="Medium legacy truth")
    gpd.GeoSeries([line.buffer(tcf_pipeline.LINE_BUFFER_DEG)], crs="EPSG:4326").plot(
        ax=axis, facecolor="#3288bd", alpha=0.25, edgecolor="#2166ac",
        label="Current 0.15° corridor")
    x, y = line.xy
    axis.plot(x, y, color="black", linewidth=2, marker="o",
              label="Issued centerline")
    axis.set_xlim(-101, -80)
    axis.set_ylim(33, 46)
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.set_title(f"{EVENT_ID}: legacy-max truth (not Decision 1A)")
    axis.legend(handles=[
        Line2D([0], [0], color="black", marker="o", label="Issued centerline"),
        Patch(facecolor="#3288bd", alpha=0.25, edgecolor="#2166ac",
              label="Current 0.15° corridor"),
        Patch(facecolor="#66c2a5", alpha=0.25, edgecolor="#238b45",
              label="Sparse legacy truth"),
        Patch(facecolor="#fc8d62", alpha=0.35, edgecolor="#d7301f",
              label="Medium legacy truth"),
    ], loc="upper left")
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    parser.add_argument("--plot", type=Path)
    args = parser.parse_args(argv)
    payload, line, sparse, medium = analyze()
    text = json.dumps(payload, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.plot:
        args.plot.parent.mkdir(parents=True, exist_ok=True)
        plot_case(args.plot, line, sparse, medium)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
