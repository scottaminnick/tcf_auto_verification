# Observational minimum-area threshold analysis

> **Methodology 1.0 owner decision:** the 15,000 km² floor no longer filters
> forecast-scoring Sparse/Medium truth. It remains provisionally only for
> automated Candidate Miss triage. The historical sensitivity results below
> characterize why the former scoring use was rejected and why the triage value
> is not a definition of observational truth.

## 1. Executive summary

This read-only study evaluates the magnitude and role of the production
15,000 km² observational truth floor. It does not change the value, the current
post-domain order, or any verification behavior.

The floor does **not** measure raw reflectivity, raw echo tops, raw pair-qualified
cores, or forecast polygons. It filters each connected, domain-clipped,
EPSG:5070-measured polygon produced after temporal qualification, dilation,
20×20 smoothing, Sparse/Medium thresholding, and cell-footprint polygonization.
It is therefore a cutoff on a processed verification envelope.

Repository history supports only a historical implementation heuristic: 15,000
km² was introduced to match a notebook, briefly changed to 10,000 km², and then
restored. No committed scientific rationale was found. Official-source searching
was unavailable because the configured search service returned HTTP 401; no
authoritative requirement is claimed.

The six-event legacy-max sensitivity sweep demonstrates that the parameter is
load-bearing:

* at 0 km², 15 of 48 forecast fractions change, 4 categories change, and misses
  rise from 7 to 43;
* at 10,000 km², 5 fractions and 2 categories change, and misses rise to 9;
* at 20,000 km², 4 fractions and 2 categories change, and misses fall to 2;
* at 30,000 km², 13 fractions and 8 categories change, and all automated misses
  disappear.

These changes show consequence, not correctness. Optimizing categories or miss
counts on six maxima-only cases would be methodologically invalid.

Synthetic processing also shows strong morphology dependence upstream of the
shape-neutral floor: a compact 7,925 km² raw seed becomes 15,400 km² Sparse
truth, while a similarly sized long narrow seed becomes 34,400 km² Sparse truth
and produces no Medium component. Nearby broken clusters merge or remain separate
depending on spacing.

**Recommendation: Option D — threshold magnitude remains unsupported.** The
evidence does not approve 15,000 km², another number, or elimination of a hard
floor. First establish whether the filter is intended to suppress artifacts,
define strategic relevance, impose meteorological scale, or merely reproduce a
notebook. Separately determine whether any hard minimum should exist and, only
then, what its value should be.

## 2. Evidence classes and frozen-data limitation

This report distinguishes:

* **Implementation fact:** executable behavior.
* **Repository evidence:** history, comments, tests, and frozen artifacts.
* **Synthetic evidence:** controlled mathematical behavior.
* **Analysis inference:** a possible interpretation, not policy.

Frozen `arrays.npz` files contain independent temporal maxima, not the paired
mask required by approved Decision 1A. Historical component, forecast, and miss
results are explicitly labeled **legacy independent-max evidence**. Synthetic
seeds directly represent same-observation Boolean qualification before spatial
processing. No paired mask is fabricated from frozen maxima.

Six events are useful for locating sensitivity and review cases, not for
estimating an optimal operational scale or climatological performance.

## 3. What the threshold filters

Production applies `GradingParams.min_area_m2 = 15_000_000_000` m² after:

1. same-pair ≥40 dBZ and ≥FL250 qualification and temporal Boolean union;
2. one binary-dilation iteration;
3. a 20×20 uniform filter;
4. thresholding at 25% for Sparse or 40% for Medium;
5. raster-cell-footprint polygonization with four-neighbor connectivity;
6. clipping each parent component to the verification domain; and
7. projecting the clipped component to EPSG:5070.

The complete clipped area of each pre-domain parent row is compared with the
floor. Retained rows are unioned only afterward. Sparse and Medium receive the
same absolute floor independently. Solid LINE indirectly depends on it because
its interim score uses retained Medium truth; misses depend on retained Sparse
truth.

Consequently the parameter simultaneously decides:

