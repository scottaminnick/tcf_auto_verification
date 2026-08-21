# Independent technical review of TCF auto verification

> **Current owner-decision note:** this review characterizes the inherited
> 15,000 km² behavior. Methodology 1.0 RC1 now removes that hard floor from
> forecast scoring and Candidate Miss visibility, adds EPSG:5070 Sparse/Medium
> density metadata, and adds non-reportable individual Medium-core review flags.
> See `docs/candidate_miss_owner_decision.md`.

**Review date:** 2026-08-18  
**Scope:** static trace of the repository, replay of the frozen baselines, and
small synthetic/numerical checks. No verification algorithm was changed as part
of this review. This assessment is now read together with
[`METHODOLOGY_SPEC.md`](METHODOLOGY_SPEC.md), version 0.1. Where that working
specification deliberately leaves a methodology decision open, this review
describes the current implementation and its consequences without treating one
unapproved option as the required answer.

## Executive assessment

The engine is appropriately conceived as **human-in-the-loop decision support**,
not as an unattended or authoritative verification system. In that role it is
useful for continued research and operational evaluation, provided the reviewer
can see data-quality and methodological limitations and the automated result is
preserved separately from reviewer edits. The implementation is coherent enough
to produce repeatable draft reports, and several important choices are explicit:
Sparse uses the
25% truth field, Medium AREA and Solid LINE use the 40% field, a forecast grade is the
truth-covered fraction of that forecast, and a miss is based on the
forecast-covered fraction of a Sparse truth object. The 15,000 km² truth-area
floor is correctly measured in EPSG:5070 after domain clipping.

The principal problem is not a conventional coding failure. Several stages can
produce a plausible categorical answer while measuring a subtly different
quantity from the apparent scientific intent:

1. Decision 1A is now implemented: reflectivity and echo-top criteria are joined
   within each usable nominal pair, then Boolean-unioned across the window;
2. a composite made from one paired scan is still accepted because adequacy
   thresholds remain open, but requested/resolved/used observations and product
   time separation are now exposed factually to the reviewer;
3. truth is clipped to the verification domain, but forecast denominators are
   not. This implements one of the specification's two open boundary policies,
   with potentially large score consequences;
4. contour construction truncates interpolated coordinates, mishandles holes
   and array-edge contours, and simplifies by a tolerance as large as one
   verification-grid interval;
5. overlap ratios and LINE buffers are evaluated in longitude/latitude units;
6. missed-event logic uses every forecast regardless of its coverage class,
   selecting one still-open miss-suppression policy without exposing it.

Those issues can change **Verified Well / Verified Close / Overforecasted /
Missed** recommendations. Some are implementation defects against requirements
already established by specification 0.1 (physical-area math, auditability,
missing-data visibility, hole handling, and unavailable-top handling); others
are explicit methodology decisions that must be resolved before version 1.0.
The existing baseline suite is a strong behavior-preservation harness, not
evidence that the preserved methodology is scientifically correct.

## End-to-end architecture and value trace

### Forecast acquisition and representation

1. `app.py` obtains the selected UTC date, issuance hour, and 4/6/8-hour lead,
   then `compute_valid_dt` performs a one-day rollover when the sum is at least
   24.
2. `fetch_iem_cow_raw` maps lead 4/6/8 to IEM products CFP02/03/04 and fetches
   the archived AFOS response. Unsupported leads silently fall back to CFP02 in
   the low-level function.
3. `parse_iem_cow_text` removes HTML, uses a digit/whitespace regex to extract
   AREA and LINE blocks, reads the coverage code, and converts tenths of degrees
   to longitude/latitude. Positive longitudes are forced west.
4. AREA features with at least three coordinates become repaired polygons via
   `Polygon(...).buffer(0)`. LINE features with at least two coordinates become
   round-ended Shapely buffers with half-width 0.15 degrees. A two-point AREA is
   also treated as such a line buffer. Parse exceptions are silently discarded.

### MRMS retrieval and temporal composite

1. `build_composite` requests nominal times at two-minute offsets from -14 to
   +14 minutes. Thus the configured “+/-15 minute” window actually samples 15
   nominal times over 28 minutes; it does not request the endpoints at +/-15.
2. EchoTop_18 and MergedReflectivityQCComposite keys are resolved independently
   to the nearest file within five minutes on the requested UTC day. Adjacent
   nominal times may resolve to the same physical file. The downloader
   deduplicates keys, but the fold plan can use the same scan repeatedly.
3. Only nominal entries for which both products download are decoded. Echo tops
   are multiplied by 3.28084; baseline values confirm that this produces values
   in thousands of feet from an input expressed in kilometers. The arrays are
   decimated by five to an approximately 0.05-degree grid.
4. Tops and reflectivity are independently reduced with `np.maximum` for
   diagnostics. Separately, each usable pair is jointly thresholded and its
   Boolean mask is unioned across the window for verification truth. Structured
   provenance records requested/resolved/used observations and timing.

