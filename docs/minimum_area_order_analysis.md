# Minimum-area processing-order analysis

> **Superseded historical analysis:** no minimum-area filter now applies to
> forecast scoring, Candidate Misses, or Medium-core flags. The post-domain order
> analysis below characterizes the retired 15,000 km² heuristic only.

## 1. Executive summary

This is a read-only analysis of the existing 15,000 km² observational truth
floor. It does not change the threshold, processing order, domain, truth,
forecast scoring, misses, LINE handling, or historical baselines.

Production currently applies the minimum **after** clipping each processed truth
component to the verification domain. For complete component `O`, domain `D`,
physical area `A`, and `M = 15,000 km²`:

```text
Candidate A (current): retain O ∩ D when A(O ∩ D) >= M
Candidate B:           retain O ∩ D when A(O) >= M
```

The six-event frozen audit reconstructed 87 Sparse and 61 Medium pre-domain
components using the explicitly named legacy independent-max path. Sparse had 68
inside, 6 crossing, and 13 outside components; Medium had 51 inside, 2 crossing,
and 8 outside. **No component changed retention**, so forecast scores, categories,
truth area, and seven automated misses were identical. This sample establishes
current behavior but provides no empirical discrimination between the candidates.

Synthetic cases prove that Candidate B can retain an arbitrarily small in-domain
sliver from a large out-of-domain parent, while Candidate A guarantees at least
15,000 km² of eligible geometry at filtering time. Conversely, Candidate A can
discard the in-domain portion of a physically large parent solely because the
selected domain truncates it.

Repository history says 15,000 km² was introduced in commit `432dd9f` to “match
the notebook,” temporarily reverted to 10,000 km² in `cff71fa`, and restored in
`487a169`. No committed scientific or official rationale was found. Thus “where
is the threshold applied?” is subordinate to the deeper unresolved question
“what does 15,000 km² represent, and should it remain the threshold?”

**Recommendation status: Option C.** Post-clip filtering is the working direction
because it is internally consistent with an in-domain verification-eligibility
interpretation and prevents boundary slivers from becoming scored truth. It is
not approved: the domain is approximate, the floor measures a processed
smoothed envelope rather than raw convection, and neither repository nor
available official evidence establishes the threshold's scientific meaning.

## 2. Evidence and limitations

Evidence is labeled as follows:

* **Implementation fact:** traced from executable code.
* **Repository evidence:** committed history, comments, tests, and documents.
* **Mathematical consequence:** follows from the candidate definitions.
* **Analysis inference:** a policy interpretation, not an approved rule.

The frozen arrays contain temporal maximum reflectivity and tops, not Decision
1A paired masks. All historical truth/score/miss results here are explicitly
legacy independent-max reconstructions. The geometry-order comparison remains
valid for that fixed seed, but it is not a Decision 1A historical validation.

An official-source search was attempted through the available search service,
restricted to AWC/NWS/FAA domains, but the service returned HTTP 401. No official
minimum-area requirement was located or inferred. Absence from the repository is
not proof that no external operational rationale exists.

## 3. Threshold trace and origin

### 3.1 Current definition and consumers

At the time of this historical analysis, `GradingParams.min_area_m2` was
`15_000_000_000` square metres: 15,000 km². The
same value is passed to `extract_tcf_polygons()` for independently constructed
Sparse 25% and Medium 40% truth. Solid LINE indirectly depends on the Medium
filter because its interim buffered-area score uses retained Medium truth. AREA
2 uses Medium; AREA 3 uses Sparse. Misses use retained Sparse components.

No other production path applies a separate truth-area floor. The parameter is
documented in the baseline README, methodology specification, technical review,
fixture tests, and independent methodology tests. Tests establish physical
EPSG:5070 measurement and behavior immediately above/below the configured floor;
they deliberately do not approve the clipping order.

### 3.2 Repository history

The first found 15,000 km² literal is commit `432dd9f` (2026-05-26,
“Refactor app.py for clarity and modern API usage”). Its code comment says the
change from 10,000 to 15,000 km² was made “to match the notebook” and reproduce
the notebook's set of truth blobs. Commit `cff71fa` then changed it back to
10,000 km²; `487a169` restored 15,000 km². Later extraction/configuration commits
preserved the value.

This is evidence of an **inherited implementation assumption calibrated to a
notebook**, not a documented meteorological requirement. The current repository
does not explain why the notebook used 15,000 km², whether it represented an
issued-product minimum, an observed-event minimum, or a pragmatic noise filter.

## 4. Exact current processing order

