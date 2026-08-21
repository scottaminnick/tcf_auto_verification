# Methodology 1.0 Consolidation and Readiness Review

## Overall readiness

**READY FOR INTEGRATION CANDIDATE**, but **not ready for Methodology 1.0
baseline capture**.

The calculation is substantially more deterministic, physically correct, and
auditable than the inherited implementation. Decision 1A, feature-aware parsing,
physical geometry/area, and unavailable-value corrections are implemented and
tested. Most remaining research questions can remain explicitly provisional in
a mandatory human-review system.

The methodology owner resolved the two scientific/workflow blockers identified
by this review: forecast-scoring truth is no longer area-filtered, and automated
miss geometry is explicitly non-authoritative Candidate Miss triage requiring
meteorologist approval before FAA inclusion. The remaining blockers are release
mechanics: integrated validation, explicit version assignment, fresh paired
historical verification, and new baseline capture.

All other identified Option C methodologies remain explicitly provisional and
unchanged for 1.0. This does not convert them into permanent approved policy.

The authoritative planning artifact is
`analysis/methodology_1_0_decision_matrix.csv`, with 27 separately classified
decisions/subdecisions.

## Status and blocker vocabulary

Each matrix row has exactly one decision status:

* **APPROVED + IMPLEMENTED** — an approved behavior exists in this checkout;
* **APPROVED + NOT YET INTEGRATED** — approved, but absent from the candidate;
* **PROVISIONAL PRODUCTION BEHAVIOR** — implemented behavior may be carried only
  with explicit provisional documentation;
* **WORKING HYPOTHESIS** — analysis favors a direction but does not approve it;
* **OPEN — EVIDENCE REQUIRED** — empirical/authoritative support is missing;
* **OPEN — POLICY DECISION REQUIRED** — owner judgment, not more coding, is next;
* **DEFERRED BEYOND 1.0** — intentionally outside the release boundary.

No current row is labeled approved-not-integrated or deferred because the local
squashed checkout contains equivalent Decision 1A code, and deferral decisions
must be made by the methodology owner. The supplied standalone Decision 1A
commit still matters to branch integration, discussed below.

A **blocker** is not merely an unanswered question. It is current behavior that
can silently construct invalid truth, materially change FAA-facing grading via
an unsupported load-bearing parameter, conflate unavailable evidence with
absence, prevent reproducibility/version identity, undermine an approved rule,
or bypass the required approval boundary. A limitation can be provisional when
it is visible, editable, explicitly documented, and constrained to reviewed
decision support. Human review mitigates ambiguity; it cannot excuse a silent
calculation error or an undefined publication boundary.

## Consolidated decision inventory

### Approved and implemented

* **Decision 1A:** per usable pair, `reflectivity >=40 dBZ AND echo top >=FL250`,
  followed by Boolean temporal union. Numerical maxima remain diagnostic only.
* **Feature-aware TCF semantics:** AREA 2 is Medium, AREA 3 Sparse, LINE 1 Solid;
  structurally invalid combinations are rejected rather than relabeled.
* **Physical AREA metric:** intersection and denominator area use EPSG:5070.
* **Parser/topology corrections:** structured validation, LINE identity,
  cell-footprint polygonization, holes, multipart geometry, and grid edges.
* **Echo-top insufficient sample:** fewer than six qualifying samples is `None`,
  not zero.
* **Human-stage separation as specification intent:** automated, approved, and
  published results are distinct. Operational enforcement remains a release
  blocker rather than a new scientific decision.

### Working hypotheses

* **Decision 1B:** common authoritative MRMS cycle identity is preferred; current
  no-gate behavior remains interim because filename semantics are unresolved.
* **MRMS adequacy:** multi-metric Normal/Review Required/Insufficient Data is the
  preferred structure; no thresholds are approved.
* **Solid LINE:** line-length occupancy with physical-distance tolerance is the
  preferred research direction; current buffered area remains interim.
* **Domain denominator:** in-domain denominator is conceptually favored, while
  current full-issued denominator remains unchanged.
* **Minimum-area order:** post-domain filtering is favored; threshold existence
  and magnitude are a separate question.

### Provisional production behavior

* Sparse/Medium processed truth uses 25%/40% fields after inherited dilation and
  smoothing.