### Observational truth construction

1. A grid point is a raw convective core when at least one usable nominal pair
   simultaneously has reflectivity at least 40 dBZ **and** EchoTop_18 at least
   25 kft.
2. The binary core field is dilated once with SciPy's default cross-shaped
   connectivity. A `20 x 20` uniform filter then computes the fraction of
   dilated grid-point samples in a moving square (roughly 1 degree by 1 degree,
   not a fixed physical area).
3. The smoothed field is thresholded at 0.25 for Sparse truth and 0.40 for the
   shared Medium AREA/Solid LINE truth. It is converted back to a binary mask before
   contouring.
4. `find_contours(mask, 0.5)` supplies sub-grid row/column coordinates, but the
   code truncates them to integer indices. Rings with ten or fewer vertices are
   discarded. Each surviving ring is independently made into a polygon,
   accepted only if initially valid, and simplified by 0.05 degrees.
5. Truth polygons are intersected with the ARTCC-plus-CMAC domain, repaired with
   `buffer(0)`, projected to EPSG:5070, and retained when their clipped area is
   at least 15,000,000,000 m². The retained geometries are unioned.

### Forecast grading, misses, echo tops, and report

For forecast polygon *F*, let *S* be the union of retained 25% truth and *M* the
union of retained 40% truth. The implemented score is:

```text
score(F) = area(F intersect S) / area(F)  for coverage code 3 (Sparse)
score(F) = area(F intersect M) / area(F)  for every other code
```

All areas in that equation are raw EPSG:4326 planar Shapely areas. A score at
least 0.50 is **Verified Well**, at least 0.20 is **Verified Close**, and lower
is **Overforecasted**. Equality is inclusive at both thresholds.

For each exploded component *T* of the Sparse truth union, the miss calculation
is:

```text
captured(T) = area(T intersect union(all forecast polygons)) / area(T)
```

`captured(T) < 0.20` is **Missed**; equality at 0.20 is not missed. This is a
different denominator from forecast grading and deliberately detects observed
events rather than false forecast area, but it also ignores forecast coverage
class.

Echo top is independent of the truth polygons. Grid-center samples inside the
forecast exterior are selected where composite reflectivity is at least 40 dBZ
and composite top is at least 25 kft. With at least six samples, the reported
value is the NumPy 90th percentile; otherwise it is zero and omitted from the
report. Forecasts and misses are ordered east-to-west by geographic centroid,
ARTCC names are assigned by any geometric intersection, and `build_report`
formats the review table into the four text sections.

## Critical findings and unresolved high-impact choices

This section distinguishes two kinds of risk. **Specification discrepancy**
means version 0.1 already states the required behavior and the code does not
meet it. **Open methodology choice** means the code has selected one plausible
option, but version 0.1 intentionally withholds approval pending meteorological
review. Both can materially change a recommendation; only the former should be
changed without first resolving an outstanding methodology decision.

### C1. Pair-first temporal qualification

**Status: Resolved by approved Decision 1A.**

The original implementation formed `max_refl` and `max_tops` independently, then evaluated
`(max_refl >= 40) & (max_tops >= 25)`. A pixel with 45 dBZ/20 kft at one time
and 20 dBZ/35 kft at another becomes a qualifying 45 dBZ/35 kft core even though
no scan met both criteria. The same temporal mixing inflates the top percentile.
This is a categorical risk around every truth and overlap threshold if the
approved policy is contemporaneous criteria. It is, however, consistent with
the specification's Option B if “occur somewhere within the window” is intended.
The defect that exists under either option is the absence of explicit
methodology/version metadata and observation-time provenance.

Production now derives a same-pair joint mask for every usable slot and unions
those masks across the window. Numerical maxima remain available only for
display and diagnostics. Decision 1B (permitted product timestamp separation)
remains open.

### C2. Scan adequacy policy remains unresolved; factual provenance is implemented

**Type: Specification discrepancy plus Outstanding Decision 2.** Version 0.1
requires requested/available/used timestamps and visible quality status, while
leaving the exact Normal/Review Required/Insufficient Data cutoffs open.

One successful tops/reflectivity pair can still produce a result. Product
keys are selected independently within +/-5 minutes, so a nominal pair can
refer to observations almost ten minutes apart. Multiple nominal requests can
also resolve to the same archived key, creating apparent cadence without new
information. The implementation now records these actual times, offsets,
per-product availability, exclusions, and counts in structured provenance and
shows a concise summary/detail view in the app. Missing scans change a
temporal maximum downward, but asynchronous pairing can create false joint
cores upward; the direction of bias is therefore not reliably conservative.

**Recommendation:** retain and test the new factual provenance. After
Outstanding Decision 2 is resolved, apply the approved Normal /
Review Required / Insufficient Data thresholds. In keeping with the human-review
specification, “Review Required” may retain a calculation with a warning;
“Insufficient Data” should withhold an automated recommendation rather than
silently appearing complete.

### C3. Domain treatment is internally inconsistent

