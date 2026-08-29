# Paired truth spatial-transform sensitivity audit

## Scope

This is read-only instrumentation for the Methodology 1.0 RC1 meteorologist
review. It does not change or recommend production parameters. Production stays
at one binary-dilation iteration, size-20 uniform smoothing, 25% Sparse truth,
and 40% Medium truth.

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
`GradingParams` values remain at RC1 defaults.

## Outputs

* `event_summary.csv`: component counts/physical areas, review-cue counts,
  grades, and boundary counts for every event and parameter combination.
* `forecast_features.csv`: feature identity, coverage semantics, overlap,
  category, and boundary status.
* `candidate_miss_components.csv`: physical area, capture, centroid/bounds, and
  embedded Medium context for each individual Candidate Miss.
* `medium_components.csv`: every disconnected Medium component, physical area,
  capture, centroid/bounds, Sparse-parent context, and flag status.
* `summary.md`: grade changes relative to RC1 and a compact topology/review-cue
  table. It is descriptive evidence, not a replacement-parameter decision.

For the targeted April 3 review, filter the two component CSVs to
`event_id == 20260403_21Z_F04`; their centroid and bounds columns support direct
inspection of the central Plains, Iowa, Illinois, Indiana, and Ohio. Generated
binary plots are intentionally not committed. The application itself provides
interactive pair-first seed, post-dilation seed, Sparse, Medium, and forecast
layers over the diagnostic temporal-max background.
