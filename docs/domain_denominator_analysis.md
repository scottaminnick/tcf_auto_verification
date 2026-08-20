# Verification-domain forecast denominator analysis

## 1. Executive summary

This is a read-only analysis of an unresolved policy. It does not change the
verification domain, forecast geometry, truth construction, grading, misses, or
reports.

The implementation currently clips observational truth to a verification domain
but scores every forecast against its full issued (or, for LINE, buffered)
geometry. For forecast geometry `F`, domain `D`, and retained truth `Td ⊆ D`, the
implemented score is

```text
score_A = A(F ∩ Td) / A(F).
```

An in-domain alternative would use `Fi = F ∩ D` and

```text
score_B = A(Fi ∩ Td) / A(Fi).
```

Because `Td ⊆ D`, both have the same numerator and, when `A(Fi) > 0`,
`score_B ≥ score_A`. The difference is policy, not a geometric correction:
Candidate A holds a forecast accountable for its complete issued extent;
Candidate B scores only where this verifier asserts observational eligibility.

The frozen-case audit examined all 48 features. Forty-one are wholly in-domain,
seven AREA features cross the boundary, and none is wholly outside. The minimum
in-domain fraction is 85.3665%; three features have more than 1% outside, two
more than 5%, and one more than 10%. None has more than 25% outside. No category
changes occur in the legacy frozen replay because the materially clipped
features have zero observed overlap. The largest nonzero score change is only
0.0035 percentage points. Thus the asymmetry is real and present, but these six
events do not exercise the policy where it matters to a category.

**Recommendation status: Option C.** Verification eligibility provides the
clearer conceptual direction—numerator and denominator should ordinarily share
the same observational support—but an explicit operational decision is still
needed for forecasts with little or no eligible geometry and for any mismatch
between the official product domain and this repository's approximate domain.
The policy remains unapproved.

## 2. Evidence classes and limitations

This report separates:

* **Implementation fact:** directly traced from executable repository code.
* **Repository evidence:** committed geography, documentation, history, and
  frozen inputs.
* **Mathematical consequence:** follows from set/area identities.
* **Analysis inference:** interpretation requiring policy judgment.

The frozen `arrays.npz` files contain independent temporal maxima and cannot
reconstruct approved Decision 1A pair-first truth. The audit therefore uses the
explicit legacy replay solely to hold the truth field fixed while comparing two
denominators. Its category numbers are not a new Decision 1A validation.

An attempted search of authoritative AWC/NWS/FAA sources was unavailable in the
analysis environment (the search service returned HTTP 401). No official product
domain was inferred or substituted. Repository geography is not presented as an
official TCF boundary.

## 3. Verification-domain trace

### 3.1 Construction and source

`verification_domain()` reads two committed EPSG:4326 sources:

1. `artcc1.geojson`: 21 Polygon features with bounds approximately 128°W–67°W
   and 24°N–49°N; and
2. `cmac_domain.geojson`: one Polygon described in its metadata as a CMAC
   verification-domain supplement, bounded by 90°W–68.5°W and 43°N–48°N.

The function unions all ARTCC geometries with the CMAC geometry, then applies
`buffer(0)` to dissolve interior edges and repair self-intersections. It does not
call `simplify`. The result in the current files is one valid Polygon, with 50
interior rings, in EPSG:4326 and with bounds approximately
`(-128.000016, 24.000004, -66.999999, 49.000009)`.

The CMAC README is unusually important evidence: it says the northern boundary
was traced by eye from a graphic, is a judgment call, and is not an official
shapefile. It also says the southern overlap is intentionally generous to avoid
slivers. Git history first introduces the supplement and its README in commit
`61f92e5` (2026-08-17), titled “Add verification domain mask so out-of-domain
truth stops making misses.” This supports classifying `D` as a selected policy
domain assembled from ARTCC geometry and an approximation—not demonstrated
MRMS availability and not an authoritative product-domain file.

### 3.2 Geographic interpretation