**Type: Open methodology choice (Outstanding Decision 5 / Open Decision 4).**

Truth is clipped before minimum-area filtering, but each forecast's full,
unclipped area remains in the score denominator. A forecast half inside the
domain and perfectly covered inside can score only about 0.50; a feature mostly
outside can be labeled Overforecasted even though observations outside the
domain were intentionally removed. Miss coverage also uses unclipped forecast
geometry against clipped truth (which is harmless geometrically inside truth
but inconsistent as a declared scored domain).

**Recommendation:** retain this as a flagged policy choice until the domain
decision is approved. Test both denominators and present the score sensitivity
for boundary-crossing features. If the in-domain denominator is selected, define
the evaluated object as `F intersect domain` and handle zero in-domain area. If
the entire forecast is selected, document why observations outside the truth
domain are not required and warn reviewers when the result is boundary-driven.

### C4. Truth polygon topology and boundaries are not faithfully reconstructed

**Type: Specification discrepancy.** Version 0.1 requires objective geographic
intersection and physical area to be mathematically correct and processing
choices to be explicit.

There are four interacting problems:

* contour interpolation is discarded by integer truncation, shifting an edge up
  to a grid interval toward lower array indices;
* every contour ring is treated as an exterior, so an observed hole becomes a
  filled polygon when unioned;
* a contour touching the raster boundary is open and `Polygon` closes it with a
  straight chord, inventing an edge;
* `simplify(0.05)` is approximately a full verification-grid interval and is not
  revalidated.

These affect object area, the 15,000 km² decision, overlap fractions, and misses.
They are especially dangerous at domain/raster edges and for donut or nested
fields.

**Recommendation:** polygonize cell footprints or use a raster-to-vector method
with an explicit affine transform and topology-aware exterior/interior handling.
Clip the raster to a declared domain with padding before contouring, preserve
holes, and validate after every topology-changing operation. Avoid simplification
for scoring; simplify only a display copy.

### C5. Verification ratios and LINE width are calculated in angular space

**Type: Specification discrepancy for AREA physical-area ratios; open
methodology choice for the long-term LINE metric.**

Using the same CRS in numerator and denominator does not make a planar
degree-squared ratio equal to a physical-area ratio for a feature spanning
latitude. Northern portions are overweighted because one longitude degree is
physically shorter. The effect can change a category for large north-south
features or truth concentrated at one latitude end.

The 0.15-degree LINE half-width is additionally anisotropic: its east-west
physical width contracts with cosine(latitude), while its north-south width
does not. Round caps and joins are round in degrees rather than distance. The
nominal full width is about 33 km north-south, roughly 25 km east-west at 40°N,
and smaller farther north.

**Recommendation:** perform scoring, buffering, clipping, and minimum-area
measurement in one documented CONUS equal-area CRS (EPSG:5070 is already used),
then transform display geometry back to EPSG:4326. Buffer LINEs by an
operationally approved distance in meters.

### C6. Miss logic can let the wrong forecast class suppress a miss

**Type: Open methodology choice (Outstanding Decision 7 / Open Decision 6).**

All Sparse truth objects are tested against the union of all forecasts. A Solid LINE
forecast is graded only against 40% truth, yet any sliver of that Solid LINE polygon
can cover 20% of a 25%-to-40% Sparse truth object and prevent a miss. Overlapping
or very broad Overforecasted polygons can likewise erase misses. This may be an
intentional “any forecast awareness” metric, but it is not symmetric with the
coverage-dependent hit rule and is not documented as an operational criterion.

**Recommendation:** obtain a methodology decision: (a) any TCF feature suppresses a
Sparse miss, (b) each forecast suppresses only truth appropriate to its coverage
code, or (c) misses are reported separately by truth threshold. Encode that
decision in synthetic tests.

## Moderate findings

### M1. Spatial smoothing is angular, resolution-coupled, and shifted

A 20-cell window on a 0.05-degree grid is a 1-degree square whose physical area
changes with latitude. `uniform_filter`'s default reflected boundary condition
can replicate convection at array edges. An even-sized window has an asymmetric
discrete origin, so changing SciPy behavior or using an odd window can shift the
field by half a cell. One dilation iteration uses default cross connectivity and
adds about 5.5 km north-south but a latitude-dependent east-west distance.

The resulting number is a fraction of **dilated grid-point samples within an
angular kernel**, not necessarily meteorological areal coverage in a fixed-size
physical neighborhood. This may be calibrated behavior, but names and report
language should not imply a scale-independent coverage percentage.

### M2. Order of thresholding, contouring, clipping, and minimum area needs a
formal requirement

Applying the area floor after clipping is defensible for an in-domain event
minimum and prevents a mostly Canadian object from surviving on out-of-domain
area. It will, however, reject a scientifically large storm split by the domain
boundary when its in-domain remnant is under 15,000 km². Applying the threshold
after smoothing means the criterion measures the smoothed 25%/40% envelope, not
raw convective area. These are policy choices, not self-evident truths.

