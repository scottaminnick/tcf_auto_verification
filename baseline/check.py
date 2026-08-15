#!/usr/bin/env python3
"""Replay the frozen baselines and diff them against expected.json.

For every ``baseline/<event_id>/`` directory this loads ``arrays.npz`` and
``tcf_raw.txt``, re-runs the verification math through whichever module the
pipeline currently lives in, and compares the result field-by-field against
``expected.json``. On a mismatch it prints exactly which field of which polygon
moved and by how much -- not just a pass/fail.

``--pipeline MODULE`` is REQUIRED and every symbol must resolve inside that one
module. There is no candidate list and no fallback to ``baseline.capture`` --
that fallback was actively harmful: mid-refactor, a symbol that had been moved
or renamed would quietly resolve against the frozen transcription instead, and
the run would pass by comparing capture.py to itself. A missing symbol is a hard
error so that a half-moved pipeline fails loudly.

Required pipeline symbols (all from the module named by --pipeline):
    compute_valid_dt(date, issuance_hour, lead_time) -> datetime
    parse_iem_cow_text(raw_text) -> GeoDataFrame
    run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                     valid_dt, issuance_hour, lead_time, gdf_artcc) -> dict
    load_artccs() -> GeoDataFrame
    get_artccs(poly, artcc_gdf) -> str

This script never touches the network -- outbound sockets are blocked at
import time so an accidental IEM/S3 call fails loudly instead of silently
re-fetching data. Use --allow-network only when debugging that guard.

Usage:
    python baseline/check.py --pipeline tcf_core
    python baseline/check.py --pipeline tcf_core 20260524_19Z_F04
    python baseline/check.py --pipeline tcf_core --strict
    python baseline/check.py --pipeline tcf_core --pass-a   # also diff vs pass A
    python baseline/check.py --pass-a-only                  # no pipeline needed

    # Verifying the transcription itself, before any refactor exists:
    python baseline/check.py --pipeline baseline.capture

Exit status: 0 = all events match, 1 = at least one mismatch, 2 = error.
"""

import argparse
import datetime as _datetime
import difflib
import importlib
import json
import os
import socket
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO_ROOT, "baseline")

PIPELINE_SYMBOLS = (
    "compute_valid_dt",
    "parse_iem_cow_text",
    "run_verification",
    "load_artccs",
    "get_artccs",
)

PASS_A_FILENAME = "pass_a_report.txt"

# Rounding quanta from capture.py -- a difference of one unit in the last stored
# decimal is rounding noise, anything larger is a real change. --strict compares
# the stored values exactly instead.
TOL = {"coverage_fraction": 1e-4, "top_kft": 1e-2, "bounds": 1e-4}


# --- network guard ----------------------------------------------------------
_NETWORK_BLOCKED = False


def block_network():
    """Make any outbound TCP/UDP connection raise. Local/unix sockets still work."""
    global _NETWORK_BLOCKED
    if _NETWORK_BLOCKED:  # idempotent: repeated calls must not nest the wrappers
        return
    _NETWORK_BLOCKED = True
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _deny(self, address, *a, **kw):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(
                f"check.py must not hit the network (blocked connection to {address!r}). "
                "Everything it needs is in baseline/<event_id>/ and artcc1.geojson."
            )
        return real_connect(self, address, *a, **kw)

    def _deny_ex(self, address, *a, **kw):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(f"check.py must not hit the network (blocked connection to {address!r})")
        return real_connect_ex(self, address, *a, **kw)

    socket.socket.connect = _deny
    socket.socket.connect_ex = _deny_ex


# --- pipeline resolution ----------------------------------------------------
class Pipeline:
    """Binds every required symbol to ONE named module. No search, no fallback.

    Resolving symbol-by-symbol across a candidate list is what makes a
    half-finished refactor look green: move `run_verification` out of a module
    and the old copy in baseline/capture.py answers instead, so the harness ends
    up checking the transcription against itself. Everything therefore comes
    from --pipeline's module, and anything missing raises.
    """

    def __init__(self, module_name):
        self.module_name = module_name
        try:
            self.module = importlib.import_module(module_name)
        except Exception as exc:
            raise SystemExit(
                f"ERROR: could not import pipeline module `{module_name}`: "
                f"{type(exc).__name__}: {exc}\n"
                f"  sys.path[0:2] = {sys.path[0:2]}"
            )

    def bind(self):
        """Resolve all required symbols at once; report every missing one together."""
        self.symbols = {}
        missing = []
        for symbol in PIPELINE_SYMBOLS:
            fn = getattr(self.module, symbol, None)
            if fn is None:
                missing.append(symbol)
            else:
                self.symbols[symbol] = fn
        if missing:
            raise SystemExit(
                f"ERROR: pipeline module `{self.module_name}` is missing "
                f"{len(missing)} required symbol(s): {', '.join(missing)}\n"
                f"  No fallback is attempted -- resolving these elsewhere would check the\n"
                f"  pipeline against a stale copy of itself. Export them from\n"
                f"  `{self.module_name}`, or point --pipeline at the module that has them."
            )
        return self

    def get(self, symbol):
        try:
            return self.symbols[symbol]
        except KeyError:
            raise SystemExit(f"ERROR: `{symbol}` is not a declared pipeline symbol")

    def describe(self):
        width = max(len(s) for s in PIPELINE_SYMBOLS)
        return "\n".join(f"  {s.ljust(width)} <- {self.module_name}" for s in PIPELINE_SYMBOLS)


