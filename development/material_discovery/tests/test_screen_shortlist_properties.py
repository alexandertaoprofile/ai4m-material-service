from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "generation_dataset_v1" / "screen_shortlist_properties.py"
SPEC = importlib.util.spec_from_file_location("screen_shortlist_properties", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ShortlistScreenTest(unittest.TestCase):
    def test_newest_completed_record_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "hea" / "first" / "mattersim_results.json"
            second = root / "hea" / "second" / "mattersim_results.json"
            first.parent.mkdir(parents=True); second.parent.mkdir(parents=True)
            base = {
                "source_name": "aerospace_alloy-000111.cif", "source_cif": "/x/a.cif",
                "relaxed_structure_path": "/x/a.extxyz", "formula_pretty": "CoCrFeMnNi",
            }
            first.write_text(json.dumps({"status": "ok", "candidates": [{**base, "energy_above_hull_ev": 0.12}]}), encoding="utf-8")
            second.write_text(json.dumps({"status": "ok", "candidates": [{**base, "energy_above_hull_ev": 0.08}]}), encoding="utf-8")
            records = MODULE.completed_candidates(root)
            self.assertEqual(records[base["source_name"]]["energy_above_hull_ev"], 0.08)

    def test_shortlist_has_two_tracks_and_models(self) -> None:
        self.assertEqual([item["track"] for item in MODULE.SHORTLIST], ["hea", "hea", "chip", "chip"])
        self.assertIn("jv_bulk_modulus_kv_alignn", MODULE.PROPERTY_MODELS["bulk_modulus"])
        self.assertIn("jv_epsx_alignn", MODULE.PROPERTY_MODELS["dielectric_constant_x"])

    def test_portable_bundle_resolves_relaxed_file_relative_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "shortlist_bundle.json"
            manifest.write_text(json.dumps({"candidates": [{
                "source_name": "chip_packaging-000165.cif",
                "relaxed_file": "relaxed/candidate.extxyz",
            }]}), encoding="utf-8")
            record = MODULE.bundled_candidates(manifest)["chip_packaging-000165.cif"]
            self.assertEqual(record["relaxed_structure_path"], str((root / "relaxed/candidate.extxyz").resolve()))


if __name__ == "__main__":
    unittest.main()
