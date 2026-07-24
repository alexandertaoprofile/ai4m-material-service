from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import src.team_config as team_config
from src.alloy_workflow.contracts import requirement_plan


class FakeWebSocket:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.events: list[tuple[str, object]] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict:
        import json
        return {"type": "websocket.receive", "text": json.dumps(self.payload, ensure_ascii=False)}

    async def send_text(self, value: str) -> None:
        self.events.append(("text", value))

    async def send_json(self, value: dict) -> None:
        self.events.append(("json", value))

    async def close(self) -> None:
        self.closed = True

    @property
    def client(self):
        return None


def result(taskid: str) -> dict:
    return {
        "taskid": taskid,
        "status": "completed",
        "presentation": {"assets": [{"name": "screening_funnel", "url": f"/alloy/tasks/{taskid}/assets/screening_funnel.png", "type": "MaterialsPNG"}]},
        "user_conclusion": "候选初筛完成。",
    }


async def fake_assets(*_args, **_kwargs):
    return ({"screening_funnel": "https://assets.example/screening_funnel.png"}, {"screening_funnel": "测试图表"}, {}, [])


async def fake_content(websocket, _result, *, step_id, **_kwargs) -> None:
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    await websocket.send_text("测试结果")
    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")


async def failing_publish(*_args, **_kwargs):
    raise RuntimeError("MinIO unavailable")


async def run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class AlloyServiceContractTest(unittest.TestCase):
    def test_routes_and_roles_keep_current_compatibility_contract(self) -> None:
        paths = {route.path for route in main.app.routes}
        self.assertTrue({"/start", "/alloy/start", "/roles", "/alloy/propose-space", "/alloy/evaluate", "/alloy/evaluate-batch"}.issubset(paths))
        role = next(iter(main.roles().values()))
        self.assertEqual(role["role_id"], "alloy_composition_optimization_v1")
        self.assertEqual(role["__module_class_name"], "src.team_config.AlloyCompositionOptimizationRole")
        self.assertEqual(role["addresses"][0], "src.team_config.AlloyCompositionOptimizationRole")
        self.assertEqual(role["actions"][0]["name"], "Coding")
        self.assertFalse(hasattr(team_config, "XIMUAlpha_MNS"))

    def test_direct_websocket_sequence_and_asset_failure_are_frozen(self) -> None:
        payload = {"taskid": "contract-alloy", "idea": "设计高熵合金配比"}
        websocket = FakeWebSocket(payload)
        with (
            patch.object(main, "_proposal", return_value=result("contract-alloy")),
            patch.object(main, "prepare_public_assets", fake_assets),
            patch.object(main, "emit_result_content", fake_content),
            patch.object(main.asyncio, "to_thread", run_inline),
        ):
            asyncio.run(main.start(websocket))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts[-1], "[end]")
        self.assertIn(f"<<<CONTENT_START:{main.FRONTEND_STEP_ID}>>>", texts)
        self.assertIn(f"<<<CONTENT_END:{main.FRONTEND_STEP_ID}>>>", texts)
        self.assertEqual([event["type"] for event in json_events if event.get("type") in {"progress", "result"}], ["progress", "progress", "result"])
        asset = next(event for event in json_events if event.get("type") == "MaterialsPNG")
        self.assertEqual(asset["stepId"], main.FRONTEND_STEP_ID)
        self.assertEqual(asset["type"], "MaterialsPNG")
        self.assertTrue(websocket.closed)

    def test_team_action_keeps_existing_result_and_asset_events(self) -> None:
        websocket = FakeWebSocket()
        action = team_config.Coding()
        with (
            patch.object(team_config, "_proposal", return_value=result("role-alloy")),
            patch.object(team_config, "prepare_public_assets", fake_assets),
            patch.object(team_config, "emit_result_content", fake_content),
            patch.object(team_config.asyncio, "to_thread", run_inline),
        ):
            asyncio.run(action.run({"idea": "HEA 成分优化"}, websocket, "tester", "role-alloy", []))
        json_events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(json_events[0]["type"], "progress")
        self.assertEqual(json_events[-1]["type"], "result")
        self.assertTrue(any(event.get("type") == "MaterialsPNG" for event in json_events))

    def test_role_entry_exposes_the_alloy_use_case_sequence(self) -> None:
        self.assertTrue(callable(team_config.execute_alloy_optimization))

    def test_asset_publish_failure_keeps_the_failed_progress_event(self) -> None:
        websocket = FakeWebSocket()
        from src.alloy_workflow import protocol
        with patch.object(protocol, "publish_png_assets", failing_publish):
            urls, _docs, _titles, visuals = asyncio.run(protocol.prepare_public_assets(websocket, "asset-failure", result("asset-failure"), main.RESULTS))
        self.assertEqual(urls["screening_funnel"], "/alloy/tasks/asset-failure/assets/screening_funnel.png")
        self.assertEqual(len(visuals), 1)
        failed = websocket.events[0][1]
        self.assertEqual(failed["type"], "progress")
        self.assertEqual(failed["data"]["status"], "failed")

    def test_alloy_presentation_does_not_import_inorganic_workflow(self) -> None:
        source = (Path(__file__).parents[1] / "src/alloy_workflow/presentation.py").read_text(encoding="utf-8")
        self.assertNotIn("src.material_workflow.llm_streaming", source)
        self.assertIn("src.alloy_workflow.llm_streaming", source)

    def test_team_orchestrator_does_not_depend_on_transport_entry(self) -> None:
        source = (Path(__file__).parents[1] / "src/team_config.py").read_text(encoding="utf-8")
        self.assertNotIn("from main import", source)
        self.assertIn("from src.alloy_workflow.runtime import RUNTIME", source)

    def test_parent_project_idea_is_valid_alloy_context(self) -> None:
        effective, plan = requirement_plan({"taskid": "parent-context", "idea": "继续", "project_idea": "生成一款高温高熵合金最优配比"})
        self.assertEqual(effective["model_domain"], "hea_mpea")
        self.assertEqual(plan["template"], "aerospace_high_temperature_hea_exploration")

    def test_explicit_multielement_alloy_summary_is_valid_context(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "ni-co-cr-al-ti",
            "idea": "基于Ni-Co-Cr-Al-Ti体系在900°C下的筛选结果，输出综合评分最高的合金配比方案及后续验证建议。",
        })
        self.assertEqual(effective["allowed_elements"], ["Ni", "Co", "Cr", "Al", "Ti"])
        self.assertEqual(plan["template"], "aerospace_high_temperature_hea_exploration")

    def test_role_and_discovery_use_shared_descriptions(self) -> None:
        from src.alloy_workflow.identity import ACTION_DESCRIPTION, ROLE_PROFILE
        role = next(iter(main.roles().values()))
        self.assertEqual(team_config.Coding.model_fields["desc"].default, ACTION_DESCRIPTION)
        self.assertEqual(team_config.AlloyCompositionOptimizationRole.model_fields["profile"].default, ROLE_PROFILE)
        self.assertEqual(role["profile"], ROLE_PROFILE)
        self.assertEqual(role["actions"][0]["desc"], ACTION_DESCRIPTION)
