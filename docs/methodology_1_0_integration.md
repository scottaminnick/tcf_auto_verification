# Methodology 1.0 integration candidate

## Status and identity

**READY FOR FRESH PAIRED VALIDATION.** The candidate branch is
`methodology/1.0-integration`, based on `5f0c615` (`fix: apply Methodology 1.0
owner decisions`). Its machine-readable identity is `1.0-rc1`. This is not a
final Methodology 1.0 baseline identity: paired historical evidence and human
review must precede the freeze.

The separately cited Decision 1A SHA `63494ed` is not present in the local
object database and no remote is configured. It was therefore not cherry-picked.
The current implementation was audited semantically against the specification,
tests, and evidence instead.

## Approved behavior inventory

The integrated executable path contains all approved corrections:

- [x] each usable MRMS pair is jointly thresholded at 40 dBZ and FL250, then
  masks are Boolean-unioned; duplicates are idempotent;
- [x] numerical maxima remain separate diagnostics; production verification
  requires `qualifying_mask`, while a clearly named independent-max function is
  restricted to legacy replay;
- [x] actual source timestamps, nominal offsets, pair separation, availability,
  grid compatibility, use, and exclusions are retained as provenance;
- [x] physical area uses EPSG:5070; polygonization uses cell footprints,
  four-neighbor connectivity, and topology-preserving geometry;
- [x] echo-top sampling respects complete source geometry, holes, multipart
  features and nullability; fewer than six valid qualifying cells returns
  `None`, while a genuine numeric zero remains numeric;
- [x] parser semantics are feature-aware (`AREA 2 = Medium`, `AREA 3 = Sparse`,
  `LINE 1 = Solid`) and invalid combinations are diagnostically excluded;
- [x] forecast-scoring Sparse and Medium truth have no 15,000 km2 floor;
- [x] the provisional 15,000 km2 and 20% rules are isolated to Candidate Miss
  triage;
- [x] Candidate Miss rows default to `approved_for_report = False`; only an
  explicit reviewer edit permits an FAA `Missed` line, and revocation removes it;
- [x] forecast rows default approved, nullable review-table dtypes round-trip,
  and unavailable/nonpositive echo tops produce no report annotation.

## Provisional behavior preserved

No policy was changed for Decision 1B (no hard pair-time gate), MRMS adequacy
(provenance but no numerical state thresholds), Solid LINE (0.15-degree buffer,
Medium/40% truth and area-overlap scoring), the full-issued forecast denominator,
or post-domain Candidate Miss filtering. Candidate Miss triage remains
Sparse-only, class-blind, 15,000 km2, and below 20% captured. Echo tops remain
full-forecast-geometry, temporal-maximum P90 with a six-cell minimum. These are
documented provisional behaviors, not newly approved rules.

## Decision 1A equivalence and test-set audit

`build_composite` resolves two products independently for every nominal slot,
excludes missing/download-failed/incompatible pairs, applies joint thresholding
per usable pair, and folds with Boolean OR. It retains numerical maxima in
parallel. `run_verification` has a keyword-only required mask and validates its
shape. Baseline capture stores the mask. Tests cover false temporal conjunction,
union, missing pairs, duplicate resolution, grid incompatibility, and the
required-mask seam. This establishes behavioral equivalence without duplicating
an unavailable commit.

The integrated test set also covers physical area, raster footprints,
connectivity/topology/holes, feature-aware parsing, nullable echo tops,
sub-15,000 km2 scoring truth, Candidate Miss triage isolation, and candidate
approval/add/revoke behavior. AppTest is application-to-pipeline parity only; it
is not independent scientific validation and intentionally uses a labeled
legacy fixture seam.

## Static and dataflow audit

The independent-max conjunction appears in production code only inside
`run_verification_legacy_independent_max`; other occurrences are tests or
analysis. A bare `coverage == 1` remains only in read-only miss analysis. `Dense`
appears only in a negative test. `min_area_m2=0` is used for scored truth and the
configured floor only for Candidate Miss extraction. Every report-generation
path calls `build_report`, which filters `approved_for_report` before formatting.

Integrated dataflow is:

1. `parse_iem_cow_text` parses raw TCF and records structural exclusions.
2. `build_composite` produces paired provenance, diagnostic maxima, and the
   Decision 1A `qualifying_mask`.
3. `run_verification` constructs scoring truth without an area floor and grades
   forecast fractions/categories.
4. The same function separately extracts provisional Candidate Misses using the
   triage floor/capture rule.
5. `build_review_table` defaults forecasts to approved and candidates to not
   approved.
6. Streamlit retains the editable table; `build_report` reads the current table
   and cannot emit an unapproved Candidate Miss.

## Methodology 1.0 baseline schema

A new candidate artifact is valid only when all of the following are present:

- `expected.json`: `methodology_version`, event ID/date, issuance, lead and valid
  time, forecast categories/fractions/tops, Candidate Misses, default report,
  and counts;
- `arrays.npz`: `qualifying_mask`, `max_refl`, `max_tops`, `lons`, and `lats`;
- `mrms_provenance.json`: per-nominal-slot requested time, both product keys and
  parsed actual timestamps, nominal offsets, pair separation, availability,
  download/use/grid state, exclusion reason, plus aggregate counts/maxima;
- `tcf_raw.txt`: exact forecast source text required for deterministic parsing.

`baseline/check.py` rejects a versioned artifact missing either the paired mask
or provenance manifest. Unversioned maxima-only artifacts remain explicitly
legacy and are announced before invoking the named legacy replay function.

## Fresh paired validation

No network collection or baseline regeneration was performed during assembly.
Run the six-event evidence capture outside `baseline/`:

```bash
python analysis/validate_methodology_1_0_paired.py \
  --output /path/to/reviewable/paired-validation
```

The utility downloads real paired products, saves compact arrays/provenance and
results, marks them `paired-validation-evidence-not-baseline`, and retains no raw
GRIB. Before baseline capture, require all six events to reconstruct, provenance
and real masks to exist, used grids to be compatible, all integrated tests to
pass, outputs to be meteorologically inspectable, and every surprising score,
category, or Candidate Miss change to receive human review. Stale report parity
is not an acceptance condition.

## Remaining release steps

1. Run and meteorologically review the six real paired cases.
2. Resolve anomalies without fabricating masks or using legacy fallback.
3. Perform a human visual smoke test of Candidate Miss approval/revocation and
   regenerated FAA draft text.
4. Freeze the identifier as Methodology 1.0, then capture versioned baselines.
5. Re-run full regression/application parity and open the final release PR.
