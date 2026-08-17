# Frozen baselines — v2, LINE features closed into polygons

Captured at 2-minute cadence with `parse_iem_cow_text` still closing any LINE
feature of 3+ points into a `Polygon` (BUG 2 in tcf_pipeline's inventory), so
only 2-point lines ever reached the `LineString(...).buffer(0.15)` path.

**Do not regenerate this directory.** It is the before-picture for the LINE
geometry fix. `20260403_21Z_F04` is the event captured for exactly this purpose.

The live set in `baseline/` is v3 (LINE features buffered as lines at any point
count). The MRMS composite is untouched by that fix — same valid times, same
cadence — so `arrays.npz` is bit-identical between v2 and v3 and only the
forecast geometry, and therefore the grading, differs.
