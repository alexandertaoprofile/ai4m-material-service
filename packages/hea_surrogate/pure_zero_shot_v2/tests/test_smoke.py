from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from predict_joint import load_artifact, predict


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_artifact(ROOT / "model_artifact.json")

    def test_formula_prediction(self):
        result = predict(
            {
                "composition": "Cs0.2FA0.4MA0.4Pb(I0.6Br0.4)3",
                "temperatures_K": [170, 220, 330],
            },
            self.model,
        )
        self.assertEqual(result["mode"], "pure_zero_shot")
        self.assertEqual(len(result["curve"]), 3)
        self.assertGreaterEqual(result["parameters"]["Ea_high_eV"], 0.0)
        self.assertGreaterEqual(result["parameters"]["Ea_low_eV"], 0.0)
        self.assertTrue(all(row["predicted_sigma"] > 0 for row in result["curve"]))

    def test_fraction_prediction(self):
        result = predict(
            {
                "fractions": {
                    "cs_fraction": 0.0,
                    "fa_fraction": 0.5,
                    "ma_fraction": 0.5,
                    "iodide_fraction": 1.0,
                    "bromide_fraction": 0.0,
                }
            },
            self.model,
        )
        self.assertEqual(len(result["curve"]), 17)


if __name__ == "__main__":
    unittest.main()
