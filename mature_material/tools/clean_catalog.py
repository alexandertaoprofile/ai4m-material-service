from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.catalog.cleaning import run

parser = argparse.ArgumentParser(description="Extract structured commodity-material workbook")
parser.add_argument("--workbook", type=Path, default=Path("/data/se42/backend/property datasets/alloy_material_dataset_v0.1.xlsx"))
parser.add_argument("--output", type=Path, default=Path("data/processed"))
args = parser.parse_args()
print(run(args.workbook, args.output))
