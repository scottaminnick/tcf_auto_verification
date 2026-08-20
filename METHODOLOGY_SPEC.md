# TCF Auto Verification Methodology and Functional Specification

**Version:** 0.1  
**Status:** Working methodology specification  
**Purpose:** Define intended automated TCF verification behavior before further
algorithmic changes.

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

Substantial missing observations shall never be hidden. Incomplete data need not
always prevent calculation because a reviewer is required; instead expose:

* **Normal:** adequate observations for the intended method.
* **Review Required:** a calculation exists, but missing/mismatched observations
  or another concern could materially affect it.
* **Insufficient Data:** observations cannot support a meaningful automated
  recommendation; manual verification is required.

**Open Decision 2 — analysis status:** the read-only observation-adequacy
analysis identifies a multi-metric, three-state reviewer framework as the
preferred policy structure, but approves neither state rules nor numerical
thresholds. Usable-slot count alone is insufficient: unique source-pair count,
temporal gaps, valid-time proximity, pre/post-valid balance, grid exclusions,
and missingness pattern carry nonredundant information. The committed frozen
maxima do not contain per-slot arrays or actual-source provenance, so historical
leave-out sensitivity and event-level adequacy metrics cannot be reconstructed.
Thresholds require a broader scan-level provenance/paired-mask sample,
controlled degradation experiments, and operational expert evidence.

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

## 11. Spatial coverage fields

Current behavior uses approximately 25% or greater observed coverage for Sparse
AREA forecasts and approximately 40% or greater for Medium AREA forecasts.

**Open Decision 3:** determine the long-term verification method for Solid LINE
forecasts, including whether the interim 40% observational field and buffered
area-overlap metric should be replaced by a 75% or linear metric. Coverage-code
semantics themselves are resolved and are not part of this open decision.

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

**Open Decision 4:** when a forecast crosses the domain boundary, use either the
entire polygon or only its in-domain portion as the denominator. This decision
must be explicit because it directly changes the percentage.

**Analysis status:** the six-event, 48-feature denominator audit found seven
partially out-of-domain AREA forecasts, no fully out-of-domain forecasts, and no
category changes when only the denominator was changed. In-domain geometry is a
working conceptual direction because numerator and denominator then share the
same observational support, but forecasts with little or zero eligible geometry
require an explicit operational eligibility policy. No denominator policy is
approved. See `docs/domain_denominator_analysis.md`.

## 16. Minimum observed convective area

**Approved for Methodology 1.0:** forecast-scoring observational truth shall not
be removed solely because a processed Sparse or Medium component is below
15,000 km². All processed components participate in forecast overlap scoring.

The existing **15,000 km²** floor remains only a provisional Candidate Miss
triage parameter, measured in EPSG:5070 after domain clipping. It does not define
whether qualifying observational truth exists.

**Decision 5a — provisional Candidate Miss processing order:** apply the retained
triage floor after dilation, smoothing/neighborhood coverage, observational
thresholding, polygonization, and domain clipping.

**Analysis status:** the six-event legacy-replay audit found 87 Sparse and 61
Medium pre-domain components but no retention, forecast-category, or miss
differences between post-clip and parent pre-clip filtering. Post-clip filtering
remains provisional for Candidate Miss triage because it avoids arbitrarily
small boundary slivers. It no longer filters forecast-scoring truth. Repository
history supports the triage value only as notebook parity, and the floor measures
a processed envelope rather than raw convection. See
`docs/minimum_area_order_analysis.md`.

**Decision 5b — partially resolved:** no hard observational minimum applies to
forecast scoring. Candidate Miss floor magnitude remains provisional and
unapproved; the seven-value sweep demonstrates that it is a triage parameter,
not scientific event existence. See `docs/minimum_area_threshold_analysis.md`.

## 17. Missed convection

For meaningful observed feature *T* and applicable forecast coverage *F*:

```text
Truth Capture Fraction = physical_area(T ∩ F) / physical_area(T)
```

The current missed boundary is approximately 20% captured.

**Decision 6a — provisional Candidate Miss eligibility:** retained Sparse
components at or above the current 15,000 km² triage floor may be surfaced as
automated candidates. This does not decide whether observational truth exists
and does not authorize an FAA-facing Missed classification.

**Decision 6b — provisional triage threshold:** `<20%` observed-area capture may
continue to identify Candidate Misses. Repository history provides no authority
for treating 20% as an autonomous classification threshold.

**Open Decision 6c — coverage interaction:** decide whether any forecast
geometry suppresses a miss, only a corresponding coverage class does, or Sparse,
Medium, and Solid LINE classes interact hierarchically.

**Analysis status:** the six-event legacy replay found seven current Sparse-only
misses; six had zero forecast capture and one had 7.77%. A capture-threshold and
minimum-area matrix demonstrates strong parameter coupling, while Medium truth
has no independent miss role and all forecast classes currently suppress through
one geometry union. Observed-area capture is a useful candidate-miss signal, but
no permanent threshold or coverage policy is approved. See
`docs/missed_event_methodology_analysis.md`.

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

