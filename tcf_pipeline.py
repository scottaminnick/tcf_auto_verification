#!/usr/bin/env python3
"""The TCF verification pipeline: the single implementation, shared by both callers.

Everything here used to exist twice -- once inline in ``app.py`` and once
transcribed into ``baseline/capture.py``. Two copies of grading logic is one
copy too many: the whole point of the baseline harness is to prove a refactor
changed nothing, and it cannot do that while the thing it compares against is a
separate hand-maintained copy of the thing it is checking.

    app.py             thin Streamlit cache wrappers + all display code
    baseline/capture.py  EVENTS + serialisation of a run to disk
    tcf_pipeline.py    <- the math, the parsing, the report text

This module must never import streamlit. It raises instead of calling
``st.stop()`` / ``st.sidebar.error()``, and progress goes to stderr (or to a
caller-supplied ``log`` callback, which is how app.py keeps its st.write
progress lines). requests / boto3 / xarray import lazily inside the two
network-bound functions, so this module stays importable without the AWS SDK or
the cfgrib/ecCodes stack.

Nothing here is an improvement on the original app.py code. The functions were
moved verbatim; the known defects are preserved deliberately and catalogued in
the BUG INVENTORY at the bottom of this file.
"""

import gc
import gzip
import json
import os
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import geopandas as gpd
import pandas as pd
import numpy as np
from matplotlib.path import Path
from scipy.ndimage import binary_dilation, uniform_filter
from shapely.geometry import LineString, Polygon
from skimage import measure

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ARTCC_PATH = os.path.join(REPO_ROOT, "artcc1.geojson")


@dataclass(frozen=True)
class DisplayRaster:
    """Native-resolution tops/reflectivity for drawing only.

    Deliberately separate from the verification arrays: those stay decimated to
    COMPOSITE_STEP because GradingParams (smoothing_size, min_area_m2, the
    dilation) is calibrated against that grid spacing. Nothing here is ever fed
    to run_verification.
    """

    max_tops: object
    max_refl: object
    lons: object
    lats: object

    @property
    def extent(self):
        """(lon_min, lon_max, lat_min, lat_max) for anchoring the image."""
        return (float(self.lons.min()), float(self.lons.max()),
                float(self.lats.min()), float(self.lats.max()))


# --- Tuning parameters ------------------------------------------------------
@dataclass(frozen=True)
class GradingParams:
    """The tuning knobs run_verification() grades with.

    Every default is the literal that was hardcoded before this became a
    parameter, so GradingParams() reproduces the frozen baselines exactly. The
    point is to make the knobs nameable and overridable -- not to change any of
    them. A non-default value is a deliberate experiment; the baselines under
    baseline/<event_id>/ encode the defaults and will (correctly) go red for
    anything else.

    Frozen so a params object cannot be mutated halfway through a run.

    Deliberately NOT here yet: the echo-top bands (25/30/35/40 kft) and the
    40 dBZ convection floor. The bands are wrong relative to the TCF flight
    levels and are being corrected separately, so that diff stays inspectable
    on its own.
    """

    # Truth coverage thresholds. A Sparse (cov=3) forecast verifies against the
    # 25%+ truth field; Medium/Dense (cov=1,2) against the 40%+ field.
    sparse_truth_threshold: float = 0.25
    medium_truth_threshold: float = 0.40

    # Grade cutoffs on the hit/forecast area ratio.
    verified_well_cutoff: float = 0.50
    verified_close_cutoff: float = 0.20

    # A truth blob counts as Missed when the forecast captured less than this
    # much of it. Numerically equal to verified_close_cutoff, but a separate
    # field on purpose: it asks the opposite question (how much of a TRUTH blob
    # the forecast captured, not how much of a FORECAST truth filled), so the
    # two must be able to move independently.
    miss_capture_threshold: float = 0.20

    # Truth-field construction, in decimated (5x) grid cells.
    dilation_iterations: int = 1
    smoothing_size: int = 20

    # Minimum truth polygon area, m^2 (15,000 km^2), measured in EPSG:5070.
    min_area_m2: float = 15_000_000_000