* what observation may contribute to forecast overlap;
* what Sparse object may generate an automated miss; and
* what Medium object may contribute to AREA 2 or interim Solid LINE scoring.

It does not filter issued forecast geometry and is not a raw convective-core
minimum.

## 4. Possible interpretations

### A. Meteorological feature-size requirement

This would mean small convective systems are not TCF-scale events. The repository
contains language about excluding isolated or insignificant echoes, but no
definition connecting 15,000 km² of processed envelope to a physical storm. The
upstream smoothing and morphology results weaken a literal storm-area reading.

### B. Noise or artifact suppression

The floor removes numerous small components and greatly reduces miss counts.
That behavior is consistent with artifact suppression, but no repository evidence
shows those removed objects are actually noise. Correct cell-footprint
polygonization already removes some historical contour artifacts, so old
calibration may not transfer automatically.

### C. Operational relevance filter

A large-scale floor could focus strategic verification on impactful convection.
This is plausible, especially for misses, but no operational relevance definition
or expert rationale is committed. “Large enough to count as truth” and “large
enough to issue an automated Missed line” may be different policies.

### D. Historical implementation heuristic

This interpretation has direct evidence: the value was selected for notebook
parity. It is the only currently supported classification. It describes provenance,
not scientific adequacy.

## 5. Authoritative documentation review

Searches were restricted to FAA, NOAA, NWS, and Aviation Weather Center sources
and targeted minimum TCF area, dimensions, observed-event size, strategic scale,
15,000 km², and equivalent nautical-mile quantities. The configured web search
service returned HTTP 401, so authoritative review could not be completed.

No numerical match is inferred. Even if an official minimum issued AREA size is
later found, it would define forecast-product eligibility, not automatically the
minimum processed observational truth area. Transferring it would require an
explicit verification rationale.

## 6. Repository-history timeline

| Date/commit | Value/effect | Evidence, not inference |
|---|---|---|
| Before `432dd9f` | 10,000 km² | Earlier `app.py` literal. |
| `432dd9f` (2026-05-26) | 15,000 km² | Comment: changed to match notebook truth blobs. |
| `cff71fa` | 10,000 km² | Commit explicitly adjusted truth-area filter downward. |
| `487a169` | 15,000 km² | Restored while refactoring file selection/filter behavior. |
| Later pipeline/config commits | 15,000 km² | Value moved into `GradingParams`; behavior preserved. |
| Current tests | 15,000 km² | Verify EPSG:5070 and just-below/above behavior, not scientific authority. |

Historical baselines and reports encode outcomes influenced by the value, but
reproduction is not justification. No notebook source or developer note in the
repository explains why 15,000 was scientifically selected.

## 7. Frozen component distribution

The component CSV contains every pre-filter parent and its clipped in-domain
area. Distribution statistics below exclude 21 wholly outside zero-area parents,
because the post-domain floor never sees them as eligible nonempty truth.

### Sparse

* eligible components: 74;
* minimum: 25.09 km²;
* 10th percentile: 852.25 km²;
* 25th percentile: 2,249.21 km²;
* median: 7,932.78 km²;
* 75th percentile: 20,221.40 km²;
* 90th percentile: 33,816.85 km²;
* maximum: 83,208.29 km².

Bands: 30 below 5,000; 13 from 5,000–10,000; 4 from 10,000–15,000;
8 from 15,000–20,000; 7 from 20,000–25,000; and 12 at least 25,000 km².

### Medium

* eligible components: 53;
* minimum: 25.50 km²;
* 10th percentile: 282.84 km²;
* 25th percentile: 1,561.55 km²;
* median: 4,968.31 km²;
* 75th percentile: 8,454.95 km²;
* 90th percentile: 14,709.22 km²;
* maximum: 36,227.06 km².

Bands: 27 below 5,000; 17 from 5,000–10,000; 5 from 10,000–15,000;
none from 15,000–20,000; 2 from 20,000–25,000; and 2 at least
25,000 km².

Thus the shared floor retains 27/74 eligible Sparse parents but only 4/53 Medium
parents. This demonstrates different scale distributions, not that class-specific
floors are warranted.

