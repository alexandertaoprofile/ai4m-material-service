#!/usr/bin/env python3
"""Read-only comparison of root services and their development copies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess

SERVICES = ("material_composition_optimization", "material_discovery")
SUFFIXES = {".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh"}


def compare_records(root_files: dict[str, bytes], development_files: dict[str, bytes]) -> dict:
    shared = root_files.keys() & development_files.keys()
    identical = sorted(name for name in shared if
                       root_files[name].replace(b"\r\n", b"\n") ==
                       development_files[name].replace(b"\r\n", b"\n"))
    changed = sorted(shared - set(identical))
    return {"root_files": len(root_files), "development_files": len(development_files),
            "identical": identical, "changed": changed,
            "added_in_development": sorted(development_files.keys() - root_files.keys()),
            "only_in_root": sorted(root_files.keys() - development_files.keys())}


def git_paths(root: Path) -> list[str]:
    result = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    return sorted({item.decode("utf-8") for item in result.split(b"\0") if item})


def read_records(root: Path, prefix: str, paths: list[str]) -> dict[str, bytes]:
    records = {}
    for name in paths:
        path = PurePosixPath(name)
        if path.is_relative_to(prefix) and path.suffix in SUFFIXES and (root / name).is_file():
            records[path.relative_to(prefix).as_posix()] = (root / name).read_bytes()
    return records


def compare(root: Path) -> dict:
    paths = git_paths(root)
    return {service: compare_records(read_records(root, service, paths),
                                    read_records(root, "development/" + service, paths))
            for service in SERVICES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        result = compare(args.root.resolve())
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"Cannot read repository: {exc}\n")
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for service, record in result.items():
            print(f"{service}: root={record['root_files']}, development={record['development_files']}, "
                  f"identical={len(record['identical'])}, changed={len(record['changed'])}, "
                  f"added={len(record['added_in_development'])}, root_only={len(record['only_in_root'])}")
            for key, label in (("changed", "changed"), ("added_in_development", "added"),
                               ("only_in_root", "root only")):
                for name in record[key]:
                    print(f"  {label}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