# How close to a grade cutoff a coverage fraction has to land before it counts
# as fragile -- see is_boundary(). Not a GradingParams field: it does not affect
# a single graded outcome, it only annotates one.
BOUNDARY_WINDOW = 0.005


def is_boundary(coverage_fraction, params=GradingParams()):
    """True if coverage_fraction sits within BOUNDARY_WINDOW of a grade cutoff.

    Such a polygon can flip category on nothing worse than a different BLAS or
    a geometry-library point release, so both the review table and the baseline
    harness flag it rather than treating a category change there as a
    regression.
    """
    if coverage_fraction is None:
        return False
    cutoffs = (params.verified_well_cutoff, params.verified_close_cutoff)
    return any(abs(float(coverage_fraction) - c) <= BOUNDARY_WINDOW for c in cutoffs)


def _log(msg):
    """Stand-in for the st.write / st.status progress chatter in app.py."""
    print(msg, file=sys.stderr, flush=True)


# --- Geography --------------------------------------------------------------
def load_artccs(path=ARTCC_PATH):
    """ARTCC boundaries, read with plain json (the parsing half of app.py's
    load_geography()).

    load_geography() also fetches state boundaries over HTTP, but those are used
    only for drawing the map -- no verification number depends on them -- so that
    half stays in app.py and this module has no import-time network dependency.

    A read failure raises here. app.py's wrapper still swallows it and carries on
    with an empty frame (get_artccs then returns "UNKNOWN"), preserving the
    dashboard's behaviour, while capture.py lets it propagate rather than freeze a
    baseline whose report text says UNKNOWN everywhere.
    """
    with open(path, "r", encoding="utf-8") as f:
        artcc_data = json.load(f)
    return gpd.GeoDataFrame.from_features(artcc_data["features"], crs="EPSG:4326")


# --- Helper functions (moved verbatim from app.py, minus Streamlit) ----------
def _coverage_label(cov_val):
    """Map a TCF/CCFP coverage integer code to its plain-English label.
    TCF 1-digit encoding: 1=Dense (75%+), 2=Medium (40-74%), 3=Sparse (25-39%)."""
    if cov_val == 1:
        return "Dense"
    elif cov_val == 2:
        return "Medium"
    return "Sparse"


# Half-width applied to a LINE feature, in degrees. Unchanged from the original
# literal -- widening or narrowing it, and whether a line should be graded on
# area at all, is a separate decision from the geometry fix below.
LINE_BUFFER_DEG = 0.15


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

            # A LINE is a line at any point count. The old form tested only
            # len(coords) and so closed every 3+ point LINE into a Polygon --
            # a 3-point line became a triangle -- which inflated both the
            # forecast area and the hit area. Only 2-point lines ever reached
            # the buffer path. AREA is untouched: 3+ points still make a
            # polygon, and a degenerate 2-point AREA still falls back to the
            # same buffered line it always did.
            if feat_type == 'LINE' and len(coords) >= 2:
                poly = LineString(coords).buffer(LINE_BUFFER_DEG)
                records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})
            elif len(coords) >= 3:
                poly = Polygon(coords).buffer(0)
                if not poly.is_empty:
                    records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})
            elif len(coords) >= 2:
                poly = LineString(coords).buffer(LINE_BUFFER_DEG)
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


def fetch_iem_cow_tcf(date_obj, issue_hr, f_hr):
    """Automatically scrapes the TCF text from IEM archives.

    app.py's original returned an empty GeoDataFrame after calling
    st.sidebar.error(); this raises instead, and app.py's wrapper turns the
    exception back into the same sidebar error + empty frame it always showed.
    capture.py uses fetch_iem_cow_raw() directly, because it has to freeze the
    raw response to tcf_raw.txt before parsing it.
    """
    return parse_iem_cow_text(fetch_iem_cow_raw(date_obj, issue_hr, f_hr))


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