## 8. Threshold sensitivity sweep

All values are analysis-only and use current post-domain filtering.

| Floor km² | Sparse retained / area km² | Medium retained / area km² | Forecasts changed | Max fraction change | Median affected change | Categories changed | Misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 74 / 1,033,634 | 53 / 349,592 | 15 | 0.2795 | 0.0640 | 4 | 43 |
| 5,000 | 44 / 971,030 | 26 / 303,820 | 9 | 0.1667 | 0.0797 | 2 | 18 |
| 10,000 | 31 / 868,651 | 9 / 178,299 | 5 | 0.1527 | 0.0455 | 2 | 9 |
| **15,000** | **27 / 820,358** | **4 / 112,526** | **0** | **0** | **0** | **0** | **7** |
| 20,000 | 19 / 677,046 | 4 / 112,526 | 4 | 0.4634 | 0.0895 | 2 | 2 |
| 25,000 | 12 / 516,659 | 2 / 66,879 | 10 | 0.6988 | 0.0895 | 5 | 0 |
| 30,000 | 9 / 439,877 | 2 / 66,879 | 13 | 0.7169 | 0.1674 | 8 | 0 |

At 0/5,000/10,000 km², category threshold crossings are respectively 4/2/2;
at 20,000/25,000/30,000 they are 2/5/8. The CSV records every fraction and
category at all seven floors.

The sole Solid LINE changes only at the zero floor, from zero to approximately
0.000436 overlap, and remains Overforecasted. This says nothing about the
appropriate LINE methodology.

## 9. Forecast category sensitivity

Notable lower-floor changes relative to 15,000 km² include:

* `20260524_19Z_F06` feature 6: 48.71% to 53.26% at 0–10,000, Close → Well;
* `20260728_19Z_F04` feature 4: 19.93% to 23.52–26.33%, Overforecasted → Close;
* at zero, `20260524_13Z_F04` feature 6 and `20260728_19Z_F04` feature 3 also
  move from Overforecasted to Close.

Notable higher-floor changes include:

* at 20,000, `20260524_13Z_F04` feature 1 falls from 46.34% to zero;
* at 25,000, `20260524_19Z_F04` feature 4 falls from 69.88% to zero;
* at 30,000, four previously Verified Well forecasts become Overforecasted.

These are load-bearing examples for later meteorological review, not evidence
that either side of 15,000 is better.

## 10. Miss sensitivity

Relative to seven current misses:

| Floor km² | Total misses | Added | Removed | Event counts in configured order |
|---:|---:|---:|---:|---|
| 0 | 43 | 36 | 0 | 0, 9, 10, 13, 2, 9 |
| 5,000 | 18 | 11 | 0 | 0, 5, 5, 2, 2, 4 |
| 10,000 | 9 | 2 | 0 | 0, 1, 4, 1, 2, 1 |
| 15,000 | 7 | 0 | 0 | 0, 1, 3, 1, 2, 0 |
| 20,000 | 2 | 0 | 5 | 0, 0, 1, 0, 1, 0 |
| 25,000 | 0 | 0 | 7 | all zero |
| 30,000 | 0 | 0 | 7 | all zero |

Misses are more sensitive than many forecast fractions because every retained
uncovered Sparse component can create a Missed classification. The same floor
currently decides both whether a component is forecast truth and whether it is
eligible to become a miss; those purposes are not necessarily equivalent.

Near-current missed objects include areas of 15,511, 16,381, 18,044, 19,102,
and 19,159 km². Raising the floor to 20,000 deletes five current misses wholesale.
Lowering to 10,000 adds two. These components are priority candidates for human
meteorological inspection.

## 11. Load-bearing component evidence

The summary records every ≤30,000 km² component that intersects an applicable
forecast or would be a miss. Examples near the current floor include:

* `20260524_13Z_F04` Sparse component 8, 14,251 km²: intersects forecast 5 and
  would be missed if retained;
* `20260524_19Z_F06` Sparse component 8, 11,327 km²: intersects forecasts 6 and 7,
  producing the lower-floor Well/Close sensitivity;
