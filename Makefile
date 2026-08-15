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
#   make check PIPELINE=tcf_core             replay + diff against the baselines
#   make check-transcription                 replay against baseline.capture itself
#   make check-pass-a PIPELINE=tcf_core      also require byte-exact pass A reports
#   make fixture                             run the harness's own fixture test

IMAGE   ?= tcf-baseline
EVENT   ?=
PIPELINE ?=
PYTHON  ?= python3

# Write captured files back to the host as the invoking user rather than root.
DOCKER_RUN = docker run --rm \
	-v "$(CURDIR)/baseline:/app/baseline" \
	--user "$$(id -u):$$(id -g)" \
	-e HOME=/tmp \
	$(IMAGE)

.PHONY: baseline-image capture capture-shell check check-transcription check-pass-a fixture

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
	@test -n "$(PIPELINE)" || { \
		echo "PIPELINE=<module> is required, e.g. make check PIPELINE=tcf_core"; \
		echo "(use 'make check-transcription' to check baseline/capture.py against itself)"; \
		exit 2; }
ifdef IN_DOCKER
	$(DOCKER_RUN) python baseline/check.py --pipeline $(PIPELINE) $(EVENT)
else
	$(PYTHON) baseline/check.py --pipeline $(PIPELINE) $(EVENT)
endif

# Replays the baselines through baseline/capture.py itself. This proves the
# stored arrays and expected.json are self-consistent; it proves NOTHING about a
# refactored pipeline. Use `make check PIPELINE=<the new module>` for that.
check-transcription:
	$(PYTHON) baseline/check.py --pipeline baseline.capture $(EVENT)

check-pass-a:
	@test -n "$(PIPELINE)" || { echo "PIPELINE=<module> is required"; exit 2; }
	$(PYTHON) baseline/check.py --pipeline $(PIPELINE) --pass-a $(EVENT)

fixture:
	$(PYTHON) baseline/test_fixture.py
