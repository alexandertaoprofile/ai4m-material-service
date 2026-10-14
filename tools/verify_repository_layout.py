#!/usr/bin/env python3
"""Read-only verification of deployed snapshots and retained development code."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

SERVICES = (
    "material_selection",
    "material_composition_optimization",
    "material_discovery",
    "material_validation",
)
DEVELOPMENT_SERVICES = SERVICES[1:3]
SUFFIXES = {".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh"}
INTEGRATION_COMMIT = "213155c608677612a1e7845a5986ce42cb55e0c2"
PHYSICS_SOURCES = {
    "glass/code/run_glass_constraint_ablation_v1.py": "/data/se42/experiments/oxide_glass_physics_constraints_v1_20260924/code/run_glass_constraint_ablation_v1.py",
    "glass/code/train_oxide_glass_multitask_cte_adapter_v1.py": "/data/se42/experiments/oxide_glass_multitask_physics_v2_20260928/code/train_oxide_glass_multitask_cte_adapter_v1.py",
    "glass/code/run_glass_fresh_endpoint_challenge.py": "/data/se42/experiments/oxide_glass_multitask_physics_v2_20260928/code/run_glass_fresh_endpoint_challenge.py",
    "glass/code/analyze_glass_endpoint_predictions.py": "/data/se42/experiments/oxide_glass_multitask_physics_v2_20260928/code/analyze_glass_endpoint_predictions.py",
    "glass/references/GLASS_PHYSICS_CONSTRAINT_PLAN.md": "/data/se42/experiments/oxide_glass_physics_constraints_v1_20260924/GLASS_PHYSICS_CONSTRAINT_PLAN.md",
    "glass/references/GLASS_PHYSICS_CONSTRAINT_AUDIT.json": "/data/se42/experiments/oxide_glass_physics_constraints_v1_20260924/GLASS_PHYSICS_CONSTRAINT_AUDIT.json",
}


def content_sha(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def source_path(path: str, prefix: str) -> bool:
    item = PurePosixPath(path)
    return item.is_relative_to(prefix) and item.suffix in SUFFIXES


def git_paths(root: Path) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    return sorted({item.decode("utf-8") for item in output.split(b"\0") if item})


def source_records(root: Path, prefix: str) -> dict[str, str]:
    records = {}
    for relative in git_paths(root):
        if source_path(relative, prefix):
            path = root / relative
            if path.is_file():
                records[PurePosixPath(relative).relative_to(prefix).as_posix()] = content_sha(path.read_bytes())
    return records


def record_digest(records: dict[str, str]) -> dict:
    value = "".join(f"{name}\0{digest}\n" for name, digest in sorted(records.items()))
    return {"files": len(records), "sha256": hashlib.sha256(value.encode()).hexdigest()}


def inventory(root: Path) -> dict:
    physics = {}
    for relative, original in PHYSICS_SOURCES.items():
        path = root / "development/1111_physics" / relative
        physics[relative] = {"source": original, "sha256": content_sha(path.read_bytes())}
    return {
        "schema_version": 1,
        "snapshot_date": "2026-10-04",
        "source_root": "/data/se42/alpha_project/material_service_hub",
        "integration_commit": INTEGRATION_COMMIT,
        "scope_suffixes": sorted(SUFFIXES),
        "line_endings": "CRLF normalized to LF",
        "services": {name: record_digest(source_records(root, name)) for name in SERVICES},
        "development_services": {
            name: record_digest(source_records(root, "development/" + name)) for name in DEVELOPMENT_SERVICES
        },
        "physics": physics,
    }


def verify(root: Path, expected: dict, live_root: Path | None) -> dict:
    failures = []
    observed = inventory(root)
    layout = json.loads((root / "docs/source_layout_20261014.json").read_text(encoding="utf-8"))
    baseline_services = {}
    for service in SERVICES:
        records = source_records(root, service)
        for relative, digest in layout["added_source_files"].get(service, {}).items():
            if records.pop(relative, None) != digest:
                failures.append(f"Directory migration file changed: {service}/{relative}")
        baseline_name = layout["baseline_directory_names"].get(service, service)
        baseline_services[baseline_name] = record_digest(records)
    if baseline_services != expected["services"]:
        failures.append("Snapshot inventory changed: services")
    for key in ("development_services", "physics"):
        if observed[key] != expected[key]:
            failures.append(f"Snapshot inventory changed: {key}")
    for relative in (
        "README.md", "development/README.md", "docs/deployment_status.md",
        "development/1111_physics/README.md",
        *[f"{name}/main.py" for name in SERVICES],
        *[f"development/{name}/main.py" for name in DEVELOPMENT_SERVICES],
    ):
        if not (root / relative).is_file():
            failures.append(f"Missing layout entry: {relative}")
    module = "material_discovery/src/material_workflow/omatg_output_status.py"
    if (root / module).exists() or not (root / "development" / module).is_file():
        failures.append("Report status module is not isolated in development")

    payload = subprocess.check_output(
        ["git", "archive", expected["integration_commit"], "--", *DEVELOPMENT_SERVICES], cwd=root
    )
    preserved = 0
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for member in archive:
            if not member.isfile() or PurePosixPath(member.name).suffix not in SUFFIXES:
                continue
            handle = archive.extractfile(member)
            path = root / "development" / member.name
            if handle is None or not path.is_file() or content_sha(handle.read()) != content_sha(path.read_bytes()):
                failures.append(f"Integration source not preserved: {member.name}")
            preserved += 1

    comparisons = {}
    if live_root is not None:
        for service in SERVICES:
            saved = source_records(root, service)
            current = source_records(live_root, service)
            changed = sorted(name for name in saved.keys() | current.keys() if saved.get(name) != current.get(name))
            comparisons[service] = {"files": len(saved), "different": changed}
            if changed:
                failures.append(f"Live source differs: {service}")
        for relative, record in expected["physics"].items():
            original = Path(record["source"])
            if not original.is_file() or content_sha(original.read_bytes()) != record["sha256"]:
                failures.append(f"Physics original differs: {relative}")

    return {
        "ok": not failures,
        "services": observed["services"],
        "development_services": observed["development_services"],
        "preserved_integration_files": preserved,
        "physics_files": len(observed["physics"]),
        "live_comparison": comparisons,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-root", type=Path)
    parser.add_argument("--print-inventory", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.print_inventory:
        result = inventory(root)
    else:
        expected = json.loads((root / "docs/source_snapshot_20261004.json").read_text(encoding="utf-8"))
        result = verify(root, expected, args.live_root.resolve() if args.live_root is not None else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