* `20260728_19Z_F04` Sparse component 13, 11,698 km²: contributes to feature 4's
  20% crossing;
* `20260403_21Z_F04` Sparse component 14, 15,511 km²: a current uncovered miss;
* `20260524_13Z_F04` Sparse component 4, 19,698 km²: contributes to forecasts 1
  and 2 and disappears at 20,000;
* `20260728_19Z_F04` Medium component 10, 10,859 km²: affects AREA 2 feature 13
  below the current floor.

Most are wholly inside the domain. Threshold sensitivity is therefore not merely
a boundary-order artifact. The component CSV supplies centroid and bounds for
manual inspection.

## 12. Raw-to-processed scale

The stage audit provides event-level legacy core, dilated core, and pre-filter
truth-envelope areas. Examples:

* `20260403`: raw 88,458 km² → dilated 176,481 km² → Sparse 232,265 km² but
  Medium 79,568 km²;
* `20260524_19Z_F04`: raw 100,029 → dilated 226,310 → Sparse 207,062 and Medium
  74,373 km²;
* `20260728`: raw 128,103 → dilated 277,513 → Sparse 279,075 and Medium
  81,417 km².

These are union-area totals, not one-to-one storm expansion ratios. Components
can merge, split by thresholds, or disappear, so totals cannot establish a
universal multiplier. They do establish that the threshold is applied after
substantial scale transformation and that Sparse envelopes are generally much
larger than Medium envelopes.

## 13. Synthetic morphology experiments

Synthetic grids use 5 km square cells (25 km²), one dilation, the production
20-cell filter, and the 25%/40% thresholds. Seeds represent pair-qualified
occurrence, not independent maxima.

### Compact circles

Circle radius 10 cells has raw area 7,925 km², dilated area 9,425 km², Sparse
area 15,400 km², and Medium area 8,900 km². It is the first tested compact radius
whose Sparse envelope clears 15,000. Medium does not clear until radius 15, where
raw area is 17,725 and Medium area 21,100 km².

Thus a raw feature substantially smaller than 15,000 can become retained Sparse
truth, while the corresponding Medium geometry remains filtered out.

### Equal-area morphology

The compact reference has raw area 7,925 km² and Sparse area 15,400 km². A long
narrow seed with nearly equal raw area (7,875 km²) produces 34,400 km² Sparse
truth and no Medium component. Shape-neutral area filtering therefore receives
strongly shape-dependent upstream envelopes.

A one-cell-thin elongated seed does not survive either coverage threshold despite
its length. The result depends on neighborhood occupancy, not only raw area or
extent.

### Broken and separated clusters

Three radius-5 clusters merge into one Sparse object at tested center spacings
of 8–20 cells. Their processed areas range from 12,400 to 16,100 km², crossing
the hard floor as spacing changes. At 24 cells they remain three separate
3,500 km² objects, so their combined 10,500 km² is not pooled. Two distant
clusters likewise remain two 3,500 km² objects.

### Holes and discontinuity

A large annular seed retains its hole during raw construction, although smoothing
can alter that topology before final measurement. Physical polygon area respects
remaining holes. Independent exact cases show 14,999 km² is deleted completely
while 15,001 km² is retained completely. The two-square-kilometre difference can
therefore alter every downstream score and miss involving the object.

## 14. Sparse/Medium scale and nesting

The current absolute floor preferentially removes Medium objects in this sample:
only 4 of 53 eligible Medium components survive, versus 27 of 74 Sparse. That may
be desirable if both truth classes require a common strategic minimum, or
undesirable if their different processed scales warrant different treatment.
No evidence supports class-specific floors, so none is proposed.

Across 5,000–30,000 km², no material nesting violation occurred. At zero, one
event had about 7.91 km² of Medium geometry outside Sparse after independent
polygon repair/overlay. Since the raster Medium mask is necessarily a subset of
Sparse, this is a small geometry-processing anomaly exposed only when every tiny
component is retained, not evidence of meteorologically non-nested truth. A
separate ~5 m² overlay difference persists at other floors and is treated as
floating topology noise.

