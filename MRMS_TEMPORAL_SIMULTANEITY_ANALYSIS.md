# MRMS Temporal Simultaneity Analysis (Decision 1A)

> **Historical status:** This document records the pre-decision repository audit
> and its local network limitation. Decision 1A was subsequently approved using
> the six-event Colab evidence summarized in `DECISION_1A_EVIDENCE.md`. Its
> recommendation C and descriptions of the then-current production algorithm are
> retained as historical analysis, not current methodology.

**Scope:** read-only methodology analysis and attempted evidence recovery for the
six frozen historical events
**Decision under review:** whether reflectivity and echo-top criteria should be
qualified within each nominal observation pair before unioning over the existing
verification window  
**Production changes:** none

## Evidence-recovery attempt (2026-08-18 UTC)

The requested follow-up reconstruction was attempted from this environment using
the production anonymous S3 client, production product constants, production
`scan_offsets()`, and production `list_mrms_keys()`. The NOAA MRMS archive could
not be reached: the configured network proxy rejected the HTTPS tunnel with
`403 Forbidden`, which Botocore reported as `ProxyConnectionError: Failed to
connect to proxy URL: "http://proxy:8080"`.

This failure occurred while listing the first required archive prefix
(`MergedReflectivityQCComposite` for `20260324`). It happened before any archive
key could be resolved or any GRIB file could be downloaded. Because all six
events use the same S3 endpoint, continuing through the remaining prefixes would
only repeat the same endpoint-level failure; it would not provide slot-level
availability evidence.

The resulting reconstruction status is therefore:

| Event | Nominal slots planned | Slots resolved | Usable pairs recovered | Frozen-composite reproduction |
|---|---:|---:|---:|---|
| `20260324_13Z_F04` | 15 | 0 | 0 | Not testable |
| `20260403_21Z_F04` | 15 | 0 | 0 | Not testable |
| `20260524_13Z_F04` | 15 | 0 | 0 | Not testable |
| `20260524_19Z_F04` | 15 | 0 | 0 | Not testable |
| `20260524_19Z_F06` | 15 | 0 | 0 | Not testable |
| `20260728_19Z_F04` | 15 | 0 | 0 | Not testable |
| **Total** | **90** | **0** | **0** | **Not testable** |

The zeroes in this table describe **recovery in this execution environment**;
they do not assert that the historical MRMS observations are absent from NOAA's
archive. In particular, they must not be interpreted as missing-observation
counts, timing-separation statistics, or evidence about MRMS archive quality.

No substitute observations were used, no per-slot histories were fabricated,
and no raw MRMS files or raster caches were committed. Decision 1A consequently
remains unresolved under option C. The evidence-capture specification later in
this report remains the reproducible next action in a network-enabled environment.

## Executive conclusion

**Recommendation: C — the available evidence is insufficient and additional
per-observation data are required.**

The algorithmic concern is real and can be proved without additional data:
independent temporal maxima can create a qualifying cell even though no paired
observation ever satisfied both criteria. However, the frozen historical inputs
contain only the already-maximized reflectivity and echo-top fields. They do not
contain the individual observations or their provenance. Consequently, the
frequency, area, meteorological character, truth-field impact, grading impact,
and actual product-pair timing requested for this decision are not identifiable
from the repository's frozen sample.

This is not evidence that synthetic conjunctions are rare or harmless. It means
the archive is lossy with respect to the question being asked. Selecting A or B
from these artifacts would substitute an assumption for the requested empirical
comparison.

## Current algorithm

### Nominal observation slots

The configured window is 15 minutes with a two-minute cadence. The implementation
uses integer multiples of the cadence, so the requested offsets are:

```text
-14, -12, -10, -8, -6, -4, -2, 0, +2, +4, +6, +8, +10, +12, +14 minutes
```

Thus each event requests 15 nominal slots spanning 28 minutes. It does not request
observations at exactly -15 or +15 minutes. Across six frozen events, the nominal
plan would contain 90 slots.

For every slot, `build_composite()` resolves `EchoTop_18` and
`MergedReflectivityQCComposite` independently. `_resolve_scan_key()` chooses the
nearest archive timestamp for the requested product and returns no key if the
nearest observation is more than five minutes away. Therefore, the two products
assigned to one nominal slot can have different actual timestamps, different
requested-time offsets, and a nonzero cross-product separation.

A slot enters the composite only when both products resolve, download, decode,
and share the established composite grid. Adjacent nominal slots may resolve to
the same archived key; downloads are deduplicated, but each usable plan record is
still folded into the maximum. This duplication cannot change a maximum, though
it matters when interpreting nominal-slot counts as distinct observations.

### Independent temporal fold

For usable nominal slots \(i \in U\), the implementation computes, independently
at every grid location \(p\):

\[
R_{max}(p) = \max_{i \in U} R_i(p)
\]

\[
T_{max}(p) = \max_{i \in U} T_i(p)
\]

