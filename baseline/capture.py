#!/usr/bin/env python3
"""Baseline capture for the TCF verification pipeline.

This is a plain-script replication of the pipeline that currently lives inside
the ``if st.sidebar.button("Run Verification"):`` block of ``app.py`` (lines
~465-606), with every Streamlit call removed. Where app.py wrote a status
message we either stay silent or print to stderr; where app.py called
``st.stop()`` / ``st.sidebar.error()`` we raise instead.

Purpose: freeze the CURRENT numerical behaviour to disk so a later refactor can
be proven byte-for-byte equivalent. Nothing here is meant to be an improvement
on app.py -- known bugs are deliberately preserved and listed in the BUG
INVENTORY comment block at the bottom of this file.

For each event in EVENTS, writes ``baseline/<event_id>/``:

    arrays.npz    max_tops, max_refl, lons, lats   (the rolling MRMS composite)
    tcf_raw.txt   the raw IEM response text for the TCF product
    expected.json the graded output (report text, per-polygon grades, counts)

One more file belongs in each event directory but is NOT written here:

    pass_a_report.txt  the report text hand-captured from the live Streamlit
                       app for this event ("pass A"). It is copied out of the
                       running dashboard by a human, so this script must never
                       generate it -- a machine-written copy would just be
                       expected.json's report_text under another name and would
                       prove nothing. `check.py --pass-a` requires it to match
                       expected.json's report_text byte for byte, after trailing
                       whitespace is trimmed from the end of both.

Usage:
    python baseline/capture.py                 # capture every event in EVENTS
    python baseline/capture.py 20260524_19z_f04  # capture just these event ids

Requires network access (IEM archives + the public noaa-mrms-pds S3 bucket).
``baseline/check.py`` replays what this writes and needs no network at all.
"""

import gc
import gzip
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, time, timedelta

import geopandas as gpd
import numpy as np
from matplotlib.path import Path
from scipy.ndimage import binary_dilation, uniform_filter
from shapely.geometry import LineString, Polygon
from skimage import measure

# requests / boto3 / xarray are imported lazily inside the two network-bound
# functions below. Nothing about the math changes; it just means check.py can
# import this module (for its Streamlit-free copies of the pure helpers) on a
# machine with no AWS SDK and no cfgrib/ecCodes stack installed.

