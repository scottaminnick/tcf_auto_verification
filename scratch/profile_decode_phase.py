#!/usr/bin/env python3
"""Account for every second inside build_composite's decode phase.

Scratch tool, NOT part of `make test`.

Replicates the decode phase step by step, exactly as
tcf_pipeline._read_scan_arrays and the fold loop in build_composite do it.

WHAT IT FOUND, so a reader does not have to run it:

There is no hidden processing step. In steady state the phase is ~10s for 14
files and the strided GRIB read is 97.5% of it; open_dataset, the unit
conversion, the coordinate wrap, the running-max fold and gc.collect together
come to ~0.3s.

The rest of the ~34s originally attributed to "decode" is first-touch memory
cost, not work. Each read allocates ~374 MB transiently to produce a 3.92 MB
decimated array -- a 95x amplification, 5.2 GB per composite -- because the full
24.5M-point field is materialised as float64 and again as float32 before the
[::5, ::5] slice throws 96% of it away. In a fresh process those pages are
faulted from the OS and zeroed: pass 1 costs ~20s and ~941k minor faults, pass 2
onward ~10s and ~177k. Same work, half the wall-clock.

So the "~0.9s per file" from scratch/bench_grib_decode.py is a warm-allocator
number, and the ~34s figure was measured in a process that had just downloaded
and gunzipped 14 files. Neither was wrong; they were measured in different
memory states, which is the whole finding.

Steps measured, per scan and per product:

    gunzip           .grib2.gz -> .grib2  (NB: this lives in the DOWNLOAD phase,
                     inside _download_key, not in the decode phase -- measured
                     here because it was asked for, and reported separately)
    open_dataset     cfgrib index construction
    read+decimate    ds.unknown[::5, ::5].values -- the lazy strided read, which
                     is decode + missing-value handling + decimation in one call
    unit convert     * 3.28084, tops only
    coords           longitude/latitude + the >180 wrap, first scan only
    fold             np.maximum against the running max
    gc.collect       build_composite calls this once per scan

Two passes over the same work: one for wall-clock with no instrumentation, one
with tracemalloc for peak allocation. tracemalloc roughly doubles runtime, so
mixing them would corrupt the timings.

The point of the reconciliation table at the end is that sum-of-steps is
compared against the measured phase wall-clock, and any gap is reported as
UNACCOUNTED rather than quietly absorbed.

Usage:
    python scratch/profile_decode_phase.py [--cache DIR] [--repeat N]

Needs the 14 files of one composite already on disk (see --cache); it never
touches the network.
"""

import argparse
import gc
import glob
import gzip
import os
import re
import shutil
import resource
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

DEFAULT_CACHE = os.path.join(REPO_ROOT, "scratch", "composite_cache")
STEP = 5              # tcf_pipeline.COMPOSITE_STEP
FT_PER_KM = 3.28084   # the unit conversion in _read_scan_arrays

STEPS = ["open_dataset", "read+decimate", "unit convert", "coords", "fold", "gc.collect"]


def scan_pairs(cache_dir):
    """[(scan_stamp, tops_path, refl_path)] sorted by scan time, as the fold order."""
    by_stamp = defaultdict(dict)
    for path in glob.glob(os.path.join(cache_dir, "*.grib2")):
        name = os.path.basename(path)
        m = re.search(r"(\d{8}-\d{6})", name)
        if not m:
            continue
        kind = "tops" if "EchoTop" in name else "refl"
        by_stamp[m.group(1)][kind] = path
    pairs = [(stamp, d["tops"], d["refl"])
             for stamp, d in sorted(by_stamp.items()) if len(d) == 2]
    if not pairs:
        raise SystemExit(f"no scan pairs in {cache_dir}")
    return pairs


