# Methodology-validation tests

This directory tests the established requirements in
[`METHODOLOGY_SPEC.md`](../METHODOLOGY_SPEC.md) version 0.1. It is intentionally
separate from `baseline/`:

* methodology validation asks whether the implementation performs the approved
  methodology;
* historical baselines ask whether behavior changed relative to an earlier
  captured implementation.

Expected values here are analytic: rectangle intersection, projected physical
area, explicit raster cell footprints, or direct threshold logic. Nothing in
this suite captures production output as its oracle.

Run the suite with:

```bash
python -m unittest discover -s methodology_validation -p 'test_*.py' -v
```

Tests are not marked `expectedFailure`: any established specification violation
must remain visible and turn the command red until production code conforms.

## Current conformance summary

| Requirement | Current result | Responsible production function |
|---|---|---|
| 0%, 20%, 50%, and 100% AREA grading thresholds | Pass | `run_verification` |
| Sparse selects 25% truth; Medium selects 40% truth | Pass | `run_verification` |
| Same-pair reflectivity/top qualification is unioned over the window | Pass | `build_composite` / `run_verification` |
| AREA and missed-event ratios use EPSG:5070 physical area | Pass | `run_verification` |
| Forecast holes are excluded from AREA overlap | Pass | `run_verification` / Shapely intersection |
| Forecast holes are excluded from echo-top samples | Pass | `run_verification` |
| Unavailable echo tops are distinct from a real numeric zero | Pass | `run_verification` |
| Minimum-area decisions use physical cell-footprint area | Pass | `extract_tcf_polygons` |
| UTC valid-time rollover | Pass | `compute_valid_dt` |
| Unknown and malformed forecast coverage records are rejected visibly | Pass | `parse_iem_cow_text` |
| Raster polygonization preserves holes and cell-edge topology | Pass | `extract_tcf_polygons` |

The tests enforce approved pair-first temporal qualification but deliberately do
**not** select a Decision 1B separation threshold, MRMS adequacy cutoffs, the
Solid LINE verification methodology and truth field,
the domain-boundary denominator, minimum-area processing order,
miss coverage interaction, long-term LINE metric, or echo-top statistic. Those
remain open decisions in Specification 0.1.
