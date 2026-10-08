from __future__ import annotations

import unittest

from glass_shadow_compare import build_glass_shadow_batch, merge_private_diagnostics


class GlassComparisonTests(unittest.TestCase):
    def test_only_matching_domain_and_two_actual_properties(self):
        formal = {"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": [{
            "candidate_id": "CGS-001", "composition_mol_percent": {"SiO2": 80.0, "Al2O3": 20.0},
            "predicted_properties": {
                "density_g_per_cm3": {"prediction": 2.5, "unit": "g/cm3"},
                "young_modulus_GPa": {"prediction": 70.0, "unit": "GPa"},
                "CTE_linear_0_to_300C": {"prediction": 3.5, "unit": "ppm/K"},
            },
        }]}
        batch, mapping = build_glass_shadow_batch(formal)
        self.assertEqual([x["task"] for x in batch["requests"]], ["DENSITY_20C", "YOUNG_MODULUS"])
        self.assertEqual(len(mapping), 2)
        self.assertFalse(mapping[0]["comparison_allowed"])
        self.assertEqual(build_glass_shadow_batch({"model_domain": "reusable_rocket_stainless"})[0], {"requests": []})

    def test_merge_never_authorizes_display_or_formal_error_claim(self):
        _, mapping = build_glass_shadow_batch({"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": [{
            "candidate_id": "CGS-1", "composition_mol_percent": {"SiO2": 100.0},
            "predicted_properties": {"density_g_per_cm3": {"prediction": 2.5}},
        }]})
        response = {"ok": True, "results": [{"request_id": "CGS-1:DENSITY_20C", "status": "predicted_diagnostic",
                                             "prediction": {"value": 2.45}}]}
        result = merge_private_diagnostics(mapping, response)
        row = result["comparisons"][0]
        self.assertAlmostEqual(row["transformer_minus_formal_diagnostic"], -0.05)
        self.assertFalse(row["comparison_allowed"])
        self.assertFalse(row["difference_is_measured_error"])

    def test_misaligned_response_rejected(self):
        with self.assertRaises(ValueError):
            merge_private_diagnostics([{"request_id": "A"}], {"ok": True, "results": [{"request_id": "B"}]})

    def test_native_maximum_500_candidates_and_overflow(self):
        candidate={'composition_mol_percent':{'SiO2':100.0},'predicted_properties':{
            'density_g_per_cm3':{'prediction':2.5},'young_modulus_GPa':{'prediction':70.0}}}
        formal={'model_domain':'chip_glass_thermomechanical_family_v1','all_candidates':[candidate]*500}
        batch,mapping=build_glass_shadow_batch(formal)
        self.assertEqual(len(batch['requests']),1000)
        self.assertEqual(len(mapping),1000)
        formal['all_candidates'].append(candidate)
        with self.assertRaises(ValueError):build_glass_shadow_batch(formal)


if __name__ == "__main__":
    unittest.main()
