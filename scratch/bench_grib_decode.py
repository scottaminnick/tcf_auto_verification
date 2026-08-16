#!/usr/bin/env python3
"""Benchmark GRIB decode paths on one cached MRMS file per product.

Scratch tool, deliberately NOT part of `make test`: it needs files on disk and
takes a couple of minutes. It exists to answer one question -- after the fetch
speedup, cfgrib decode is ~94% of a composite build, so is there a faster reader
that returns the SAME bytes?

Paths compared, per product:
  a. cfgrib/xarray, indexpath='' ......... exactly what tcf_pipeline does today
     cfgrib/xarray, default .idx (cold) ... sidecar written this run
     cfgrib/xarray, default .idx (warm) ... sidecar already on disk
  b. eccodes low-level ................... codes_grib_new_from_file ->
                                           codes_get_values -> reshape(Nj, Ni)
  c. pygrib .............................. grbs[1].values

Every path is checked against the cfgrib result with tobytes(). A faster path
that returns different bytes is not a candidate and is reported as such rather
than as a win.

The cfgrib timing is split into open_dataset (index construction) and the first
.values access (actual unpacking), since only the second is irreducible work.

Usage:
    python scratch/bench_grib_decode.py [--runs N] [--cache DIR]

Files are fetched into the cache dir on first run (needs S3); after that it is
offline. Reported numbers are medians over N runs after a discarded warm-up, so
they measure decode, not page-cache misses.
"""

import argparse
import gc
import os
import statistics
import sys
import time
from datetime import datetime

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

DEFAULT_CACHE = os.path.join(REPO_ROOT, "scratch", "grib_cache")
SAMPLE_SCAN = datetime(2026, 5, 24, 23, 0)   # the primary dev event's valid time
DECIMATION = 5                                # tcf_pipeline's COMPOSITE_STEP


def ensure_files(cache_dir):
    """One file per product in cache_dir, downloading only what is missing."""
    import tcf_pipeline as tp

    os.makedirs(cache_dir, exist_ok=True)
    found = {}
    for product in (tp.TOPS_PRODUCT, tp.REFL_PRODUCT):
        existing = sorted(f for f in os.listdir(cache_dir)
                          if f.startswith(f"MRMS_{product}_") and f.endswith(".grib2"))
        if existing:
            found[product] = os.path.join(cache_dir, existing[0])
            continue
        print(f"fetching a {product} sample...", file=sys.stderr)
        s3 = tp._s3_client()
        key = tp._resolve_scan_key(product, SAMPLE_SCAN, s3=s3)
        if key is None:
            raise SystemExit(f"no {product} scan near {SAMPLE_SCAN}")
        found[product] = tp._download_key(key, cache_dir, s3=s3)
    return found


def idx_files(path):
    d = os.path.dirname(path) or "."
    base = os.path.basename(path)
    return [os.path.join(d, f) for f in os.listdir(d)
            if f.startswith(base) and f.endswith(".idx")]


def clear_idx(path):
    for f in idx_files(path):
        os.remove(f)


# --- decode paths -----------------------------------------------------------
USE_CFGRIB_DEFAULT_INDEX = object()   # sentinel: omit backend_kwargs entirely


def read_cfgrib(path, indexpath=""):
    """Returns (values, lons, lats, t_open, t_values).

    Pass USE_CFGRIB_DEFAULT_INDEX to let cfgrib apply its own default
    ('{path}.{short_hash}.idx'), which is what writes a sidecar. Passing
    indexpath=None does NOT mean "default" -- it disables the sidecar, which is
    how the first version of this script accidentally measured no-index twice.
    """
    import xarray as xr

    kwargs = ({} if indexpath is USE_CFGRIB_DEFAULT_INDEX
              else {"backend_kwargs": {"indexpath": indexpath}})
    t0 = time.perf_counter()
    ds = xr.open_dataset(path, engine="cfgrib", **kwargs)
    t_open = time.perf_counter() - t0

    t0 = time.perf_counter()
    values = ds.unknown.values
    t_values = time.perf_counter() - t0

    lons = ds.longitude.values
    lats = ds.latitude.values
    ds.close()
    return values, lons, lats, t_open, t_values