# --- payload rebuilding -----------------------------------------------------
COVERAGE_DP = 4
TOP_DP = 2

# KEEP IN SYNC with the identical block in capture.py. This module deliberately
# imports nothing from capture.py (see the Pipeline docstring), so the constants
# are duplicated rather than shared; baseline/test_fixture.py asserts they agree.
GRADE_CUTOFFS = (0.50, 0.20)
BOUNDARY_WINDOW = 0.005


def is_boundary(coverage_fraction):
    """True if coverage_fraction sits within BOUNDARY_WINDOW of a grade cutoff."""
    if coverage_fraction is None:
        return False
    return any(abs(float(coverage_fraction) - c) <= BOUNDARY_WINDOW for c in GRADE_CUTOFFS)


def _round_bounds(geom):
    return [round(float(b), COVERAGE_DP) for b in geom.bounds]


def _records(results, list_key, gdf_key):
    """Graded records as plain dicts, whether the pipeline returns lists or GeoDataFrames."""
    recs = results.get(list_key)
    if recs is not None:
        return list(recs)
    gdf = results.get(gdf_key)
    if gdf is None or len(gdf) == 0:
        return []
    return [dict(row) for _, row in gdf.iterrows()]


def _num(value, dp):
    """Round a possibly-missing numeric field; None survives as None so it can be diffed."""
    return None if value is None else round(float(value), dp)


def build_actual(results, meta, get_artccs, gdf_artcc):
    polygons = []
    for r in _records(results, "graded_forecasts", "gdf_graded_fcst"):
        cov_frac = _num(r.get("coverage_fraction"), COVERAGE_DP)
        entry = {
            "idx": int(r["idx"]),
            "category": r["category"],
            "coverage_code": int(r["coverage"]),
            "feat_type": r["feat_type"],
            "coverage_fraction": cov_frac,
            "top_kft": _num(r.get("top"), TOP_DP),
            "artccs": get_artccs(r["geometry"], gdf_artcc),
            "bounds": _round_bounds(r["geometry"]),
        }
        if is_boundary(cov_frac):
            entry["boundary"] = True
        polygons.append(entry)

    misses = []
    for r in _records(results, "graded_misses", "gdf_graded_miss"):
        misses.append({
            "idx": int(r["idx"]),
            "artccs": get_artccs(r["geometry"], gdf_artcc),
            "bounds": _round_bounds(r["geometry"]),
        })

    categories = {}
    for p in polygons:
        categories[p["category"]] = categories.get(p["category"], 0) + 1

    return {
        "event_id": meta["event_id"],
        "date": meta["date"],
        "issuance_hour": meta["issuance_hour"],
        "lead_time": meta["lead_time"],
        "valid_time_hour": results["valid_dt"].hour,
        "valid_dt": results["valid_dt"].strftime("%Y-%m-%dT%H:%M:%S"),
        "report_text": results["report_text"],
        "polygons": polygons,
        "misses": misses,
        "counts": {
            "polygons": len(polygons),
            "misses": len(misses),
            "verified_well": categories.get("Verified Well", 0),
            "verified_close": categories.get("Verified Close", 0),
            "overforecasted": categories.get("Overforecasted", 0),
            "boundary": sum(1 for p in polygons if p.get("boundary")),
        },
    }


# --- diffing ----------------------------------------------------------------
def _close(field, exp, act, strict):
    if exp is None or act is None:
        return exp == act
    if strict:
        return exp == act
    tol = TOL.get(field)
    if tol is None:
        return exp == act
    return abs(float(exp) - float(act)) <= tol


def _fmt(v):
    return "<missing>" if v is None else repr(v)