The ARTCC source has `COUNTRY=United States` and `ONSHORE=1` attributes. The
union is not a simple national land polygon: it contains holes and follows ARTCC
and supplement geometry. Coordinate probes show that Toronto, Ottawa, and
Montreal fall inside the committed union, while Vancouver and example open-ocean
points at 70°W/35°N and 90°W/25°N do not. These probes describe only this
geometry; they do not establish jurisdiction, official Canadian/FIR inclusion,
or actual MRMS availability.

### 3.3 Where clipping occurs

`run_verification()` obtains `D` when `apply_domain_mask=True` and passes it to
both Sparse and Medium truth polygonization. `extract_tcf_polygons()` intersects
truth with `D`, applies `buffer(0)`, removes empties, projects to EPSG:5070, and
then applies the 15,000 km² minimum. Therefore clipping occurs **before** the
minimum-area filter. Forecast geometry is never intersected with `D` in the
scoring path.

## 4. Current forecast denominators

Let:

* `F` be one parsed forecast polygon. For Solid LINE, `F` is the existing
  centerline buffered by `LINE_BUFFER_DEG`; this analysis does not revisit that
  interim methodology.
* `T25` and `T40` be un-clipped Sparse and Medium threshold fields after spatial
  processing.
* `S = retained(T25 ∩ D)` and `M = retained(T40 ∩ D)`, where retention includes
  the post-clip 15,000 km² filter.
* `A` be Shapely area after projection to EPSG:5070.

Production explodes multipart forecasts and scores each component. Its current
equations are:

```text
AREA 3 / Sparse: score = A(F ∩ S) / A(F)
AREA 2 / Medium: score = A(F ∩ M) / A(F)
LINE 1 / Solid:  score = A(Fbuffer ∩ M) / A(Fbuffer)
```

At least 50% is Verified Well; at least 20% is Verified Close; otherwise it is
Overforecasted. Both numerator and denominator use EPSG:5070. Truth is clipped
before projection/union; the full forecast is projected without domain clipping.
Polygon holes and multipart truth are handled by Shapely intersection/union.

AREA 2 and LINE 1 select the same 40% truth, but LINE uses its buffered polygon.
That is an interim LINE policy, not evidence that linear coverage is areal.

## 5. Miss denominator and domain behavior

For each exploded retained Sparse truth component `S_i`, production computes

```text
captured_i = A(S_i ∩ U(F_j)) / A(S_i)
missed_i   = captured_i < 0.20,
```

where the forecast union contains every full forecast polygon regardless of
coverage class. The miss denominator is already domain-limited because
`S_i ⊆ D`. Forecasts are not clipped before union.

For in-domain truth, pre-clipping the forecast union is set-theoretically
equivalent:

```text
S_i ∩ U(F_j) = S_i ∩ (U(F_j) ∩ D), because S_i ⊆ D.
```

Thus geometry strictly outside `D` cannot suppress an in-domain miss; only the
portion intersecting `D` can. Exact arithmetic gives identical miss fractions.
Separate clipping operations could introduce tiny floating/topology differences,
so an implementation change would still require regression tests, but no miss
policy change is mathematically required merely to change a forecast denominator.

## 6. Domain-related path inventory

| Class | Path | Current role |
|---|---|---|
| Observational preprocessing | `verification_domain` | Reads, unions, repairs, and caches ARTCC + CMAC geometry. |
| Observational preprocessing | `extract_tcf_polygons` | Clips truth before physical minimum-area filtering. |
| Forecast scoring | `run_verification` | Selects clipped truth, projects full forecast, divides hit area by full area. |
| Miss scoring | `run_verification` | Divides clipped Sparse truth captured by full forecast union by clipped truth area. |
| Display | `app.py` geography | Draws states/ARTCCs; does not define the scoring denominator. |
| Reporting | review/report builders | Carry category/fraction results; do not recalculate domain geometry. |
| Tests | `baseline/test_fixture.py` | Proves out-of-domain truth removal and clip-before-filter order. |
| Baseline replay | `run_verification_legacy_independent_max` | Uses the same current domain path with a legacy truth seed. |
| Analysis | Solid LINE and this utility | Read-only reconstruction; no production policy. |