* categories use 50%/20% forecast-area overlap cutoffs;
* verification domain is 21 ARTCC polygons plus committed CMAC supplement;
* Solid LINE is a 0.15° corridor scored against Medium truth by area overlap;
* full issued geometry is the domain-boundary denominator;
* echo top is full-geometry temporal-max P90 with a six-cell minimum;
* ARTCC labels are geometry-derived and editable; and
* Candidate Miss review evaluates every disconnected Sparse component with
  `<20%` observed-area capture and a class-blind forecast union; no hard area
  floor applies. Hidden poorly captured Medium components are separate
  non-reportable reviewer flags. Candidates still require explicit approval.

### Open decisions

The matrix separates minimum-area use for forecast scoring from use for miss
triage; miss eligibility, capture, and class interaction; echo statistic, source
region, sample count, and report role; Decision 1B; observation adequacy;
grading cutoffs; domain authority; spatial transformation; methodology version;
and final publication governance. Option C/D findings remain open—they are not
silently promoted here.

## Evidence strength

**Strong** evidence applies to executable mathematical corrections: Decision 1A
has analytic invariants plus a paired six-event experiment that exactly rebuilt
the frozen maxima; feature semantics cover all 48 frozen forecasts; physical
area, parser, topology, holes, multipart, and null behavior have independent
synthetic oracles. “Strong” here supports the rule/mechanism, not climatological
generalization.

**Moderate** evidence applies where analytic behavior is strong but policy scope
is narrower: post-domain area ordering, Sparse/Medium selection, ARTCC
attribution, and editable report plumbing.

**Limited** evidence applies to six-event boundary/timing studies, observation
adequacy without per-slot arrays, one Solid LINE, echo-top distributions, and
coverage contributions to misses.

**None / historical heuristic** applies to the 15,000 km² magnitude, six-cell
minimum, P90 choice, 20% miss capture, inherited smoothing/dilation parameters,
and—pending owner documentation—the 20/50 category cutoffs. Regression tests
prove implementation stability, not scientific authority.

## Implemented methodology corrections

Local history is squashed: current commit `a3c02bb` contains the consolidated
equivalent of multiple supplied methodology commits, so not every correction has
a separately addressable local hash. The integration record must preserve both
the supplied original IDs and equivalence checks.

| Change | Commit/equivalent | Corrected behavior | Validation / known impact |
|---|---|---|---|
| Decision 1A | supplied `63494ed`; equivalent present in local `a3c02bb` | rejects cross-time reflectivity/top conjunction | pair-first subset/oracle tests; six events removed 1,417 cells, changed one category and two misses |
| feature-aware coverage/parser | supplied methodology-hardening commits; consolidated in `a3c02bb` | correct AREA/LINE labels and rejects invalid combinations | 48-feature audit and malformed-record tests |
| EPSG:5070 physical area | consolidated in `a3c02bb` | replaces degree-space physical area | analytic projected-area/intersection tests |
| cell-footprint polygonization | consolidated in `a3c02bb` | replaces contour truncation and preserves topology | single-cell, edge, hole, connectivity, nonuniform-grid tests |
| complete echo geometry | consolidated in `a3c02bb` | holes and all multipart pieces sampled strictly inside | hole/multipart/boundary tests |
| MRMS provenance/failures | consolidated in `a3c02bb` | exclusions and unavailable composite are explicit | 17 unit/audit tests include resolve/download/read/grid paths |
| nullable echo-top availability | consolidated in `a3c02bb` | unavailable array is not zero | nullable table/report tests |
| insufficient echo sample | supplied `89865cf`; equivalent present in `a3c02bb` | `<6` qualifying cells returns `None` | 15/48 frozen fields changed; no score/category/FAA-text changes; 0/1/5/6/7/NaN tests |

These are production corrections. By contrast, Solid LINE, denominator,
minimum-area order/magnitude, miss policy, adequacy rules, Decision 1B, and the
broader echo-top statistic analysis have not changed production policy.

## Special blocker assessments

### Minimum-area threshold

The owner resolved the forecast-scoring blocker: **no processed Sparse or Medium
component is removed from forecast truth solely because it is below 15,000
km²**. Forecast overlap now uses all post-domain processed components.

For **Candidate Misses**, the owner subsequently removed the floor after fresh
paired validation and meteorologist review. Historical sensitivity remains
evidence that a hard floor was load-bearing, not a reason to retain one. Area
and embedded Medium density are now reviewer context.

### Misses

Current triage remains deterministic—retained Sparse components, `<20%`
observed-area capture, and class-blind forecast union—but eligibility and capture
remain provisional. Automated results are now labeled Candidate Miss, default to
`approved_for_report=False`, and are omitted from the FAA `Missed` section until
a meteorologist explicitly checks approval in the review table.

### MRMS adequacy

