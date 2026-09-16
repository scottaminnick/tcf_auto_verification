# September 16 coverage-underforecast regression

Issuance: 2026-09-16 05Z, forecast hour 4, valid 09Z.

`arrays.npz` contains the production verification-grid composite and paired
qualification union from all 15 usable MRMS analyses. `mrms_provenance.json`
records actual source keys/timestamps. `tcf_raw.txt` contains the exact IEM
CFP02 issuance, retrieved from:
https://mesonet.agron.iastate.edu/wx/afos/p.php?pil=CFP02&e=202609160500

Captured using main 15f83d8300dbe39ac3260bc2b627ded45eecacfa. Methodology 1.1
reuses these unchanged observations. Run:

    python -m unittest discover -s tests -p test_coverage_underforecast.py

Expected: five unchanged forecast grades, four existing misses, zero old
medium-core flags, one new MO/IL coverage-underforecast candidate. The Florida
components (~7,327 and ~2,787 km²) remain excluded by the 7,500 km² floor.
