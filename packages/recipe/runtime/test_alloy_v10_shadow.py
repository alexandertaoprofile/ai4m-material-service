from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "models/alloy_v10/code"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(HERE))
import frozen_descriptors
import frozen_process
from alloy_v10_shadow import InputError, encode_request


class V10RawInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre = json.loads((HERE / "models/alloy_v10/preprocessor.json").read_text(encoding="utf-8"))
        cls.properties = json.loads((SOURCE / "descriptor_properties.json").read_text(encoding="utf-8"))
        cls.base = json.loads((HERE / "alloy_v10_health_request.json").read_text(encoding="utf-8"))["requests"][0]

    def encode(self, request):
        return encode_request(request, self.pre, self.properties, frozen_process, frozen_descriptors.descriptor_values)

    def test_aluminum_closure_and_113_columns(self):
        encoded, audit = self.encode(self.base)
        self.assertEqual(encoded["numeric"].shape, (113,))
        self.assertEqual(encoded["element_ids"].shape, (20,))
        self.assertAlmostEqual(float(encoded["fractions"].sum()), 1.0, places=6)
        self.assertFalse(audit["comparison_allowed"])
        self.assertFalse(audit["production_release_authorized"])

    def test_stainless_structured_solution_is_positive_evidence(self):
        request = {
            "domain": "stainless", "composition": {"Fe": 72.0, "Cr": 18.0, "Ni": 10.0},
            "composition_basis": "wt_percent", "temperature": {"value": 293.15, "unit": "K"},
            "process": {"text": "form=1|wq=1.0|aq=0.0", "parameters": {
                "solution_temp_C": 1049.85, "solution_time_h": 1.0,
            }},
        }
        encoded, audit = self.encode(request)
        self.assertAlmostEqual(audit["test_temperature_C"], 20.0)
        self.assertEqual(audit["process_label_encoded"], "form=1|wq=1.0|aq=0.0")
        col = self.pre["numeric_cols"].index("desc_v8_evidence_flag_solution")
        expected = ((1.0 - self.pre["numeric_mean"]["desc_v8_evidence_flag_solution"])
                    / self.pre["numeric_std"]["desc_v8_evidence_flag_solution"])
        self.assertAlmostEqual(float(encoded["numeric"][col]), expected, places=6)

    def test_nickel_multistage_is_not_silently_discarded(self):
        request = {
            "domain": "nickel_superalloy", "composition": {"Ni": 70.0, "Cr": 20.0, "Co": 10.0},
            "composition_basis": "wt_percent", "temperature": {"value": 800.0, "unit": "C"},
            "process": {"text": "cast", "parameters": {},
                        "v6_stages": "solution_stage_1_temp_C=1200; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=850; precipitation_stage_1_time_h=8"},
        }
        encoded, audit = self.encode(request)
        self.assertEqual(len(audit["v6_stage_fields_mapped"]), 4)
        missing_col = self.pre["numeric_cols"].index("desc_v6_solution_stage_1_temp_C_missing")
        self.assertTrue(np.isfinite(encoded["numeric"][missing_col]))
        self.assertFalse(audit["v6_fields_unreported"])

    def test_unclosed_and_nonfinite_amounts_are_rejected(self):
        for composition in ({"Al": 99.0}, {"Al": float("nan")}, {"Al": True}):
            request = {**self.base, "composition": composition, "composition_basis": "at_percent"}
            with self.assertRaises(InputError):
                self.encode(request)

    def test_null_temperature_is_explicitly_masked(self):
        request = {**self.base, "temperature": None}
        encoded, audit = self.encode(request)
        self.assertTrue(audit["temperature_missing"])
        column = self.pre["numeric_cols"].index("temperature_missing")
        self.assertTrue(np.isfinite(encoded["numeric"][column]))

    def test_invalid_nickel_stage_string_is_rejected(self):
        request = {
            "domain": "nickel_superalloy", "composition": {"Ni": 100.0},
            "composition_basis": "wt_percent", "temperature": {"value": 800.0, "unit": "C"},
            "process": {"text": "cast", "parameters": {}, "v6_stages": "arbitrary stage"},
        }
        with self.assertRaises(InputError):
            self.encode(request)


if __name__ == "__main__":
    unittest.main()
