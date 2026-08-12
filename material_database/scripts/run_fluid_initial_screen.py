"""Run one explicit fluid-material screening request and create a report bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.fluid_lubricant.presentation import render_assets
from src.fluid_lubricant.query import run_query, save_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic fluid initial-screening query")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True, help="JSON query request")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text(encoding="utf-8"))
    result = run_query(args.database, payload)
    summary = save_result(result, args.output)
    assets = render_assets(result, args.output / "assets")
    (args.output / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {summary}")
    print(f"rendered {len(assets)} assets")


if __name__ == "__main__":
    main()
