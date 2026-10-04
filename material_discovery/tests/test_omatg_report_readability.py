"""Customer headings and explanations change; scientific evidence must not."""
from copy import deepcopy
from unittest import TestCase

from src.material_workflow.omatg_chart import build_comparison_data
from src.material_workflow.omatg_report import build_plan, build_result_report
from src.material_workflow.schemas import (GeneratedCandidate, GenerationConstraint,
    GenerationManifest, NewMaterialPipelineResult, RankedCandidate, ValidationResult)


class ReadableReportTest(TestCase):
    def result(self, *, state="passed", hull=0.0411, duplicate=False, predictions=True):
        c = GenerationConstraint(taskid="readability", generation_mode="dng", allowed_elements=["Fe", "Cr", "Ni"], required_elements=["Fe"],
            target_properties={"energy_above_hull": 0.05}, validation_targets={"bulk_modulus": True}, min_distinct_elements=2,
            parameter_sources={"energy_above_hull": "服务后筛默认", "num_atoms": "用户确认字段"},
            task_context={"scenario": "为火箭外壳的局部部位寻找候选晶体", "scenario_label": "火箭外壳的部分", "scenario_origin": "upstream"})
        candidate = GeneratedCandidate(candidate_id="dng-009", formula_pretty="CrFe3", metadata={"element_counts": {"Fe": 6, "Cr": 2}})
        v = ValidationResult(candidate_id=candidate.candidate_id, status="ok", is_valid=True, formula_pretty="CrFe3", energy_above_hull=hull,
            metadata={"composition_ok": True, "screening": {"state": state, "failures": ["凸包能超过上限"] if state == "failed" else [], "missing": ["凸包能估算"] if hull is None else []},
                "mattersim": {"relaxation_converged": True, "relaxed_energy_per_atom_ev": -8.72, "checkpoint_sha256": "secret-not-secret-audit-hash"},
                "element_counts": {"Fe": 6, "Cr": 2}, "num_sites": 8})
        if predictions:
            v.property_predictions = {"band_gap": {"value": -0.0015, "model": "alignn_head", "model_version": "version", "unit": "eV"},
                "bulk_modulus": {"value": 236.39, "model": "alignn_head", "model_version": "version", "unit": "GPa"}}
        g = GenerationManifest(taskid=c.taskid, status="ok", backend="omatg", candidates=[candidate], metadata={"mode": "dng"})
        ranked = [RankedCandidate(candidate=candidate, rank=1, score=0, validation=v)]
        validations = [v]
        if duplicate:
            copy = deepcopy(candidate)
            copy.candidate_id = "dng-003"
            copied_v = deepcopy(v)
            copied_v.candidate_id = copy.candidate_id
            copied_v.metadata["duplicate_of"] = candidate.candidate_id
            g.candidates.append(copy)
            ranked.append(RankedCandidate(candidate=copy, rank=2, score=0, validation=copied_v))
            validations.append(copied_v)
        return NewMaterialPipelineResult(taskid=c.taskid, status="ok", constraints=c, generation=g, validations=validations, ranked_candidates=ranked)

    def test_titles_are_complete_ordered_and_parent_child_numbers_align(self):
        text = build_result_report(self.result())
        headings = [line for line in text.splitlines() if line.startswith("### ")]
        self.assertEqual(headings, ["### 1. 材料设计需求与探索目标", "### 2. 材料组成范围与计算条件",
            "### 3. 晶体生成、结构优化与性能评价方法", "### 4. 晶体生成结果与候选结构比较",
            "### 5. 优选晶体的组成、结构、预测性能与验证建议"])
        parent = None
        for line in text.splitlines():
            if line.startswith("### "):
                parent = line[4]
            if line.startswith("#### "):
                self.assertEqual(line[5], parent)
        for old in ("问题描述", "使用模型与数学定义", "完整性质卡", "评分卡", "来源及可调整方式", "模型权重追踪"):
            self.assertNotIn(old, text)

    def test_report_and_graph_share_scheme_names_and_raw_ids_stay_in_records(self):
        result = self.result(duplicate=True)
        text = build_result_report(result)
        rows = build_comparison_data(result.to_dict())["rows"]
        self.assertEqual([r["candidate_id"] for r in rows], ["dng-009", "dng-003"])
        self.assertRegex(rows[0]["display_name"], r"^JC-[0-9A-F]{10}-01$")
        self.assertRegex(rows[1]["display_name"], r"^JC-[0-9A-F]{10}-02$")
        for row in rows:
            self.assertIn(row["display_name"], text)
            self.assertNotIn(row["candidate_id"], text)
        self.assertIn("与 " + rows[0]["display_name"] + " 属于同一近似结构", text)
        self.assertIn(rows[0]["display_name"] + "（优选结构）", text)
        self.assertIn("候选编号在本次任务中唯一", text)

    def test_composition_structure_and_properties_are_in_one_section_with_meanings(self):
        text = build_result_report(self.result())
        final = text.split("### 5.", 1)[1]
        for content in ("铬（Cr）", "铁（Fe）", "75.0000", "25.0000", "| 空间群 |", "| 单原子能量 |", "| 体积模量 |", "抵抗体积压缩", "按原子个数计算的百分比", "1 Å = 10⁻¹⁰ m"):
            self.assertIn(content, final)
        self.assertNotIn("secret-not-secret-audit-hash", text)
        self.assertTrue(all("来源" not in line for line in text.splitlines() if line.startswith("|") and ("---" not in line)))

    def test_failed_lowest_candidate_is_a_review_object_never_a_passing_recommendation(self):
        text = build_result_report(self.result(state="failed", hull=0.09083))
        self.assertIn("### 5. 候选晶体的复核与后续验证建议", text)
        self.assertNotIn("### 5. 优选晶体", text)
        self.assertIn("以下汇总待复核结构", text)
        self.assertIn("0.09083", text)
        self.assertIn("稳定性差值超过上限", text)
        self.assertNotIn("优先验证方案", text)

    def test_above_reference_keeps_selected_structure_and_clear_warning(self):
        result = self.result(hull=0.09083)
        result.validations[0].metadata["screening"]["stability_review"] = True
        text = build_result_report(result)
        self.assertIn("JC-A6B2CBEB7F-01（优选结构）", text)
        self.assertIn("优选结构（JC-A6B2CBEB7F-01）", text)
        self.assertIn("高于参考线，需高精度复核", text)
        self.assertIn("建议优先通过统一精度计算复核", text)

    def test_missing_and_negative_values_are_not_silently_rewritten(self):
        text = build_result_report(self.result(state="incomplete", hull=None))
        self.assertIn("稳定性结果未记录", text)
        self.assertIn("-0.0015", text)
        self.assertIn("负带隙为回归模型超出物理范围的原始预测", text)
        self.assertNotIn("稳定性差值（凸包能估算） | 0.00000", text)

    def test_additional_predicted_properties_do_not_point_to_absent_goal_explanations(self):
        result = self.result()
        result.constraints.validation_targets = {}
        text = build_result_report(result)
        comparison = text.split("#### 4.2", 1)[1].split("#### 4.3", 1)[0]
        self.assertIn("带隙——表示价带与导带之间的能量差", comparison)
        self.assertIn("需要通过电子结构计算复核", comparison)
        self.assertNotIn("含义见第 1 节", comparison)

    def test_scene_defaults_actual_inference_and_formulas_have_explanations(self):
        text = build_result_report(self.result())
        for content in ("承接上文火箭外壳局部部位", "允许但非必含元素", "（默认设置）", "（已确认）", "本任务调用预训练模型进行推理采样", "优化后的局部低能结构", "有限温度下的稳定性可进一步加入熵和振动贡献"):
            self.assertIn(content, text)
        self.assertTrue(all(line == "$$" for line in text.splitlines() if "$$" in line))
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line == "$$" and index > 0 and lines[index - 1] != "":
                self.assertNotEqual(lines[index + 2] if index + 2 < len(lines) else "", "")

    def test_report_does_not_mutate_parameters_or_scientific_results(self):
        result = self.result(duplicate=True)
        result.constraints.seed = 93517
        result.generation.metadata.update(model_provenance={"seed": 93517, "integration_time_steps": 710, "package_version": "2.0.0"},
                                          scientific_wall_seconds=132.3, calculation_settings={"relax_max_steps": 500})
        result.validations[0].metadata["mattersim"]["fmax_ev_angstrom"] = 0.05
        before = deepcopy(result.to_dict())
        text = build_result_report(result)
        plan = build_plan(result.constraints)
        self.assertEqual(result.to_dict(), before)
        for internal in ("93517", "随机采样种子", "时间网格", "生成过程设置", "seed="):
            self.assertNotIn(internal, text + plan)
        parameters = text.split("### 2.", 1)[1].split("### 3.", 1)[0]
        self.assertIn("#### 2.3 结构优化与相似结构处理", parameters)
        self.assertIn("最多 500 步", parameters)
        self.assertIn("0.05 eV/Å", parameters)
        self.assertNotIn("132.3", parameters)
        self.assertIn("性质评价用时 132.3 秒", text.split("### 4.", 1)[1])

    def test_missing_requested_property_is_visible_in_complete_property_table(self):
        text = build_result_report(self.result(predictions=False))
        self.assertIn("| 体积模量 | —（性质结果未记录） |", text)
        methods = text.split("### 3.", 1)[1].split("### 4.", 1)[0]
        self.assertNotIn("ALIGNN", methods)

    def test_generated_but_unranked_candidates_remain_visible_as_pending(self):
        result = self.result()
        result.ranked_candidates.clear()
        result.validations.clear()
        text = build_result_report(result)
        self.assertIn("| JC-A6B2CBEB7F-01 | 铬-铁晶体（CrFe3） | —（评价记录缺失） |", text)
        self.assertIn("没有该候选的评价结果或执行状态", text)
        self.assertNotIn("### 5. 优选晶体", text)
        self.assertNotIn("| 组成与周期几何检查 | pymatgen", text)

    def test_csp_does_not_describe_fixed_formula_elements_as_optional(self):
        result = self.result()
        result.constraints.generation_mode = "csp"
        result.constraints.target_formula = "Fe6CrNi"
        result.generation.metadata["mode"] = "csp"
        result.generation.candidates[0].formula_pretty = "CrFe6Ni"
        result.generation.candidates[0].metadata["element_counts"] = {"Cr": 1, "Fe": 6, "Ni": 1}
        result.validations[0].formula_pretty = "CrFe6Ni"
        result.validations[0].metadata["element_counts"] = {"Cr": 1, "Fe": 6, "Ni": 1}
        text = build_result_report(result)
        self.assertIn("本次由固定化学式确定全部元素及其数量", text)
        self.assertIn("按固定化学式全部保留", text)
        self.assertNotIn("列出的元素不一定全部出现", text)
