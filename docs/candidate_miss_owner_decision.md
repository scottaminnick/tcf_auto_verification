# Candidate Miss owner decision — Methodology 1.0 RC1

## Approved behavior

The historical 15,000 km² exclusion is removed. Every disconnected Sparse
(25%+) component with forecast capture strictly below the still-provisional 20%
threshold is visible as a Candidate Miss. EPSG:5070 Sparse area and embedded
Medium (40%+) area/fraction are factual reviewer context, not eligibility gates.

Every disconnected Medium component is evaluated independently. A poorly
captured Medium component whose parent Sparse component is not a Candidate Miss
becomes a **Medium-core Review Flag**. A flag defaults unapproved and
non-reportable, is not counted as a Candidate Miss, and is unconditionally
excluded from FAA text. If the Sparse parent is already a Candidate Miss,
Medium density is represented on that candidate and no duplicate flag is emitted.

No replacement area threshold, distance merge, component consolidation, or new
density definition is introduced. Sparse remains 25%, Medium remains 40%, and
the `<20%` capture boundary remains provisional.

## Owner-supplied paired evidence

For `20260403_21Z_F04`, removing the floor exposed nine Sparse candidates. The
reviewed cases included an approximately 14,827.8 km² Ohio component with no
Medium core and an approximately 8,894.7 km² west-Texas component containing an
approximately 2,028.5 km² Medium core. Meteorologist review found the compact
Texas convection operationally meaningful.

The corrected six-event audit found 51 individual Medium components, 22 below
20% capture, and 10 poorly captured Medium components hidden by adequately
captured Sparse parents. This supports visibility, not a defensible Medium-area
cutoff; those components are flags rather than automatic misses.

## Release state

This is an approved RC1 owner decision. `METHODOLOGY_VERSION` remains
`1.0-rc1`; Methodology 1.0 is not frozen. Fresh paired evidence must be rerun
after implementation and the reviewer cues inspected before release.
