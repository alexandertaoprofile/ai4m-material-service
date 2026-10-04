from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "generation_dataset_v1" / "summarize_mattersim_screening.py"
SPEC = importlib.util.spec_from_file_location("summarize_mattersim_screening", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def candidate(source: str, hull: float) -> dict:
    return {
        "source_cif": source, "source_name": Path(source).name,
        "formula_pretty": "CoCrFeMnNi", "energy_above_hull_ev": hull,
        "formation_energy_per_atom_ev": 0.02, "relaxed_structure_path": source + ".relaxed",
    }


class MatterSimSummaryTest(unittest.TestCase):
    def test_newer_result_replaces_repeated_source_and_groups_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for scenario, name, rows in (
                ("hea", "first", [candidate("/x/a.cif", 0.12)]),
                ("hea", "second", [candidate("/x/a.cif", 0.08)]),
                ("chip", "only", [candidate("/x/b.cif", 0.03)]),
            ):
                result = root / scenario / name / "mattersim_results.json"
                result.parent.mkdir(parents=True)
                result.write_text(json.dumps({"status": "ok", "candidates": rows}), encoding="utf-8")
            records, files = MODULE.load_records(root)
            self.assertEqual(files, 3)
            self.assertEqual(len(records), 2)
            self.assertEqual(MODULE.describe([x for x in records if x["scenario"] == "hea"])["minimum_energy_above_hull_ev"], 0.08)


if __name__ == "__main__":
    unittest.main()
