# Methodology 1.0 Final Release Readiness Record

## Final status

**METHODOLOGY 1.0 RELEASE FREEZE COMPLETE.**

The integrated verification methodology is frozen at machine-readable version
`1.0`. The workflow remains mandatory human-reviewed decision support:
automated verification → meteorologist review/edit/approval → editable FAA draft
→ downstream slide preparation → final meteorologist review → publication.

The six historical events and independent September 4 case are release evidence,
not climatology or claims of scientific optimization.

## Frozen Methodology 1.0 behavior

Observed TCF convection requires reflectivity >=40 dBZ and echo tops >=FL250
within the same usable nominal MRMS pair. Per-pair masks are Boolean-unioned
across the verification window; independent numerical maxima are diagnostic only.

Production spatial defaults are Sparse 25%, Medium 40%, one dilation iteration,
and size-15 smoothing. Forecast-scoring truth has no component-area floor.
Physical area calculations use EPSG:5070.

Forecast categories remain:

* Verified Well >=50%;
* Verified Close >=20% and <50%;
* Overforecasted <20%.

Candidate Miss review requires strict capture <20% and Sparse area >=7,500 km².
Medium-core Review Flags require strict capture <20%, Medium area >=5,000 km²,
and no already-eligible Candidate Miss parent. Both area floors are reviewer
triage only and do not filter scored truth. Medium-core flags are never
reportable; Candidate Misses require explicit meteorologist approval before FAA
inclusion.

The current full-issued forecast denominator, class-blind Candidate Miss forecast
union, ARTCC+CMAC domain, one-usable-pair MRMS adequacy minimum, no additional
Decision 1B separation gate, interim buffered Solid LINE method, and reviewer-only
P90/six-cell echo-top diagnostic are accepted provisional Methodology 1.0
behaviors. They remain eligible for later evidence-based revision.

## Six-event official paired baseline

Every official event contains the exact frozen pair-first evidence package:
`arrays.npz` with `qualifying_mask`, exact `tcf_raw.txt`,
`mrms_provenance.json`, and a Methodology 1.0 `expected.json`.

Event results:

* 20260324_13Z_F04: 15 pairs; 1 forecast; 0 Well / 0 Close / 1 Over;
  0 Candidate Misses; 0 Medium flags; 0 boundary.
* 20260403_21Z_F04: 15 pairs; 7 forecasts; 1 Well / 2 Close / 4 Over;
  4 Candidate Misses; 1 Medium flag; 0 boundary.
* 20260524_13Z_F04: 15 pairs; 6 forecasts; 0 Well / 3 Close / 3 Over;
  5 Candidate Misses; 0 Medium flags; 0 boundary.
* 20260524_19Z_F04: 15 pairs; 12 forecasts; 1 Well / 4 Close / 7 Over;
  2 Candidate Misses; 1 Medium flag; 1 boundary.
* 20260524_19Z_F06: 15 pairs; 9 forecasts; 3 Well / 1 Close / 5 Over;
  2 Candidate Misses; 0 Medium flags; 0 boundary.
* 20260728_19Z_F04: 15 pairs; 13 forecasts; 0 Well / 7 Close / 6 Over;
  2 Candidate Misses; 0 Medium flags; 0 boundary.

Aggregate: 90 usable MRMS pairs, 48 forecasts, 5 Well, 17 Close, 26 Over,
15 Candidate Misses, 2 Medium-core Review Flags, and 1 boundary case.

The observational arrays and provenance were promoted byte-for-byte from the
previously frozen and meteorologist-reviewed paired evidence. Exact source hashes
are retained in `baseline/methodology_1_0_release_manifest.json`.

## Independent September 4 validation

Independent case `20260904_19Z_F04` used 15/15 usable MRMS pairs with zero
reflectivity/echo-top pair separation, compatible grids, and no parser
diagnostics.

It produced 11 forecasts: 1 Verified Well, 2 Verified Close,
8 Overforecasted, 4 Candidate Misses, and 0 Medium-core Review Flags.

Meteorologist visual review accepted the Candidate Miss inventory and truth
morphology. This supported the final 5,000 km² Medium-core reviewer floor.

## Final validation inventory

Release validation completed with:

* `git diff --check` — PASS;
* Python compile checks — PASS;
* methodology suite — 80/80 PASS;
* unit/audit discovery — 20/20 PASS;
* truth spatial sensitivity — 3/3 PASS;
* baseline fixture harness — 35/35 scenarios PASS;
* strict Methodology 1.0 six-event replay — 6/6 PASS; and
* Streamlit application/direct-pipeline stored pair-first parity — PASS.

Streamlit emitted non-fatal bare-mode/deprecation warnings about
`ScriptRunContext` and future `use_container_width` removal. These did not alter
verification results.

## Baseline and legacy separation

The six active repository baselines are Methodology 1.0 pair-first baselines.
Versioned artifacts cannot fall back to legacy independent-max replay.

`run_verification_legacy_independent_max()` remains intentionally available only
for old unversioned/maxima-only external or historical artifacts. Git history
preserves the superseded checked-in pre-1.0 baseline state.

## Post-1.0 research

Future methodology work may revisit Decision 1B timing semantics, denominator
treatment, coverage-class miss interaction, Solid LINE verification, observation
adequacy categories, and an FAA-suitable observed echo-top methodology.

None is a Methodology 1.0 release blocker. Any adopted result-altering change
requires a new methodology version, tests, and appropriately versioned baselines.
