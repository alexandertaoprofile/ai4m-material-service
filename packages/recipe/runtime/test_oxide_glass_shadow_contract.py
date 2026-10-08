import math
import unittest
from unittest.mock import patch
from contextlib import nullcontext

from oxide_glass_shadow_contract import ContractError, normalize_request
from oxide_glass_shadow_runner import run_batch, verify_health


PRE = {
    "vocab": {"SiO2": 1, "B2O3": 2, "Na2O": 3, "Al2O3": 4},
    "max_tokens": 4,
    "tasks": ["DENSITY_20C", "YOUNG_MODULUS", "CTE_FIXED_T", "THERMAL_CONDUCTIVITY"],
}
BASE = {
    "request_id": "glass-001",
    "composition_mol_percent": {"SiO2": 70.0, "B2O3": 15.0, "Na2O": 10.0, "Al2O3": 5.0},
    "task": "DENSITY_20C",
    "condition_temperature_C": 20.0,
}


class GlassShadowContractTests(unittest.TestCase):
    def check_error(self, body, code):
        with self.assertRaises(ContractError) as caught:
            normalize_request(body, PRE)
        self.assertEqual(caught.exception.code, code)

    def test_valid_density_is_always_internal_only(self):
        result = normalize_request(BASE, PRE)
        self.assertEqual(sum(result["composition_mol_percent"].values()), 100.0)
        self.assertTrue(result["shadow_task_admitted"])
        self.assertFalse(result["comparison_allowed"])
        self.assertFalse(result["display_allowed"])
        self.assertFalse(result["production_release_authorized"])

    def test_rounding_tolerance_only(self):
        body = {**BASE, "composition_mol_percent": {"SiO2": 70.0, "B2O3": 15.0, "Na2O": 10.0, "Al2O3": 5.005}}
        result = normalize_request(body, PRE)
        self.assertAlmostEqual(sum(result["composition_mol_percent"].values()), 100.0)
        for borderline in (4.97, 4.98, 4.99, 5.01, 5.02, 5.05):
            body["composition_mol_percent"]["Al2O3"] = borderline
            result = normalize_request(body, PRE)
            self.assertAlmostEqual(sum(result["composition_mol_percent"].values()), 100.0)
        body["composition_mol_percent"]["Al2O3"] = 5.06
        self.check_error(body, "composition_closure")

    def test_string_bool_and_nan_are_not_numbers(self):
        for bad in ("70", True, math.nan, math.inf):
            body = {**BASE, "composition_mol_percent": {**BASE["composition_mol_percent"], "SiO2": bad}}
            self.check_error(body, "invalid_number")

    def test_nan_temperature_cannot_bypass_density_check(self):
        self.check_error({**BASE, "condition_temperature_C": math.nan}, "invalid_number")

    def test_unknown_oxide_and_missing_silica(self):
        body = {**BASE, "composition_mol_percent": {"SiO2": 70.0, "Unlisted2O": 30.0}}
        self.check_error(body, "unknown_oxide")
        body["composition_mol_percent"] = {"B2O3": 100.0}
        self.check_error(body, "unsupported_family")

    def test_fixed_cte_uses_documented_training_temperature(self):
        body = {**BASE, "task": "CTE_FIXED_T", "condition_temperature_C": 55.0}
        self.assertFalse(normalize_request(body, PRE)["shadow_task_admitted"])
        self.check_error({**body, "condition_temperature_C": 45.0}, "temperature_outside_training_range")
        self.check_error({**body, "condition_temperature_C": 360.0}, "temperature_outside_training_range")

    def test_thermal_conductivity_is_not_service_shadow(self):
        body = {**BASE, "task": "THERMAL_CONDUCTIVITY", "condition_temperature_C": None}
        self.assertFalse(normalize_request(body, PRE)["shadow_task_admitted"])

    def test_unexpected_fields_rejected(self):
        self.check_error({**BASE, "claimed_lab_result": 2.3}, "unsupported_fields")


class ShadowBatchTests(unittest.TestCase):
    def test_real_inference_health_contract_rejects_drift(self):
        expected = {
            "density": {"task": "DENSITY_20C", "value": 2.5, "unit": "g_cm-3"},
            "cte": {"status": "skipped"},
        }
        output = {"ok": True, "display_allowed": False, "production_release_authorized": False,
                  "results": [
                      {"request_id": "density", "status": "predicted_diagnostic",
                       "prediction": {"task": "DENSITY_20C", "value": 2.5, "unit": "g_cm-3"}},
                      {"request_id": "cte", "status": "skipped"},
                  ]}
        verify_health(output, expected)
        output["results"][0]["prediction"]["value"] = 2.51
        with self.assertRaises(RuntimeError):
            verify_health(output, expected)

    def test_batch_keeps_results_private_and_isolates_invalid_input(self):
        class TorchStub:
            @staticmethod
            def inference_mode():
                return nullcontext()

            @staticmethod
            def device(name):
                return name

        class ResearchStub:
            @staticmethod
            def infer(state, model, request, device):
                assert request["task"] == "DENSITY_20C"
                return {"prediction": 2.5, "unit": "g_cm-3"}

        payload = {"requests": [
            BASE,
            {**BASE, "request_id": "bad", "condition_temperature_C": math.nan},
            {**BASE, "request_id": "k", "task": "THERMAL_CONDUCTIVITY", "condition_temperature_C": None},
        ]}
        # The model encoder is separately checked by actual-checkpoint
        # scalar/batch and training-encoder parity; here test isolation only.
        with patch('oxide_glass_shadow_runner.infer_many',
                   return_value=[{'prediction':2.5,'unit':'g_cm-3','task':'DENSITY_20C'}]) as predict:
            result = run_batch(
                payload, {"model_name": "glass-test", "preprocessor": PRE},
                object(), ResearchStub, TorchStub, "abc",
            )
            self.assertEqual(len(predict.call_args.args[0]),1)
        self.assertEqual(
            [row["status"] for row in result["results"]],
            ["predicted_diagnostic", "invalid_input", "skipped"],
        )
        self.assertEqual(result["results"][0]["prediction"]["value"], 2.5)
        self.assertFalse(result["display_allowed"])
        self.assertFalse(result["production_release_authorized"])
        self.assertTrue(all(not row["comparison_allowed"] for row in result["results"]))

    def test_batch_model_failure_is_private_and_cannot_expose_prediction(self):
        with patch('oxide_glass_shadow_runner.infer_many',side_effect=RuntimeError('test-only failure')):
            result=run_batch({'requests':[BASE]}, {'model_name':'test','preprocessor':PRE},
                             object(),object(),object(),'abc')
        self.assertEqual(result['results'][0]['status'],'inference_error')
        self.assertNotIn('prediction',result['results'][0])
        self.assertIs(result['display_allowed'],False)

    def test_batch_request_cap_cannot_be_bypassed(self):
        with self.assertRaises(ValueError):
            run_batch({'requests':[BASE]*4097},{},None,None,None,'abc')


if __name__ == "__main__":
    unittest.main()