Both truth classes follow the same stages, differing only at the coverage-field
threshold:

1. Start with the pair-first Boolean temporal qualifying mask in production.
2. Apply one binary-dilation iteration.
3. Apply the 20×20 uniform filter.
4. Threshold at 0.25 for Sparse or 0.40 for Medium.
5. `_mask_cell_union()` converts true raster cells to complete cell footprints,
   unions edge-connected runs, preserves holes, and returns connected Polygon or
   MultiPolygon components.
6. `extract_tcf_polygons()` puts each pre-domain component in a separate row.
7. Each row is intersected with `D` and repaired by `buffer(0)`. A parent can
   become a MultiPolygon, but all its clipped pieces remain one row.
8. Each clipped row is projected to EPSG:5070.
9. The **combined clipped area of that parent row** is compared with M.
10. Retained rows are finally unioned for scoring.

Filtering occurs before the final union. Two disconnected sub-threshold
components do not collectively clear M unless dilation/smoothing connected their
raster masks before polygonization. If clipping splits one parent into two
pieces, current production evaluates their combined area, not each fragment
separately.

The 15,000 km² quantity is therefore the area of a **dilated, smoothed,
coverage-thresholded cell-footprint envelope**, not raw ≥40 dBZ/FL250 convective
core area and not necessarily a meteorological storm-object area.

## 5. Candidate definitions and feature identity

Let `O_i` be one connected component produced before domain clipping.

### Candidate A — post-clip filter (current)

```text
C_i = O_i ∩ D
retain_A(i) iff A(C_i) >= M
output_A(i) = C_i
```

If clipping splits `C_i`, the implementable current interpretation measures all
pieces together because they remain associated with the same pre-domain row.

### Candidate B — pre-clip parent filter

```text
retain_B(i) iff A(O_i) >= M
output_B(i) = O_i ∩ D, if nonempty
```

This preserves parent identity through clipping and likewise retains all clipped
pieces together. A wholly outside qualifying parent produces empty output.

### Alternative per-piece interpretation

A third implementation could filter each connected clipped fragment separately.
That is neither current A nor the analyzed parent-based B. It would discard split
fragments whose combined area clears M and would make the result depend on domain
topology. No repository evidence supports it.

“Feature identity” here means connectivity after dilation, smoothing, and
thresholding. It is an algorithmic truth object, not independently established
storm identity. Domain clipping can split it, and final unioning can erase parent
labels; the analysis utility records identity before that loss.

## 6. Semantic question

### Interpretation 1 — in-domain event size

The verifier requires at least M of eligible processed truth inside `D`. This
naturally supports A. It guarantees every retained object had at least M of
scorable geometry when filtered and prevents a huge outside object from
introducing a tiny in-domain event.

### Interpretation 2 — physical parent-feature size

The complete processed observed feature must meet M; clipping only limits later
eligibility. This supports B. It avoids declaring a large physical feature
insignificant merely because an approximate policy boundary cuts it.

Neither is established. Moreover, calling B a physical-storm interpretation is
too strong because `O` has already been dilated, smoothed, and thresholded. The
semantic distinction is more accurately “complete processed parent envelope”
versus “eligible processed envelope.”

## 7. Synthetic analysis

The compact JSON records exact area inputs and Boolean outcomes:

| Case | Whole | In domain | A post-clip | B pre-clip |
|---|---:|---:|---|---|
| Inside large | 20,000 | 20,000 | retain | retain |
| Inside small | 10,000 | 10,000 | remove | remove |
| Boundary distinction | 20,000 | 12,000 | remove | retain 12,000 |
| Large-parent sliver | 100,000 | 2,000 | remove | retain 2,000 |
| Parent below floor | 14,000 | 13,000 | remove | remove |
| Split parent | 15,100 | 8,000 combined | remove | retain both pieces |
| Negligible loss | 20,000 | 19,999 | retain | retain |

### Remaining requested constructions

* **Two disconnected sub-threshold objects:** current polygonization returns two
  components; filtering occurs before final union, so their aggregate area does
  not count. B behaves the same unless each parent independently meets M.
* **Hole:** EPSG:5070 Shapely area subtracts interior rings under both candidates.
  Clipping may alter the ring, but no method fills it by definition.
* **MultiPolygon/domain pieces:** current A combines clipped pieces belonging to
  one pre-domain parent row. Parent B does likewise. Per-piece filtering is a
  distinct unapproved option.
* **Boundary touch:** a zero-area line/point intersection is cleaned by
  `buffer(0)` and cannot satisfy either area rule.

