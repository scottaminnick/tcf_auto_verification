# Automated missed-event methodology analysis

> **Current Methodology 1.0 RC1 decision:** this algorithm produces **Candidate
> Misses** only; the 15,000 km² floor is removed, while `<20%` remains a
> provisional capture parameter. The historical floor sensitivity below is
> retained as evidence. Candidate FAA inclusion still requires explicit approval,
> and hidden poorly captured Medium components are non-reportable review flags.

## 1. Executive summary

This is a read-only analysis. It changes neither production behavior nor any
threshold, floor, forecast geometry, truth field, report, or baseline.

Production currently defines an automated miss as follows. Let `O` be one
retained, domain-clipped **Sparse** truth component after the 15,000 km² floor,
and let `F` be the union of every forecast verification geometry—Sparse AREA,
Medium AREA, and the buffered Solid LINE. In EPSG:5070:

```text
capture(O) = A(O ∩ F) / A(O)
Missed iff capture(O) < 0.20
```

Medium truth cannot independently create a miss. Forecast coverage class does
not affect suppression: any included forecast geometry contributes identically
to the union. Overlaps are unioned, so duplicate forecast area is not counted
twice.

The six frozen maxima-only events contain seven current misses. Six have exactly
zero capture; one has 7.77% capture from a Sparse AREA. None receives capture
from Medium AREA or Solid LINE. Two retained non-misses lie near the decision at
22.43% and 27.10% capture.

The 20% magnitude is an inherited heuristic introduced with the original miss
feature in commit `6a01776`; no rationale was found. A sensitivity sweep shows
the frozen result would still be seven misses at 10% because no eligible
component lies between 10% and 20%. It becomes 9 at 30%, 12 at 50%, 20 at 75%,
and 26 at 100%. Thus the frozen count cannot validate 20% over 10%.

The two-dimensional area/capture matrix demonstrates parameter compensation:
9 misses arise from 10,000 km²/20%, 15,000 km²/30–40%, or 25,000 km²/75%; 43
misses arise from 0 km²/20% or 5,000 km²/100%. Historical miss count cannot
independently validate either inherited parameter.

**Recommendation: Option C.** The preferred conceptual direction is to treat
automation as a reproducible **candidate-miss detector** based on observed-area
capture, with explicit event eligibility, coverage-class semantics, parameter
provenance, and meteorologist approval. Evidence is insufficient to approve the
current 20%, a replacement value, hierarchical Sparse/Medium logic, or a
coverage-aware algorithm.

## 2. Evidence and limitations

Evidence is separated into implementation fact, repository history, exact
synthetic geometry, historical sensitivity, and policy inference.

Frozen arrays lack the paired mask required by Decision 1A. Historical results
are explicitly legacy independent-max replay and are not presented as current
pair-first truth. Synthetic cases are independent of that limitation.

An authoritative search restricted to FAA/NWS/AWC sources was attempted for a
formal TCF Miss definition, capture threshold, and coverage-aware verification.
The configured search service returned HTTP 401. No official requirement is
claimed, and generic object-verification practice is not imported as TCF policy.

## 3. Complete implementation trace

1. Production pair-first qualification seeds the observational field.
2. Dilation and smoothing create the neighborhood-coverage field.
3. The 25% threshold creates Sparse truth; 40% separately creates Medium truth.
4. Both are cell-footprint polygonized, clipped to the ARTCC-plus-CMAC domain,
   projected to EPSG:5070, and filtered at 15,000 km².
5. Only retained Sparse geometry is exploded into candidate miss components.
6. Every parsed forecast geometry is unioned without filtering by forecast
   category or coverage semantics. AREA polygons use their issued geometry; the
   LINE uses the existing 0.15° buffered polygon.
7. Sparse component and forecast union are projected to EPSG:5070.
8. Capture is intersection area divided by observed component area.
9. Zero-area geometry would use capture zero, although empty truth is removed
   upstream.
10. Strict `< 0.20` produces a red `Missed` geometry; exactly 20% is not missed.
11. Misses are ordered east-to-west and labeled `M1`, `M2`, etc.
12. `build_review_table()` stores no capture fraction, truth class, area, or top
    for a miss. `build_report()` emits only `ARTCC - Missed (Area M#)`.
13. Streamlit fills the miss polygon red and uses hover text `Missed Area M#`.
    It displays the generated report but currently exposes no data-editor review
    step, despite the review-table seam in the pipeline.

The visual/report wording presents a categorical result without the automated
capture evidence or area-floor sensitivity that produced it.

## 4. Current mathematical definition

For retained Sparse components `O_i`, all forecast geometries `F_j`, and physical
area `A`:

