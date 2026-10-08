#!/usr/bin/env python3
"""Check maintained documentation without installing, deploying or running models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

EXTRA_DOCUMENTS = (
    "docs/reproduction.md", "docs/research_resources.md", "docs/REPRODUCTION_VERIFICATION_20261008.md",
    "HANDOFF_2026-08-21.md", "docs/deployment_status.md", "docs/development_changes.md",
    "docs/review_guide.md", "docs/external_dependencies.md", "docs/code_synchronization.md",
    "docs/material_service_architecture_standard.md", "docs/MAIN_LAYOUT_VERIFICATION_20261004.md",
    "docs/README_REVIEW_20261004.md", "docs/RELEASE_HANDOFF_20261004.md",
    "material_discovery/docs/OMATG_POPUP_INPUTS.md", "material_discovery/docs/REQUIREMENT_REVIEW.md",
    "development/material_discovery/docs/OMATG_POPUP_INPUTS.md",
    "development/material_discovery/docs/REQUIREMENT_REVIEW.md",
)


def check_text(relative: str, content: str, root: Path, check_shell: bool) -> tuple[dict, list[str]]:
    counts = {"links": 0, "json_examples": 0, "shell_blocks": 0}
    errors = []
    fence = False
    table_columns = None
    lines = content.splitlines()
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            fence = not fence
            table_columns = None
            continue
        if fence:
            continue
        if re.match(r"^#{1,6} ", line) and number < len(lines) and lines[number].strip():
            errors.append(f"{relative}:{number}: add a blank line after the heading")
        if line.startswith("|"):
            columns = len(re.findall(r"(?<!\\)\|", line)) - 1
            if table_columns is not None and columns != table_columns:
                errors.append(f"{relative}:{number}: inconsistent table columns")
            table_columns = columns
        else:
            table_columns = None
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", line):
            target = match.group(1).split("#", 1)[0].strip("<>")
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            counts["links"] += 1
            if not ((root / relative).parent / target).exists():
                errors.append(f"{relative}:{number}: broken local link: {target}")
    if fence:
        errors.append(f"{relative}: unbalanced code fences")
    for language, block in re.findall(r"^```(\w+)\n(.*?)^```", content, re.M | re.S):
        if language == "json":
            counts["json_examples"] += 1
            try:
                json.loads(block)
            except ValueError as exc:
                errors.append(f"{relative}: invalid JSON example: {exc}")
        elif language in {"bash", "sh"}:
            counts["shell_blocks"] += 1
            if check_shell:
                result = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
                if result.returncode:
                    errors.append(f"{relative}: invalid shell syntax: {result.stderr.strip()}")
    return counts, errors


def check(root: Path) -> dict:
    tracked = subprocess.check_output(["git", "ls-files", "--cached", "--others",
                                       "--exclude-standard", "-z"], cwd=root)
    paths = {item.decode("utf-8") for item in tracked.split(b"\0") if item}
    # Preserve imported upstream documentation unchanged; validate our maintained
    # integration guides rather than their omitted training assets and datasets.
    readmes = {name for name in paths if Path(name).name == "README.md"
               and not name.startswith(("third_party/OMatG/", "packages/hea_surrogate/", "packages/recipe/", "packages/recipe_report/"))}
    documents = sorted(readmes | set(EXTRA_DOCUMENTS))
    counts = {"readmes": len(readmes), "documents": len(documents),
              "links": 0, "json_examples": 0, "shell_blocks": 0}
    errors = []
    shell_available = shutil.which("bash") is not None
    for relative in documents:
        path = root / relative
        if not path.is_file():
            errors.append(f"Missing document: {relative}")
            continue
        record, failures = check_text(relative, path.read_text(encoding="utf-8"), root, shell_available)
        for key, value in record.items():
            counts[key] += value
        errors.extend(failures)
    return {"ok": not errors, "counts": counts,
            "shell_syntax": "checked" if shell_available else "skipped: Bash not installed",
            "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check(args.root.resolve())
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"Cannot read repository: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
