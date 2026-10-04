from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import WebSocketDisconnect

from src.material_workflow.llm_constraint_inference import GenerationInputRequired, constraint_from_payload, resolve_generation_request, infer_element_system
from src.material_workflow.requirement_review import ReviewStopped, request_element_clarification, review_enabled
import src.team_config as team_config


class Socket:
    def __init__(self, replies=()):
        self.sent = []
        self.replies = list(replies)

    async def send_json(self, value):
        self.sent.append(value)

    async def send_text(self, value):
        self.sent.append(value)

    async def receive_json(self):
        if not self.replies:
            raise WebSocketDisconnect()
        reply = self.replies.pop(0)
        if callable(reply):
            return reply(self.sent[0])
        return reply


def reply(request, action, value=None):
    result = {"type": "checkpoint.reply", "schema_version": 1, "taskid": request["taskid"],
              "request_id": request["request_id"], "action": action}
    if action == "apply":
        result["data"] = {"edits": [], "answers": [{"id": request["data"]["questions"][0]["id"], "answer": value}] if value is not None else []}
    return result


class RequirementReviewTests(unittest.TestCase):
    def test_flag_requires_both_switches(self):
        with patch.dict("os.environ", {"REQUIREMENT_CONFIRMATION_ENABLED": "1", "REQUIREMENT_REVIEW_PROTOCOL": "structured"}):
            self.assertTrue(review_enabled())
        with patch.dict("os.environ", {"REQUIREMENT_CONFIRMATION_ENABLED": "0", "REQUIREMENT_REVIEW_PROTOCOL": "structured"}):
            self.assertFalse(review_enabled())

    def test_invalid_reply_preserves_input_then_apply(self):
        socket = Socket([lambda req: {**reply(req, "apply", "铁铝"), "taskid": "other"},
                         lambda req: reply(req, "apply", "   "),
                         lambda req: reply(req, "apply", "铁铝")])
        self.assertEqual(asyncio.run(request_element_clarification(socket, "t1", "缺少元素")), "铁铝")
        self.assertEqual([event["type"] for event in socket.sent],
                         ["requirement_review_request", "requirement_review_error", "requirement_review_error", "requirement_review_accepted"])
        self.assertEqual(socket.sent[1]["message"], "taskid 或 request_id 不匹配。")
        self.assertEqual(socket.sent[0]["schema_version"], 1)
        self.assertEqual(socket.sent[0]["stageid"], "FILAMENT_SELECTION_OPTIMIZATION")
        self.assertEqual(socket.sent[0]["data"]["fields"], [])
        self.assertTrue(socket.sent[0]["data"]["questions"][0]["required"])
        self.assertEqual(socket.sent[0]["data"]["baseline_markdown"], "")
        self.assertFalse(socket.sent[0]["data"]["allow_comment"])
        self.assertEqual(socket.sent[-1]["action"], "apply")
        self.assertTrue(socket.sent[-1]["will_reanalyze"])

    def test_continue_stop_disconnect(self):
        for action, result in (("continue", None), ("stop", ReviewStopped)):
            socket = Socket([lambda req, action=action: reply(req, action)])
            if result is ReviewStopped:
                with self.assertRaises(ReviewStopped):
                    asyncio.run(request_element_clarification(socket, "t1", "缺少元素"))
            else:
                self.assertIsNone(asyncio.run(request_element_clarification(socket, "t1", "缺少元素")))
        socket = Socket()
        with self.assertRaises(WebSocketDisconnect):
            asyncio.run(request_element_clarification(socket, "t1", "缺少元素"))
        self.assertEqual(len(socket.sent), 1)

    def test_generic_metal_does_not_invent_elements(self):
        payload = {"taskid": "t1", "idea": "使用金属做一个新结构生成"}
        with patch.dict("os.environ", {"NEW_MATERIAL_LLM_ELEMENT_INFERENCE": "true"}):
            self.assertIsNone(asyncio.run(infer_element_system(payload)))
            with self.assertRaises(GenerationInputRequired):
                asyncio.run(resolve_generation_request(payload))
        merged = {**payload, "idea": payload["idea"] + "\n用户补充：铁铝"}
        self.assertEqual(constraint_from_payload(merged).allowed_elements, ["Fe", "Al"])

    def test_single_explicit_supplement_is_element_not_incidental_prose(self):
        original = "【用户原始目标】 使用金属做一个新结构生成 【历史执行上下文】 任务刚启动。 【本轮必须执行的任务】 在金属元素空间内生成候选晶体结构。"
        with self.assertRaises(ValueError):
            constraint_from_payload({"taskid": "t1", "idea": original})
        for value, expected in (("Fe", ["Fe"]), ("fe", ["Fe"]), ("铁", ["Fe"]),
                                ("Ag Fe", ["Ag", "Fe"]), ("Ag  Fe", ["Ag", "Fe"]),
                                ("fe ag", ["Fe", "Ag"]), ("Fe ag", ["Fe", "Ag"])):
            parsed = constraint_from_payload({"taskid": "t1", "idea": original + "\n用户补充：" + value})
            self.assertEqual(parsed.allowed_elements, expected)
        with self.assertRaises(ValueError):
            constraint_from_payload({"taskid": "t1", "idea": original + "\n用户补充：金属"})
        with patch.dict("os.environ", {"NEW_MATERIAL_LLM_ELEMENT_INFERENCE": "true"}):
            with self.assertRaises(GenerationInputRequired):
                asyncio.run(resolve_generation_request({"taskid": "single-element-check", "idea": original + "\n用户补充：金属"}))
        actual = ("【用户原始目标】 使用金属做一个新结构生成 【历史执行上下文】 任务刚启动，尚未执行任何历史步骤。 "
                  "【最新用户补充，高优先级】 无 【本轮必须执行的任务】 调用材料-计算模拟（新材料结构生成）能力，"
                  "在金属元素空间内按目标性能生成候选晶体或合金结构并进行稳定性初筛。")
        _, resolved = asyncio.run(resolve_generation_request({"taskid": "t1", "idea": actual + "\n用户补充：Ag Fe"}))
        self.assertEqual(resolved.allowed_elements, ["Ag", "Fe"])
        _, resolved = asyncio.run(resolve_generation_request({"taskid": "t1", "idea": actual + "\n用户补充：fe ag"}))
        self.assertEqual(resolved.allowed_elements, ["Fe", "Ag"])
        for invalid in ("Fe 性能要好", "Ag Metal", "Ag Xx", "metal", "fe xx"):
            with self.assertRaises(ValueError):
                constraint_from_payload({"taskid": "t1", "idea": original + "\n用户补充：" + invalid})

    def test_action_continue_never_starts_generation(self):
        socket = Socket([lambda req: reply(req, "continue")])
        action = team_config.InorganicNewMaterialDiscoveryAction()

        async def missing(*args, **kwargs):
            raise GenerationInputRequired("缺少元素")

        with (patch.object(team_config, "review_enabled", return_value=True),
              patch.object(team_config, "resolve_generation_request", missing),
              patch.object(team_config, "load_config", return_value={"base_url_1": "test", "api_key": "test"}),
              patch.object(team_config, "SeLLM", return_value=object()),
              patch.object(team_config, "run_upstream_request") as generate):
            with self.assertRaises(GenerationInputRequired):
                asyncio.run(action.run(json.dumps({"taskid": "t1", "idea": "使用金属"}), socket, "u", "t1", []))
            generate.assert_not_called()
        self.assertEqual(len([event for event in socket.sent if isinstance(event, dict) and event.get("type") == "requirement_review_request"]), 1)

    def test_action_waits_before_generation_and_reparses(self):
        socket = Socket([lambda req: reply(req, "apply", "铁铝")])
        action = team_config.InorganicNewMaterialDiscoveryAction()
        payload = {"taskid": "t1", "idea": "使用金属做一个新结构生成"}
        captured = []

        async def fake_resolve(value, **kwargs):
            if "用户补充：" not in value["idea"]:
                raise GenerationInputRequired("缺少元素")
            return value, constraint_from_payload(value)

        async def fake_stream(*args, **kwargs):
            return None

        async def fake_thread(function, *args):
            captured.append(args[0])
            class Result:
                status = "ok"
                artifacts = {"presentation": {"assets": []}}
            return Result()

        with (patch.object(team_config, "review_enabled", return_value=True),
              patch.object(team_config, "resolve_generation_request", fake_resolve),
              patch.object(team_config, "load_config", return_value={"base_url_1": "test", "api_key": "test"}),
              patch.object(team_config, "SeLLM", return_value=object()),
              patch.object(team_config, "stream_discovery_progress", fake_stream),
              patch.object(team_config, "run_upstream_request", return_value=object()),
              patch.object(team_config.asyncio, "to_thread", fake_thread),
              patch.object(team_config, "result_summary", return_value="done"),
              patch.object(action, "_stream_authoritative_markdown", fake_stream)):
            asyncio.run(action.run(json.dumps(payload, ensure_ascii=False), socket, "u", "t1", []))
        self.assertEqual(captured[0]["idea"], "使用金属做一个新结构生成\n用户补充：铁铝")
        self.assertTrue(any(event.get("type") == "requirement_review_accepted" for event in socket.sent if isinstance(event, dict)))

    def test_empty_apply_and_wrong_field_retry_on_same_request(self):
        socket = Socket([lambda req: reply(req, "apply"),
                         lambda req: {**reply(req, "apply", "铁铝"), "data": {"edits": [], "answers": [{"id": "wrong", "answer": "铁铝"}]}},
                         lambda req: reply(req, "apply", "铁铝")])
        self.assertEqual(asyncio.run(request_element_clarification(socket, "t1", "缺少元素")), "铁铝")
        self.assertEqual([event["type"] for event in socket.sent],
                         ["requirement_review_request", "requirement_review_error", "requirement_review_error", "requirement_review_accepted"])
        self.assertTrue(all(event["request_id"] == socket.sent[0]["request_id"] for event in socket.sent))