# --- Event configuration ----------------------------------------------------
# Each entry mirrors one set of sidebar selections in app.py:
#   date            -> st.sidebar.date_input("Select Event Date")
#   issuance_hour   -> st.sidebar.selectbox("Issuance Time (Z)")  [5,7,...,23]
#   lead_time       -> st.sidebar.radio("Forecast Hour")          [4, 6, 8]
#
# `note` is documentation only -- it records why each event is in the set, so a
# later reader can tell which code path a given baseline is meant to pin down.
EVENTS = [
    {"event_id": "20260524_19Z_F04", "date": date(2026, 5, 24), "issuance_hour": 19, "lead_time": 4,
     "note": "primary dev case"},
    {"event_id": "20260524_19Z_F06", "date": date(2026, 5, 24), "issuance_hour": 19, "lead_time": 6,
     "note": "lead plumbing (CFP03)"},
    {"event_id": "20260524_13Z_F04", "date": date(2026, 5, 24), "issuance_hour": 13, "lead_time": 4,
     "note": "issuance plumbing"},
    {"event_id": "20260728_19Z_F04", "date": date(2026, 7, 28), "issuance_hour": 19, "lead_time": 4,
     "note": "external anchor"},
    # 21Z + 4 = 01Z the NEXT day. compute_valid_dt() rolls the date forward, and
    # download_mrms_scan() builds its S3 prefix from dt_obj (= valid_dt + offset),
    # so this event's MRMS keys come from CONUS/<product>/20260404/, not 20260403.
    {"event_id": "20260403_21Z_F04", "date": date(2026, 4, 3), "issuance_hour": 21, "lead_time": 4,
     "note": "LINE features + UTC day rollover"},
    {"event_id": "20260324_05Z_F04", "date": date(2026, 3, 24), "issuance_hour": 5, "lead_time": 4,
     "note": "sparse/empty paths"},
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")
ARTCC_PATH = os.path.join(REPO_ROOT, "artcc1.geojson")


def _log(msg):
    """Stand-in for the st.write / st.status progress chatter in app.py."""
    print(msg, file=sys.stderr, flush=True)


# --- Geography --------------------------------------------------------------
def load_artccs(path=ARTCC_PATH):
    """ARTCC boundaries, read with plain json exactly as load_geography() does.

    app.py's load_geography() also fetches state boundaries over HTTP, but those
    are used only for drawing the map -- no verification number depends on them,
    so they are omitted here to keep this script network-free apart from the
    IEM/MRMS pulls. app.py swallows a read failure and carries on with an empty
    frame (get_artccs then returns "UNKNOWN"); here we raise, because a silently
    empty ARTCC frame would poison the baseline report text.
    """
    with open(path, "r", encoding="utf-8") as f:
        artcc_data = json.load(f)
    return gpd.GeoDataFrame.from_features(artcc_data["features"], crs="EPSG:4326")


# --- Helper functions (verbatim from app.py, minus Streamlit) ----------------
def _coverage_label(cov_val):
    """Map a TCF/CCFP coverage integer code to its plain-English label.
    TCF 1-digit encoding: 1=Dense (75%+), 2=Medium (40-74%), 3=Sparse (25-39%)."""
    if cov_val == 1:
        return "Dense"
    elif cov_val == 2:
        return "Medium"
    return "Sparse"


def parse_iem_cow_text(text_data):
    """Parses legacy NWS/AWIPS AREA/LINE text into a GeoDataFrame, fixing line-wraps with regex."""
    records = []

    # Strip ALL HTML tags so we just have raw text and numbers
    text_data = re.sub(r'<[^>]+>', ' ', text_data)

    # TCF/CCFP format:
    #   AREA: COV(0) CONF(1) GRW(2) TOPS(3) SPEED(4) DIR(5) NPTS(6) lat1 lon1 ...
    #   LINE: COV(0) NPTS(1) lat1 lon1 ...
    # COV is a 1-digit integer: 1=Dense, 2=Medium, 3=Sparse
    feat_blocks = re.findall(r'(AREA|LINE)\s+([\d\s]+)', text_data)

    for feat_type, block in feat_blocks:
        parts = block.split()
        try:
            cov_val = int(parts[0])
            if feat_type == 'LINE':
                # LINE has no CONF/GRW/TOPS/SPEED/DIR fields
                num_points = int(parts[1])
                idx = 2
            else:
                num_points = int(parts[6])
                idx = 7
            coords = []

            for _ in range(num_points):
                if idx + 1 < len(parts):
                    lat = float(parts[idx]) / 10.0
                    lon = float(parts[idx + 1]) / 10.0
                    if lon > 0:
                        lon = -lon
                    coords.append((lon, lat))
                    idx += 2

            if len(coords) >= 3:
                poly = Polygon(coords).buffer(0)
                if not poly.is_empty:
                    records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})
            elif len(coords) >= 2:
                poly = LineString(coords).buffer(0.15)
                records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})

        except Exception:
            continue

    if records:
        return gpd.GeoDataFrame(records, crs="EPSG:4326")
    else:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def fetch_iem_cow_raw(date_obj, issue_hr, f_hr):
    """Scrapes the raw TCF text from IEM archives.

    Same URL construction as app.py's fetch_iem_cow_tcf(); this returns the raw
    response text (so it can be frozen to tcf_raw.txt) instead of a parsed
    GeoDataFrame, and raises on the failure paths where app.py called
    st.sidebar.error() and returned an empty frame.
    """
    date_str = date_obj.strftime("%Y%m%d")
    issue_str = f"{issue_hr:02d}"

    # TCF products are valid at 4/6/8 hrs after issuance.
    if f_hr == 4:
        pil = "CFP02"
    elif f_hr == 6:
        pil = "CFP03"
    elif f_hr == 8:
        pil = "CFP04"
    else:
        pil = "CFP02"

    url = f"https://mesonet.agron.iastate.edu/wx/afos/p.php?pil={pil}&e={date_str}{issue_str}00"

    import requests

    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"IEM returned HTTP {response.status_code} for {url}")
    if "Could not find product" in response.text:
        raise RuntimeError(f"IEM: Data missing for {issue_str}:00Z ({pil})")
    return response.text


