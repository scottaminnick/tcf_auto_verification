# TCF Auto Verification Methodology and Functional Specification

**Version:** 1.0
**Status:** Frozen Methodology 1.0 for mandatory meteorologist-reviewed decision support
**Purpose:** Define the reproducible Methodology 1.0 automated first-pass TCF
verification behavior, reviewer safeguards, provisional limitations, and
publication boundary.

## 1. Purpose and authority

The system assists a meteorologist with National System Review verification of
the Traffic Flow Management Convective Forecast (TCF). It retrieves forecasts
and observations, performs repeatable first-pass calculations, identifies
forecast and observed areas, recommends classifications and misses, exposes
diagnostics and incomplete data, and prepares an editable FAA report draft.

It does **not** replace the meteorologist or autonomously publish an authoritative
verification. The meteorologist remains the final reviewer.

## 2. Human-in-the-loop stages

The system shall keep three stages conceptually distinct:

1. **Automated verification:** the reproducible algorithmic result.
2. **Reviewer-approved verification:** the classification and description the
   meteorologist approves after reviewing inputs, calculations, and limitations.
3. **Published verification:** the approved information formatted for the FAA
   National System Review presentation.

Automated output is decision support. It shall always be reviewed before being
transferred into the final FAA product.

## 3. Objective calculation and meteorological judgment

Geographic intersection, physical area, percentage coverage, MRMS thresholds,
temporal availability, echo-top statistics, category thresholds, and domain
intersection shall be calculated as correctly and reproducibly as practical.
Human review does not excuse silent computational errors.

Incomplete-but-usable observations, marginal thresholds, unusual morphology,
ambiguous object separation, the meteorological significance of an automated
miss, interacting forecasts, geographic wording, and exceptional situations may
require judgment. Where practical, the system shall flag such cases rather than
silently resolving them.

## 4. TCF physical criteria

Current AWC documentation describes AREA production criteria including:

* composite reflectivity at least 40 dBZ;
* echo tops at or above FL250;
* qualifying convection covering at least 25% of the polygon; and
* at least 50% forecaster confidence.

LINE criteria include reflectivity at least 40 dBZ, length at least 100 NM,
linear coverage at least 75%, echo tops at or above FL250, and at least 50%
confidence. These provide the physical basis for verification, but verification
need not reproduce production rules literally. Every difference shall be
documented explicitly.

## 5. Forecast coverage categories

The current AWC TCF ASCII format defines coverage in conjunction with feature
type:

* `AREA 2` = **Medium**, 40–74% areal coverage;
* `AREA 3` = **Sparse**, 25–39% areal coverage; and
* `LINE 1` = **Solid**, 75–100% linear coverage.

There is no supported Dense AREA category in the current format. Code 1 is valid
only for LINE records and shall not be labeled Dense. AREA 1 and LINE 2/3 records
are invalid feature/code combinations and shall be rejected with parser
diagnostics rather than reinterpreted.

## 6. Verification time

TCFs are issued every two hours with products valid 4, 6, and 8 hours after
issuance. Each result shall retain Issue Time (IT), Forecast Hour, calculated
Valid Time (VT), and explicit UTC date rollover metadata.

## 7. MRMS observations and provenance

MRMS is the primary objective source for composite reflectivity and echo-top
height. Each verification shall retain requested scans, available scans, used
reflectivity and echo-top scans, their actual observation times, and missing
expected observations. Completeness shall be visible rather than inferred from
an image.

## 8. Verification window and temporal simultaneity

The methodology samples approximately VT ±15 minutes to reduce dependence on an
exact scan time and represent convection near VT.

**Approved Decision 1A:** qualifying observed convection requires reflectivity
≥40 dBZ and echo top ≥FL250 within the same usable nominal MRMS observation
pair. The qualifying masks from usable pairs are combined across the window by
Boolean temporal union. Reflectivity and echo-top fields shall not be
independently maximized across the window before applying the joint criteria.

Independent numerical maximum-reflectivity and maximum-echo-top fields may be
retained for display and diagnostic statistics, but they do not seed truth.

