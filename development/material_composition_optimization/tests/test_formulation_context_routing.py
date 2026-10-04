"""Material references in parent-service formulation tasks use selected context."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock

import pytest

from src.alloy_workflow.contracts import MaterialContextError, requirement_plan, upstream_requirement
from src.alloy_workflow.fallback import generic_fallback_plan, generic_fallback_result


REQUEST = "针对上述材料做一个配比优化"
GLASS = "已选定芯片封装玻璃基板，采用低硼无碱铝硼硅酸盐。"


@pytest.mark.parametrize("name,domain", [
    ("CoCrFeMnNi 高熵合金", "hea_mpea"),
    ("单晶镍基高温合金", "ni_superalloy_hot_end"),
    ("GRCop-84 铜基热端合金", "copper_hot_end_local_composition_v1"),
    ("火箭奥氏体不锈钢 304L", "reusable_rocket_stainless"),
    ("7075 航空铝合金", "aerospace_aluminum_composition_v1"),
    ("芯片玻璃基板的低硼无碱铝硼硅酸盐", "chip_glass_thermomechanical_family_v1"),
    ("短碳纤维增强 PETG 复合耗材", "short_cf_thermomechanical_rve_v1"),
    ("Cs/FA/MA 卤化物钙钛矿", "perovskite_transport_stability_v2"),
])
def test_reference_resolves_each_existing_expert(name, domain):
    _, plan = requirement_plan({"idea": REQUEST, "material_conclusion": {"summary": name}})
    assert plan["effective_model_input"]["model_domain"] == domain
    assert plan["upstream_context_keys"] == ["idea", "material_conclusion"]
    assert REQUEST in plan["raw_requirement"]


@pytest.mark.parametrize("request_text", [REQUEST, "针对上一步选择的材料做配方优化", "调整上述体系的组分比例", "做一个配方优化", "继续"])
def test_formulation_aliases_inherit_the_selected_material(request_text):
    text, keys = upstream_requirement({"idea": request_text, "material_conclusion": {"summary": GLASS}})
    assert "芯片封装玻璃" in text
    assert keys == ["idea", "material_conclusion"]


@pytest.mark.parametrize("key", ["material_conclusion", "upstream_result", "upstream_context", "previous_results", "project_idea"])
def test_named_upstream_context_fields_are_accepted(key):
    text, keys = upstream_requirement({"idea": REQUEST, key: {"selected_material": {"material_name": GLASS}}})
    assert "芯片封装玻璃" in text
    assert keys == ["idea", key]


def test_latest_prior_user_wins_and_assistant_capability_list_is_not_material_evidence():
    payload = {
        "idea": REQUEST,
        "messages": [
            {"role": "user", "content": "之前用 7075 航空铝合金"},
            {"role": "assistant", "content": "可预测镍基、铜基、不锈钢、玻璃、复合材料"},
            {"role": "user", "content": GLASS},
            {"role": "user", "content": REQUEST},
        ],
    }
    text, _ = upstream_requirement(payload)
    assert "芯片封装玻璃" in text
    assert "7075" not in text
    assert "镍基" not in text


def test_latest_plain_user_context_wins_in_the_alpha_adapter_shape():
    text, _ = upstream_requirement({"idea": REQUEST, "conversation_context": ["7075 航空铝合金", GLASS]})
    assert "芯片封装玻璃" in text
    assert "7075" not in text


def test_current_explicit_material_overrides_old_selected_material():
    current = "这次不做上述材料，改用 7075 航空铝合金进行配方优化。"
    text, keys = upstream_requirement({"idea": current, "material_conclusion": {"summary": GLASS}})
    assert text == current
    assert keys == ["idea"]
    assert requirement_plan({"idea": current, "material_conclusion": {"summary": GLASS}})[0]["model_domain"] == "aerospace_aluminum_composition_v1"


def test_structured_upstream_selection_inherits_only_existing_model_inputs():
    payload = {
        "idea": REQUEST,
        "upstream_result": {"selected_material": {
            "material_name": "火箭奥氏体不锈钢",
            "test_temperature_K": 600,
            "processing": {"quench": "water"},
            "composition_basis": "wt.%",
            "administrative_note": "not a model input",
        }},
        "constraints": {"test_temperature_K": 700},
    }
    effective, plan = requirement_plan(payload)
    assert effective["model_domain"] == "reusable_rocket_stainless"
    assert effective["test_temperature_K"] == 700
    assert plan["field_provenance"]["test_temperature_K"] == "user"
    assert effective["processing"] == {"quench": "water"}
    assert plan["field_provenance"]["processing"] == "upstream_context"
    assert "administrative_note" not in effective


def test_context_selected_record_wins_over_multiple_unselected_candidates():
    payload = {"idea": REQUEST, "upstream_result": {
        "selected_material": {"material_name": GLASS},
        "candidates": [{"material_name": "7075 航空铝合金"}, {"material_name": "镍基高温合金"}],
    }}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_selected_candidate_flag_disambiguates_a_list():
    payload = {"idea": REQUEST, "upstream_result": [
        {"material_name": "7075 航空铝合金"},
        {"material_name": GLASS, "selected": True},
    ]}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_explicit_structured_selection_in_tool_envelope_can_be_used():
    payload = {"idea": REQUEST, "upstream_result": {
        "role": "tool", "selected_material": {"material_name": GLASS},
        "content": "其他服务支持镍基与复合材料",
    }}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_json_string_upstream_selection_is_accepted():
    payload = {"idea": REQUEST, "upstream_result": json.dumps({"selected_material": {"material_name": GLASS}})}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_parent_text_envelope_can_supply_the_referenced_material():
    payload = {"idea": (
        f"【用户原始目标】\n{GLASS}\n"
        "【历史执行上下文】\n助手介绍过航空铝和镍基模型。\n"
        f"【最新用户补充，高优先级】\n{REQUEST}\n"
        "【本轮必须执行的任务】\n优化配方。"
    )}
    effective, plan = requirement_plan(payload)
    assert effective["model_domain"] == "chip_glass_thermomechanical_family_v1"
    assert "航空铝" not in plan["raw_requirement"]


def test_embedded_user_material_is_not_overridden_by_an_old_project_goal():
    payload = {"idea": f"【用户原始目标】\n{GLASS}\n【最新用户补充，高优先级】\n{REQUEST}", "project_idea": "7075 航空铝合金"}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_selected_upstream_material_wins_over_project_goal_without_current_idea():
    payload = {"material_conclusion": {"material_name": GLASS}, "project_idea": "7075 航空铝合金"}
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_only_upstream_structured_identity_without_current_idea_is_accepted():
    assert requirement_plan({"material_conclusion": {"material_name": GLASS}})[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_explicit_existing_model_domain_does_not_require_a_repeated_material_name():
    assert requirement_plan({"idea": REQUEST, "constraints": {"model_domain": "chip_glass_thermomechanical_family_v1"}})[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


@pytest.mark.parametrize("payload", [
    {"idea": REQUEST},
    {"idea": "做一个配方优化"},
    {"idea": REQUEST, "upstream_result": [{"material_name": GLASS}, {"material_name": "7075 航空铝合金"}]},
    {"idea": REQUEST, "material_conclusion": {"summary": "有芯片玻璃基板和航空铝合金两个候选，尚未选定。"}},
    {"idea": REQUEST, "messages": [{"role": "assistant", "content": "本服务可计算芯片玻璃基板，也可计算航空铝合金。"}]},
])
def test_missing_or_ambiguous_identity_is_confirmation_not_an_untrained_alloy(payload):
    with pytest.raises(MaterialContextError):
        upstream_requirement(payload)
    effective, plan = generic_fallback_plan(payload, "routing")
    assert effective["model_domain"] == "generic_composition_design_fallback_v1"
    assert not plan.get("requires_domain_confirmation")
    assert effective["material_family"]
    result, _ = generic_fallback_result(payload, "routing", "alloy-composition-optimization")
    assert result["status"] == "completed_with_fallback"
    assert result["initial_candidates"]
    assert 'predicted_properties' not in result['initial_candidates'][0]


def test_resolved_untrained_family_still_receives_its_specific_trial_design():
    payload = {"idea": REQUEST, "material_conclusion": {"selected_material": {"material_name": "环氧树脂与连续玻璃纤维复合材料"}}}
    effective, plan = generic_fallback_plan(payload, "未接入模型")
    assert effective["model_domain"] == "generic_composition_design_fallback_v1"
    assert effective["material_family"] == "复合材料/多相体系"
    assert not plan.get("requires_domain_confirmation")


@pytest.mark.parametrize("payload", [
    {"idea": REQUEST, "material_conclusion": {"role": "assistant", "content": GLASS}},
    {"idea": REQUEST, "messages": [{"role": "assistant", "content": GLASS}, {"role": "user", "content": REQUEST}]},
    {"idea": REQUEST, "upstream_result": {"role": "tool", "content": json.dumps({"selected_material": {"material_name": GLASS}})}},
])
def test_explicit_assistant_or_tool_selection_is_available_when_user_references_it(payload):
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_system_message_is_not_a_material_selection():
    with pytest.raises(MaterialContextError):
        upstream_requirement({"idea": REQUEST, "upstream_result": {"role": "system", "selected_material": GLASS}})


@pytest.mark.parametrize("name", [
    "环氧树脂连续碳纤维复合材料，需要优化模量",
    "短碳纤维增强环氧热固性复合材料，优化模量",
    "普通硼硅酸盐玻璃配方，非芯片封装",
    "Ti-Nb-Mo 钛合金，按 at.% 优化强度",
])
def test_untrained_families_are_not_forced_into_an_incompatible_specialist(name):
    import main
    effective, _ = main._safe_requirement_plan({"idea": REQUEST, "material_conclusion": {"material_name": name}})
    assert effective["model_domain"] == "generic_composition_design_fallback_v1"


def test_nickel_based_hea_is_one_material_not_ambiguous_candidates():
    payload = {"idea": REQUEST, "material_conclusion": {"material_name": "镍基高熵合金"}}
    assert requirement_plan(payload)[0]["model_domain"] == "hea_mpea"


def test_role_discovery_describes_capability_without_input_resolution_details():
    import main
    role = next(iter(main.roles().values()))
    assert "材料配方设计" in role["profile"]
    assert "性能" in role["actions"][0]["desc"]
    public_role = json.dumps(role, ensure_ascii=False)
    for detail in ("上游已选材料", "上游材料选择", "上下文", "最新一句", "重复材料名称", "上述材料"):
        assert detail not in public_role
    assert "配方优化" in role["routing"]["include_keywords"]
    assert "组分优化" in role["routing"]["include_keywords"]
    assert not role["routing"]["exclude_keywords"]
    assert role["role_id"] == "alloy_composition_optimization_v1"
    assert role["addresses"][0] == "src.team_config.AlloyCompositionOptimizationRole"


class CaptureWebSocket:
    client = None

    def __init__(self, payload):
        self.payload = payload
        self.events = []

    async def accept(self):
        pass

    async def receive(self):
        return {"type": "websocket.receive", "text": json.dumps(self.payload)}

    async def send_text(self, value):
        self.events.append(("text", value))

    async def send_json(self, value):
        self.events.append(("json", value))

    async def close(self):
        pass


async def stream_locally(websocket, markdown, *, step_id):
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    await websocket.send_text(markdown)
    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")


def test_direct_websocket_reuses_existing_waiting_protocol_and_does_not_infer():
    import main
    websocket = CaptureWebSocket({"taskid": "context-missing", "idea": REQUEST})
    result,_=generic_fallback_result(websocket.payload,'routing','test')
    async def emit(ws,value,**kwargs):
        from src.alloy_workflow.fallback_design import render_fallback
        await stream_locally(ws,render_fallback(value),step_id=kwargs['step_id'])
    with patch.object(main, "_proposal",return_value=result) as infer, patch.object(main, "stream_authoritative_markdown", stream_locally), \
         patch.object(main,'prepare_public_assets',AsyncMock(return_value=({},[],[],[]))),patch.object(main,'emit_result_content',emit):
        asyncio.run(main.start(websocket))
    infer.assert_called_once()
    texts = [event for kind, event in websocket.events if kind == "text"]
    results = [event for kind, event in websocket.events if kind == "json" and event["type"] == "result"]
    assert texts[0] == "[start]" and texts[-1] == "[end]"
    assert results[0]["data"]["status"] == "completed_with_fallback"
    assert any('配方' in part for part in texts)
    assert all(event["data"]["stepId"] == "FILAMENT_SELECTION_OPTIMIZATION" for kind, event in websocket.events if kind == "json" and event["type"] == "progress")


def test_alpha_role_reuses_the_same_context_and_waiting_branch():
    import src.team_config as team
    websocket = CaptureWebSocket({})
    result,_=generic_fallback_result({'idea':REQUEST},'routing','test')
    with patch.object(team, "execute_alloy_optimization",AsyncMock(return_value=result)) as infer, patch.object(team, "stream_authoritative_markdown", stream_locally):
        message = asyncio.run(team.Coding.run(SimpleNamespace(), {"idea": REQUEST}, websocket, "tester", "context-alpha", []))
    infer.assert_called_once()
    assert "配方" in message
    assert any(kind == "json" and event["type"] == "result" and event["data"]["status"] == "completed_with_fallback" for kind, event in websocket.events)


def test_alpha_message_adapter_preserves_prior_user_material_for_a_reference():
    import src.team_config as team
    payload = team._payload_from_instruction([
        {"role": "user", "content": GLASS},
        {"role": "assistant", "content": "已提供多个模型的说明"},
        {"role": "user", "content": REQUEST},
    ], "alpha-reference", "tester", [])
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_alpha_message_adapter_preserves_explicit_upstream_selection():
    import src.team_config as team
    payload = team._payload_from_instruction([
        {"role": "user", "content": "需要一种封装材料"},
        {"role": "assistant", "content": GLASS},
        {"role": "user", "content": REQUEST},
    ], "alpha-selected-reference", "tester", [])
    assert requirement_plan(payload)[0]["model_domain"] == "chip_glass_thermomechanical_family_v1"


def test_runtime_confirmation_does_not_call_runner_or_create_fake_charts(tmp_path):
    from src.alloy_workflow.application import AlloyOptimizationApplication
    from src.alloy_workflow.runtime import AlloyRuntime
    runner = SimpleNamespace(run=Mock(), submit_tensile_shadow=Mock())
    runtime = AlloyRuntime.__new__(AlloyRuntime)
    runtime.application = AlloyOptimizationApplication(runner, "alloy-composition-optimization")
    runtime.results_root = tmp_path
    runtime._render = Mock(return_value={})
    result = runtime.propose({"taskid": "waiting-no-prediction", "idea": REQUEST})
    assert result["status"] == "completed_with_fallback"
    runner.run.assert_not_called()
    runtime._render.assert_called_once()
    assert result["presentation"]["assets"] == []


def test_rest_evaluate_preserves_waiting_status_in_outer_manifest():
    import main
    with patch.object(main.RUNTIME, "save"):
        result = main.evaluate({"taskid": "waiting-evaluate", "idea": REQUEST})
    assert result["status"] == result["result"]["status"] == "completed_with_fallback"
