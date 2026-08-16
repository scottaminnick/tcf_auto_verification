#!/usr/bin/env python3
"""Prove app.py still produces the baseline report, through the real Streamlit run.

baseline/check.py replays tcf_pipeline directly. That covers the math but not
app.py's own glue -- the widget-to-argument wiring, the progress `log=st.write`
seam, and the session_state stash the render functions read. A refactor that
broke only the glue would leave check.py green and the dashboard wrong.

So this drives app.py through streamlit's AppTest with the two network calls
stubbed out by the frozen baseline inputs, clicks "Run Verification", and
asserts the report text the app ends up holding equals the event's
expected.json report_text byte for byte.

The app's default widget values (2026-05-24, 19Z, FH 4) are exactly the
20260524_19Z_F04 event, so no widget manipulation is needed.

Requires streamlit; skips cleanly (exit 0) if it is not installed, since the
rest of the harness deliberately runs without it.

    python baseline/test_app_parity.py

Exit status: 0 = parity (or skipped), 1 = the app diverged from the baseline.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

EVENT_ID = "20260524_19Z_F04"
EVENT_DIR = os.path.join(REPO_ROOT, "baseline", EVENT_ID)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    print("SKIP: streamlit is not installed (the rest of the harness does not need it)")
    sys.exit(0)

if not os.path.exists(os.path.join(EVENT_DIR, "expected.json")):
    print(f"SKIP: no captured baseline at {EVENT_DIR}")
    sys.exit(0)

import numpy as np  # noqa: E402
import tcf_pipeline  # noqa: E402

with open(os.path.join(EVENT_DIR, "expected.json"), encoding="utf-8") as f:
    expected = json.load(f)
with open(os.path.join(EVENT_DIR, "tcf_raw.txt"), encoding="utf-8") as f:
    raw_text = f.read()
with np.load(os.path.join(EVENT_DIR, "arrays.npz")) as npz:
    frozen = (npz["max_tops"], npz["max_refl"], npz["lons"], npz["lats"])

# Feed the app the frozen inputs instead of IEM/S3. Only the two network-bound
# functions are replaced; every line of grading and report logic runs for real.
scan_log = []


def fake_fetch(date_obj, issue_hr, f_hr):
    assert (issue_hr, f_hr) == (expected["issuance_hour"], expected["lead_time"]), \
        f"app.py passed the wrong issuance/lead: {(issue_hr, f_hr)}"
    return tcf_pipeline.parse_iem_cow_text(raw_text)


def fake_composite(valid_dt, log=None):
    assert valid_dt.strftime("%Y-%m-%dT%H:%M:%S") == expected["valid_dt"], \
        f"app.py computed the wrong valid time: {valid_dt}"
    assert log is not None, "app.py should pass its own progress sink"
    log("Pulling MRMS (stubbed from the frozen baseline)...")
    scan_log.append(valid_dt)
    return frozen


tcf_pipeline.fetch_iem_cow_tcf = fake_fetch
tcf_pipeline.build_composite = fake_composite

at = AppTest.from_file(os.path.join(REPO_ROOT, "app.py"), default_timeout=300)
at.run()
if at.exception:
    print(f"FAIL: app.py raised on load: {[e.value for e in at.exception]}")
    sys.exit(1)

# Default widgets already select the event; click Run Verification.
buttons = [b for b in at.sidebar.button if "Run Verification" in b.label]
assert buttons, f"could not find the Run Verification button (found {[b.label for b in at.sidebar.button]})"
buttons[0].click().run()

if at.exception:
    print(f"FAIL: app.py raised during the run: {[e.value for e in at.exception]}")
    sys.exit(1)

# AppTest's session_state proxies attribute access to keys, so .get() is not
# available on it -- probe with `in` instead.
if "results" not in at.session_state:
    print("FAIL: app.py did not stash results in session_state")
    sys.exit(1)
results = at.session_state["results"]

failures = []

if not scan_log:
    failures.append("app.py never called build_composite")

actual_report = results["report_text"]
if actual_report != expected["report_text"]:
    failures.append("report_text differs from the baseline")
    import difflib
    for line in difflib.unified_diff(expected["report_text"].splitlines(),
                                     actual_report.splitlines(),
                                     fromfile="expected.json", tofile="app.py", lineterm="", n=1):
        failures.append(f"    {line}")

# The render functions read these off session_state on every rerun.
for key in ("lons", "lats", "top_verif_matrix", "gdf_graded_fcst",
            "gdf_graded_miss", "gdf_sparse", "report_text", "valid_dt"):
    if key not in results:
        failures.append(f"session_state['results'] is missing {key!r}, which the render code reads")

n_polys = len(results["gdf_graded_fcst"])
n_misses = len(results["gdf_graded_miss"])
if n_polys != expected["counts"]["polygons"]:
    failures.append(f"graded polygon count: expected {expected['counts']['polygons']}, got {n_polys}")
if n_misses != expected["counts"]["misses"]:
    failures.append(f"miss count: expected {expected['counts']['misses']}, got {n_misses}")

if failures:
    print(f"FAIL  {EVENT_ID}")
    for line in failures:
        print(f"  {line}")
    sys.exit(1)

print(f"PASS  {EVENT_ID}: app.py's report matches the baseline byte for byte "
      f"({n_polys} polygons, {n_misses} misses)")
sys.exit(0)