```text
F = union_j(F_j)
c_i = A(O_i ∩ F) / A(O_i)
miss_i = (c_i < 0.20)
```

`c_i` is dimensionless and bounded from 0 to 1. Unioning ensures overlapping
forecasts cannot inflate it above 1. Forecast category—Well, Close, or
Overforecasted—does not affect membership in `F`.

Since every `O_i ⊆ D` after clipping:

```text
O_i ∩ F = O_i ∩ (F ∩ D).
```

Forecast geometry strictly outside the verification domain cannot affect miss
capture. Clipping the forecast union to `D` would be set-theoretically equivalent
for this calculation, apart from possible floating overlay noise.

## 5. Truth and forecast-class table

| Candidate observed truth | Forecast class | Suppresses current miss? | Geometry used |
|---|---|---|---|
| Retained Sparse 25% | Sparse AREA 3 | Yes | Issued AREA polygon |
| Retained Sparse 25% | Medium AREA 2 | Yes | Issued AREA polygon |
| Retained Sparse 25% | Solid LINE 1 | Yes | Existing 0.15° buffered corridor |
| Retained Medium 40% | Any | No independent miss exists | Medium is ignored for miss creation |

Consequences:

* a Sparse forecast can suppress a miss containing a Medium core;
* a Medium forecast can suppress a broad Sparse-envelope miss even if it covers
  only a limited core, provided union capture reaches 20%;
* a Solid LINE corridor can suppress an AREA-derived miss;
* several forecast classes can collectively cross 20%; and
* an Overforecasted forecast polygon still participates in `F`.

These are implementation facts. Their scientific appropriateness is unresolved.

## 6. Capture-threshold origin

The first miss implementation found is commit `6a01776` (2026-05-24,
“Improve app.py with new features and optimizations”). It introduced the Sparse
truth loop, area-capture equation, strict 0.20 comparison, red polygon, map label,
and report line together. The commit contains no miss-specific rationale or
source.

The value remained 0.20 through extraction into `tcf_pipeline.py`. It was later
named `miss_capture_threshold` and deliberately separated from the numerically
equal forecast `verified_close_cutoff`, because they answer inverse questions.
Tests prove the parameter is consumed and physical area is used; they do not
provide operational authority.

Classification: **inherited heuristic / origin unresolved**. No earlier value,
notebook explanation, or authoritative requirement was found.

## 7. Minimum-area coupling

The 15,000 km² floor is an implicit miss-event eligibility rule. An uncovered
14,999 km² Sparse object cannot be a miss because it is deleted; an otherwise
identical 15,001 km² object can immediately become one. The preceding sweep found
43/18/9/7/2/0/0 misses at floors 0/5k/10k/15k/20k/25k/30k with capture fixed at
20%.

The current system therefore uses one unsupported floor to decide both whether
an object may affect forecast verification and whether it may generate an FAA
Missed line. Those need not be the same policy question.

Current post-domain filtering and its Option C status remain unchanged. None of
the seven current misses is a domain-crossing component; current miss eligibility
in this sample is not driven by the processing-order ambiguity.

## 8. Seven current frozen misses

| Event / component | Area km² | Capture | Class contribution | Nearest forecast | Report label |
|---|---:|---:|---|---|---|
| 20260403 / S14 | 15,511 | 0% | none | Sparse AREA, 364 km | ZID/ZOB - Missed (Area M1) |
| 20260524_13Z / S5 | 19,102 | 0% | none | Sparse AREA, 47.5 km | ZID/ZME/ZTL - Missed (Area M3) |
| 20260524_13Z / S13 | 24,830 | 7.77% | Sparse AREA only | Sparse AREA intersects | ZJX - Missed (Area M2) |
| 20260524_13Z / S15 | 16,381 | 0% | none | Sparse AREA, 24.0 km | ZDC - Missed (Area M1) |
| 20260524_19Z_F04 / S9 | 19,159 | 0% | none | Sparse AREA, 559 km | ZMP - Missed (Area M1) |
| 20260524_19Z_F06 / S5 | 18,044 | 0% | none | Sparse AREA, 19.7 km | ZMP - Missed (Area M2) |
| 20260524_19Z_F06 / S6 | 23,112 | 0% | none | Sparse AREA, 102 km | ZJX - Missed (Area M1) |

All are retained Sparse objects. Medium provenance is diagnostic only; no current
miss has Medium AREA or LINE capture. Two retained components are near misses:
19,300 km² at 22.43% and 22,592 km² at 27.10%.

