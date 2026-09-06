from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import src.team_config as team_config
from src.material_workflow import llm_constraint_inference
from src.material_workflow import presentation


class _FakeWebSocket:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def send_text(self, value: str) -> None:
        self.events.append(("text", value))

    async def send_json(self, value: dict) -> None:
        self.events.append(("json", value))


class _FakeResult:
    status = "ok"
    artifacts = {"presentation": {"assets": []}}


class _FakeLLM:
    def __init__(self, **_: object) -> None:
        pass

    @staticmethod
    def _default_system_msg() -> dict:
        return {"role": "system", "content": "test"}

    @staticmethod
    def _user_msg(content: str) -> dict:
        return {"role": "user", "content": content}


async def _fake_stream_llm(_llm, _messages, *, websocket, logger_obj) -> None:
    await websocket.send_text("测试正文\n")


async def _wait_for_cancellation(*_args, **_kwargs) -> None:
    await asyncio.Event().wait()


async def _run_inline(function, *args, **kwargs):
    """Keep the protocol test independent of a worker thread/GPU runtime."""
    return function(*args, **kwargs)


class _FakeMemory:
    def add(self, _message) -> None:
        pass


class _FakeTodo:
    desc = "无 GPU 契约动作"
    PROMPT_TEMPLATE = None

    async def run(self, _history, _websocket, _user_name, _taskid, _file_metadata) -> str:
        return "[[WORKFLOW_STATUS:ok]]\n测试完成"


class _FakeRole:
    is_human = False
    _setting = "测试角色"
    profile = "测试角色"

    def __init__(self) -> None:
        self.rc = type("RC", (), {"todo": _FakeTodo(), "history": [], "memory": _FakeMemory()})()

    @staticmethod
    def _observe() -> bool:
        return True

    @staticmethod
    def _no_think() -> None:
        pass

    @staticmethod
    def _set_state(*, state: int) -> None:
        assert state == -1

    def set_todo(self, value) -> None:
        self.rc.todo = value

    @staticmethod
    def publish_message(_message) -> None:
        pass


class _FakeTeam:
    def __init__(self, todo=None) -> None:
        self.role = _FakeRole()
        if todo is not None:
            self.role.rc.todo = todo
        self.env = type("Environment", (), {"roles": {"test": self.role}})()
        self.idea = ""

    def run_project(self, idea: str) -> None:
        self.idea = idea


