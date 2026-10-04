"""No GPU/network contract, routing, screening and report regressions."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from src.material_workflow.constraints import constraint_from_payload
from src.material_workflow.omatg_contract import formula_counts, ELEMENTS
from tools.run_omatg_generation import species_mask_indices
from src.material_workflow.omatg_pipeline import screening_state, run_omatg_pipeline, _run_claimed
from src.material_workflow.omatg_report import build_plan, build_result_report
from src.material_workflow.schemas import GeneratedCandidate, GenerationConstraint, GenerationManifest, NewMaterialPipelineResult, ValidationResult, RankedCandidate


class OMatGContractTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def parse(self, **fields):
        return constraint_from_payload({"taskid": "test-omatg", "new_material": fields})

    def test_default_count_is_10_both_routes(self):
        csp = self.parse(target_formula="LiAlO2")
        dng = self.parse(allowed_elements=["Li", "Al", "O"])
        self.assertEqual((csp.generation_mode, dng.generation_mode), ("csp", "dng"))
        self.assertEqual((csp.requested_candidate_count, dng.requested_candidate_count), (10, 10))
        self.assertFalse(dng.refine_with_csp)

    def test_extra_csp_is_explicit_boolean_and_dng_only(self):
        self.assertTrue(self.parse(allowed_elements=["Li", "O"], refine_with_csp=True).refine_with_csp)
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "布尔"):
                self.parse(allowed_elements=["Li", "O"], refine_with_csp=value)
        with self.assertRaisesRegex(ValueError, "仅用于 DNG"):
            self.parse(target_formula="LiAlO2", refine_with_csp=True)

    def test_parameter_sources_distinguish_defaults_and_user_input(self):
        default = self.parse(allowed_elements=["Li", "O"])
        explicit = self.parse(allowed_elements=["Li", "O"], num_atoms=8, target_properties={"energy_above_hull": 0.05})
        self.assertEqual(default.parameter_sources["energy_above_hull"], "服务后筛默认")
        self.assertEqual(explicit.parameter_sources["energy_above_hull"], "用户确认字段")
        self.assertEqual(default.parameter_sources["num_atoms"], "服务默认")
        self.assertEqual(explicit.parameter_sources["num_atoms"], "用户确认字段")
        self.assertEqual(default.parameter_sources["energy_above_hull_mode"], "参考线")

    def test_stability_reference_does_not_block_but_explicit_hard_limit_does(self):
        reference = self.parse(target_formula="LiAlO2")
        hard = self.parse(target_formula="LiAlO2", stability_limit_mode="hard")
        v = ValidationResult(candidate_id="csp-1", status="ok", is_valid=True,
            energy_above_hull=0.09083, metadata={"mattersim": {"relaxation_converged": True}})
        observed = screening_state(v, reference)
        self.assertEqual(observed["state"], "passed")
        self.assertTrue(observed["stability_review"])
        self.assertEqual(screening_state(v, hard)["state"], "failed")
        self.assertIn("凸包能超过上限", screening_state(v, hard)["failures"])
        with self.assertRaisesRegex(ValueError, "stability_limit_mode"):
            self.parse(target_formula="LiAlO2", stability_limit_mode="other")

    def test_formula_atom_count_and_parentheses(self):
        self.assertEqual(formula_counts("Ca(OH)2"), {"Ca": 1, "O": 2, "H": 2})
        self.assertEqual(formula_counts("LiAlO₂"), {"Li": 1, "Al": 1, "O": 2})
        self.assertEqual(self.parse(target_formula="LiAlO2", formula_units=2).num_atoms, 8)
        for formula in ("Li0.5Al0.5O2", "PLA", "Fe0", "(LiAlO2", "Li/Al/O", "LiAlO2)"):
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                formula_counts(formula)
        with self.assertRaisesRegex(ValueError, "20"):
            self.parse(target_formula="Li6PS5Cl", formula_units=2)

    def test_open_space_vocabulary_ends_at_atomic_number_100(self):
        self.assertEqual(len(ELEMENTS), 100)
        self.assertEqual(ELEMENTS[-1], "Fm")
        self.assertEqual(species_mask_indices(list(range(1, 101)), 100), list(range(100)))
        for numbers in ([], [0], [101], [100, 102]):
            with self.subTest(numbers=numbers), self.assertRaisesRegex(ValueError, "vocabulary"):
                species_mask_indices(numbers, 100)
        for elements in (["Md"], ["No"]):
            with self.subTest(elements=elements), self.assertRaisesRegex(ValueError, "1–100"):
                self.parse(allowed_elements=elements)

    def test_whitelist_is_not_required_elements(self):
        constraints = self.parse(allowed_elements=["Li", "Al", "O"])
        self.assertEqual(constraints.required_elements, [])
        explicit = self.parse(allowed_elements=["Li", "Al", "O"], required_elements=["Li", "O"])
        self.assertEqual(explicit.required_elements, ["Li", "O"])
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.parse(allowed_elements=["Li", "O"], required_elements=["Al"])
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.parse(target_formula="LiAlO2", excluded_elements=["Li"])

    def test_current_elements_do_not_restore_old_formula(self):
        c = constraint_from_payload({"taskid": "precedence", "idea": "改为 Li-Al-O 晶体探索", "history": "助手：建议 BaTiO3"})
        self.assertIsNone(c.target_formula)
        self.assertEqual(c.allowed_elements, ["Li", "Al", "O"])
        self.assertEqual(c.generation_mode, "dng")

    def test_explicit_formula_ignores_incidental_prose_elements(self):
        payload = {"taskid": "formula-priority", "idea": "探索锂铝氧周期晶体的稳定构型并比较已接入性质。",
                   "new_material": {"generation_mode": "csp", "target_formula": "LiAlO2"}}
        parsed = constraint_from_payload(payload)
        self.assertEqual(parsed.target_formula, "LiAlO2")
        self.assertEqual(set(parsed.allowed_elements), {"Li", "Al", "O"})
        payload["new_material"]["allowed_elements"] = ["Li", "O"]
        with self.assertRaisesRegex(ValueError, "化学式元素不在允许元素集合"):
            constraint_from_payload(payload)

    def test_missing_composition_does_not_call_llm_or_invent_elements(self):
        from src.material_workflow.llm_constraint_inference import resolve_generation_request, GenerationInputRequired
        with patch("src.material_workflow.llm_constraint_inference.enrich_payload_with_llm_elements") as inference:
            with self.assertRaises(GenerationInputRequired):
                asyncio.run(resolve_generation_request({"taskid": "missing", "idea": "希望高稳定的新晶体"}))
            inference.assert_not_called()
        opened = self.parse(allow_open_chemical_space=True, material_family="无机半导体")
        self.assertEqual(opened.generation_mode, "dng")
        self.assertEqual(opened.allowed_elements, [])

    def test_invalid_counts_modes_and_units_block_before_gpu(self):
        for fields in ({"max_candidates": 11}, {"max_candidates": 0}, {"max_candidates": True}, {"formula_units": 1.5}, {"generation_mode": "irl"}, {"seed": -1}, {"target_properties": {"energy_above_hull": float("nan")}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.parse(target_formula="LiAlO2", **fields)
        with self.assertRaises(ValueError):
            self.parse(target_formula="LiAlO2", property_constraints={"band_gap": {"min": 2, "max": 1}})
        with self.assertRaises(ValueError):
            self.parse(target_formula="LiAlO2", property_constraints={"band_gap": {"min": 2, "unit": "meV"}})

    def test_unknown_hard_property_cannot_silently_pass(self):
        with self.assertRaisesRegex(ValueError, "上下限"):
            self.parse(target_formula="LiAlO2", property_constraints={"yield_strength": {"min": 500}})

    def test_postscreen_missing_head_is_not_passed(self):
        c = self.parse(target_formula="LiAlO2", property_constraints={"band_gap": {"min": 2, "unit": "eV"}})
        v = ValidationResult(candidate_id="csp-1", status="ok", is_valid=True, energy_above_hull=0.02, metadata={"mattersim": {"relaxation_converged": True}})
        self.assertEqual(screening_state(v, c)["state"], "incomplete")
        v.property_predictions["band_gap"] = {"value": 1.0}
        self.assertEqual(screening_state(v, c)["state"], "failed")
        v.property_predictions["band_gap"]["value"] = 3.0
        self.assertEqual(screening_state(v, c)["state"], "passed")
        v.metadata["mattersim"]["relaxation_converged"] = False
        self.assertEqual(screening_state(v, c)["state"], "incomplete")

    def test_symmetry_checked_on_relaxed_geometry(self):
        c = self.parse(target_formula="LiAlO2", symmetry_constraints={"non_centrosymmetric": True})
        v = ValidationResult(candidate_id="csp-1", status="ok", is_valid=True, energy_above_hull=0.02, metadata={"non_centrosymmetric": False, "mattersim": {"relaxation_converged": True}})
        self.assertEqual(screening_state(v, c)["state"], "failed")

    def test_plan_exposes_counts_and_actual_inference_not_training(self):
        c = self.parse(target_formula="LiAlO2")
        markdown = build_plan(c, 10)
        self.assertIn("已训练的模型进行推理采样", markdown)
        self.assertIn("10 个", markdown)
        self.assertIn("参考相能量比较", markdown)
        self.assertIn("### 计算方案", markdown)
        self.assertNotIn("### 5.", markdown)

    def run_mock_route(self, constraints, converged=True):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def generation(c, directory, count):
                calls.append((c.generation_mode, c.seed, c.target_formula, count))
                candidate = GeneratedCandidate(candidate_id=c.generation_mode + "-001", formula_pretty="Li2O", cif_path=Path(temp) / "candidate.cif", source="omatg_" + c.generation_mode,
                    metadata={"composition_ok": True, "element_counts": {"Li": 2, "O": 1}})
                return GenerationManifest(taskid=c.taskid, status="ok", backend="omatg", candidates=[candidate], metadata={"mode": c.generation_mode})
            def evaluate(c, g, directory):
                return [ValidationResult(candidate_id=g.candidates[0].candidate_id, status="ok", is_valid=True, formula_pretty="Li2O", energy_above_hull=0.01,
                    metadata={"composition_ok": True, "mattersim": {"relaxation_converged": converged, "source_name": "candidate.cif", "relaxed_energy_per_atom_ev": -4.0}},
                    artifacts={"relaxed_structure_path": str(Path(temp) / "relaxed.cif")})]
            with patch("src.material_workflow.omatg_pipeline.run_omatg_generation", generation), patch("src.material_workflow.omatg_pipeline.evaluate", evaluate), patch("src.material_workflow.omatg_pipeline.enrich_validations_with_alignn") as alignn:
                result = _run_claimed(constraints, Path(temp), 10)
            self.assertEqual(alignn.call_count, 1)
            return result, calls

    def test_direct_dng_does_not_generate_csp_even_with_a_valid_composition(self):
        result, calls = self.run_mock_route(self.parse(allowed_elements=["Li", "O"]))
        self.assertEqual([row[0] for row in calls], ["dng"])
        self.assertEqual(result.generation.metadata["executed_generation_modes"], ["dng"])
        self.assertIsNone(result.generation.metadata["selected_dng_composition"])
        self.assertEqual(result.ranked_candidates[0].candidate.candidate_id, "dng-001")
        self.assertIn("已生成", result.message)
        self.assertIn("结构优化收敛", result.message)
        markdown = build_result_report(result)
        self.assertIn("| DNG | 1 |", markdown)
        self.assertNotIn("| CSP |", markdown)

    def test_open_dng_also_does_not_generate_csp(self):
        result, calls = self.run_mock_route(self.parse(allow_open_chemical_space=True, material_family="无机晶体"))
        self.assertEqual([row[0] for row in calls], ["dng"])
        self.assertIn("开放化学空间 DNG", build_result_report(result))

    def test_opt_in_csp_preserves_selected_exact_counts_and_increments_seed(self):
        c = self.parse(allowed_elements=["Li", "O"], refine_with_csp=True, seed=42)
        result, calls = self.run_mock_route(c)
        self.assertEqual(calls, [("dng", 42, None, 10), ("csp", 43, "Li2O", 10)])
        self.assertEqual(result.generation.metadata["executed_generation_modes"], ["dng", "csp"])
        self.assertEqual(result.generation.metadata["selected_dng_composition"]["element_counts"], {"Li": 2, "O": 1})

    def test_opt_in_csp_cannot_start_without_a_converged_dng_source(self):
        result, calls = self.run_mock_route(self.parse(allowed_elements=["Li", "O"], refine_with_csp=True), converged=False)
        self.assertEqual([row[0] for row in calls], ["dng"])
        self.assertIsNone(result.generation.metadata["selected_dng_composition"])
        self.assertIn("未形成", result.message)

    def test_csp_default_still_only_samples_once(self):
        result, calls = self.run_mock_route(self.parse(target_formula="Li2O"))
        self.assertEqual([row[0] for row in calls], ["csp"])
        self.assertEqual(result.generation.metadata["executed_generation_modes"], ["csp"])

    def test_final_report_has_reference_style_and_no_unperformed_model(self):
        c = self.parse(target_formula="LiAlO2")
        candidate = GeneratedCandidate(candidate_id="csp-001", formula_pretty="LiAlO2")
        generation = GenerationManifest(taskid=c.taskid, status="ok", backend="omatg", candidates=[candidate], metadata={"mode": "csp"})
        v = ValidationResult(candidate_id="csp-001", status="ok", is_valid=False, errors=["原子过近"], metadata={"screening": {"state": "failed", "failures": ["原子过近"], "missing": []}})
        result = NewMaterialPipelineResult(taskid=c.taskid, status="ok", constraints=c, generation=generation, validations=[v], ranked_candidates=[RankedCandidate(candidate=candidate, rank=1, score=0, validation=v)])
        markdown = build_result_report(result)
        for heading in ("### 1. 材料设计需求与探索目标", "### 2. 材料组成范围与计算条件", "### 3. 晶体生成、结构优化与性能评价方法", "### 4. 晶体生成结果与候选结构比较", "### 5. 候选晶体的复核与后续验证建议"):
            self.assertEqual(markdown.count(heading), 1)
        methods = markdown.split("### 3.", 1)[1].split("### 4.", 1)[0]
        self.assertNotIn("MatterSim", methods)
        self.assertNotIn("ALIGNN", methods)
        self.assertNotIn("Step ", markdown)
        self.assertIn("\n$$\n", markdown)
        self.assertTrue(all(line == "$$" for line in markdown.splitlines() if "$$" in line))
        self.assertIn("原子过近", markdown)

    def test_progress_precreated_directory_does_not_block_pipeline(self):
        c = self.parse(target_formula="LiAlO2")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / c.taskid
            directory.mkdir()
            (directory / "progress.json").write_text("{}")
            with patch("src.material_workflow.runtime_lock.gpu_job_lock") as lock, patch("src.material_workflow.omatg_pipeline._run_claimed", return_value="claimed"):
                self.assertEqual(run_omatg_pipeline(c, Path(temp), 10), "claimed")
                with self.assertRaises(FileExistsError):
                    run_omatg_pipeline(c, Path(temp), 10)

    def test_zero_candidate_report_preserves_reason(self):
        c = self.parse(allowed_elements=["Li", "Al", "O"])
        generation = GenerationManifest(taskid=c.taskid, status="ok", backend="omatg", metadata={"dng_stage": {"generation": {"candidates": []}, "validations": []}})
        result = NewMaterialPipelineResult(taskid=c.taskid, status="ok", constraints=c, generation=generation, message="没有合规起点")
        markdown = build_result_report(result)
        self.assertIn("没有合规起点", markdown)
        self.assertNotIn("训练完成", markdown)
        self.assertNotIn("NaN", markdown)

    def test_queue_timeout_is_an_explicit_unfinished_result(self):
        c = self.parse(target_formula="LiAlO2")
        with tempfile.TemporaryDirectory() as temp:
            with patch("src.material_workflow.runtime_lock.gpu_job_lock", side_effect=TimeoutError("queue")):
                result = run_omatg_pipeline(c, Path(temp), 10)
            self.assertEqual(result.status, "timeout")
            self.assertEqual(result.generation.backend, "omatg")
            self.assertIn("计算队列", result.message)
            self.assertTrue((Path(temp) / c.taskid / "new_material_pipeline_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
