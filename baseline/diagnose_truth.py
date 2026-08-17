#!/usr/bin/env python3
"""Read-only diagnostic: why each truth blob survived, and how each forecast scored.

Replays one frozen event offline through tcf_pipeline with default GradingParams
and prints the intermediate stages the pipeline does not expose. It reads and
prints; it writes nothing except its own report under scratch/.

    python baseline/diagnose_truth.py [EVENT_ID]

Defaults to 20260403_21Z_F04. Works on any captured event without modification.

PART 1 shows the connected components of each thresholded truth field BEFORE the
min_area_m2 filter, so the blobs the filter deletes are visible with their size,
fill and echo tops.

PART 2 traces every forecast polygon from raw radar cells through the smoothed
field to its final grade, and prints what it WOULD have scored against the other
truth thresholds.

Two things worth knowing about the numbers:

* The truth-field stages (dilate -> smooth -> threshold) are recomputed here
  because run_verification builds them internally and returns only the finished
  polygons. Every constant comes from GradingParams and from tcf_pipeline itself,
  so this cannot drift from the pipeline silently -- but it IS a second
  implementation of those three lines, and that is the one thing to re-check if
  the pipeline changes.
* The grade, coverage_fraction and category in PART 2 are NOT recomputed. They
  come straight out of run_verification, so they are the real graded values.
"""

import argparse
import os
import sys

import geopandas as gpd
import numpy as np
from matplotlib.path import Path as MplPath
from scipy.ndimage import binary_dilation, label, uniform_filter
from shapely.geometry import Polygon
from skimage import measure

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(1, os.path.join(REPO_ROOT, "baseline"))

import tcf_pipeline  # noqa: E402
from tcf_pipeline import GradingParams  # noqa: E402
import capture  # noqa: E402

BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")
SCRATCH_DIR = os.path.join(REPO_ROOT, "scratch")
DEFAULT_EVENT = "20260403_21Z_F04"

AREA_CRS = "EPSG:5070"     # what extract_tcf_polygons measures the area filter in
KM2 = 1e6                  # m^2 per km^2

# The reflectivity floor and echo-top floor that define a convective core. Not
# GradingParams fields (deliberately, they are being corrected separately), so
# they are restated here to keep raw_cores identical to the pipeline's.
CORE_DBZ = 40
CORE_TOP_KFT = 25


class Tee:
    """Write to stdout and to a file at once."""

    def __init__(self, path):
        self.fh = open(path, "w", encoding="utf-8")

    def __call__(self, line=""):
        print(line)
        self.fh.write(line + "\n")

    def close(self):
        self.fh.close()


# --- loading ----------------------------------------------------------------
def load_event(event_id):
    event = next((e for e in capture.EVENTS if e["event_id"] == event_id), None)
    if event is None:
        known = ", ".join(e["event_id"] for e in capture.EVENTS)
        raise SystemExit(f"unknown event {event_id!r}. Known: {known}")

    ev_dir = os.path.join(BASELINE_DIR, event_id)
    if not os.path.isdir(ev_dir):
        raise SystemExit(f"no captured baseline at {ev_dir}")

    with open(os.path.join(ev_dir, "tcf_raw.txt"), encoding="utf-8") as f:
        raw_text = f.read()
    with np.load(os.path.join(ev_dir, "arrays.npz")) as npz:
        arrays = {k: npz[k] for k in ("max_tops", "max_refl", "lons", "lats")}
    return event, raw_text, arrays


def cell_area_km2(lons, lats):
    """Per-row physical cell area, km^2. Rows share a latitude so one value each."""
    dlon = abs(float(np.diff(lons)[0]))
    dlat = abs(float(np.diff(lats)[0]))
    km_per_deg = 111.32
    return (km_per_deg * dlat) * (km_per_deg * dlon * np.cos(np.radians(lats)))


# --- truth field ------------------------------------------------------------
def smoothed_field(arrays, params):
    """The pipeline's coverage_fraction, recomputed. See the module docstring."""
    raw_cores = ((arrays["max_refl"] >= CORE_DBZ) & (arrays["max_tops"] >= CORE_TOP_KFT))
    buffered = binary_dilation(raw_cores, iterations=params.dilation_iterations)
    return raw_cores, uniform_filter(buffered.astype(float), size=params.smoothing_size)