## 7. Mathematical asymmetry

Write `F = Fi ∪ Fo`, where `Fi = F ∩ D` and `Fo = F \ D`. Because retained truth
`Td` is a subset of `D`, `A(Fo ∩ Td)=0`. Candidate A nevertheless includes
`A(Fo)` in its denominator. This creates a location-dependent upper bound:

```text
score_A ≤ A(Fi) / A(F).
```

Even perfect truth coverage throughout the eligible portion cannot exceed the
forecast's in-domain fraction. This is a mathematical consequence, not by itself
proof that Candidate A is wrong: it can express a deliberate accountability
policy for everything issued.

For nonzero `A(Fi)`:

```text
score_B = A(F ∩ Td) / A(Fi) = score_A / in_domain_fraction.
```

Therefore `score_B ≥ score_A`; equality holds when the forecast is wholly inside
or when the numerator is zero. A denominator change cannot lower a score.

## 8. Synthetic controlled cases

The analysis utility uses a 10×10 Cartesian domain and perfect truth wherever a
forecast is eligible. Coordinates are arbitrary equal-area units.

| Case | Candidate A | Candidate B | Policy issue |
|---|---:|---:|---|
| Fully inside | 100% | 100% | Identical. |
| Fully outside | 0% | undefined | Overforecast versus unscorable. |
| 50% inside | 50% | 100% | Load-bearing denominator distinction. |
| 90% inside | 90% | 100% | Modest boundary penalty under A. |
| 10% inside | 10% | 100% | Potentially pathological favorable score under B. |

Additional consequences:

* If a forecast is fully inside and original truth crosses the boundary, clipping
  removes only truth outside `D`; neither denominator changes.
* Irregular boundaries, holes, MultiPolygons, and slivers do not alter the set
  definitions, but they can make `A(Fi)` tiny and numerically fragile.
* A zero-area `Fi` makes Candidate B undefined, not zero. Treating it as zero
  silently chooses Candidate A's accountability outcome.
* For the existing buffered LINE, the same area equations apply. A future linear
  method must separately choose full issued length versus eligible in-domain
  length; this report does not resolve Solid LINE methodology.

## 9. Frozen historical audit

### 9.1 Method

`analysis/domain_denominator_frozen_cases.py` parses all six committed TCF
products, runs the named legacy replay against each maxima-only artifact, projects
forecast/domain geometry to EPSG:5070, and records both denominators for every
source feature. It preserves the current LINE buffer. The committed CSV contains
all 48 records; the JSON contains aggregate and synthetic results.

The Candidate B numerator is unchanged and its score is computed as
`score_A / in_domain_fraction`. Values are capped to `[0,1]` only against
floating-point overshoot. A zero eligible area is labeled `Unscorable` for
analysis rather than assigned a production category.

### 9.2 Aggregate results

| Metric | All | AREA | LINE |
|---|---:|---:|---:|
| Features | 48 | 47 | 1 |
| Fully inside | 41 | 40 | 1 |
| Partially outside | 7 | 7 | 0 |
| Fully outside | 0 | 0 | 0 |

The median in-domain fraction is 100%; the minimum is 85.3665%. Counts exceeding
out-of-domain thresholds are: >1%: 3, >5%: 2, >10%: 1, >25%: 0, >50%: 0.

### 9.3 Boundary-sensitive records