Also, components are area-filtered before the final union. Nearby sub-threshold
objects do not collectively meet the minimum unless smoothing already connected
their masks. Tests should establish whether the minimum applies per connected
object or to an aggregate complex.

### M3. Solid LINE and Medium AREA use the same observational truth

LINE coverage code 1 (Solid, 75–100% linear coverage) and AREA code 2 (Medium, 40–74%) both use
the 40% truth field. Solid LINE therefore receives no stricter observational test
than Medium. The repository explicitly describes this choice, so it is
consistent code, but it does not verify the forecast's 75–100% linear coverage
with a linear observational metric. This needs meteorological confirmation.

### M4. Echo-top sampling can be biased or missing

The 90th percentile is based on centers of the decimated grid, not area-weighted
pixels. It excludes holes incorrectly, omits boundary points according to
Matplotlib `Path` behavior, and uses a bounding-box slice. Small/narrow polygons
and LINE buffers can contain fewer than six centers and report no top even with
valid convection. Conversely, a forecast hole can contribute a high cell. The
top comes from independent temporal maxima and can therefore be systematically
high or physically unmatched to reflectivity. `np.percentile` interpolation can
change slightly across NumPy versions and is not configured with an explicit
method.

Use a raster mask that honors all rings and multiparts, document pixel-center
versus pixel-area inclusion, return `NA` plus sample count instead of zero, and
specify the percentile method. A physical minimum sampled area is preferable to
an arbitrary count when grid resolution can change.

### M5. Grid compatibility is now checked before compositing

Reflectivity and echo-top shapes/coordinates are now compared within a pair, and
each later scan is compared exactly with the first used verification grid before
`np.maximum`. Incompatible scans are excluded with a provenance reason rather
than silently combined. CRS metadata is not separately retained, and there is no
approved resampling policy; exact rejection is appropriate until one exists.

### M6. NaN and missing-value behavior is not explicit

`np.maximum` propagates NaN based on ordinary NumPy semantics, potentially
turning a pixel missing if any later scan is NaN rather than using other valid
scans. Whether cfgrib supplies NaN, masked values, or product-specific sentinels
is not validated. Negative EchoTop_18 missing values happen to fail the >=25
test, but relying on that is format-specific. Decode product metadata, normalize
missing values, track sample availability per pixel, and choose `maximum` versus
`fmax` intentionally.

### M7. A logical multipart forecast can become several graded forecasts

Forecasts are exploded before grading. `buffer(0)` can turn one invalid AREA
into a MultiPolygon, so one source feature may receive multiple categories and
indices, while the miss union treats it as one contribution. Source feature IDs
are not retained, making this impossible to audit from the report. Decide
whether grading is per source feature or per connected component and preserve a
stable source identifier either way.

### M8. Parser failures are silent and validation is weak

Unknown coverage codes were previously treated as Medium/Solid-line for truth selection but
labeled Sparse in the report. Declared point counts can be underfilled without
rejection, broad exceptions discard malformed features, and no warning/count is
returned. A technically valid report may therefore omit forecast objects. The
regex can absorb numeric trailers. Parse into a typed intermediate record,
validate codes and exact coordinate counts, and surface rejected records.

## Minor findings

* `compute_valid_dt` handles the offered leads correctly but only subtracts 24
  once; it is not a general timedelta implementation. Unsupported lead values
  default to CFP02 rather than failing.
* `get_artccs` counts boundary touches as intersections and does not sort center
  identifiers explicitly. A tiny repaired sliver or point touch can add an ARTCC.
* Centroid sorting occurs in EPSG:4326; this is adequate for east-west display
  order but centroids of concave objects may lie outside the object.
* Initial forecast validity is repaired with `buffer(0)`, but the resulting type
  and area change are not recorded. Empty LINE buffers are appended without an
  explicit check.
* The pre-simplification validity check drops invalid truth rings rather than
  repairing or reporting them. Post-simplification validity is unchecked.
* The `len(contour) > 10` rule is a second, undocumented size filter in vertex
  count, distinct from the physical minimum-area rule. Its physical meaning
  changes with shape and raster resolution.
* A top of exactly zero and unavailable top are conflated in report formatting.
* Boundary annotation is only +/-0.005 around forecast grade thresholds. It does
  not flag truth values near 0.25/0.40, physical areas near 15,000 km², or miss
  capture near 0.20, all of which can change categorical output.
* Exact pins improve deployment repeatability, but the stated future-version
  pins make recreation dependent on package availability and Python ABI. The
  Docker image, PROJ data, GEOS, ecCodes, and platform should be recorded with a
  result for auditability.

## Methodological questions for the domain expert

1. Is a rolling maximum over approximately 30 minutes the intended verifying
   observation, or should coverage represent persistence/frequency during that
   period? A maximum rewards any transient cell and expands moving storms into a
   swath.
2. What maximum actual timestamp separation is acceptable within a nominal
   reflectivity/echo-top pair (Decision 1B)? Pair-first qualification is approved.