def read_eccodes(path):
    """codes_get_values + reshape. Returns (values, lons, lats)."""
    import eccodes

    with open(path, "rb") as fh:
        gid = eccodes.codes_grib_new_from_file(fh)
        if gid is None:
            raise RuntimeError("no GRIB message")
        try:
            ni = eccodes.codes_get(gid, "Ni")
            nj = eccodes.codes_get(gid, "Nj")
            flat = eccodes.codes_get_values(gid)
            # scanningMode 0 / jScansPositively 0 -> row-major, north to south,
            # which is the orientation cfgrib hands back.
            values = flat.reshape(nj, ni)
            lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
            lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
            di = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
            dj = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
            lons = lon0 + np.arange(ni) * di
            lats = lat0 - np.arange(nj) * dj
        finally:
            eccodes.codes_release(gid)
    return values, lons, lats


def read_pygrib(path, with_coords=False):
    """values only by default.

    msg.latlons() materialises a full 24.5M-point lat AND lon grid, which costs
    an order of magnitude more than the decode itself. The pipeline needs
    coordinates once per composite, not once per scan, so timing them on every
    read would badly misrepresent pygrib.
    """
    import pygrib

    grbs = pygrib.open(path)
    try:
        msg = grbs[1]
        values = msg.values
        if np.ma.isMaskedArray(values):
            values = values.filled(np.nan)
        if not with_coords:
            return np.asarray(values), None, None
        lats, lons = msg.latlons()
        return np.asarray(values), lons[0, :], lats[:, 0]
    finally:
        grbs.close()


# --- harness ----------------------------------------------------------------
def timed(fn, runs):
    """Median wall-clock over `runs`, after one discarded warm-up."""
    fn()
    samples = []
    for _ in range(runs):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples), min(samples), max(samples)


def run_isolated(path, which, runs):
    """Benchmark ONE decode path in a fresh subprocess, returning its samples.

    cfgrib, pygrib and the eccodes python module all bind the same ecCodes C
    library. Timing them in one process makes them interfere: measured in a
    round-robin loop next to the other two, cfgrib read the same field in ~1.85s,
    versus ~0.89s when it was the only reader in the process. Neither number is
    "the" cost until they stop sharing a process, so each path gets its own.
    """
    import json
    import subprocess

    out = subprocess.run(
        [sys.executable, os.path.abspath(__file__),
         "--worker", which, "--file", path, "--runs", str(runs)],
        capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"worker {which} produced no result:\n{out.stdout}\n{out.stderr[-2000:]}")


def timed_interleaved(named_fns, runs):
    """Time several paths round-robin rather than one after another.

    This box shows occasional multi-hundred-millisecond stalls. Running all N
    samples of one path consecutively lets a stall land entirely on that path
    and shift its median -- the first version of this script reported cfgrib at
    1.37s median against its own 0.83s minimum for exactly that reason.
    Interleaving spreads any interference across every path instead.
    """
    for _, fn in named_fns:
        fn()                                    # warm-up, discarded
    samples = {name: [] for name, _ in named_fns}
    n = len(named_fns)
    for r in range(runs):
        # Rotate the order every round. Whichever path runs first in a round
        # pays for whatever the previous round left behind (allocator state,
        # page cache pressure), and with a fixed order that cost lands on the
        # same path every time -- which is exactly how the first interleaved
        # version made cfgrib look 68% slower than two identical cfgrib rows.
        for i in range(n):
            name, fn = named_fns[(r + i) % n]
            gc.collect()
            t0 = time.perf_counter()
            fn()
            samples[name].append(time.perf_counter() - t0)
    return {name: (statistics.median(v), min(v), max(v)) for name, v in samples.items()}


def as_pipeline_array(values):
    """What tcf_pipeline actually consumes: float32, decimated by COMPOSITE_STEP.

    The decimation is a plain numpy slice and is identical for every reader, so
    comparing here rather than on the full grid keeps the check honest while
    matching what the pipeline stores.
    """
    return np.asarray(values, dtype=np.float32)[::DECIMATION, ::DECIMATION]