It then defines the raw qualifying core as:

\[
Q_{current}(p) = [R_{max}(p) \ge 40\ \mathrm{dBZ}]
                  \land [T_{max}(p) \ge 25\ \mathrm{kft}]
\]

The reflectivity and echo-top thresholds are therefore combined only **after**
the two fields have been independently maximized across the entire window. The
result is dilated, neighborhood-smoothed, thresholded at 25% and 40%, converted
to cell-footprint geometry, domain-clipped under the current policy, and filtered
by the existing minimum-area rule.

## Candidate algorithm

Decision 1A option B would retain the same nominal slots, resolved product pairs,
grid checks, thresholds, and downstream processing, but reverse the conjunction
and temporal-union operations:

\[
Q_i(p) = [R_i(p) \ge 40\ \mathrm{dBZ}]
         \land [T_i(p) \ge 25\ \mathrm{kft}]
\]

\[
Q_{paired}(p) = \bigvee_{i \in U} Q_i(p)
\]

This remains a window-based occurrence method: a cell may qualify at any usable
slot from -14 through +14 minutes. It does not require occurrence exactly at valid
time. It also does not settle Decision 1B; each \(R_i,T_i\) pair would use the same
two files currently associated with nominal slot \(i\), regardless of their
actual timestamp separation.

For finite, mutually aligned inputs:

\[
Q_{paired} \subseteq Q_{current}
\]

because a paired observation satisfying both criteria necessarily makes both
corresponding temporal maxima meet their criteria. Accordingly, **paired-only
pixels are mathematically impossible** in this controlled comparison. Any
observed paired-only pixel would indicate that some other input or processing
step differed.

## Frozen-data audit

The repository contains six frozen events:

| Event | Frozen per-slot arrays | Frozen provenance | Current qualifying cells | Percent of 980,000-cell grid |
|---|---:|---:|---:|---:|
| `20260324_13Z_F04` | No | No | 53 | 0.0054% |
| `20260403_21Z_F04` | No | No | 3,699 | 0.3774% |
| `20260524_13Z_F04` | No | No | 2,303 | 0.2350% |
| `20260524_19Z_F04` | No | No | 4,001 | 0.4083% |
| `20260524_19Z_F06` | No | No | 3,496 | 0.3567% |
| `20260728_19Z_F04` | No | No | 5,488 | 0.5600% |

The counts above are reproducible from the frozen `max_refl` and `max_tops`
arrays using the production thresholds. They describe only the size of
\(Q_{current}\); they are not estimates of synthetic conjunction.

Each `arrays.npz` contains exactly `max_tops`, `max_refl`, `lons`, and `lats`.
The event `expected.json` files contain verification outputs but neither scan
timestamps nor per-slot provenance. Although the current runtime provenance
layer records the necessary timestamps and exclusion reasons, those records were
not captured with these historical artifacts.

### Why the requested comparison cannot be reconstructed

Temporal maximum is a many-to-one transformation. For a single cell, both of
these histories produce the identical frozen values `max_refl=45` and
`max_tops=30`:

| History | Slot A (dBZ/kft) | Slot B (dBZ/kft) | Current | Paired |
|---|---|---|---|---|
| Contemporaneous | 45 / 30 | 20 / 20 | qualifies | qualifies |
| Synthetic conjunction | 45 / 20 | 20 / 30 | qualifies | does not qualify |

No operation on the two frozen maxima can distinguish those histories. The same
loss prevents recovery of the times supplying the maxima, their separation, or
the values of the other product at either time.

## MRMS timing characteristics

The requested timing statistics are **not computable from the frozen repository**.
This includes usable-pair counts, actual pair separations, missing-product counts,
incompatible-grid exclusions, and the minimum/median/mean/percentile/maximum
separation summaries. Reporting zeros would incorrectly imply complete,
timestamp-identical observations.

Likewise, the numbers of observations that hypothetical 1-, 2-, 3-, or 5-minute
Decision 1B limits would exclude are not computable. There is no empirical basis
in the frozen files for identifying outliers, a natural cutoff, or correlation
between product-pair separation and synthetic conjunction.

## Synthetic-conjunction analysis

Define:

\[
S = Q_{current} \setminus Q_{paired}
\]

The repository establishes the upper bounds \(|S| \le |Q_{current}|\) shown in
the event table and the lower bound \(|S| \ge 0\). Those bounds are too broad to
support a methodology decision. Exact counts, percentages, representative
locations, maximum-supplying times, and separation bins (at most 2, 2–5, 5–10,
and over 10 minutes) all require the missing per-slot fields.

Spatial characteristics are equally non-identifiable. Connected-component area,
adjacency to paired convection, and whether cells are storm-edge artifacts or
independent objects depend on the unknown mask \(Q_{paired}\), not merely on
\(Q_{current}\).

## Truth-field and verification impact