3. Does the EchoTop_18 product threshold correspond exactly to the intended TCF
   top definition, and are 25/30/35/40 kft the desired reporting bands?
4. What physical neighborhood defines 25%, 40%, and 75% coverage? Is the current
   approximately 1-degree square, after one-cell dilation, an approved proxy?
5. Should Solid LINE require a 75% or linear observational metric, or is the
   interim 40% areal truth field operationally appropriate for buffered lines?
6. Is 15,000 km² measured on the smoothed coverage envelope, raw core pixels,
   or some other convective object? Does it apply before or after domain clipping?
7. Is forecast success correctly `truth-covered forecast area / forecast area`,
   or should it measure captured truth, object matching, or both? The current
   metric penalizes broad forecasts but does not require one-to-one matching.
8. May one forecast polygon verify multiple truth objects and vice versa? Current
   unions explicitly allow it and eliminate object identity.
9. Should any forecast suppress a Sparse miss, even a Solid LINE buffer that fails
   its own 40% criterion or an otherwise Overforecasted polygon?
10. What physical corridor does a LINE forecast represent, and should it be
    graded by buffered area, distance-to-observation, length verified, or an
    object-based line metric? Area overlap makes the chosen width part of the
    score and gives endpoint caps nontrivial weight for short lines.
11. Should echo top characterize all convection inside the forecast, only the
    matched truth, or the source feature's predicted top? The current calculation
    samples any qualifying composite cell inside the forecast even when the
    forecast is Overforecasted.
12. Is the domain intended to govern both truth and forecasts, and how should a
    forecast crossing several ARTCCs be attributed: any touch, area majority, or
    separate center-specific pieces?

## Existing BUG INVENTORY assessment

| # | Status and severity | Assessment / action |
|---|---|---|
| 1 No-MRMS crash | **Partly fixed; Moderate operational** | A clear `RuntimeError` now replaces the downstream `TypeError`, but Streamlit has no user-level recovery and no result-quality state. Keep the guard and handle it in the UI. It interacts with #10: zero scans fail, one scan passes. |
| 2 LINEs become polygons | **Fixed; former Critical** | Multi-vertex LINEs now always use `LineString.buffer`. Preserve a parser test. The angular buffer and area metric remain critical methodological concerns (C5). |
| 3 Truncated contours | **Exists; Critical** | Integer indexing discards `find_contours` interpolation and shifts boundaries. Fix as part of a topology-safe raster/vector rewrite, not a local rounding tweak. |
| 4 Simplify not revalidated | **Exists; Moderate** | Output is not checked after a scoring-relevant 0.05-degree simplification. Remove simplification from scoring or validate/repair and quantify area change. Interacts with #3 and holes. |
| 5 Holes ignored for echo tops | **Exists; Moderate** | Exterior-only `Path` includes holes. Use a topology-aware rasterizer. It can raise the reported percentile materially. |
| 6 Degree-space areas | **Exists; Critical for broad/N-S objects** | Ratios only approximately cancel distortion. Reproject scoring geometries to equal area. This also fixes physical LINE buffering and makes domain treatment coherent. |
| 7 Default coverage mismatch | **Exists; Minor symptom, Moderate robustness** | Grading defaults to 3 while the review table defaults to 25; both label Sparse accidentally, but 25 would select 40% truth if it entered grading. Reject missing/unknown coverage rather than default silently. |
| 8 Asymmetric miss test | **Exists; Critical/Methodological** | Sparse truth is compared with every forecast. This can suppress misses inconsistently. Obtain and test the intended policy (C6). |
| 9 Dead index assignment | **Exists; Minor** | Initial indices are overwritten after sorting. No scientific effect, but source identity should replace ephemeral indices. |
| 10 Silent scan drops | **Visibility fixed; adequacy policy open** | Per-product resolution/download status, actual times, offsets, use, and exclusions are returned and displayed. A one-pair result remains possible until Decision 2 defines quality thresholds. |
| 11 Grid-shape assumption | **Fixed for exact compatibility** | Product-pair and cross-scan shapes/coordinates are checked before compositing; mismatches are recorded and excluded. No resampling policy is implied. |
| 12 Small-core tops become zero | **Exists; Moderate** | Fewer than six samples becomes an omitted top rather than missing/low confidence. Return nullable top plus sample count and sampled area. |
| 13 Fragile regex splitting | **Exists; Moderate** | Numeric material can be absorbed and malformed records disappear under broad exception handling. Replace with validated record parsing and diagnostics. |
| 14 Hard-coded western hemisphere | **Exists; Minor in current CONUS scope** | Appropriate only while the product/domain contract is CONUS. Assert that contract or support signed/hemisphere-aware coordinates before OCONUS use. |

The inventory header says none of the 14 defects are fixed, but item 2 is
explicitly fixed and item 1 has a clearer guard. The inventory is therefore
useful historical documentation but not a clean current-state defect list.