def get_artccs(poly, artcc_gdf):
    """Finds which ARTCCs a polygon intersects."""
    if artcc_gdf.empty:
        return "UNKNOWN"
    intersecting = artcc_gdf[artcc_gdf.intersects(poly)]
    if intersecting.empty:
        return "UNKNOWN"

    if 'IDENT' in intersecting.columns:
        centers = intersecting['IDENT'].dropna().unique().tolist()
    else:
        centers = ["UNKNOWN_COL"]
    return "/".join(centers)


def download_mrms_scan(product, dt_obj, dest_dir="mrms_data"):
    import boto3
    import botocore

    os.makedirs(dest_dir, exist_ok=True)
    date_str = dt_obj.strftime('%Y%m%d')
    bucket_name = 'noaa-mrms-pds'
    prefix = f"CONUS/{product}_00.50/{date_str}/"
    s3 = boto3.client('s3', config=botocore.client.Config(signature_version=botocore.UNSIGNED))

    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        if 'Contents' not in response:
            return None

        # Pick the file NEAREST in time to dt_obj, not an exact HHMM match.
        best_key, best_diff = None, None
        for obj in response['Contents']:
            key = obj['Key']
            if not key.endswith('.grib2.gz'):
                continue
            m = re.search(r'(\d{8})-(\d{6})', key.split('/')[-1])
            if not m:
                continue
            file_dt = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
            diff = abs((file_dt - dt_obj).total_seconds())
            if best_diff is None or diff < best_diff:
                best_key, best_diff = key, diff

        # Reject if the closest file is more than 5 min away (a genuine archive gap).
        if best_key is None or best_diff > 5 * 60:
            return None

        local_gz = os.path.join(dest_dir, best_key.split('/')[-1])
        local_grib = local_gz.replace('.gz', '')

        if not os.path.exists(local_grib):
            s3.download_file(bucket_name, best_key, local_gz)
            with gzip.open(local_gz, 'rb') as f_in, open(local_grib, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(local_gz)
        return local_grib
    except Exception:
        return None


def extract_tcf_polygons(coverage_mask, lons, lats, min_area_m2=0):
    """Turns a binary coverage mask into dissolved 'truth' polygons."""
    contours = measure.find_contours(coverage_mask, 0.5)
    polygons = []
    for contour in contours:
        if len(contour) > 10:
            poly = Polygon(zip([lons[int(p[1])] for p in contour],
                               [lats[int(p[0])] for p in contour]))
            if poly.is_valid:
                polygons.append(poly.simplify(0.05))
    gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    if gdf.is_empty.all():
        return gdf

    gdf_m = gdf.to_crs("EPSG:5070")
    if min_area_m2 > 0:
        valid_area = gdf_m.geometry.area >= min_area_m2
        gdf = gdf[valid_area]
    if not gdf.is_empty.all():
        gdf = gpd.GeoDataFrame(geometry=[gdf.union_all()], crs="EPSG:4326")
    return gdf


def build_report(gdf_graded_fcst, gdf_graded_miss, valid_dt, issuance_hour, lead_time, gdf_artcc):
    """Assembles the copy-paste FAA/NWS text report.

    Identical to app.py's build_report(); gdf_artcc is an explicit argument here
    instead of a module-level global left over from load_geography().
    """
    report_text = ""
    doc_report = {"Verified Well:": [], "Verified Close:": [], "Over-forecast:": [], "Missed:": []}

    if not gdf_graded_fcst.empty:
        for _, row in gdf_graded_fcst.iterrows():
            artccs = get_artccs(row.geometry, gdf_artcc)
            top_str = f" [Top: {row.top:.1f} kft]" if row.top > 0 else ""
            cov_label = _coverage_label(getattr(row, 'coverage', 25))
            feat_label = "Line" if getattr(row, 'feat_type', 'AREA') == 'LINE' else "Area"
            line_text = f"{artccs} - {cov_label} ({feat_label} {row.idx}){top_str}"
            if row.category == "Verified Well":
                doc_report["Verified Well:"].append(line_text)
            elif row.category == "Verified Close":
                doc_report["Verified Close:"].append(line_text)
            elif row.category == "Overforecasted":
                doc_report["Over-forecast:"].append(line_text)

    if not gdf_graded_miss.empty:
        for _, row in gdf_graded_miss.iterrows():
            artccs = get_artccs(row.geometry, gdf_artcc)
            doc_report["Missed:"].append(f"{artccs} - Missed (Area M{row.idx})")

    report_text = f"National System Review\nNWS TCF Review\n{valid_dt.strftime('%A, %B %d, %Y')}\n"
    report_text += f"  {valid_dt.strftime('%b %d, %Y')}   IT: {issuance_hour:02d}Z   VT: {valid_dt.strftime('%H')}Z   FCST HR: {lead_time:02d}\n"

    for cat, items in doc_report.items():
        report_text += f"{cat}\n"
        if not items:
            report_text += "None\n"
        for item in items:
            report_text += f"{item}\n"
        report_text += "\n"
    return report_text


# --- Pipeline stages --------------------------------------------------------
def compute_valid_dt(target_date, issuance_hour, lead_time):
    """Valid datetime for an issuance/lead pair (app.py lines 454-459)."""
    valid_time = issuance_hour + lead_time
    if valid_time >= 24:
        valid_time -= 24
        valid_dt = datetime.combine(target_date + timedelta(days=1), time(valid_time, 0))
    else:
        valid_dt = datetime.combine(target_date, time(valid_time, 0))
    return valid_dt


def build_composite(valid_dt):
    """The rolling MRMS composite (app.py lines 476-513). Network-bound.

    Returns (max_tops, max_refl, lons, lats). Raises if no scan in the +/-15 min
    window could be read -- app.py has no such guard and would blow up later
    with a TypeError on None (see BUG 1 at the bottom of this file).
    """
    time_offsets = list(range(-15, 16, 5))
    max_tops, max_refl = None, None
    lons, lats = None, None
    step = 5

    for offset in time_offsets:
        scan_dt = valid_dt + timedelta(minutes=offset)
        _log(f"Pulling MRMS for {scan_dt.strftime('%H:%MZ')}...")
        tops_file = download_mrms_scan("EchoTop_18", scan_dt)
        refl_file = download_mrms_scan("MergedReflectivityQCComposite", scan_dt)

        if tops_file and refl_file:
            # Imported at the point of use, not at module scope: that keeps the
            # composite loop exercisable (and this module importable) without the
            # cfgrib/ecCodes stack installed.
            import xarray as xr

            ds_t = xr.open_dataset(tops_file, engine='cfgrib', backend_kwargs={'indexpath': ''})
            ds_r = xr.open_dataset(refl_file, engine='cfgrib', backend_kwargs={'indexpath': ''})

            curr_tops = ds_t.unknown[::step, ::step].values * 3.28084
            curr_refl = ds_r.unknown[::step, ::step].values

            if lons is None:
                lons = ds_t.longitude[::step].values
                lons = np.where(lons > 180, lons - 360, lons)
                lats = ds_t.latitude[::step].values

            if max_tops is None:
                max_tops, max_refl = curr_tops, curr_refl
            else:
                max_tops = np.maximum(max_tops, curr_tops)
                max_refl = np.maximum(max_refl, curr_refl)

            ds_t.close()
            ds_r.close()
            del ds_t, ds_r, curr_tops, curr_refl
            gc.collect()

    if os.path.exists("mrms_data"):
        shutil.rmtree("mrms_data")

    if max_tops is None:
        raise RuntimeError(f"No MRMS scans available in the +/-15 min window around {valid_dt}")

    return max_tops, max_refl, lons, lats


def run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                     valid_dt, issuance_hour, lead_time, gdf_artcc):
    """The verification math (app.py lines 517-606), pure and Streamlit-free.

    Everything downstream of the MRMS composite lives here: this is the function
    check.py replays against the frozen arrays, so it must stay a pure function
    of its arguments (no network, no globals, no session_state).

    Returns a dict with the same members app.py stashes in st.session_state
    ['results'], plus 'graded_forecasts'/'graded_misses' as plain lists of dicts.

    NOTE: the per-polygon 'coverage_fraction' (the hit/forecast area ratio) is
    carried through in the graded dicts. app.py computes the identical value in
    the local variable `coverage` but only keeps the category it maps to; the
    number itself is recorded here because the baseline grades on it. No math is
    changed by carrying it -- it is the same expression, stored instead of
    dropped.
    """
    valid_convection = (max_refl >= 40)
    top_verif_matrix = np.zeros_like(max_tops, dtype=int)
    top_verif_matrix[valid_convection & (max_tops >= 25) & (max_tops < 30)] = 1
    top_verif_matrix[valid_convection & (max_tops >= 30) & (max_tops < 35)] = 2
    top_verif_matrix[valid_convection & (max_tops >= 35) & (max_tops < 40)] = 3
    top_verif_matrix[valid_convection & (max_tops >= 40)] = 4

    raw_cores = ((max_refl >= 40) & (max_tops >= 25))
    buffered_cores = binary_dilation(raw_cores, iterations=1)
    coverage_fraction = uniform_filter(buffered_cores.astype(float), size=20)

    # 15_000_000_000 m^2 (15,000 km^2) truth-area filter, matching the notebook.
    gdf_sparse = extract_tcf_polygons((coverage_fraction >= 0.25).astype(int), lons, lats,
                                      min_area_m2=15_000_000_000)
    # Medium (cov=2) and Dense (cov=1) forecasts must verify against 40%+ truth,
    # matching the TCF Medium coverage threshold (40-74%).
    gdf_medium_truth = extract_tcf_polygons((coverage_fraction >= 0.40).astype(int), lons, lats,
                                            min_area_m2=15_000_000_000)
    del coverage_fraction, raw_cores, buffered_cores
    gc.collect()

    truth_sparse_union = gdf_sparse.union_all() if not gdf_sparse.is_empty.all() else Polygon()
    truth_medium_union = gdf_medium_truth.union_all() if not gdf_medium_truth.is_empty.all() else Polygon()
    fcst_union = gdf_forecast.union_all() if not gdf_forecast.is_empty.all() else Polygon()

    graded_forecasts, graded_misses = [], []

    fcst_iter = (gdf_forecast.explode(index_parts=False).reset_index(drop=True)
                 if not gdf_forecast.is_empty.all() else gpd.GeoDataFrame(geometry=[]))
    for idx, row in fcst_iter.iterrows():
        poly = row.geometry
        if poly.is_empty:
            continue

        row_cov = row['coverage'] if 'coverage' in fcst_iter.columns else 3
        # Sparse (3) forecasts verify against 25%+ truth; Medium/Dense (1,2) against 40%+ truth.
        truth_union = truth_sparse_union if row_cov == 3 else truth_medium_union

        fcst_area = poly.area
        hit_area = poly.intersection(truth_union).area
        coverage = hit_area / fcst_area if fcst_area > 0 else 0

        min_lon, min_lat, max_lon, max_lat = poly.bounds
        lat_mask, lon_mask = (lats >= min_lat) & (lats <= max_lat), (lons >= min_lon) & (lons <= max_lon)
        subset_tops, subset_refl = max_tops[lat_mask][:, lon_mask], max_refl[lat_mask][:, lon_mask]
        lon_grid, lat_grid = np.meshgrid(lons[lon_mask], lats[lat_mask])

        in_poly_mask = Path(np.array(poly.exterior.coords)).contains_points(
            np.vstack((lon_grid.flatten(), lat_grid.flatten())).T).reshape(lon_grid.shape)
        valid_tops = subset_tops[in_poly_mask & (subset_refl >= 40) & (subset_tops >= 25)]

        actual_top_kft = np.percentile(valid_tops, 90) if len(valid_tops) > 5 else 0

        cat, color = ("Verified Well", 'lime') if coverage >= 0.50 else \
                     ("Verified Close", 'yellow') if coverage >= 0.20 else \
                     ("Overforecasted", 'orange')
        row_feat = row['feat_type'] if 'feat_type' in fcst_iter.columns else 'AREA'
        graded_forecasts.append({'geometry': poly, 'category': cat, 'color': color,
                                 'idx': idx + 1, 'top': actual_top_kft,
                                 'coverage': row_cov, 'feat_type': row_feat,
                                 'coverage_fraction': coverage})

    truth_iter = (gdf_sparse.explode(index_parts=False).reset_index(drop=True)
                  if not gdf_sparse.is_empty.all() else gpd.GeoDataFrame(geometry=[]))
    for idx, row in truth_iter.iterrows():
        poly = row.geometry
        if poly.is_empty:
            continue
        captured = (poly.intersection(fcst_union).area / poly.area) if poly.area > 0 else 0
        if captured < 0.20:
            graded_misses.append({'geometry': poly, 'category': 'Missed', 'color': 'red', 'idx': idx + 1})

    # ORDER EAST -> WEST: east = larger (least-negative) longitude, so sort centroid.x
    # descending. Renumber after sorting so BOTH the map labels and the report read E->W.
    graded_forecasts.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
    for i, r in enumerate(graded_forecasts, start=1):
        r['idx'] = i
    graded_misses.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
    for i, r in enumerate(graded_misses, start=1):
        r['idx'] = i

    gdf_graded_fcst = gpd.GeoDataFrame(graded_forecasts, crs="EPSG:4326") if graded_forecasts else gpd.GeoDataFrame(geometry=[])
    gdf_graded_miss = gpd.GeoDataFrame(graded_misses, crs="EPSG:4326") if graded_misses else gpd.GeoDataFrame(geometry=[])

    report_out = build_report(gdf_graded_fcst, gdf_graded_miss, valid_dt,
                              issuance_hour, lead_time, gdf_artcc)

    return {
        'lons': lons, 'lats': lats,
        'top_verif_matrix': top_verif_matrix,
        'gdf_graded_fcst': gdf_graded_fcst,
        'gdf_graded_miss': gdf_graded_miss,
        'gdf_sparse': gdf_sparse,
        'graded_forecasts': graded_forecasts,
        'graded_misses': graded_misses,
        'report_text': report_out,
        'valid_dt': valid_dt,
    }


