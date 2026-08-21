# Independent Methodology Analysis: Solid TCF LINE Verification

**Status:** read-only analysis; no verification policy is approved here

**Scope:** current implementation plus the sole frozen Solid LINE case

**Recommendation status:** Option C — a preferred conceptual direction is
identifiable, but its parameters require additional evidence

## 1. Executive summary

The current implementation does not verify the linear assertion represented by
a Solid LINE directly. It converts the issued centerline to a polygon by applying
a 0.15-degree Shapely buffer in EPSG:4326, selects the same 40% smoothed truth
field used by Medium AREA forecasts, calculates equal-area corridor overlap, and
applies the AREA-derived 20%/50% grade thresholds.

That implementation is internally traceable and computationally consistent, but
the repository contains no scientific or authoritative justification for its
buffer width, truth-field choice, or AREA grading thresholds. The buffer first
appears as a literal implementation choice in commit `d5d34bd` and was later
named without changing its value. Repository evidence therefore classifies
`LINE_BUFFER_DEG` as an **inherited implementation assumption**, not a documented
TCF width requirement.

The one frozen Solid LINE is useful as a worked example but not as validation.
It is a three-vertex, approximately 285.5-NM line in
`20260403_21Z_F04`. Under the frozen legacy-max observations its current score is
0%, Overforecasted. The Medium truth is about 94 km from the centerline; varying
the angular half-buffer from 0.03° through 0.50° leaves the score at zero. This
case therefore cannot reveal ordinary buffer sensitivity near a grade threshold.
It does, however, expose truth-choice sensitivity: approximately 36.1% of the
centerline intersects the legacy Sparse truth while none intersects Medium truth.

**Working hypothesis, not approved methodology:** score Solid LINE forecasts in
the line-length dimension, with any displacement tolerance represented by a
separately justified physical-distance corridor. Candidate E (corridor linear
occupancy), or equivalently a carefully specified Candidate C, best preserves
the distinction between spatial tolerance and linear forecast coverage. The
distance, observational field, gap treatment, eligibility denominator, and grade
thresholds cannot be selected from one case.

## 2. Evidence classes and constraints

This document labels evidence as follows:

* **Repository fact:** directly traced from source, Git history, frozen input, or
  reproducible analysis output.
* **Mathematical consequence:** follows from the stated geometry and equation.
* **Authoritative external evidence:** official FAA/NOAA/NWS/AWC material.
* **Inference or working hypothesis:** a proposal requiring approval/evidence.

External authoritative review could not be completed in this environment. HTTPS
requests to `aviationweather.gov` failed because the configured proxy rejected
the tunnel with HTTP 403. This analysis therefore does not claim an official
width, gap rule, or verification algorithm. The repository's resolved semantics
and working specification establish only that LINE 1 means Solid, 75–100% linear
coverage, and that LINE production criteria include an approximately 100-NM
minimum length.

The frozen MRMS arrays predate Decision 1A. They contain only independent
numerical maxima, so the single-case observational calculations below are
explicitly **legacy-max characterizations**, not reconstructions of modern
pair-first truth.

## 3. Resolved product semantics

Current feature-aware semantics are:

| Feature/code | Meaning | Dimension |
|---|---|---|
| AREA 3 | Sparse, 25–39% | areal coverage |
| AREA 2 | Medium, 40–74% | areal coverage |
| LINE 1 | Solid, 75–100% | linear coverage |

“Solid” is not a Dense AREA category. These percentages describe product
semantics; they do not by themselves prescribe an observational smoothing
threshold, verification score cutoff, or displacement tolerance.

## 4. Current implementation trace

