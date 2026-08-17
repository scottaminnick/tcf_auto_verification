# Baseline verification harness

Before-pictures of the TCF verification pipeline as it behaves *today*, so a
refactor can be shown to change nothing.

The pipeline lives in **`tcf_pipeline.py`** at the repo root — one
implementation, imported by both `app.py` and `capture.py`. It used to exist
twice (inline in app.py, transcribed into capture.py); that transcription is
gone, because a harness whose reference is a second hand-maintained copy of the
code proves nothing about the code.

    app.py               thin Streamlit cache wrappers + all display code
    baseline/capture.py  EVENTS + serialisation of a run to disk
    tcf_pipeline.py      the math, the parsing, the report text

`tcf_pipeline.py` never imports streamlit: it raises instead of calling
`st.stop()`, and progress goes to stderr or to a caller-supplied `log` callback
(which is how app.py keeps its per-scan status lines). It is deliberately
bug-for-bug faithful to the original — the 14 known defects are catalogued at
the bottom of that file and **none of them are fixed**. Fixing one would change
the numbers, and the numbers are the point.

## Layout

```
tcf_pipeline.py      the shared pipeline (no streamlit)
baseline/
  capture.py         freezes each event to disk (needs network)
  check.py           replays the frozen inputs and diffs (never touches network)
  test_fixture.py    tests the harness itself against synthetic data
  test_app_parity.py drives app.py and diffs its report against the baseline
  <event_id>/
    arrays.npz         max_tops, max_refl, lons, lats — the rolling MRMS composite
    tcf_raw.txt        the raw IEM response for the TCF product
    expected.json      graded output: metadata, report_text, polygons, misses, counts
    pass_a_report.txt  report text captured from the live app (see below)
```

## Events

| event_id | date | issuance | lead | why it is in the set |
|---|---|---|---|---|
| `20260524_19Z_F04` | 2026-05-24 | 19Z | 4 | primary dev case |
| `20260524_19Z_F06` | 2026-05-24 | 19Z | 6 | lead plumbing (CFP03 instead of CFP02) |
| `20260524_13Z_F04` | 2026-05-24 | 13Z | 4 | issuance plumbing |
| `20260728_19Z_F04` | 2026-07-28 | 19Z | 4 | external anchor |
| `20260403_21Z_F04` | 2026-04-03 | 21Z | 4 | LINE features + UTC day rollover |
| `20260324_13Z_F04` | 2026-03-24 | 13Z | 4 | sparse/empty paths |

`20260403_21Z_F04` is valid at **01Z on 2026-04-04**. `compute_valid_dt()` rolls
the date forward, and `download_mrms_scan()` builds its S3 prefix from the scan
datetime (`valid_dt + offset`), so that event's MRMS keys come from
`CONUS/<product>_00.50/20260404/` — the scan date, not the issuance date.

## Capturing

Capture runs **inside the repo's own Dockerfile**, against the pinned
`requirements.txt` that Railway deploys. A baseline captured against a different
scipy or shapely build is not a baseline.

```sh
make capture                            # every event in capture.py's EVENTS
make capture EVENT=20260524_19Z_F04     # just one
```

That is `docker build -t tcf-baseline .` followed by

```sh
docker run --rm \
  -v "$PWD/baseline:/app/baseline" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  tcf-baseline python baseline/capture.py [EVENT_ID...]
```

The bind mount is what makes the captured files land on the host; without it
they die with the container. Capture needs outbound network to
`mesonet.agron.iastate.edu` (IEM archives) and the public `noaa-mrms-pds` S3
bucket. `make capture-shell` drops into the same image interactively when a
capture fails and you want to poke at it.

Nothing else in this harness needs Docker or network.

## Checking

```sh
make check          # replay tcf_pipeline + diff against the baselines
make check-pass-a   # also require byte-exact pass A reports
make fixture        # test the harness itself
make parity         # drive app.py and diff its report
make test           # all three
```

`--pipeline MODULE` is **mandatory** (it defaults to `tcf_pipeline` in the
Makefile) and every symbol — `compute_valid_dt`, `parse_iem_cow_text`,
`run_verification`, `load_artccs`, `get_artccs` — must resolve inside that one
module. There is no candidate list and no fallback.

The fallback used to exist and was actively harmful: mid-refactor, a symbol that
had been moved or renamed would quietly resolve against the frozen transcription
instead, and the run would go green by comparing `capture.py` to itself. A
missing symbol is now a hard error naming the symbol.

### What check.py cannot see

`check.py` replays `tcf_pipeline` directly, so it covers the math but not
app.py's own glue — the widget-to-argument wiring, the `log=st.write` progress
seam, and the `session_state` stash the render functions read. A refactor that
broke only the glue would leave `check.py` green and the dashboard wrong.

`make parity` closes that gap: it drives `app.py` through streamlit's `AppTest`
with the two network calls fed from the frozen baseline, clicks *Run
Verification*, and requires the report the app ends up holding to equal
`expected.json`'s `report_text` byte for byte.

### Report building is two stages