Thirty-five below-floor nonempty Sparse components have capture below 20% and
would be misses if the current floor were removed without any other change. This
explains why the floor functions as a strong miss suppressor.

## 9. Forecast-class capture accounting

The component CSV provides:

* total union capture, used for status;
* capture against each class union individually; and
* marginal unique capture after removing each class from the full union.

Individual class fractions can sum above total where classes overlap; they are
diagnostic and are not substituted for the no-double-counting union. Marginal
fractions identify capture that would vanish if that class were excluded.

The sole frozen Solid LINE overlaps two Sparse components, with maximum individual
capture 5.84%, but has zero unique contribution because other forecast geometry
covers the same area. It overlaps no current miss and changes no current miss
status. This single case cannot validate the policy.

## 10. Capture-threshold sensitivity at 15,000 km²

| Capture boundary | Total misses | Event counts in configured nonempty-event order |
|---:|---:|---|
| 0% | 0 | all zero because production uses strict `<` |
| 10% | 7 | 1, 3, 1, 2, 0 |
| **20%** | **7** | **1, 3, 1, 2, 0** |
| 30% | 9 | 1, 3, 2, 3, 0 |
| 40% | 9 | 1, 3, 2, 3, 0 |
| 50% | 12 | 1, 4, 4, 3, 0 |
| 75% | 20 | 3, 4, 6, 6, 1 |
| 100% | 26 | 4, 4, 7, 6, 5 |

No eligible component lies from 10% through just under 20%, so 10% and 20%
produce exactly the same historical misses. Similarly 30% and 40% agree. Count
stability over an interval is absence of sample leverage, not support for either
endpoint.

At 0%, an entirely unforecast component is not missed because `0 < 0` is false.
That mathematically correct consequence makes a zero capture threshold an
implausible “any unforecast event” policy unless comparison semantics also change;
no such change is proposed.

## 11. Two-dimensional parameter sensitivity

Total misses for area floor / capture boundary:

| Floor km² | 0% | 10% | 20% | 30% | 40% | 50% | 75% | 100% |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 42 | 43 | 46 | 48 | 51 | 61 | 72 |
| 5,000 | 0 | 17 | 18 | 21 | 22 | 25 | 34 | 43 |
| 10,000 | 0 | 8 | 9 | 12 | 12 | 15 | 23 | 29 |
| 15,000 | 0 | 7 | 7 | 9 | 9 | 12 | 20 | 26 |
| 20,000 | 0 | 2 | 2 | 3 | 3 | 5 | 13 | 19 |
| 25,000 | 0 | 0 | 0 | 0 | 0 | 1 | 9 | 13 |

The matrix uses production-style final union/explode behavior, so the 20% column
reproduces the prior area-floor miss counts exactly.

Parameter compensation is direct:

* 9 misses result from 10,000/20%, 15,000/30%, or 15,000/40%;
* 12 result from 10,000/30–40% or 15,000/50%;
* 43 result from 0/20% or 5,000/100%.

The same count can encode very different event eligibility and capture meaning.
Historical count alone cannot validate either parameter.

## 12. Exact synthetic cases

With an observed square as `O`:

1. no forecast: capture 0%, currently Missed if area-eligible;
2. complete forecast: capture 100%, not Missed;
3. exact half: capture 50%, not Missed;
4. small centered forecast: capture 4%, Missed;
5. large forecast barely clipping one edge: capture 10%, Missed;
6. two overlapping forecasts with individual fractions summing to 120% have
   union capture exactly 100%, proving no double count;
7. Sparse forecast covering a Medium core and 25% of broad Sparse truth prevents
   a current miss regardless of forecast class;
8. Medium forecast covering the entire core but only 15% of broad Sparse truth
   still yields a current Sparse miss;
9. the production 0.15° LINE corridor crossing a geographic AREA captures about
   75.0% in the constructed case and suppresses its miss; and
10. uncovered 14,999/15,001 km² objects are respectively ineligible/Missed.

Cases 4 and 5 show that identical capture fractions can arise from very different
forecast extent and intent. Current miss scoring considers only observed-area
capture, not forecast precision, object matching, orientation, or forecast class.

## 13. What “Missed” currently means

Current behavior most closely matches **B: areal capture of an eligible processed
Sparse object**. It is more than any-intersection detection, because up to 19.999%
capture remains Missed. It is not hierarchical event detection, because Medium
cores have no independent status. It is not formally a reviewer-candidate state,
because map/report output states “Missed” without qualifications.

The broader application philosophy is human-in-the-loop, and `build_review_table`
creates a potential editing seam. However, the current Streamlit result does not
display capture evidence or provide an editor. Automated geometry therefore
communicates stronger certainty than the unresolved inputs warrant.

