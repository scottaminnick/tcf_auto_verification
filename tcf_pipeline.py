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

The grading methodology remains bug-for-bug compatible with the original app,
except where the BUG INVENTORY explicitly marks infrastructure/visibility fixes.
Known scientific defects remain catalogued at the bottom of this file.
"""

import functools
import gc
import gzip
import json
import os
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

import geopandas as gpd
import pandas as pd
import numpy as np
from scipy.ndimage import binary_dilation, uniform_filter
from shapely import contains_xy, union_all
from shapely.geometry import LineString, MultiPolygon, Polygon, box

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ARTCC_PATH = os.path.join(REPO_ROOT, "artcc1.geojson")
CMAC_DOMAIN_PATH = os.path.join(REPO_ROOT, "cmac_domain.geojson")
PHYSICAL_AREA_CRS = "EPSG:5070"
METHODOLOGY_VERSION = "1.0-rc1"


@dataclass(frozen=True)
class DisplayRaster:
    """Native-resolution tops/reflectivity for drawing only.

    Deliberately separate from the verification arrays: those stay decimated to
    COMPOSITE_STEP because GradingParams (smoothing_size, the
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


@dataclass(frozen=True)
class MRMSObservationProvenance:
    """Factual provenance for one nominal time in the MRMS composite plan."""

    requested_time: datetime
    reflectivity_product: str
    echo_top_product: str
    reflectivity_key: str | None
    echo_top_key: str | None
    reflectivity_resolved: bool
    echo_top_resolved: bool
    reflectivity_time: datetime | None
    echo_top_time: datetime | None
    reflectivity_offset_seconds: float | None
    echo_top_offset_seconds: float | None
    product_separation_seconds: float | None
    reflectivity_downloaded: bool = False
    echo_top_downloaded: bool = False
    both_products_available: bool = False
    grid_compatible: bool | None = None
    used: bool = False
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class MRMSCompositeProvenance:
    """Descriptive composite summary; deliberately contains no quality grade."""

    observations: tuple[MRMSObservationProvenance, ...]
    total_requested: int
    reflectivity_resolved: int
    echo_top_resolved: int
    both_resolved: int
    observations_used: int
    missing_reflectivity: int
    missing_echo_top: int
    max_reflectivity_offset_seconds: float | None
    max_echo_top_offset_seconds: float | None
    max_product_separation_seconds: float | None
    all_used_grids_compatible: bool

    @classmethod
    def from_observations(cls, observations):
        """Build counts/maxima directly from detailed records."""
        records = tuple(observations)

        def _maximum(field):
            values = [abs(getattr(r, field)) for r in records
                      if getattr(r, field) is not None]
            return max(values) if values else None

        used = [r for r in records if r.used]
        return cls(
            observations=records,
            total_requested=len(records),
            reflectivity_resolved=sum(r.reflectivity_resolved for r in records),
            echo_top_resolved=sum(r.echo_top_resolved for r in records),
            both_resolved=sum(r.reflectivity_resolved and r.echo_top_resolved
                              for r in records),
            observations_used=len(used),
            missing_reflectivity=sum(not r.reflectivity_resolved for r in records),
            missing_echo_top=sum(not r.echo_top_resolved for r in records),
            max_reflectivity_offset_seconds=_maximum("reflectivity_offset_seconds"),
            max_echo_top_offset_seconds=_maximum("echo_top_offset_seconds"),
            max_product_separation_seconds=_maximum("product_separation_seconds"),
            all_used_grids_compatible=bool(used) and all(
                r.grid_compatible is True for r in used),
        )


class MRMSCompositeUnavailableError(RuntimeError):
    """No usable composite; detailed factual provenance remains inspectable."""

    def __init__(self, message, provenance):
        super().__init__(message)
        self.provenance = provenance


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

    # Truth coverage thresholds. A Sparse AREA (cov=3) verifies against the
    # 25%+ truth field; Medium AREA (cov=2) and Solid LINE (cov=1) continue to
    # use the 40%+ field pending the separate LINE-methodology decision.
    sparse_truth_threshold: float = 0.25
    medium_truth_threshold: float = 0.40

    # Grade cutoffs on the hit/forecast area ratio.
    verified_well_cutoff: float = 0.50
    verified_close_cutoff: float = 0.20

    # A retained Sparse component becomes a Candidate Miss when the forecast
    # captured less than this much of it. Numerically equal to verified_close_cutoff, but a separate
    # field on purpose: it asks the opposite question (how much of a TRUTH blob
    # the forecast captured, not how much of a FORECAST truth filled), so the
    # two must be able to move independently.
    miss_capture_threshold: float = 0.20

    # Truth-field construction, in decimated (5x) grid cells.
    dilation_iterations: int = 1
    smoothing_size: int = 20

    # Clip truth to the verification domain (ARTCC boundaries unioned with
    # cmac_domain.geojson) before Candidate Miss review. On means convection
    # outside the scored area cannot be proposed as a candidate. Off is for comparison runs --
    # it is what the baselines captured before the domain existed.
    apply_domain_mask: bool = True


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
@functools.lru_cache(maxsize=1)
def verification_domain(artcc_path=ARTCC_PATH, cmac_path=CMAC_DOMAIN_PATH):
    """The scored area: ARTCC boundaries unioned with the CMAC supplement.

    Cached for the life of the process -- it is read from two files, dissolved
    and repaired, and none of that depends on the event, so rebuilding it per run
    would be pure waste. lru_cache keys on the paths, so a test can point at a
    different domain without disturbing the cached default.

    buffer(0) after the union dissolves the shared interior edges and repairs any
    self-intersection the two independently drawn boundaries introduce where they
    overlap.
    """
    artccs = load_artccs(artcc_path)
    with open(cmac_path, "r", encoding="utf-8") as f:
        cmac = gpd.GeoDataFrame.from_features(json.load(f)["features"], crs="EPSG:4326")
    return artccs.union_all().union(cmac.union_all()).buffer(0)


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
@dataclass(frozen=True)
class TCFCoverageSemantics:
    """Meaning of one feature-aware coverage encoding in the current TCF."""

    name: str
    coverage_min: float
    coverage_max: float
    measure: str


SUPPORTED_TCF_COVERAGE = {
    ('AREA', 2): TCFCoverageSemantics('Medium', 0.40, 0.74, 'areal'),
    ('AREA', 3): TCFCoverageSemantics('Sparse', 0.25, 0.39, 'areal'),
    ('LINE', 1): TCFCoverageSemantics('Solid', 0.75, 1.00, 'linear'),
}
KNOWN_TCF_COVERAGE_CODES = frozenset(
    coverage_code for _feature_type, coverage_code in SUPPORTED_TCF_COVERAGE)


def get_tcf_coverage_semantics(feat_type, cov_val):
    """Return current AWC semantics; never infer feature type from a bare code."""
    try:
        key = (str(feat_type).upper(), int(cov_val))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid TCF feature/coverage values: {feat_type!r} / {cov_val!r}"
        ) from exc
    try:
        return SUPPORTED_TCF_COVERAGE[key]
    except KeyError as exc:
        raise ValueError(
            f"unsupported TCF feature/coverage combination: {key[0]} coverage {key[1]}"
        ) from exc