# --- MRMS S3 access ---------------------------------------------------------
# One listing per (product, UTC day), cached for the life of the process. The
# old code listed the bucket once per scan per product -- 14 listings to fetch 14
# files -- and every one of those listings returned the same ~720 keys.
_MRMS_BUCKET = 'noaa-mrms-pds'
_MRMS_KEY_CACHE = {}
_MRMS_KEY_CACHE_LOCK = threading.Lock()


def _s3_client():
    import boto3
    import botocore

    return boto3.client('s3', config=botocore.client.Config(signature_version=botocore.UNSIGNED))


def list_mrms_keys(product, date_str, s3=None):
    """[(key, file_datetime)] for one product-day, newest listing cached.

    Paginated. A 2-minute product is ~720 keys a day, comfortably under the
    1000-key page limit the old single list_objects_v2 call relied on, but that
    call had no IsTruncated guard at all -- it would silently see only the first
    page and the afternoon scans would simply not resolve. Raising the file count
    (a finer cadence) would have walked straight into that.
    """
    cache_key = (product, date_str)
    with _MRMS_KEY_CACHE_LOCK:
        cached = _MRMS_KEY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    s3 = s3 or _s3_client()
    prefix = f"CONUS/{product}_00.50/{date_str}/"
    entries = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=_MRMS_BUCKET, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.grib2.gz'):
                continue
            m = re.search(r'(\d{8})-(\d{6})', key.split('/')[-1])
            if not m:
                continue
            entries.append((key, datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')))

    with _MRMS_KEY_CACHE_LOCK:
        _MRMS_KEY_CACHE[cache_key] = entries
    return entries


def _resolve_scan_key(product, dt_obj, s3=None):
    """The archived key NEAREST in time to dt_obj, or None.

    Same rule as before: nearest wins, and anything more than 5 minutes away is
    a genuine archive gap rather than a usable scan. MRMS scans are stamped with
    seconds (...20260524-231038), so requests on 5-minute marks rarely match
    exactly and an exact-match lookup silently drops the scan.
    """
    try:
        entries = list_mrms_keys(product, dt_obj.strftime('%Y%m%d'), s3=s3)
    except Exception:
        return None

    best_key, best_diff = None, None
    for key, file_dt in entries:
        diff = abs((file_dt - dt_obj).total_seconds())
        if best_diff is None or diff < best_diff:
            best_key, best_diff = key, diff

    if best_key is None or best_diff > 5 * 60:
        return None
    return best_key


def _download_key(key, dest_dir="mrms_data", s3=None):
    """Fetch and gunzip one key, returning the local .grib2 path (or None)."""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        local_gz = os.path.join(dest_dir, key.split('/')[-1])
        local_grib = local_gz.replace('.gz', '')

        if not os.path.exists(local_grib):
            (s3 or _s3_client()).download_file(_MRMS_BUCKET, key, local_gz)
            with gzip.open(local_gz, 'rb') as f_in, open(local_grib, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(local_gz)
        return local_grib
    except Exception:
        return None


def download_mrms_scan(product, dt_obj, dest_dir="mrms_data"):
    """Resolve and fetch the scan nearest dt_obj. Unchanged behaviour, now built
    on the cached listing rather than a fresh one per call."""
    key = _resolve_scan_key(product, dt_obj)
    if key is None:
        return None
    return _download_key(key, dest_dir)


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


# Column order and dtypes of the review table. Nullable pandas dtypes throughout
# (miss rows have no coverage code, feat type, fraction or top), so the frame can
# round-trip through st.data_editor without integers turning into floats -- an
# idx that came back as 1.0 would render as "(Area 1.0)" in the report.
REVIEW_COLUMNS = {
    'idx': 'Int64',
    'kind': 'string',
    'category': 'string',
    'coverage_code': 'Int64',
    'feat_type': 'string',
    'artccs': 'string',
    'coverage_fraction': 'Float64',
    'top_kft': 'Float64',
    'boundary': 'boolean',
}


def build_review_table(gdf_graded_fcst, gdf_graded_miss, gdf_artcc, params=GradingParams()):
    """Everything build_report needs, as a plain DataFrame -- one row per graded
    polygon and per miss.

    This is the seam an editable review table sits in: geometry, ARTCC lookups
    and grading all happen here, and build_report() below formats text from the
    result and nothing else. Anything a reviewer can change about the report has
    to be a column, because the frame is the only thing build_report() sees.

    No geometry and no numpy scalars in the output -- it has to survive a round
    trip through st.data_editor.

    top_kft carries the RAW top, not the value expected.json rounds to 2 dp. The
    report formats it with :.1f, and rounding twice can land on a different last
    digit.
    """
    rows = []

    if not gdf_graded_fcst.empty:
        for _, row in gdf_graded_fcst.iterrows():
            coverage_fraction = getattr(row, 'coverage_fraction', None)
            rows.append({
                'idx': int(row.idx),
                'kind': 'forecast',
                'category': row.category,
                # getattr defaults preserved verbatim from the original
                # build_report -- 25 is not a coverage code (BUG 7), it just
                # happens to fall through _coverage_label to "Sparse".
                'coverage_code': int(getattr(row, 'coverage', 25)),
                'feat_type': getattr(row, 'feat_type', 'AREA'),
                'artccs': get_artccs(row.geometry, gdf_artcc),
                'coverage_fraction': (None if coverage_fraction is None
                                      else float(coverage_fraction)),
                'top_kft': float(row.top),
                'boundary': bool(is_boundary(coverage_fraction, params)),
            })

    if not gdf_graded_miss.empty:
        for _, row in gdf_graded_miss.iterrows():
            rows.append({
                'idx': int(row.idx),
                'kind': 'miss',
                'category': 'Missed',
                'coverage_code': None,
                'feat_type': None,
                'artccs': get_artccs(row.geometry, gdf_artcc),
                'coverage_fraction': None,
                'top_kft': None,
                'boundary': False,
            })

    table = pd.DataFrame(rows, columns=list(REVIEW_COLUMNS))
    return table.astype(REVIEW_COLUMNS)


def build_report(review_table, valid_dt, issuance_hour, lead_time):
    """Assembles the copy-paste FAA/NWS text report from the review table.

    Formatting only: no geometry, no ARTCC lookups, no grading. Whatever the
    table says is what the report says -- which is what makes an edited table
    produce an edited report.
    """
    report_text = ""
    doc_report = {"Verified Well:": [], "Verified Close:": [], "Over-forecast:": [], "Missed:": []}

    for row in review_table.itertuples(index=False):
        if row.kind == 'miss':
            doc_report["Missed:"].append(f"{row.artccs} - Missed (Area M{row.idx})")
            continue

        top_str = f" [Top: {row.top_kft:.1f} kft]" if row.top_kft > 0 else ""
        cov_label = _coverage_label(row.coverage_code)
        feat_label = "Line" if row.feat_type == 'LINE' else "Area"
        line_text = f"{row.artccs} - {cov_label} ({feat_label} {row.idx}){top_str}"
        if row.category == "Verified Well":
            doc_report["Verified Well:"].append(line_text)
        elif row.category == "Verified Close":
            doc_report["Verified Close:"].append(line_text)
        elif row.category == "Overforecasted":
            doc_report["Over-forecast:"].append(line_text)

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
    """Valid datetime for an issuance/lead pair (the sidebar rollover in app.py)."""
    valid_time = issuance_hour + lead_time
    if valid_time >= 24:
        valid_time -= 24
        valid_dt = datetime.combine(target_date + timedelta(days=1), time(valid_time, 0))
    else:
        valid_dt = datetime.combine(target_date, time(valid_time, 0))
    return valid_dt


# Products the composite needs, in the order build_composite reads them.
TOPS_PRODUCT = "EchoTop_18"
REFL_PRODUCT = "MergedReflectivityQCComposite"

# Defaults are exactly the values that were hardcoded in build_composite. They
# are arguments so app.py can key its st.cache_data wrapper on them; changing
# any of them changes the composite and will (correctly) fail the baselines.
COMPOSITE_WINDOW_MINUTES = 15
COMPOSITE_CADENCE_MINUTES = 2
COMPOSITE_STEP = 5          # verification grid: 0.05 deg, what GradingParams is calibrated to
DISPLAY_STEP = 1            # display raster: native 0.01 deg
COMPOSITE_MAX_WORKERS = 8


def scan_offsets(window_minutes=COMPOSITE_WINDOW_MINUTES,
                 cadence_minutes=COMPOSITE_CADENCE_MINUTES):
    """Scan offsets in minutes, symmetric about the valid time.

    Built as multiples of the cadence outward from 0 rather than
    range(-window, window+1, cadence). At the old 5-minute cadence the two are
    identical (-15..15), but at 2 minutes range() would start at -15 and step to
    +15 WITHOUT EVER LANDING ON 0 -- the scan at the valid time itself would be
    dropped, and there would be 16 scans rather than 15.
    """
    k = window_minutes // cadence_minutes
    return [i * cadence_minutes for i in range(-k, k + 1)]


def _read_scan_arrays(tops_file, refl_file, step):
    """Decode one scan pair into (tops_kft, refl, lons, lats) at `step`.

    Split out of build_composite so the fixture test can drive the whole fetch
    path -- including the thread pool -- without cfgrib or S3.
    """
    # Imported at the point of use, not at module scope: that keeps this module
    # importable without the cfgrib/ecCodes stack installed.
    import xarray as xr

    ds_t = xr.open_dataset(tops_file, engine='cfgrib', backend_kwargs={'indexpath': ''})
    ds_r = xr.open_dataset(refl_file, engine='cfgrib', backend_kwargs={'indexpath': ''})

    curr_tops = ds_t.unknown[::step, ::step].values * 3.28084
    curr_refl = ds_r.unknown[::step, ::step].values

    lons = ds_t.longitude[::step].values
    lons = np.where(lons > 180, lons - 360, lons)
    lats = ds_t.latitude[::step].values

    ds_t.close()
    ds_r.close()
    del ds_t, ds_r
    return curr_tops, curr_refl, lons, lats


def build_composite(valid_dt, log=_log,
                    window_minutes=COMPOSITE_WINDOW_MINUTES,
                    cadence_minutes=COMPOSITE_CADENCE_MINUTES,
                    step=COMPOSITE_STEP,
                    max_workers=COMPOSITE_MAX_WORKERS,
                    dest_dir="mrms_data",
                    with_display=False):
    """The rolling MRMS composite. Network-bound.

    Scans are fetched concurrently but folded into the running max in a FIXED
    order -- ascending time offset, exactly the order the old sequential loop
    used. That is deliberate and load-bearing: np.maximum returns its first
    argument when the two compare equal, so for a cell holding -0.0 in one scan
    and +0.0 in another the RESULT'S SIGN BIT depends on fold order. The values
    are numerically equal and nothing downstream can see the difference, but
    "bit-identical" would not hold if the fold followed completion order.
    Pinning the order makes download completion order irrelevant by construction;
    baseline/test_fixture.py asserts it rather than trusting this comment.

    `log` is the one seam this module offers its callers: capture.py leaves it
    at the stderr default, app.py passes st.write so the per-scan progress lines
    still appear in the dashboard's status box. It is only ever called from the
    calling thread -- never from a pool worker -- so a Streamlit callback cannot
    be invoked off-script-thread and the lines cannot interleave. It carries no
    data and cannot affect the arrays.

    Returns (max_tops, max_refl, lons, lats). Raises if no scan in the window
    could be read -- the original inline version had no such guard and blew up
    later with a TypeError on None (see BUG 1 at the bottom of this file).
    """
    time_offsets = scan_offsets(window_minutes, cadence_minutes)

    # Resolve every scan first. Each distinct (product, UTC day) is listed once
    # and cached, so this is 2 listings for a window inside one day and 4 across
    # a midnight rollover -- not one per scan per product.
    s3 = _s3_client()
    plan = []
    for offset in time_offsets:
        scan_dt = valid_dt + timedelta(minutes=offset)
        plan.append((scan_dt,
                     _resolve_scan_key(TOPS_PRODUCT, scan_dt, s3=s3),
                     _resolve_scan_key(REFL_PRODUCT, scan_dt, s3=s3)))

    # Deduplicate before fetching. Adjacent offsets can resolve to the same
    # archived file when the archive is sparse; downloading a key twice would be
    # wasted work sequentially and a write race in parallel, since both would
    # target the same path.
    wanted = sorted({key for _, tk, rk in plan for key in (tk, rk) if key})
    log(f"Fetching {len(wanted)} MRMS files for {len(time_offsets)} scans "
        f"({max_workers} at a time)...")

    paths = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(wanted))) as pool:
            for key, path in zip(wanted, pool.map(
                    lambda k: _download_key(k, dest_dir, s3=s3), wanted)):
                paths[key] = path

    max_tops, max_refl = None, None
    lons, lats = None, None
    disp_tops, disp_refl = None, None
    disp_lons, disp_lats = None, None

    for scan_dt, tops_key, refl_key in plan:
        tops_file = paths.get(tops_key)
        refl_file = paths.get(refl_key)
        log(f"Pulling MRMS for {scan_dt.strftime('%H:%MZ')}...")

        if tops_file and refl_file:
            if with_display:
                # Decode ONCE at native resolution and slice the verification
                # grid out of it. ds.unknown[::5,::5].values and
                # ds.unknown.values[::5,::5] are bit-identical (checked in
                # scratch/bench_grib_decode.py), so this changes no verification
                # number while giving the display raster for free -- decoding
                # each file twice would double the slowest phase of the build.
                full_tops, full_refl, full_lons, full_lats = _read_scan_arrays(
                    tops_file, refl_file, DISPLAY_STEP)
                curr_tops = full_tops[::step, ::step]
                curr_refl = full_refl[::step, ::step]
                curr_lons, curr_lats = full_lons[::step], full_lats[::step]

                if disp_tops is None:
                    disp_tops, disp_refl = full_tops, full_refl
                    disp_lons, disp_lats = full_lons, full_lats
                else:
                    disp_tops = np.maximum(disp_tops, full_tops)
                    disp_refl = np.maximum(disp_refl, full_refl)
                del full_tops, full_refl
            else:
                curr_tops, curr_refl, curr_lons, curr_lats = _read_scan_arrays(
                    tops_file, refl_file, step)

            if lons is None:
                lons, lats = curr_lons, curr_lats

            if max_tops is None:
                max_tops, max_refl = curr_tops, curr_refl
            else:
                max_tops = np.maximum(max_tops, curr_tops)
                max_refl = np.maximum(max_refl, curr_refl)

            del curr_tops, curr_refl
            gc.collect()

    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)

    if max_tops is None:
        raise RuntimeError(f"No MRMS scans available in the +/-{window_minutes} min "
                           f"window around {valid_dt}")

    if with_display:
        return (max_tops, max_refl, lons, lats,
                DisplayRaster(disp_tops, disp_refl, disp_lons, disp_lats))
    return max_tops, max_refl, lons, lats


