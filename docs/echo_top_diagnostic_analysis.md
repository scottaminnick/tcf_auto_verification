# TCF Echo-Top Diagnostic Methodology Analysis

## Scope and recommendation

This read-only analysis changes no production behavior. **Recommendation: Option
C — retain full-geometry temporal-maximum P90 provisionally as a reviewer
upper-end descriptor, but do not approve it as a permanent FAA-facing method
until intent and sample domain are documented.** A subsequent approved semantic
correction now returns unavailable for fewer than six cells. The P90 statistic,
six-cell count, geometry, and report structure remain unchanged.

## Current diagnostic

### Source, sample, and calculation

`build_composite()` converts `EchoTop_18` from metres to thousands of feet using
`* 3.28084`, then retains a numerical maximum at every grid cell across all
usable nominal pairs. The diagnostic uses that **temporal maximum**, not the
pair-specific top that helped create the Decision 1A qualifying mask.

For each forecast source geometry \(F\), production bounds the raster, constructs
grid-center coordinates, and uses strict Shapely `contains` membership. The full
source geometry is retained for sampling even when multipart geometry is
exploded for grading; polygon holes and all multipart pieces are respected.
AREA uses its polygon. Solid LINE uses the parser's current 0.15-degree buffered
corridor, so its diagnostic is corridor-area based.

Define

\[
S(F)=\{x:\ x\text{ is strictly inside }F, R_{max}(x)\ge40\text{ dBZ},
E_{max}(x)\ge25\text{ kft}\}.
\]

This “valid tops” name is narrower than mere finite availability: a diagnostic
sample must independently satisfy both thresholds in the two temporal maxima.
NaNs fail those comparisons. Production separately checks whether any finite
reflectivity/top pair exists inside the geometry.

Exact output is:

* if geometry contains grid centers but none has both finite fields: `None`
  (unavailable);
* otherwise, if \(|S|=0,1,...,5\): `None` (insufficient sample);
* if \(|S|\ge6\): NumPy's interpolated 90th percentile of \(E_{max}\) in kft.

Insufficient evidence is therefore distinct from meteorological zero. A numeric
zero remains representable in downstream nullable fields, although the current
`top >= 25 kft` sample definition does not permit six qualifying zero-valued
samples.

### Presentation

The raw floating statistic is carried into the editable review table. Map hover
shows one decimal kft or `Unavailable`. FAA text includes `[Top: x.x kft]` only
when the value is positive; zero and unavailable both
omit the annotation. Miss rows contain no echo-top diagnostic (`top_kft=None`)
and their hover/report text has no top. Frozen expected JSON rounds the raw value
to two decimals, but production FAA text uses one decimal.

The one-decimal presentation is coarser than the internal percentile, but it can
still imply more certainty than a temporal maximum, sample-size-dependent
percentile, and unresolved observation adequacy support. It should be read as a
descriptive diagnostic, not exact feature altitude.

## Origins and authority

Repository history first introduces both `np.percentile(valid_tops, 90)` and
`len(valid_tops) > 5` together in commit `6a01776` (`Improve app.py with new
features and optimizations`). The code moved into `tcf_pipeline.py` in
`123b35b`. No earlier max/mean/median implementation, notebook, scientific
comment, statistical-stability rationale, grid-area rationale, or operational
requirement is committed. Later tests correctly encode P90, strict geometry,
holes/multipart handling, nullable unavailability, and numeric zero; those tests
prove behavior, not authority.

Accordingly:

* **P90:** inherited implementation heuristic; scientific origin unresolved.
* **Six qualifying cells:** inherited implementation heuristic; origin and
  physical/statistical rationale unresolved. Only the below-minimum return
  semantics have since been resolved.

An official FAA/NOAA/NWS/AWC-only search was attempted, but the configured web
service returned HTTP 401. No authoritative TCF verification percentile or
six-cell requirement is claimed. A product definition for forecast echo tops or
MRMS `EchoTop_18`, if later located, must still be distinguished from a rule for
summarizing processed verification observations.

## What P90 currently means