def _coverage_label(feat_type, cov_val):
    """Return the feature-aware current TCF coverage label."""
    return get_tcf_coverage_semantics(feat_type, cov_val).name


# Half-width applied to a LINE feature, in degrees. Unchanged from the original
# literal -- widening or narrowing it, and whether a line should be graded on
# area at all, is a separate decision from the geometry fix below.
LINE_BUFFER_DEG = 0.15


@dataclass(frozen=True)
class TCFParseDiagnostic:
    """Structured reason one AREA/LINE record was excluded from verification."""

    record_index: int
    feature_type: str
    reason: str
    coverage_code: int | None = None
    declared_points: int | None = None
    available_coordinate_pairs: int | None = None

    @property
    def message(self):
        if self.reason == "unsupported_coverage_code":
            return f"Unsupported TCF coverage code: {self.coverage_code}"
        if self.reason == "unsupported_feature_coverage_combination":
            return ("Unsupported TCF feature/coverage combination: "
                    f"{self.feature_type} coverage {self.coverage_code}")
        return self.reason.replace("_", " ").capitalize()


def parse_iem_cow_text(text_data):
    """Parse valid legacy AREA/LINE records and attach rejection diagnostics.

    The returned GeoDataFrame API is unchanged. Rejected-record details live in
    ``gdf.attrs['parse_diagnostics']`` so callers that need visibility can inspect
    them without forcing existing callers to unpack a new return type.
    """
    records = []
    diagnostics = []

    # Strip ALL HTML tags so we just have raw text and numbers
    text_data = re.sub(r'<[^>]+>', ' ', text_data)

    # TCF/CCFP format:
    #   AREA: COV(0) CONF(1) GRW(2) TOPS(3) SPEED(4) DIR(5) NPTS(6) lat1 lon1 ...
    #   LINE: COV(0) NPTS(1) lat1 lon1 ...
    # Current AWC TCF encoding is feature-specific: AREA 2=Medium,
    # AREA 3=Sparse, and LINE 1=Solid.
    feat_blocks = re.findall(r'(AREA|LINE)\s+([\d\s]+)', text_data)

    for record_index, (feat_type, block) in enumerate(feat_blocks, start=1):
        parts = block.split()
        try:
            cov_val = int(parts[0])
            if cov_val not in KNOWN_TCF_COVERAGE_CODES:
                diagnostics.append(TCFParseDiagnostic(
                    record_index, feat_type, "unsupported_coverage_code",
                    coverage_code=cov_val))
                continue
            if (feat_type, cov_val) not in SUPPORTED_TCF_COVERAGE:
                diagnostics.append(TCFParseDiagnostic(
                    record_index, feat_type,
                    "unsupported_feature_coverage_combination",
                    coverage_code=cov_val))
                continue
            if feat_type == 'LINE':
                # LINE has no CONF/GRW/TOPS/SPEED/DIR fields
                num_points = int(parts[1])
                idx = 2
            else:
                num_points = int(parts[6])
                idx = 7
        except (IndexError, TypeError, ValueError):
            diagnostics.append(TCFParseDiagnostic(
                record_index, feat_type, "invalid_or_missing_metadata"))
            continue

        coordinate_tokens = parts[idx:]
        required_tokens = num_points * 2
        trailing_tokens = len(coordinate_tokens) - required_tokens
        # Archived AREA records may carry one trailing label-position pair after
        # the declared polygon vertices. LINE records do not. Any other remainder
        # is structural ambiguity, including a dangling coordinate value.
        valid_trailing = trailing_tokens in ((0, 2) if feat_type == 'AREA' else (0,))
        available_pairs = len(coordinate_tokens) // 2
        if num_points < 0 or len(coordinate_tokens) < required_tokens or not valid_trailing:
            diagnostics.append(TCFParseDiagnostic(
                record_index, feat_type, "coordinate_count_mismatch",
                coverage_code=cov_val, declared_points=num_points,
                available_coordinate_pairs=available_pairs))
            continue

        minimum_points = 2
        if num_points < minimum_points:
            diagnostics.append(TCFParseDiagnostic(
                record_index, feat_type, "insufficient_geometry_points",
                coverage_code=cov_val, declared_points=num_points,
                available_coordinate_pairs=available_pairs))
            continue

        coords = []
        for point_index in range(num_points):
            token_index = point_index * 2
            lat = float(coordinate_tokens[token_index]) / 10.0
            lon = float(coordinate_tokens[token_index + 1]) / 10.0
            if lon > 0:
                lon = -lon
            coords.append((lon, lat))

        try:
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
                else:
                    diagnostics.append(TCFParseDiagnostic(
                        record_index, feat_type, "empty_geometry",
                        coverage_code=cov_val, declared_points=num_points,
                        available_coordinate_pairs=available_pairs))
            elif len(coords) >= 2:
                poly = LineString(coords).buffer(LINE_BUFFER_DEG)
                records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})
        except (TypeError, ValueError) as exc:
            diagnostics.append(TCFParseDiagnostic(
                record_index, feat_type, f"geometry_error:{type(exc).__name__}",
                coverage_code=cov_val, declared_points=num_points,
                available_coordinate_pairs=available_pairs))

    result = (gpd.GeoDataFrame(records, crs="EPSG:4326") if records else
              gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"))
    result.attrs["parse_diagnostics"] = tuple(diagnostics)
    return result


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


def _timestamp_from_mrms_key(key):
    """Actual UTC observation timestamp encoded in an MRMS archive key."""
    if not key:
        return None
    match = re.search(r'(\d{8})-(\d{6})', key.split('/')[-1])
    if not match:
        return None
    return datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S')


def _observation_provenance(requested_time, echo_top_key, reflectivity_key):
    """Initial resolved-key provenance before download/read decisions."""
    reflectivity_time = _timestamp_from_mrms_key(reflectivity_key)
    echo_top_time = _timestamp_from_mrms_key(echo_top_key)
    return MRMSObservationProvenance(
        requested_time=requested_time,
        reflectivity_product=REFL_PRODUCT,
        echo_top_product=TOPS_PRODUCT,
        reflectivity_key=reflectivity_key,
        echo_top_key=echo_top_key,
        reflectivity_resolved=reflectivity_key is not None,
        echo_top_resolved=echo_top_key is not None,
        reflectivity_time=reflectivity_time,
        echo_top_time=echo_top_time,
        reflectivity_offset_seconds=(
            (reflectivity_time - requested_time).total_seconds()
            if reflectivity_time is not None else None),
        echo_top_offset_seconds=(
            (echo_top_time - requested_time).total_seconds()
            if echo_top_time is not None else None),
        product_separation_seconds=(
            abs((reflectivity_time - echo_top_time).total_seconds())
            if reflectivity_time is not None and echo_top_time is not None else None),
    )


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


def _cell_edges(centers):
    """Cell boundaries for a monotonic 1-D array of cell-center coordinates."""
    centers = np.asarray(centers, dtype=float)
    if centers.ndim != 1 or len(centers) < 2:
        raise ValueError("grid coordinates must be one-dimensional with at least two centers")
    differences = np.diff(centers)
    if not (np.all(differences > 0) or np.all(differences < 0)):
        raise ValueError("grid-center coordinates must be strictly monotonic")
    edges = np.empty(len(centers) + 1, dtype=float)
    edges[1:-1] = (centers[:-1] + centers[1:]) / 2
    edges[0] = centers[0] - differences[0] / 2
    edges[-1] = centers[-1] + differences[-1] / 2
    return edges


def _mask_cell_union(coverage_mask, lons, lats):
    """Union full footprints of True cells using row-wise run rectangles.

    Longitude/latitude arrays are MRMS cell centers. Midpoints define internal
    edges; the first/last half-spacing is extrapolated at raster boundaries.
    Corner-only contact remains disconnected (four-neighbor connectivity), while
    edge-sharing cells dissolve. Row runs avoid constructing one box per cell.
    """
    mask = np.asarray(coverage_mask, dtype=bool)
    if mask.shape != (len(lats), len(lons)):
        raise ValueError(
            f"coverage mask shape {mask.shape} does not match "
            f"latitude/longitude grid {(len(lats), len(lons))}")
    if not mask.any():
        return Polygon()

    lon_edges = _cell_edges(lons)
    lat_edges = _cell_edges(lats)
    runs = []
    for row_idx, row in enumerate(mask):
        padded = np.pad(row.astype(np.int8), (1, 1))
        changes = np.diff(padded)
        starts = np.flatnonzero(changes == 1)
        stops = np.flatnonzero(changes == -1)
        for start, stop in zip(starts, stops):
            runs.append(box(lon_edges[start], lat_edges[row_idx],
                            lon_edges[stop], lat_edges[row_idx + 1]))
    return union_all(runs)


def _geometry_point_mask(geometry, lon_grid, lat_grid):
    """Strict-interior membership for complete polygonal geometry.

    Shapely's vectorized predicate respects every polygon component and interior
    ring. Strict `contains` keeps the prior effective treatment of grid points on
    a polygon boundary as outside rather than changing the sampling policy.
    """
    return contains_xy(geometry, lon_grid, lat_grid)


def extract_tcf_polygons(coverage_mask, lons, lats, min_area_m2=0, domain=None):
    """Turns a binary coverage mask into dissolved cell-footprint truth polygons.

    `domain`, when given, is the verification domain from
    verification_domain(). Truth is clipped to it BEFORE min_area_m2 is applied
    -- see the comment at the clip.
    """
    geometry = _mask_cell_union(coverage_mask, lons, lats)
    if geometry.is_empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    polygons = (list(geometry.geoms) if isinstance(geometry, MultiPolygon)
                else [geometry])
    gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")

    if domain is not None:
        # ORDER OF OPERATIONS: clip to the domain FIRST, then apply min_area_m2
        # below. Filtering first would measure each blob at its full extent and
        # keep anything that cleared the floor, including a blob that is 95% in
        # Saskatchewan and only clips into a corner of the scored area -- it
        # would survive on area it does not have, and then be graded. Clipping
        # first means the floor is applied to the part that actually counts.
        #
        # buffer(0) after the intersection cleans the slivers and
        # zero-width spikes clipping leaves along the boundary, and normalises a
        # GeometryCollection (a clip can return lines or points where a contour
        # runs tangent to the edge) back to something polygonal.
        gdf = gpd.GeoDataFrame(geometry=gdf.geometry.intersection(domain).buffer(0),
                               crs="EPSG:4326")
        gdf = gdf[~gdf.is_empty]
        if gdf.empty:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    gdf_m = gdf.to_crs(PHYSICAL_AREA_CRS)
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
    'approved_for_report': 'boolean',
    'reportable': 'boolean',
    'forecast_capture_fraction': 'Float64',
    'sparse_area_km2': 'Float64',
    'medium_core_area_km2': 'Float64',
    'medium_core_fraction': 'Float64',
    'contains_medium_core': 'boolean',
    'medium_area_km2': 'Float64',
    'medium_capture_fraction': 'Float64',
    'parent_sparse_component_id': 'Int64',
}