## 14. Sparse/Medium hierarchy and duplicate events

Evaluating Medium independently could reveal an entirely missed Medium core
inside a partly captured Sparse envelope. But naïvely evaluating both would allow
one physical system to generate parent Sparse and child Medium misses. A future
hierarchical approach would require parent/child association and one explicit
reporting rule, adding complexity and parameters.

Coverage-aware suppression likewise requires policy:

* any strategically relevant forecast depiction might be enough to say the
  event was not wholly missed; or
* forecast and observed coverage classes might need compatibility.

Neither principle is established. Current all-class union is simple and monotonic,
but semantically accidental: bare geometry, not feature meaning, controls status.

## 15. Candidate directions

### A. Current observed-area capture

Strengths: simple, monotonic, no double counting, physical interpretation, and
reproducibility. Weaknesses: unsupported 20%, unsupported event floor,
Sparse-only truth, class-blind suppression, and categorical report certainty.

### B. Any meaningful intersection

More detection-oriented, but “meaningful” still needs area, distance, duration,
or object criteria. Pure nonempty intersection is numerically fragile.

### C. Coverage-aware capture

Makes semantics explicit but needs a compatibility matrix and evidence for how
Sparse, Medium, and LINE should interact. It can create counterintuitive status
changes when a forecast label changes but geometry does not.

### D. Hierarchical Sparse/Medium event logic

Represents core/envelope structure but requires association and de-duplication.
It risks double misses and increased review burden.

### E. Candidate-miss reviewer workflow

Preserves objective geometry as evidence while requiring a meteorologist to
approve FAA-facing “Missed.” It can surface capture, area, boundary, and
parameter sensitivity without pretending those unresolved choices are final.
It still requires a reproducible candidate detector.

## 16. Desirable invariants

Any future method should likely ensure:

1. adding forecast coverage cannot increase miss likelihood;
2. duplicate overlap cannot inflate capture above 100%;
3. unrelated distant forecasts cannot suppress a miss;
4. ordering and equivalent geometry splitting do not matter;
5. outside-domain geometry cannot affect in-domain truth;
6. coverage-class behavior is explicit;
7. Sparse/Medium parent-child reporting avoids duplicates;
8. event eligibility and capture parameters have interpretable meanings; and
9. classification changes near boundaries are visible to reviewers.

Current union capture satisfies the first five; it does not resolve the latter
four.

## 17. Decision matrix

| Direction | Semantic clarity | Class handling | Parameter burden | Duplicate risk | Reviewer fit | Reproducibility |
|---|---|---|---|---|---|---|
| Current capture | Moderate | Class-blind | Area + capture | Low | Weakly surfaced | High |
| Meaningful intersection | Low until defined | Potentially class-blind | Meaningful threshold | Low | Moderate | Depends |
| Coverage-aware | High if specified | Explicit | Compatibility + capture | Moderate | Moderate | High once fixed |
| Hierarchical | Potentially high | Parent/core | Association + thresholds | High unless solved | High | Moderate |
| Candidate-review workflow | Explicit uncertainty | Can expose all | Candidate + review rules | Review can reconcile | Highest | Requires audit trail |

## 18. Recommendation and evidence needed

**Option C — preferred conceptual direction, parameters and policy unresolved.**

Retain observed-area union capture as a useful candidate signal, but treat the
long-term automated output conceptually as a candidate miss requiring transparent
evidence and meteorologist approval. This does not endorse 20%, 15,000 km²,
Sparse-only eligibility, or class-blind suppression.

Before approving a permanent policy, obtain:

1. authoritative or documented expert meaning of a TCF miss;
2. a Decision 1A-capable multi-season archive of observed events and forecasts;
3. meteorological review of current misses, near misses, and below-floor objects;
4. explicit separation of event eligibility from capture sufficiency;
5. a coverage-class and Solid LINE interaction policy;
6. a parent/child rule if Medium cores receive independent significance;
7. sensitivity evidence around any proposed area/capture values; and
8. an auditable reviewer workflow showing automated evidence and final approval.

## 19. Reproduction and scope preservation

```bash
python analysis/missed_event_frozen_cases.py \
  --components analysis/missed_event_components.csv \
  --sensitivity analysis/missed_event_sensitivity.csv \
  --summary analysis/missed_event_summary.json
```

The utility reads committed inputs and writes lightweight analysis artifacts.
Production miss logic, 20%, 15,000 km², post-domain order, forecast scoring,
coverage semantics, LINE behavior, Decision 1A/1B, report output, and frozen
expected files remain unchanged.