## 8. Mathematical properties and invariants

1. If `O ⊆ D`, then `A(O ∩ D)=A(O)` and A equals B.
2. The candidates differ exactly when `A(O) >= M` but `0 < A(O ∩ D) < M`.
3. Since intersection cannot increase area, A retention implies B retention.
   Thus B can only add nonempty truth relative to A; it cannot remove truth.
4. B can retain arbitrarily small positive in-domain area when a sufficiently
   large parent lies mostly outside.
5. A guarantees at least M of combined eligible parent geometry at filtering
   time, although later operations could change representation.
6. Separate components do not pool area unless upstream spatial processing has
   connected them.
7. Reordering components or reversing ring orientation must not change outcomes.
8. Holes must reduce both pre- and post-clip physical areas.

These are mathematical facts. Whether sliver exclusion or parent legitimacy is
the desired invariant is policy.

## 9. Verification-domain implications

The prior analysis established `D` as the EPSG:4326 union of 21 ARTCC polygons
and a hand-traced CMAC supplement. It has not been demonstrated to be the
authoritative product boundary or a hard MRMS availability boundary.

If M defines a complete processed meteorological feature, an approximate policy
boundary should arguably not change whether the parent meets M. If verification
explicitly requires a minimum eligible event within `D`, out-of-domain size is
irrelevant. The uncertainty in what both `D` and `M` represent prevents a purely
mathematical resolution.

## 10. Frozen historical audit

### 10.1 Method

`analysis/minimum_area_order_frozen_cases.py` reconstructs the legacy seed,
dilation, smoothing, Sparse/Medium thresholds, corrected cell-footprint
components, domain intersections, and EPSG:5070 areas for all six events. It
records every pre-domain component, both retention decisions, all 48 forecast
fractions/categories, miss objects, and nested-truth diagnostics.

### 10.2 Component summary

| Truth | Components | Inside | Crossing | Outside | Retention differences |
|---|---:|---:|---:|---:|---:|
| Sparse | 87 | 68 | 6 | 13 | 0 |
| Medium | 61 | 51 | 2 | 8 | 0 |

Current and candidate retained in-domain area are identical: 820,358.314 km²
for Sparse and 112,526.365 km² for Medium across all events. Added area is zero;
there is no candidate-only sliver in this sample.

The largest crossing retained Sparse component is 75,190.831 km² before clipping
and 75,190.088 km² inside on `20260728_19Z_F04`. The largest material truncation
is a 9,930.166 km² Sparse parent with 4,504.962 km² inside on
`20260524_13Z_F04`; it is below M both before and after. Consequently none of the
crossing parents occupies the candidate-disagreement interval.

### 10.3 Forecast impact

All 48 forecast rows have identical current/candidate fractions. The largest
absolute difference is zero; no 20% or 50% crossing and no category change
occurs. The sole Solid LINE retains its current buffer and Medium truth path.

### 10.4 Miss impact

Both methods produce seven legacy-replay misses: 0, 1, 3, 1, 2, and 0 in event
order. There are no added or removed misses and no responsible changed truth
component. Candidate B could add a miss in a different case by retaining a
candidate-only Sparse sliver; the frozen cases do not contain one.

## 11. Sparse/Medium nesting

Before filtering, the 40% mask is a subset of the 25% mask. Each Medium connected
component therefore lies inside some Sparse component. Its complete area and its
clipped area cannot exceed those of that containing Sparse parent. With the same
M applied to both:

* under A, retained Medium implies its containing clipped Sparse parent meets M;
* under B, retained Medium implies its containing complete Sparse parent meets M.

Therefore independent parent-component filtering should preserve
`Medium ⊆ Sparse` for both orders. The audit found no material violation. One
event produced only about 0.000005 km² (roughly 5 m²) of Medium difference outside
Sparse after projection/overlay, classified as floating topology noise rather
than a methodological nesting failure.

Synthetic nesting can be violated only by changing assumptions—for example,
using different minimums, per-fragment filtering with incompatible parent
identity, or numerical overlay defects. Current production does not intentionally
permit a retained Medium object without its containing Sparse truth.

## 12. Boundary-sliver and two-stage alternatives

Candidate B's load-bearing pathology is real: for any epsilon greater than zero,
a parent of area at least M can intersect `D` in epsilon area and survive. That
may be appropriate if the parent event's existence is the criterion, but it means
the verifier scores an object far below its otherwise stated eligible scale.

