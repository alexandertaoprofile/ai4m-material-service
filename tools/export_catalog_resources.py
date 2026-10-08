#!/usr/bin/env python3
"""Export the source-reviewed 1105 catalog bundles used by its query loader."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

from runtime_resources import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(Path("/data/se42")):
        parser.error("Use a new export directory below /data/se42")
    args.output.mkdir(parents=True)
    manifest = json.loads(args.manifest.read_text())
    originals = list(args.source.glob("*.csv")) + list(args.source.glob("*.json"))
    for directory in ("material_core", "high_temperature"):
        originals += [path for path in (args.source / directory).rglob("*")
                      if path.is_file() and path.suffix in {".csv", ".json"}]
    records = []
    for original in sorted(originals):
        if original.is_symlink():
            raise ValueError("Linked catalog source")
        checksum = digest(original)
        path = "data/1105/catalog/" + original.relative_to(args.source).as_posix()
        target = args.output / "payload" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if digest(original) != checksum or digest(target) != checksum:
            raise ValueError("Catalog changed during snapshot")
        records.append({"source": str(original), "path": path, "bytes": target.stat().st_size,
                        "sha256": checksum, "group": "1105", "in_git": False})
    name = "runtime-20261008-1105-catalog.tar.gz"
    archive = args.output / name
    with tarfile.open(archive, "w:gz") as stream:
        for row in records:
            stream.add(args.output / "payload" / row["path"], arcname=row["path"], recursive=False)
    manifest["files"] += records
    manifest["assets"].append({"name": name, "group": "1105", "files": [row["path"] for row in records],
                               "bytes": archive.stat().st_size, "sha256": digest(archive)})
    (args.output / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"files": len(records), "asset": name, "bytes": archive.stat().st_size,
                      "sha256": digest(archive)}))


if __name__ == "__main__":
    main()
