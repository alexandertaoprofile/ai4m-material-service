from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "generation_dataset_v1" / "generate_dataset.py"
SPEC = importlib.util.spec_from_file_location("generation_dataset_v1", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GenerationDatasetV1UnitTest(unittest.TestCase):
    def test_exact_target_is_element_set_not_equiatomic_ratio(self) -> None:
        fractions = {"Co": 0.3, "Cr": 0.1, "Fe": 0.2, "Mn": 0.2, "Ni": 0.2}
        self.assertEqual(set(fractions), MODULE.TARGET_HEA_ELEMENTS)
        self.assertFalse(MODULE.is_equiatomic(fractions))
        self.assertAlmostEqual(MODULE.distance_from_equiatomic(fractions), 0.1)

    def test_ceramic_family_rules_are_explainable(self) -> None:
        self.assertEqual(MODULE.classify_ceramic({"Si", "Al", "O", "N"}), "SiAlON_related")
        self.assertEqual(MODULE.classify_ceramic({"B", "N"}), "BN_related")
        self.assertEqual(MODULE.classify_ceramic({"Al", "O"}), "other")

    def test_hashes_are_order_independent(self) -> None:
        left = {"Co": 0.2, "Cr": 0.2, "Fe": 0.2, "Mn": 0.2, "Ni": 0.2}
        right = {"Ni": 0.2, "Mn": 0.2, "Fe": 0.2, "Cr": 0.2, "Co": 0.2}
        self.assertEqual(MODULE.composition_hash(left), MODULE.composition_hash(right))

    def test_numeric_checkpoint_epoch_is_an_integer_for_mattergen(self) -> None:
        self.assertEqual(MODULE.parse_checkpoint_epoch("18"), 18)
        self.assertEqual(MODULE.parse_checkpoint_epoch("last"), "last")


if __name__ == "__main__":
    unittest.main()