| Event / source feature | Type/code | In domain | Current | Candidate B | Category change | Bounds (lon/lat) |
|---|---|---:|---:|---:|---|---|
| 20260524_19Z_F04 / 3 | AREA/3 | 85.3665% | 0% | 0% | none | -95.4,27.1 to -94.5,28.4 |
| 20260524_19Z_F06 / 5 | AREA/3 | 92.2280% | 0% | 0% | none | -90.0,28.2 to -88.3,28.9 |
| 20260524_19Z_F04 / 6 | AREA/3 | 96.4602% | 0% | 0% | none | -91.7,28.2 to -90.5,29.3 |
| 20260403_21Z_F04 / 6 | AREA/3 | 99.0487% | 0% | 0% | none | -103.1,29.7 to -100.0,32.1 |
| 20260728_19Z_F04 / 4 | AREA/3 | 99.9822% | 19.9274% | 19.9310% | none | -112.6,33.4 to -101.0,43.1 |
| 20260728_19Z_F04 / 1 | AREA/3 | 99.9955% | 0% | 0% | none | -110.8,39.3 to -108.8,41.4 |
| 20260728_19Z_F04 / 10 | AREA/3 | 99.9996% | 31.8317% | 31.8318% | none | -79.1,37.6 to -70.8,48.0 |

The largest nonzero change is feature 4 on 20260728: +0.0000354 fraction
(+0.00354 percentage points). It remains just below 20% under both denominators.
No feature crosses 20% or 50%, and no FAA-facing category changes. This sample
therefore demonstrates boundary geometry but supplies almost no empirical
leverage on scoring policy.

## 10. Candidate A — full issued denominator

**Definition:** `A(F ∩ Td) / A(F)`.

Strengths:

* directly assesses the complete issued geometry;
* cannot yield 100% from a tiny in-domain fragment of a much larger forecast;
* is defined as zero when a nonempty forecast is wholly outside `D`;
* matches current production behavior and is simple to reproduce.

Concerns:

* unverifiable geometry can only reduce the score;
* the verifier's chosen/approximate boundary acts as a false-alarm penalty;
* a perfectly observed in-domain portion is capped by its eligible fraction;
* location near an artificial boundary can affect category independent of
  forecast-observation displacement.

Candidate A is defensible only if the policy explicitly says that forecasts are
accountable outside this verifier's observation support or that such geometry is
itself operationally invalid. The repository does not establish either premise.

## 11. Candidate B — in-domain denominator

**Definition:** `A((F ∩ D) ∩ Td) / A(F ∩ D)` for nonzero eligible area.

Strengths:

* numerator and denominator share observational support;
* removes an implicit penalty created by unavailable/discarded truth;
* has a direct interpretation: fraction of eligible forecast area observed;
* is consistent across AREA and the current interim buffered LINE geometry.

Concerns:

* a tiny eligible sliver can score 100%; wholly outside forecasts are undefined;
* it ignores potentially valid or invalid assertions outside `D` alike;
* results inherit every approximation in the domain boundary;
* it needs a declared denominator-zero and minimum-eligibility policy.

## 12. Candidate C — eligibility/reviewer handling

Candidate C can pair Candidate B's eligible denominator with transparent
diagnostics or a scorable-eligibility rule. Possible diagnostics include full and
in-domain areas, in/out percentages, denominator policy, and a boundary flag.
Possible policies include review or unscorable status when too little geometry is
eligible. No percentage threshold is supported by these six events and none is
selected here.

This approach addresses pathologies but adds an operational judgment: how much
of an issued forecast is enough to grade objectively? It would also require
clear FAA/report handling. Those are future decisions, not analysis outputs.

## 13. Forecast responsibility versus verification eligibility

Two principles conflict:

1. **Forecast responsibility:** assess everything asserted in the issued
   product. This favors A, particularly if `D` is the actual product boundary.
2. **Verification eligibility:** objectively assess only where the system supplies
   accepted truth. This favors B, particularly if valid TCF geometry can lie
   outside an approximate or data-limited `D`.

The current system combines eligibility for truth with responsibility for the
forecast. That is internally asymmetric but not logically contradictory if it
is explicit policy. It is presently implicit, and the repository's CMAC boundary
warning makes treating it as an authoritative accountability boundary difficult
to justify from repository evidence alone.

## 14. Product domain, MRMS availability, and policy domain

Repository evidence establishes that `D` was added to prevent out-of-domain
truth from producing misses and that part of it was traced from a graphic. The
code does not derive `D` from MRMS grid coverage, per-event data availability, or
an official product-domain service. MRMS arrays may contain samples outside `D`,
but truth there is intentionally discarded. Consequently “outside `D`” means
“outside selected scoring support,” not necessarily “unobserved,” “invalid TCF,”
or “outside MRMS.”

