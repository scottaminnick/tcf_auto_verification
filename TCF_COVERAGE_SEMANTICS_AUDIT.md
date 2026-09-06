# TCF Coverage-Code Semantics Audit

## Conformance rule

The current Aviation Weather Center TCF ASCII encoding is feature-specific:

| Feature | Code | Label | Coverage meaning |
|---|---:|---|---|
| AREA | 2 | Medium | 40–74% areal coverage |
| AREA | 3 | Sparse | 25–39% areal coverage |
| LINE | 1 | Solid | 75–100% linear coverage |

There is no current Dense AREA encoding. The parser now rejects AREA 1, LINE 2,
LINE 3, and every other feature/code combination rather than interpreting a code
without its feature type. This semantics correction does not approve or alter
the interim LINE verification calculation.

Production code records these definitions once in `SUPPORTED_TCF_COVERAGE`,
including the label, numerical range, and whether the range is areal or linear.
Labels and parser admission both use that feature/code-keyed source of truth.

## Six-event historical audit

All six frozen `tcf_raw.txt` products were parsed with the feature-specific
validation. Counts are accepted source records, before forecast geometries are
exploded for component-based grading.

| Event | AREA 2 | AREA 3 | LINE 1 | Invalid combinations |
|---|---:|---:|---:|---:|
| `20260324_13Z_F04` | 0 | 1 | 0 | 0 |
| `20260403_21Z_F04` | 1 | 5 | 1 | 0 |
| `20260524_13Z_F04` | 0 | 6 | 0 | 0 |
| `20260524_19Z_F04` | 0 | 12 | 0 | 0 |
| `20260524_19Z_F06` | 0 | 9 | 0 | 0 |
| `20260728_19Z_F04` | 3 | 10 | 0 | 0 |
| **Total** | **4** | **43** | **1** | **0** |

No frozen forecast record is newly rejected. A direct before/after comparison
confirmed identical accepted feature ordering, numeric coverage codes, feature
types, and Shapely geometries for every event.

The complete 48-feature detail is retained in
`baseline/tcf_coverage_feature_audit.csv`. Regenerate and summarize it with:

```bash
python baseline/audit_tcf_coverage.py \
  --csv baseline/tcf_coverage_feature_audit.csv
```

## Verification and report impact

Replaying both the immediately preceding implementation and this semantics-only
implementation against the same frozen forecast text and MRMS arrays produced:

* identical forecast coverage fractions and verification categories for all 48
  source forecast records;
* identical missed-feature counts for every event; and
* one intentional report-label change:

```text
20260403_21Z_F04
old: ZFW/ZKC - Dense (Line 4) [Top: 47.6 kft]
new: ZFW/ZKC - Solid (Line 4) [Top: 47.6 kft]
```

No historical expected artifact was updated. Consequently, the frozen baseline
for this event is expected to retain the obsolete `Dense` text until baselines
are deliberately versioned for an approved methodology milestone.

## Policy preservation

The correction changes parser admission and human-readable semantics only. It
does not change `LINE_BUFFER_DEG`, buffered LINE geometry, the 40% truth field
currently selected for LINE, area-overlap grading, 20%/50% category thresholds,
miss interaction, MRMS processing, or truth construction. Whether Solid LINE
forecasts should ultimately use a linear or other verification metric remains an
open methodology decision.
