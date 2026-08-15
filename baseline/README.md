# Baseline verification harness

Before-pictures of the TCF verification pipeline as it behaves *today*, so a
refactor can be shown to change nothing.

The pipeline currently lives inline in the `if st.sidebar.button("Run
Verification"):` block of `app.py`. `capture.py` is a Streamlit-free
transcription of that block, deliberately bug-for-bug faithful — the 14 known
defects are catalogued at the bottom of `capture.py` and **none of them are
fixed here**. Fixing one would change the numbers, and the numbers are the
point.

## Layout

```
baseline/
  capture.py         freezes each event to disk (needs network)
  check.py           replays the frozen inputs and diffs (never touches network)
  test_fixture.py    tests the harness itself against synthetic data
  <event_id>/
    arrays.npz         max_tops, max_refl, lons, lats — the rolling MRMS composite
    tcf_raw.txt        the raw IEM response for the TCF product
    expected.json      graded output: metadata, report_text, polygons, misses, counts
    pass_a_report.txt  report text hand-copied from the live app (see below)
```

## Events

| event_id | date | issuance | lead | why it is in the set |
|---|---|---|---|---|
| `20260524_19Z_F04` | 2026-05-24 | 19Z | 4 | primary dev case |
| `20260524_19Z_F06` | 2026-05-24 | 19Z | 6 | lead plumbing (CFP03 instead of CFP02) |
| `20260524_13Z_F04` | 2026-05-24 | 13Z | 4 | issuance plumbing |
| `20260728_19Z_F04` | 2026-07-28 | 19Z | 4 | external anchor |
| `20260403_21Z_F04` | 2026-04-03 | 21Z | 4 | LINE features + UTC day rollover |
| `20260324_05Z_F04` | 2026-03-24 | 05Z | 4 | sparse/empty paths |

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
make check PIPELINE=tcf_core            # replay + diff against the baselines
make check-transcription                # replay through baseline/capture.py itself
make check-pass-a PIPELINE=tcf_core     # also require byte-exact pass A reports
make fixture                            # test the harness itself
```

`--pipeline MODULE` is **mandatory** and every symbol
(`compute_valid_dt`, `parse_iem_cow_text`, `run_verification`, `load_artccs`,
`get_artccs`) must resolve inside that one module. There is no candidate list
and no fallback to `baseline.capture`.

The fallback used to exist and was actively harmful: mid-refactor, a symbol that
had been moved or renamed would quietly resolve against the frozen transcription
instead, and the run would go green by comparing `capture.py` to itself. A
missing symbol is now a hard error naming the symbol.

`make check-transcription` is the one place `baseline.capture` is legitimate —
it proves the stored arrays and `expected.json` are self-consistent. It proves
nothing about a refactored pipeline.

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

`pass_a_report.txt` is the report text **hand-copied out of the running
Streamlit app** for that event. It is the only artefact in the directory that a
human produces, and `capture.py` will never write it — a machine-generated copy
would just be `expected.json`'s `report_text` under a second filename and would
prove nothing.

Its job is to catch the failure mode this harness cannot otherwise see: the
headless transcription drifting away from what the live dashboard actually
renders.

```sh
python baseline/check.py --pipeline tcf_core --pass-a   # replay + pass A
python baseline/check.py --pass-a-only                  # pass A alone, no pipeline needed
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

To record one: run the event in the live app, copy the FAA report panel verbatim
into `baseline/<event_id>/pass_a_report.txt`, then run `--pass-a`.
