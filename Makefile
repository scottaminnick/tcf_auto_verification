# Baseline capture/verification harness.
#
# Capture runs inside the repo's own Dockerfile so the frozen numbers come from
# the pinned requirements.txt that Railway deploys -- not from whatever happens
# to be installed on a developer laptop. A baseline captured against a different
# scipy or shapely is not a baseline, it is a second opinion.
#
#   make baseline-image                      build the image
#   make capture                             capture every event in EVENTS
#   make capture EVENT=20260524_19Z_F04      capture one event
#   make check                               replay + diff against the baselines
#   make check-pass-a                        also require byte-exact pass A reports
#   make fixture                             run the harness's own fixture test
#   make parity                              drive app.py and diff its report
#   make test                                fixture + parity + check-pass-a

IMAGE   ?= tcf-baseline
EVENT   ?=
# The pipeline lives in tcf_pipeline.py, shared by app.py and baseline/capture.py.
# Override only to check a differently-named module.
PIPELINE ?= tcf_pipeline
PYTHON  ?= python3

# Write captured files back to the host as the invoking user rather than root.
DOCKER_RUN = docker run --rm \
	-v "$(CURDIR)/baseline:/app/baseline" \
	--user "$$(id -u):$$(id -g)" \
	-e HOME=/tmp \
	$(IMAGE)

.PHONY: baseline-image capture capture-shell check check-pass-a fixture parity test methodology

baseline-image:
	docker build -t $(IMAGE) .

# Needs network: IEM archives (mesonet.agron.iastate.edu) + the public
# noaa-mrms-pds S3 bucket. Everything else in this file runs offline.
capture: baseline-image
	$(DOCKER_RUN) python baseline/capture.py $(EVENT)

# Same image, interactive -- for poking at a capture that failed.
capture-shell: baseline-image
	docker run --rm -it -v "$(CURDIR)/baseline:/app/baseline" $(IMAGE) bash

# check.py is offline and dependency-light, so it runs on the host by default.
# Add `make check ... IN_DOCKER=1` to run it against the pinned stack instead.
check:
ifdef IN_DOCKER
	$(DOCKER_RUN) python baseline/check.py --pipeline $(PIPELINE) $(EVENT)
else
	$(PYTHON) baseline/check.py --pipeline $(PIPELINE) $(EVENT)
endif

# Adds the byte-exact pass A comparison against the report text captured from
# the live app.
check-pass-a:
	$(PYTHON) baseline/check.py --pipeline $(PIPELINE) --pass-a $(EVENT)

fixture:
	$(PYTHON) baseline/test_fixture.py

# Drives app.py through streamlit's AppTest with the network calls fed from the
# frozen baseline, and diffs the report the app ends up holding. This is what
# covers app.py's own glue, which check.py cannot see. Skips if streamlit is
# not installed.
parity:
	$(PYTHON) baseline/test_app_parity.py

# Independent analytic oracles for approved Methodology Specification 0.1
# requirements. This is intentionally NOT part of the historical baseline test
# target: known specification conflicts remain red until production is corrected.
methodology:
	$(PYTHON) -m unittest discover -s methodology_validation -p 'test_*.py' -v

test: fixture parity check-pass-a
