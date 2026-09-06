# Decision 1B: MRMS Within-Pair Temporal Compatibility

## Scope and recommendation

This is a read-only methodology analysis. It changes neither Decision 1A nor
production pairing, and it establishes no timestamp-separation threshold or
cycle rule.

**Recommendation: Option C — prefer authoritative common-analysis-cycle
identity as the scientific basis for pairing, with reviewer-oriented handling
where cycle identity is uncertain, but obtain timestamp semantics and wider
archive evidence before implementation.** A fixed sanity limit might eventually
supplement cycle identity, but no value is supported. Current no-gate behavior is
an acceptable evidence-preserving interim state, not an approved permanent
method.

## Current pairing algorithm

For nominal slot \(n\), production independently lists and resolves
`MergedReflectivityQCComposite` and `EchoTop_18`. For each product it selects the
archive filename timestamp nearest the nominal time, rejecting that product only
when the nearest candidate is more than 300 seconds away. It parses timestamps
to second resolution from the keys and records:

\[
o_r(n)=t_r(n)-n,\quad o_e(n)=t_e(n)-n
\]

and

\[
\delta_{pair}(n)=|t_r(n)-t_e(n)|.
\]

There is no test of \(\delta_{pair}\). A slot is excluded if either key cannot be
resolved or downloaded, either file cannot be decoded, the product grids differ,
or its grid differs from the established composite grid. Otherwise Decision 1A
computes `[reflectivity >= 40 dBZ] AND [echo top >= 25 kft]` for those two arrays
and Boolean-unions the result across slots.

Distinct keys are downloaded once, but every nominal plan record is evaluated.
If several slots resolve to the same pair, repeated qualification is idempotent.
Future compatibility could be evaluated once per unique source pair and copied
to each referring slot; evaluating it per slot should give the same scientific
answer, while retaining nominal-slot provenance.

### Three non-equivalent times

1. **Nominal verification-slot time** is the requested point in the configured
   `-14, -12, ..., +14` minute schedule.
2. **Filename time** is the UTC second-resolution timestamp parsed from each
   archive key.
3. **Underlying analysis/cube time** is the meteorological state represented by
   a derived product.

The repository calculates with (1) and (2). Neither repository evidence nor the
supplied external context proves that filename time has exactly the semantics of
underlying analysis time. Thus `filename time == analysis/cube time` is
**unproven**, and “different seconds” must not automatically be described as
different atmospheric states.

## MRMS architecture evidence

The following is **supplied authoritative context checked outside this Codex
environment**, not independently retrieved here:

* NOAA's operational GRIB2 table lists both products at two-minute frequency.
* NOAA/WDTD describes Echo Top and Composite Reflectivity as derived from the
  MRMS 3D Reflectivity Cube.
* operational filenames contain UTC timestamps with second resolution; and
* MRMS generates more than 100 mosaic products every two minutes.

This documents a shared source architecture and matched nominal frequency. It
strongly suggests that these are sibling derived products. It does **not**
guarantee that two independently resolved filenames represent one cube cycle,
define an operational cycle identifier, establish even-minute anchoring, or say
whether seconds encode validity, analysis, generation, or write time.

The hypothesis that processing order can give sibling products slightly
different filename seconds while representing one source state is **plausible
but unproven**. A documented same-cycle guarantee is absent from the supplied
evidence.

## Historical evidence

The task supplies additional detail for the external six-event Decision 1A
experiment: 90 nominal pairs comprised 88 exact timestamp matches and two pairs
separated by one second. The committed evidence document confirms only the 90
pair total and aggregate 0–1-second range; it does not contain the per-slot
manifest or the 88/2 breakdown.

This establishes that those six events had essentially coincident filename
times. It does not establish an archive distribution, outage/recovery behavior,
abnormal latency behavior, a permissible maximum, or whether the two one-second
cases shared one underlying cube state. Six selected events are not a basis for
a permanent cutoff.

## Pair separation versus nominal offset

Decision 1B and nominal resolution address different questions. If a nominal
23:00 slot resolves to 23:03:00 and 23:03:01, separation is one second while both
offsets are about three minutes. The pair may be mutually compatible yet provide
less support near the nominal time. Conversely, 22:58:30 and 23:01:30 both pass
the current five-minute resolver but are three minutes apart. They could
represent different cycles despite each being individually close to nominal.

Accordingly, cadence (two minutes), nominal resolver allowance (five minutes),
verification window (approximately ±15 minutes), and within-pair separation are
four different time scales. None supplies a threshold for another by analogy.

## Synthetic timing cases

`analysis/mrms_pair_timing.py` reproduces ten factual timing cases without
assigning acceptance:

| Case | Separation | Methodological observation |
|---|---:|---|
| exact match | 0 s | compatible under every plausible rule |
| historical-style jitter | 1 s | no supplied scientific basis for rejection |
| moderate differences | 30/60 s | consequences depend on timestamp/cycle semantics |
| near one cadence | 119 s | cycle membership cannot safely be inferred from magnitude |
| beyond one cadence | 121 s | potentially concerning, but cadence is not an approved limit |
| mutually close, nominally late | 1 s at +4 min | pair compatibility does not imply ideal nominal support |
| individually close, cross-pair | 180 s | load-bearing consequence of independent nearest selection |
| preceding/following candidates | 120 s | can associate neighboring source times |
| one product missing | undefined | Decision 1A excludes it; 1B has no pair to assess |

