"""Validate and package the traceable copper hot-end evidence catalogue.

This is deliberately a catalogue/curve-fit service, not a free-composition ML
model: the available primary data do not justify predicting arbitrary Cu-Cr-
Nb-Ag-Zr blends.  The runtime only ranks properties measured at the requested
state or marks a bounded interpolation as an engineering estimate.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/processed/copper_hot_end_evidence_v1_20260910/copper_hot_end_catalog.json"
TARGET = ROOT / "models/copper_hot_end_evidence_v1_20260910/catalog_artifact.json"

def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert len(data["grades"]) >= 4
    for grade in data["grades"]:
        assert abs(sum(grade["composition_wt_percent"].values()) - 100.0) < 1e-6
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    payload = {**data, "catalog_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest()}
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(TARGET)

if __name__ == "__main__":
    main()
