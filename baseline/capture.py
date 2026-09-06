#!/usr/bin/env python3
"""Baseline capture: freeze each configured event's pipeline run to disk.

The pipeline itself lives in ``tcf_pipeline.py`` at the repo root -- this script
is only the event list plus the serialisation on top of it. It used to carry its
own transcribed copy of every pipeline function; that copy is gone, because a
harness that compares the pipeline against a second hand-maintained copy of the
pipeline proves nothing about the pipeline.

For each event in EVENTS, writes ``baseline/<event_id>/``:

    arrays.npz    max_tops, max_refl, qualifying_mask, lons, lats
    tcf_raw.txt   the raw IEM response text for the TCF product
    expected.json the versioned graded output and event metadata
    mrms_provenance.json the nominal-slot source manifest and summary

One more file belongs in each event directory but is NOT written here:

    pass_a_report.txt  the report text captured from the live Streamlit app for
                       this event ("pass A"). It comes out of the running
                       dashboard, so this script must never generate it -- a copy
                       written here would just be expected.json's report_text
                       under another name. `check.py --pass-a` requires it to
                       match expected.json's report_text byte for byte, after
                       trailing whitespace is trimmed from the end of both.

Usage:
    python baseline/capture.py                   # capture every event in EVENTS
    python baseline/capture.py 20260524_19Z_F04  # capture just these event ids

Requires network access (IEM archives + the public noaa-mrms-pds S3 bucket).
``baseline/check.py`` replays what this writes and needs no network at all.

WARNING: running this overwrites the frozen baselines. They are the before-
pictures a refactor is judged against; re-capturing them silently re-baselines
whatever the pipeline does today.
"""

import json
import os
import sys
from dataclasses import asdict
from datetime import date

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tcf_pipeline  # noqa: E402
from tcf_pipeline import (  # noqa: E402
    GradingParams,
    build_composite,
    compute_valid_dt,
    fetch_iem_cow_raw,
    get_artccs,
    is_boundary,
    load_artccs,
    parse_iem_cow_text,
    run_verification,
    _log,
)

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
    {"event_id": "20260324_13Z_F04", "date": date(2026, 3, 24), "issuance_hour": 13, "lead_time": 4,
     "note": "sparse/empty paths"},
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")

# --- Serialisation ----------------------------------------------------------
COVERAGE_DP = 4   # decimal places for coverage_fraction and geometry bounds
TOP_DP = 2        # decimal places for top_kft

# The boundary rule comes from the pipeline (it is derived from the grade
# cutoffs, which are GradingParams fields). check.py still keeps its own copy on
# purpose -- it must not import anything from the module it is checking -- so
# test_fixture.py asserts the two agree.
GRADE_CUTOFFS = (GradingParams().verified_well_cutoff, GradingParams().verified_close_cutoff)
BOUNDARY_WINDOW = tcf_pipeline.BOUNDARY_WINDOW


def _round_bounds(geom):
    return [round(float(b), COVERAGE_DP) for b in geom.bounds]


def _json_value(value):
    """Convert dataclass provenance to stable JSON-compatible values."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def provenance_payload(provenance):
    """Serialize the factual nominal-slot MRMS source manifest."""
    return _json_value(asdict(provenance))


def build_expected(event, valid_dt, results, gdf_artcc, methodology_version=None):
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
            'top_kft': (None if r['top'] is None
                        else round(float(r['top']), TOP_DP)),
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
            'forecast_capture_fraction': round(float(r['forecast_capture_fraction']), COVERAGE_DP),
            'sparse_area_km2': round(float(r['sparse_area_km2']), TOP_DP),
            'medium_core_area_km2': round(float(r['medium_core_area_km2']), TOP_DP),
            'medium_core_fraction': round(float(r['medium_core_fraction']), COVERAGE_DP),
            'contains_medium_core': bool(r['contains_medium_core']),
            'approved_for_report': False,
        })

    medium_core_review_flags = []
    for r in results['medium_core_review_flags']:
        medium_core_review_flags.append({
            'idx': int(r['idx']),
            'artccs': get_artccs(r['geometry'], gdf_artcc),
            'bounds': _round_bounds(r['geometry']),
            'medium_area_km2': round(float(r['medium_area_km2']), TOP_DP),
            'medium_capture_fraction': round(float(r['medium_capture_fraction']), COVERAGE_DP),
            'parent_sparse_component_id': r['parent_sparse_component_id'],
            'reportable': False,
        })

    categories = {}
    for p in polygons:
        categories[p['category']] = categories.get(p['category'], 0) + 1

    payload = {
        'event_id': event['event_id'],
        'date': event['date'].strftime('%Y-%m-%d'),
        'issuance_hour': event['issuance_hour'],
        'lead_time': event['lead_time'],
        'valid_time_hour': valid_dt.hour,
        'valid_dt': valid_dt.strftime('%Y-%m-%dT%H:%M:%S'),
        'report_text': results['report_text'],
        'polygons': polygons,
        'misses': misses,
        'medium_core_review_flags': medium_core_review_flags,
        'counts': {
            'polygons': len(polygons),
            'misses': len(misses),
            'medium_core_review_flags': len(medium_core_review_flags),
            'verified_well': categories.get('Verified Well', 0),
            'verified_close': categories.get('Verified Close', 0),
            'overforecasted': categories.get('Overforecasted', 0),
            'boundary': sum(1 for p in polygons if p.get('boundary')),
        },
    }
    if methodology_version is not None:
        payload = {'methodology_version': methodology_version, **payload}
    return payload




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

    (max_tops, max_refl, qualifying_mask, lons, lats,
     provenance) = build_composite(valid_dt, with_provenance=True)

    _log("Building Objective Truth Polygons...")
    results = run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                               valid_dt, event['issuance_hour'], event['lead_time'], gdf_artcc,
                               qualifying_mask=qualifying_mask)

    with open(os.path.join(out_dir, 'tcf_raw.txt'), 'w', encoding='utf-8') as f:
        f.write(raw_text)
    np.savez_compressed(os.path.join(out_dir, 'arrays.npz'),
                        max_tops=max_tops, max_refl=max_refl,
                        qualifying_mask=qualifying_mask, lons=lons, lats=lats)
    expected = build_expected(
        event, valid_dt, results, gdf_artcc,
        methodology_version=tcf_pipeline.METHODOLOGY_VERSION)
    with open(os.path.join(out_dir, 'expected.json'), 'w', encoding='utf-8') as f:
        json.dump(expected, f, indent=2, sort_keys=False)
        f.write('\n')
    with open(os.path.join(out_dir, 'mrms_provenance.json'), 'w', encoding='utf-8') as f:
        json.dump(provenance_payload(provenance), f, indent=2, sort_keys=False)
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
