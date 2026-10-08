#!/usr/bin/env python3
"""Record installed packages and dependency checks from independent environments."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--constraints-dir", type=Path, help="Also save the tested package versions as pip constraints")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new environment record filename")
    records = {}
    for name in ("web", "numeric", "recipe", "omatg", "alignn"):
        python = args.env_root / name / "bin/python"
        version = subprocess.check_output([str(python), "--version"], text=True).strip()
        packages = json.loads(subprocess.check_output([str(python), "-m", "pip", "list", "--format=json"], text=True))
        # The editable OMatG source is already fixed by the resource manifest;
        # retain package versions without embedding the test machine's paths.
        packages = [{"name": row["name"], "version": row["version"]} for row in packages]
        checked = subprocess.run([str(python), "-m", "pip", "check"], capture_output=True, text=True)
        records[name] = {"python": version, "packages": packages,
                         "dependency_check_ok": checked.returncode == 0,
                         "dependency_check": (checked.stdout + checked.stderr).strip()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    if args.constraints_dir:
        args.constraints_dir.mkdir(parents=True, exist_ok=True)
        for name, row in records.items():
            label = "alignn-cpu" if name == "alignn" else name
            target = args.constraints_dir / (label + ".txt")
            with target.open("x") as stream:
                stream.write("# Versions from the independent Linux verification, 2026-10-08.\n")
                stream.write("".join(f"{item['name']}=={item['version']}\n" for item in row["packages"]
                                     if item["name"].lower() not in {"omg", "pip", "setuptools", "wheel"}))
    print(json.dumps({"ok": all(row["dependency_check_ok"] for row in records.values()),
                      "environments": {name: len(row["packages"]) for name, row in records.items()}}))
    return 0 if all(row["dependency_check_ok"] for row in records.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