| Stage | Repository location |
|---|---|
| Type-aware semantics and labels | `tcf_pipeline.py`: `SUPPORTED_TCF_COVERAGE`, `get_tcf_coverage_semantics()` |
| Raw record parsing and buffering | `tcf_pipeline.py`: `parse_iem_cow_text()` |
| Buffer constant | `tcf_pipeline.py`: `LINE_BUFFER_DEG` |
| Pair-first observation seed | `tcf_pipeline.py`: `_pair_qualifying_mask()`, `build_composite()` |
| Dilation, smoothing, truth selection | `tcf_pipeline.py`: `run_verification()` |
| Cell-footprint truth polygons | `tcf_pipeline.py`: `extract_tcf_polygons()` |
| Equal-area score and miss union | `tcf_pipeline.py`: `run_verification()` |
| Echo-top diagnostic | `tcf_pipeline.py`: `_geometry_point_mask()`, `run_verification()` |
| Reviewer/report fields | `tcf_pipeline.py`: `build_review_table()`, `build_report()` |
| Streamlit map/report display | `app.py`: `render_scorecard()` |

### 4.1 Raw record and parser

The sole frozen record is:

```text
LINE 1 3 389 943 363 960 352 978
```

The LINE record grammar is coverage code, declared point count, then latitude /
unsigned-west-longitude tenths-degree pairs. `parse_iem_cow_text()`:

1. recognizes feature type `LINE`;
2. accepts only the feature-aware combination LINE 1;
3. reads `NPTS` from token 2;
4. converts `(lat, lon)` to `(-lon/10, lat/10)`; and
5. constructs `LineString(coords).buffer(LINE_BUFFER_DEG)`.

The issued centerline is not retained as a separate production column. Once
parsed, the canonical forecast geometry is the buffered polygon. This matters
for scoring, miss suppression, ARTCC attribution, map drawing, and echo-top
sampling.

### 4.2 Buffer construction

`LINE_BUFFER_DEG = 0.15`. Buffering happens before a CRS is attached and uses the
longitude/latitude coordinates directly, so 0.15 is an angular half-width. The
result is a Shapely Polygon with round joins/end caps under Shapely defaults.

At roughly 35–39°N, 0.15° latitude is about 16.7 km while 0.15° longitude is only
about 13–14 km. The physical cross-line tolerance therefore varies with latitude
and line orientation. Later projection to EPSG:5070 corrects area measurement;
it does not undo the latitude/orientation dependence already embedded in the
degree-space buffer.

### 4.3 Truth selection and scoring

Production truth begins with the approved pair-first temporal mask. It then uses
the common dilation, uniform smoothing, raster thresholding, cell-footprint
polygonization, domain clipping, and 15,000-km² filter.

Sparse AREA code 3 selects the 25% field. Every other admitted forecast is either
Medium AREA code 2 or Solid LINE code 1, and both select the 40% field. This is an
explicit interim policy, not a consequence of LINE semantics.

Define:

* \(L\): issued centerline in EPSG:4326;
* \(B_{0.15}(L)\): polygon created by a 0.15-degree Shapely buffer;
* \(T_{40}\): retained 40% observational truth union;
* \(P_{5070}(G)\): projection of geometry \(G\) to EPSG:5070; and
* \(A(G)\): planar area in EPSG:5070.

The current score is confirmed as:

\[
S_A = \frac{A(P_{5070}(B_{0.15}(L)) \cap P_{5070}(T_{40}))}
           {A(P_{5070}(B_{0.15}(L)))}.
\]

The denominator is the full issued buffered polygon. Forecast geometry is not
clipped to the verification domain before scoring; truth is clipped. Scores at
or above 50% are Verified Well, scores at or above 20% but below 50% are Verified
Close, and lower scores are Overforecasted.

The buffer, areal numerator/denominator, 40% truth selection, and 20%/50% grades
are inherited AREA-verification concepts. None measures the fraction of issued
centerline length occupied by convection.

### 4.4 Components, holes, and boundaries

The parser creates one connected LineString per LINE record, whose buffer is
normally one Polygon. The grading loop explodes multipart forecast geometry and
grades each component independently if multipart geometry enters by another
path. Shapely area/intersection preserves polygon holes.

