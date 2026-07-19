import unittest

from src.material_workflow.constraints import _formula_from_text, constraint_from_payload


class FormulaExtractionTests(unittest.TestCase):
    def test_fdm_fff_is_not_a_formula(self):
        self.assertIsNone(_formula_from_text("采用 FDM/FFF 丝材打印，适配 PLA、PETG 和 TPU"))

    def test_standard_formulas_remain_supported(self):
        self.assertEqual(_formula_from_text("候选为 Li3PS4"), "Li3PS4")
        self.assertEqual(_formula_from_text("候选为 NaCl"), "NaCl")

    def test_filament_request_without_inorganic_system_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "FDM/FFF 丝材工艺"):
            constraint_from_payload({
                "taskid": "fdm-test",
                "idea": "采用 FDM/FFF 丝材打印，优先适配拓竹 A1，评估机翼主梁和发动机短舱。",
            })


if __name__ == "__main__":
    unittest.main()