def diff_entry(label, exp, act, fields, strict, out):
    """Per-field diff of one polygon/miss record.

    Every differing field is reported independently -- a grade-boundary flip
    shows up as BOTH the coverage_fraction delta and the category change, never
    one standing in for the other. If the polygon is flagged `boundary`, a note
    is appended explaining that the category change is float noise on a cutoff
    rather than a behavioural regression.
    """
    before = len(out)
    for field in fields:
        e, a = exp.get(field), act.get(field)
        if field == "bounds":
            if e is None or a is None or len(e) != len(a):
                if e != a:
                    out.append(f"  {label}.bounds: expected {_fmt(e)}, got {_fmt(a)}")
                continue
            bad = [(i, be, ba) for i, (be, ba) in enumerate(zip(e, a))
                   if not _close("bounds", be, ba, strict)]
            if bad:
                names = ["minx", "miny", "maxx", "maxy"]
                parts = ", ".join(
                    f"{names[i] if i < 4 else i}: {be} -> {ba} (d={ba - be:+.6f})" for i, be, ba in bad)
                out.append(f"  {label}.bounds: {parts}")
            continue

        if not _close(field, e, a, strict):
            if isinstance(e, (int, float)) and not isinstance(e, bool) \
                    and isinstance(a, (int, float)) and not isinstance(a, bool):
                out.append(f"  {label}.{field}: expected {e}, got {a} (d={a - e:+.6f})")
            else:
                out.append(f"  {label}.{field}: expected {_fmt(e)}, got {_fmt(a)}")

    if len(out) == before:
        return
    if exp.get("category") != act.get("category") and (exp.get("boundary") or act.get("boundary")):
        cutoff = min(GRADE_CUTOFFS,
                     key=lambda c: abs((exp.get("coverage_fraction") or 0) - c))
        out.append(f"    note: {label} sits within {BOUNDARY_WINDOW} of the {cutoff:.2f} "
                   f"cutoff -- this category change is expected float sensitivity, "
                   f"not necessarily a regression")


def diff_list(kind, expected, actual, fields, strict, out):
    """Align two record lists by position and diff each; report length changes explicitly."""
    if len(expected) != len(actual):
        out.append(f"  {kind}: count expected {len(expected)}, got {len(actual)}")

    for i in range(max(len(expected), len(actual))):
        e = expected[i] if i < len(expected) else None
        a = actual[i] if i < len(actual) else None
        label = f"{kind}[{i}]"
        if e is None:
            out.append(f"  {label}: unexpected extra entry {json.dumps(a, sort_keys=True)}")
            continue
        if a is None:
            out.append(f"  {label}: missing (expected {json.dumps(e, sort_keys=True)})")
            continue
        diff_entry(f"{label} idx={e.get('idx')}", e, a, fields, strict, out)


def diff_expected(expected, actual, strict):
    out = []

    for field in ("event_id", "date", "issuance_hour", "lead_time", "valid_time_hour", "valid_dt"):
        if expected.get(field) != actual.get(field):
            out.append(f"  meta.{field}: expected {_fmt(expected.get(field))}, got {_fmt(actual.get(field))}")

    exp_counts, act_counts = expected.get("counts", {}), actual.get("counts", {})
    for field in sorted(set(exp_counts) | set(act_counts)):
        if exp_counts.get(field) != act_counts.get(field):
            out.append(f"  counts.{field}: expected {_fmt(exp_counts.get(field))}, got {_fmt(act_counts.get(field))}")

    diff_list("polygons", expected.get("polygons", []), actual.get("polygons", []),
              ("category", "coverage_code", "feat_type", "coverage_fraction",
               "top_kft", "artccs", "idx", "bounds", "boundary"), strict, out)
    diff_list("misses", expected.get("misses", []), actual.get("misses", []),
              ("idx", "artccs", "bounds"), strict, out)

    exp_report, act_report = expected.get("report_text", ""), actual.get("report_text", "")
    if exp_report != act_report:
        out.append("  report_text: differs")
        for line in difflib.unified_diff(exp_report.splitlines(), act_report.splitlines(),
                                         fromfile="expected", tofile="actual", lineterm="", n=1):
            out.append(f"    {line}")

    return out


# --- pass A (hand-captured report from the live app) ------------------------
def _first_byte_difference(exp_bytes, act_bytes):
    """Offset of the first differing byte, and a readable rendering of both sides."""
    limit = min(len(exp_bytes), len(act_bytes))
    for i in range(limit):
        if exp_bytes[i] != act_bytes[i]:
            return i, repr(exp_bytes[i:i + 1]), repr(act_bytes[i:i + 1])
    if len(exp_bytes) == len(act_bytes):
        return None, "", ""
    if len(exp_bytes) > len(act_bytes):
        return limit, repr(exp_bytes[limit:limit + 8]), "<end of file>"
    return limit, "<end of file>", repr(act_bytes[limit:limit + 8])


