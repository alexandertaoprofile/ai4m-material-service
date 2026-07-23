from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from src.team_config import MaterialMature


ROOT = Path(__file__).resolve().parents[1]


def _payload(taskid: str) -> dict:
    return {
        "taskid": taskid,
        "idea": "查询 IN718 在常温下的材料性质",
        "mature_material": {
            "material_queries": ["IN718"],
            "service_temperature_C": 21,
            "property_constraints": [
                {"property": "density", "operator": "<=", "value": 8300, "unit": "kg/m³"}
            ],
        },
    }


class _FakeWebSocket:
    def __init__(self, payload: dict):
        self.payload = payload
        self.client = None
        self.events: list[tuple[str, object]] = []
        self.closed = False

    async def accept(self) -> None:
        self.events.append(("accept", None))

    async def receive(self) -> dict:
        return {"type": "websocket.receive", "text": json.dumps(self.payload, ensure_ascii=False)}

    async def send_text(self, value: str) -> None:
        self.events.append(("text", value))

    async def send_json(self, value: dict) -> None:
        self.events.append(("json", value))

    async def close(self) -> None:
        self.closed = True


class MatureMaterialServiceTest(unittest.TestCase):
    def _service(self, results_root: Path) -> MaterialMature:
        return MaterialMature(
            catalog_root=ROOT / "data" / "processed",
            raw_data_root=ROOT / "data",
            results_root=results_root,
            service_name="mature-material",
        )

    def test_orchestrator_returns_traceable_catalogue_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            contract = service.contract(_payload("unit-in718"))
            result = asyncio.run(service.run(contract))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"][0]["material"]["material_id"], "MAT-IN718")
        self.assertTrue(result["results"][0]["eligible"])
        self.assertEqual(result["name_resolution"][0]["status"], "matched")

    def test_unconstrained_lookup_does_not_claim_performance_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            payload = _payload("unit-unconstrained")
            payload["mature_material"].pop("property_constraints")
            result = asyncio.run(service.run(service.contract(payload)))

        conclusion = service.summary(result)
        self.assertIn("未给出量化性质阈值", conclusion)
        self.assertNotIn("满足当前可比较的性质条件", conclusion)

    def test_frontend_event_contract(self) -> None:
        async def fake_publish(taskid: str, assets: list[dict]) -> dict[str, str]:
            return {item["name"]: f"https://example.invalid/{taskid}/{item['name']}.png" for item in assets}

        with tempfile.TemporaryDirectory() as temporary:
            service = self._service(Path(temporary))
            websocket = _FakeWebSocket(_payload("ws-contract"))
            with patch.object(main, "ORCHESTRATOR", service), patch.object(main, "publish_png_assets", fake_publish), patch.dict(os.environ, {"MATURE_MATERIAL_LLM_STREAM": "false"}):
                asyncio.run(main.start(websocket))

        route_paths = {route.path for route in main.app.routes}
        self.assertTrue({"/start", "/mature-material/start", "/roles"}.issubset(route_paths))

        texts = [value for kind, value in websocket.events if kind == "text"]
        json_events = [value for kind, value in websocket.events if kind == "json"]
        progress_events = [event for event in json_events if event.get("type") == "progress"]
        result_events = [event for event in json_events if event.get("type") == "result"]
        self.assertEqual(texts[0], "[start]")
        self.assertEqual(texts.count(f"<<<CONTENT_START:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts.count(f"<<<CONTENT_END:{main.FRONTEND_STEP_ID}>>>"), 2)
        self.assertEqual(texts[-1], "[end]")
        self.assertEqual(len(progress_events), 1)
        self.assertEqual(progress_events[0]["data"]["id"], main.FRONTEND_STEP_ID)
        self.assertEqual(progress_events[0]["data"]["stepId"], main.FRONTEND_STEP_ID)
        self.assertEqual(len(result_events), 1)
        self.assertTrue(any(event.get("type") == "MaterialsPNG" for event in json_events))

        start_index = next(index for index, event in enumerate(websocket.events) if event == ("text", "[start]"))
        progress_index = next(index for index, event in enumerate(websocket.events) if event == ("json", progress_events[0]))
        result_index = next(index for index, event in enumerate(websocket.events) if event == ("json", result_events[0]))
        end_index = next(index for index, event in enumerate(websocket.events) if event == ("text", "[end]"))
        self.assertLess(start_index, progress_index)
        self.assertLess(progress_index, result_index)
        self.assertLess(result_index, end_index)
        self.assertTrue(websocket.closed)

    def test_roles_contract_shape(self) -> None:
        payload = main.roles()
        self.assertEqual(len(payload), 1)
        role = next(iter(payload.values()))
        self.assertEqual(role["role_id"], "mature_material_catalog_v1")
        self.assertEqual(role["addresses"][0], "src.team_config.MaterialMature")
        self.assertEqual(role["__module_class_name"], "src.team_config.MaterialMature")
        self.assertEqual(role["actions"][0]["__module_class_name"], "src.team_config.MatureMaterialCatalogQuery")
        self.assertIn("rc", role)
        self.assertIn("planner", role)

    def test_processed_catalogue_integrity(self) -> None:
        processed = ROOT / "data" / "processed"

        def read(name: str) -> list[dict[str, str]]:
            with (processed / name).open(encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))

        materials = read("materials.csv")
        material_ids = [row.get("material_id", "") for row in materials]
        self.assertTrue(material_ids)
        self.assertNotIn("", material_ids)
        self.assertEqual(len(material_ids), len(set(material_ids)))

        known_ids = set(material_ids)
        for row in read("material_aliases.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("alias", "").strip())

        for row in read("property_points.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("property", "").strip())
            self.assertTrue(row.get("unit", "").strip())
            float(row["value"])

        for row in read("curve_data.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("property", "").strip())
            self.assertTrue(row.get("SI_unit", "").strip())
            float(row["temperature_K"])
            float(row["value_SI"])

        for row in read("composition_long.csv"):
            self.assertIn(row.get("material_id"), known_ids)
            self.assertTrue(row.get("component", "").strip())


if __name__ == "__main__":
    unittest.main()
