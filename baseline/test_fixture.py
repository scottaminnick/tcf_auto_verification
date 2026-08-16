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
    results = tcf_pipeline.run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
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
assert MAIN_EXPECTED["counts"]["misses"] == 1, "fixture should find 1 miss"
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
    exp["polygons"][0]["top_kft"] = round(exp["polygons"][0]["top_kft"] + 3.5, 2)
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
    assert_in("misses[1]: missing", out, "missing entry")
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
    assert_in("misses: count expected 0, got 1", out, "miss count diff")
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
        r = tcf_pipeline.run_verification(gdf, max_tops, max_refl, lons, lats,
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
    lons, lats = make_grid()
    max_tops, max_refl = make_arrays(lons, lats, MAIN_BLOBS)
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
        r = tcf_pipeline.run_verification(gdf, max_tops, max_refl, lons, lats,
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
        "dilation_iterations": 6,
        "smoothing_size": 40,
        "min_area_m2": 1e13,
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
        "dilation_iterations": 1,
        "smoothing_size": 20,
        "min_area_m2": 15_000_000_000,
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
    import datetime as dt

    evt = next(e for e in capture.EVENTS if e["event_id"] == "20260403_21Z_F04")
    valid_dt = tcf_pipeline.compute_valid_dt(evt["date"], evt["issuance_hour"], evt["lead_time"])
    assert valid_dt == dt.datetime(2026, 4, 4, 1, 0), \
        f"21Z + 4 should be 01Z the next day, got {valid_dt}"

    # Record what download_mrms_scan would be asked for, without touching S3.
    seen = []

    def spy(product, dt_obj, dest_dir="mrms_data"):
        seen.append((product, dt_obj))
        return None

    original = tcf_pipeline.download_mrms_scan
    tcf_pipeline.download_mrms_scan = spy
    try:
        try:
            tcf_pipeline.build_composite(valid_dt)
        except RuntimeError:
            pass  # expected: the spy reports no scans available
    finally:
        tcf_pipeline.download_mrms_scan = original

    assert len(seen) == 14, f"expected 7 offsets x 2 products, got {len(seen)}"
    # The prefix download_mrms_scan builds is CONUS/<product>_00.50/<dt_obj date>/.
    dates = sorted({d.strftime("%Y%m%d") for _, d in seen})
    assert dates == ["20260404"], \
        f"MRMS keys must come from the scan date 20260404, not the issuance date; got {dates}"
    assert "20260403" not in dates, "issuance date leaked into the S3 prefix"
    # +/-15 min around 01Z stays inside 2026-04-04.
    assert min(d for _, d in seen) == dt.datetime(2026, 4, 4, 0, 45)
    assert max(d for _, d in seen) == dt.datetime(2026, 4, 4, 1, 15)


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