A two-stage Candidate C could require parent legitimacy before clipping and then
apply a separate eligibility condition or reviewer flag. If the second condition
is the same M, it collapses exactly to A. A smaller numeric floor introduces a
new unsupported threshold. A review-only rule avoids inventing a number but adds
workflow and reproducibility requirements. No such rule is implemented or
recommended numerically here.

Potential future reviewer diagnostics are `preclip_area_km2`,
`indomain_area_km2`, `indomain_fraction`, `boundary_truncated`, and
`minimum_area_sensitive`. They would make boundary judgments visible regardless
of final automation policy, but production UI/report changes are out of scope.

## 13. Interaction with the forecast denominator

| Forecast denominator | Observation filter | Boundary interpretation |
|---|---|---|
| Full issued | Post-clip A | Forecast accountable everywhere; truth must have M inside. Strongest boundary penalty. |
| Full issued | Pre-clip B | Forecast accountable everywhere; small eligible slivers from large parents may verify it. |
| In-domain | Post-clip A | Both score support and truth-size eligibility are domain-limited. Most internally symmetric. |
| In-domain | Pre-clip B | Score support is limited, but truth legitimacy uses the complete parent. Separates event definition from eligibility. |

The prior denominator analysis identified in-domain scoring only as a working
direction. Nothing here approves it.

## 14. Other coupled decisions

### Solid LINE

Current Solid LINE scoring uses filtered Medium truth, so either order could
change its interim area score. A future linear-occupancy method would also be
sensitive to whether a nearby boundary sliver exists. No LINE method is selected.

### Miss coverage policy

Only Sparse currently creates misses, but both Sparse and Medium were analyzed
because a future coverage-aware miss policy could use either. Pre-clip filtering
can only add truth relative to post-clip filtering and therefore can add potential
miss objects; it cannot remove current truth under the parent definitions.

### Observational processing

Dilation and smoothing can connect raw cells before the area test and inflate or
reshape envelopes. Resolving order does not establish whether M is scientifically
appropriate for those processed objects.

### Selected domain

Moving or replacing the approximate boundary changes A's retention decision and
B's output sliver, even if B's parent decision is unchanged. Domain provenance
remains coupled to either policy.

## 15. Candidate decision matrix

| Criterion | A: post-clip | B: pre-clip | C: two-stage/review |
|---|---|---|---|
| In-domain eligibility meaning | Strong | Weak | Explicit |
| Complete-parent meaning | Truncated by D | Strong | Strong first stage |
| Sliver behavior | Excludes <M | Can retain epsilon | Flag/second rule needed |
| Mathematical simplicity | High | High | Moderate |
| Domain dependence | Retention + shape | Shape only after parent pass | Explicit but still dependent |
| Sparse/Medium nesting | Preserved | Preserved | Depends on second rule |
| Miss effect | Only eligible events ≥M | May add small sliver misses | Review-dependent |
| Future in-domain scoring | Symmetric | Separates support/identity | Potentially transparent |
| Reviewer transparency | Low without diagnostics | Low without diagnostics | High |
| Frozen empirical support | No differences | No differences | No threshold evidence |
| Evidence needed | Meaning of eligible event | Meaning of parent event | Workflow and eligibility rule |

## 16. Recommendation and deeper threshold question

**Option C — preferred direction identified but requiring policy and evidence.**

Post-clip filtering is the working direction because it prevents arbitrarily
small eligible truth and aligns with the repository's current goal of suppressing
out-of-domain misses. That conclusion is provisional, not approval. A different
choice could be justified if authoritative methodology establishes M as a
complete-parent feature criterion independent of the verifier's domain.

More fundamentally, no repository or retrieved official evidence supports
15,000 km² as a scientific observational threshold. Repository history supports
only notebook parity. Before approving order, establish:

1. what M is intended to represent after dilation/smoothing;
2. the source and rationale for 15,000 km² itself;
3. whether `D` is authoritative product support or selected scoring policy;
4. examples where a physically large parent has a small operationally meaningful
   in-domain portion; and
5. reviewer treatment for boundary-sensitive components.

The numerical threshold remains unchanged.

## 17. Reproduction and scope preservation

```bash
python analysis/minimum_area_order_frozen_cases.py \
  --components analysis/minimum_area_order_components.csv \
  --forecasts analysis/minimum_area_order_forecasts.csv \
  --summary analysis/minimum_area_order_summary.json
```

The utility reads only committed frozen inputs and writes lightweight analysis
artifacts. It does not modify expected files. Decision 1A, Decision 1B status,
coverage semantics, domain denominator status, Solid LINE status, miss policy,
echo-top policy, and every production threshold remain unchanged.
