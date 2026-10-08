#!/usr/bin/env python3
"""Predict five installed ALIGNN properties for a real relaxed structure."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROPERTIES = {"band_gap", "bulk_modulus", "shear_modulus", "electron_effective_mass", "hole_effective_mass"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True, help="Existing 1115 ALIGNN request containing a relaxed structure")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    request = json.loads(args.request.read_text())
    request["candidates"] = request["candidates"][:1]
    request["properties"] = {key: value for key, value in request["properties"].items() if key in PROPERTIES}
    if set(request["properties"]) != PROPERTIES:
        raise ValueError("The source request needs all five installed properties")
    request["output_path"] = str((args.output_dir / "predictions.json").absolute())
    request_path = args.output_dir / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    environment = dict(os.environ, ALIGNN_SITE_PACKAGES="", ALIGNN_CHECKPOINT_ROOT=str(args.weights.absolute()),
                       CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                       TMPDIR=str(args.output_dir), XDG_CACHE_HOME=str(args.output_dir / "cache"),
                       DGLDEFAULTDIR=str(args.output_dir / "dgl"))
    completed = subprocess.run([str(args.python), str(ROOT / "material_discovery/tools/run_omatg_alignn.py"),
                                "--request", str(request_path)], env=environment, capture_output=True, text=True, timeout=180)
    (args.output_dir / "worker.log").write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError((completed.stdout + completed.stderr)[-4000:])
    rows = json.loads(Path(request["output_path"]).read_text())
    row = rows[request["candidates"][0]["candidate_id"]]
    if set(row["predictions"]) != PROPERTIES or row["errors"]:
        raise RuntimeError(json.dumps(row, ensure_ascii=False) + completed.stdout[-3000:])
    report = {"ok": True, "seconds": round(time.monotonic() - started, 3), "properties": sorted(PROPERTIES),
              "python": str(args.python), "predictions": row["predictions"]}
    (args.output_dir / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