Because only truth is domain-clipped, a LINE crossing the domain boundary retains
its entire buffered area in the denominator. Whether eligible length/area should
be full-issued or in-domain remains the general domain-denominator decision and
must not be silently resolved by a future LINE method.

### 4.5 Miss interaction

The buffered LINE polygon is included in the union of all forecast geometries.
Each Sparse truth component is classified as missed when less than 20% of its
physical area intersects that union. Consequently:

* Solid LINE can suppress a miss even though its own score uses Medium truth;
* degree-buffer width affects how much Sparse truth it can capture; and
* coverage class/linear occupancy is otherwise ignored in miss calculation.

A future linear forecast score does not logically dictate a miss-policy change,
but the two are coupled because the present miss union uses the corridor.

### 4.6 Echo-top and report/reviewer output

Echo-top sampling uses the forecast geometry stored in the GeoDataFrame, which
for LINE is the buffered polygon—not the centerline. Grid-center samples inside
that corridor are filtered using independent numerical maximum reflectivity/top
fields, and the current six-cell/90th-percentile diagnostic is retained. This is
a diagnostic and not the Decision 1A truth seed.

The review table retains feature type, raw coverage code, physical overlap
fraction, category, ARTCCs, boundary flag, and nullable echo top. The report uses
the feature-aware `Solid (Line N)` label.

## 5. Origin and status of `LINE_BUFFER_DEG`

### Repository evidence

* Current value: 0.15 degrees.
* `git log -S'buffer(0.15)'` first finds commit `d5d34bd` (2026-05-25,
  “Implement forecast cleanup for geometry processing”).
* The literal persists through subsequent app refactors and pipeline extraction.
* Commit `491e2b4` names it `LINE_BUFFER_DEG` while fixing multi-vertex LINE
  parsing; its comment explicitly says the literal is unchanged and that width
  and LINE area grading are separate decisions.
* Synthetic tests lock 0.15 degrees as current behavior, but a regression test is
  evidence of implementation stability, not scientific justification.
* No repository document identifies 0.15 degrees as an official LINE width.

### External evidence

Unavailable in this environment because authoritative AWC pages could not be
reached. No external rationale is asserted.

### Classification

**Inherited implementation assumption.** Its historical source is identifiable,
but its scientific rationale and relationship to an official TCF width remain
unresolved.

## 6. Sole frozen Solid LINE case

The committed coverage audit identifies source feature 7 in
`20260403_21Z_F04`; east-to-west report ordering labels it Line 4.

| Attribute | Reproducible result |
|---|---:|
| Issue time | 21Z on 2026-04-03 |
| Valid time | 2026-04-04 01Z |
| Centerline vertices | 3 |
| Coordinates | (-94.3,38.9), (-96.0,36.3), (-97.8,35.2) |
| Geodesic centerline length | 285.49 NM |
| Centerline bounds | 97.8–94.3°W, 35.2–38.9°N |
| Current corridor area (EPSG:5070) | 16,164.92 km² |
| Current legacy fraction | 0.0000 |
| Current category | Overforecasted |
| Echo-top diagnostic | 47.61 kft |
| Centerline-to-Medium-truth distance | 93.98 km |
| Misses with / without LINE | 1 / 1 |

It exceeds the working specification's approximately 100-NM production minimum;
the parser does not validate physical LINE length. Whether eligibility should be
measured before or after domain clipping remains open.

The legacy Medium truth is one 30,651.51-km² polygon north of the line, bounded
approximately by 94.42–92.82°W and 39.72–43.22°N. The legacy Sparse truth is much
larger (193,515.00 km²), and 36.06% of the centerline intersects it. This contrast
demonstrates sensitivity to observational-field selection, not that Sparse truth
is the correct choice.

Removing this LINE from the forecast union leaves the sole legacy miss unchanged.
That result is specific to this event; the code proves a buffered LINE can affect
misses in other geometries.