def compare(name, got, reference, out):
    """Bit-identity against the cfgrib reference, reported not assumed."""
    ref_full, ref_dec = reference
    got_full = np.asarray(got)
    verdict = []

    if got_full.shape != ref_full.shape:
        out.append(f"      {name}: shape {got_full.shape} != cfgrib {ref_full.shape}")
        return False

    same_raw = (got_full.dtype == ref_full.dtype
                and got_full.tobytes() == ref_full.tobytes())
    dec = as_pipeline_array(got_full)
    same_pipeline = dec.tobytes() == ref_dec.tobytes()

    if got_full.dtype != ref_full.dtype:
        verdict.append(f"dtype {got_full.dtype} vs cfgrib {ref_full.dtype}")
    if not same_raw and got_full.dtype == ref_full.dtype:
        verdict.append("raw bytes differ")
    verdict.append("pipeline array BIT-IDENTICAL" if same_pipeline
                   else "pipeline array DIFFERS")

    if not same_pipeline:
        diff = np.abs(dec.astype(np.float64) - ref_dec.astype(np.float64))
        verdict.append(f"max|delta|={np.nanmax(diff):.6g}, "
                       f"{int(np.count_nonzero(diff))} of {diff.size} cells")
    out.append(f"      {name}: " + "; ".join(verdict))
    return same_pipeline


