#!/usr/bin/env python3
"""Run each service's tests in its own process and save the results."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("material_database", "material_composition_optimization", "material_discovery", "material_validation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new report filename")
    results = []
    for service in SERVICES:
        started = time.monotonic()
        environment = dict(os.environ, PYTHONPATH=".", OPENAI_API_KEY="local-test-placeholder",
                           MINIO_ENDPOINT="http://127.0.0.1:9", MINIO_ACCESS_KEY_ID="local-test",
                           MINIO_ACCESS_KEY_SECRET="local-test")
        for key, value in dotenv_values(ROOT / service / ".env.runtime.example").items():
            if value is not None:
                environment[key] = value
        # Individual tests exercise the feature switch themselves; a deployment
        # template must not globally suppress the behavior they are testing.
        environment.pop("UNIFIED_TENSILE_SHADOW_ENABLED", None)
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT / service,
                                   env=environment, capture_output=True, text=True, timeout=300)
        row = {"service": service, "ok": completed.returncode == 0,
               "seconds": round(time.monotonic() - started, 3),
               "output": (completed.stdout + completed.stderr)[-8000:]}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    args.output.write_text(json.dumps({"ok": all(row["ok"] for row in results), "checks": results},
                                     ensure_ascii=False, indent=2) + "\n")
    return 0 if all(row["ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
