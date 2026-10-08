"""Stable entry point for the isolated unified-alloy tensile shadow runtime.

The versioned model package remains under ``models/``.  This tracked wrapper
keeps the 1111 adapter independent of the handoff/archive location and makes
the model version selected by deployment explicit.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "models/unified_alloy_tensile_v2_20260921/runtime"


def main() -> None:
    runtime = Path(os.getenv("UNIFIED_TENSILE_RUNTIME_ROOT", str(DEFAULT_RUNTIME))).resolve()
    runner = runtime / "runner.py"
    if not runner.is_file():
        raise SystemExit(f"unified tensile runtime is unavailable: {runner}")
    sys.path.insert(0, str(runtime))
    runpy.run_path(str(runner), run_name="__main__")


if __name__ == "__main__":
    main()
