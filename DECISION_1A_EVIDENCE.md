# Decision 1A Evidence: Pair-First MRMS Qualification

## Approved conclusion

Qualifying TCF convection requires reflectivity ≥40 dBZ and echo tops ≥FL250
within the same usable nominal MRMS observation pair. Per-pair qualifying masks
are combined by Boolean union over the existing approximately ±15-minute window.
Independent temporal maxima remain diagnostic fields and do not seed truth.

## Six-event experiment

The controlled Colab experiment used the six frozen historical events already
selected for methodology development and recovered all 90 nominal observation
pairs. It exactly reconstructed every frozen numerical maximum-reflectivity and
maximum-echo-top composite before comparing qualification methods.

| Metric | Result |
|---|---:|
| Historical events | 6 |
| Nominal observation pairs | 90 |
| Reflectivity/top pair separation | 0–1 seconds |
| Independent-max qualifying cells | 19,040 |
| Pair-first qualifying cells | 17,623 |
| Synthetic-conjunction cells | 1,417 |
| Synthetic fraction of independent field | approximately 7.44% |
| Forecast category changes | 1 (Verified Well → Verified Close) |
| Automated miss change | 2 fewer misses under pair-first qualification |

Synthetic-conjunction cells met the reflectivity criterion at one observation
and the echo-top criterion at another but never met both in the same nominal
pair. The mechanism is mathematically possible under independent maxima and was
therefore both analytically established and empirically observed.

## Interpretation and limits

The result separates two concepts: the verification window controls *when near
valid time* qualifying convection may occur, while pair-first conjunction
requires the physical characteristics to coexist in an observed state. Boolean
union preserves the former tolerance while preventing unobserved conjunctions.

Six events are not a climatology. They nevertheless demonstrated material truth,
classification, and miss effects, while the 0–1 second product separation showed
that the observed difference was not driven by materially asynchronous products
in this sample. Decision 1B—the acceptable pair timestamp separation—remains
unresolved and no threshold is inferred from these cases.

## Legacy artifact limitation

Existing frozen `arrays.npz` files contain only independent numerical maxima and
grid coordinates. They cannot reconstruct pair-first qualification. Their replay
must use the explicitly named legacy path; new approved-method captures must also
store the paired qualifying mask. Historical expected artifacts are unchanged.
