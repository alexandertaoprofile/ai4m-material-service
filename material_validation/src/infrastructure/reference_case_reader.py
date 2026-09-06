"""Read only versioned reference-case evidence; never infer missing science."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_evidence(case_root: Path) -> dict[str, Any]:
    manifest_path = case_root / "evidence_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing reference evidence manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    benchmark_path = case_root / "user_supplied_benchmark.json"
    if benchmark_path.is_file():
        manifest["user_supplied_benchmark"] = json.loads(benchmark_path.read_text(encoding="utf-8"))
    artifacts: list[dict[str, str]] = []
    for relative in ("raw", "models", "literature"):
        directory = case_root / relative
        if directory.is_dir():
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                artifacts.append({"path": str(path.relative_to(case_root)), "sha256": _sha256(path)})
    return {**manifest, "case_root": str(case_root), "artifacts": artifacts}
