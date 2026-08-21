# MRMS Observation Adequacy Analysis

## Scope and conclusion

This is a read-only methodology analysis. It changes no production behavior and
sets no adequacy or within-pair timing threshold. **Recommendation: Option C — a
three-state, reviewer-oriented quality framework is the preferred structure,
but its exact rules remain unresolved.** The preference is structural rather
than numerical: it preserves the distinction between a computable result and a
sufficiently supported result, matches the existing human-review workflow, and
can express missingness patterns that one count cannot. Six events do not
support cutoffs.

## Current observation behavior

### Nominal schedule and resolution

Production requests 15 nominal slots at `-14, -12, ..., 0, ..., +12, +14`
minutes: a 28-minute span under the configured approximately ±15-minute window.
For each slot it independently resolves `EchoTop_18` and
`MergedReflectivityQCComposite` to the nearest archived file. A file more than
300 seconds from the nominal request is unavailable. Thus the following are
different objects:

* a **nominal slot** is one requested time;
* an **actual source time** is parsed from an archive key;
* a **usable pair** is a slot whose two files resolve, download, decode, agree
  spatially, and are folded into the composite; and
* a **unique pair** is a distinct tuple of actual reflectivity and top keys.

Files are deduplicated for downloading, not for plan evaluation. Two nominal
slots may therefore reuse one actual pair. Repetition is harmless to Boolean
union and numerical maxima, but inflates a nominal usable-slot count relative to
independent temporal support.

### Qualification and exclusions

A usable slot contributes `[reflectivity >= 40 dBZ] AND [top >= 25 kft]` before
the per-slot masks are Boolean-unioned (Decision 1A). Unusable slots contribute
nothing. Production records distinct reasons for missing one/both products,
download failure, read failure, and grid incompatibility. It also verifies the
two products' grids and every later pair against the established composite grid.

Production has **no adequacy gate**. One usable pair is enough to proceed. No
usable pair raises `MRMSCompositeUnavailableError` with provenance. Any number
of isolated or contiguous missing slots, absence at valid time, one-sided
support, or duplicate source pairs otherwise proceeds. Provenance is shown to
the reviewer but does not alter the result.

This behavior does not literally insert a missing raster as all-False. Yet the
temporal union cannot contain convection that occurred only during an unusable
slot. Accordingly, downstream absence in the resulting union must not be
interpreted as affirmative observation of no convection throughout the window.

## Historical provenance evidence

The six-event external Decision 1A experiment reports recovery of all 90
nominal pairs and product separations of 0–1 seconds. It does not commit the
per-slot key/timestamp manifest or paired arrays. The repository's six frozen
`arrays.npz` files contain only temporal maxima and coordinates. Consequently:

* event-level usable and unique-pair counts cannot be independently reproduced;
* actual offsets, duplication, gaps, missing runs, balance, and per-event
  separation percentiles cannot be calculated from committed artifacts;
* 0–1 seconds is an aggregate reported range, not a basis for assigning a
  separation to any row; and
* paired leave-out degradation cannot be reconstructed from maxima.

`analysis/mrms_observation_provenance.csv` therefore records the 90
reconstructable nominal requests and deliberately leaves every actual-source,
usability, grid, and exclusion field blank. Its evidence-status column prevents
unknown facts from looking like zero or `False`. This is preferable to
fabricating completeness from a maxima-only artifact.

## Adequacy dimensions

No single proposed measure subsumes the others:

| Dimension | Information supplied | Important limitation |
|---|---|---|
| usable slots | effective pair opportunities | duplicates can inflate it |
| unique pairs | independent actual sources | does not describe placement |
| temporal span | window extent sampled | can hide a central hole |
| proximity to VT | support near forecast time | one near-VT scan is not full-window support |
| largest gap / missing run | continuity | edge gaps and central gaps may differ |
| pre/post balance | asymmetry | a temporal occurrence envelope does not itself prove symmetry is mandatory |
| pair separation | within-pair simultaneity | belongs also to unresolved Decision 1B |
| grid integrity | spatial comparability | binary prerequisite, not temporal sufficiency |
| exclusion reasons | cause and recoverability | not a scalar adequacy score |

Useful compact reviewer facts are usable and unique-pair counts, nearest pair to
VT, largest actual-time gap, pre/at/post support, longest missing run, excluded
count/reasons, and maximum pair separation. Full keys and all per-slot records
remain valuable drill-down provenance but are too detailed for a primary banner.

## Synthetic missing-data analysis

The reproducible synthetic schedule demonstrates why count alone is inadequate:

* all 15 slots have span 28 minutes, nearest distance 0, and largest gap 2;
* 15 nominal usable slots reusing eight source pairs still have only eight
  independent pairs;
* ten usable slots surrounding a central five-slot hole have a 12-minute gap
  and nearest observation six minutes from VT;
* eight alternating slots have fewer observations but only a four-minute gap
  and nearest distance two minutes;
* one exact-VT pair and one `VT-14` pair both have count one, but proximity and
  window placement are materially different;
* complete reflectivity with only five usable echo-top times supplies five, not
  fifteen, Decision 1A opportunities; and
