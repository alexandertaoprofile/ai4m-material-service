from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import main
from src.application.request_normalization import normalize_request
from src.team_config import FRONTEND_STEP_ID


class FakeWebSocket:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.url = "ws://platform.example/refractory-validation/start"
        self.events: list[tuple[str, object]] = []
        self.closed = False
    async def accept(self): pass
    async def receive_json(self): return self.payload
    async def send_text(self, value): self.events.append(("text", value))
    async def send_json(self, value): self.events.append(("json", value))
    async def close(self): self.closed = True


async def run_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class RefractoryServiceContractTest(unittest.TestCase):
    def test_w_reference_request_is_normalized(self):
        request = normalize_request({"taskid": "w-01", "idea": "请验证纯钨 W 的热弹性", "refractory_validation": {"material_system": "W"}})
        self.assertEqual(request.material_system, "W")
        self.assertEqual(request.execution_mode, "reference_case")

    def test_other_refractory_system_requires_own_evidence(self):
        with self.assertRaisesRegex(ValueError, "仅开放 W"):
            normalize_request({"taskid": "mo-01", "refractory_validation": {"material_system": "Mo"}})

    def test_routes_and_role_are_discoverable(self):
        paths = {route.path for route in main.app.routes}
        self.assertTrue({"/start", "/refractory-validation/start", "/roles", "/refractory-validation/evaluate"}.issubset(paths))
        self.assertIn("RefractoryMultiscaleValidationRole", main.roles())

    def test_websocket_keeps_markers_progress_and_result(self):
        payload = {"taskid": "w-ws", "idea": "验证钨", "refractory_validation": {"material_system": "W"}}
        ws = FakeWebSocket(payload)
        with patch.object(main.asyncio, "to_thread", run_inline), patch.object(main, "publish_png_assets", new_callable=AsyncMock, return_value={}):
            asyncio.run(main.start(ws))
        texts = [value for kind, value in ws.events if kind == "text"]
        events = [value for kind, value in ws.events if kind == "json"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts[-1], "[end]")
        self.assertIn(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>", texts)
        self.assertTrue(any("http://platform.example/refractory-validation/tasks/w-ws/assets/training_convergence.png" in text for text in texts))
        self.assertEqual([item["type"] for item in events if item["type"] in {"progress", "result"}], ["progress", "result"])
        self.assertTrue(ws.closed)

    def test_w14_reference_case_emits_task_scoped_png_assets(self):
        result = main._run({
            "taskid": "w14-visual-contract",
            "idea": "验证 W-14 的训练收敛和 NPT 热响应",
            "refractory_validation": {"material_system": "W"},
        }, publish_assets=False)
        assets = result["presentation"]["assets"]
        self.assertGreaterEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "training_convergence")
        for asset in assets:
            self.assertEqual(asset["type"], "MaterialsPNG")
            self.assertTrue(asset["url"].startswith("/refractory-validation/tasks/w14-visual-contract/assets/"))
            self.assertTrue((Path(main.RESULTS) / "w14-visual-contract" / "presentation" / Path(asset["path"]).name).is_file())
        summary = (Path(main.RESULTS) / "w14-visual-contract" / "presentation" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("## 1. 材料体系与计算任务定义", summary)
        self.assertIn("## 4. 性能验证与可信度评估", summary)
        self.assertTrue((Path(main.RESULTS) / "w14-visual-contract" / "logs" / "evidence_audit.md").is_file())
