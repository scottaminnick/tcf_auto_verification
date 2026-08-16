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