**Open Decision 1B:** determine whether a maximum actual timestamp separation
between the two products within a nominal pair is required. No additional
pair-separation threshold is currently imposed beyond existing retrieval,
availability, and grid-compatibility rules.

**Decision 1B analysis status:** common authoritative MRMS analysis-cycle
identity is the preferred conceptual basis for temporal compatibility, with
reviewer-oriented treatment of ambiguous/outlier pairs and potentially a later
documented sanity limit. This is not approved for implementation: filename
timestamp semantics, operational cycle identity, expected sibling-product
jitter, and a wider archive timing distribution remain unavailable. The
supplied six-event evidence (88 exact matches and two one-second separations)
shows ordinary near-coincidence only; it does not justify a threshold. Current
no-gate behavior remains an evidence-preserving interim state, and Decision 1A
is unchanged.

## 9. MRMS data adequacy

**Approved provisional behavior for Methodology 1.0:** at least one usable
paired MRMS analysis is required. Zero usable pairs is a hard failure. For every
successful calculation, requested/resolved source times, offsets, pair
separation, availability, grid compatibility, exclusions, and use are exposed as
factual provenance for mandatory meteorologist review.

No additional numerical adequacy threshold or Normal / Review Required /
Insufficient Data classification is approved for 1.0. Such rules require new
evidence and may be added only through a later methodology decision. This
provenance-first minimum is reproducible and operationally reviewable, but is
explicitly provisional rather than asserted to be an optimized adequacy policy.

This decision remains separate from **Open Decision 1B**. Product-pair
separation should remain recorded as provenance, but this analysis establishes
no maximum acceptable separation.

## 10. Observed convective field

The truth field shall represent TCF-relevant convection near VT, considering at
least reflectivity ≥40 dBZ, echo top ≥FL250, spatial coverage, temporal sampling,
and minimum meaningful area. The pixel-to-coverage transformation shall be
documented and reproducible. Dilation, smoothing, neighborhood averaging,
contouring, simplification, and area filtering shall be deliberate methodology,
not accidental raster side effects.

**Approved provisional core parameters for Methodology 1.0:** retain the current
25% Sparse and 40% Medium thresholds, one dilation iteration, size-15 smoothing,
20%/50% grade cutoffs, and ARTCC-plus-CMAC verification domain. These values are
fixed for reproducibility in 1.0 but are not claimed to be scientifically
optimized and remain eligible for later evidence-based revision.

On the approximately 0.05° geographic verification grid, the 15×15 window is
about 66 × 84 km at 37.5°N; east-west physical width varies with latitude. Size
15 was selected provisionally after six-event paired-MRMS sensitivity plus
meteorologist spatial review. Six events do not establish climatological
optimization.

## 11. Spatial coverage fields

The reviewer interface exposes the full implemented transformation without
altering it: the Decision 1A pair-first Boolean seed, the post-dilation seed,
the processed 25% Sparse field, and the processed 40% Medium field. Numeric
temporal-max radar imagery is labeled diagnostic and is not verification truth.
The seed and dilation geometries are display-only; the displayed Medium geometry
is the complete field already used for Medium AREA and interim Solid LINE
scoring, not the subset represented by Medium-core Review Flags.

A paired-artifact sensitivity audit may compare zero/one dilation iterations
and smoothing sizes 5/10/15/20. Such runs must use stored `qualifying_mask`
arrays and must reject maxima-only legacy artifacts. Production is one dilation
iteration and size-15 smoothing.

Current behavior uses approximately 25% or greater observed coverage for Sparse
AREA forecasts and approximately 40% or greater for Medium AREA forecasts.

**Approved provisional behavior for Methodology 1.0:** Solid LINE retains its
current interim method: 0.15° geographic buffering, comparison against the 40%
Medium truth field, EPSG:5070 physical-area overlap, and the existing 20%/50%
grade cutoffs. No length, distance, or corridor-occupancy replacement is adopted.
The long-term LINE method remains eligible for later evidence-based refinement,
but it is not a 1.0 blocker.

## 12. AREA verification metric

For AREA forecast feature *F* and its applicable observed field *O*:

```text
Forecast Verification Fraction = physical_area(F ∩ O) / physical_area(F evaluated)
```

Numerator and denominator shall use an appropriate physical-area projection,
not angular degree-space area.

## 13. Automated categories

* **Verified Well:** fraction ≥50%.
* **Verified Close:** 20% ≤ fraction <50%.
* **Overforecasted:** fraction <20%.

These are first-pass recommendations. A meteorologist may change them during
review when justified; reviewer changes should remain distinguishable from the
automated classification.

## 14. Boundary cases

Results near 20% or 50% shall be marked marginal without hiding their unrounded
percentage. Imagery and broader context remain available to the reviewer.

## 15. Verification domain

Verification applies only to the intended geographic domain. Truth shall not
produce misses primarily from out-of-domain convection.

**Methodology 1.0 provisional behavior:** when a forecast crosses the
verification-domain boundary, the denominator remains the full issued forecast
geometry. Truth remains limited to the ARTCC-plus-CMAC verification domain.

The six-event, 48-feature denominator audit found seven partially out-of-domain
AREA forecasts, no fully out-of-domain forecasts, and no category changes when
only the denominator was changed. An in-domain-only denominator remains a
post-1.0 research alternative, not current production behavior. See
`docs/domain_denominator_analysis.md`.

## 16. Minimum observed convective area

**Approved for Methodology 1.0:** forecast-scoring observational truth shall not
be removed solely because a processed Sparse or Medium component is below
15,000 km². All processed components participate in forecast overlap scoring.

**Approved owner decision:** no component-area floor applies to scored truth.
Every disconnected post-domain Sparse component is evaluated individually for
review, but Candidate Miss inventory requires EPSG:5070 area at least 7,500 km²
in addition to strict capture below 20%. This is reviewer triage, not a TCF
physical definition or truth filter.

**Analysis status:** the six-event legacy-replay audit found 87 Sparse and 61
Medium pre-domain components but no retention, forecast-category, or miss
differences between post-clip and parent pre-clip filtering. This is now
historical characterization because no production minimum-area filter remains
for scoring. See
`docs/minimum_area_order_analysis.md`.

**Decision 5b — resolved for Methodology 1.0:** no component-area minimum
filters Sparse or Medium forecast-scoring truth. Reviewer inventory separately
uses a provisional 7,500 km² floor for Sparse Candidate Misses and a 5,000 km²
floor for Medium-core Review Flags. Neither floor defines qualifying convection.

## 17. Missed convection

For meaningful observed feature *T* and applicable forecast coverage *F*:

```text
Truth Capture Fraction = physical_area(T ∩ F) / physical_area(T)
```

The current missed boundary is approximately 20% captured.

**Decision 6a — approved Candidate Miss eligibility:** an individual Sparse
component is surfaced as a Candidate Miss when forecast capture is strict
`<20%` and EPSG:5070 physical area is `>=7,500 km²`. Equality is eligible. Each
candidate carries Sparse area, embedded Medium-core area/fraction, and a
contains-Medium indicator. The area threshold controls reviewer inventory only.

**Decision 6b — provisional triage threshold:** `<20%` observed-area capture may
continue to identify Candidate Misses. Repository history provides no authority
for treating 20% as an autonomous classification threshold.

**Methodology 1.0 provisional coverage interaction:** Sparse AREA, Medium AREA,
and buffered Solid LINE forecast geometries suppress Candidate Miss capture
through the current class-blind forecast union. Class-aware or hierarchical
suppression remains post-1.0 methodology research.

**Approved Medium-core review cue:** an individual Medium component is surfaced
as a non-reportable Medium-core Review Flag when capture is strict `<20%`,
EPSG:5070 area is `>=5,000 km²`, and its parent Sparse component is not already
an eligible Candidate Miss. Equality is eligible. If the parent is already a
candidate, its Medium density is metadata and no duplicate flag is created. The
5,000 km² floor is reviewer triage only; it does not filter Medium truth or
define valid TCF convection. No automatic object consolidation is approved.