def run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                     valid_dt, issuance_hour, lead_time, gdf_artcc,
                     params=GradingParams()):
    """The verification math, pure and Streamlit-free.

    Everything downstream of the MRMS composite lives here: this is the function
    check.py replays against the frozen arrays, so it must stay a pure function
    of its arguments (no network, no globals, no session_state).

    `params` carries the tuning knobs (see GradingParams). The default instance
    holds exactly the values that were hardcoded here before, so callers that
    pass nothing -- app.py, capture.py, check.py -- get the frozen behaviour.
    The default is safe to share because GradingParams is frozen.

    Returns a dict with the same members app.py stashes in st.session_state
    ['results'], plus 'graded_forecasts'/'graded_misses' as plain lists of dicts.

    NOTE: the per-polygon 'coverage_fraction' (the hit/forecast area ratio) is
    carried through in the graded dicts. The original computed the identical
    value in the local variable `coverage` but kept only the category it maps to;
    the number itself is recorded because the baseline grades on it. No math is
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
    buffered_cores = binary_dilation(raw_cores, iterations=params.dilation_iterations)
    coverage_fraction = uniform_filter(buffered_cores.astype(float), size=params.smoothing_size)

    # Truth-area filter (default 15,000 km^2), matching the notebook.
    gdf_sparse = extract_tcf_polygons((coverage_fraction >= params.sparse_truth_threshold).astype(int),
                                      lons, lats, min_area_m2=params.min_area_m2)
    # Medium (cov=2) and Dense (cov=1) forecasts must verify against 40%+ truth,
    # matching the TCF Medium coverage threshold (40-74%).
    gdf_medium_truth = extract_tcf_polygons((coverage_fraction >= params.medium_truth_threshold).astype(int),
                                            lons, lats, min_area_m2=params.min_area_m2)
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

        cat, color = ("Verified Well", 'lime') if coverage >= params.verified_well_cutoff else \
                     ("Verified Close", 'yellow') if coverage >= params.verified_close_cutoff else \
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
        # NOT params.verified_close_cutoff -- see the note on that field. Same
        # value today, opposite question.
        if captured < params.miss_capture_threshold:
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

    # Two stages: everything geometric collapses into the review table, then the
    # report is formatted from that table alone. An editable table drops in
    # between these two lines.
    review_table = build_review_table(gdf_graded_fcst, gdf_graded_miss, gdf_artcc, params=params)
    report_out = build_report(review_table, valid_dt, issuance_hour, lead_time)

    return {
        'lons': lons, 'lats': lats,
        'top_verif_matrix': top_verif_matrix,
        'gdf_graded_fcst': gdf_graded_fcst,
        'gdf_graded_miss': gdf_graded_miss,
        'gdf_sparse': gdf_sparse,
        'graded_forecasts': graded_forecasts,
        'graded_misses': graded_misses,
        'review_table': review_table,
        'report_text': report_out,
        'valid_dt': valid_dt,
    }



# ============================================================================
# BUG INVENTORY -- carried over from app.py, DELIBERATELY NOT FIXED
# ============================================================================
# These were catalogued while this code was transcribed out of app.py, and they
# moved here with it unchanged. The baselines under baseline/<event_id>/ encode
# their effects, so fixing any one of them will (correctly) turn check.py red.
# References are by function rather than line number, since the code now lives
# here rather than at a fixed offset in app.py.
#
#  1. No-MRMS crash (build_composite). If every download_mrms_scan() call in the
#     +/-15 min window returns None, max_tops/max_refl stay None and the
#     downstream np.zeros_like(None) / (None >= 40) raise a TypeError with no
#     useful message. There is still no "no radar data" branch -- build_composite
#     raises a clear RuntimeError first, purely because it cannot return a
#     composite at all. That guard predates this move (it was in capture.py's
#     copy) and app.py now inherits it: the failure is still an unhandled
#     exception surfaced by Streamlit, but a legible one. No grading math differs.
#
#  2. FIXED -- LINE features no longer become polygons. parse_iem_cow_text used
#     to buffer a LineString only when the feature had exactly 2 points; a LINE
#     with 3+ points fell into the `len(coords) >= 3` branch and was closed into
#     a Polygon, so multi-point TCF lines were graded as filled areas. Lines are
#     now buffered at any point count. The 0.15 deg buffer itself is unchanged,
#     and so is the way a line is graded once buffered -- both are open
#     questions, deliberately not settled here. Baselines before the fix are
#     frozen in baseline_v2_line_closed/.
#
#  3. Truth contours are truncated, not interpolated (extract_tcf_polygons).
#     extract_tcf_polygons indexes lons/lats with int(p[1]) / int(p[0]), throwing
#     away the sub-cell precision that find_contours computed. Every truth
#     polygon edge is biased toward the grid origin by up to one 5x-decimated
#     cell (~0.05 deg), which systematically shrinks/shifts truth areas.
#
#  4. simplify() output is never re-validated (extract_tcf_polygons). Validity is
#     checked before .simplify(0.05); simplification can produce a
#     self-intersecting ring, and that invalid geometry goes straight into the
#     union.
#
#  5. Interior rings ignored in the echo-top calc (run_verification). Path() is built
#     from poly.exterior.coords only, so a forecast polygon with a hole counts
#     grid cells inside the hole toward its 90th-percentile top.
#
#  6. Degree-space areas (run_verification). fcst_area, hit_area and the miss
#     `captured` ratio are computed in EPSG:4326, so a degree of longitude is
#     treated as a fixed length. The numerator and denominator are both in the
#     same units so it mostly cancels, but not for polygons spanning a wide
#     latitude range -- unlike extract_tcf_polygons, which correctly reprojects
#     to EPSG:5070 for its area filter.
#
#  7. Inconsistent default coverage code. The grading loop defaults a missing
#     'coverage' column to 3 (run_verification) while build_report defaults it to 25
#     Both happen to land on "Sparse" via _coverage_label's
#     fall-through, so the disagreement is currently invisible -- but only by
#     accident.
#
#  8. Asymmetric miss test (run_verification). Hits are graded against a
#     coverage-dependent truth union (25% for Sparse, 40% for Medium/Dense),
#     but misses are always measured against the 25% sparse truth and against
#     the union of ALL forecast polygons regardless of their coverage code.
#
#  9. Dead index assignment (run_verification). Each graded record gets
#     'idx': idx + 1, which is unconditionally overwritten by the E->W renumber
#     a few lines later.
#
# 10. Silent scan drops (build_composite). If a scan yields a tops file but no refl
#     file (or vice versa), the whole scan is skipped with no record. The
#     composite can quietly be built from 1 of 7 scans and nothing downstream
#     can tell.
#
# 11. Grid-shape assumption across scans (build_composite). np.maximum() assumes
#     every scan in the window shares the decimated grid shape captured from the
#     first successful scan; a mid-window MRMS grid change raises a broadcast
#     error rather than being handled.
#
# 12. Small-core tops report as zero (run_verification). Fewer than 6 qualifying cells
#     yields actual_top_kft = 0, and build_report's `if row.top > 0` then omits
#     the top entirely -- indistinguishable from "no tops data".
#
# 13. Regex block-splitting is fragile (parse_iem_cow_text). r'(AREA|LINE)\s+([\d\s]+)'
#     consumes digits and whitespace across newlines, so it relies on the next
#     non-digit token to terminate a block. Any numeric trailer between features
#     (a footer, a product ID) is silently absorbed into the preceding feature's
#     coordinate list.
#
# 14. Hard-coded western hemisphere (parse_iem_cow_text). `if lon > 0: lon = -lon`
#     in the coordinate parser, and the lons > 180 wrap in the composite loop,
#     both assume CONUS. Fine today, wrong for any OCONUS product.
# ============================================================================
