#!/usr/bin/env python3
"""Fixture test for the baseline harness itself.

This does NOT test the TCF pipeline's meteorology -- it tests that check.py
actually detects the things it claims to detect. It builds a synthetic MRMS
composite and a synthetic TCF product in a temp directory, captures a baseline
from them, then perturbs that baseline in specific ways and asserts check.py
reports each one.

Runs offline in a few seconds; needs numpy/geopandas/shapely/scipy/skimage and
the repo's artcc1.geojson, but no cfgrib, no AWS, no network.

    python baseline/test_fixture.py

Exit status: 0 = all scenarios behaved as expected, 1 = a scenario failed.
"""

import contextlib
import copy
import dataclasses
import difflib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import date

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(1, os.path.join(REPO_ROOT, "baseline"))

import tcf_pipeline  # noqa: E402
from baseline import capture  # noqa: E402
import check as check_mod  # noqa: E402

# The pipeline under test. capture.py is imported too, but only for the
# serialisation layer it still owns (EVENTS, build_expected, the boundary rule).
PIPELINE = "tcf_pipeline"

_results = []


def scenario(name):
    """Decorator: run a scenario, record pass/fail, keep going after a failure."""
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            _results.append((name, False, str(exc)))
            print(f"FAIL  {name}\n      {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a scenario failure too
            _results.append((name, False, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {name}\n      {type(exc).__name__}: {exc}")
        else:
            _results.append((name, True, ""))
            print(f"ok    {name}")
        return fn
    return wrap


def run_check(argv):
    """Run check.main(argv), returning (exit_code, captured_stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = check_mod.main(argv)
    return rc, buf.getvalue()


def assert_in(needle, haystack, what):
    assert needle in haystack, f"{what}: expected to find {needle!r} in output:\n{haystack}"


def assert_not_in(needle, haystack, what):
    assert needle not in haystack, f"{what}: did NOT expect {needle!r} in output:\n{haystack}"


# --- fixture construction ---------------------------------------------------
def make_grid():
    """Fake decimated MRMS grid: 0.05 deg, latitudes descending like the real product."""
    lats = np.arange(45.0, 30.0, -0.05)
    lons = np.arange(-105.0, -85.0, 0.05)
    return lons, lats


def make_arrays(lons, lats, blobs):
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    max_refl = np.zeros(lon_grid.shape, dtype=np.float32)
    max_tops = np.zeros(lon_grid.shape, dtype=np.float32)
    for lo0, lo1, la0, la1, refl, top in blobs:
        m = (lon_grid >= lo0) & (lon_grid <= lo1) & (lat_grid >= la0) & (lat_grid <= la1)
        max_refl[m] = np.maximum(max_refl[m], refl)
        max_tops[m] = np.maximum(max_tops[m], top)
    return max_tops, max_refl


def make_wide_grid():
    """Grid that reaches past the verification domain's northern edge.

    make_grid() stops at 45N, entirely inside ARTCC territory, so nothing on it
    can be outside the domain -- a mask scenario built on it would pass whether
    the mask worked or not. This one runs to 54N so blobs can be placed in
    Canada.
    """
    lats = np.arange(54.0, 30.0, -0.05)
    lons = np.arange(-105.0, -85.0, 0.05)
    return lons, lats


def grade_blobs(lons, lats, blobs, raw, params=None):
    """Run one synthetic event end to end and return the results dict."""
    max_tops, max_refl = make_arrays(lons, lats, blobs)
    gdf = tcf_pipeline.parse_iem_cow_text(raw)
    valid_dt = tcf_pipeline.compute_valid_dt(date(2026, 5, 24), 19, 4)
    return tcf_pipeline.run_verification_legacy_independent_max(
        gdf, max_tops, max_refl, lons, lats, valid_dt, 19, 4, ARTCC,
        params=params or tcf_pipeline.GradingParams())


def truth_centroids(results):
    """(lat, lon) of every surviving truth polygon part, for locating blobs."""
    gs = results["gdf_sparse"]
    if gs.empty or gs.is_empty.all():
        return []
    return [(round(p.centroid.y, 2), round(p.centroid.x, 2))
            for p in gs.explode(index_parts=False).geometry]


def area_block(cov, pts):
    """One AREA feature in the AWIPS tenths-of-a-degree encoding the parser expects."""
    flat = " ".join(f"{int(round(la * 10))} {int(round(abs(lo) * 10))}" for lo, la in pts)
    return f"AREA \t{cov} 3 0 400 20 270 {len(pts)} {flat}\n"


MAIN_RAW = ("<html><pre>\nTCFNTA CFP02 TEST PRODUCT\n"
            # covers the big truth blob -> strong hit
            + area_block(3, [(-100.5, 36.5), (-95.5, 36.5), (-95.5, 41.5), (-100.5, 41.5)])
            # over open ground -> overforecast
            + area_block(2, [(-89.0, 43.0), (-86.0, 43.0), (-86.0, 44.5), (-89.0, 44.5)])
            # clips the big blob -> partial
            + area_block(3, [(-97.5, 39.5), (-92.5, 39.5), (-92.5, 42.5), (-97.5, 42.5)])
            + "</pre></html>\n")

EMPTY_RAW = "<html><pre>\nTCFNTA CFP02 TEST PRODUCT\nNO SIGNIFICANT CONVECTION EXPECTED\n</pre></html>\n"

MAIN_BLOBS = [
    (-100.0, -96.0, 37.0, 41.0, 52.0, 41.0),    # big truth blob, forecast over it
    (-93.0, -90.0, 33.0, 36.0, 47.0, 33.0),     # truth with no forecast -> miss
    (-102.0, -101.5, 31.0, 31.5, 45.0, 28.0),   # too small, dropped by the area filter
]

# --- domain-mask fixtures ---------------------------------------------------
# Blobs big enough to clear min_area_m2 on their own; the domain's northern edge
# is 49N here (ARTCC), west of the CMAC supplement's -90 western limit.
IN_DOMAIN_BLOB = (-100.0, -96.0, 37.0, 41.0, 52.0, 41.0)          # Kansas/Nebraska
OUT_OF_DOMAIN_BLOB = (-100.0, -96.0, 50.5, 53.5, 52.0, 41.0)      # Canada, north of 49N
# Mostly Saskatchewan, clipping into the scored area only along its southern
# edge: ~127,000 km2 whole, ~4,000 km2 after the clip, against a 15,000 floor.
STRADDLE_BLOB = (-100.0, -96.0, 49.2, 52.2, 52.0, 41.0)

# One forecast far from all of them: these scenarios are about truth survival,
# and a forecast polygon overlapping a blob would suppress it as a miss.
FAR_FORECAST = ("<html><pre>\nTCFNTA CFP02 DOMAIN TEST\n"
                + area_block(3, [(-89.0, 31.0), (-87.0, 31.0), (-87.0, 32.5), (-89.0, 32.5)])
                + "</pre></html>\n")




def write_event(root, event_id, raw, blobs, issuance_hour=19, lead_time=4):
    """Capture a synthetic event into root/<event_id>/ and return its expected payload."""
    out_dir = os.path.join(root, event_id)
    os.makedirs(out_dir, exist_ok=True)

    lons, lats = make_grid()
    max_tops, max_refl = make_arrays(lons, lats, blobs)

    with open(os.path.join(out_dir, "tcf_raw.txt"), "w", encoding="utf-8") as f:
        f.write(raw)
    np.savez_compressed(os.path.join(out_dir, "arrays.npz"),
                        max_tops=max_tops, max_refl=max_refl, lons=lons, lats=lats)

    event = {"event_id": event_id, "date": date(2026, 5, 24),
             "issuance_hour": issuance_hour, "lead_time": lead_time}
    valid_dt = tcf_pipeline.compute_valid_dt(event["date"], issuance_hour, lead_time)
    gdf_forecast = tcf_pipeline.parse_iem_cow_text(raw)
    results = tcf_pipeline.run_verification_legacy_independent_max(gdf_forecast, max_tops, max_refl, lons, lats,
                                            valid_dt, issuance_hour, lead_time, ARTCC)
    expected = capture.build_expected(event, valid_dt, results, ARTCC)
    write_expected(root, event_id, expected)
    return expected


def read_expected(root, event_id):
    with open(os.path.join(root, event_id, "expected.json"), encoding="utf-8") as f:
        return json.load(f)


def write_expected(root, event_id, payload):
    with open(os.path.join(root, event_id, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


# --- setup ------------------------------------------------------------------
ARTCC = tcf_pipeline.load_artccs()
TMP = tempfile.mkdtemp(prefix="tcf_baseline_fixture_")
check_mod.BASELINE_DIR = TMP

MAIN = "fixture_main"
EMPTY = "fixture_empty"

MAIN_EXPECTED = write_event(TMP, MAIN, MAIN_RAW, MAIN_BLOBS)
# No AREA blocks in the product and no convection on the grid: zero graded
# polygons and zero misses, the degenerate end of the pipeline.
EMPTY_EXPECTED = write_event(TMP, EMPTY, EMPTY_RAW, [])

print(f"fixture: {MAIN} -> {MAIN_EXPECTED['counts']}")
print(f"fixture: {EMPTY} -> {EMPTY_EXPECTED['counts']}")
print()

assert MAIN_EXPECTED["counts"]["polygons"] == 3, "fixture should grade 3 polygons"
assert MAIN_EXPECTED["counts"]["misses"] == 2, "fixture should find 2 visible candidates"
assert EMPTY_EXPECTED["counts"]["polygons"] == 0 and EMPTY_EXPECTED["counts"]["misses"] == 0


# --- scenarios --------------------------------------------------------------
@scenario("clean replay of both fixtures passes")
def _():
    rc, out = run_check(["--pipeline", PIPELINE])
    assert rc == 0, f"expected exit 0, got {rc}:\n{out}"
    assert_in(f"PASS  {MAIN}", out, "main fixture")
    assert_in(f"PASS  {EMPTY}", out, "empty fixture")


@scenario("empty event reports NOTHING GRADED rather than a bare PASS")
def _():
    rc, out = run_check(["--pipeline", PIPELINE, EMPTY])
    assert rc == 0, f"expected exit 0, got {rc}:\n{out}"
    # An all-zero baseline is legitimate but must not read like a normal green run.
    assert_in("NOTHING GRADED", out, "empty fixture")
    assert_in("(0 polygons, 0 misses", out, "empty fixture counts")


@scenario("scalar, bounds, count and report perturbations are each reported")
def _():
    exp = copy.deepcopy(MAIN_EXPECTED)
    top_polygon = next(p for p in exp["polygons"] if p["top_kft"] is not None)
    top_polygon["top_kft"] = round(top_polygon["top_kft"] + 3.5, 2)
    exp["polygons"][0]["coverage_fraction"] = round(exp["polygons"][0]["coverage_fraction"] - 0.11, 4)
    exp["polygons"][-1]["bounds"][0] = round(exp["polygons"][-1]["bounds"][0] - 0.5, 4)
    exp["counts"]["misses"] += 1
    exp["misses"].append({"idx": 99, "artccs": "ZZZ", "bounds": [0.0, 0.0, 1.0, 1.0]})
    exp["report_text"] = exp["report_text"].replace("Missed:", "Missing:")
    write_expected(TMP, MAIN, exp)
    try:
        rc, out = run_check(["--pipeline", PIPELINE, MAIN])
    finally:
        write_expected(TMP, MAIN, MAIN_EXPECTED)

    assert rc == 1, f"expected exit 1, got {rc}:\n{out}"
    assert_in("top_kft", out, "scalar diff")
    assert_in("coverage_fraction", out, "scalar diff")
    assert_in("bounds: minx", out, "bounds diff names the component")
    assert_in("counts.misses", out, "count diff")
    assert_in("misses[2]: missing", out, "missing entry")
    assert_in("report_text: differs", out, "report diff")
    assert_in("-Missing:", out, "report unified diff body")


@scenario("(3a) grade-boundary flip reports BOTH the fraction delta and the category change")
def _():
    # 0.5001 -> 0.4999 crosses the "Verified Well" / "Verified Close" cutoff.
    # Driven straight through the diff layer so the two values are exact.
    base = {"idx": 1, "coverage_code": 3, "feat_type": "AREA", "artccs": "ZKC",
            "top_kft": 38.0, "bounds": [-100.0, 36.0, -95.0, 41.0]}
    exp_poly = dict(base, coverage_fraction=0.5001, category="Verified Well", boundary=True)
    act_poly = dict(base, coverage_fraction=0.4999, category="Verified Close", boundary=True)

    meta = {"event_id": MAIN, "date": "2026-05-24", "issuance_hour": 19, "lead_time": 4,
            "valid_time_hour": 23, "valid_dt": "2026-05-24T23:00:00", "report_text": "",
            "misses": [], "counts": {"polygons": 1, "misses": 0, "verified_well": 1,
                                     "verified_close": 0, "overforecasted": 0, "boundary": 1}}
    expected = dict(meta, polygons=[exp_poly])
    actual = dict(meta, polygons=[act_poly],
                  counts=dict(meta["counts"], verified_well=0, verified_close=1))

    diffs = check_mod.diff_expected(expected, actual, strict=False)
    text = "\n".join(diffs)
    print(text)

    frac = [d for d in diffs if "coverage_fraction" in d]
    cat = [d for d in diffs if ".category" in d]
    assert frac, f"the 0.0002 fraction delta must be reported:\n{text}"
    assert cat, f"the resulting category change must be reported:\n{text}"
    assert_in("expected 0.5001, got 0.4999", frac[0], "fraction delta")
    assert_in("Verified Well", cat[0], "category change")
    assert_in("Verified Close", cat[0], "category change")
    # The boundary flag is what distinguishes float noise from a real regression.
    assert_in("note:", text, "boundary annotation")
    assert_in("0.50 cutoff", text, "boundary annotation names the cutoff")
    # Both category counts moved; neither may be swallowed.
    assert_in("counts.verified_well", text, "count diff")
    assert_in("counts.verified_close", text, "count diff")


@scenario("(4) polygons near a grade cutoff are flagged boundary, others are not")
def _():
    assert capture.GRADE_CUTOFFS == check_mod.GRADE_CUTOFFS, \
        "GRADE_CUTOFFS drifted between capture.py and check.py"
    assert capture.BOUNDARY_WINDOW == check_mod.BOUNDARY_WINDOW, \
        "BOUNDARY_WINDOW drifted between capture.py and check.py"
    # Values sit clearly inside or outside the +/-0.005 window; exactly-0.005
    # offsets are skipped because they are not representable (0.205 - 0.2 is
    # 0.0050000000000000044 in binary float, so the comparison there is a coin toss).
    for cf, want in [(0.5, True), (0.4951, True), (0.4945, False),
                     (0.5049, True), (0.5055, False),
                     (0.2, True), (0.2049, True), (0.2055, False),
                     (0.1951, True), (0.1945, False),
                     (0.35, False), (None, False)]:
        assert capture.is_boundary(cf) is want, f"capture.is_boundary({cf}) should be {want}"
        assert check_mod.is_boundary(cf) is want, f"check.is_boundary({cf}) should be {want}"
    # And the flag is emitted only when true, so ordinary polygons stay clean.
    for poly in MAIN_EXPECTED["polygons"]:
        if not capture.is_boundary(poly["coverage_fraction"]):
            assert "boundary" not in poly, f"polygon {poly['idx']} should not carry a boundary flag"


@scenario("(3b) expected-empty vs actual-non-empty FAILS instead of passing on an empty loop")
def _():
    exp = copy.deepcopy(MAIN_EXPECTED)
    exp["polygons"] = []
    exp["misses"] = []
    exp["counts"] = {"polygons": 0, "misses": 0, "verified_well": 0,
                     "verified_close": 0, "overforecasted": 0, "boundary": 0}
    write_expected(TMP, MAIN, exp)
    try:
        rc, out = run_check(["--pipeline", PIPELINE, MAIN])
    finally:
        write_expected(TMP, MAIN, MAIN_EXPECTED)

    assert rc == 1, f"an empty expectation against a non-empty run must FAIL, got {rc}:\n{out}"
    assert_in("polygons: count expected 0, got 3", out, "polygon count diff")
    assert_in("misses: count expected 0, got 2", out, "miss count diff")
    assert_in("unexpected extra entry", out, "each extra entry is listed")
    assert_not_in(f"PASS  {MAIN}", out, "must not pass")


@scenario("(3b) expected-non-empty vs actual-empty FAILS with every missing entry listed")
def _():
    # Same expectation, but the product now parses to nothing -> the run grades nothing.
    raw_path = os.path.join(TMP, MAIN, "tcf_raw.txt")
    with open(raw_path, encoding="utf-8") as f:
        original = f.read()
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(EMPTY_RAW)
    try:
        rc, out = run_check(["--pipeline", PIPELINE, MAIN])
    finally:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(original)

    assert rc == 1, f"an empty run against a non-empty expectation must FAIL, got {rc}:\n{out}"
    assert_in("polygons: count expected 3, got 0", out, "polygon count diff")
    assert out.count("missing (expected") >= 3, f"every dropped polygon must be listed:\n{out}"
    assert_in("report_text: differs", out, "the report collapses to None as well")


@scenario("(3b) an all-empty event diffs cleanly and does not crash")
def _():
    rc, out = run_check(["--pipeline", PIPELINE, EMPTY])
    assert rc == 0, f"expected exit 0, got {rc}:\n{out}"
    assert_not_in("Traceback", out, "no crash")
    assert_not_in("ERROR", out, "no error")


@scenario("(2) --pipeline is mandatory")
def _():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            rc = check_mod.main([])
        except SystemExit as exc:
            rc = exc.code
    assert rc == 2, f"missing --pipeline should exit 2, got {rc}"
    assert_in("--pipeline MODULE is required", err.getvalue(), "argparse error")
    assert_in("no fallback", err.getvalue().lower(), "error explains why")


@scenario("(2) a module missing a pipeline symbol is a hard error, not a fallthrough")
def _():
    stub_dir = os.path.join(TMP, "_stub")
    os.makedirs(stub_dir, exist_ok=True)
    # Everything except run_verification, which is the symbol most likely to be
    # mid-move during a refactor -- and exactly the one a fallback would hide.
    with open(os.path.join(stub_dir, "half_moved.py"), "w", encoding="utf-8") as f:
        f.write("from tcf_pipeline import (compute_valid_dt, parse_iem_cow_text,\n"
                "                          load_artccs, get_artccs)\n")
    sys.path.insert(0, stub_dir)
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                rc = check_mod.main(["--pipeline", "half_moved"])
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
                err.write(str(exc.code) if not isinstance(exc.code, int) else "")
    finally:
        sys.path.remove(stub_dir)

    combined = err.getvalue()
    assert rc != 0, "a module missing run_verification must not pass"
    assert_in("run_verification", combined, "names the missing symbol")
    assert_in("No fallback", combined, "explains that no fallback is attempted")


@scenario("(6) pass A matching report_text passes byte-for-byte")
def _():
    path = os.path.join(TMP, MAIN, "pass_a_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MAIN_EXPECTED["report_text"])
    try:
        rc, out = run_check(["--pipeline", PIPELINE, "--pass-a", MAIN])
    finally:
        os.remove(path)
    assert rc == 0, f"identical pass A should pass, got {rc}:\n{out}"
    assert_in("pass A", out, "mode line mentions pass A")


@scenario("(6) pass A tolerates trailing whitespace at end-of-file on either side")
def _():
    path = os.path.join(TMP, MAIN, "pass_a_report.txt")
    report = MAIN_EXPECTED["report_text"]
    # End-of-file whitespace is an editor artifact (a final newline added or
    # dropped on save), not a difference in the report.
    for label, text in [
        ("lost trailing newlines", report.rstrip("\n")),
        ("stripped of all trailing whitespace", report.rstrip()),
        ("extra trailing newlines", report + "\n\n\n"),
        ("trailing spaces and tabs", report + "  \t \n"),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            rc, out = run_check(["--pipeline", PIPELINE, "--pass-a", MAIN])
        finally:
            os.remove(path)
        assert rc == 0, f"pass A {label} should still pass, got {rc}:\n{out}"


@scenario("(6) pass A stays byte-exact everywhere except end-of-file")
def _():
    path = os.path.join(TMP, MAIN, "pass_a_report.txt")
    report = MAIN_EXPECTED["report_text"]
    lines = report.split("\n")
    mid = len(lines) // 2
    # Each of these is an interior difference; the end-of-file trim must not
    # reach any of them.
    cases = {
        "trailing space on a mid-report line":
            "\n".join(lines[:mid] + [lines[mid] + " "] + lines[mid + 1:]),
        "CRLF line endings": report.replace("\n", "\r\n"),
        "an extra blank line between sections": report.replace("Missed:", "\nMissed:"),
        "a changed character": report.replace("Missed:", "Missing:"),
    }
    for label, text in cases.items():
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        try:
            rc, out = run_check(["--pipeline", PIPELINE, "--pass-a", MAIN])
        finally:
            os.remove(path)
        assert rc == 1, f"pass A with {label} must FAIL, got {rc}:\n{out}"
        assert_in("pass_a: report_text differs", out, f"pass A diff ({label})")
        assert_in("first difference at byte", out, f"byte offset reported ({label})")


@scenario("(6) pass A truncated mid-report FAILS and says where it ran out")
def _():
    path = os.path.join(TMP, MAIN, "pass_a_report.txt")
    report = MAIN_EXPECTED["report_text"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(report[:len(report) // 2])
    try:
        rc, out = run_check(["--pipeline", PIPELINE, "--pass-a", MAIN])
    finally:
        os.remove(path)
    assert rc == 1, f"a truncated pass A report must FAIL, got {rc}:\n{out}"
    assert_in("<end of file>", out, "truncation described")


@scenario("(6) missing pass_a_report.txt FAILS in pass A mode")
def _():
    rc, out = run_check(["--pipeline", PIPELINE, "--pass-a", MAIN])
    assert rc == 1, f"a missing pass A report must FAIL, got {rc}:\n{out}"
    assert_in("is missing", out, "names the missing file")


@scenario("(6) --pass-a-only runs without --pipeline")
def _():
    path = os.path.join(TMP, MAIN, "pass_a_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MAIN_EXPECTED["report_text"])
    try:
        rc, out = run_check(["--pass-a-only", MAIN])
    finally:
        os.remove(path)
    assert rc == 0, f"pass-A-only should pass, got {rc}:\n{out}"
    assert_in("pass A report matches", out, "pass A only result")
    assert_not_in("pipeline symbols resolved", out, "replay must be skipped")


@scenario("(5) a lowered grade cutoff actually moves a polygon's category")
def _():
    """The parameters must be wired through, not accepted and ignored."""
    lons, lats = make_grid()
    max_tops, max_refl = make_arrays(lons, lats, MAIN_BLOBS)
    gdf = tcf_pipeline.parse_iem_cow_text(MAIN_RAW)
    valid_dt = tcf_pipeline.compute_valid_dt(date(2026, 5, 24), 19, 4)

    def grade(params):
        r = tcf_pipeline.run_verification_legacy_independent_max(gdf, max_tops, max_refl, lons, lats,
                                          valid_dt, 19, 4, ARTCC, params=params)
        return {p["idx"]: (p["category"], p["coverage_fraction"]) for p in r["graded_forecasts"]}

    default = grade(tcf_pipeline.GradingParams())
    # The defaults must still be the frozen behaviour.
    assert [c for c, _ in default.values()].count("Verified Well") == 1
    close = [(i, f) for i, (c, f) in default.items() if c == "Verified Close"]
    assert close, f"fixture should have a Verified Close polygon to promote: {default}"
    idx, frac = close[0]

    # Drop the cutoff below that polygon's coverage fraction: it must promote.
    lowered = grade(tcf_pipeline.GradingParams(verified_well_cutoff=frac - 0.01))

    changed = [i for i in default if default[i][0] != lowered[i][0]]
    assert changed, (f"lowering verified_well_cutoff from 0.50 to {frac - 0.01:.4f} changed "
                     f"no categories -- the parameter is being ignored:\n"
                     f"  default {default}\n  lowered {lowered}")
    assert lowered[idx][0] == "Verified Well", \
        f"polygon {idx} (coverage {frac:.4f}) should promote to Verified Well, got {lowered[idx][0]}"
    # Only the grade moved; the underlying fraction is the same measurement.
    for i in default:
        assert abs(default[i][1] - lowered[i][1]) < 1e-12, \
            f"polygon {i}: a grade cutoff must not change the coverage fraction"
    print(f"  cutoff 0.50 -> {frac - 0.01:.4f} promoted polygon {idx}: "
          f"{default[idx][0]} -> {lowered[idx][0]}")


@scenario("(5) every GradingParams field is consumed, not silently dropped")
def _():
    """Each field gets a deliberately extreme value; the output must move.

    A separate probe fixture, because MAIN's only Medium (cov=2) forecast sits
    over open ground -- its coverage is 0 whatever medium_truth_threshold says,
    so MAIN alone cannot tell that field apart from a no-op.
    """
    # The wide grid plus an out-of-domain blob, so apply_domain_mask has
    # something to bite on: on make_grid() everything is inside ARTCC territory
    # and the field would look like a no-op.
    lons, lats = make_wide_grid()
    max_tops, max_refl = make_arrays(lons, lats, MAIN_BLOBS + [OUT_OF_DOMAIN_BLOB])
    box = [(-100.5, 36.5), (-95.5, 36.5), (-95.5, 41.5), (-100.5, 41.5)]   # over the big blob
    clip = [(-97.5, 39.5), (-92.5, 39.5), (-92.5, 42.5), (-97.5, 42.5)]    # partial overlap
    raw = ("<html><pre>\nTCFNTA CFP02 PROBE\n"
           + area_block(2, box)     # Medium over truth -> medium_truth_threshold bites
           + area_block(3, box)     # Sparse over truth -> sparse_truth_threshold bites
           + area_block(3, clip)    # lands between the cutoffs -> both cutoffs bite
           + "</pre></html>\n")
    gdf = tcf_pipeline.parse_iem_cow_text(raw)
    valid_dt = tcf_pipeline.compute_valid_dt(date(2026, 5, 24), 19, 4)

    def summarise(params):
        r = tcf_pipeline.run_verification_legacy_independent_max(gdf, max_tops, max_refl, lons, lats,
                                          valid_dt, 19, 4, ARTCC, params=params)
        return (tuple((p["coverage"], round(p["coverage_fraction"], 6), p["category"])
                      for p in r["graded_forecasts"]),
                len(r["graded_misses"]))

    base = summarise(tcf_pipeline.GradingParams())
    assert {c for _, _, c in base[0]} >= {"Verified Close", "Verified Well"}, \
        f"probe fixture should span both grade bands, got {base}"

    probes = {
        "sparse_truth_threshold": 0.60,
        "medium_truth_threshold": 0.90,
        "verified_well_cutoff": 0.90,
        "verified_close_cutoff": 0.30,
        "miss_capture_threshold": 1.01,   # > 1.0: every truth blob becomes a miss
        "dilation_iterations": 6,
        "smoothing_size": 40,
        "apply_domain_mask": False,
    }
    fields = {f.name for f in dataclasses.fields(tcf_pipeline.GradingParams)}
    assert fields == set(probes), \
        f"GradingParams fields changed; probe list is stale: {fields ^ set(probes)}"

    ignored = []
    for field, value in probes.items():
        moved = summarise(dataclasses.replace(tcf_pipeline.GradingParams(), **{field: value}))
        if moved == base:
            ignored.append(f"{field}={value}")
    assert not ignored, ("these GradingParams fields changed nothing and are therefore not "
                        f"wired through: {', '.join(ignored)}\n  baseline output: {base}")


@scenario("(5) GradingParams defaults are exactly the previously-hardcoded values")
def _():
    p = tcf_pipeline.GradingParams()
    expected = {
        "sparse_truth_threshold": 0.25,
        "medium_truth_threshold": 0.40,
        "verified_well_cutoff": 0.50,
        "verified_close_cutoff": 0.20,
        "miss_capture_threshold": 0.20,
        "dilation_iterations": 1,
        "smoothing_size": 20,
        "apply_domain_mask": True,
    }
    for field, want in expected.items():
        got = getattr(p, field)
        assert got == want, f"GradingParams.{field}: expected {want}, got {got}"
    # Frozen, so the shared default instance cannot be mutated mid-run.
    try:
        p.verified_well_cutoff = 0.9
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("GradingParams should be frozen")


def _fixture_review_table():
    """The main fixture's review table, plus the pieces build_report needs."""
    lons, lats = make_grid()
    max_tops, max_refl = make_arrays(lons, lats, MAIN_BLOBS)
    gdf = tcf_pipeline.parse_iem_cow_text(MAIN_RAW)
    valid_dt = tcf_pipeline.compute_valid_dt(date(2026, 5, 24), 19, 4)
    results = tcf_pipeline.run_verification_legacy_independent_max(gdf, max_tops, max_refl, lons, lats,
                                            valid_dt, 19, 4, ARTCC)
    return results, valid_dt


def _report_sections(text):
    """Split a report into {heading: [lines]} so a line can be located by section."""
    sections, current = {}, None
    for line in text.split("\n"):
        if line.endswith(":") and not line.startswith(" "):
            current = line
            sections[current] = []
        elif current and line.strip() and line != "None":
            sections[current].append(line)
    return sections


@scenario("(1) build_review_table carries every column build_report needs")
def _():
    results, _ = _fixture_review_table()
    table = results["review_table"]

    assert list(table.columns) == list(tcf_pipeline.REVIEW_COLUMNS), \
        f"unexpected columns: {list(table.columns)}"
    assert len(table) == 5, f"3 forecasts + 2 candidates expected, got {len(table)} rows"
    assert list(table["kind"]) == ["forecast"] * 3 + ["candidate_miss"] * 2, \
        f"forecast rows then candidate rows expected, got {list(table['kind'])}"

    # Pandas-native nullable dtypes throughout, so the frame survives
    # st.data_editor -- in particular idx must stay an integer, since an idx that
    # came back as 1.0 would render as "(Area 1.0)".
    assert dict(table.dtypes.astype(str)) == dict(tcf_pipeline.REVIEW_COLUMNS), \
        f"dtypes drifted: {dict(table.dtypes.astype(str))}"

    # No geometry anywhere -- that is the thing which would not round-trip.
    for col in table.columns:
        assert "geometry" not in col
        for value in table[col]:
            assert not hasattr(value, "geom_type"), \
                f"column {col} holds a shapely {type(value).__name__}"

    fcst = table[table["kind"] == "forecast"]
    assert set(fcst["category"]) == {"Verified Well", "Verified Close", "Overforecasted"}
    assert set(fcst["coverage_code"]) <= {1, 2, 3}
    assert all(a and a != "UNKNOWN" for a in table["artccs"]), \
        f"ARTCC lookup should have happened in the table: {list(table['artccs'])}"
    assert fcst["top_kft"].notna().any(), "fixture should retain numeric echo tops"
    assert fcst["top_kft"].isna().any(), "insufficient echo-top samples should remain nullable"
    assert fcst["coverage_fraction"].notna().all()


@scenario("(3) run_verification's report equals build_report on its own table")
def _():
    results, valid_dt = _fixture_review_table()
    rebuilt = tcf_pipeline.build_report(results["review_table"], valid_dt, 19, 4)
    assert rebuilt == results["report_text"], (
        "rebuilding the report from the returned table must reproduce it exactly:\n"
        + "\n".join(difflib.unified_diff(results["report_text"].splitlines(),
                                         rebuilt.splitlines(),
                                         fromfile="run_verification", tofile="rebuilt",
                                         lineterm="", n=1)))


@scenario("(5) editing a category in the table changes the report text")
def _():
    """The seam is only real if the table is what the report is built from."""
    results, valid_dt = _fixture_review_table()
    table = results["review_table"]
    before = tcf_pipeline.build_report(table, valid_dt, 19, 4)

    well = table.index[(table["kind"] == "forecast") & (table["category"] == "Verified Well")]
    assert len(well) >= 1, f"fixture should grade something Verified Well:\n{table}"
    row = well[0]
    idx, feat = int(table.at[row, "idx"]), table.at[row, "feat_type"]
    label = f"({'Line' if feat == 'LINE' else 'Area'} {idx})"

    edited = table.copy()
    edited.at[row, "category"] = "Overforecasted"
    after = tcf_pipeline.build_report(edited, valid_dt, 19, 4)

    assert after != before, "editing a category changed nothing -- the seam is fake"

    sec_before, sec_after = _report_sections(before), _report_sections(after)
    moved = [ln for ln in sec_before["Verified Well:"] if label in ln]
    assert moved, f"could not find {label} under Verified Well:\n{before}"
    line = moved[0]

    assert line not in sec_after.get("Verified Well:", []), \
        f"{label} should have left Verified Well:\n{after}"
    assert line in sec_after.get("Over-forecast:", []), \
        f"{label} should now be under Over-forecast:\n{after}"
    # Only that one line moved; nothing else in the report shifted.
    for heading in ("Verified Close:", "Missed:"):
        assert sec_before.get(heading, []) == sec_after.get(heading, []), \
            f"{heading} should be untouched by the edit"
    print(f"  edited {label}: Verified Well -> Over-forecast, report followed")


@scenario("(5) editing artccs and top_kft in the table also reaches the report")
def _():
    """Category is not a special case -- every column the report reads is live."""
    results, valid_dt = _fixture_review_table()
    table = results["review_table"]

    edited = table.copy()
    row = edited.index[edited["kind"] == "forecast"][0]
    edited.at[row, "artccs"] = "ZZZ/ZYY"
    edited.at[row, "top_kft"] = 47.0
    miss_row = edited.index[edited["kind"] == "candidate_miss"][0]
    edited.at[miss_row, "artccs"] = "ZQQ"
    edited.at[miss_row, "approved_for_report"] = True

    after = tcf_pipeline.build_report(edited, valid_dt, 19, 4)
    assert "ZZZ/ZYY - " in after, f"edited ARTCC did not reach the report:\n{after}"
    assert "[Top: 47.0 kft]" in after, f"edited top did not reach the report:\n{after}"
    assert "ZQQ - Missed" in after, f"edited miss ARTCC did not reach the report:\n{after}"

    # A zero top drops the bracket entirely, as it always has. Check the line for
    # THIS polygon, not whichever line happens to sort first.
    idx, feat = int(table.at[row, "idx"]), table.at[row, "feat_type"]
    label = f"({'Line' if feat == 'LINE' else 'Area'} {idx})"
    zeroed = table.copy()
    zeroed.at[row, "top_kft"] = 0.0
    lines = [ln for sec in _report_sections(tcf_pipeline.build_report(zeroed, valid_dt, 19, 4)).values()
             for ln in sec if label in ln]
    assert len(lines) == 1, f"expected exactly one line for {label}, got {lines}"
    assert "[Top:" not in lines[0], f"a zero top should print no bracket: {lines[0]!r}"


# --- composite fetch (parallel downloads, fixed fold order) ------------------
def _run_stubbed_composite(completion_delays, workers=8):
    """Drive the real build_composite with S3 and cfgrib stubbed out.

    completion_delays maps a scan offset (minutes) to a sleep in the fake
    downloader, so downloads finish in a controlled, deliberately jumbled order.
    Returns the composite plus the order the workers actually finished in.
    """
    import threading
    import time as _time

    lons, lats = make_grid()
    # One distinct array per scan, with values that make the running max
    # order-sensitive if it is ever folded in completion order.
    rng = np.random.default_rng(1234)
    per_scan = {}
    for i, offset in enumerate(tcf_pipeline.scan_offsets()):
        tops = rng.uniform(0, 60, size=(len(lats), len(lons))).astype(np.float32)
        refl = rng.uniform(0, 70, size=(len(lats), len(lons))).astype(np.float32)
        # Signed zeros in overlapping cells: np.maximum keeps its FIRST argument
        # when the two compare equal, so these are exactly the cells whose sign
        # bit would follow fold order.
        tops[i % 3::7] = -0.0 if i % 2 else 0.0
        refl[i % 5::11] = 0.0 if i % 2 else -0.0
        per_scan[offset] = (tops, refl)

    finished = []
    finished_lock = threading.Lock()
    log_threads = set()

    def fake_resolve(product, dt_obj, s3=None):
        return f"{product}/{dt_obj:%H%M}"

    def fake_download(key, dest_dir="mrms_data", s3=None):
        offset = _key_offset(key)
        _time.sleep(completion_delays.get(offset, 0.0))
        with finished_lock:
            finished.append((key, _time.perf_counter()))
        return key

    def fake_read(tops_file, refl_file, step):
        offset = _key_offset(tops_file)
        tops, refl = per_scan[offset]
        return tops.copy(), refl.copy(), lons, lats

    def spy_log(msg):
        log_threads.add(threading.current_thread().name)

    saved = (tcf_pipeline._resolve_scan_key, tcf_pipeline._download_key,
             tcf_pipeline._read_scan_arrays, tcf_pipeline._s3_client)
    tcf_pipeline._resolve_scan_key = fake_resolve
    tcf_pipeline._download_key = fake_download
    tcf_pipeline._read_scan_arrays = fake_read
    tcf_pipeline._s3_client = lambda: None
    try:
        out = tcf_pipeline.build_composite(
            VALID_DT, log=spy_log, max_workers=workers, dest_dir=TMP_MRMS)
    finally:
        (tcf_pipeline._resolve_scan_key, tcf_pipeline._download_key,
         tcf_pipeline._read_scan_arrays, tcf_pipeline._s3_client) = saved
    order = [k for k, _ in sorted(finished, key=lambda kv: kv[1])]
    return out, order, log_threads


def _key_offset(key):
    """Recover the scan offset (minutes) from a stubbed key like 'EchoTop_18/2315'."""
    hhmm = key.split('/')[-1]
    scan = VALID_DT.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
    delta = round((scan - VALID_DT).total_seconds() / 60)
    # +/-15 min around 23:00 stays inside the day for this fixture's VALID_DT.
    return delta


VALID_DT = tcf_pipeline.compute_valid_dt(date(2026, 5, 24), 19, 4)
TMP_MRMS = os.path.join(TMP, "mrms_stub")


@scenario("(2b) composite is bit-identical regardless of download completion order")
def _():
    """The whole point of parallel fetch: workers finish in whatever order they
    like, and the arrays must not care."""
    offsets = tcf_pipeline.scan_offsets()
    (a_tops, a_refl, a_qual, a_lons, a_lats), order_a, _ = _run_stubbed_composite({})
    # Make the LAST scan finish first and the first finish last.
    jumbled = {o: 0.20 * (len(offsets) - 1 - i) / len(offsets)
               for i, o in enumerate(offsets)}
    (b_tops, b_refl, b_qual, b_lons, b_lats), order_b, _ = _run_stubbed_composite(jumbled)
    # And a third pass with a different jumble again.
    shuffled = {o: 0.20 * ((i * 7) % len(offsets)) / len(offsets)
                for i, o in enumerate(offsets)}
    (c_tops, c_refl, c_qual, _, _), order_c, _ = _run_stubbed_composite(shuffled)

    assert order_a != order_b or order_b != order_c, \
        "the stub failed to produce differing completion orders, so this proves nothing"

    for name, x, y, z in (("max_tops", a_tops, b_tops, c_tops),
                          ("max_refl", a_refl, b_refl, c_refl),
                          ("qualifying_mask", a_qual, b_qual, c_qual)):
        # tobytes(), not array_equal: -0.0 == 0.0 compares equal but is a
        # different bit pattern, and bit-identical is what was asked for.
        assert x.tobytes() == y.tobytes() == z.tobytes(), \
            f"{name} changed with download completion order -- the fold is not order-pinned"
    assert a_lons.tobytes() == b_lons.tobytes() and a_lats.tobytes() == b_lats.tobytes(), \
        "lons/lats came from a different scan depending on completion order"
    print(f"  3 completion orders, identical bytes "
          f"(first finisher: {order_a[0].split('/')[-1]} / {order_b[0].split('/')[-1]} / "
          f"{order_c[0].split('/')[-1]})")


@scenario("(2d) the log callback is only ever called from the calling thread")
def _():
    """st.write from a pool worker has no ScriptRunContext and its output can
    interleave, so the pipeline must not hand the callback to workers."""
    _offsets = tcf_pipeline.scan_offsets()
    _, _, threads = _run_stubbed_composite({_offsets[0]: 0.05, _offsets[-1]: 0.0})
    assert threads, "the composite logged nothing at all"
    assert threads == {"MainThread"}, \
        f"log() was called from worker thread(s): {sorted(threads)}"


@scenario("(2b) np.maximum is order-sensitive for signed zeros, which is why the fold is pinned")
def _():
    """Documents the hazard the pinned fold order exists to avoid.

    If this ever starts failing, numpy changed and the fold order stopped being
    load-bearing -- which would be good news, but the comment in build_composite
    would need updating.
    """
    import functools
    neg = np.float32(-0.0)
    pos = np.float32(0.0)
    assert np.maximum(neg, pos).tobytes() != np.maximum(pos, neg).tobytes(), \
        "np.maximum no longer distinguishes signed zeros by argument order"

    # Without signed zeros, any fold order agrees bit for bit.
    rng = np.random.default_rng(7)
    plain = [rng.uniform(1, 50, size=(30, 40)).astype(np.float32) for _ in range(7)]
    folds = {functools.reduce(np.maximum, [plain[i] for i in perm]).tobytes()
             for perm in ([0, 1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1, 0], [3, 0, 6, 1, 5, 2, 4])}
    assert len(folds) == 1, "np.maximum disagreed across fold orders on ordinary data"


@scenario("(2a) each product-day is listed once and cached, not once per scan")
def _():
    calls = []

    class FakeS3:
        def get_paginator(self, _op):
            class P:
                def paginate(self, Bucket=None, Prefix=None):
                    calls.append(Prefix)
                    # Two pages, to prove pagination is followed rather than
                    # only the first 1000 keys being read.
                    yield {"Contents": [
                        {"Key": f"{Prefix}MRMS_X_00.50_20260524-{h:02d}{m:02d}00.grib2.gz"}
                        for h in range(12) for m in (0, 30)]}
                    yield {"Contents": [
                        {"Key": f"{Prefix}MRMS_X_00.50_20260524-{h:02d}{m:02d}00.grib2.gz"}
                        for h in range(12, 24) for m in (0, 30)]}
            return P()

    tcf_pipeline._MRMS_KEY_CACHE.clear()
    try:
        fake = FakeS3()
        first = tcf_pipeline.list_mrms_keys("EchoTop_18", "20260524", s3=fake)
        assert len(first) == 48, f"pagination dropped keys: got {len(first)}, expected 48 across 2 pages"
        for _ in range(13):
            tcf_pipeline.list_mrms_keys("EchoTop_18", "20260524", s3=fake)
        assert len(calls) == 1, f"listed {len(calls)} times for one product-day; the cache is not working"
        # A different day is a different cache entry.
        tcf_pipeline.list_mrms_keys("EchoTop_18", "20260525", s3=fake)
        assert len(calls) == 2, f"a second day should list once more, got {len(calls)} total"
    finally:
        tcf_pipeline._MRMS_KEY_CACHE.clear()


def _feature_block(kind, cov, pts):
    """One AREA or LINE feature in the AWIPS tenths-of-a-degree encoding."""
    flat = " ".join(f"{int(round(la * 10))} {int(round(abs(lo) * 10))}" for lo, la in pts)
    head = f"{cov} {len(pts)}" if kind == "LINE" else f"{cov} 3 0 400 20 270 {len(pts)}"
    return f"{kind} \t{head} {flat}\n"


@scenario("(BUG 2 fixed) a LINE is buffered as a line at any point count")
def _():
    """The old parser branched on point count alone, so a 3+ point LINE was
    closed into a Polygon and graded as a filled area."""
    zigzag = [(-100.0, 40.0), (-98.0, 41.0), (-96.0, 40.0), (-94.0, 41.0)]

    areas = {}
    for n in (2, 3, 4):
        gdf = tcf_pipeline.parse_iem_cow_text(
            "<pre>" + _feature_block("LINE", 1, zigzag[:n]) + "</pre>")
        assert len(gdf) == 1, f"{n}-point LINE did not parse"
        assert gdf["feat_type"].iloc[0] == "LINE"
        areas[n] = gdf.geometry.iloc[0].area

    # A buffered open line grows with each added segment. A closed polygon does
    # not: the 3-point zigzag encloses a triangle, and the 4-point one folds
    # back on itself, so under the old behaviour area would have gone DOWN from
    # 3 points to 4.
    assert areas[2] < areas[3] < areas[4], \
        f"LINE area should grow with each segment, got {areas}"

    # Each extra segment adds roughly the same corridor area.
    step_a, step_b = areas[3] - areas[2], areas[4] - areas[3]
    assert abs(step_a - step_b) < 0.25 * step_a, \
        f"segments should contribute comparable area, got {step_a:.4f} then {step_b:.4f}"

    # The 3-point LINE must NOT equal the closed triangle it used to become.
    from shapely.geometry import Polygon as _Poly
    closed = _Poly(zigzag[:3]).buffer(0)
    assert abs(areas[3] - closed.area) > 1e-6, \
        "3-point LINE still has the area of the closed polygon -- the fix is not applied"


@scenario("(BUG 2 fixed) AREA features are untouched by the LINE fix")
def _():
    quad = [(-100.0, 40.0), (-98.0, 40.0), (-98.0, 42.0), (-100.0, 42.0)]
    from shapely.geometry import LineString as _Line, Polygon as _Poly

    gdf = tcf_pipeline.parse_iem_cow_text(
        "<pre>" + _feature_block("AREA", 2, quad) + "</pre>")
    assert gdf["feat_type"].iloc[0] == "AREA"
    assert abs(gdf.geometry.iloc[0].area - _Poly(quad).buffer(0).area) < 1e-9, \
        "a 4-point AREA should still be the closed polygon"

    # A degenerate 2-point AREA keeps its long-standing buffered-line fallback.
    gdf2 = tcf_pipeline.parse_iem_cow_text(
        "<pre>" + _feature_block("AREA", 2, quad[:2]) + "</pre>")
    expected = _Line(quad[:2]).buffer(tcf_pipeline.LINE_BUFFER_DEG).area
    assert abs(gdf2.geometry.iloc[0].area - expected) < 1e-9, \
        "2-point AREA fallback changed"

    # And the buffer itself was not retuned as part of the geometry fix.
    assert tcf_pipeline.LINE_BUFFER_DEG == 0.15


@scenario("(5) the domain mask is live: out-of-domain truth vanishes, in-domain survives")
def _():
    lons, lats = make_wide_grid()
    blobs = [IN_DOMAIN_BLOB, OUT_OF_DOMAIN_BLOB]

    on = grade_blobs(lons, lats, blobs, FAR_FORECAST,
                     tcf_pipeline.GradingParams(apply_domain_mask=True))
    off = grade_blobs(lons, lats, blobs, FAR_FORECAST,
                      tcf_pipeline.GradingParams(apply_domain_mask=False))

    on_lats = [lat for lat, _lon in truth_centroids(on)]
    off_lats = [lat for lat, _lon in truth_centroids(off)]

    assert any(lat > 49 for lat in off_lats), \
        f"the out-of-domain blob should survive with the mask OFF, got {off_lats}"
    assert not any(lat > 49 for lat in on_lats), \
        f"the out-of-domain blob should be gone with the mask ON, got {on_lats}"
    assert any(35 < lat < 45 for lat in on_lats), \
        f"the in-domain blob must survive the mask, got {on_lats}"

    # And it is the miss count that this is really about.
    assert len(on["graded_misses"]) < len(off["graded_misses"]), \
        (f"masking should remove a miss: {len(off['graded_misses'])} unmasked -> "
         f"{len(on['graded_misses'])} masked")
    print(f"  misses {len(off['graded_misses'])} unmasked -> "
          f"{len(on['graded_misses'])} masked; truth lats {off_lats} -> {on_lats}")


@scenario("(5) HISTORICAL: extract_tcf_polygons clips before an optional area floor")
def _():
    """If anyone reorders clip and filter, this is the scenario that fails.

    The blob clears min_area_m2 at full extent but not after the domain clip, so
    filter-then-clip keeps it (graded on area it does not have inside the scored
    region) and clip-then-filter deletes it.
    """
    lons, lats = make_wide_grid()
    params = tcf_pipeline.GradingParams()
    historical_floor_m2 = 15_000_000_000
    domain = tcf_pipeline.verification_domain()

    max_tops, max_refl = make_arrays(lons, lats, [STRADDLE_BLOB])
    raw_cores = (max_refl >= 40) & (max_tops >= 25)
    from scipy.ndimage import binary_dilation as _dil, uniform_filter as _uni
    field = _uni(_dil(raw_cores, iterations=params.dilation_iterations).astype(float),
                 size=params.smoothing_size)
    mask = (field >= params.sparse_truth_threshold).astype(int)

    # Unfiltered, unclipped: what the blob measures at full extent.
    whole = tcf_pipeline.extract_tcf_polygons(mask, lons, lats, min_area_m2=0)
    whole_m2 = float(whole.to_crs("EPSG:5070").geometry.area.iloc[0])
    # Unfiltered but clipped: what is left inside the scored area.
    clipped = tcf_pipeline.extract_tcf_polygons(mask, lons, lats, min_area_m2=0,
                                               domain=domain)
    clipped_m2 = (float(clipped.to_crs("EPSG:5070").geometry.area.iloc[0])
                  if not clipped.empty else 0.0)

    assert whole_m2 >= historical_floor_m2, \
        (f"fixture is not exercising the ordering: the blob must clear the floor "
         f"whole ({whole_m2 / 1e6:,.0f} km2 vs {historical_floor_m2 / 1e6:,.0f} km2)")
    assert clipped_m2 < historical_floor_m2, \
        (f"fixture is not exercising the ordering: the blob must fall BELOW the "
         f"floor once clipped ({clipped_m2 / 1e6:,.0f} km2)")

    # Clip-then-filter, which is what the pipeline does: nothing survives.
    survivors = tcf_pipeline.extract_tcf_polygons(
        mask, lons, lats, min_area_m2=historical_floor_m2, domain=domain)
    assert survivors.empty or survivors.is_empty.all(), \
        ("the straddling blob survived -- min_area_m2 was applied before the clip, "
         "so it was measured at its full extent")

    # Filter-then-clip, the wrong order, spelled out so the difference is visible.
    wrong = tcf_pipeline.extract_tcf_polygons(mask, lons, lats,
                                              min_area_m2=historical_floor_m2)
    assert not (wrong.empty or wrong.is_empty.all()), \
        "the wrong order should keep this blob; if it does not, the fixture is stale"
    print(f"  straddling blob {whole_m2 / 1e6:,.0f} km2 whole -> "
          f"{clipped_m2 / 1e6:,.0f} km2 clipped (floor "
          f"{historical_floor_m2 / 1e6:,.0f}): DELETED, correct order")


@scenario("(1) scan offsets are symmetric about the valid time and include it")
def _():
    """A naive range(-window, window+1, cadence) drops the valid-time scan at any
    cadence that does not divide the window."""
    offsets = tcf_pipeline.scan_offsets()
    assert 0 in offsets, f"the scan at the valid time itself is missing: {offsets}"
    assert offsets == sorted(offsets), "offsets should be in ascending time order"
    assert offsets[0] == -offsets[-1], f"offsets are not symmetric: {offsets}"
    assert all(abs(b - a) == tcf_pipeline.COMPOSITE_CADENCE_MINUTES
               for a, b in zip(offsets, offsets[1:])), "uneven spacing"
    assert abs(offsets[0]) <= tcf_pipeline.COMPOSITE_WINDOW_MINUTES, \
        "offsets escape the configured window"
    # The 5-minute form this replaced must still come out identical.
    assert tcf_pipeline.scan_offsets(15, 5) == list(range(-15, 16, 5))
    assert len(tcf_pipeline.scan_offsets(15, 2)) == 15


@scenario("(1) EVENTS holds the six configured events with the documented ids")
def _():
    want = [("20260524_19Z_F04", date(2026, 5, 24), 19, 4),
            ("20260524_19Z_F06", date(2026, 5, 24), 19, 6),
            ("20260524_13Z_F04", date(2026, 5, 24), 13, 4),
            ("20260728_19Z_F04", date(2026, 7, 28), 19, 4),
            ("20260403_21Z_F04", date(2026, 4, 3), 21, 4),
            ("20260324_13Z_F04", date(2026, 3, 24), 13, 4)]
    got = [(e["event_id"], e["date"], e["issuance_hour"], e["lead_time"]) for e in capture.EVENTS]
    assert got == want, f"EVENTS drifted:\n  want {want}\n  got  {got}"


@scenario("(1) the day-rollover event pulls MRMS from the SCAN date, not the issuance date")
def _():
    import datetime as dtmod

    evt = next(e for e in capture.EVENTS if e["event_id"] == "20260403_21Z_F04")
    valid_dt = tcf_pipeline.compute_valid_dt(evt["date"], evt["issuance_hour"], evt["lead_time"])
    assert valid_dt == dtmod.datetime(2026, 4, 4, 1, 0), \
        f"21Z + 4 should be 01Z the next day, got {valid_dt}"

    # Record both what the composite asks for (product, scan datetime) and the
    # product-day each listing is built for -- the latter is the actual S3
    # prefix component, which is the thing that must not follow the issuance date.
    asked, listed = [], []
    real_resolve = tcf_pipeline._resolve_scan_key

    def spy_resolve(product, dt_obj, s3=None):
        asked.append((product, dt_obj))
        return real_resolve(product, dt_obj, s3=s3)

    # Stub at the S3 client, not at list_mrms_keys, so the real caching path runs
    # and the listing count below means something.
    class SilentS3:
        def get_paginator(self, _op):
            class P:
                def paginate(self, Bucket=None, Prefix=None):
                    listed.append(Prefix)
                    yield {}          # no keys -> nothing resolves -> nothing downloads
            return P()

    saved = (tcf_pipeline._resolve_scan_key, tcf_pipeline._s3_client)
    tcf_pipeline._resolve_scan_key = spy_resolve
    tcf_pipeline._s3_client = SilentS3
    tcf_pipeline._MRMS_KEY_CACHE.clear()
    try:
        try:
            tcf_pipeline.build_composite(valid_dt, log=lambda _m: None, dest_dir=TMP_MRMS)
        except RuntimeError:
            pass  # expected: the stub reports no scans available
    finally:
        (tcf_pipeline._resolve_scan_key, tcf_pipeline._s3_client) = saved
        tcf_pipeline._MRMS_KEY_CACHE.clear()

    n_scans = len(tcf_pipeline.scan_offsets())
    assert len(asked) == n_scans * 2, \
        f"expected {n_scans} offsets x 2 products, got {len(asked)}"
    dates = sorted({d.strftime("%Y%m%d") for _, d in asked})
    assert dates == ["20260404"], \
        f"scans must come from the scan date 20260404, not the issuance date; got {dates}"
    # The window around 01Z stays inside 2026-04-04 whatever the cadence.
    edge = tcf_pipeline.scan_offsets()[-1]
    assert min(d for _, d in asked) == valid_dt - dtmod.timedelta(minutes=edge)
    assert max(d for _, d in asked) == valid_dt + dtmod.timedelta(minutes=edge)

    # And the S3 prefix actually listed carries the same date.
    listed_dates = sorted({pfx.rstrip("/").split("/")[-1] for pfx in listed})
    assert listed_dates == ["20260404"], \
        f"listing prefix used {listed_dates}, expected ['20260404']"
    # One listing per product for the whole window, not one per scan per product.
    assert len(listed) == 2, \
        f"expected 2 listings (one per product), got {len(listed)}: {listed}"


@scenario("the network guard blocks outbound connections")
def _():
    import socket
    check_mod.block_network()
    try:
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    except RuntimeError as exc:
        assert "must not hit the network" in str(exc), f"unexpected message: {exc}"
    else:
        raise AssertionError("outbound connection was not blocked")


# --- summary ----------------------------------------------------------------
shutil.rmtree(TMP, ignore_errors=True)

passed = sum(1 for _, ok, _ in _results if ok)
print()
print(f"{passed}/{len(_results)} scenario(s) passed")
for name, ok, detail in _results:
    if not ok:
        print(f"  FAILED: {name} -- {detail}")
sys.exit(0 if passed == len(_results) else 1)