def component_polygon_areas_m2(component_mask, lons, lats):
    """Areas the min_area_m2 filter would actually test for one component.

    Mirrors extract_tcf_polygons: contours with >10 points, simplify(0.05),
    reprojected to EPSG:5070. The filter compares each contour polygon
    individually, so this returns a list rather than a total.
    """
    polys = []
    for contour in measure.find_contours(component_mask.astype(int), 0.5):
        if len(contour) > 10:
            poly = Polygon(zip([lons[int(p[1])] for p in contour],
                               [lats[int(p[0])] for p in contour]))
            if poly.is_valid:
                polys.append(poly.simplify(0.05))
    if not polys:
        return []
    gdf = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326").to_crs(AREA_CRS)
    return [float(a) for a in gdf.geometry.area]


def census(out, label_name, threshold, coverage_codes, field, arrays, params):
    """PART 1 for one threshold."""
    lons, lats = arrays["lons"], arrays["lats"]
    mask = field >= threshold
    labelled, n = label(mask)
    row_area = cell_area_km2(lons, lats)

    rows = []
    for cid in range(1, n + 1):
        comp = labelled == cid
        rr, cc = np.nonzero(comp)
        raster_km2 = float(row_area[rr].sum())

        poly_areas = component_polygon_areas_m2(comp, lons, lats)
        filter_km2 = max(poly_areas) / KM2 if poly_areas else 0.0
        survived = any(a >= params.min_area_m2 for a in poly_areas)

        vals = field[comp]
        tops_in = arrays["max_tops"][comp]
        rows.append({
            "lat": float(lats[rr].mean()), "lon": float(lons[cc].mean()),
            "raster_km2": raster_km2, "filter_km2": filter_km2,
            "peak": float(vals.max()), "mean": float(vals.mean()),
            "max_top": float(tops_in.max()) if tops_in.size else 0.0,
            "survived": survived, "cells": int(comp.sum()),
            "n_polys": len(poly_areas),
        })

    deleted = sorted([r for r in rows if not r["survived"]],
                     key=lambda r: -r["filter_km2"])
    kept = sorted([r for r in rows if r["survived"]], key=lambda r: -r["filter_km2"])

    out()
    out(f"--- {label_name}: threshold {threshold:.2f}  "
        f"(coverage code{'s' if len(coverage_codes) > 1 else ''} "
        f"{', '.join(str(c) for c in coverage_codes)}) ---")
    out(f"{'':4} {'centroid':>18} {'area km2':>10} {'filter km2':>11} "
        f"{'peak fill':>10} {'mean fill':>10} {'max top kft':>12}  verdict")
    for r in deleted + kept:
        out(f"{'':4} {r['lat']:8.4f},{r['lon']:9.4f} {r['raster_km2']:10.1f} "
            f"{r['filter_km2']:11.1f} {r['peak']:10.4f} {r['mean']:10.4f} "
            f"{r['max_top']:12.2f}  "
            f"{'SURVIVED' if r['survived'] else 'DELETED'}"
            + ("" if r["n_polys"] else "  (no contour >10 pts)"))
    deleted_area = sum(r["filter_km2"] for r in deleted)
    out(f"{'':4} summary: {n} components, {len(kept)} survived, "
        f"{deleted_area:.1f} km2 deleted "
        f"(floor {params.min_area_m2 / KM2:,.0f} km2)")
    return rows


# --- forecast trace ---------------------------------------------------------
def polygon_cell_mask(poly, lons, lats):
    """The pipeline's own point-in-polygon test: bbox subset + exterior Path."""
    min_lon, min_lat, max_lon, max_lat = poly.bounds
    lat_mask = (lats >= min_lat) & (lats <= max_lat)
    lon_mask = (lons >= min_lon) & (lons <= max_lon)
    if not lat_mask.any() or not lon_mask.any():
        return lat_mask, lon_mask, None
    lon_grid, lat_grid = np.meshgrid(lons[lon_mask], lats[lat_mask])
    inside = MplPath(np.array(poly.exterior.coords)).contains_points(
        np.vstack((lon_grid.flatten(), lat_grid.flatten())).T).reshape(lon_grid.shape)
    return lat_mask, lon_mask, inside