The earlier provisional 1,500 km² floor was superseded after final RC1 visual
review found it too permissive, reinforced by an independent September 4, 2026
case. Medium denotes dense coverage, so the distinct cue is reserved for
substantial dense areas. This operational interpretation is not an official
Medium-size rule or climatological optimization.

**Historical analysis status:** the legacy replay found seven floor-filtered
Sparse-only misses and demonstrated strong area/capture parameter coupling. The
subsequent paired audit established the hidden Medium-core visibility gap now
addressed by reviewer flags. Methodology 1.0 retains the class-blind forecast
union and strict `<20%` capture rule provisionally; neither is claimed to be
scientifically optimized. See `docs/missed_event_methodology_analysis.md`.

**Approved publication safeguard:** an automated Candidate Miss is excluded from
the FAA-facing `Missed` section by default. It enters that section only after a
meteorologist explicitly approves it in the review table.

## 18. LINE forecasts

LINEs shall not automatically be treated as ordinary AREAs. Buffered-polygon
area verification is an acceptable interim technique only when its limitations
are visible and the result remains identifiable as line-derived.

**Open Decision 7:** choose a long-term method: a physically buffered line with
area overlap, distance to observed convection, percentage of forecast line
intersecting qualifying convection, or another linear metric.

## 19. Echo tops

Echo tops are diagnostic information for the reviewer. Processing shall use
physically corresponding locations, handle missing values, respect holes, avoid
turning unavailable data into zero, and identify the statistic used.

**Approved publication decision for Methodology 1.0:** echo-top information is
reviewer-only. Retain the current full-forecast-geometry temporal-maximum P90 and
six-cell minimum as provisional meteorologist context, but never include it in
automatically generated FAA-facing report text. It does not affect grading and
is neither a maximum storm top nor the operational TCF forecast-top methodology.
Any future FAA observed-top component requires a separate methodology decision.

Repository history supplies no
scientific or operational rationale for P90 or the six-cell minimum, and the
six-event legacy audit shows material statistic and sample-size sensitivity.
Keep the remaining decisions separate:

* **statistic:** P90 remains provisional reviewer context and may be refined;
* **sample geometry/domain:** full forecast versus pair-qualified or verified
  observation region remains open;
* **minimum sample size:** six qualifying cells remains implemented but
  unsupported; and
* **insufficient-sample semantics — resolved:** fewer than the current required
  six valid cells is unavailable (`None`), not numeric zero. This resolution
  does not approve six as the permanent minimum.

The diagnostic continues to use independent numerical temporal maxima and must
not be described as a maximum top, an operational forecast-top statistic, or the
top specifically associated with Decision 1A pair-qualified convection.

## 20. Geographic attribution

The system should identify intersecting ARTCCs. Reviewers may add operationally
useful state, partial-state, or regional descriptors such as `(SD)`, `(WY/CO)`,
`(CO/NM/AZ)`, or `(IN/IL)`.

## 21. Reviewer interface

For each forecast, show identifier, AREA/LINE type, coverage category, ARTCCs,
automated category, calculated percentage, echo-top diagnostics, map location,
marginal flag, and observational-quality warnings.

For each miss, show identifier, ARTCCs, physical truth area, percentage captured,
and observational warnings.

## 22. Human editing and state separation

Reviewers shall be able to edit the final report without changing the underlying
automated calculation. Editable values should include final category, geographic
and ARTCC descriptions, report inclusion, wording, and notes. At minimum during
the review, retain **automated classification** and **reviewer-approved
classification** separately.

## 23. Editable FAA report

Output is an editable draft, organized by Issue Time, Valid Time, Forecast Hour,
Verified Well, Verified Close, Over-forecast, and Missed. Diagnostic UI content
need not appear in FAA-facing text. Review and formatting layers shall remain
separate.

## 24. Publication workflow

```text
Automated verification
→ meteorologist review
→ meteorologist edit/approval
→ editable FAA Google Doc
→ Gemini Gem conversion to FAA Google Slide
→ final meteorologist visual/content review
→ publication
```

This application is responsible primarily through editable report creation and
shall not equate its automated recommendation with publication.

