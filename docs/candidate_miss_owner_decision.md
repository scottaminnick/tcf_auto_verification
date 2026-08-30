# Candidate Miss owner decision — Methodology 1.0 RC1

## Approved behavior

The historical 15,000 km² exclusion is removed from truth and Candidate Miss
logic. Following paired sensitivity and meteorologist review, a disconnected
Sparse (25%+) component is a Candidate Miss only when forecast capture is
strictly below the still-provisional 20% threshold and EPSG:5070 area is at
least 7,500 km². Equality is eligible. The new floor is reviewer-inventory
triage, not a TCF definition and never filters scored Sparse/Medium truth.

Every disconnected Medium component is evaluated independently. A poorly
captured Medium component with EPSG:5070 area at least 1,500 km² whose parent
Sparse component is not an eligible Candidate Miss becomes a **Medium-core
Review Flag**. A flag defaults unapproved and
non-reportable, is not counted as a Candidate Miss, and is unconditionally
excluded from FAA text. If the Sparse parent is already a Candidate Miss,
Medium density is represented on that candidate and no duplicate flag is emitted.

The 1,500 km² Medium threshold is reviewer triage only, not a truth filter or a
valid-convection definition. No distance merge, component consolidation, or new
density definition is introduced. A low-capture Sparse parent below 7,500 km²
does not suppress an otherwise eligible Medium-core flag. Sparse remains 25%,
Medium remains 40%, and the `<20%` capture boundary remains provisional.

The six paired development events contained 15 Candidate Misses and 24 raw
Medium-core flags (39 cues). The 1,500 km² floor retained 12 Medium flags and 27
total cues. These six events support triage practicality, not climatological
optimization.

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