def diff_pass_a(event_dir, expected):
    """Diff expected.json's report_text against pass_a_report.txt.

    Both sides are rstrip()ed first -- trailing whitespace at end-of-file is an
    editor artifact (a final newline added or dropped on save), not a difference
    in the report. Everything after that trim is byte-exact: no per-line
    whitespace normalisation, no line-ending fixups, no case or unicode folding.
    A stray CR inside the text, a doubled blank line between sections, or a
    trailing space on a mid-report line all still fail.

    The point of pass A is to prove the headless pipeline reproduces what the
    live Streamlit app actually rendered, so invisible differences get a byte
    offset (into the trimmed text) to make them findable.
    """
    out = []
    path = os.path.join(event_dir, PASS_A_FILENAME)
    if not os.path.exists(path):
        return [f"  pass_a: {PASS_A_FILENAME} is missing -- paste the report text from the "
                f"live app into {path}"]

    with open(path, "rb") as f:
        raw_pass_a = f.read()
    raw_report = expected.get("report_text", "").encode("utf-8")

    # bytes.rstrip() with no argument strips trailing ASCII whitespace only at
    # the end of the content -- interior whitespace is untouched.
    pass_a = raw_pass_a.rstrip()
    report = raw_report.rstrip()

    if pass_a == report:
        return out

    offset, exp_ch, act_ch = _first_byte_difference(report, pass_a)
    out.append(f"  pass_a: report_text differs from {PASS_A_FILENAME} "
               f"(expected.json {len(report)} bytes, pass A {len(pass_a)} bytes; "
               f"trailing whitespace trimmed from both)")
    if offset is not None:
        line = report[:offset].count(b"\n") + 1
        col = offset - (report.rfind(b"\n", 0, offset) + 1) + 1
        out.append(f"    first difference at byte {offset} (line {line}, col {col}): "
                   f"expected.json has {exp_ch}, pass A has {act_ch}")

    for l in difflib.unified_diff(report.decode("utf-8", "replace").splitlines(),
                                  pass_a.decode("utf-8", "replace").splitlines(),
                                  fromfile="expected.json:report_text",
                                  tofile=PASS_A_FILENAME, lineterm="", n=1):
        out.append(f"    {l}")
    return out


# --- driver -----------------------------------------------------------------
def check_event(event_dir, pipe, gdf_artcc, strict, pass_a=False, replay=True):
    """Diff one event. Returns (diff_lines, actual_payload_or_None)."""
    event_id = os.path.basename(event_dir)
    with open(os.path.join(event_dir, "expected.json"), "r", encoding="utf-8") as f:
        expected = json.load(f)

    if not replay:
        return diff_pass_a(event_dir, expected), None

    with open(os.path.join(event_dir, "tcf_raw.txt"), "r", encoding="utf-8") as f:
        raw_text = f.read()

    with np.load(os.path.join(event_dir, "arrays.npz")) as npz:
        max_tops = npz["max_tops"]
        max_refl = npz["max_refl"]
        lons = npz["lons"]
        lats = npz["lats"]

    target_date = _datetime.datetime.strptime(expected["date"], "%Y-%m-%d").date()
    issuance_hour = expected["issuance_hour"]
    lead_time = expected["lead_time"]

    gdf_forecast = pipe.get("parse_iem_cow_text")(raw_text)
    valid_dt = pipe.get("compute_valid_dt")(target_date, issuance_hour, lead_time)
    results = pipe.get("run_verification")(
        gdf_forecast, max_tops, max_refl, lons, lats,
        valid_dt, issuance_hour, lead_time, gdf_artcc)

    actual = build_actual(results, {
        "event_id": event_id,
        "date": expected["date"],
        "issuance_hour": issuance_hour,
        "lead_time": lead_time,
    }, pipe.get("get_artccs"), gdf_artcc)

    diffs = diff_expected(expected, actual, strict)
    if pass_a:
        diffs.extend(diff_pass_a(event_dir, expected))
    return diffs, actual


