# cmac_domain.geojson

A single polygon that supplements `artcc1.geojson` for verification scoring. The
union of the two is the **verification domain**: truth outside it is discarded
before grading, so convection over Canada or open ocean can no longer be counted
as a missed forecast.

    48.0N, -90.0  ->  48.0N, -68.5  ->  47.0N, -68.5
              ->  43.0N, -70.0  ->  43.0N, -90.0  ->  close

## The northern edge is a judgement call

**48N is the scoring-relevant boundary, and it was traced by eye from a TCF
graphic. It is not an official shapefile and carries no authority.**

Every truth blob that straddles 48N has its geometry and measured reviewer area
reduced by the clip, so moving this line up or down can directly change scoring
and Candidate Miss context. Anyone auditing a scored event should
treat a miss near the northern edge as provisional. If an authoritative CMAC or
TCF domain boundary becomes available, replace this file rather than nudging the
coordinates: the intent is to be replaceable, not to be tuned.

## The southern edge is deliberately generous

43N overlaps ARTCC territory rather than abutting it. That overlap is the point.
Two independently drawn boundaries that merely touch leave slivers between them
where truth would fall through unscored, and those gaps are invisible in the
output — nothing reports "this blob was in neither polygon". Overlapping costs
nothing, because the domain is a union and the shapes are dissolved with
`buffer(0)`.

The same reasoning applies to the eastern edge stepping out to -68.5.

## What uses it

`tcf_pipeline.verification_domain()` unions this with `artcc1.geojson` and
caches the result for the life of the process. `extract_tcf_polygons` clips truth
to it. Its optional area argument remains for historical sensitivity analysis;
production scoring/review passes zero and applies no hard component-area floor.

Set `GradingParams(apply_domain_mask=False)` to score without it, which is how
the effect of the mask on a given event can be measured.