`run_verification` no longer formats text from geometry in one pass:

    build_review_table(gdf_graded_fcst, gdf_graded_miss, gdf_artcc) -> DataFrame
    build_report(review_table, valid_dt, issuance_hour, lead_time)  -> str

The table is one row per graded polygon and per miss, carrying everything the
report is derived from — idx, kind, category, coverage code, feat type, ARTCCs,
coverage fraction, top and the boundary flag — in plain nullable pandas dtypes
with no geometry, so it can round-trip through `st.data_editor`. All the
geometry work and the ARTCC lookups happen in the first stage; the second only
formats. That gap is where an editable review table drops in.

`run_verification` returns the table as `results['review_table']` alongside the
report, and `make fixture` proves the seam is real by editing a category, an
ARTCC and a top in the frame and requiring the rebuilt report to follow. If
editing the table did not change the report, the split would be decorative.

### Tuning parameters

The grading knobs live in `tcf_pipeline.GradingParams`, a frozen dataclass whose
defaults are exactly the values that used to be hardcoded — truth thresholds
(0.25 sparse / 0.40 medium), grade cutoffs (0.50 / 0.20), `binary_dilation`
iterations (1), `uniform_filter` size (20), the 0.20 miss-capture threshold and
the 15,000 km² minimum truth area. `run_verification(..., params=GradingParams())` is the only entry point;
app.py, capture.py and check.py all pass nothing and get the frozen behaviour.

The baselines encode the defaults, so `make check` will correctly go red for any
other value — that is the point, not a limitation. To try a setting, pass a
`params=` explicitly rather than editing the defaults.

Still deliberately **not** parameters: the echo-top bands (25/30/35/40 kft) and
the 40 dBZ convection floor, which are being corrected in their own step so the
diff stays inspectable.

`miss_capture_threshold` is its own field rather than a reuse of
`verified_close_cutoff`. They hold the same 0.20 today but answer opposite
questions — how much of a *truth blob* the forecast captured, versus how much of
a *forecast* truth filled — so they have to be able to move independently.

`make fixture` proves the parameters are wired through rather than accepted and
ignored: it lowers a grade cutoff and requires a polygon to change category, and
separately gives every field an extreme value and requires the output to move.

### Comparison tolerance

Floats are stored rounded (coverage fractions and bounds to 4 dp, echo tops to
2 dp) and compared with a tolerance of one unit in that last stored decimal, so
ordinary rounding noise is quiet. `--strict` demands exact equality.

Any polygon whose `coverage_fraction` lands within **0.005** of a grade cutoff
(0.50 or 0.20) is tagged `"boundary": true`. Such a polygon can flip category on
nothing worse than a BLAS change, so when its category moves, `check.py` reports
the fraction delta, the category change, *and* a note that this one is expected
float sensitivity rather than a regression. Polygons away from a cutoff carry no
flag, and a category change there is a real finding.

## Pass A reports

`pass_a_report.txt` is the report text taken **out of the running Streamlit
app** for that event, via the *Pass A* download button on the scorecard.
`capture.py` never writes it: a copy written there would just be
`expected.json`'s `report_text` under a second filename.

Its job is to catch the headless pipeline drifting away from what the live
dashboard renders.

One caveat worth knowing: the download button serves `R['report_text']`, the
same string the pipeline produced, so a pass A file captured that way is only as
independent as the app's rendering path. It still catches app-side drift after
the fact — if app.py later diverges, re-downloading produces a file that no
longer matches the frozen `expected.json` — but it is not an independent
transcription of what a human read on screen. `make parity` is the stronger
check on that path.

```sh
python baseline/check.py --pipeline tcf_pipeline --pass-a  # replay + pass A
python baseline/check.py --pass-a-only                     # pass A alone, no pipeline needed
```

The comparison trims trailing whitespace from the end of both sides first — a
final newline added or dropped when a file is saved is an editor artifact, not a
difference in the report. Everything after that trim is byte-for-byte: no
per-line whitespace normalisation, no CRLF fixups, no case folding. A stray CR
inside the text, a trailing space on a mid-report line, an extra blank line
between sections, or a truncated paste all still fail.

Failures print the byte offset, line and column of the first difference
alongside a unified diff, because otherwise an invisible difference is
unfindable.

To record one: run the event in the live app, use the *Pass A* download button
under the FAA report panel, save the file as
`baseline/<event_id>/pass_a_report.txt`, then run `--pass-a`.

## Cadence v1 -> v2, and the pass A files

`baseline/` is now **v2**: 2-minute cadence, 15 scans, 30 files per composite.
The 5-minute set it replaced is frozen in `baseline_v1_5min/` and must not be
regenerated — it is the before-picture for this change.

`make check` is 6/6 against v2. **`make check-pass-a` is red for 5 of 6 events,
and that is correct**: `pass_a_report.txt` holds the report a human downloaded
from the live app under the 5-minute cadence, and the 2-minute pipeline produces
a different report. Those files are the one artefact this repo must not
regenerate itself — a machine-written copy would make the check vacuous. Re-run
each event in the dashboard, use the *Pass A* button, and replace the file.

`20260324_13Z_F04` still passes pass A because its graded output did not change.