**Open Decision 8:** select maximum, 90th percentile, 75th percentile, or another
operationally meaningful statistic, and decide whether it belongs in the FAA
text or only the review interface.

**Decision 8 analysis status:** retain the current full-forecast-geometry,
temporal-maximum P90 provisionally as a reviewer upper-end descriptor; neither
P90 nor FAA-text use is approved permanently. Repository history supplies no
scientific or operational rationale for P90 or the six-cell minimum, and the
six-event legacy audit shows material statistic and sample-size sensitivity.
Keep the remaining decisions separate:

* **statistic:** P90 versus maximum/P95/another summary remains open;
* **sample geometry/domain:** full forecast versus pair-qualified or verified
  observation region remains open;
* **minimum sample size:** six qualifying cells remains implemented but
  unsupported; and
* **insufficient-sample semantics — resolved:** fewer than the current required
  six valid cells is unavailable (`None`), not numeric zero. This resolution
  does not approve six as the permanent minimum.

The diagnostic continues to use independent numerical temporal maxima and must
not be described as the top specifically associated with Decision 1A
pair-qualified convection.

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

## 29. Decisions required before methodology 1.0

1. What maximum actual reflectivity/echo-top timestamp separation, if any, is
   acceptable within a nominal observation pair (Decision 1B)?
2. What missing-data levels define Normal, Review Required, and Insufficient Data?
3. What is the approved long-term verification method and truth field for Solid
   LINE forecasts?
4. Is a domain-crossing forecast denominator whole or in-domain only?
5. Should a hard observational minimum exist; if so, what magnitude and processed
   object should it use, and at what stage should it be applied?
6. Which observed events are miss-eligible, what capture prevents a miss, and how
   do Sparse, Medium, and Solid LINE forecast classes interact?
7. What echo-top statistic is used internally and/or reported?
8. Which automated fields belong in the editable FAA Google Doc rather than
    remaining diagnostic-only?

Resolve and document these independently rather than answering them implicitly
through code.

## 30. Methodology 1.0 Readiness

**Readiness status: READY FOR INTEGRATION CANDIDATE, not ready for Methodology
1.0 baseline capture.** See `docs/methodology_1_0_readiness.md` and the authoritative planning
matrix `analysis/methodology_1_0_decision_matrix.csv`.

### Blockers

1. Integrate and validate all approved corrections on one candidate branch.
2. Assign Methodology 1.0 only after fresh paired historical verification and
   final application/report parity review.
3. Capture new paired baselines only after the integrated policy is frozen.

The current minimum-area order, full-issued denominator, no Decision 1B gate,
absence of numerical adequacy thresholds, interim Solid LINE method, Candidate
Miss triage parameters, and broader echo-top choices remain provisional. The
owner's decisions do not promote them to permanent approved policies.

### Integration prerequisites

* integrate or verify equivalent Decision 1A, parser/coverage, physical-geometry,
  provenance, and nullable echo-top corrections on one candidate branch;
* preserve the explicit legacy independent-max replay for old artifacts;
* run fresh paired historical cases with saved `qualifying_mask` and provenance;
  and
* resolve merge conflicts according to approved methodology rather than stale
  baseline parity.

### Minimum exit criteria

* forecast-scoring truth remains independent of the Candidate Miss area floor;
* Candidate Misses require explicit meteorologist approval for FAA inclusion;
* automated and reviewer-approved states remain distinct and final FAA use
  requires human approval;
* methodology, unit, fixture, and application/report parity checks pass on the
  integrated candidate;
* no unavailable or insufficient observation is silently represented as
  meteorological zero;
* unsupported TCF feature/code combinations remain rejected;
* provenance and provisional limitations remain reviewer-visible;
* Methodology 1.0 is explicitly versioned only after policy freeze; and
* new paired baselines are captured afterward while legacy maxima-only artifacts
  remain unmistakably distinct.

## 31. Methodology 1.0 integration candidate

The approved behaviors are assembled on `methodology/1.0-integration` with the
machine-readable release-candidate identity `1.0-rc1`. This identity is not a
final baseline designation. The integration audit is recorded in
`docs/methodology_1_0_integration.md`.

The candidate contains Decision 1A, provenance, physical/topology corrections,
feature-aware parsing, nullable echo tops, scoring truth without the historical
area floor, and the separate human-approved Candidate Miss boundary. All Option
C behaviors listed above remain provisional and unchanged.

**Current release status: READY FOR FRESH PAIRED VALIDATION.** Actual paired
historical evidence, meteorologist inspection, a final UI/report smoke test, and
an explicit policy freeze remain prerequisites to renaming the methodology
`1.0` and capturing new baselines. New versioned artifacts must contain the
paired mask and source provenance; a maxima-only artifact is always legacy.