## Additional issues discovered

The following material issues are not separately identified in the inventory:

1. **Resolved: pseudo-simultaneous cores from independent maxima (C1).**
2. **Independent nearest-time pairing and duplicate reuse of scans (C2).**
3. **Truth-only domain clipping and the resulting forecast-denominator bias
   (C3).**
4. **Hole filling and artificial closure of raster-edge contours (C4), distinct
   from ignoring forecast holes during echo-top sampling.**
5. **Angular smoothing/dilation with reflected array boundaries and an even
   filter kernel (M1).**
6. **Solid LINE forecasts use the Medium AREA truth threshold (M3).**
7. **No reflectivity coordinate validation and no equal-shape shifted-grid
   detection (M5).**
8. **NaN/missing-value propagation is unspecified (M6).**
9. **One source MultiPolygon can become multiple independently graded forecast
   rows (M7).**
10. **Unknown coverage codes select 40% truth but print as Sparse (M8).**
11. **The vertex-count contour filter is an undocumented second size criterion.**
12. **Quality-boundary flags omit truth, area, and miss thresholds.**

## Specification 0.1 conformance and functional gaps

The working specification clarifies that a mathematically imperfect autonomous
system is not the goal; visible, reproducible decision support is. Against that
standard, the repository has useful foundations but does not yet implement the
full review workflow:

| Specification area | Current state | Assessment |
|---|---|---|
| Automated first-pass categories | Implemented with retained fractions and a review-table seam | Substantially aligned, subject to the calculation findings above. |
| Automated vs reviewer-approved state | The pipeline builds a plain review table, but `app.py` stores and displays only the automated GeoDataFrames/report | **Gap:** no separate approved category, inclusion flag, geographic text, or reviewer notes are persisted. |
| Editable FAA draft | The report is rendered as escaped HTML and offered as a text download | **Gap:** it is not editable in the application and edits cannot regenerate the downloaded report. |
| MRMS provenance and quality | Structured requested/resolved/downloaded/used records, actual timestamps, offsets, pair separation, exclusions, grid status, and factual summary are returned and displayed | Provenance requirement implemented; Normal/Review Required/Insufficient thresholds intentionally remain open. |
| Marginal forecast grades | A ±0.005 internal boundary flag exists in the review table | Partial: the app does not display the fraction or marginal flag, and misses/truth-area boundaries are not flagged. |
| AREA physical-area metric | Forecast and hit ratios use EPSG:4326 planar area | **Nonconforming:** specification 0.1 explicitly requires an appropriate physical-area projection. |
| Minimum truth area | EPSG:5070 and 15,000 km² are implemented after smoothing, thresholding, polygonization, and domain clipping | Physical units align; processing order awaits Open Decision 5. |
| Domain | Out-of-domain truth is clipped; full forecast denominator remains | One documented but unapproved option; requires Open Decision 4 and reviewer flagging meanwhile. |
| LINE identity | LINE type survives parsing and report labeling | Partial: interim buffering is visible only as “Line”; physical width and limitations are not shown. |
| Echo-top diagnostics | 90th percentile is calculated and conditionally printed | Partial/nonconforming: statistic remains open, while hole handling and unavailable-as-zero already conflict with section 19. |
| Audit metadata/version | IT/VT/FH appear in report; parameters exist in code; scan provenance accompanies the live result | **Partial gap:** no methodology version, input hashes, or automated/reviewer-approved history is persisted. |
| Publication boundary | Download is a draft text artifact; no autonomous publication exists | Aligned in principle, though the UI should label it explicitly as an automated draft awaiting approval. |

These functional gaps should be treated separately from the ten open
meteorological decisions. Provenance capture, separate automated/reviewer state,
physical AREA calculations, and honest missing-value representation can proceed
without prejudging the open choices.

## Baseline and regression framework assessment

The harness has meaningful engineering value:

* frozen raw TCF and composite arrays isolate deterministic grading from network
  and archive changes;
* expected records cover counts, categories, coverage fractions, tops, bounds,
  ARTCCs, and report text;
* fixture tests exercise the harness and parameter wiring;
* app parity checks Streamlit wiring and report persistence;
* exact dependency pins reduce accidental numerical drift;
* old baseline sets preserve known cadence and LINE-behavior transitions.

What it guarantees is **preservation of captured behavior under frozen inputs**.
It does not independently establish correct scan selection, correct GRIB units,
simultaneity, grid registration, raster-to-vector topology, equal-area overlap,
or the meteorological meaning of thresholds. `expected.json` is generated by the
same pipeline it later checks. Therefore a regression can faithfully preserve
every critical issue in this review and remain green. App parity similarly
shows that UI glue reaches the same pipeline; it is not an independent oracle.

The real-event baselines should remain as characterization tests, but should be
labeled accordingly. Scientific acceptance needs synthetic analytic oracles and
at least a few manually adjudicated cases whose expected geometry and grade were
computed independently (for example in an equal-area GIS workflow).