def bench_product(product, path, runs):
    print(f"\n{'=' * 78}\n{product}\n  {os.path.basename(path)}  "
          f"({os.path.getsize(path) / 1e6:.1f} MB on disk)")

    import eccodes
    with open(path, "rb") as fh:
        gid = eccodes.codes_grib_new_from_file(fh)
        meta = {k: eccodes.codes_get(gid, k) for k in
                ("Ni", "Nj", "packingType", "bitmapPresent")}
        eccodes.codes_release(gid)
    print(f"  grid {meta['Nj']}x{meta['Ni']} = {meta['Ni'] * meta['Nj'] / 1e6:.1f}M points, "
          f"packing={meta['packingType']}, bitmap={meta['bitmapPresent']}")

    # --- correctness first, in this process (timing-insensitive) ------------
    clear_idx(path)
    ref_full, ref_lons, ref_lats, _, _ = read_cfgrib(path, indexpath="")
    reference = (ref_full, as_pipeline_array(ref_full))
    identity, notes = [], []

    ecc_ok = pyg_ok = None
    try:
        ecc_vals, ecc_lons, ecc_lats = read_eccodes(path)
        ecc_ok = compare("eccodes", ecc_vals, reference, identity)
        identity.append(f"      eccodes lons/lats match cfgrib: "
                        f"{np.allclose(ecc_lons, ref_lons) and np.allclose(ecc_lats, ref_lats)}")
    except Exception as exc:
        identity.append(f"      eccodes: FAILED {type(exc).__name__}: {exc}")
    try:
        pg_vals, _, _ = read_pygrib(path)
        pyg_ok = compare("pygrib", pg_vals, reference, identity)
    except Exception as exc:
        identity.append(f"      pygrib: FAILED {type(exc).__name__}: {exc}")

    # Drop the comparison arrays BEFORE timing anything. Three full-grid copies
    # (one float32 + two float64 over 24.5M points) is ~500 MB held in the
    # parent, and leaving it resident while a worker allocates its own copy put
    # the first worker under memory pressure -- reflectivity's cfgrib median read
    # 1.93s that way against a 0.89s minimum and a 0.90s isolated re-measurement.
    ref_full = ref_lons = ref_lats = reference = None
    ecc_vals = ecc_lons = ecc_lats = pg_vals = None
    gc.collect()

    # --- timing, one process per path ---------------------------------------
    clear_idx(path)
    results = {}
    for which in ("cfgrib", "eccodes", "pygrib"):
        results[which] = run_isolated(path, which, runs)
    # cfgrib with its default sidecar: first worker run writes it, the rest read
    # it, so this median is the warm case.
    clear_idx(path)
    results["cfgrib_idx"] = run_isolated(path, "cfgrib_idx", runs)
    wrote_idx = bool(idx_files(path))

    labels = {"cfgrib": "cfgrib indexpath='' (current)",
              "eccodes": "eccodes low-level",
              "pygrib": "pygrib (values only)",
              "cfgrib_idx": "cfgrib default .idx (warm)"}
    verdicts = {"cfgrib": "reference",
                "eccodes": "candidate" if ecc_ok else "NOT a candidate (bytes differ)",
                "pygrib": "candidate" if pyg_ok else "NOT a candidate (bytes differ)",
                "cfgrib_idx": ""}
    rows = [(labels[k], results[k]["median"], results[k]["min"], results[k]["max"],
             verdicts[k]) for k in ("cfgrib", "eccodes", "pygrib", "cfgrib_idx")]

    cf = results["cfgrib"]
    notes.append(f"cfgrib split: open_dataset (index build) {cf['open_median']:.3f}s, "
                 f"first .values (unpack) {cf['values_median']:.3f}s "
                 f"-> {cf['values_median'] / (cf['open_median'] + cf['values_median']) * 100:.1f}%"
                 f" of a cfgrib read is unpacking, not indexing")
    ci = results["cfgrib_idx"]
    notes.append(f"default .idx sidecar written: {wrote_idx}"
                 + (f" ({os.path.getsize(idx_files(path)[0])} bytes)" if wrote_idx else "")
                 + f"; open_dataset {ci['open_median']:.3f}s with a warm sidecar vs "
                   f"{cf['open_median']:.3f}s building the index in memory. Both are ~2% of "
                   f"the read -- the sidecar cannot touch the unpack, which is the other 98%.")
    clear_idx(path)

    if pyg_ok is not None:
        coords = run_isolated(path, "pygrib_coords", max(3, runs // 3))
        notes.append(f"pygrib latlons() takes the read from {results['pygrib']['median']:.3f}s "
                     f"to {coords['median']:.3f}s -- it materialises a full 24.5M-point lat AND "
                     f"lon grid. The pipeline needs coordinates once per composite, not per scan.")

    cfgrib_meds = [results["cfgrib"]["median"], results["cfgrib_idx"]["median"]]
    spread = (max(cfgrib_meds) - min(cfgrib_meds)) / min(cfgrib_meds) * 100
    notes.append(f"NOISE CONTROL: the 2 cfgrib rows unpack the same field and should agree; "
                 f"they span {min(cfgrib_meds):.3f}-{max(cfgrib_meds):.3f}s ({spread:.0f}%). "
                 f"Treat any speedup smaller than that as unproven on this box.")

    print(f"\n  {'path':<32} {'median':>8} {'min':>8} {'max':>8}   vs cfgrib")
    base = rows[0][1]
    for name, med, lo, hi, note in rows:
        if med is None:
            print(f"  {name:<32} {'--':>8} {'--':>8} {'--':>8}   {note}")
            continue
        print(f"  {name:<32} {med:7.3f}s {lo:7.3f}s {hi:7.3f}s   {base / med:5.2f}x  {note}")

    print()
    for n in notes:
        print(f"    - {n}")
    for line in identity:
        print(line)
    return rows


WORKERS = {
    "cfgrib": lambda path: read_cfgrib(path, indexpath=""),
    "cfgrib_idx": lambda path: read_cfgrib(path, indexpath=USE_CFGRIB_DEFAULT_INDEX),
    "eccodes": read_eccodes,
    "pygrib": read_pygrib,
    "pygrib_coords": lambda path: read_pygrib(path, with_coords=True),
}


def worker_main(which, path, runs):
    """One path, one process, no other GRIB library loaded."""
    import json

    fn = WORKERS[which]
    splits = []

    def once():
        result = fn(path)
        if which.startswith("cfgrib"):
            splits.append((result[3], result[4]))
        return result

    med, lo, hi = timed(once, runs)
    payload = {"path": which, "median": med, "min": lo, "max": hi}
    if splits:
        payload["open_median"] = statistics.median(s[0] for s in splits)
        payload["values_median"] = statistics.median(s[1] for s in splits)
    print(json.dumps(payload))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=5, help="timed runs per path (default 5)")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="directory holding the sample files")
    ap.add_argument("--worker", choices=sorted(WORKERS), help=argparse.SUPPRESS)
    ap.add_argument("--file", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        worker_main(args.worker, args.file, args.runs)
        return

    files = ensure_files(args.cache)
    print(f"decimation for the pipeline-array comparison: [::{DECIMATION}, ::{DECIMATION}]")
    print(f"medians over {args.runs} runs, one warm-up discarded")
    for product, path in files.items():
        bench_product(product, path, args.runs)
    print("\nNote: every path is compared on the array tcf_pipeline actually keeps "
          "(float32, decimated).\nA path is only a candidate if those bytes are identical.")


if __name__ == "__main__":
    main()