# --- Serialisation ----------------------------------------------------------
COVERAGE_DP = 4   # decimal places for coverage_fraction and geometry bounds
TOP_DP = 2        # decimal places for top_kft

# A polygon whose coverage fraction lands this close to a grade cutoff can flip
# category on pure float noise (a different BLAS, a geometry-library point
# release). Marking those polygons lets check.py say "this category change is
# expected fragility" instead of "the pipeline regressed".
# KEEP IN SYNC with the identical block in check.py -- check.py deliberately does
# not import from this module, so the two definitions must be maintained together.
GRADE_CUTOFFS = (0.50, 0.20)
BOUNDARY_WINDOW = 0.005


def is_boundary(coverage_fraction):
    """True if coverage_fraction sits within BOUNDARY_WINDOW of a grade cutoff."""
    if coverage_fraction is None:
        return False
    return any(abs(float(coverage_fraction) - c) <= BOUNDARY_WINDOW for c in GRADE_CUTOFFS)


def _round_bounds(geom):
    return [round(float(b), COVERAGE_DP) for b in geom.bounds]


def build_expected(event, valid_dt, results, gdf_artcc):
    """The expected.json payload: metadata + report + per-polygon grades."""
    polygons = []
    for r in results['graded_forecasts']:
        cov_frac = round(float(r['coverage_fraction']), COVERAGE_DP)
        entry = {
            'idx': int(r['idx']),
            'category': r['category'],
            'coverage_code': int(r['coverage']),
            'feat_type': r['feat_type'],
            'coverage_fraction': cov_frac,
            'top_kft': round(float(r['top']), TOP_DP),
            'artccs': get_artccs(r['geometry'], gdf_artcc),
            'bounds': _round_bounds(r['geometry']),
        }
        # Emitted only when true, so a diff of two expected.json files stays quiet
        # for the ordinary case.
        if is_boundary(cov_frac):
            entry['boundary'] = True
        polygons.append(entry)

    misses = []
    for r in results['graded_misses']:
        misses.append({
            'idx': int(r['idx']),
            'artccs': get_artccs(r['geometry'], gdf_artcc),
            'bounds': _round_bounds(r['geometry']),
        })

    categories = {}
    for p in polygons:
        categories[p['category']] = categories.get(p['category'], 0) + 1

    return {
        'event_id': event['event_id'],
        'date': event['date'].strftime('%Y-%m-%d'),
        'issuance_hour': event['issuance_hour'],
        'lead_time': event['lead_time'],
        'valid_time_hour': valid_dt.hour,
        'valid_dt': valid_dt.strftime('%Y-%m-%dT%H:%M:%S'),
        'report_text': results['report_text'],
        'polygons': polygons,
        'misses': misses,
        'counts': {
            'polygons': len(polygons),
            'misses': len(misses),
            'verified_well': categories.get('Verified Well', 0),
            'verified_close': categories.get('Verified Close', 0),
            'overforecasted': categories.get('Overforecasted', 0),
            'boundary': sum(1 for p in polygons if p.get('boundary')),
        },
    }