## 25. Auditability

Retain enough information to reproduce or understand each result: issuance and
valid time, forecast hour and source identifier, MRMS scan timestamps and missing
scans, methodology version and parameters, automated and approved categories,
and major quality warnings. The goal is practical traceability, not a burdensome
formal audit system.

## 26. Methodology versioning

The verification methodology shall have an explicit version. Any change capable
of altering results—including thresholds, compositing, smoothing, minimum area,
domain treatment, misses, LINE handling, or area calculation—shall increment
it. Pure display changes need not. Regression baselines shall ultimately be tied
to a methodology version.

## 27. Testing philosophy

Maintain two distinct suites:

* **Methodology validation tests:** synthetic cases with independently known
  answers, proving implementation of approved methodology.
* **Regression baselines:** historical cases produced with a named approved
  methodology version, detecting unintended behavioral changes.

Historical baseline success alone does not demonstrate scientific correctness.

## 28. Operational philosophy

The objective is not autonomous mathematical perfection. It is to remove manual
calculation, consistently apply approved rules, expose assumptions, identify
questionable cases, enable meteorological judgment, and reduce the effort needed
to prepare the existing FAA product. When choosing between a silent uncertain
decision and a clear reviewer flag, generally choose the flag.

## 29. Post-1.0 methodology research

Methodology 1.0 intentionally carries several visible provisional behaviors.
Future evidence-based revisions may examine:

1. authoritative MRMS cycle/timestamp semantics and whether Decision 1B should
   eventually impose a pair-separation sanity limit;
2. full-issued versus in-domain forecast denominators near the verification
   boundary;
3. class-aware Candidate Miss capture interaction among Sparse AREA, Medium AREA,
   and Solid LINE forecasts;
4. long-term Solid LINE distance/occupancy verification methods;
5. MRMS adequacy classifications beyond the current one-usable-pair minimum; and
6. an observed echo-top methodology suitable for FAA-facing publication.

These are post-1.0 research items, not unresolved release blockers. Any adopted
result-altering change requires a new methodology version and new regression
baseline identity.

## 30. Methodology 1.0 release status

**Release status: METHODOLOGY 1.0 FROZEN.**

The release freeze completed after paired historical validation, independent
meteorologist review, official pair-first baseline promotion, strict replay, and
application/direct-pipeline parity review.

Frozen six-event regression inventory:

* 48 forecasts;
* 5 Verified Well;
* 17 Verified Close;
* 26 Overforecasted;
* 15 Candidate Misses;
* 2 Medium-core Review Flags; and
* 1 forecast near a grading boundary.

Each of the six official baseline events contains 15 usable paired MRMS analyses,
the stored Decision 1A `qualifying_mask`, numerical diagnostic maxima, exact TCF
source text, and `mrms_provenance.json`.

An independent September 4, 2026 F04 case contained 11 forecasts, produced
1 Verified Well / 2 Verified Close / 8 Overforecasted, 4 Candidate Misses, and
0 Medium-core Review Flags with 15/15 usable paired analyses. Meteorologist
visual review accepted the resulting morphology and reviewer inventory.

These cases establish release regression evidence and operational review
acceptability; they are not a climatology or proof of scientific optimization.

## 31. Methodology identity and baseline governance

The machine-readable production identity is `1.0`.

Versioned Methodology 1.0 regression artifacts require:

* `expected.json` with `methodology_version = "1.0"`;
* `arrays.npz` containing `max_tops`, `max_refl`, `qualifying_mask`, `lons`,
  and `lats`;
* exact `tcf_raw.txt`; and
* `mrms_provenance.json`.

Versioned Methodology 1.0 artifacts always replay through normal pair-first
`run_verification(..., qualifying_mask=...)`. The explicitly named
`run_verification_legacy_independent_max()` path remains only so old external or
historical maxima-only artifacts remain interpretable.

Any future change capable of altering truth, grading, review-cue inventory,
misses, domain treatment, LINE scoring, temporal qualification, or another
result-bearing calculation shall increment the methodology version and receive
corresponding regression evidence.
