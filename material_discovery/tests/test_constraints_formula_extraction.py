import unittest

from src.material_workflow.constraints import _formula_from_text, constraint_from_payload, normalize_taskid


class FormulaExtractionTests(unittest.TestCase):
    def test_nonportable_taskid_is_mapped_to_a_safe_local_key(self):
        safe_taskid, external_taskid = normalize_taskid("上游任务/2026-07-23")
        self.assertEqual(external_taskid, "上游任务/2026-07-23")
        self.assertRegex(safe_taskid, r"^[A-Za-z0-9_.-]{1,128}$")
        self.assertNotIn("/", safe_taskid)
        with self.assertRaisesRegex(ValueError, "navigation"):
            normalize_taskid("..")

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

    def test_alloy_composition_request_is_rejected_by_service_boundary(self):
        with self.assertRaisesRegex(ValueError, "alloy_composition_optimization"):
            constraint_from_payload({
                "taskid": "alloy-scope-test",
                "idea": "优化 Nb-Mo-Ta-W 高熵合金的原子百分比和成分空间。",
            })

    def test_alloy_structure_generation_request_is_not_misrouted_to_composition_optimization(self):
        constraint = constraint_from_payload({
            "taskid": "alloy-structure-generation",
            "idea": "我考虑传统制备方法，基于上述信息尝试做一个新材料结构生成。",
            "history": "用户：生成 Nb-Mo-Ta-W 高温高熵合金候选晶体结构。",
        })
        self.assertEqual(constraint.allowed_elements, ["Nb", "Mo", "Ta", "W"])
        self.assertEqual(constraint.target_properties["energy_above_hull"], 0.05)


if __name__ == "__main__":
    unittest.main()
