#!/usr/bin/env python3
"""Replay the frozen baselines and diff them against expected.json.

For every ``baseline/<event_id>/`` directory this loads ``arrays.npz`` and
``tcf_raw.txt``, re-runs the verification math through whichever module the
pipeline currently lives in, and compares the result field-by-field against
``expected.json``. On a mismatch it prints exactly which field of which polygon
moved and by how much -- not just a pass/fail.

The pipeline is resolved by name, one symbol at a time, over a list of candidate
modules (see PIPELINE_CANDIDATES). Whichever module is found first wins, so as
the refactor moves code out of app.py into a real module this script follows it
without edits; ``baseline.capture`` is the last resort. Override with
``--pipeline <module>`` or ``TCF_PIPELINE=<module>``.

Required pipeline symbols:
    compute_valid_dt(date, issuance_hour, lead_time) -> datetime
    parse_iem_cow_text(raw_text) -> GeoDataFrame
    run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                     valid_dt, issuance_hour, lead_time, gdf_artcc) -> dict
    load_artccs() -> GeoDataFrame          (falls back to baseline.capture)
    get_artccs(poly, artcc_gdf) -> str     (falls back to baseline.capture)

This script never touches the network -- outbound sockets are blocked at
import time so an accidental IEM/S3 call fails loudly instead of silently
re-fetching data. Use --allow-network only when debugging that guard.

Usage:
    python baseline/check.py                    # check every captured event
    python baseline/check.py 20260524_19z_f04   # check specific event ids
    python baseline/check.py --strict           # require exact equality
    python baseline/check.py --pipeline tcf_core

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

# Searched in order; the first module exposing a given symbol provides it.
# Speculative names come first so a future refactor is picked up automatically;
# the frozen copy in baseline/capture.py is the fallback.
PIPELINE_CANDIDATES = (
    "tcf_pipeline",
    "tcf_core",
    "pipeline",
    "core",
    "verification",
    "baseline.capture",
    "capture",
)

# Rounding quanta from capture.py -- a difference of one unit in the last stored
# decimal is rounding noise, anything larger is a real change. --strict compares
# the stored values exactly instead.
TOL = {"coverage_fraction": 1e-4, "top_kft": 1e-2, "bounds": 1e-4}


# --- network guard ----------------------------------------------------------
def block_network():
    """Make any outbound TCP/UDP connection raise. Local/unix sockets still work."""
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
    """Resolves pipeline symbols across candidate modules and records where each came from."""

    def __init__(self, preferred=None):
        candidates = list(PIPELINE_CANDIDATES)
        if preferred:
            candidates.insert(0, preferred)

        self._modules = []
        self._errors = {}
        for name in candidates:
            try:
                self._modules.append((name, importlib.import_module(name)))
            except Exception as exc:  # not installed / not written yet / import error
                self._errors[name] = f"{type(exc).__name__}: {exc}"
        self.sources = {}

    def get(self, symbol, required=True):
        for name, mod in self._modules:
            fn = getattr(mod, symbol, None)
            if fn is not None:
                self.sources[symbol] = name
                return fn
        if required:
            tried = ", ".join(n for n, _ in self._modules) or "(no candidate module imported)"
            detail = "".join(f"\n    {k}: {v}" for k, v in self._errors.items())
            raise SystemExit(
                f"ERROR: could not find `{symbol}` in any candidate module.\n"
                f"  searched: {tried}\n"
                f"  import failures:{detail or ' none'}\n"
                f"  pass --pipeline <module> to point at the refactored pipeline."
            )
        self.sources[symbol] = None
        return None

    def describe(self):
        width = max(len(s) for s in self.sources)
        return "\n".join(f"  {s.ljust(width)} <- {m}" for s, m in sorted(self.sources.items()))


# --- payload rebuilding -----------------------------------------------------
COVERAGE_DP = 4
TOP_DP = 2


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
        polygons.append({
            "idx": int(r["idx"]),
            "category": r["category"],
            "coverage_code": int(r["coverage"]),
            "feat_type": r["feat_type"],
            "coverage_fraction": _num(r.get("coverage_fraction"), COVERAGE_DP),
            "top_kft": _num(r.get("top"), TOP_DP),
            "artccs": get_artccs(r["geometry"], gdf_artcc),
            "bounds": _round_bounds(r["geometry"]),
        })

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
    """Per-field diff of one polygon/miss record."""
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
            if isinstance(e, (int, float)) and isinstance(a, (int, float)):
                out.append(f"  {label}.{field}: expected {e}, got {a} (d={a - e:+.6f})")
            else:
                out.append(f"  {label}.{field}: expected {_fmt(e)}, got {_fmt(a)}")


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
               "top_kft", "artccs", "idx", "bounds"), strict, out)
    diff_list("misses", expected.get("misses", []), actual.get("misses", []),
              ("idx", "artccs", "bounds"), strict, out)

    exp_report, act_report = expected.get("report_text", ""), actual.get("report_text", "")
    if exp_report != act_report:
        out.append("  report_text: differs")
        for line in difflib.unified_diff(exp_report.splitlines(), act_report.splitlines(),
                                         fromfile="expected", tofile="actual", lineterm="", n=1):
            out.append(f"    {line}")

    return out


# --- driver -----------------------------------------------------------------
def check_event(event_dir, pipe, gdf_artcc, strict):
    event_id = os.path.basename(event_dir)
    with open(os.path.join(event_dir, "expected.json"), "r", encoding="utf-8") as f:
        expected = json.load(f)
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

    return diff_expected(expected, actual, strict), actual


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
                    help="module to import the pipeline from (tried before the defaults)")
    ap.add_argument("--strict", action="store_true",
                    help="require exact equality instead of one-unit-in-last-place tolerance")
    ap.add_argument("--allow-network", action="store_true",
                    help="do not install the outbound-socket guard (debugging only)")
    ap.add_argument("--dump-actual", metavar="DIR",
                    help="write each event's recomputed payload to DIR/<event_id>.json")
    args = ap.parse_args(argv)

    if not args.allow_network:
        block_network()

    # Import after the guard so a pipeline module cannot fetch anything at import time.
    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(1, BASELINE_DIR)
    pipe = Pipeline(args.pipeline)

    event_dirs = discover_events(set(args.event_ids))
    if not event_dirs:
        target = ", ".join(args.event_ids) if args.event_ids else "any event"
        print(f"ERROR: no captured baselines found for {target} under {BASELINE_DIR}.\n"
              f"       Run `python baseline/capture.py` first.", file=sys.stderr)
        return 2

    # Resolve everything up front so the table below is complete and a missing
    # symbol fails immediately rather than part-way through the first event.
    for symbol in ("compute_valid_dt", "parse_iem_cow_text", "run_verification",
                   "load_artccs", "get_artccs"):
        pipe.get(symbol)

    gdf_artcc = pipe.get("load_artccs")()
    print("pipeline symbols resolved from:")
    print(pipe.describe())
    print(f"mode: {'strict (exact)' if args.strict else 'tolerant (one unit in last stored decimal)'}")
    print()

    failed, errored = [], []
    for event_dir in event_dirs:
        event_id = os.path.basename(event_dir)
        try:
            diffs, actual = check_event(event_dir, pipe, gdf_artcc, args.strict)
        except Exception as exc:
            errored.append(event_id)
            print(f"ERROR {event_id}: {type(exc).__name__}: {exc}")
            continue

        if args.dump_actual:
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
        else:
            counts = actual["counts"]
            print(f"PASS  {event_id}  ({counts['polygons']} polygons, {counts['misses']} misses)")

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