No same-cycle field is synthesized. Every row marks cycle identity unknown and
Decision 1B acceptance unassigned.

## Meteorological failure mechanisms

Large genuine analysis-time differences can reintroduce temporal conjunction at
a smaller scale than the full verification window:

* **intensification:** early 45 dBZ/FL220 and later 45 dBZ/FL300 can make a
  later top appear simultaneous with earlier reflectivity;
* **decay:** an earlier high top can be combined with later reflectivity after
  the joint state has ceased;
* **motion:** reflectivity and top footprints can move, producing false overlap
  or suppressing a real co-located state; and
* **threshold crossing:** small evolution around 40 dBZ or FL250 can flip the
  conjunction discontinuously.

These examples establish why 1B matters; they do not establish a universal safe
number. Decision 1A rejected allowing different window-scale states to supply
the two criteria. Decision 1B must eventually prevent the nominal-pair label from
silently recreating that failure, while not rejecting normal same-cycle product
jitter.

## Candidate policies

| Candidate | Advantages | Evidence gap / risk |
|---|---|---|
| A. no gate | preserves data; no invented parameter | unusual asynchronous files can undermine 1A |
| B. fixed \(\delta\le P\) | deterministic, testable, provenance-native | unsupported P; hard discontinuity; timestamp may be processing time |
| C. same-cycle | closest to meteorological source identity; tolerates generation jitter | cycle identity and timestamp semantics undocumented |
| D. cycle + sanity limit | guards misassociation/outages while respecting cycles | needs both authoritative semantics and a justified limit |
| E. reviewer classification | exposes uncertainty and fits human review | does not alone prevent an asynchronous automated conjunction |

The preferred structure is C, potentially combined later with D/E: pair by an
authoritative common cycle; use a documented sanity check if necessary; and
surface ambiguous/outlier cases for review. It is not implementable defensibly
until cycle semantics are known. Merely rounding timestamps to even minutes
would invent an unsupported cycle mapping and is rejected as an analysis method.

Current provenance-only behavior avoids false precision and is acceptable while
evidence is gathered. It must not be interpreted as proof that all separations
within the existing five-minute nominal resolver are compatible.

## Same-cycle and archive evidence plan

A future metadata-only sampler should list keys for both products without
downloading GRIB grids, parse filename times, and retain nominal request, both
actual times, both nominal offsets, separation, missingness, duplicate keys, and
an authoritative cycle identifier if one becomes available. Sampling should be
stratified by season/year, convective and nonconvective regimes, UTC rollover,
known outages/recovery, and random ordinary periods. Sample size should follow
the rarity of outliers to be estimated rather than an arbitrary fixed count.

Report the full distribution and especially maximum, 95th/99th percentiles,
frequencies above 1/5/10/30/60/120 seconds, missing siblings, and apparent
neighbor-cycle cases. Percentiles characterize operations; they do not directly
become policy thresholds. No archive collection was performed because this task
supplies authoritative context but not authenticated archive access or a cycle
definition.

Minimum authoritative follow-up questions for NOAA/NSSL MRMS documentation or
subject-matter experts are:

1. Do both products use the same 3D Reflectivity Cube cycle?
2. What precisely does each filename timestamp mean?
3. Is seconds-level divergence expected for sibling outputs from one cycle?
4. Is a cycle identifier available, and how is it encoded?
5. Can outage or latency make sibling timestamps diverge beyond one cadence?
6. Is there an official multi-product temporal-association practice?

## Policy invariants

1. Exact timestamp matches are accepted.
2. Documented normal generation jitter within one cycle is not rejected.
3. Products representing materially different states do not jointly qualify.
4. Pairing cannot reintroduce Decision 1A's rejected temporal conjunction.
5. Missing products are not replaced with asynchronous data merely to form a pair.
6. Compatibility is reproducible from provenance and independent of slot order.
7. Threshold/cycle-sensitive cases remain reviewer-visible.
8. Availability, grid compatibility, and temporal compatibility remain distinct.

## Relationship to observation adequacy

Decision 1B is pair-level: whether two products may jointly contribute one mask.
Observation adequacy is event-level: whether the collection of usable pairs
supports an automated result. A compatible pair can exist in an event that is
still `Review Required`; rejecting a pair under a future 1B rule would reduce
usable/unique support and could degrade event adequacy. Neither decision should
be collapsed into the other.

## Specification status and evidence needed

Decision 1B remains open. Before implementation, obtain authoritative timestamp
semantics, operational cycle identity, expected sibling-product jitter, archive
outlier behavior, and guidance for outage/recovery association. Then test the
rule against evolving-storm synthetic invariants and a wider metadata sample.
The supplied 88/2 result supports accepting ordinary seconds-level coincidence
conceptually, but cannot define a boundary.

## Scope preservation

No production source, resolver, warning, exclusion, threshold, cycle matching,
quality state, Decision 1A behavior, report, or frozen baseline was changed.
