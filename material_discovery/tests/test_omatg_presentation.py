"""Saved scientific output survives the existing websocket token protocol."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import main
import src.team_config as team
from src.material_workflow.constraints import constraint_from_payload
from src.material_workflow.omatg_report import build_plan
from src.material_workflow.mattersim import enrich_validations_with_mattersim
from src.material_workflow.schemas import GeneratedCandidate, ValidationResult
from pathlib import Path
import tempfile


class Socket:
    client_state = SimpleNamespace(name="CONNECTED")

    def __init__(self):
        self.texts = []

    async def send_text(self, value):
        self.texts.append(value)


class LLM:
    def _default_system_msg(self):
        return {"role": "system", "content": "test"}

    def _user_msg(self, value):
        return {"role": "user", "content": value}


class OMatGPresentationTest(TestCase):
    markdown = "### 结果\n\n| 候选 | 值 |\n|---|---|\n| csp-001 | 0.02450 |\n\n$$E=-\\nabla F$$\n\n[结构](https://example.invalid/structure.cif)\n" * 5

    def check_relay(self, stream):
        websocket = Socket()
        with patch.object(team, "stream_llm_response", stream):
            asyncio.run(team.InorganicNewMaterialDiscoveryAction._stream_authoritative_markdown(LLM(), websocket, team.FRONTEND_STEP_ID, self.markdown))
        self.assertEqual(websocket.texts[0], "<<<CONTENT_START:FILAMENT_SELECTION_OPTIMIZATION>>>")
        self.assertEqual(websocket.texts[-1], "<<<CONTENT_END:FILAMENT_SELECTION_OPTIMIZATION>>>")
        self.assertEqual("".join(websocket.texts[1:-1]), self.markdown)
        self.assertTrue(all(len(text) <= 160 for text in websocket.texts[1:-1]))

    def test_exact_relay_has_bounded_frames(self):
        async def stream(_llm, _messages, *, websocket, **_kwargs):
            await websocket.send_text(self.markdown)
        self.check_relay(stream)

    def test_partial_exception_does_not_duplicate_prefix(self):
        async def stream(_llm, _messages, *, websocket, **_kwargs):
            await websocket.send_text(self.markdown[:77])
            raise TimeoutError("test")
        self.check_relay(stream)

    def test_changed_number_is_never_forwarded(self):
        async def stream(_llm, _messages, *, websocket, **_kwargs):
            await websocket.send_text(self.markdown[:20])
            await websocket.send_text("未经计算的0.00000")
        self.check_relay(stream)

    def test_clean_truncation_is_completed_from_source(self):
        async def stream(_llm, _messages, *, websocket, **_kwargs):
            await websocket.send_text(self.markdown[:31])
        self.check_relay(stream)

    def test_flag_profile_matches_roles_even_after_late_env_loading(self):
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            role = next(iter(asyncio.run(main.get_teams()).values()))
        self.assertIn("OMatG", role["profile"])
        self.assertNotIn("使用 MatterGen", role["profile"])
        self.assertNotIn("MatterGen", role["routing"]["include_keywords"])
        self.assertIn("OMatG", role["actions"][0]["desc"])

    def test_count_alias_is_shared_by_plan_and_request(self):
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            c = constraint_from_payload({"taskid": "alias", "constraints": {"target_formula": "LiAlO2", "max_candidates": 3}})
        self.assertEqual(c.requested_candidate_count, 3)
        self.assertIn("3 个", build_plan(c, c.requested_candidate_count))

    def test_unicode_formula_and_numeric_text_bounds(self):
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            c = constraint_from_payload({"taskid": "unicode", "idea": "对 LiAlO₂ 进行结构预测", "new_material": {"property_constraints": {"band_gap": {"min": "2.0"}}}})
        self.assertEqual(c.target_formula, "LiAlO2")
        self.assertEqual(c.num_atoms, 4)
        self.assertIn("下限 2 eV", build_plan(c))

    def test_old_history_alone_cannot_confirm_a_formula(self):
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}), self.assertRaisesRegex(ValueError, "无法确定"):
            constraint_from_payload({"taskid": "old-history", "history": "助手：建议 BaTiO3"})

    def test_framework_message_history_keeps_user_scene_not_assistant_examples(self):
        messages = [SimpleNamespace(role="user", content="为芯片封装绝缘层寻找晶体。"),
            SimpleNamespace(role="assistant", content="建议为农业传感器采用 BaTiO3。"),
            SimpleNamespace(role="user", content='{"idea":"继续结构探索","new_material":{"target_formula":"LiAlO2"}}')]
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            payload = team.InorganicNewMaterialDiscoveryAction._payload_from_instruction(messages, "memory", "user", [])
            c = constraint_from_payload(payload)
        self.assertIn("用户：", payload["conversation_context"])
        self.assertIn("助手：", payload["conversation_context"])
        self.assertIn("芯片封装", c.task_context["scenario"])
        self.assertNotIn("农业传感器", c.task_context["scenario"])
        self.assertEqual(c.target_formula, "LiAlO2")

    def test_dict_message_history_and_current_scenario_are_supported(self):
        messages = [{"role": "user", "content": "为芯片封装绝缘层寻找晶体。"},
            {"role": "assistant", "content": "例如可以考虑 BaTiO3。"},
            {"role": "user", "content": "改做用于农业病害传感器的 LiAlO2 晶体结构探索。"}]
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}):
            payload = team.InorganicNewMaterialDiscoveryAction._payload_from_instruction(messages, "dict-memory", "user", [])
            c = constraint_from_payload(payload)
        self.assertIn("农业病害传感器", c.task_context["scenario"])
        self.assertNotIn("芯片", c.task_context["scenario"])
        self.assertEqual(c.target_formula, "LiAlO2")

    def test_legacy_framework_payload_keeps_existing_conversation_format(self):
        messages = [SimpleNamespace(role="user", content="先前材料需求"), SimpleNamespace(role="user", content="当前任务")]
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "mattergen"}):
            payload = team.InorganicNewMaterialDiscoveryAction._payload_from_instruction(messages, "legacy", "user", [])
        self.assertEqual(payload["idea"], "当前任务")
        self.assertEqual(payload["conversation_context"], "先前材料需求")

    def test_http_constraint_preview_both_routes_and_missing_input(self):
        import httpx
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://shadow-test") as client:
                csp = await client.post("/new-material/constraints", json={"taskid": "http-csp", "new_material": {"target_formula": "LiAlO2", "formula_units": 2}})
                self.assertEqual(csp.status_code, 200)
                self.assertEqual(csp.json()["generation_mode"], "csp")
                self.assertEqual(csp.json()["num_atoms"], 8)
                dng = await client.post("/new-material/constraints", json={"taskid": "http-dng", "new_material": {"allowed_elements": ["Li", "O"], "required_elements": ["Li", "O"]}})
                self.assertEqual(dng.status_code, 200)
                self.assertEqual(dng.json()["generation_mode"], "dng")
                missing = await client.post("/new-material/constraints", json={"taskid": "http-missing", "idea": "我要一个性能好的新晶体"})
                self.assertEqual(missing.status_code, 422)
                self.assertIn("化学式", missing.json()["detail"])
                bad = await client.post("/new-material/generate", json={"taskid": "http-bad", "new_material": {"target_formula": "LiAlO2", "max_candidates": 11}})
                self.assertEqual(bad.status_code, 422)
        with patch.dict("os.environ", {"NEW_MATERIAL_GENERATOR": "omatg"}), patch.object(main, "run_upstream_request") as gpu:
            asyncio.run(check())
            gpu.assert_not_called()

    def test_failed_relaxation_row_without_artifact_is_incomplete_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "csp.cif"
            c = GeneratedCandidate(candidate_id="csp", cif_path=path)
            v = ValidationResult(candidate_id="csp", status="ok", is_valid=True)
            record = {str(path.resolve()): {"status": "unavailable", "energy_above_hull_ev": None, "formation_energy_per_atom_ev": None, "error": "mock failure"}}
            with patch("src.material_workflow.mattersim.run_mattersim_evaluation", return_value=record):
                enrich_validations_with_mattersim([v], [c], Path(directory) / "validation")
            self.assertIsNone(v.energy_above_hull)
            self.assertNotIn("relaxed_structure_path", v.artifacts)