# --- the decode phase, instrumented -----------------------------------------
def decode_phase(pairs, record, track_alloc=False):
    """Replicates _read_scan_arrays + build_composite's fold, timing each step.

    `record(product, step, seconds, peak_bytes)` is called for every step.
    """
    import xarray as xr

    max_tops = max_refl = None
    lons = lats = None

    def measure(product, step, fn):
        if track_alloc:
            gc.collect()
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
        t0 = time.perf_counter()
        out = fn()
        dt = time.perf_counter() - t0
        peak = 0
        if track_alloc:
            peak = tracemalloc.get_traced_memory()[1] - before
        record(product, step, dt, peak)
        return out

    for stamp, tops_path, refl_path in pairs:
        ds_t = measure("tops", "open_dataset", lambda: xr.open_dataset(
            tops_path, engine="cfgrib", backend_kwargs={"indexpath": ""}))
        ds_r = measure("refl", "open_dataset", lambda: xr.open_dataset(
            refl_path, engine="cfgrib", backend_kwargs={"indexpath": ""}))

        # The lazy strided read: decode, missing-value handling and decimation
        # all happen inside this one call, which is why they cannot be split
        # further without reaching past cfgrib's public surface.
        raw_tops = measure("tops", "read+decimate",
                           lambda: ds_t.unknown[::STEP, ::STEP].values)
        curr_refl = measure("refl", "read+decimate",
                            lambda: ds_r.unknown[::STEP, ::STEP].values)

        curr_tops = measure("tops", "unit convert", lambda: raw_tops * FT_PER_KM)

        if lons is None:
            def _coords():
                lo = ds_t.longitude[::STEP].values
                lo = np.where(lo > 180, lo - 360, lo)
                la = ds_t.latitude[::STEP].values
                return lo, la
            lons, lats = measure("tops", "coords", _coords)
        else:
            record("tops", "coords", 0.0, 0)

        if max_tops is None:
            max_tops, max_refl = curr_tops, curr_refl
            record("tops", "fold", 0.0, 0)
            record("refl", "fold", 0.0, 0)
        else:
            max_tops = measure("tops", "fold", lambda: np.maximum(max_tops, curr_tops))
            max_refl = measure("refl", "fold", lambda: np.maximum(max_refl, curr_refl))

        ds_t.close()
        ds_r.close()
        # tcf_pipeline uses `del` here; rebinding to None drops the same
        # references and keeps this file lint-clean.
        ds_t = ds_r = curr_tops = curr_refl = raw_tops = None
        measure("both", "gc.collect", gc.collect)

    return max_tops, max_refl, lons, lats


