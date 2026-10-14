#!/usr/bin/env python3
"""Export an explicit, read-only inventory from 75 into a new staging directory.

Source is preserved byte-for-byte. Credentials, results and original PDFs are
excluded. Large runtime files become versioned release assets, not Git blobs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path

VERSION = "runtime-20261008"
TEXT = {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".sh", ".md"}
SKIP = {"__pycache__", ".pytest_cache", ".git", ".venv", "results", "reports", "logs", "assets", "omg.egg-info",
        "private_rendered_results", "streamed_markdown"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else digest_stream(stream)


def digest_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(Path("/data/se42")) or output.exists():
        parser.error("Use a NEW staging directory below /data/se42")
    output.mkdir(parents=True)
    source = output / "source"
    payload = output / "payload"
    assets = output / "assets"
    source.mkdir(); payload.mkdir(); assets.mkdir()
    records = []

    def copy(original, relative, group, *, code=False):
        original = Path(original)
        if original.is_symlink() or not original.is_file():
            raise RuntimeError(f"Missing or linked source: {original}")
        target = (source if code else payload) / relative
        if target.exists():
            if sha(target) != sha(original):
                raise RuntimeError(f"Conflicting snapshot: {relative}")
            return
        before = sha(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if before != sha(original) or before != sha(target):
            raise RuntimeError(f"Source changed during export: {original}")
        records.append({"source": str(original), "path": str(relative), "bytes": target.stat().st_size,
                        "sha256": before, "group": group, "in_git": code})

    def code_tree(root, dest, group):
        root = Path(root)
        for item in sorted(root.rglob("*")):
            rel = item.relative_to(root)
            if any(part in SKIP for part in rel.parts) or item.suffix not in TEXT or item.name.startswith(".env"):
                continue
            if item.is_file():
                copy(item, Path(dest) / rel, group, code=True)

    hea = Path("/data/se42/hea_surrogate")
    fixed = Path("/data/se42/experiments/material_rsi_prelaunch_20260928/package")
    code_tree(hea / "src", "packages/hea_surrogate/src", "1111")
    code_tree(hea / "tools", "packages/hea_surrogate/tools", "1111")
    # predict_joint imports training class definitions and small lookup tables.
    code_tree(hea / "pure_zero_shot_v2", "packages/hea_surrogate/pure_zero_shot_v2", "1111")
    for item in sorted((hea / "pure_zero_shot_v2/data").glob("*.csv")):
        copy(item, Path("packages/hea_surrogate/pure_zero_shot_v2/data") / item.name, "1111", code=True)
    code_tree(fixed / "runtime", "packages/recipe/runtime", "1111")
    for item in sorted((fixed / "runtime").rglob("*")):
        if item.is_file() and item.suffix in {".pt", ".pth", ".joblib"}:
            copy(item, Path("packages/recipe/runtime") / item.relative_to(fixed / "runtime"), "1111")
    for row in json.loads((fixed / "SPECIALIST_FILES.json").read_text()):
        item = fixed / row["bundle_path"]
        # The frozen specialist version and current surrogate version are separate.
        copy(item, Path("packages/recipe") / row["bundle_path"], "1111", code=item.suffix in TEXT)
        original = Path(row["source"])
        if original.is_file():
            copy(original, Path("packages/hea_surrogate") / original.relative_to(hea), "1111", code=original.suffix in TEXT)
    copy(fixed / "SPECIALIST_FILES.json", "packages/recipe/SPECIALIST_FILES.json", "1111", code=True)
    for item in sorted(Path("/data/se42/experiments/material_shadow_frontend_v2_20260928").glob("*.py")):
        copy(item, Path("packages/recipe_report") / item.name, "1111", code=True)
    code_tree("/data/third_party/OMatG", "third_party/OMatG", "1115")
    for name in ("LICENSE", "AUTHORS.md"):
        copy(Path("/data/third_party/OMatG") / name, Path("third_party/OMatG") / name, "1115", code=True)
    checkpoints = Path("/data/se42/omatg_local/checkpoints")
    for model in ("Alex-MP-20-CSP/Trig-SDE-Gamma", "MP-20-DNG/Linear-SDE-Gamma"):
        for name in ("checkpoint.ckpt", "train.yaml"):
            copy(checkpoints / model / name, Path("weights/omatg") / model / name, "1115-models")
    copy(checkpoints / "mattersim-v1.0.0-1M.pth", "weights/omatg/mattersim-v1.0.0-1M.pth", "1115-models")
    alignn = Path("/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages/alignn")
    for name in ("jv_mbj_bandgap_alignn", "jv_bulk_modulus_kv_alignn", "jv_shear_modulus_gv_alignn",
                 "jv_avg_elec_mass_alignn", "jv_avg_hole_mass_alignn"):
        copy(alignn / (name + ".zip"), Path("weights/alignn") / (name + ".zip"), "1115-models")
    for directory in ("DeepMD-W-14-commercial-level", "DeepMD_W-14_commercial-level"):
        for item in sorted((Path("/data/se42/cache") / directory).rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".log":
                copy(item, Path("evidence") / directory / item.relative_to(Path("/data/se42/cache") / directory), "1116")
    db = Path("/data/se42/alpha_project/material_service_hub/material_selection/data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite")
    db_rel = Path("data/1105/fluid_property_evidence.sqlite")
    snapshot = payload / db_rel
    snapshot.parent.mkdir(parents=True)
    # SQLite backup produces a transaction-consistent snapshot, including WAL.
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(snapshot) as dst:
        src.backup(dst)
        if dst.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite snapshot integrity check failed")
    records.append({"source": str(db), "path": str(db_rel), "bytes": snapshot.stat().st_size,
                    "sha256": sha(snapshot), "group": "1105", "in_git": False, "snapshot_method": "sqlite_backup"})

    environments = {
        "web": "/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python",
        "numeric": "/data/mamba/envs/mattergen-py310/bin/python",
        "recipe": "/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python",
        "omatg": "/data/mamba/envs/omatg-py312/bin/python",
        "alignn": "/home/ubuntu/miniconda3/envs/dsc2/bin/python",
    }
    export = "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'packages': sorted([{'name':d.metadata['Name'],'version':d.version} for d in m.distributions()],key=lambda r:r['name'].lower())}))"
    env_records = {}
    for name, python in environments.items():
        row = json.loads(subprocess.check_output([python, "-c", export], text=True))
        row["source_python"] = python
        env_records[name] = row
    # ALIGNN is actually imported from a supplemental site-packages directory.
    env_records["alignn_supplement"] = json.loads(subprocess.check_output([
        environments["alignn"], "-c", "import importlib.metadata as m,json; print(json.dumps([{'name':d.metadata['Name'],'version':d.version} for d in m.distributions(path=['/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages'])]))"], text=True))
    env_dir = source / "env/current"
    env_dir.mkdir(parents=True)
    (env_dir / "installed-20261008.json").write_text(json.dumps(env_records, indent=2, ensure_ascii=False) + "\n")
    asset_rows = []
    for group in ("1105", "1111", "1115-models", "1116"):
        name = f"{VERSION}-{group}.tar.gz"
        target = assets / name
        members = [row for row in records if row["group"] == group and not row["in_git"]]
        with tarfile.open(target, "w:gz") as archive:
            for row in members:
                archive.add(payload / row["path"], arcname=row["path"], recursive=False)
        asset_rows.append({"name": name, "group": group, "bytes": target.stat().st_size, "sha256": sha(target),
                           "files": [row["path"] for row in members]})
    reference = Path("/data/mattersim_reference_cache/reference_MP2020correction")
    reference_sha = sha(reference)
    parts = []
    with reference.open("rb") as stream:
        index = 0
        while True:
            block = stream.read(512 * 1024 * 1024)
            if not block:
                break
            index += 1
            name = f"{VERSION}-1115-reference.part{index:02d}"
            target = assets / name
            target.write_bytes(block)
            parts.append(name)
            asset_rows.append({"name": name, "group": "1115-reference", "bytes": len(block), "sha256": sha(target), "files": []})
    if sha(reference) != reference_sha:
        raise RuntimeError("Reference changed during export")
    records.append({"source": str(reference), "path": "reference/reference_MP2020correction", "group": "1115-reference",
                    "bytes": reference.stat().st_size, "sha256": reference_sha, "in_git": False, "parts": parts})
    manifest = {"schema_version": 1, "version": VERSION, "repository": "AI4SciFoundation/Material-Computation",
                "release_tag": VERSION, "source_export_date": "2026-10-08", "files": records, "assets": asset_rows}
    docs = source / "resources"
    docs.mkdir(parents=True)
    (docs / "runtime-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        archive.add(source, arcname=".")
    print(json.dumps({"output": str(output), "source_files": sum(row["in_git"] for row in records),
                      "runtime_files": sum(not row["in_git"] for row in records), "assets": asset_rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