def area_km2(geom):
    return float(gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(AREA_CRS).area.iloc[0]) / KM2


def trace(out, results, arrays, field, raw_cores, params, thresholds):
    """PART 2."""
    lons, lats = arrays["lons"], arrays["lats"]
    unions = {}
    for name, thr, _codes in thresholds:
        gdf = tcf_pipeline.extract_tcf_polygons(
            (field >= thr).astype(int), lons, lats, min_area_m2=params.min_area_m2)
        unions[name] = gdf.union_all() if not gdf.is_empty.all() else Polygon()

    # Which threshold entry a coverage code selects, read off the same table the
    # census used, so the two parts cannot disagree about the mapping.
    code_to_name = {c: name for name, _thr, codes in thresholds for c in codes}
    by_name = {name: thr for name, thr, _codes in thresholds}

    for r in results["graded_forecasts"]:
        poly = r["geometry"]
        code = int(r["coverage"])
        label_txt = ("Line" if r["feat_type"] == "LINE" else "Area") + f" {r['idx']}"
        own = code_to_name.get(code)
        if own is None:
            out()
            out(f"  {label_txt}  coverage code {code} maps to no truth threshold "
                f"-- skipped")
            continue
        own_thr = by_name[own]

        a_km2 = area_km2(poly)
        perim_km = float(gpd.GeoSeries([poly], crs="EPSG:4326")
                         .to_crs(AREA_CRS).length.iloc[0]) / 1000.0
        width_km = 4 * a_km2 / perim_km if perim_km else float("nan")

        lat_mask, lon_mask, inside = polygon_cell_mask(poly, lons, lats)
        if inside is None or not inside.any():
            out()
            out(f"  {label_txt}  (coverage code {code} = "
                f"{tcf_pipeline._coverage_label(code)})")
            out("    no grid cells fall inside this polygon")
            continue

        sub_refl = arrays["max_refl"][lat_mask][:, lon_mask][inside]
        sub_cores = raw_cores[lat_mask][:, lon_mask][inside]
        sub_field = field[lat_mask][:, lon_mask][inside]

        out()
        out(f"  {label_txt}  (coverage code {code} = "
            f"{tcf_pipeline._coverage_label(code)} -> {own} truth @ {own_thr:.2f})")
        out(f"    area                {a_km2:12,.1f} km2")
        out(f"    mean width 4A/P     {width_km:12.1f} km   (perimeter {perim_km:,.1f} km)")
        out(f"    cells inside        {int(inside.sum()):12,d}")
        out("    RAW fill (pre-smoothing)")
        out(f"      >= {CORE_DBZ} dBZ         {float((sub_refl >= CORE_DBZ).mean()):12.4f}")
        out(f"      raw_cores         {float(sub_cores.mean()):12.4f}   "
            f"(>= {CORE_DBZ} dBZ AND >= {CORE_TOP_KFT} kft, what the pipeline dilates)")
        out("    smoothed field inside")
        out(f"      max               {float(sub_field.max()):12.4f}")
        out(f"      mean              {float(sub_field.mean()):12.4f}")
        out(f"      frac >= {own_thr:.2f}      "
            f"{float((sub_field >= own_thr).mean()):12.4f}")
        out(f"    GRADED              coverage_fraction "
            f"{r['coverage_fraction']:.4f} -> {r['category']}")

        # Counterfactual: same polygon, same frozen field, other thresholds.
        # Nothing is mutated; GradingParams is untouched.
        fcst_area = poly.area
        cf = []
        for name, thr, _codes in thresholds:
            if name == own:
                continue
            hit = poly.intersection(unions[name]).area
            frac = hit / fcst_area if fcst_area > 0 else 0
            cat = ("Verified Well" if frac >= params.verified_well_cutoff else
                   "Verified Close" if frac >= params.verified_close_cutoff else
                   "Overforecasted")
            cf.append(f"{name} @ {thr:.2f} -> {frac:.4f} {cat}")
        out("    COUNTERFACTUAL (print only, no parameter changed): " + "; ".join(cf))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("event_id", nargs="?", default=DEFAULT_EVENT)
    args = ap.parse_args()

    # Load before opening the report, so a bad event id does not leave an empty
    # diagnose_<whatever>.txt lying around in scratch/.
    event, raw_text, arrays = load_event(args.event_id)

    os.makedirs(SCRATCH_DIR, exist_ok=True)
    out = Tee(os.path.join(SCRATCH_DIR, f"diagnose_{args.event_id}.txt"))
    try:
        params = GradingParams()
        gdf_artcc = tcf_pipeline.load_artccs()
        gdf_forecast = tcf_pipeline.parse_iem_cow_text(raw_text)
        valid_dt = tcf_pipeline.compute_valid_dt(
            event["date"], event["issuance_hour"], event["lead_time"])
        results = tcf_pipeline.run_verification(
            gdf_forecast, arrays["max_tops"], arrays["max_refl"],
            arrays["lons"], arrays["lats"],
            valid_dt, event["issuance_hour"], event["lead_time"], gdf_artcc,
            params=params)

        raw_cores, field = smoothed_field(arrays, params)

        out(f"{'=' * 100}")
        out(f"TRUTH DIAGNOSTIC  {args.event_id}")
        out(f"{'=' * 100}")
        out(f"valid time        {valid_dt:%Y-%m-%d %H:%MZ}  "
            f"(issuance {event['issuance_hour']:02d}Z, lead {event['lead_time']}h)")
        out(f"grid              {arrays['max_refl'].shape[0]} x "
            f"{arrays['max_refl'].shape[1]} cells at "
            f"{abs(float(np.diff(arrays['lons'])[0])):.2f} deg")
        out(f"composite         {len(tcf_pipeline.scan_offsets())} scans @ "
            f"{tcf_pipeline.COMPOSITE_CADENCE_MINUTES}-min over "
            f"+/-{tcf_pipeline.COMPOSITE_WINDOW_MINUTES} min")
        out(f"core definition   >= {CORE_DBZ} dBZ AND >= {CORE_TOP_KFT} kft")
        out(f"dilation          {params.dilation_iterations} iteration(s)")
        out(f"smoothing         uniform_filter size {params.smoothing_size}")
        out(f"area floor        {params.min_area_m2 / KM2:,.0f} km2")
        out(f"grade cutoffs     well >= {params.verified_well_cutoff:.2f}, "
            f"close >= {params.verified_close_cutoff:.2f}")
        out(f"raw core cells    {int(raw_cores.sum()):,d} of {raw_cores.size:,d} "
            f"({raw_cores.mean() * 100:.3f}%)")
        out(f"forecast features {len(results['graded_forecasts'])} graded, "
            f"{len(results['graded_misses'])} misses")

        # Sparse uses its own threshold; Medium and Dense both select
        # medium_truth_threshold in run_verification, so they are ONE field, not
        # two. Printing it twice would imply a third field exists.
        thresholds = [("sparse", params.sparse_truth_threshold, [3]),
                      ("medium/dense", params.medium_truth_threshold, [2, 1])]

        out()
        out(f"{'=' * 100}")
        out("PART 1  deleted-blob census (components of the thresholded field, "
            "before the area filter)")
        out(f"{'=' * 100}")
        if params.medium_truth_threshold == params.sparse_truth_threshold:
            out("  NOTE: sparse and medium/dense thresholds are equal, so all three "
                "coverage codes share one field.")
        else:
            out("  NOTE: Medium (code 2) and Dense (code 1) both select "
                f"medium_truth_threshold = {params.medium_truth_threshold:.2f} in "
                "run_verification,")
            out("        so there are TWO distinct truth fields here, not three. "
                "Dense has no separate threshold.")
        out("  'area km2' is the summed physical cell area; 'filter km2' is the "
            "largest contour-polygon")
        out("  area in EPSG:5070, which is the number min_area_m2 actually tests.")
        for name, thr, codes in thresholds:
            census(out, name, thr, codes, field, arrays, params)

        out()
        out(f"{'=' * 100}")
        out("PART 2  per-forecast-polygon trace")
        out(f"{'=' * 100}")
        trace(out, results, arrays, field, raw_cores, params, thresholds)

        out()
        out(f"report written to scratch/diagnose_{args.event_id}.txt")
    finally:
        out.close()


if __name__ == "__main__":
    main()