* grid-excluded pairs are unavailable evidence, not non-convective evidence.

The set-based evolving-storm experiment contains persistent, short-lived,
developing, decaying, and moving cells. Removing the VT slot loses 2/30 reference
cells; a central five-slot gap loses 6/30; removing one entire side loses 12/30;
and retaining only VT loses 24/30. These numbers are synthetic diagnostics, not
empirical error rates. They prove the mechanism: a short-lived feature in a
missing slot disappears; persistence can protect a feature; one-sided sampling
loses developing or decaying evolution; and fast motion makes every observation
spatially informative.

Missing exact VT while retaining `VT-2` and `VT+2` is therefore not equivalent
to a broad hole around VT. Nor does the ±15-minute occurrence definition itself
prove that both sides are mandatory. Proximity, gap, and balance should remain
separate evidence until operational intent is documented.

## Historical degradation limitation

No historical truth-area, forecast-fraction, category, or miss degradation
results are reported. Performing leave-out experiments requires each paired
qualifying raster (or both product arrays) by actual time. Independent maxima
cannot be decomposed into them. The proper next dataset is a compact manifest
plus per-pair qualifying masks for a broader, reviewed sample; raw GRIBs need not
be committed.

Observation removal can nevertheless affect the pipeline nonlinearly. Lost
qualifying cells may shrink or split processed Sparse/Medium truth, push a
component below the unresolved 15,000 km² floor, change forecast overlap, or
prevent a candidate miss from existing. Miss detection may be especially
vulnerable because missing truth creates no candidate to review. Whether misses
require stricter support than scoring is a separate policy question; this
analysis creates no separate gate.

## Candidate quality framework

### States

* **Normal:** sufficient observational support for an automated first pass.
* **Review Required:** computation is possible, but timing, gaps, duplication,
  exclusions, or asymmetry may matter and require explicit review.
* **Insufficient Data:** the evidence cannot support an automated categorical
  claim; manual verification is required.

The states fit the existing architecture because calculation and provenance are
already separate and the meteorologist already reviews output. They should be
reproducible from provenance. No state is assigned here and no numerical rule is
approved.

### Policy-family matrix

| Family | Strength | Principal weakness |
|---|---|---|
| usable-count only | simplest and reproducible | ignores duplication and distribution |
| unique-count only | resists repeated-source inflation | ignores gaps and VT proximity |
| count + maximum gap | captures continuity | still misses balance/proximity; adds a cutoff |
| count + proximity + balance | temporally expressive | several correlated parameters |
| multi-metric three-state review | transparent uncertainty; matches workflow | needs evidence, escalation rules, and UI design |
| provenance-only review | no unsupported gate | permits categorical automation with plainly weak support |

The three-state family is preferred because an adequacy problem is not naturally
binary and because factual metrics are not interchangeable. Thresholds require
degradation evidence, operational expert judgment, and a larger range of real
missingness/duplication patterns. Official product cadence or latency, if later
documented, describes MRMS characteristics; it does not by itself establish a
TCF verification adequacy rule.

An official-source-only web search was attempted for NOAA MRMS cadence and
FAA/AWC TCF verification requirements, but the search service returned an
authorization error in this environment. No external requirement is therefore
claimed or used in the recommendation.

## Desirable invariants

1. Adding a usable independent observation cannot reduce adequacy.
2. Repeating one actual pair cannot simulate independent support.
3. A central block gap remains distinguishable from an isolated edge loss.
4. Near-VT support remains distinguishable from edge-only support.
5. Missing/unusable data never means observed non-convection.
6. Grid-incompatible pairs never count as usable.
7. Classification is reproducible from saved provenance alone.
8. Classification-sensitive facts remain reviewer-visible.
9. Reordering provenance records cannot change metrics.
10. Decision 1A qualification and adequacy classification remain separate:
    adequacy describes evidence supporting the union, not its meteorology.

## Decision 1B and coupled decisions

Decision 1B remains separately unresolved. Pair separation is an informative
provenance dimension, but the reported 0–1 second range in six events cannot
justify a permanent cutoff. This analysis introduces none.

Adequacy also couples to the unresolved area floor, missed-event eligibility,
and Solid LINE method because all consume the same observed truth. Those
policies are not reopened here. In particular, no missing slot is approximated
using independent maxima and no Decision 1A behavior changes.

## Evidence required

Before approving rules, retain scan-level provenance and paired masks across a
larger, meteorologically varied sample; deliberately include isolated gaps,
contiguous central gaps, one-sided windows, duplicate resolutions, product
failures, and grid exclusions. Run controlled leave-out experiments and measure
truth IoU/area, score/category changes, and misses. Then combine empirical
sensitivity with documented FAA/NWS/AWC operational intent and meteorologist
review. This evidence should calibrate state boundaries without turning ordinary
MRMS cadence documentation into an unsupported TCF policy.

## Scope preservation

The analysis utility is dependency-free and writes only compact CSV/JSON
artifacts. It performs no network access, modifies no frozen artifact, assigns no
quality state, and changes neither `tcf_pipeline.py` nor `app.py`.