### Reproducibility artifacts

* Script: `analysis/solid_line_frozen_case.py`
* Numeric output: `analysis/solid_line_frozen_case.json`
* Optional visualization: regenerate the ignored
  `analysis/solid_line_frozen_case.png` with the script's `--plot` option.

The plot shows the reconstructed centerline and current corridor against Sparse
and Medium legacy-max truth. It explicitly labels the observational limitation;
Decision 1A truth cannot be recovered from the maxima-only artifact.

## 7. Single-case sensitivity

### 7.1 Angular-buffer area overlap

| Analysis-only half-width | Corridor area | Medium-truth area score |
|---:|---:|---:|
| 0.03° | 3,121.69 km² | 0.0000 |
| 0.05° | 5,233.74 km² | 0.0000 |
| 0.10° | 10,622.05 km² | 0.0000 |
| **0.15° current** | **16,164.92 km²** | **0.0000** |
| 0.20° | 21,862.37 km² | 0.0000 |
| 0.30° | 33,720.91 km² | 0.0000 |
| 0.50° | 59,292.44 km² | 0.0000 |
| 1.00° | 134,035.96 km² | 0.0017 |

The event is not useful for estimating local score sensitivity: the truth is too
far away, so even large changes leave the category unchanged. It does show that
corridor area scales dramatically with the inherited parameter.

### 7.2 Analysis-only physical-distance length association

The fraction of centerline within distance \(D\) of legacy Medium truth is zero
for 0–50 NM, 8.68% at 75 NM, and 17.49% at 100 NM. These distances are diagnostic
probes, not proposed tolerances. They reinforce that this case cannot calibrate a
reasonable displacement distance.

## 8. Conceptual assumptions in Candidate A

The following are **proven from implementation**:

1. a linear forecast becomes an areal polygon;
2. the Medium 40% truth field is selected;
3. overlap is scored by physical area;
4. grade thresholds are 20% and 50%;
5. buffering occurs in degrees while area measurement occurs in EPSG:5070;
6. observed truth already contains dilation and neighborhood smoothing; and
7. the buffered polygon also participates in miss suppression.

The following are **mathematical consequences**:

* For equal along-line extent, wide truth generally contributes more overlap than
  narrow truth. Area is therefore sensitive to observed width as well as length.
* Convection crossing perpendicularly can fill only a short corridor section,
  but an exceptionally wide crossing can add substantial area without providing
  long continuous along-line coverage.
* Narrow continuous convection can occupy nearly all centerline length while
  filling only a small fraction of a wide corridor.
* One-sided convection is credited in proportion to corridor area on that side;
  the centerline itself need not be intersected.
* Gaps matter by their missing corridor area, not directly by along-line gap
  length or operational continuity.
* Angular buffering makes physical tolerance latitude- and orientation-dependent.
* Dilation/smoothing of observations plus forecast buffering supplies two
  distinct spatial-tolerance mechanisms.

The following are **plausible concerns requiring multi-case tests**:

* whether real perpendicular systems are systematically overrewarded;
* whether narrow linear systems are systematically underrated;
* whether 20%/50% categories provide useful operational discrimination for LINE;
* whether Medium truth and the double tolerance produce excess false verification;
  and
* how often LINE corridors suppress otherwise meaningful misses.

## 9. Separating definition, observation, tolerance, and score

A defensible method should specify five independent elements:

1. **Forecast assertion:** a Solid LINE asserts 75–100% linear convective
   coverage along an issued line under the product's operational definition.
2. **Qualifying observation:** Decision 1A establishes same-pair ≥40 dBZ and
   ≥FL250 qualification, but spatial aggregation for LINE remains open.
3. **Displacement tolerance:** a forecast may be useful despite modest location
   error; tolerance requires a physical rationale and units.
4. **Occurrence measure:** line length, segments, or another explicitly linear
   dimension should be considered separately from tolerance.