class InorganicNewMaterialServiceContractTest(unittest.TestCase):
    def test_retrieval_progress_event_matches_frontend_bar_schema(self) -> None:
        event = presentation.build_retrieval_progress_payload(
            "progress-task",
            3,
            stage="generation",
            percent=42,
            status="in_progress",
            text="正在生成候选晶体；已完成真实扩散步数 40/100。",
        )
        self.assertEqual(event["type"], "retrieval_progress")
        self.assertEqual(event["request_id"], "progress-task")
        self.assertEqual(event["seq"], 3)
        self.assertEqual(event["content"], {
            "stage": "generation",
            "percent": 42,
            "status": "in_progress",
            "text": "正在生成候选晶体；已完成真实扩散步数 40/100。",
        })

    def test_asset_delivery_uses_markdown_for_images_and_json_for_glb(self) -> None:
        class _Storage:
            async def aobject_exists(self, *_args) -> bool:
                return True

        async def upload(*_args) -> dict:
            return {"status": 200}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            png, gif, glb = root / "card.png", root / "rotation.gif", root / "structure.glb"
            for path in (png, gif, glb):
                path.write_bytes(b"test")
            result = SimpleNamespace(
                taskid="asset-contract",
                artifacts={"presentation": {"assets": [
                    {"path": str(png), "type": "MaterialsPNG", "name": "结构卡"},
                    {"path": str(gif), "type": "MaterialsPNG", "name": "旋转图"},
                    {"path": str(glb), "type": "MaterialsGLB", "name": "三维模型"},
                ]}},
            )
            websocket = _FakeWebSocket()
            with patch.object(presentation, "oss_upload", upload), patch.object(presentation, "get_storage_client", return_value=_Storage()):
                markdown = asyncio.run(presentation.emit_presentation_assets(websocket, result))

        self.assertIn("![结构卡](https://", markdown)
        self.assertIn("card.png)", markdown)
        self.assertIn("![旋转图](https://", markdown)
        self.assertIn("rotation.gif)", markdown)
        json_events = [value for kind, value in websocket.events if kind == "json"]
        self.assertEqual(len(json_events), 1)
        self.assertEqual(json_events[0]["type"], "MaterialsGLB")
        self.assertEqual(json_events[0]["name"], "三维模型")
    def test_routes_and_roles_contract(self) -> None:
        route_paths = {route.path for route in main.app.routes}
        self.assertTrue({"/start", "/new-material/start", "/roles", "/new-material/constraints", "/new-material/generate"}.issubset(route_paths))

        roles = asyncio.run(main.get_teams())
        self.assertEqual(len(roles), 1)
        role = next(iter(roles.values()))
        self.assertEqual(role["name"], "新材料发现与候选生成")
        self.assertEqual(role["role_id"], "inorganic_new_material_generation_v1")
        self.assertEqual(role["addresses"][0], "src.team_config.InorganicNewMaterialDiscoveryRole")
        self.assertEqual(role["__module_class_name"], "src.team_config.InorganicNewMaterialDiscoveryRole")
        self.assertEqual(role["routing"]["service_id"], "inorganic_new_material_generation")
        self.assertIn("input_contract", role["routing"])
        self.assertIn("output_contract", role["routing"])
        self.assertIn("waiting_for_input", role["routing"]["output_contract"])
        self.assertIn("结合当前任务与完整上文", role["profile"])
        self.assertIn("材料方向、应用场景、性能目标", role["profile"])
        self.assertIn("结合当前任务与完整上文", role["actions"][0]["desc"])
        self.assertIn("只有完全没有材料、应用、性能", role["routing"]["output_contract"]["waiting_for_input"])

    def test_constraint_preview_does_not_start_gpu_work(self) -> None:
        payload = {
            "taskid": "contract-li-ps-cl",
            "idea": "生成 Li-P-S-Cl 固态电解质候选",
            "new_material": {
                "allowed_elements": ["Li", "P", "S", "Cl"],
                "target_properties": {"energy_above_hull": 0.05},
            },
        }
        result = asyncio.run(main.preview_new_material_constraints(payload))
        self.assertEqual(result["taskid"], "contract-li-ps-cl")
        self.assertEqual(result["allowed_elements"], ["Li", "P", "S", "Cl"])
        self.assertEqual(result["target_properties"]["energy_above_hull"], 0.05)

    def test_websocket_envelope_keeps_full_structured_payload(self) -> None:
        payload = {
            "taskid": "envelope-li-ps-cl",
            "new_material": {"allowed_elements": ["Li", "P", "S", "Cl"]},
        }
        request = main.ORCHESTRATOR.websocket_request(payload)
        self.assertEqual(request["taskid"], "envelope-li-ps-cl")
        self.assertEqual(request["idea"], "")
        self.assertEqual(request["user_name"], "-")
        self.assertEqual(request["file_metadata"], [])
        self.assertEqual(json.loads(request["project_idea"])["new_material"]["allowed_elements"], ["Li", "P", "S", "Cl"])
        with self.assertRaisesRegex(ValueError, "taskid"):
            main.ORCHESTRATOR.websocket_request({"idea": "missing task ID"})

    def test_action_event_contract_without_gpu(self) -> None:
        payload = {
            "taskid": "ws-li-ps-cl",
            "idea": "生成 Li-P-S-Cl 固态电解质候选",
            "new_material": {"allowed_elements": ["Li", "P", "S", "Cl"]},
        }
        websocket = _FakeWebSocket()
        action = team_config.InorganicNewMaterialDiscoveryAction()
        with (
            patch.object(team_config, "load_config", return_value={"base_url_1": "http://example.invalid", "api_key": "test"}),
            patch.object(team_config, "SeLLM", _FakeLLM),
            patch.object(team_config, "stream_llm_response", _fake_stream_llm),
            patch.object(team_config, "stream_discovery_progress", _wait_for_cancellation),
            patch.object(team_config, "run_upstream_request", return_value=_FakeResult()),
            patch.object(team_config.asyncio, "to_thread", _run_inline),
            patch.object(team_config, "result_summary", return_value="### 测试结果\n\n候选已完成。"),
        ):
            status = asyncio.run(action.run(json.dumps(payload, ensure_ascii=False), websocket, "tester", "ws-li-ps-cl", []))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        progress = [event for event in json_events if event.get("type") == "progress"]
        retrieval_progress = [event for event in json_events if event.get("type") == "retrieval_progress"]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["data"]["id"], team_config.FRONTEND_STEP_ID)
        self.assertEqual(progress[0]["data"]["stepId"], team_config.FRONTEND_STEP_ID)
        self.assertEqual(len(retrieval_progress), 1)
        self.assertEqual(retrieval_progress[0]["content"]["percent"], 100)
        self.assertEqual(retrieval_progress[0]["content"]["status"], "completed")
        self.assertEqual(texts.count(f"<<<CONTENT_START:{team_config.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts.count(f"<<<CONTENT_END:{team_config.FRONTEND_STEP_ID}>>>"), 2)
        self.assertIn("[[WORKFLOW_STATUS:ok]]", status)

    def test_domain_template_is_refined_from_upstream_context(self) -> None:
        """A domain fallback must not prevent LLM extraction from fuller evidence."""
        payload = {
            "taskid": "ws-sulfide-halide",
            "idea": "基于筛选出的硫化物及卤化物体系，生成高稳定性固态电解质晶体结构。",
        }
        websocket = _FakeWebSocket()
        action = team_config.InorganicNewMaterialDiscoveryAction()
        captured: dict[str, object] = {}

        async def resolve_with_halide(request_payload: dict, **_kwargs):
            enriched = {
                **request_payload,
                "new_material": {"allowed_elements": ["Li", "P", "S", "Cl"]},
            }
            return enriched, llm_constraint_inference.constraint_from_payload(enriched)

        def capture_request(request_payload: dict, _results_root):
            captured["payload"] = request_payload
            return _FakeResult()

        with (
            patch.object(team_config, "load_config", return_value={"base_url_1": "http://example.invalid", "api_key": "test"}),
            patch.object(team_config, "SeLLM", _FakeLLM),
            patch.object(team_config, "stream_llm_response", _fake_stream_llm),
            patch.object(team_config, "resolve_generation_request", resolve_with_halide),
            patch.object(team_config, "stream_discovery_progress", _wait_for_cancellation),
            patch.object(team_config, "run_upstream_request", capture_request),
            patch.object(team_config.asyncio, "to_thread", _run_inline),
            patch.object(team_config, "result_summary", return_value="### 测试结果\n\n候选已完成。"),
        ):
            asyncio.run(action.run(json.dumps(payload, ensure_ascii=False), websocket, "tester", "ws-sulfide-halide", []))

        self.assertEqual(captured["payload"]["new_material"]["allowed_elements"], ["Li", "P", "S", "Cl"])

    def test_team_round_preserves_outer_markers(self) -> None:
        websocket = _FakeWebSocket()
        team = _FakeTeam()
        asyncio.run(main.ORCHESTRATOR.run_round(websocket, team, "测试任务", 1, "tester", "team-round", []))

        texts = [value for kind, value in websocket.events if kind == "text"]
        self.assertEqual(team.idea, "测试任务")
        self.assertEqual(texts[0], "【XXX 开始: xxxx】")
        self.assertEqual(texts.count("[start]"), 1)
        self.assertEqual(texts.count("[end]"), 1)
        self.assertEqual(texts[-1], "【XXX 已完成: xxxx】")

    def test_input_request_is_not_reported_as_pipeline_failure(self) -> None:
        class InputRequiredTodo(_FakeTodo):
            async def run(self, *_args) -> str:
                raise llm_constraint_inference.GenerationInputRequired("请补充目标材料。")

        websocket = _FakeWebSocket()
        team = _FakeTeam(InputRequiredTodo())
        asyncio.run(main.ORCHESTRATOR.run_round(websocket, team, "测试任务", 1, "tester", "need-input", []))

        texts = [value for kind, value in websocket.events if kind == "text"]
        self.assertEqual(texts.count("[start]"), 1)
        self.assertEqual(texts.count("[end]"), 1)
        self.assertTrue(any(text.startswith("### 需要补充生成信息") for text in texts))
        self.assertEqual(texts[-1], "【XXX 等待补充: 已收到任务，等待补充材料生成信息后继续执行】")
        self.assertNotIn("【XXX 未完成: 新材料生成未得到可用候选，请查看失败原因与下一步建议】", texts)

    def test_llm_context_includes_nonstandard_upstream_fields(self) -> None:
        current, evidence = llm_constraint_inference._context({
            "taskid": "nested-upstream",
            "idea": "继续执行新材料发现",
            "upstream_result": {
                "material_conclusion": "针对锂硫化物固态电解质开展候选生成",
                "target": {"application": "固态电池"},
            },
        })
        self.assertIn("继续执行新材料发现", current)
        self.assertIn("锂硫化物固态电解质", evidence)

    def test_low_k_chiplet_context_uses_local_text_start_system(self) -> None:
        """Useful upstream context must execute even when LLM inference is unavailable."""
        payload = {
            "taskid": "low-k-chiplet",
            "idea": "基于以上力学性能指标开展材料筛选或配比优化",
            "upstream_result": {
                "material_direction": "面向超高密度 Chiplet 3D 混合键合的超低k有机-无机杂化介质材料",
                "requirements": "兼顾导热、热膨胀、界面热阻与热机械应力",
            },
        }
        with patch.object(llm_constraint_inference, "infer_element_system", return_value=None):
            enriched, constraints = asyncio.run(llm_constraint_inference.resolve_generation_request(payload))

        self.assertEqual(constraints.allowed_elements, ["Si", "O", "C", "H"])
        self.assertEqual(enriched["new_material"]["allowed_elements"], ["Si", "O", "C", "H"])
        self.assertTrue(any("文本解析" in note for note in constraints.notes))

    def test_ni_superalloy_direction_gets_local_start_system_without_llm(self) -> None:
        payload = {
            "taskid": "nih-thin-handoff",
            "idea": "基于NIH-1镍基合金成分为元素基底，启动高通量新晶体相结构生成，并计算热力学稳定性与电子结构特征。",
        }
        with patch.object(llm_constraint_inference, "infer_element_system", return_value=None):
            enriched, constraints = asyncio.run(llm_constraint_inference.resolve_generation_request(payload))

        self.assertEqual(constraints.allowed_elements, ["Ni", "Cr", "Co", "Al", "Ta", "W"])
        self.assertEqual(enriched["new_material"]["allowed_elements"], ["Ni", "Cr", "Co", "Al", "Ta", "W"])
        self.assertTrue(any("文本解析" in note for note in constraints.notes))

    def test_role_registers_only_discovery_action(self) -> None:
        role = team_config.InorganicNewMaterialDiscoveryRole()
        self.assertEqual(len(role.actions), 1)
        self.assertIsInstance(role.actions[0], team_config.InorganicNewMaterialDiscoveryAction)
