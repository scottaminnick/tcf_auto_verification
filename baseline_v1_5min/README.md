# Frozen baselines — v1, 5-minute cadence

Captured with `COMPOSITE_CADENCE_MINUTES = 5`: 7 scans over ±15 minutes,
14 files per composite.

**Do not regenerate this directory.** It is the before-picture for the
cadence change to 2 minutes (15 scans, 30 files), which changes graded
output by design — more scans means more `raw_cores`, so more truth area.

The live set in `baseline/` is v2 (2-minute cadence). `baseline/check.py`
runs against `baseline/`; this directory is a reference copy for
comparing the two, not an input to `make test`.

Contents per event: `arrays.npz`, `tcf_raw.txt`, `expected.json`,
`pass_a_report.txt` — identical layout to `baseline/`.
