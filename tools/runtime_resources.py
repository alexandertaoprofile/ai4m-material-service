#!/usr/bin/env python3
"""Download, install and verify versioned runtime assets (Python standard library)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in value or ":" in value:
        raise ValueError(f"Unsafe relative path: {value}")
    return path


def check_file(path, record):
    if path.is_symlink() or not path.is_file():
        return "missing"
    if path.stat().st_size != record["bytes"]:
        return "size mismatch"
    return "ok" if digest(path) == record["sha256"] else "sha256 mismatch"


def extract(archive, destination, expected):
    """Extract only listed regular files, rejecting links, duplicates and escapes."""
    with tarfile.open(archive, "r:gz") as stream:
        seen = set()
        for member in stream:
            name = relative(member.name).as_posix()
            if not member.isfile() or name not in expected or name in seen:
                raise ValueError(f"Unexpected archive member: {member.name}")
            seen.add(name)
            record = expected[name]
            if member.size != record["bytes"]:
                raise ValueError(f"Unexpected member size: {name}")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f"Linked target: {name}")
            with stream.extractfile(member) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if check_file(target, record) != "ok":
                raise ValueError(f"Corrupt member: {name}")
        if seen != set(expected):
            raise ValueError("Archive does not contain every listed file")


def download(manifest, groups, cache):
    cache.mkdir(parents=True, exist_ok=True)
    for asset in manifest["assets"]:
        if asset["group"] not in groups:
            continue
        name = relative(asset["name"]).as_posix()
        target = cache / name
        if check_file(target, asset) == "ok":
            print(f"Verified cached asset: {name}")
            continue
        if target.exists():
            raise ValueError(f"Existing asset differs; move it aside before retrying: {target}")
        with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as tmp:
            subprocess.run(["gh", "release", "download", manifest["release_tag"], "--repo", manifest["repository"],
                            "--pattern", name, "--dir", tmp], check=True)
            fetched = Path(tmp) / name
            status = check_file(fetched, asset)
            if status != "ok":
                raise ValueError(f"Downloaded asset {name}: {status}")
            fetched.rename(target)
        print(f"Downloaded and verified: {name}")


def install(manifest, groups, cache, destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    wanted = {row["path"]: row for row in manifest["files"] if not row["in_git"] and row["group"] in groups}
    for path in wanted:
        relative(path)
    existing = [name for name in wanted if (destination / name).exists()]
    if existing:
        raise ValueError("Selected files already exist; verify them or choose an empty resource directory: " + existing[0])
    with tempfile.TemporaryDirectory(prefix="install-", dir=destination) as tmp:
        stage = Path(tmp)
        for asset in manifest["assets"]:
            if asset["group"] not in groups:
                continue
            archive = cache / relative(asset["name"])
            status = check_file(archive, asset)
            if status != "ok":
                raise ValueError(f"Asset {archive}: {status}")
            if asset["files"]:
                extract(archive, stage, {name: wanted[name] for name in asset["files"]})
        for name, row in wanted.items():
            if "parts" not in row:
                continue
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as dst:
                for part in row["parts"]:
                    with (cache / relative(part)).open("rb") as src:
                        shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
            if check_file(target, row) != "ok":
                raise ValueError("Reassembled reference database checksum mismatch")
        # Validate the complete staged installation before moving any file.
        for name, row in wanted.items():
            if check_file(stage / name, row) != "ok":
                raise ValueError(f"Staged resource missing/corrupt: {name}")
        for name in wanted:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink() or not target.resolve().is_relative_to(destination):
                raise ValueError(f"Unsafe installation target: {name}")
            (stage / name).rename(target)
    print(f"Installed {len(wanted)} verified files into {destination}")


def verify(manifest, groups, destination):
    rows = []
    for row in manifest["files"]:
        if row["group"] not in groups:
            continue
        root = ROOT if row["in_git"] else destination
        # Git may convert CRLF on Windows; original-source checks run on Linux.
        status = check_file(root / relative(row["path"]), row)
        rows.append({"path": row["path"], "status": status})
    failures = [row for row in rows if row["status"] != "ok"]
    print(json.dumps({"ok": not failures, "checked_files": len(rows), "failures": failures}, ensure_ascii=False, indent=2))
    return not failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "download", "install", "verify"])
    parser.add_argument("--manifest", type=Path, default=ROOT / "resources/runtime-manifest.json")
    parser.add_argument("--groups", nargs="+", default=["1105", "1111", "1115", "1115-models", "1115-reference", "1116"])
    parser.add_argument("--cache", type=Path, default=ROOT / ".runtime-downloads")
    parser.add_argument("--destination", type=Path, default=ROOT / ".runtime")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    valid = {row["group"] for row in manifest["files"]}
    if set(args.groups) - valid:
        parser.error("Unknown groups: " + ", ".join(sorted(set(args.groups) - valid)))
    if args.action == "list":
        for group in sorted(set(args.groups)):
            files = [row for row in manifest["files"] if row["group"] == group]
            print(f"{group}: {len(files)} files; external bytes: {sum(row['bytes'] for row in files if not row['in_git']):,}")
    elif args.action == "download":
        download(manifest, set(args.groups), args.cache)
    elif args.action == "install":
        install(manifest, set(args.groups), args.cache, args.destination)
    else:
        return 0 if verify(manifest, set(args.groups), args.destination) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