Full-geometry P90 most closely approximates a **representative upper-end
echo-top environment inside the issued feature during the temporal window**. It
is not the maximum, typical whole-area top, top specifically associated with
pair-qualified truth, or top of only the portion that verified.

A high numerical top can enter \(E_{max}\) at a time when reflectivity did not
simultaneously reach 40 dBZ. Independent `max_refl >= 40` can also come from a
different time. Consequently the diagnostic itself retains the temporal
conjunction mechanism rejected for truth by Decision 1A. This is acceptable only
if its stated meaning is environmental/reviewer context rather than observed
pair-qualified TCF convection.

## Frozen six-event audit

The reproducible audit samples the exact current forecast geometries and frozen
`max_tops`/`max_refl` fields. It covers all 48 forecasts and is explicitly a
**legacy independent temporal-max characterization**, not pair-first historical
truth.

### Qualifying sample counts

* minimum 0; P10 0; P25 1.75; median 41.5; P75 252.5; P90 400; maximum 1,307;
* 11 features have zero qualifying cells;
* 4 have 1–5; 4 have 6–10; 3 have 11–25; 3 have 26–50; and 23 exceed 50; and
* the six-cell rule controls 15/48 features (all 11 zero plus four 1–5), which
  now return unavailable rather than zero.

Across features with at least one qualifying sample:

* median `max − P90` is 3.36 kft; the largest is 16.40 kft;
* median `P95 − P90` is 1.35 kft; and
* median `P90 − median` is 6.65 kft.

These spreads show that statistic selection is load-bearing for descriptive
tops, even though it cannot alter verification categories. The CSV records each
feature's count, minimum, median, mean, P75/P90/P95/max, current category, and
reported value. For every sample with at least six cells, reproduced P90 equals
the production diagnostic. The semantic correction changes 15 stored/replayed
diagnostics from zero to unavailable, including four with positive samples;
fractions and categories do not change. FAA report lines do not change because
both the former zero and current unavailable value omit the top annotation.

The sample does not validate P90. It only measures sensitivity on six selected
events, one of which contains the sole frozen Solid LINE.

## Synthetic analysis

The deterministic cases expose statistical semantics:

| Distribution | Median | Mean | P90 | P95 | Max | Current result |
|---|---:|---:|---:|---:|---:|---:|
| ten FL300 | 30 | 30 | 30 | 30 | 30 | 30 |
| nine FL250 + one FL500 | 25 | 27.5 | 27.5 | 38.75 | 50 | 27.5 |
| FL200–FL450, six cells | 32.5 | 32.5 | 42.5 | 43.75 | 45 | 42.5 |
| half FL250, half FL400 | 32.5 | 32.5 | 40 | 40 | 40 | 40 |
| exactly six, one FL450 | 30 | 31.67 | 39 | 42 | 45 | 39 |
| five FL300 | 30 | 30 | 30 | 30 | 30 | **unavailable** |
| one FL450 | 45 | 45 | 45 | 45 | 45 | **unavailable** |
| 95 FL250 + five FL450 | 25 | 26 | 25 | ~26 | 45 | 25 |
| 95 FL400 + five FL250 | 40 | 39.25 | 40 | 40 | 40 | 40 |

P90 resists one isolated outlier but can entirely omit a physically small
high-top core in a large polygon. Keeping the same core while adding low-top
area can lower P90 without changing the highest storm. This is desirable for an
upper-end *representative area* interpretation, but undesirable for a *highest
associated top* interpretation. At six samples, interpolated P90 lies halfway
between the two highest order statistics, so one value can materially affect it.

The hole case assigns high values only to excluded cells; the six included FL300
cells still produce FL300. This confirms geometry semantics independently of
statistic selection.

## Grid and physical sample scale

Verification arrays use every fifth native coordinate, approximately a
0.05° grid. An illustrative 0.05° cell projected to EPSG:5070 is about 26.74 km²
at 30°N, 23.70 km² at 40°N, and 19.94 km² at 50°N. Six cells therefore represent
roughly 160.44, 142.23, and 119.62 km² respectively. Six is not a stable physical
area across latitude, and resampling would change its meaning. Grid centers also
represent samples rather than an explicitly area-weighted integration.

