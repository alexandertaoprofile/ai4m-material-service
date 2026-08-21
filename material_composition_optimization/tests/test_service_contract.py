from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import src.team_config as team_config
from src.alloy_workflow.contracts import requirement_plan
from src.alloy_workflow.presentation import final_conclusion_block
from src.alloy_workflow.microstructure_tendency import build_microstructure_tendency


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
    return ({"screening_funnel": "https://assets.example/screening_funnel.png"}, {"screening_funnel": "测试图表"}, {}, [{"url": "https://assets.example/screening_funnel.png", "title": "测试图表", "description": "测试说明"}])


async def fake_content(websocket, _result, *, step_id, visual_assets=None, **_kwargs) -> None:
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    await websocket.send_text("测试结果")
    for asset in visual_assets or []:
        await websocket.send_text(f"![{asset['title']}]({asset['url']})")
    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")


async def failing_publish(*_args, **_kwargs):
    raise RuntimeError("MinIO unavailable")


async def run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class AlloyServiceContractTest(unittest.TestCase):
    def test_final_report_ends_with_complete_optimal_candidate_card(self) -> None:
        report = final_conclusion_block({
            "model_evidence": {"model_version": "hea_mpea_mlp_v1", "data_type": "实验 HEA/MPEA 数据"},
            "search_space": {"processing_method": "CAST", "test_temperature_C": 900},
            "sampling": {"generated": 100, "feasible": 1},
            "initial_candidates": [{
                "composition_at_pct": {"Ni": 30.0, "Co": 25.0, "Cr": 20.0, "Al": 10.0, "Ti": 15.0},
                "yield_strength_MPa": {"mean": 520.0, "std": 28.0},
                "hardness_HV": {"mean": 700.0, "std": 20.0},
                "phase_probabilities": {"SS": 0.70, "IM": 0.01, "SS+IM": 0.29},
                "phase_risk": "low",
                "selection_score": 0.81,
                "applicability_domain": {"level": "boundary", "nearest_training_composition_distance": 0.21},
            }],
        })
        self.assertIn("预训练 MLP", report)
        self.assertIn("### 最优候选材料卡", report)
        self.assertIn("Ni | 30.00 at.%", report)
        self.assertIn("工程估算与验证重点", report)
        self.assertIn("D级工程估算", report)
        self.assertIn("抗拉强度", report)
        self.assertLess(report.index("### 本轮结论"), report.index("### 最优候选材料卡"))

    def test_microstructure_tendency_maps_phase_risk_without_claiming_micrograph(self) -> None:
        stable = build_microstructure_tendency({
            "phase_probabilities": {"SS": .90, "IM": .03, "SS+IM": .07},
            "applicability_domain": {"level": "inside"},
        })
        mixed = build_microstructure_tendency({
            "phase_probabilities": {"SS": .72, "IM": .04, "SS+IM": .24},
            "applicability_domain": {"level": "boundary"},
        })
        risk = build_microstructure_tendency({
            "phase_probabilities": {"SS": .58, "IM": .18, "SS+IM": .24},
            "applicability_domain": {"level": "outside"},
        })
        self.assertEqual((stable["level"], stable["visual_marker_count"]), ("A", 2))
        self.assertEqual((mixed["level"], mixed["confidence"]), ("B", "探索性"))
        self.assertEqual((risk["level"], risk["visual_marker_count"]), ("C", 14))
        self.assertIn("规则映射", stable["source"])

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
        self.assertFalse(any(event.get("type") == "MaterialsPNG" for event in json_events))
        self.assertIn("![测试图表](https://assets.example/screening_funnel.png)", texts)
        self.assertTrue(websocket.closed)

    def test_team_action_keeps_markdown_images_and_result_events(self) -> None:
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
        texts = [value for kind, value in websocket.events if kind == "text"]
        self.assertEqual(json_events[0]["type"], "progress")
        self.assertEqual(json_events[-1]["type"], "result")
        self.assertFalse(any(event.get("type") == "MaterialsPNG" for event in json_events))
        self.assertIn("![测试图表](https://assets.example/screening_funnel.png)", texts)

    def test_role_entry_exposes_the_alloy_use_case_sequence(self) -> None:
        self.assertTrue(callable(team_config.execute_alloy_optimization))

    def test_asset_publish_failure_uses_plain_text_notice(self) -> None:
        websocket = FakeWebSocket()
        from src.alloy_workflow import protocol
        with patch.object(protocol, "publish_png_assets", failing_publish):
            urls, _docs, _titles, visuals = asyncio.run(protocol.prepare_public_assets(websocket, "asset-failure", result("asset-failure"), main.RESULTS))
        self.assertEqual(urls["screening_funnel"], "/alloy/tasks/asset-failure/assets/screening_funnel.png")
        self.assertEqual(len(visuals), 1)
        self.assertEqual(websocket.events, [("text", "\n图片发布失败，已改用本服务任务资产链接继续展示。\n")])

    def test_unhandled_websocket_failure_uses_plain_text_notice(self) -> None:
        websocket = FakeWebSocket({"taskid": "failed-alloy", "idea": "设计高熵合金配比"})
        with patch.object(main, "_requirement_plan", side_effect=RuntimeError("计算执行器不可用")):
            asyncio.run(main.start(websocket))

        self.assertEqual(websocket.events, [("text", "\n处理失败：计算执行器不可用\n")])
        self.assertTrue(websocket.closed)

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