The candidate field cannot be propagated honestly from the frozen maxima.
Therefore the following requested differences cannot be calculated:

- Sparse and Medium truth masks, polygon counts, bounds, and EPSG:5070 areas;
- forecast coverage-fraction or marginal-flag changes;
- category crossings at 20% or 50%;
- current-only or paired-only misses;
- truth objects crossing the 15,000 km² floor; and
- FAA report-line changes.

Reusing `Q_current`, heuristically subtracting cells, or manufacturing per-scan
values consistent with the maxima would predetermine the result and would not be
an analysis of the historical events.

## Scientific interpretation

### Case for retaining independent maxima

- The window deliberately provides tolerance around valid time. Independent
  maxima create a permissive spatiotemporal envelope that can accommodate storm
  translation, fast growth or decay, and asynchronously generated MRMS products.
- A reflectivity maximum and an echo-top maximum separated in time can still
  describe the same evolving convective cell, even if neither nominal pair
  crosses both thresholds because of sampling cadence or product latency.
- Dilation and smoothing already make the truth field a neighborhood-coverage
  representation rather than a literal instantaneous storm footprint.

### Case for paired qualification

- The physical definition requires the reflectivity and echo-top attributes to
  characterize a qualifying convective state. Independent maxima distribute the
  existential quantifier separately over each criterion; that is logically
  weaker than requiring one observation pair to satisfy both.
- Independent maxima can combine developing, mature, and decaying states—or
  different translating echoes at the same grid point—and produce a condition
  that was never observed.
- Downstream dilation, smoothing, and minimum-area filtering can amplify or merge
  current-only pixels. A small raw difference could therefore become a retained
  truth object or an apparent miss; the frozen data cannot show whether this
  actually occurred.
- Pair-first qualification retains the full ±14-minute temporal tolerance. It
  removes only cross-slot conjunction, not the intended near-valid-time window.

Algorithmically, option B more directly represents “both required characteristics
occurred together.” Operationally, option A may be a deliberate tolerance. The
choice depends materially on whether the provable logical difference produces
minor boundary noise or substantial truth and grading changes in real cases—the
specific evidence missing here.

## Representative cases

No real historical pixel example can be supplied from the frozen inputs because
the time of either maximum and the paired values were discarded. The two-row
construction in the frozen-data audit is an analytic demonstration, not a claim
about any historical storm. Presenting it as a historical example would be
misleading.

## Decision 1A recommendation

**Choose C: evidence is insufficient and additional cases are required.**

Confidence is **high** that the current algorithm permits synthetic conjunctions
and **high** that the frozen sample cannot quantify them. Confidence is **low**
about their operational magnitude. Option B has the stronger direct physical
interpretation, but adopting it without the requested impact analysis would
violate the project's principle of resolving methodology choices explicitly and
empirically.

This recommendation is limited to Decision 1A. It does not select a maximum
actual reflectivity-to-echo-top separation for Decision 1B.

## Decision 1B inputs and required evidence capture

A bounded recapture of the same six events—not a climatology—is the minimum next
step. For every nominal slot it should preserve:

1. requested UTC time;
2. both resolved keys and actual archive timestamps;
3. requested-time offsets and cross-product separation;
4. download, decode, grid-compatibility, use, and exclusion states;
5. the aligned per-slot reflectivity and echo-top arrays, or a lossless equivalent
   sufficient to reproduce both thresholds and representative pixel histories;
6. file identifiers or hashes to make the analysis reproducible; and
7. whether adjacent nominal slots resolved to duplicate product keys.

A standalone analyzer can then compute both raw masks and feed each through the
same downstream functions while varying only the raw qualification mask. It
should produce all requested timing, pixel, component, truth, forecast, miss, and
report deltas, plus sensitivity exclusion counts at 1, 2, 3, and 5 minutes. Those
limits should remain descriptive until Decision 1B is considered.

## Confidence and limitations

The historical sample has six events, including two forecast hours for one issue
cycle. Even after recapture, it would be adequate for detecting large effects and
checking the analysis machinery, but not for robust warm-season generalization.
If the six-event results are small or mixed, a deliberately sampled archive set
covering developing, mature, decaying, translating, linear, sparse, and widespread
convection would materially improve confidence.

The conclusions supported now are deliberately separated:

- **Proved by algorithm:** independent maxima can create synthetic conjunctions;
  paired-only pixels cannot occur in an otherwise identical finite-input run.
- **Proved by repository audit:** the six frozen events retain maxima only and
  cannot answer timing or contemporaneity questions.
- **Not established:** historical frequency, spatial scale, truth impact,
  classification impact, miss impact, or a Decision 1B cutoff.

Until per-observation evidence is captured, the central question remains
empirically unanswered: the current method provides additional temporal
tolerance, but the available artifacts cannot determine whether that tolerance
creates materially significant TCF-qualifying convection that never jointly met
both criteria.