5. **Verification category thresholds:** production coverage and verification
   grade thresholds answer different questions and need not be numerically equal.

Neither a 75% areal truth field nor a 75% grade cutoff follows automatically from
the Solid production definition.

## 10. Candidate methods

### Candidate A — current buffered-area overlap

**Definition:** the equation in §4.3.

**Strengths:** simple; implemented; easy to map; shares AREA truth machinery;
provides symmetric-looking displacement tolerance in coordinate space.

**Weaknesses:** scores area rather than length; buffer is angular and
unjustified; orientation/width effects; Medium truth has no established linkage
to Solid semantics; observed smoothing and forecast buffer may double-count
tolerance; AREA grades are inherited.

### Candidate B — exact centerline intersection

\[
S_B = \frac{\operatorname{length}(L \cap Q)}{\operatorname{length}(L)}.
\]

**Strengths:** directly linear; physically interpretable in an appropriate
projected/geodesic length system; gaps and endpoints naturally affect length;
unrelated remote convection has no effect.

**Weaknesses:** exact intersection is likely too sensitive to displacement,
raster resolution, contour boundaries, and zero-width geometry; result depends
strongly on how \(Q\) is constructed; domain denominator remains open.

### Candidate C — centerline with physical-distance tolerance

Parameterize the line by physical arclength \(s\). Mark a location verified when
\(d(L(s),Q) \le D\), then integrate verified length over eligible length.

**Strengths:** preserves linear scoring while explicitly tolerating displacement;
uses physical units; both sides can be treated equally; gap behavior is
interpretable.

**Weaknesses:** requires an evidence-based \(D\), sampling/integration rules, an
observed field, and domain eligibility; buffering an already smoothed \(Q\) can
still double-count tolerance.

### Candidate D — segment-based verification

Partition eligible centerline length into fixed physical segments; classify each
using a defined nearby-observation rule; score qualifying segments / eligible
segments.

**Strengths:** operationally explainable; gaps are explicit; less sensitive to
individual pixel/contour perturbations; synthetic tests are straightforward.

**Weaknesses:** segment length and endpoint alignment are parameters; coarse
segments lose precision; a binary segment rule can create threshold jumps; a
distance and observational field are still needed.

### Candidate E — corridor linear occupancy

Use a physical-distance corridor only to associate qualifying observations with
positions along the centerline. Project/associate the observations to arclength
and score the union of occupied along-track intervals.

**Strengths:** cleanly separates tolerance (corridor width) from scoring dimension
(length); handles broad and narrow observations more similarly when along-line
extent matches; gaps and duplicate observations can be handled by interval union;
physically interpretable.

**Weaknesses:** association/projection rules become important near bends,
self-approaches, endpoints, and multipart lines; still needs distance, field,
domain, and grade policies; more complex than B/C.

## 11. Synthetic thought experiments

| Case | A: area corridor | B: exact line | C/E: tolerant length | D: segments |
|---|---|---|---|---|
| Perfect colocated line | High if truth has width | High | High | High |
| Parallel modest displacement | Depends on corridor/width | Zero | High if within justified D | High if within rule |
| Short perpendicular crossing | Can grow with storm width | Credits only crossed length | Credits nearby short length | Credits affected segments |
| Narrow continuous line | Can be low in wide corridor | High if coincident | High | High |
| Wide broken cells | Credits their area/width | Credits intersected lengths | Credits union of nearby lengths | Exposes gap segments |
| Exactly half-line observed | Not necessarily 50% | Approximately 50% | Approximately 50% | Near 50%, quantized |
| Endpoint-only convection | Area/end-cap dependent | Short length | Short nearby length | Endpoint segments |
| Just outside tolerance | Abrupt at buffer edge | Zero already | Abrupt at D unless softened | Rule-dependent |

These are mathematical behavior probes, not evidence for numerical parameters.

## 12. Observational truth choices