def build_review_table(gdf_graded_fcst, gdf_graded_miss, gdf_artcc,
                       params=GradingParams(), gdf_medium_core_flags=None):
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
                'top_kft': (None if row.top is None or pd.isna(row.top)
                            else float(row.top)),
                'boundary': bool(is_boundary(coverage_fraction, params)),
                'approved_for_report': True,
                'reportable': True,
            })

    if not gdf_graded_miss.empty:
        for _, row in gdf_graded_miss.iterrows():
            rows.append({
                'idx': int(row.idx),
                'kind': 'candidate_miss',
                'category': 'Candidate Miss',
                'coverage_code': None,
                'feat_type': None,
                'artccs': get_artccs(row.geometry, gdf_artcc),
                'coverage_fraction': None,
                'top_kft': None,
                'boundary': False,
                'approved_for_report': False,
                'reportable': True,
                'forecast_capture_fraction': getattr(row, 'forecast_capture_fraction', None),
                'sparse_area_km2': getattr(row, 'sparse_area_km2', None),
                'medium_core_area_km2': getattr(row, 'medium_core_area_km2', None),
                'medium_core_fraction': getattr(row, 'medium_core_fraction', None),
                'contains_medium_core': getattr(row, 'contains_medium_core', False),
            })

    if gdf_medium_core_flags is not None and not gdf_medium_core_flags.empty:
        for _, row in gdf_medium_core_flags.iterrows():
            rows.append({
                'idx': int(row.idx),
                'kind': 'medium_core_review_flag',
                'category': 'Medium-core Review',
                'artccs': get_artccs(row.geometry, gdf_artcc),
                'boundary': False,
                'approved_for_report': False,
                'reportable': False,
                'medium_area_km2': row.medium_area_km2,
                'medium_capture_fraction': row.medium_capture_fraction,
                'parent_sparse_component_id': row.parent_sparse_component_id,
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
        # Reviewer cues have no promotion path to FAA text. This kind check is
        # intentionally independent of editable approval/reportable columns.
        if row.kind == 'medium_core_review_flag':
            continue
        if not row.approved_for_report:
            continue
        if row.kind == 'candidate_miss':
            doc_report["Missed:"].append(
                f"{row.artccs} - Missed (Area M{row.idx})")
            continue

        top_str = (f" [Top: {row.top_kft:.1f} kft]"
                   if pd.notna(row.top_kft) and row.top_kft > 0 else "")
        cov_label = _coverage_label(row.feat_type, row.coverage_code)
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


def _individual_geometries(gdf):
    """Return disconnected polygon components as independent geometries."""
    if gdf is None or gdf.empty or gdf.is_empty.all():
        return []
    exploded = gdf.explode(index_parts=False).reset_index(drop=True)
    return [geom for geom in exploded.geometry if geom is not None and not geom.is_empty]


def _build_miss_review_cues(gdf_sparse, gdf_medium, forecast_union,
                            miss_capture_threshold):
    """Build physical-area Candidate Misses and reviewer-only Medium cues.

    Every disconnected Sparse and Medium component is evaluated separately.
    Area is factual reviewer context, never an eligibility threshold. Medium
    flags are suppressed when their parent Sparse component is already a
    Candidate Miss, because that candidate carries the embedded-density facts.
    """
    sparse_geoms = sorted(
        _individual_geometries(gdf_sparse), key=lambda geom: geom.centroid.x,
        reverse=True)
    medium_geoms = sorted(
        _individual_geometries(gdf_medium), key=lambda geom: geom.centroid.x,
        reverse=True)
    forecast_m = gpd.GeoSeries(
        [forecast_union], crs="EPSG:4326").to_crs(PHYSICAL_AREA_CRS).iloc[0]
    sparse_m = (list(gpd.GeoSeries(sparse_geoms, crs="EPSG:4326")
                     .to_crs(PHYSICAL_AREA_CRS)) if sparse_geoms else [])
    medium_m = (list(gpd.GeoSeries(medium_geoms, crs="EPSG:4326")
                     .to_crs(PHYSICAL_AREA_CRS)) if medium_geoms else [])
    medium_union_m = union_all(medium_m) if medium_m else Polygon()

    sparse_context = []
    candidates = []
    for component_id, (geom, geom_m) in enumerate(
            zip(sparse_geoms, sparse_m), start=1):
        area_m2 = geom_m.area
        capture = (geom_m.intersection(forecast_m).area / area_m2
                   if area_m2 > 0 else 0.0)
        core_area_m2 = geom_m.intersection(medium_union_m).area
        is_candidate = capture < miss_capture_threshold
        context = {
            "component_id": component_id,
            "geometry_m": geom_m,
            "is_candidate": is_candidate,
        }
        sparse_context.append(context)
        if is_candidate:
            candidates.append({
                "geometry": geom,
                "category": "Candidate Miss",
                "color": "red",
                "forecast_capture_fraction": capture,
                "sparse_area_km2": area_m2 / 1_000_000.0,
                "medium_core_area_km2": core_area_m2 / 1_000_000.0,
                "medium_core_fraction": (core_area_m2 / area_m2
                                         if area_m2 > 0 else 0.0),
                "contains_medium_core": core_area_m2 > 0.0,
                "sparse_component_id": component_id,
            })

    flags = []
    for geom, geom_m in zip(medium_geoms, medium_m):
        area_m2 = geom_m.area
        capture = (geom_m.intersection(forecast_m).area / area_m2
                   if area_m2 > 0 else 0.0)
        parent = None
        parent_overlap = 0.0
        for context in sparse_context:
            overlap = geom_m.intersection(context["geometry_m"]).area
            if overlap > parent_overlap:
                parent, parent_overlap = context, overlap
        if (capture < miss_capture_threshold
                and not (parent is not None and parent["is_candidate"])):
            flags.append({
                "geometry": geom,
                "category": "Medium-core Review",
                "color": "magenta",
                "medium_area_km2": area_m2 / 1_000_000.0,
                "medium_capture_fraction": capture,
                "parent_sparse_component_id": (
                    parent["component_id"] if parent is not None else None),
                "reportable": False,
            })
    return candidates, flags


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

    refl_lons = ds_r.longitude[::step].values
    refl_lons = np.where(refl_lons > 180, refl_lons - 360, refl_lons)
    refl_lats = ds_r.latitude[::step].values

    compatible = (curr_tops.shape == curr_refl.shape
                  and np.array_equal(lons, refl_lons)
                  and np.array_equal(lats, refl_lats))

    ds_t.close()
    ds_r.close()
    del ds_t, ds_r
    if not compatible:
        raise ValueError("incompatible reflectivity and echo-top grids")
    return curr_tops, curr_refl, lons, lats


def _same_grid(shape, lons, lats, reference_shape, reference_lons, reference_lats):
    """Exact compatibility check used before arrays enter the running maximum."""
    return (shape == reference_shape
            and np.array_equal(lons, reference_lons)
            and np.array_equal(lats, reference_lats))


def _pair_qualifying_mask(reflectivity, echo_tops):
    """TCF criteria evaluated within one usable nominal observation pair."""
    if reflectivity.shape != echo_tops.shape:
        raise ValueError("incompatible reflectivity and echo-top grids")
    return ((reflectivity >= 40.0) & (echo_tops >= 25.0))


def build_composite(valid_dt, log=_log,
                    window_minutes=COMPOSITE_WINDOW_MINUTES,
                    cadence_minutes=COMPOSITE_CADENCE_MINUTES,
                    step=COMPOSITE_STEP,
                    max_workers=COMPOSITE_MAX_WORKERS,
                    dest_dir="mrms_data",
                    with_display=False,
                    with_provenance=False):
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

    Returns (max_tops, max_refl, qualifying_mask, lons, lats), optionally followed by the
    DisplayRaster and/or MRMSCompositeProvenance requested by the corresponding
    flags. The paired mask is intentionally mandatory in the default return so
    production callers cannot continue without carrying Decision 1A evidence.

    Provenance is descriptive only: it assigns no meteorological data-quality
    category and changes no threshold or temporal compositing rule.
    """
    time_offsets = scan_offsets(window_minutes, cadence_minutes)

    # Resolve every scan first. Each distinct (product, UTC day) is listed once
    # and cached, so this is 2 listings for a window inside one day and 4 across
    # a midnight rollover -- not one per scan per product.
    s3 = _s3_client()
    plan = []
    for offset in time_offsets:
        scan_dt = valid_dt + timedelta(minutes=offset)
        tops_key = _resolve_scan_key(TOPS_PRODUCT, scan_dt, s3=s3)
        refl_key = _resolve_scan_key(REFL_PRODUCT, scan_dt, s3=s3)
        plan.append(_observation_provenance(scan_dt, tops_key, refl_key))

    # Deduplicate before fetching. Adjacent offsets can resolve to the same
    # archived file when the archive is sparse; downloading a key twice would be
    # wasted work sequentially and a write race in parallel, since both would
    # target the same path.
    wanted = sorted({key for record in plan
                     for key in (record.echo_top_key, record.reflectivity_key)
                     if key})
    log(f"Fetching {len(wanted)} MRMS files for {len(time_offsets)} scans "
        f"({max_workers} at a time)...")

    paths = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(wanted))) as pool:
            for key, path in zip(wanted, pool.map(
                    lambda k: _download_key(k, dest_dir, s3=s3), wanted)):
                paths[key] = path

    max_tops, max_refl, qualifying_mask = None, None, None
    lons, lats = None, None
    disp_tops, disp_refl = None, None
    disp_lons, disp_lats = None, None

    completed_plan = []
    for record in plan:
        tops_file = paths.get(record.echo_top_key)
        refl_file = paths.get(record.reflectivity_key)
        tops_downloaded = bool(tops_file)
        refl_downloaded = bool(refl_file)
        record = replace(
            record,
            echo_top_downloaded=tops_downloaded,
            reflectivity_downloaded=refl_downloaded,
            both_products_available=tops_downloaded and refl_downloaded,
        )
        log(f"Pulling MRMS for {record.requested_time.strftime('%H:%MZ')}...")

        if not record.echo_top_resolved and not record.reflectivity_resolved:
            completed_plan.append(replace(record, exclusion_reason="both_unavailable"))
            continue
        if not record.echo_top_resolved:
            completed_plan.append(replace(record, exclusion_reason="echo_top_unavailable"))
            continue
        if not record.reflectivity_resolved:
            completed_plan.append(replace(record, exclusion_reason="reflectivity_unavailable"))
            continue
        if not tops_downloaded and not refl_downloaded:
            completed_plan.append(replace(record, exclusion_reason="both_download_failed"))
            continue
        if not tops_downloaded:
            completed_plan.append(replace(record, exclusion_reason="echo_top_download_failed"))
            continue
        if not refl_downloaded:
            completed_plan.append(replace(record, exclusion_reason="reflectivity_download_failed"))
            continue

        try:
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

            else:
                curr_tops, curr_refl, curr_lons, curr_lats = _read_scan_arrays(
                    tops_file, refl_file, step)

            if lons is not None and not _same_grid(
                    curr_tops.shape, curr_lons, curr_lats,
                    max_tops.shape, lons, lats):
                completed_plan.append(replace(
                    record, grid_compatible=False,
                    exclusion_reason="incompatible_grid"))
                del curr_tops, curr_refl
                if with_display:
                    del full_tops, full_refl
                continue

            if with_display:
                if disp_tops is None:
                    disp_tops, disp_refl = full_tops, full_refl
                    disp_lons, disp_lats = full_lons, full_lats
                else:
                    disp_tops = np.maximum(disp_tops, full_tops)
                    disp_refl = np.maximum(disp_refl, full_refl)
                del full_tops, full_refl

            if lons is None:
                lons, lats = curr_lons, curr_lats

            if max_tops is None:
                max_tops, max_refl = curr_tops, curr_refl
                qualifying_mask = _pair_qualifying_mask(curr_refl, curr_tops)
            else:
                max_tops = np.maximum(max_tops, curr_tops)
                max_refl = np.maximum(max_refl, curr_refl)
                qualifying_mask |= _pair_qualifying_mask(curr_refl, curr_tops)

            completed_plan.append(replace(
                record, grid_compatible=True, used=True, exclusion_reason=None))
            del curr_tops, curr_refl
            gc.collect()
        except ValueError as exc:
            reason = ("incompatible_grid" if "incompatible" in str(exc).lower()
                      else f"read_failure:{type(exc).__name__}")
            completed_plan.append(replace(
                record, grid_compatible=False if reason == "incompatible_grid" else None,
                exclusion_reason=reason))
        except Exception as exc:
            completed_plan.append(replace(
                record, exclusion_reason=f"read_failure:{type(exc).__name__}"))

    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)

    provenance = MRMSCompositeProvenance.from_observations(completed_plan)
    if max_tops is None:
        raise MRMSCompositeUnavailableError(
            f"No MRMS scans available in the +/-{window_minutes} min "
            f"window around {valid_dt}", provenance)

    output = [max_tops, max_refl, qualifying_mask, lons, lats]
    if with_display:
        output.append(DisplayRaster(disp_tops, disp_refl, disp_lons, disp_lats))
    if with_provenance:
        output.append(provenance)
    return tuple(output)


def run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                     valid_dt, issuance_hour, lead_time, gdf_artcc,
                     params=GradingParams(), *, qualifying_mask):
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
    qualifying_mask = np.asarray(qualifying_mask, dtype=bool)
    if qualifying_mask.shape != max_refl.shape:
        raise ValueError("qualifying_mask must match the MRMS composite grid")

    # Numeric maxima remain diagnostics. Truth qualification is the Boolean
    # union of same-slot reflectivity/top conjunctions built by build_composite.
    valid_convection = qualifying_mask
    top_verif_matrix = np.zeros_like(max_tops, dtype=int)
    top_verif_matrix[valid_convection & (max_tops >= 25) & (max_tops < 30)] = 1
    top_verif_matrix[valid_convection & (max_tops >= 30) & (max_tops < 35)] = 2
    top_verif_matrix[valid_convection & (max_tops >= 35) & (max_tops < 40)] = 3
    top_verif_matrix[valid_convection & (max_tops >= 40)] = 4

    raw_cores = qualifying_mask
    buffered_cores = binary_dilation(raw_cores, iterations=params.dilation_iterations)
    coverage_fraction = uniform_filter(buffered_cores.astype(float), size=params.smoothing_size)

    # The scored area. None disables clipping entirely, which is what the
    # baselines captured before the domain mask existed.
    domain = verification_domain() if params.apply_domain_mask else None

    # Forecast scoring and review cues use every processed component. Physical
    # area is metadata, not an eligibility floor.
    gdf_sparse = extract_tcf_polygons((coverage_fraction >= params.sparse_truth_threshold).astype(int),
                                      lons, lats, min_area_m2=0,
                                      domain=domain)
    # Medium AREA (cov=2) and Solid LINE (cov=1) forecasts continue to use the
    # 40%+ truth field. LINE scoring policy is intentionally unchanged here.
    gdf_medium_truth = extract_tcf_polygons((coverage_fraction >= params.medium_truth_threshold).astype(int),
                                            lons, lats, min_area_m2=0,
                                            domain=domain)
    del coverage_fraction, raw_cores, buffered_cores
    gc.collect()

    truth_sparse_union = gdf_sparse.union_all() if not gdf_sparse.is_empty.all() else Polygon()
    truth_medium_union = gdf_medium_truth.union_all() if not gdf_medium_truth.is_empty.all() else Polygon()
    fcst_union = gdf_forecast.union_all() if not gdf_forecast.is_empty.all() else Polygon()

    # Canonical geometries remain EPSG:4326 for mapping, ARTCC attribution,
    # echo-top sampling and reporting. These projected copies exist only for
    # physical area measurement. Project each reused union once rather than once
    # per forecast feature.
    truth_sparse_union_m = gpd.GeoSeries(
        [truth_sparse_union], crs="EPSG:4326").to_crs(PHYSICAL_AREA_CRS).iloc[0]
    truth_medium_union_m = gpd.GeoSeries(
        [truth_medium_union], crs="EPSG:4326").to_crs(PHYSICAL_AREA_CRS).iloc[0]
    graded_forecasts = []

    # Preserve each source geometry alongside exploded grading components. Area
    # grading remains component-based exactly as before; echo-top sampling uses
    # the complete source geometry so every MultiPolygon component contributes.
    fcst_iter = ((gdf_forecast.assign(_echo_geometry=gdf_forecast.geometry)
                  .explode(index_parts=False).reset_index(drop=True))
                 if not gdf_forecast.is_empty.all() else gpd.GeoDataFrame(geometry=[]))
    fcst_iter_m = (fcst_iter.to_crs(PHYSICAL_AREA_CRS)
                   if not fcst_iter.empty else gpd.GeoDataFrame(geometry=[]))
    for idx, row in fcst_iter.iterrows():
        poly = row.geometry
        if poly.is_empty:
            continue

        row_cov = row['coverage'] if 'coverage' in fcst_iter.columns else 3
        # Sparse AREA (3) uses 25%+ truth; Medium AREA (2) and Solid LINE (1)
        # continue to use 40%+ truth. This task changes semantics, not scoring.
        truth_union_m = (truth_sparse_union_m if row_cov == 3
                         else truth_medium_union_m)

        poly_m = fcst_iter_m.geometry.iloc[idx]
        fcst_area = poly_m.area
        hit_area = poly_m.intersection(truth_union_m).area
        coverage = hit_area / fcst_area if fcst_area > 0 else 0

        echo_geometry = row['_echo_geometry']
        min_lon, min_lat, max_lon, max_lat = echo_geometry.bounds
        lat_mask, lon_mask = (lats >= min_lat) & (lats <= max_lat), (lons >= min_lon) & (lons <= max_lon)
        subset_tops, subset_refl = max_tops[lat_mask][:, lon_mask], max_refl[lat_mask][:, lon_mask]
        lon_grid, lat_grid = np.meshgrid(lons[lon_mask], lats[lat_mask])

        in_poly_mask = _geometry_point_mask(echo_geometry, lon_grid, lat_grid)
        # This retained 90th-percentile diagnostic uses numerical window maxima;
        # it is not the pair-first mask that seeds verification truth.
        valid_tops = subset_tops[in_poly_mask & (subset_refl >= 40) & (subset_tops >= 25)]

        available = in_poly_mask & np.isfinite(subset_refl) & np.isfinite(subset_tops)
        if in_poly_mask.any() and not available.any():
            actual_top_kft = None
        else:
            # Six qualifying cells remain the current minimum. Insufficient
            # evidence is unavailable, not a meteorological zero.
            actual_top_kft = np.percentile(valid_tops, 90) if len(valid_tops) > 5 else None

        cat, color = ("Verified Well", 'lime') if coverage >= params.verified_well_cutoff else \
                     ("Verified Close", 'yellow') if coverage >= params.verified_close_cutoff else \
                     ("Overforecasted", 'orange')
        row_feat = row['feat_type'] if 'feat_type' in fcst_iter.columns else 'AREA'
        graded_forecasts.append({'geometry': poly, 'category': cat, 'color': color,
                                 'idx': idx + 1, 'top': actual_top_kft,
                                 'coverage': row_cov, 'feat_type': row_feat,
                                 'coverage_fraction': coverage})

    graded_misses, medium_core_flags = _build_miss_review_cues(
        gdf_sparse, gdf_medium_truth, fcst_union, params.miss_capture_threshold)

    # ORDER EAST -> WEST: east = larger (least-negative) longitude, so sort centroid.x
    # descending. Renumber after sorting so BOTH the map labels and the report read E->W.
    graded_forecasts.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
    for i, r in enumerate(graded_forecasts, start=1):
        r['idx'] = i
    graded_misses.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
    for i, r in enumerate(graded_misses, start=1):
        r['idx'] = i
    medium_core_flags.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
    for i, r in enumerate(medium_core_flags, start=1):
        r['idx'] = i

    gdf_graded_fcst = gpd.GeoDataFrame(graded_forecasts, crs="EPSG:4326") if graded_forecasts else gpd.GeoDataFrame(geometry=[])
    gdf_graded_miss = gpd.GeoDataFrame(graded_misses, crs="EPSG:4326") if graded_misses else gpd.GeoDataFrame(geometry=[])
    gdf_medium_core_flags = (gpd.GeoDataFrame(medium_core_flags, crs="EPSG:4326")
                             if medium_core_flags else gpd.GeoDataFrame(geometry=[]))

    # Two stages: everything geometric collapses into the review table, then the
    # report is formatted from that table alone. An editable table drops in
    # between these two lines.
    review_table = build_review_table(
        gdf_graded_fcst, gdf_graded_miss, gdf_artcc, params=params,
        gdf_medium_core_flags=gdf_medium_core_flags)
    report_out = build_report(review_table, valid_dt, issuance_hour, lead_time)

    return {
        'lons': lons, 'lats': lats,
        'top_verif_matrix': top_verif_matrix,
        'gdf_graded_fcst': gdf_graded_fcst,
        'gdf_graded_miss': gdf_graded_miss,
        'gdf_medium_core_flags': gdf_medium_core_flags,
        'gdf_sparse': gdf_sparse,
        'graded_forecasts': graded_forecasts,
        'graded_misses': graded_misses,
        'medium_core_review_flags': medium_core_flags,
        'review_table': review_table,
        'report_text': report_out,
        'valid_dt': valid_dt,
    }


def run_verification_legacy_independent_max(
        gdf_forecast, max_tops, max_refl, lons, lats,
        valid_dt, issuance_hour, lead_time, gdf_artcc,
        params=GradingParams()):
    """Replay pre-Decision-1A artifacts that lack a paired qualifying mask.

    This deliberately reconstructs the retired independent-max conjunction and
    must never be used by production verification. Its explicit name prevents a
    frozen legacy replay from being confused with the approved methodology.
    """
    legacy_mask = ((max_refl >= 40.0) & (max_tops >= 25.0))
    return run_verification(
        gdf_forecast, max_tops, max_refl, lons, lats,
        valid_dt, issuance_hour, lead_time, gdf_artcc, params,
        qualifying_mask=legacy_mask)



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
#  3. FIXED -- truth raster geometry (extract_tcf_polygons). Qualifying cells are
#     represented by their complete midpoint-derived footprints and unioned.
#     Boundaries now fall on cell edges, including half-spacing extrapolation at
#     the raster boundary; no contour coordinates are truncated.
#
#  4. FIXED -- scoring truth is no longer simplified. Cell-footprint unions
#     preserve holes and components directly, so there is no post-simplification
#     validity gap in extract_tcf_polygons.
#
#  5. FIXED -- interior rings and multipart echo-top sampling (run_verification).
#     Shapely contains_xy evaluates the complete source geometry, excluding every
#     hole and including every MultiPolygon component with strict-interior
#     boundary behavior.
#
#  6. FIXED -- degree-space areas (run_verification). Forecast/hit and
#     truth/captured ratios are measured on EPSG:5070 copies. The canonical
#     geometries remain EPSG:4326, and which geometries participate (including
#     the whole-forecast denominator and current miss union) is unchanged.
#
#  7. Inconsistent default coverage code. The grading loop defaults a missing
#     'coverage' column to 3 (run_verification) while build_report defaults it to 25
#     Both happen to land on "Sparse" via _coverage_label's
#     fall-through, so the disagreement is currently invisible -- but only by
#     accident.
#
#  8. Asymmetric miss test (run_verification). Hits are graded against a
#     coverage-dependent truth union (25% for Sparse AREA, 40% for Medium AREA
#     and Solid LINE),
#     but misses are always measured against the 25% sparse truth and against
#     the union of ALL forecast polygons regardless of their coverage code.
#
#  9. Dead index assignment (run_verification). Each graded record gets
#     'idx': idx + 1, which is unconditionally overwritten by the E->W renumber
#     a few lines later.
#
# 10. FIXED -- silent scan drops (build_composite). Every nominal request now
#     carries typed per-product resolution/download/timestamp provenance, a used
#     flag and an exclusion reason. A partial composite is still allowed because
#     the Normal/Review Required/Insufficient thresholds remain an open
#     methodology decision, but it is no longer indistinguishable from a complete
#     one in the review UI.
#
# 11. FIXED -- grid-shape assumption across scans (build_composite). Product-pair
#     coordinates/shapes and each used scan's coordinates/shape are checked
#     before np.maximum; an incompatible observation is excluded and recorded in
#     provenance. No resampling or acceptability policy is introduced.
#
# 12. FIXED -- unavailable and insufficient echo-top samples are nullable:
#     fewer than 6 qualifying cells yields actual_top_kft = None, which survives
#     the nullable review table. Numeric zero remains a distinct representable
#     value rather than a missing-data sentinel.
#
# 13. PARTLY FIXED -- regex block-splitting remains legacy, but structural
#     validation now rejects unexpected numeric remainders instead of silently
#     absorbing them as feature coordinates. The one documented trailing AREA
#     label-position pair remains accepted. Rejected records carry typed
#     diagnostics without preventing neighboring valid features from parsing.
#
# 14. Hard-coded western hemisphere (parse_iem_cow_text). `if lon > 0: lon = -lon`
#     in the coordinate parser, and the lons > 180 wrap in the composite loop,
#     both assume CONUS. Fine today, wrong for any OCONUS product.
# ============================================================================