def discover_events(wanted):
    if not os.path.isdir(BASELINE_DIR):
        return []
    dirs = []
    for name in sorted(os.listdir(BASELINE_DIR)):
        path = os.path.join(BASELINE_DIR, name)
        if not os.path.isdir(path):
            continue
        if not os.path.exists(os.path.join(path, "expected.json")):
            continue
        if wanted and name not in wanted:
            continue
        dirs.append(path)
    return dirs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("event_ids", nargs="*", help="event ids to check (default: all captured)")
    ap.add_argument("--pipeline", default=os.environ.get("TCF_PIPELINE"),
                    help="REQUIRED (unless --pass-a-only): module every pipeline symbol "
                         "must come from. No search, no fallback.")
    ap.add_argument("--strict", action="store_true",
                    help="require exact equality instead of one-unit-in-last-place tolerance")
    ap.add_argument("--pass-a", action="store_true",
                    help="also require expected.json's report_text to match "
                         f"{PASS_A_FILENAME} byte for byte, ignoring trailing "
                         "whitespace at end-of-file")
    ap.add_argument("--pass-a-only", action="store_true",
                    help=f"only run the {PASS_A_FILENAME} comparison; skips the replay, "
                         "so --pipeline is not needed")
    ap.add_argument("--allow-network", action="store_true",
                    help="do not install the outbound-socket guard (debugging only)")
    ap.add_argument("--dump-actual", metavar="DIR",
                    help="write each event's recomputed payload to DIR/<event_id>.json")
    args = ap.parse_args(argv)

    replay = not args.pass_a_only
    if replay and not args.pipeline:
        ap.error("--pipeline MODULE is required.\n"
                 "  Every pipeline symbol is resolved from that one module -- there is no\n"
                 "  candidate list and no fallback to baseline.capture, because falling back\n"
                 "  would check a half-moved pipeline against a stale copy of itself.\n"
                 "  To verify the transcription itself: --pipeline baseline.capture\n"
                 "  To skip the replay entirely:        --pass-a-only")

    if not args.allow_network:
        block_network()

    # Import after the guard so a pipeline module cannot fetch anything at import time.
    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(1, BASELINE_DIR)

    event_dirs = discover_events(set(args.event_ids))
    if not event_dirs:
        target = ", ".join(args.event_ids) if args.event_ids else "any event"
        print(f"ERROR: no captured baselines found for {target} under {BASELINE_DIR}.\n"
              f"       Run `python baseline/capture.py` first.", file=sys.stderr)
        return 2

    pipe, gdf_artcc = None, None
    if replay:
        # bind() resolves every required symbol up front, so a partially-moved
        # pipeline fails here rather than part-way through the first event.
        pipe = Pipeline(args.pipeline).bind()
        gdf_artcc = pipe.get("load_artccs")()
        print("pipeline symbols resolved from:")
        print(pipe.describe())
        print(f"mode: {'strict (exact)' if args.strict else 'tolerant (one unit in last stored decimal)'}"
              + (f" + pass A ({PASS_A_FILENAME}, byte-exact after end-of-file trim)" if args.pass_a else ""))
    else:
        print(f"mode: pass A only ({PASS_A_FILENAME}, byte-exact after end-of-file trim); replay skipped")
    print()

    failed, errored = [], []
    for event_dir in event_dirs:
        event_id = os.path.basename(event_dir)
        try:
            diffs, actual = check_event(event_dir, pipe, gdf_artcc, args.strict,
                                        pass_a=args.pass_a, replay=replay)
        except Exception as exc:
            errored.append(event_id)
            print(f"ERROR {event_id}: {type(exc).__name__}: {exc}")
            continue

        if args.dump_actual and actual is not None:
            os.makedirs(args.dump_actual, exist_ok=True)
            with open(os.path.join(args.dump_actual, f"{event_id}.json"), "w", encoding="utf-8") as f:
                json.dump(actual, f, indent=2)

        if diffs:
            failed.append(event_id)
            top_level = sum(1 for line in diffs if not line.startswith("    "))
            print(f"FAIL  {event_id}  ({top_level} difference(s))")
            for line in diffs:
                print(line)
            print()
        elif actual is None:
            print(f"PASS  {event_id}  (pass A report matches)")
        else:
            counts = actual["counts"]
            # An all-empty event is a legitimate baseline, but say so out loud --
            # "PASS (0 polygons, 0 misses)" reads very differently from a bare PASS
            # and stops an accidentally-empty capture from looking like a green run.
            empty = " -- NOTHING GRADED" if counts["polygons"] == 0 and counts["misses"] == 0 else ""
            boundary = f", {counts['boundary']} near a cutoff" if counts.get("boundary") else ""
            print(f"PASS  {event_id}  ({counts['polygons']} polygons, "
                  f"{counts['misses']} misses{boundary}){empty}")

    print()
    total = len(event_dirs)
    ok = total - len(failed) - len(errored)
    print(f"{ok}/{total} event(s) match baseline"
          + (f"; failed: {', '.join(failed)}" if failed else "")
          + (f"; errored: {', '.join(errored)}" if errored else ""))

    if errored:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
