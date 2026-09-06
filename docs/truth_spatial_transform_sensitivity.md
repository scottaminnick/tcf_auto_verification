# Paired truth spatial-transform sensitivity audit

## Scope

This is read-only instrumentation for the Methodology 1.0 release-development meteorologist
review. It does not recommend further production parameters. Production now uses
one binary-dilation iteration, size-15 uniform smoothing, 25% Sparse truth,
and 40% Medium truth.

Size 15 on the approximately 0.05° geographic verification grid spans roughly
66 × 84 km at 37.5°N. The east-west physical width varies with latitude. The
owner selected it provisionally after the six-event paired numerical audit and
meteorologist spatial review; this sample is not climatological optimization.

The audit accepts only fresh paired evidence produced by:

```bash
python analysis/validate_methodology_1_0_paired.py --output /path/to/paired
```

Each immediate event directory must contain `arrays.npz`, `tcf_raw.txt`,
`mrms_provenance.json`, and `validation.json`. `arrays.npz` must include
`qualifying_mask`, `max_tops`, `max_refl`, `lons`, and `lats`, and the metadata
must identify the directory as `paired-validation-evidence-not-baseline` with at
least one usable pair. A legacy maxima-only capture is rejected; the tool never
fabricates a Decision 1A seed from `max_refl` and `max_tops`.

## Invocation

```bash
python analysis/truth_spatial_transform_sensitivity.py \
  --input-root /path/to/paired_validation_artifacts \
  --output /path/to/spatial_sensitivity_output
```

The grid is dilation `{0, 1}` by smoothing `{5, 10, 15, 20}`. Zero dilation
means the stored pair-first seed is passed through unchanged. All other
`GradingParams` values remain at the frozen Methodology 1.0 defaults.

## Outputs

* `event_summary.csv`: component counts/physical areas, review-cue counts,
  grades, and boundary counts for every event and parameter combination.
* `forecast_features.csv`: feature identity, coverage semantics, overlap,
  category, and boundary status.
* `candidate_miss_components.csv`: every low-capture Sparse component before
  area triage, including physical area, capture, centroid/bounds, embedded
  Medium context, and `eligible_candidate_miss`. This preserves future 5k,
  7.5k, 10k, and 15k comparisons without another MRMS run.
* `medium_components.csv`: every disconnected Medium component, physical area,
  capture, centroid/bounds, Sparse-parent context, raw low-capture status,
  5,000 km² eligibility, and duplicate-suppression status. Thus future Medium
  triage comparisons do not require another MRMS run.
* `summary.md`: grade changes relative to the Methodology 1.0 production default and a compact topology/review-cue
  table. It is descriptive evidence, not a replacement-parameter decision.

For the targeted April 3 review, filter the two component CSVs to
`event_id == 20260403_21Z_F04`; their centroid and bounds columns support direct
inspection of the central Plains, Iowa, Illinois, Indiana, and Ohio. Generated
binary plots are intentionally not committed. The application itself provides
interactive pair-first seed, post-dilation seed, Sparse, Medium, and forecast
layers over the diagnostic temporal-max background.

Projected physical calculations use a narrow topology safeguard. Canonical
EPSG:4326 truth is retained exactly; only invalid polygonal geometry produced by
projection to EPSG:5070 is repaired before area/intersection operations. Valid
projected geometry bypasses repair unchanged. This prevents rare GEOS topology
failures in fragmented sensitivity fields without changing the meteorological
transformation being evaluated.

The current reviewer hierarchy is deliberately separate from truth: Candidate
Misses require Sparse capture below 20% and area at least 7,500 km²; Medium-core
flags require Medium capture below 20% and area at least 5,000 km² and are
suppressed only by an eligible Candidate Miss parent. Neither area floor removes
Sparse or Medium scoring truth. In six paired development events, the earlier
1,500 km² Medium triage retained 12 flags; final release-candidate and independent September
4 visual review found it too permissive. The revised 5,000 km² floor retains 2
six-event flags while all 15 Candidate Misses remain unchanged. Sub-floor
components remain serialized. These are development evidence, not climatological
optimization or definitions of valid convection.
