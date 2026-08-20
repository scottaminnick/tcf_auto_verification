#!/usr/bin/env python3
"""Audit feature-aware coverage encodings in the six frozen TCF products."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tcf_pipeline  # noqa: E402


def audit_rows(baseline_dir=REPO_ROOT / "baseline"):
    rows = []
    for event_dir in sorted(baseline_dir.glob("20*")):
        raw_path = event_dir / "tcf_raw.txt"
        expected_path = event_dir / "expected.json"
        if not raw_path.exists() or not expected_path.exists():
            continue
        metadata = json.loads(expected_path.read_text(encoding="utf-8"))
        parsed = tcf_pipeline.parse_iem_cow_text(
            raw_path.read_text(encoding="utf-8"))
        for feature_number, row in enumerate(parsed.itertuples(), start=1):
            rows.append({
                "event_id": event_dir.name,
                "feature_number": feature_number,
                "feat_type": row.feat_type,
                "coverage_code": int(row.coverage),
                "issuance_hour": metadata["issuance_hour"],
                "valid_time": metadata["valid_dt"],
                "geometry_type": row.geometry.geom_type,
                "parser_status": "accepted",
                "diagnostic": "",
            })
        for diagnostic in parsed.attrs.get("parse_diagnostics", ()):
            rows.append({
                "event_id": event_dir.name,
                "feature_number": diagnostic.record_index,
                "feat_type": diagnostic.feature_type,
                "coverage_code": diagnostic.coverage_code,
                "issuance_hour": metadata["issuance_hour"],
                "valid_time": metadata["valid_dt"],
                "geometry_type": "",
                "parser_status": "rejected",
                "diagnostic": diagnostic.message,
            })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, help="write event-level audit CSV")
    args = parser.parse_args(argv)
    rows = audit_rows()
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    accepted = Counter((r["feat_type"], r["coverage_code"])
                       for r in rows if r["parser_status"] == "accepted")
    print("feat_type  coverage_code  count")
    for (feature_type, coverage_code), count in sorted(accepted.items()):
        print(f"{feature_type:<9}  {coverage_code:<13}  {count}")
    rejected = sum(r["parser_status"] == "rejected" for r in rows)
    print(f"accepted={sum(accepted.values())} rejected={rejected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
