from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import src.team_config as team_config
from src.alloy_workflow.contracts import requirement_plan
from src.alloy_workflow.application import AlloyOptimizationApplication
from src.alloy_workflow.presentation import final_conclusion_block, hot_end_summary_block, planned_alloy_method_block
from src.alloy_workflow.presentation import _embed_rocket_visuals, rocket_stainless_summary_block
from src.alloy_workflow.microstructure_tendency import build_microstructure_tendency
from src.alloy_workflow.runtime import AlloyRuntime


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
    def test_rocket_runtime_does_not_attach_hea_phase_classification(self) -> None:
        runtime = AlloyRuntime.__new__(AlloyRuntime)
        runtime.application = SimpleNamespace(propose_space=lambda _: (
            {"taskid": "rocket-no-phase", "model_domain": "reusable_rocket_stainless", "initial_candidates": [{"candidate_id": "RSS-001"}]},
            {"taskid": "rocket-no-phase"},
        ))
        runtime._render = lambda _: {}
        runtime.save = lambda _: None

        report = runtime.propose({"taskid": "rocket-no-phase"})

        self.assertNotIn("microstructure_tendency", report)

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
        self.assertIn("预测依据与适用范围", report)
        self.assertNotIn("ExtraTrees", report)
        self.assertNotIn("training_report", report)
        self.assertIn("$$\\mathcal{F}", report)
        self.assertIn("$$J=", report)
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

    def test_alloy_chart_urls_use_the_existing_three_segment_public_path(self) -> None:
        from tempfile import TemporaryDirectory
        from src.alloy_workflow.assets import publish_png_assets

        async def accepted_upload(_bucket: str, _key: str, _data: bytes) -> dict[str, int]:
            return {"status": 200}

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"PICTURE_PUBLIC_BASE_URL": "https://assets.example/materials/modelfiles/image"}):
            image = Path(directory) / "funnel.png"
            image.write_bytes(b"png")
            with patch("src.storage_utils.oss_upload", accepted_upload):
                urls = asyncio.run(publish_png_assets("task-001", [{"name": "rocket_screening_funnel", "local_path": image}]))
        self.assertEqual(
            urls["rocket_screening_funnel"],
            "https://assets.example/materials/modelfiles/image/task-001/alloy_composition_optimization/funnel.png",
        )

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

    def test_nickel_superalloy_request_uses_visible_platform_defaults(self) -> None:
        effective, plan = requirement_plan({"taskid": "ni-guide", "idea": "为单晶镍基发动机叶片做蠕变寿命和成分筛选"})
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")
        self.assertEqual(plan["template"], "hot_end_ni_superalloy_screening")
        self.assertEqual(plan["missing_required_inputs"], [])
        self.assertEqual(effective["manufacturing_route"], "single_crystal")
        self.assertEqual(effective["test_temperature_C"], 950)
        self.assertEqual(effective["applied_stress_MPa"], 250)
        self.assertEqual(
            {item["field"] for item in plan["default_assumptions"]},
            {
                "element_bounds_wt_percent",
                "manufacturing_route",
                "heat_treatment",
                "test_temperature_C",
                "applied_stress_MPa",
                "screening_thresholds",
            },
        )
        self.assertEqual(
            effective["screening_thresholds"],
            {"uts_min_MPa": 900, "proof_strength_min_MPa": 500, "rupture_life_min_h": 250},
        )

    def test_hot_end_context_conditions_override_platform_defaults(self) -> None:
        payload = {
            "taskid": "ni-explicit-service-condition",
            "idea": "第四/第五代单晶镍基高温合金叶片，要求在1100°C/200MPa条件下蠕变断裂寿命超过100小时。",
        }
        effective, plan = requirement_plan(payload)
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")
        self.assertEqual(effective["test_temperature_C"], 1100.0)
        self.assertEqual(effective["applied_stress_MPa"], 200.0)
        self.assertEqual(effective["screening_thresholds"]["rupture_life_min_h"], 100.0)
        self.assertEqual(plan["field_provenance"]["test_temperature_C"], "upstream_context")
        self.assertEqual(plan["field_provenance"]["applied_stress_MPa"], "upstream_context")
        self.assertEqual(plan["field_provenance"]["screening_thresholds"], "upstream_context")
        preview = planned_alloy_method_block(payload)
        self.assertIn("1100.0 °C / 200.0 MPa", preview)
        self.assertIn("蠕变寿命 ≥ 100.0 h", preview)

    def test_hot_end_extrapolated_condition_keeps_reference_candidates_visible(self) -> None:
        candidate = {
            "candidate_id": "NIH-001",
            "composition_wt_percent": {"Ni": 61.0, "Cr": 8.0, "Co": 7.0, "Al": 5.5, "Ta": 6.5, "W": 8.0, "Re": 4.0},
            "source_anchor": {"alloy_name": "RENE N5"},
            "ultimate_tensile_strength_MPa": {"mean": 720.0, "screening_MAE_MPa": 111.0},
            "proof_strength_0p2_MPa": {"mean": 490.0, "screening_MAE_MPa": 86.0},
            "creep_rupture": {"predicted_hours": 135.0, "screening_error_factor": 1.81, "predicted_log10_hours": 2.13},
            "elongation_percent_auxiliary": {"elongation_percent": 8.0},
            "applicability_domain": {"level": "inside"},
            "screening_score": .8,
        }
        report = hot_end_summary_block({
            "screening_conditions": {
                "manufacturing_route": "single_crystal", "test_temperature_C": 1150, "applied_stress_MPa": 200,
                "temperature_support": {"level": "extrapolated_reference", "label": "高于短时强度训练温区，作为工况外推参考", "reference_temperature_C": 1093},
            },
            "sampling": {"generated": 120, "funnel_stages": [{"label": "满足成分、路线与工况约束", "count": 120}, {"label": "综合排序优先短名单", "count": 0}]},
            "initial_candidates": [], "reference_candidates": [candidate],
        })
        self.assertIn("工况外推参考候选", report)
        self.assertIn("当前筛选门槛通过数为 0", report)
        self.assertIn("NIH-001", report)
        self.assertIn("高于短时强度训练温区", report)

    def test_hot_end_handoff_includes_candidate_provenance_and_full_wt_percent_composition(self) -> None:
        result = {
            "model_version": "test", "initial_candidates": [{
                "candidate_id": "NIH-111",
                "source_anchor": {"alloy_name": "RENE N5"},
                "composition_wt_percent": {"Ni": 61.32431, "Cr": 7.30404, "Co": 7.52456, "Al": 6.43728, "Ta": 6.4522, "W": 4.83848},
                "ultimate_tensile_strength_MPa": {"mean": 960, "screening_MAE_MPa": 111},
                "proof_strength_0p2_MPa": {"mean": 630, "screening_MAE_MPa": 86},
                "creep_rupture": {"predicted_hours": 414.7},
            }],
            "screening_conditions": {"manufacturing_route": "single_crystal", "test_temperature_C": 950, "applied_stress_MPa": 250},
        }
        application = AlloyOptimizationApplication.__new__(AlloyOptimizationApplication)
        application._enrich_hot_end(result, {})
        handoff = result["user_conclusion"]
        self.assertIn("NIH-111（基于 RENE N5 的镍基高温合金研发候选", handoff)
        self.assertIn("元素体系：Ni-Cr-Co-Al-Ta-W", handoff)
        self.assertIn("Ni 61.32431", handoff)
        self.assertIn("Cr 7.30404", handoff)

    def test_engine_high_temperature_creep_context_routes_to_nickel_hot_end(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "engine-context-ni",
            "idea": "根据上述资料做一个高温合金的配比设计",
            "conversation_context": "火箭发动机极端高温工况，重点评估蠕变与持久寿命。",
        })
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")
        self.assertEqual(plan["template"], "hot_end_ni_superalloy_screening")

    def test_engine_high_temperature_without_nickel_name_routes_to_nickel_hot_end(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "engine-high-temperature-ni",
            "idea": "为航空发动机高温承力部件设计合金配比",
        })
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")
        self.assertEqual(plan["template"], "hot_end_ni_superalloy_screening")

    def test_explicit_hea_exploration_remains_hea_when_high_temperature_is_mentioned(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "hea-exploration",
            "idea": "探索高温多主元 HEA 的强度、硬度与相稳定性，设计 at.% 配比",
        })
        self.assertEqual(effective["model_domain"], "hea_mpea")
        self.assertEqual(plan["template"], "aerospace_high_temperature_hea_exploration")

    def test_unscoped_high_temperature_alloy_defaults_to_nickel_hot_end(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "high-temperature-confirmation",
            "idea": "做一个高温合金的配比设计",
        })
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")
        self.assertEqual(plan["template"], "hot_end_ni_superalloy_screening")

    def test_team_payload_keeps_prior_user_context_for_routing(self) -> None:
        payload = team_config._payload_from_instruction([
            {"role": "user", "content": "火箭发动机高温蠕变工况的合金设计需求"},
            {"role": "assistant", "content": "请确认材料体系。"},
            {"role": "user", "content": "根据上述资料做一个高温合金的配比设计"},
        ], "history-ni", "tester", [])
        effective, _plan = requirement_plan(payload)
        self.assertEqual(effective["model_domain"], "ni_superalloy_hot_end")

    def test_hot_end_websocket_runs_with_platform_defaults(self) -> None:
        websocket = FakeWebSocket({"taskid": "ni-wait", "idea": "为单晶镍基发动机叶片做蠕变寿命和成分筛选"})
        with (
            patch.object(main, "_proposal", return_value=result("ni-wait")),
            patch.object(main, "prepare_public_assets", fake_assets),
            patch.object(main, "emit_result_content", fake_content),
            patch.object(main.asyncio, "to_thread", run_inline),
        ):
            asyncio.run(main.start(websocket))
        texts = [value for kind, value in websocket.events if kind == "text"]
        events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts[-1], "[end]")
        self.assertEqual(events[-1]["data"]["status"], "completed")
        self.assertIn("950 °C / 250 MPa", "".join(str(item) for item in texts))

    def test_reusable_rocket_stainless_request_uses_its_own_wt_percent_template(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "rocket-guide", "idea": "为可回收火箭 LOX 贮箱设计低温奥氏体不锈钢配方",
        })
        self.assertEqual(effective["model_domain"], "reusable_rocket_stainless")
        self.assertEqual(plan["template"], "reusable_rocket_stainless_screening")
        self.assertEqual(effective["test_temperature_K"], 293)
        self.assertIn("Cr", effective["element_bounds_wt_percent"])
        self.assertIn("solution_treatment_temperature_K", effective["processing"])

    def test_natural_reusable_rocket_stainless_phrase_routes_to_rocket_service(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "rocket-natural-phrase",
            "idea": "我想针对航空航天火箭的表面可回收壳体不锈钢做一个配比设计",
        })
        self.assertEqual(effective["model_domain"], "reusable_rocket_stainless")
        self.assertEqual(plan["template"], "reusable_rocket_stainless_screening")

    def test_rocket_stainless_page_uses_visible_conditions_and_customer_domain_label(self) -> None:
        report = rocket_stainless_summary_block({
            "mode": "short_time_tensile_screening",
            "screening_conditions": {"test_temperature_K": 293, "processing": {"solution_treatment_temperature_K": 1323, "solution_treatment_time_s": 3600, "quench": "water"}, "low_temperature_verification_K": [90, 111]},
            "requirement_interpretation": {"default_assumptions": [{"field": "test_temperature_K"}]},
            "initial_candidates": [{"candidate_id": "RSS-001", "composition_wt_percent": {"Cr": 18.0, "Ni": 9.0, "N": .011, "Fe": 72.989}, "short_time_tensile": {"yield_0p2_MPa": {"mean": 250, "screening_MAE": 16.2}, "uts_MPa": {"mean": 600, "screening_MAE": 16.3}, "elongation_pct": {"mean": 60, "screening_MAE": 3.29}}, "applicability_domain": {"level": "inside", "nearest_training_composition_distance": 2.1}}],
        })
        self.assertIn("### 5. 筛选结果与候选卡", report)
        self.assertNotIn("\n## ", report)
        self.assertIn("{{VISUAL:rocket_screening_funnel}}", report)
        self.assertIn("训练成分邻域内", report)
        self.assertIn("N0.011", report)

    def test_rocket_visual_tokens_become_public_markdown_images(self) -> None:
        report = "{{VISUAL:rocket_screening_funnel}}\n{{VISUAL:rocket_strength_ductility_tradeoff}}\n{{VISUAL:rocket_composition_comparison}}"
        visual_assets = [
            {"name": name, "title": title, "description": "图表说明", "url": f"https://www.science42.tech/images/{name}.png"}
            for name, title in (
                ("rocket_screening_funnel", "候选筛选路径"),
                ("rocket_strength_ductility_tradeoff", "强度—延性取舍"),
                ("rocket_composition_comparison", "优先候选成分"),
            )
        ]
        rendered = _embed_rocket_visuals(report, visual_assets)
        self.assertNotIn("{{VISUAL:", rendered)
        self.assertEqual(rendered.count("https://www.science42.tech/images/"), 3)
        self.assertEqual(rendered.count("!["), 3)

    def test_explicit_multielement_alloy_summary_is_valid_context(self) -> None:
        effective, plan = requirement_plan({
            "taskid": "ni-co-cr-al-ti",
            "idea": "基于Ni-Co-Cr-Al-Ti体系在900°C下的筛选结果，输出综合评分最高的合金配比方案及后续验证建议。",
        })
        self.assertEqual(effective["allowed_elements"], ["Ni", "Co", "Cr", "Al", "Ti"])
        self.assertEqual(plan["template"], "aerospace_high_temperature_hea_exploration")

    def test_composite_material_is_rejected_even_with_metal_element_bounds(self) -> None:
        payload = {
            "taskid": "metal-fiber-composite",
            "idea": "优化 Co-Cr-Fe-Mn-Ni 与碳纤维复合材料的配比",
            "alloy_optimization": {
                "allowed_elements": ["Co", "Cr", "Fe", "Mn", "Ni"],
                "element_bounds_at_pct": {"Co": [10, 30]},
            },
        }
        with self.assertRaisesRegex(ValueError, "单一金属合金"):
            requirement_plan(payload)

    def test_role_and_discovery_use_shared_descriptions(self) -> None:
        from src.alloy_workflow.identity import ACTION_DESCRIPTION, ROLE_PROFILE
        role = next(iter(main.roles().values()))
        self.assertEqual(team_config.Coding.model_fields["desc"].default, ACTION_DESCRIPTION)
        self.assertEqual(team_config.AlloyCompositionOptimizationRole.model_fields["profile"].default, ROLE_PROFILE)
        self.assertEqual(role["profile"], ROLE_PROFILE)
        self.assertEqual(role["actions"][0]["desc"], ACTION_DESCRIPTION)
