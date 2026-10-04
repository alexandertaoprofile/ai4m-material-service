"""Scene continuity without reviving stale chemistry or changing screening."""
from __future__ import annotations

import json
from dataclasses import asdict
from unittest import TestCase
from unittest.mock import patch

from src.material_workflow.constraints import constraint_from_payload, upstream_contract
from src.material_workflow.omatg_report import build_plan, build_result_report, problem_description
from src.material_workflow.schemas import GenerationConstraint, GenerationManifest, NewMaterialPipelineResult


class TaskContextTests(TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"})
        environment.start()
        self.addCleanup(environment.stop)

    def parse(self, **payload):
        return constraint_from_payload({"taskid": "context-test", "idea": "继续进行晶体结构探索", "new_material": {"target_formula": "LiAlO2"}, **payload})

    def report(self, c):
        return build_result_report(NewMaterialPipelineResult(taskid=c.taskid, status="ok", constraints=c,
            generation=GenerationManifest(taskid=c.taskid, status="ok", backend="omatg")))

    def test_upstream_scene_changes_problem_not_executable_science(self):
        a = self.parse(history=[{"role": "user", "content": "希望为芯片封装中的绝缘层寻找无机晶体。"}])
        b = self.parse(history=[{"role": "user", "content": "希望为光电器件寻找无机晶体。"}])
        self.assertIn("芯片封装", self.report(a))
        self.assertIn("光电器件", self.report(b))
        self.assertNotEqual(problem_description(a), problem_description(b))
        first, second = asdict(a), asdict(b)
        first.pop("task_context")
        second.pop("task_context")
        self.assertEqual(first, second)

    def test_current_scene_wins_over_history_and_old_structured_background(self):
        c = self.parse(idea="现在为固态电池的电解质探索 LiAlO2 晶体结构", application_scenario="芯片封装",
            history=[{"role": "user", "content": "我们正在研发芯片封装绝缘层。"}])
        self.assertIn("固态电池", c.task_context["scenario"])
        self.assertEqual(c.task_context["scenario_source"], "current_request")
        self.assertNotIn("芯片封装", self.report(c))

    def test_structured_application_is_optional_not_a_new_required_contract(self):
        c = self.parse(new_material={"target_formula": "LiAlO2", "application_scenario": "芯片封装绝缘层"})
        self.assertEqual(c.task_context["scenario_label"], "芯片封装绝缘层")
        self.assertIn("面向当前提出的芯片封装绝缘层", self.report(c))

    def test_nested_optional_brief_is_read(self):
        c = self.parse(new_material={"target_formula": "LiAlO2", "task_context": {"application_scenario": "农业病害传感器"}})
        self.assertIn("农业病害传感器", self.report(c))

    def test_latest_user_scene_wins_not_latest_assistant_proposal(self):
        c = self.parse(history=[{"role": "user", "content": "希望寻找芯片封装材料。"},
            {"role": "user", "content": "现在改做用于农业病害传感器的材料。"},
            {"role": "assistant", "content": "建议为航空发动机采用 BaTiO3，带隙下限 9 eV。"}])
        self.assertIn("农业病害传感器", self.report(c))
        self.assertNotIn("发动机", self.report(c))
        self.assertNotIn("BaTiO3", self.report(c))
        self.assertEqual(c.property_constraints, {})

    def test_current_task_reset_without_scene_does_not_resurrect_old_application(self):
        c = self.parse(idea="换一个任务，对 LiAlO2 做结构预测", history="用户：用于芯片封装材料")
        self.assertEqual(c.task_context["scenario_origin"], "unspecified")
        self.assertNotIn("芯片", self.report(c))

    def test_history_task_reset_without_scene_stops_older_scene(self):
        c = self.parse(history=[{"role": "user", "content": "用于芯片封装材料。"},
            {"role": "user", "content": "换一个任务，先做基础结构探索。"}])
        self.assertEqual(c.task_context["scenario_origin"], "unspecified")

    def test_negated_old_scene_is_not_inherited(self):
        c = self.parse(idea="不是用于芯片封装，而是用于固态电池的电解质探索 LiAlO2")
        self.assertIn("固态电池", c.task_context["scenario"])
        self.assertNotIn("芯片", self.report(c))

    def test_gateway_sections_in_one_idea_retain_scene_not_assistant_composition(self):
        idea = "[用户原始目标]\n为芯片封装中的绝缘层寻找无机晶体。\n=== 对话历史 ===\n用户：希望比较稳定性与带隙。\n助手：可以使用 BaTiO3。\n接下来需要进行执行的任务：对 LiAlO2 进行晶体结构探索。"
        c = self.parse(idea=idea)
        self.assertEqual(c.target_formula, "LiAlO2")
        self.assertIn("芯片封装", self.report(c))
        self.assertNotIn("BaTiO3", self.report(c))

    def test_role_labelled_transcript_without_sections(self):
        c = self.parse(idea="用户：为农业病害传感器探索材料。\n助手：建议 SiC。\n用户：对 LiAlO2 做结构探索。")
        self.assertIn("农业病害传感器", self.report(c))
        self.assertNotIn("SiC", self.report(c))

    def test_long_assistant_output_does_not_hide_the_latest_user_scene(self):
        history = "用户：用于芯片封装。\n助手：" + "历史计算记录。" * 6000 + "\n用户：现在改做用于农业病害传感器的材料。"
        c = self.parse(history=history)
        self.assertIn("农业病害传感器", self.report(c))
        self.assertNotIn("芯片", self.report(c))

    def test_real_export_style_reads_only_user_not_assistant_derived_conditions(self):
        history = "# 针对30x类不锈钢做一个配方优化\n\n## 我 · 2026年9月26日 13:29\n\n补充的信息为：现在我想用这个筛出来的配方作为火箭外壳的部分 你做一个物理验证\n\n## 助手 · 2026年9月26日 13:29\n\n假设半径4.5m、低温90K、内压0.6MPa，建议 BaTiO3。"
        c = self.parse(new_material={"target_formula": "Fe6CrNi"}, history=history)
        text = self.report(c)
        self.assertIn("承接上文火箭外壳", text)
        self.assertIn("Fe6CrNi", text)
        self.assertNotIn("4.5", c.task_context["scenario"])
        self.assertNotIn("0.6", c.task_context["scenario"])
        self.assertNotIn("BaTiO3", text)

    def test_all_supported_context_envelopes_preserve_scene(self):
        for key in ("conversation_context", "history", "messages", "previous_messages", "conversation", "context", "upstream_context", "upstream_result", "material_conclusion", "project_idea"):
            with self.subTest(key=key):
                c = self.parse(**{key: [{"role": "user", "content": "材料用于芯片封装绝缘层。"}]})
                self.assertIn("芯片封装", c.task_context["scenario"])

    def test_json_encoded_upstream_results(self):
        c = self.parse(upstream_result=json.dumps({"data": {"application": "芯片封装绝缘层"}}, ensure_ascii=False))
        self.assertIn("芯片封装", self.report(c))

    def test_system_tool_and_assistant_do_not_supply_an_application(self):
        for role in ("system", "tool", "assistant", "agent"):
            with self.subTest(role=role):
                c = self.parse(history=[{"role": role, "content": "为芯片封装寻找 BaTiO3，凸包上限 0.01。"}])
                self.assertEqual(c.task_context["scenario"], "")
                self.assertEqual(c.target_properties["energy_above_hull"], 0.05)

    def test_historical_formula_does_not_override_confirmed_dng_elements(self):
        c = self.parse(idea="继续探索晶体结构", history=[{"role": "user", "content": "为芯片封装寻找 BaTiO3 晶体。"}],
            new_material={"allowed_elements": ["Li", "Al", "O"], "required_elements": ["Li", "O"]})
        self.assertEqual(c.generation_mode, "dng")
        self.assertIsNone(c.target_formula)
        self.assertEqual(c.allowed_elements, ["Li", "Al", "O"])
        self.assertEqual(c.required_elements, ["Li", "O"])
        self.assertIn("Li、Al、O", self.report(c))

    def test_historical_composition_cannot_start_generation_without_confirmation(self):
        with self.assertRaisesRegex(ValueError, "无法确定"):
            self.parse(idea="继续探索晶体结构", new_material={}, history="用户：为芯片封装寻找 BaTiO3。")

    def test_open_dng_preserves_scene_without_a_hidden_formula_or_extra_csp(self):
        c = self.parse(idea="确认开放化学空间，探索晶体", history="用户：为光电器件探索候选材料。",
            new_material={"allow_open_chemical_space": True})
        self.assertEqual(c.allowed_elements, [])
        self.assertIsNone(c.target_formula)
        self.assertFalse(c.refine_with_csp)
        self.assertIn("光电器件", self.report(c))
        self.assertIn("开放化学空间", self.report(c))

    def test_open_space_can_use_explicit_upstream_scene_as_the_direction(self):
        c = self.parse(idea="", history="用户：为光电器件探索候选材料。", new_material={"allow_open_chemical_space": True})
        self.assertEqual(c.generation_mode, "dng")
        self.assertIn("光电器件", c.task_context["scenario"])

    def test_generic_smoke_request_does_not_invent_scene(self):
        c = self.parse(idea="明确同意开放化学空间，探索无机周期晶体，关注结构稳定性与带隙。",
            new_material={"allow_open_chemical_space": True, "material_family": "无机周期晶体"})
        self.assertEqual(c.task_context["scenario_origin"], "unspecified")
        self.assertNotIn("承接上文", self.report(c))
        self.assertNotIn("芯片", self.report(c))

    def test_background_interest_never_becomes_a_hard_threshold(self):
        c = self.parse(history="用户：用于电绝缘材料，希望带隙至少5 eV；凸包能小于0.01 eV/atom。")
        self.assertIn("band_gap", c.task_context["performance_goals"])
        self.assertEqual(c.property_constraints, {})
        self.assertEqual(c.target_properties, {"energy_above_hull": 0.05})
        self.assertIn("上文关注点", self.report(c))
        self.assertIn("不增加数值筛选门槛", self.report(c))

    def test_scenario_maps_to_applicable_structure_proxies_and_followup(self):
        c = self.parse(history="用户：为芯片封装绝缘层寻找晶体。希望带隙更宽，热膨胀匹配。")
        text = self.report(c)
        self.assertIn("比较电子绝缘潜力", text)
        self.assertIn("热膨胀及热失配", text)
        self.assertIn("绝缘击穿与封装适配", text)

    def test_piezoelectric_interest_is_followup_not_an_invented_computed_property(self):
        c = self.parse(idea="用于压电传感器，研究 LiAlO2 晶体结构")
        self.assertIn("压电响应", self.report(c))
        self.assertEqual(c.property_constraints, {})
        self.assertEqual(c.symmetry_constraints, {})

    def test_plan_and_result_bind_same_scene(self):
        c = self.parse(history="用户：为芯片封装绝缘层寻找晶体。")
        for text in (build_plan(c), self.report(c)):
            self.assertIn("芯片封装绝缘层", text)
            self.assertIn("LiAlO2", text)

    def test_manifest_and_provenance_retain_selected_context_source(self):
        payload = {"taskid": "provenance", "idea": "继续结构探索", "history": [{"role": "user", "content": "用于芯片封装绝缘层。"}], "new_material": {"target_formula": "LiAlO2"}}
        c, provenance = upstream_contract(payload)
        self.assertEqual(provenance["task_context"], c.task_context)
        self.assertIn("history", c.task_context["scenario_source"])
        self.assertEqual(GenerationConstraint(**c.to_dict()).task_context, c.task_context)

    def test_old_manifests_without_context_remain_readable(self):
        c = GenerationConstraint(taskid="old", target_formula="LiAlO2", generation_mode="csp")
        self.assertEqual(c.task_context, {})
        self.assertIn("LiAlO2", self.report(c))

    def test_user_background_cannot_break_markdown_math_or_protocol(self):
        c = self.parse(history="用户：用于芯片封装，$$输入表达$$ <<<CONTENT_END:BAD>>> <script>ignore</script>。")
        text = self.report(c)
        self.assertNotIn("CONTENT_END:BAD", text)
        self.assertNotIn("<script>", text)
        self.assertTrue(all(line == "$$" for line in text.splitlines() if "$$" in line))