def capture_event(event, gdf_artcc):
    out_dir = os.path.join(BASELINE_DIR, event['event_id'])
    os.makedirs(out_dir, exist_ok=True)

    valid_dt = compute_valid_dt(event['date'], event['issuance_hour'], event['lead_time'])
    _log(f"=== {event['event_id']} | VT {valid_dt:%Y-%m-%d %H:%MZ} ===")

    _log("Pulling Forecast from IEM Archives...")
    raw_text = fetch_iem_cow_raw(event['date'], event['issuance_hour'], event['lead_time'])
    gdf_forecast = parse_iem_cow_text(raw_text)
    if gdf_forecast.empty:
        raise RuntimeError("IEM failed or data missing for this issuance/lead time.")

    max_tops, max_refl, lons, lats = build_composite(valid_dt)

    _log("Building Objective Truth Polygons...")
    results = run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                               valid_dt, event['issuance_hour'], event['lead_time'], gdf_artcc)

    with open(os.path.join(out_dir, 'tcf_raw.txt'), 'w', encoding='utf-8') as f:
        f.write(raw_text)
    np.savez_compressed(os.path.join(out_dir, 'arrays.npz'),
                        max_tops=max_tops, max_refl=max_refl, lons=lons, lats=lats)
    expected = build_expected(event, valid_dt, results, gdf_artcc)
    with open(os.path.join(out_dir, 'expected.json'), 'w', encoding='utf-8') as f:
        json.dump(expected, f, indent=2, sort_keys=False)
        f.write('\n')

    _log(f"wrote {out_dir}: {expected['counts']['polygons']} polygons, "
         f"{expected['counts']['misses']} misses, "
         f"{expected['counts']['boundary']} near a grade cutoff")
    if not os.path.exists(os.path.join(out_dir, 'pass_a_report.txt')):
        _log(f"  NOTE: {event['event_id']}/pass_a_report.txt is missing -- paste the report "
             f"text from the live app there, then run `check.py --pass-a`.")
    return expected