At review time, the checked-in suite is also **not green against the checked-in
pipeline**: only 2 of 6 events pass. Four expected files retain an out-of-domain
`UNKNOWN` miss that the current default domain mask removes (one miss each in
`20260524_13Z_F04`, `20260524_19Z_F04`, `20260524_19Z_F06`, and
`20260728_19Z_F04`). This is consistent with stale expectations from behavior
before domain clipping, but contradicts the README statement that `make check`
is 6/6. That mismatch does not itself prove the current domain behavior wrong;
it means the repository currently lacks a trustworthy green regression signal.
The expectations should be versioned/recaptured only after the domain policy is
approved, with the intentional removed misses documented.

### Numerical reproducibility

Pinned versions make current replay relatively stable, but categorical
reproducibility remains sensitive to:

* SciPy filter boundary/origin semantics and binary-structure defaults;
* scikit-image contour ordering/interpolation and topology at ambiguous cells;
* Shapely/GEOS `buffer(0)`, simplification, union, intersection, and polygon
  ordering changes;
* PROJ transformation/database changes in the 15,000 km² boundary decision;
* NumPy percentile interpolation and NaN behavior;
* raster resolution/decimation origin, because every spatial tuning parameter
  is in cells or degrees;
* inclusive `>=` at 0.25, 0.40, 0.50, and 0.20 versus strict `<` for misses;
* formatting after floating computation.

A result manifest should store dependency/library versions, input hashes, scan
keys and timestamps, grid metadata, parameter values, domain version/hash,
intermediate counts/areas, and distances to **all** decision thresholds.

## Recommended tests

### Priority 0: mathematical grading oracles

Use simple rectangles in EPSG:5070 and bypass MRMS construction so expected
physical areas are exact.

1. **Forecast overlap 100%, 50%, 50%-epsilon, 20%, 20%-epsilon, and 0%.** Assert
   both fraction and category. Detects denominator changes and inclusive-boundary
   mistakes.
2. **North-south forecast with truth in its north half, then south half.** An
   equal-area implementation must give equal physical answers for equal areas;
   the current degree-space method will expose latitude weighting.
3. **Forecast partly outside domain with perfect in-domain truth.** Assert the
   chosen domain policy and zero-in-domain behavior. Detects C3.
4. **Overlapping and nested forecasts over one truth object.** Assert each
   forecast's score is independent and document whether overlap may suppress a
   miss. Detects accidental cross-polygon influence.
5. **One truth object captured at 20%, just below, and just above.** Assert exact
   strictness of Missed and separately test which coverage classes suppress it.
6. **Sparse AREA, Medium AREA, and Solid LINE forecasts over appropriate synthetic fields.** This makes the LINE policy explicit and catches code/label mapping
   errors.

### Priority 0: temporal-composite oracles

7. **Noncontemporaneous refl/top test.** Scan A has 40+ dBZ and low top; scan B
   has weak reflectivity and 25+ kft top. Expected joint truth is false if
   simultaneity is required. Detects C1 directly.
8. **0, 1, 2, minimum-acceptable, and complete unique paired scans.** Assert
   reject/quality status rather than merely array output. Detects C2/#10.
9. **Product timestamps at allowed and disallowed separations, including
   midnight.** Detects independent mispairing and date-prefix errors.
10. **Duplicate nearest-key resolution.** Ensure one physical scan counts once
    in quality and temporal statistics.
11. **Grid mismatch matrix:** different shape, reversed latitude, shifted equal
    shape, unequal spacing, and tops/refl coordinate mismatch. All should be
    deliberately regridded or rejected.
12. **NaN and missing sentinels.** Establish whether valid scans fill missing
    pixels and verify a per-pixel availability threshold.

### Priority 1: truth raster/vector oracles

13. **Single filled cell/block with known affine grid.** Compare polygon bounds
    to cell edges, not centers; detects off-by-half/one-cell errors.
14. **Donut mask.** Assert one polygon with one interior ring and correct area;
    detects hole filling.
15. **Nested islands and diagonal connectivity.** Locks the intended 4- versus
    8-connectivity and topology.
16. **Object touching each raster edge/corner.** Ensures no artificial chord or
    reflected smoothing expansion.
17. **Physical smoothing at low and high latitude.** Equivalent projected
    patterns should produce equivalent coverage.
18. **Minimum area exactly 15,000 km² and +/-epsilon, before and after domain
    clipping.** Detects units, projection, operation order, and inclusive
    boundary errors.
19. **Two nearby sub-minimum objects.** Establish whether the area floor applies
    per component or complex.
20. **Invalid/self-touching contour and simplification-sensitive geometry.**
    Assert a valid, area-conserving result or an explicit rejection diagnostic.

### Priority 1: LINE and echo-top oracles

21. **Same physical LINE at 25°N and 48°N, horizontal and vertical.** Assert a
    constant meter width and comparable score after projection.
22. **Two-point and many-vertex LINE with short and long segments.** Detects
    accidental closure, cap/join effects, and unstable buffering.