Zero usable pairs already raises, and detailed provenance is visible; that makes
absence of numerical adequacy thresholds acceptable provisionally. However, one
usable pair currently produces ordinary categories. Before 1.0 the owner must
decide whether mandatory provenance review alone is sufficient or whether a
non-numeric pathological **Insufficient Data** safeguard is required. Archive
evidence is needed before quantitative state thresholds, but quantitative rules
need not block 1.0.

### Decision 1B

The supplied 90 pairs were separated by 0–1 second, and separation is visible.
No abnormal archive evidence or filename/cycle semantics supports a gate. The
current no-gate behavior is acceptable provisionally for 1.0 if documented and
reviewed; cycle semantics and outlier sampling can follow. A discovered large
separation would reopen blocker classification because it could undermine 1A.

### Solid LINE

One historical LINE cannot calibrate a replacement distance. Current buffered
area is conceptually misaligned but reproducible, identifiable as LINE, rare in
the sample, and human-reviewed. It is a conditional blocker: the owner may
explicitly accept it as interim 1.0 behavior, or withhold authoritative LINE
classification until a distance/occupancy policy exists. More analysis without a
policy target is not the shortest path.

### Domain and minimum-area order

Seven forecasts were partially outside the selected domain; the minimum
in-domain fraction was about 85.37%, and no frozen category changed. The full
denominator is acceptable provisional behavior. Authority for the domain itself
needs explicit owner adoption because it governs all eligibility.

Post-domain minimum-area filtering had no frozen retention, category, or miss
difference and avoids arbitrary boundary slivers. It can remain the documented
working order once the magnitude/use decision is made.

### Echo-top diagnostic

P90, full-geometry temporal maxima, and six cells are inherited, but the value
does not affect grading. The corrected null semantics and editable review stage
make it acceptable provisional reviewer context. Positive P90 currently enters
FAA text, so the owner must approve it as a reviewed descriptor or constrain it
to reviewer-only use. Its meaning must not be “pair-qualified storm top.”

## Reviewer-only versus FAA-facing fields

| Field | Reviewer | FAA draft | Publication risk |
|---|---|---|---|
| category | table/map, editable | section heading | high; unsupported grading/truth parameters directly affect it |
| overlap fraction | table and boundary context | not printed | moderate; it drives category |
| Missed | map/table | `Missed` line | high unless explicitly human-approved candidate |
| echo-top P90 | table/hover, editable | positive value printed | moderate; diagnostic heuristic needs approved descriptive meaning |
| ARTCC | table, editable | printed | low/moderate; geometry-derived wording is reviewable |
| MRMS provenance/separations | provenance panel | absent | reviewer context; essential safety evidence |
| exclusion reasons | provenance/parser diagnostics | absent | reviewer context; prevents silent failure |
| boundary flag | review table/context | absent | useful marginal-case safeguard |

The UI/report seam permits edits, and fixture tests prove report text follows the
table. The deployed workflow must also retain automated versus approved state;
editability alone does not prove that approval occurred.

## Human-in-the-loop safety case

Intended flow is automated first pass → meteorologist review/edit → editable FAA
report → downstream slide conversion → final meteorologist review → publication.
Human review adequately mitigates documented interim LINE/echo/domain choices,
geographic wording, and candidate-miss triage when the limitation is visible.
It does not mitigate invisible removal of truth by an unsupported floor, hidden
data absence, or an operational process that can publish an unapproved draft.

Therefore Methodology 1.0 needs a documented approval control, provenance access,
editable automated outputs, and explicit provisional labels. It does not need a
permanent answer to every diagnostic research question.

## Validation inventory

At this review point:

* `make methodology` runs 56 independent analytic methodology tests;
* unit discovery runs 17 MRMS provenance and coverage-audit tests;
* `baseline/test_fixture.py` runs 35 harness scenarios;
* six frozen events contain 48 forecasts but maxima-only arrays;
* committed scripts reproduce domain, area, miss, timing, LINE, and echo audits;
* legacy replay is explicitly named and cannot validate Decision 1A; and
* app parity exists for UI plumbing but was not rerun as part of this review.

Tests establish behavior and invariants. Six historical events characterize
impact but are not climatology or independent scientific validation.

## Baseline status

Historical expected artifacts are intentionally stale characterizations. Their
arrays lack per-slot `qualifying_mask`; they encode legacy independent maxima,
older geometry/domain behavior, and zero-valued insufficient echo diagnostics.
The named legacy path preserves their interpretability without pretending they
represent approved Decision 1A.