def main(argv):
    wanted = set(argv[1:])
    events = [e for e in EVENTS if not wanted or e['event_id'] in wanted]
    unknown = wanted - {e['event_id'] for e in EVENTS}
    if unknown:
        raise SystemExit(f"unknown event id(s): {', '.join(sorted(unknown))}")

    gdf_artcc = load_artccs()
    failures = []
    for event in events:
        try:
            capture_event(event, gdf_artcc)
        except Exception as exc:
            failures.append((event['event_id'], exc))
            _log(f"FAILED {event['event_id']}: {type(exc).__name__}: {exc}")

    if failures:
        raise SystemExit(f"{len(failures)} of {len(events)} event(s) failed to capture")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))


# ============================================================================
# BUG INVENTORY -- observed in app.py while transcribing, DELIBERATELY NOT FIXED
# ============================================================================
# This file exists to freeze current behaviour, so every one of these is
# reproduced as-is. Line numbers refer to app.py as of this commit.
#
#  1. No-MRMS crash (app.py 478-524). If every download_mrms_scan() call in the
#     +/-15 min window returns None, max_tops/max_refl stay None and
#     np.zeros_like(None) / (None >= 40) raise a TypeError with no useful
#     message. There is no "no radar data" branch. capture.py raises a clear
#     RuntimeError instead only because it cannot proceed at all.
#
#  2. LINE features become polygons (app.py 119-130). parse_iem_cow_text only
#     buffers a LineString when the feature has exactly 2 points; a LINE with 3+
#     points falls into the `len(coords) >= 3` branch and is closed into a
#     Polygon. Multi-point TCF lines are therefore graded as filled areas, which
#     inflates both the forecast area and the hit area.
#
#  3. Truth contours are truncated, not interpolated (app.py 242-243).
#     extract_tcf_polygons indexes lons/lats with int(p[1]) / int(p[0]), throwing
#     away the sub-cell precision that find_contours computed. Every truth
#     polygon edge is biased toward the grid origin by up to one 5x-decimated
#     cell (~0.05 deg), which systematically shrinks/shifts truth areas.
#
#  4. simplify() output is never re-validated (app.py 244-245). Validity is
#     checked before .simplify(0.05); simplification can produce a
#     self-intersecting ring, and that invalid geometry goes straight into the
#     union.
#
#  5. Interior rings ignored in the echo-top calc (app.py 568). Path() is built
#     from poly.exterior.coords only, so a forecast polygon with a hole counts
#     grid cells inside the hole toward its 90th-percentile top.
#
#  6. Degree-space areas (app.py 559-561, 588). fcst_area, hit_area and the miss
#     `captured` ratio are computed in EPSG:4326, so a degree of longitude is
#     treated as a fixed length. The numerator and denominator are both in the
#     same units so it mostly cancels, but not for polygons spanning a wide
#     latitude range -- unlike extract_tcf_polygons, which correctly reprojects
#     to EPSG:5070 for its area filter.
#
#  7. Inconsistent default coverage code. The grading loop defaults a missing
#     'coverage' column to 3 (app.py 555) while build_report defaults it to 25
#     (app.py 419). Both happen to land on "Sparse" via _coverage_label's
#     fall-through, so the disagreement is currently invisible -- but only by
#     accident.
#
#  8. Asymmetric miss test (app.py 588). Hits are graded against a
#     coverage-dependent truth union (25% for Sparse, 40% for Medium/Dense),
#     but misses are always measured against the 25% sparse truth and against
#     the union of ALL forecast polygons regardless of their coverage code.
#
#  9. Dead index assignment (app.py 579, 596-601). Each graded record gets
#     'idx': idx + 1, which is unconditionally overwritten by the E->W renumber
#     a few lines later.
#
# 10. Silent scan drops (app.py 488). If a scan yields a tops file but no refl
#     file (or vice versa), the whole scan is skipped with no record. The
#     composite can quietly be built from 1 of 7 scans and nothing downstream
#     can tell.
#
# 11. Grid-shape assumption across scans (app.py 503-504). np.maximum() assumes
#     every scan in the window shares the decimated grid shape captured from the
#     first successful scan; a mid-window MRMS grid change raises a broadcast
#     error rather than being handled.
#
# 12. Small-core tops report as zero (app.py 572). Fewer than 6 qualifying cells
#     yields actual_top_kft = 0, and build_report's `if row.top > 0` then omits
#     the top entirely -- indistinguishable from "no tops data".
#
# 13. Regex block-splitting is fragile (app.py 95). r'(AREA|LINE)\s+([\d\s]+)'
#     consumes digits and whitespace across newlines, so it relies on the next
#     non-digit token to terminate a block. Any numeric trailer between features
#     (a footer, a product ID) is silently absorbed into the preceding feature's
#     coordinate list.
#
# 14. Hard-coded western hemisphere (app.py 114-115). `if lon > 0: lon = -lon`
#     in the coordinate parser, and the lons > 180 wrap in the composite loop,
#     both assume CONUS. Fine today, wrong for any OCONUS product.
# ============================================================================