## 15. Hard-cutoff false precision

A hard floor is reproducible and simple, but discontinuous. Small input,
projection, raster resolution, domain, or library changes can flip an entire
component near M. Current reviewer output does not expose removed components or
distance from the floor.

Conceptual alternatives—not implementations—include no floor, a reviewer flag,
graded weighting, a review band, or joint area/extent/component criteria. Each
adds policy or complexity. The current evidence supports surfacing component
area and retention reason for review, but does not yet justify replacing the
hard filter.

## 16. Product scale and human review

No authoritative issued-TCF size requirement was recovered. If one is later
found, it must be evaluated as a forecast eligibility rule before transferring it
to processed observations.

Useful future diagnostics include `component_area_km2`,
`below_nominal_minimum`, `near_minimum_threshold`, and `retained_reason`. They
would allow a meteorologist to distinguish a genuine but small event from noise
without silently changing automation. No UI or FAA report field is added here.

## 17. Coupled decisions

* **Processing order:** post-domain filtering remains current and the prior
  working direction; this analysis does not reopen it.
* **Forecast denominator:** most identified sensitive components are inside the
  domain, but crossing components still couple the floor to the unresolved
  denominator/domain policy.
* **Solid LINE:** only zero-floor truth changes its frozen numerical score, with
  no category effect; long-term LINE methodology remains unresolved.
* **Misses:** one threshold currently makes both truth-eligibility and automated
  miss-eligibility decisions.
* **Decision 1A/1B:** pair-first qualification remains approved, no separation
  threshold is introduced, and frozen evidence remains explicitly legacy.

## 18. Policy-direction matrix

| Direction | Authority | Interpretation | Sensitivity | Transparency | Reproducibility |
|---|---|---|---|---|---|
| Current 15,000 hard floor | Notebook parity only | Unresolved | Material | Low for deleted objects | High |
| Lower fixed floor | None found | Includes smaller objects | More scores/misses | Low | High |
| Higher fixed floor | None found | Strategic-large-only possibility | Deletes current scores/misses | Low | High |
| No hard minimum | None required, but policy needed | All processed truth counts | Very high miss burden in sample | Potentially high | High |
| Reviewer-aware handling | Needs workflow authority | Separates evidence from automated classification | Preserves visibility | High | Depends on explicit rules |

Historical stability is intentionally not treated as a ranking criterion.

## 19. Recommendation

**Option D — threshold magnitude remains unsupported.**

The sample demonstrates that the floor suppresses small components and strongly
controls automated misses, but cannot establish that 15,000 km² represents
meteorological scale, noise, or operational relevance. It also cannot identify a
better fixed number. Evidence is insufficient to conclude that the hard filter
itself is unnecessary, so Option E would overstate the result.

### Evidence needed to decide whether a minimum should exist

1. An explicit operational statement of what small observed convection should do
   in forecast scoring and automated miss reporting.
2. Meteorological review of retained and removed components across morphologies,
   seasons, regions, and boundary cases.
3. Artifact analysis under corrected cell-footprint polygonization.
4. Synthetic invariants for connectivity, morphology, nesting, and resolution.
5. Human-review workflow requirements for real but strategically minor events.

### Additional evidence needed to select a value

1. Authoritative FAA/NWS/AWC verification guidance or documented expert rationale.
2. A larger Decision 1A-capable archive, not maxima-only replay.
3. Manual classification of near-threshold components as noise, valid but minor,
   or strategically relevant—without optimizing report categories.
4. Sensitivity across grid resolution and spatial-processing parameters.
5. A declared physical object: raw core, processed envelope, eligible in-domain
   portion, or another reproducible construct.

## 20. Reproduction and scope preservation

```bash
python analysis/minimum_area_threshold_sensitivity.py \
  --components analysis/minimum_area_threshold_components.csv \
  --forecasts analysis/minimum_area_threshold_forecasts.csv \
  --summary analysis/minimum_area_threshold_summary.json
```

The utility reads committed inputs only and writes lightweight artifacts. No
production file, expected baseline, threshold, order, score, miss, report, or
unrelated decision is changed.