## Candidate sample regions

| Region | Meaning and behavior |
|---|---|
| full forecast — current | context exists for every forecast category, including overforecasts; may dilute cores and include non-pair-qualified temporal maxima |
| forecast ∩ raw Decision 1A mask | directly paired convection but raster association and temporal top values must be retained; empty for pure overforecasts |
| forecast ∩ Sparse/Medium truth | aligns with coverage semantics but depends on smoothing, area floor, and class; risks conflating verification with diagnosis |
| verified intersection | describes only credited portion; unavailable/empty for overforecasts and circularly category-dependent |
| associated observation region | potentially meteorologically meaningful but requires a new object-association policy |

For misses, production currently supplies no scalar. Sampling the retained Sparse
miss geometry would naturally focus on processed qualifying truth, but would
have different semantics from full forecast geometry and would depend on the
unresolved miss and minimum-area policies.

## Candidate statistics and decision matrix

* **Maximum** answers highest sampled top, is intuitive, and is dominated by one
  cell and increasingly likely to rise with polygon size.
* **P95** is more upper-tail sensitive than P90 and still sample-size dependent.
* **P90** is robust to isolated extremes and interpretable as an upper-end
  distribution descriptor, but polygon dilution and interpolation matter.
* **P75** emphasizes a broader portion and can miss small intense cores.
* **Median/mean** describe typical values; median is robust, mean responds to
  tails, and neither naturally communicates operational maximum hazard.
* **Area weighting** would be physically coherent on nonuniform cells, but the
  current center samples are nearly regular and no operational weighting intent
  is established.
* **Distribution summary** (count plus median/P90/max) is most transparent to a
  reviewer but too verbose for the current FAA line and adds policy/UI choices.

Full-geometry P90 is simple, reproducible, common across Sparse/Medium/LINE, and
available for overforecasts. Pair-qualified or verified-intersection P90 has
clearer truth association but becomes unavailable for some categories, couples
to Decision 1A/coverage/area decisions, and cannot be reconstructed from the
frozen maxima. No class-specific statistic is justified by current evidence.

## Missingness, adequacy, and human review

Incomplete temporal echo-top support can bias the maximum downward, while a
small spatial sample can make the percentile unstable. A future observation
adequacy state can contextualize the diagnostic, but event-level adequacy and
spatial sample sufficiency remain separate facts. This analysis adds no labels.

The scalar should be treated as descriptive evidence available to the reviewer,
not an authoritative characterization of an entire forecast object. If retained
in FAA text, its sample domain, temporal-max nature, and unavailable/insufficient
semantics require approval.

## Desirable invariants

1. Missing and insufficient samples are not meteorological zero (resolved).
2. Holes, outside areas, and geometry vertex order cannot affect membership.
3. Multipart components participate exactly once.
4. An extreme cell dominates only if the declared statistic intends that.
5. Physical meaning and sample region are explicit.
6. Sample-size sensitivity remains visible.
7. Decision 1A qualification remains distinct from the diagnostic.
8. Availability/adequacy context can accompany, but not silently alter, value.

## Remaining subdecisions

These must remain separate:

1. **Statistic:** P90 is provisional; maximum/P95/distribution alternatives lack
   operational evidence.
2. **Sample region:** full forecast is provisional; pair-qualified versus
   verified-region intent remains unresolved.
3. **Minimum sample:** six has no documented justification.
4. **Insufficient-sample semantics:** resolved—fewer than six valid cells is
   unavailable, not numeric zero.
5. **Report role:** reviewer evidence versus authoritative FAA descriptor remains
   unresolved.

## Scope preservation

The analysis utility reads frozen inputs through the named legacy replay path and
writes compact CSV/JSON only. The later scoped production correction changes
only insufficient-sample nullability; no baseline, percentile, six-cell count,
geometry, report structure, Decision 1A/1B, adequacy, miss, or LINE behavior
changed.