### Raw pair-qualified seed

Closest to the physical ≥40 dBZ/FL250 occurrence and avoids neighborhood
aggregation. It may be too pixelated and temporally/spatially strict for direct
line association, but any added tolerance remains visible.

### Dilated pair-qualified seed

Adds a one-cell morphological tolerance before any line tolerance. It can bridge
small gaps and should not be combined with a corridor without quantifying the
effective total tolerance.

### Sparse 25% truth

Represents a smoothed neighborhood with at least 25% qualifying occupancy. It is
broader than Medium and, in the frozen case, intersects 36.1% of the centerline.
That result does not make it the correct LINE field.

### Medium 40% truth

Current choice. It is a stricter smoothed areal-occupancy envelope, not a direct
measurement of 75–100% linear coverage. The sole case changes dramatically
between Sparse and Medium, showing this is a load-bearing choice.

### LINE-specific field

Could use pair-qualified observations plus explicitly defined along-line
association, continuity, and physical displacement rules. This is conceptually
cleaner but requires approval and validation.

Creating a 75% **areal** field solely because the source LINE is Solid would
conflate linear product semantics with neighborhood areal occupancy.

## 13. Continuity, gaps, and minimum length

Repository documentation supports Solid as 75–100% linear coverage and describes
an approximately 100-NM production minimum. It does not define, within the
available sources, corridor width, permitted gap morphology, or how raster
observations should be associated with line length. The parser verifies point
structure and coverage encoding but not physical length.

A future method must decide whether continuity means total occupied length only,
limits on individual/aggregate gaps, or segment occupancy. Two forecasts can both
have 75% occupied length but very different operational continuity (one long gap
versus many small gaps).

## 14. Domain interaction

Possible denominators are:

* full issued line length;
* issued length inside the verification domain; or
* another explicitly eligible portion.

The current method effectively uses the full buffered forecast denominator while
truth is domain-clipped. A linear method must not silently choose an in-domain
denominator and thereby resolve the separate domain policy.

## 15. Miss interaction

Future linear grading does not require removing the corridor from miss detection.
Misses ask how much observed truth was captured, not whether the LINE itself
verified. Nevertheless, an approved policy must decide whether a LINE suppresses
a miss by centerline proximity, an approved corridor, verified along-line
portions, or the existing whole buffer. Buffer width is therefore a coupled miss
parameter even if LINE grading becomes linear.

## 16. Echo-top interaction

The current top is sampled from the buffered polygon. This provides a corridor
diagnostic but can incorporate convection far from the centerline within the
angular buffer. A future LINE score need not change the diagnostic, but UI/report
language should continue distinguishing diagnostic top from linear verification.

## 17. Desirable methodological invariants

These are proposed analysis criteria, not approved requirements:

1. perfect colocation should not score below an otherwise identical displaced case;
2. adding qualifying observed length should not reduce score;
3. unrelated distant convection should not increase score;
4. a short perpendicular crossing should not verify an entire long line;
5. nearly continuous narrow convection should be recognized;
6. latitude alone should not change physical tolerance;
7. reversing vertex order should not change score;
8. equivalent segmentation/component representation should not materially change score;
9. duplicate observations should not inflate score; and
10. the score should state an interpretable physical fraction.

Candidate A is weak on 4–6 and 10. B is strong on interpretability but weak on
displacement robustness. C and E can satisfy most invariants if their physical
distance and interval rules are well defined. D adds discretization sensitivity.

## 18. Candidate decision matrix