An authoritative comparison is still needed to answer whether valid issued TCF
features may extend outside this verifier's domain and whether the two domains
are intended to coincide.

## 15. Coupled methodology questions

### Solid LINE

The current LINE corridor uses Candidate A's area denominator. Any future linear
method has the analogous full-line versus in-domain-line denominator question.
No inference about distance tolerance, truth field, continuity, or LINE grading
threshold follows from this domain analysis.

### Misses

Changing only the forecast denominator need not alter miss suppression because
clipping forecasts before intersection with `S_i ⊆ D` is mathematically
equivalent. Whether all forecast classes should suppress misses remains a
separate open question.

### Minimum-area order

Truth is currently clipped before applying 15,000 km². A boundary-crossing truth
object can disappear after its eligible portion falls below the floor. That
changes the numerator available to either denominator and couples boundary
results to the unresolved minimum-area-order policy; this analysis does not
reorder it.

### Domain approximation

A denominator policy cannot cure uncertainty in `D`. Replacing the CMAC trace
with an authoritative boundary could change eligible fractions under B and
truth itself under both A and B.

## 16. Decision matrix

| Criterion | A: full issued | B: in-domain | C: eligibility/review |
|---|---|---|---|
| Mathematical support symmetry | No | Yes | Yes when scored |
| Full forecast accountability | Strong | Limited to eligible part | Explicitly surfaced |
| Boundary-location bias | Can penalize | Removes denominator penalty | Flags rather than hides |
| Mostly-outside pathology | Low score | Tiny part can score highly | Can defer/qualify score |
| Wholly-outside behavior | Zero | Undefined | Explicitly handled |
| Interpretability | Whole forecast truth-filled | Eligible area truth-filled | Most transparent, more policy |
| Implementation complexity | Existing | Low | Moderate |
| Reproducibility | High | High given authoritative `D` | Requires explicit rule |
| AREA compatibility | Existing | Natural | Natural |
| Future LINE compatibility | Leaves length issue | Natural eligible-length analogue | Can handle low eligibility |
| Evidence still needed | Accountability mandate | Product/domain relation | Eligibility threshold/workflow |

## 17. Recommendation and status

**Option C — preferred policy identified but requiring additional evidence or an
explicit operational decision.**

The working hypothesis is to use in-domain geometry as the objective denominator
because accepted truth is restricted to that support, while reporting eligibility
and treating zero/tiny eligible geometry explicitly. This is not approved policy.
Before approval, obtain:

1. an authoritative TCF/product-domain statement and boundary if available;
2. an operational decision on accountability outside verifier support;
3. a broader set of boundary-crossing forecasts, especially cases with observed
   overlap and cases mostly/fully outside;
4. a rule for zero or small eligible denominators, including reviewer/report
   handling; and
5. synthetic tests for holes, MultiPolygons, slivers, boundary contact, and
   future line-length denominators.

## 18. Open questions

1. Is the official product domain identical to, broader than, or narrower than
   this committed approximation?
2. Does `D` represent policy eligibility or merely a temporary data boundary?
3. What minimum eligible fraction, if any, makes a forecast objectively scorable?
4. Should both whole-issued and eligible fractions be visible to reviewers?
5. How should a zero eligible denominator be reported?
6. For future Solid LINE scoring, is eligible length defined before or after
   domain clipping?
7. Should minimum-area filtering be evaluated before or after domain clipping?

Decision 1A, feature-aware coverage semantics, Decision 1B status, Solid LINE
status, miss policy, and every production threshold remain unchanged.

## 19. Reproduction

```bash
python analysis/domain_denominator_frozen_cases.py \
  --csv analysis/domain_denominator_feature_audit.csv \
  --summary analysis/domain_denominator_summary.json
```

The script reads committed inputs only and does not write baselines. Generated
CSV/JSON values are lightweight and intentionally label their legacy truth source.