def measure_gunzip(cache_dir, record, track_alloc=False):
    """gunzip cost, for the record. This is download-phase work, not decode."""
    for gz in sorted(glob.glob(os.path.join(cache_dir, "*.gz"))):
        product = "tops" if "EchoTop" in os.path.basename(gz) else "refl"
        out = os.path.join(cache_dir, "_gunzip_probe.tmp")
        if track_alloc:
            gc.collect()
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
        t0 = time.perf_counter()
        with gzip.open(gz, "rb") as f_in, open(out, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        dt = time.perf_counter() - t0
        peak = (tracemalloc.get_traced_memory()[1] - before) if track_alloc else 0
        record(product, "gunzip", dt, peak)
        os.remove(out)


def missing_value_report(pairs):
    """What missing-value handling actually costs here -- which is nothing."""
    import eccodes
    import xarray as xr

    out = []
    for label, path in (("tops", pairs[0][1]), ("refl", pairs[0][2])):
        with open(path, "rb") as fh:
            gid = eccodes.codes_grib_new_from_file(fh)
            bitmap = eccodes.codes_get(gid, "bitmapPresent")
            missing = eccodes.codes_get(gid, "missingValue")
            eccodes.codes_release(gid)
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
        vals = ds.unknown.values
        n_nan = int(np.isnan(vals).sum())
        n_at_missing = int((vals == missing).sum())
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        ds.close()
        out.append(f"{label}: bitmapPresent={bitmap}, missingValue={missing:g}, "
                   f"NaN cells={n_nan}, cells equal to missingValue={n_at_missing}, "
                   f"range [{lo:g}, {hi:g}]")
    return out


def fmt_bytes(n):
    if n <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--repeat", type=int, default=3,
                    help="timing passes; the median pass is reported (default 3)")
    args = ap.parse_args()

    pairs = scan_pairs(args.cache)
    print(f"composite: {len(pairs)} scans x 2 products = {len(pairs) * 2} files "
          f"from {args.cache}")
    print(f"decimation [::{STEP}, ::{STEP}], unit factor {FT_PER_KM}")

    # --- pass 1: wall-clock, no instrumentation -----------------------------
    totals = []
    per_run = []
    print(f"\n  {'pass':<6} {'wall':>8} {'minor faults':>14} {'major faults':>13} {'RSS after':>11}")
    for run in range(args.repeat):
        times = defaultdict(list)

        def rec(product, step, dt, _peak):
            times[(product, step)].append(dt)

        gc.collect()
        ru0 = resource.getrusage(resource.RUSAGE_SELF)
        t0 = time.perf_counter()
        decode_phase(pairs, rec)
        wall = time.perf_counter() - t0
        ru1 = resource.getrusage(resource.RUSAGE_SELF)
        totals.append(wall)
        per_run.append(dict(times))
        print(f"  {run + 1:<6} {wall:>7.2f}s {ru1.ru_minflt - ru0.ru_minflt:>14,} "
              f"{ru1.ru_majflt - ru0.ru_majflt:>13,} {ru1.ru_maxrss / 1024:>10.0f}MB")
    if len(totals) > 1:
        print(f"\n  first pass {totals[0]:.2f}s vs steady-state "
              f"{statistics.median(totals[1:]):.2f}s "
              f"({totals[0] / statistics.median(totals[1:]):.1f}x). Minor faults are the "
              f"tell: each read+decimate allocates ~374 MB transiently, and on the first\n"
              f"  pass those pages are faulted in from the OS and zeroed; afterwards the "
              f"allocator reuses its arena.")

    median_wall = statistics.median(totals)
    chosen = per_run[totals.index(sorted(totals)[len(totals) // 2])]

    # --- pass 2: peak allocation --------------------------------------------
    peaks = defaultdict(list)

    def rec_alloc(product, step, _dt, peak):
        peaks[(product, step)].append(peak)

    tracemalloc.start()
    decode_phase(pairs, rec_alloc, track_alloc=True)
    measure_gunzip(args.cache, rec_alloc, track_alloc=True)
    tracemalloc.stop()

    gz_times = defaultdict(list)
    measure_gunzip(args.cache, lambda p, s, dt, _pk: gz_times[(p, s)].append(dt))

    # --- report --------------------------------------------------------------
    print(f"\n{'=' * 92}")
    print(f"DECODE PHASE, {len(pairs)} scans   (median of {args.repeat} passes: "
          f"{median_wall:.2f}s)")
    print(f"{'=' * 92}")
    print(f"{'step':<16} {'product':<8} {'n':>3} {'total':>9} {'per call':>10} "
          f"{'% phase':>8} {'peak alloc':>12}")
    print("-" * 92)

    accounted = 0.0
    for step in STEPS:
        for product in ("tops", "refl", "both"):
            samples = chosen.get((product, step))
            if not samples:
                continue
            total = sum(samples)
            nonzero = [s for s in samples if s > 0]
            per = statistics.median(nonzero) if nonzero else 0.0
            accounted += total
            pk = peaks.get((product, step)) or [0]
            print(f"{step:<16} {product:<8} {len(samples):>3} {total:>8.2f}s "
                  f"{per:>9.3f}s {total / median_wall * 100:>7.1f}% "
                  f"{fmt_bytes(max(pk)):>12}")

    print("-" * 92)
    print(f"{'sum of steps':<29} {accounted:>8.2f}s          "
          f"{accounted / median_wall * 100:>7.1f}%")
    gap = median_wall - accounted
    print(f"{'UNACCOUNTED':<29} {gap:>8.2f}s          {gap / median_wall * 100:>7.1f}%"
          "   (loop overhead, ds.close(), del)")

    print(f"\n{'=' * 92}\nNOT part of the decode phase\n{'=' * 92}")
    for (product, step), samples in sorted(gz_times.items()):
        pk = peaks.get((product, step)) or [0]
        print(f"{step:<16} {product:<8} {len(samples):>3} {sum(samples):>8.2f}s "
              f"{statistics.median(samples):>9.3f}s {'':>8} {fmt_bytes(max(pk)):>12}"
              "   runs in _download_key, inside the fetch phase")

    print(f"\n{'=' * 92}\nmissing-value handling\n{'=' * 92}")
    for line in missing_value_report(pairs):
        print(f"  {line}")


if __name__ == "__main__":
    main()