23. **Forecast polygon with a hole containing the highest top.** The hole's
    sample must not affect the percentile.
24. **Known top arrays of 5, 6, and more valid pixels.** Assert nullable behavior,
    sample count, units, and an explicitly selected percentile method.
25. **Small/narrow polygon crossing pixels without containing centers.** Locks
    the chosen pixel-area inclusion rule rather than silently returning no top.
26. **Top paired with weak reflectivity in another scan.** Ensures truth uses
    contemporaneous qualifying observations while the retained top percentile
    remains explicitly diagnostic.

### Priority 2: parser, reporting, and reproducibility

27. AREA/LINE fixtures for missing fields, invalid coverage, underfilled point
    count, numeric trailer, HTML wrapping, positive/signed longitude, degenerate
    geometry, and multiple ARTCC intersections; assert explicit diagnostics.
28. Source MultiPolygon fixture; assert the chosen per-feature/per-component
    identity policy and stable IDs.
29. Report test with missing top versus genuine numeric top, unknown category,
    and editable review-table changes.
30. Cross-version/container golden test on analytic inputs. Compare physical
    areas and categories, not WKB ordering; require threshold-distance metadata.

## Recommended improvement sequence

1. **Adopt specification 0.1 as the working contract and track its open
   decisions explicitly.** Resolve Decision 1B, time-window quality floors,
   Solid LINE truth methodology, domain denominator, area-order policy,
   miss suppression, LINE metric, echo-top statistic, and report fields one at a
   time. These choices determine what a recommendation means; code should record
   its current choice without presenting it as approved methodology 1.0.
2. **Add Priority 0 synthetic tests before changing algorithms.** They provide
   independent mathematical oracles and make intentional classification changes
   reviewable rather than merely turning characterization baselines red.
3. **Preserve pair-first qualification and composite provenance, then implement
   the approved quality policy.** Decision 1A is implemented; Decision 1B and
   Normal/Review Required/Insufficient behavior remain unresolved.
4. **Move the scoring domain and all physical geometry operations to an
   equal-area CRS.** Clip forecasts and truth consistently, buffer LINEs in
   meters, and calculate all overlap/minimum areas in the same space. This
   removes latitude and out-of-domain classification bias.
5. **Replace scoring contours with topology-safe raster polygonization.** Retain
   holes and cell-edge geometry, handle raster boundaries explicitly, and keep
   simplification display-only. This stabilizes minimum-area, overlap, and miss
   classifications.
6. **Make smoothing/dilation physical and resolution-independent.** Define a
   projected kernel/structuring element and explicit edge handling. Otherwise a
   grid-resolution or latitude change silently changes the meteorological test.
7. **Implement the approved miss and Solid LINE policies.** Do this after the common
   geometry foundation so hit/miss asymmetries are deliberate and testable.
8. **Rebuild echo-top sampling with topology-aware masks and quality metadata.**
   Return nullable top, valid sample count/area, percentile method, and temporal
   provenance. This prevents omitted or inflated tops from appearing
   authoritative.
9. **Harden parser and feature identity.** Reject or surface malformed records,
   validate coverage codes, preserve source IDs, and decide multipart semantics.
   A complete scientific calculation is not useful if inputs disappear silently.
10. **Implement separate automated and reviewer-approved state plus an editable
    draft.** Preserve the objective category/fraction while allowing final
    category, inclusion, geographic description, and notes to regenerate the
    report. This fulfills the human-in-the-loop purpose without permitting edits
    to obscure what the algorithm calculated.
11. **Retain current baselines as characterization tests and add independent
    adjudicated cases.** Recapture only when an intentional methodological change
    is approved; record before/after classification impacts rather than treating
    baseline parity as scientific validation.
12. **Add result manifests and operational error states.** Expose scan adequacy,
    grid checks, rejected TCF records, threshold proximity, input hashes, domain
    version, and dependency versions in the UI/report archive. This makes each
    result auditable and prevents a valid-looking report from hiding weak input.
13. **Only then pursue performance or broad architectural refactoring.** The
    current separation of retrieval, pure grading, review table, and report is a
    good validation seam. Optimize after the authoritative method is covered by
    tests so speed changes cannot silently change science.

## Bottom line

The implementation is sufficiently structured to evolve into a defensible
verification engine, and the baseline harness is a useful safety net. It should
continue in parallel/research evaluation, with human review. It should not yet
be treated as scientifically validated or used for unattended categorical
judgment. The first development work should establish independent synthetic
oracles, use the new MRMS provenance while defining quality thresholds, and move AREA calculations into a
physical-area CRS. Pair-first temporal simultaneity is implemented; Decision 1B,
the forecast-domain denominator, miss
interactions, and LINE treatment should change only after their corresponding
specification decisions are approved. UI workflow development can continue, but
should prominently expose an experimental status, distinguish automated from
reviewer-approved output, and show MRMS input-quality metadata until those
issues are resolved.