Before recapture: resolve true blockers; integrate equivalent production
changes; assign an explicit methodology version; run fresh paired events; store
`qualifying_mask` and provenance; review app/report parity; and preserve legacy
artifacts under an unmistakable legacy identity. Do not tune policy to make stale
expected files green.

## Branch and commit integration

The locally available branch is squashed at `a3c02bb` and already contains an
equivalent pair-first implementation and the supplied `89865cf` semantic change.
The task-supplied standalone `63494ed` object is not present in local refs, so
this checkout cannot determine its branch ancestry or byte-level overlap. The
candidate integrator must fetch the source refs and compare behavior before
cherry-picking; blindly applying both risks duplicate/conflicting changes.

Recommended order, not executed:

1. obtain owner decisions for true and conditional blockers;
2. select a candidate base and fetch `63494ed` plus coverage/parser and
   `89865cf` source branches;
3. integrate Decision 1A first, or document equivalence if already present;
4. integrate parser/coverage/physical-geometry corrections;
5. integrate provenance and nullable/insufficient echo semantics;
6. resolve conflicts by approved behavior, not by baseline parity;
7. run methodology, unit, fixture, app-parity, and legacy-characterization tests;
8. run fresh paired historical verification with provenance;
9. freeze/version the candidate methodology;
10. capture new 1.0 baselines with paired masks, perform report review, then open
    one final integration PR.

## Minimum Methodology 1.0 exit criteria

- [x] Remove the unsupported minimum-area floor from forecast scoring truth.
- [x] Remove the unsupported 15,000 km² Candidate Miss gate; retain `<20%`
  capture provisionally and expose physical area/density as reviewer context.
- [x] Require explicit meteorologist approval before a candidate enters FAA
      `Missed` text.
- [ ] Integrate/verify Decision 1A and all approved corrections on one branch.
- [ ] Preserve parser rejection of unsupported feature/code combinations.
- [ ] Preserve provenance visibility and no unavailable-to-zero semantics.
- [ ] Run all validation layers on the integrated candidate.
- [ ] Run fresh paired cases and review score/report impacts.
- [ ] Document every retained provisional behavior and its reviewer mitigation.
- [ ] Assign explicit Methodology 1.0 version only after policy freeze.
- [ ] Capture new baselines containing `qualifying_mask` and provenance.
- [ ] Keep legacy maxima-only artifacts and replay visibly distinct.
- [ ] Perform application/report parity and final meteorologist review before the
      final integration PR.

## Shortest path and owner questions

The owner has answered the minimum-truth and Candidate Miss governance
questions. Remaining actions for the integration owner are:

1. **Core provisional parameters:** “Will Methodology 1.0 explicitly retain the
   inherited 20/50 grading cutoffs, selected ARTCC+CMAC domain, and
   dilation/smoothing parameters provisionally?”
2. **Adequacy safeguard:** “Is mandatory provenance review sufficient for 1.0,
   or must a pathological Insufficient Data safeguard precede categories?”
3. **Solid LINE:** “May 0.15° buffered-area/Medium-truth scoring remain an
   explicitly interim reviewed method, or should LINE be withheld?”
4. **Echo publication:** “May full-geometry temporal-max P90 remain a reviewed
   FAA descriptor, or should it be reviewer-only until its methodology is
   approved?”

After those judgments, only targeted implementation/workflow changes, integrated
testing, a fresh paired experiment, version assignment, and baseline capture are
needed.

## Draft 0.1 → 1.0 candidate changelog

### Approved behavioral corrections

* pair-first MRMS qualification and temporal Boolean union;
* factual per-slot provenance and explicit unusable composite failure;
* feature-aware coverage parsing and invalid-combination rejection;
* physical EPSG:5070 area calculations;
* full cell-footprint topology with holes/multipart support;
* full-geometry hole/multipart echo sampling; and
* unavailable and insufficient echo samples represented as nullable.

### Documented provisional behavior

* inherited spatial transformation and 25%/40% truth fields;
* full-issued denominator over the selected policy domain;
* no hard area floor for Candidate Miss or Medium-core reviewer visibility;
* buffered-area Solid LINE;
* Candidate Miss capture logic with explicit FAA-report approval;
* no Decision 1B gate or adequacy thresholds, with provenance review; and
* temporal-max full-geometry P90 and six-cell minimum.

This is a candidate changelog, not a declaration of Methodology 1.0.

## Scope preservation

This consolidation changes documentation and a compact planning CSV only. It
does not change production code, thresholds, truth, scoring, misses, LINE,
domain, MRMS retrieval, Decisions 1A/1B, echo statistics, reports, or baselines.