| Criterion | A buffered area | B exact centerline | C line + distance | D segments | E corridor occupancy |
|---|---|---|---|---|---|
| Semantic fidelity | Low–moderate | High | High | High | High |
| Displacement tolerance | Implicit/angular | None | Explicit/physical | Explicit if defined | Explicit/physical |
| Gap treatment | Indirect area | Direct length | Direct length | Explicit but quantized | Along-track intervals |
| Arbitrary parameters | Buffer, truth, grades | Truth | Distance, truth, grades | Segment, distance, truth | Distance, association, truth |
| Physical meaning | Corridor fill | Exact occupied length | Nearby occupied length | Segment fraction | Nearby occupied along-track length |
| Smoothing interaction | High/double tolerance | Truth-dependent | Must be controlled | Must be controlled | Must be controlled |
| Orientation bias | Plausible/high | Low | Low | Low | Association-dependent |
| Complexity | Low | Low | Moderate | Moderate | Moderate–high |
| Domain policy | Full buffer today | Must define length | Must define length | Must define segments | Must define intervals |
| Miss coupling | Existing buffer | Separate decision | Tolerance may be reusable | Separate decision | Corridor may be reusable |
| Synthetic testability | High | High | High | High | High |
| Historical evidence need | Buffer/threshold calibration | Displacement impacts | Distance/threshold calibration | Segment/distance calibration | Distance/association calibration |

The matrix is qualitative and deliberately not a numerical ranking.

## 19. Evidence limitations

One Solid LINE can establish code paths, geometry, units, and reproducible
sensitivity for that case. It cannot establish climatological skill, optimal
distance or segment length, category thresholds, performance across storm
morphologies, or false-alarm/miss tradeoffs. Moreover, its Medium truth is too
far from the line to diagnose normal buffer sensitivity, and its frozen arrays
cannot reproduce Decision 1A truth.

## 20. Minimum additional evidence

### Synthetic invariants first

Construct mathematically obvious cases for perfect, displaced parallel,
perpendicular, narrow continuous, broken, half-line, endpoint, curved, reversed,
multipart, domain-crossing, and duplicate-observation geometries. Test candidate
monotonicity, representation invariance, latitude invariance, and gap behavior.

### Empirical parameter evidence

Collect more archived Solid LINEs spanning seasons, regions/latitudes, squall
lines, broken lines, MCSs, displacement errors, lengths, orientations, and domain
boundaries. Preserve Decision 1A masks and source centerlines. Compare candidate
scores against reviewer judgments and operational outcomes without selecting
parameters on the same cases used for evaluation.

Required decisions include observational field, physical distance, continuity/
gap rule, centerline/segment/interval implementation, eligible denominator,
category thresholds, minimum-length validation, and miss interaction.

## 21. Recommendation / working hypothesis

**Option C — preferred conceptual direction identified, parameters require
additional evidence.**

The preferred direction is a line-length score with explicit physical-distance
tolerance—most clearly Candidate E, with Candidate C as a simpler formulation.
This direction matches the resolved linear product semantics and separates
displacement tolerance from the quantity scored. It is not approved because the
repository and one frozen event cannot justify the observational field, distance,
gap rule, denominator, or grade thresholds.

Candidate A should remain labeled interim/legacy rather than endorsed. Candidate
B is a useful zero-tolerance reference but likely too displacement-sensitive.
Candidate D may be operationally interpretable, but no segment length is
supported.

## 22. Open questions

1. What official geometry/width, if any, accompanies the issued centerline?
2. What exactly constitutes 75–100% linear coverage and permitted gaps?
3. Which Decision 1A-derived observational representation should feed LINE verification?
4. What physical displacement tolerance is operationally meaningful?
5. Should score integrate continuous length, fixed segments, or occupied intervals?
6. What grade thresholds map that score to Well/Close/Overforecasted?
7. Is eligible length full-issued or in-domain?
8. Should minimum 100-NM eligibility be parser-validated and where measured?
9. How should Solid LINE affect Sparse missed-event suppression?
10. Should echo-top diagnostics remain corridor-based if scoring becomes linear?
11. What Decision 1B product-pair separation is acceptable?

Specification 0.1 should continue to list Solid LINE verification as unresolved.
No production behavior, baseline artifact, or approved Decision 1A rule is
changed by this analysis.
