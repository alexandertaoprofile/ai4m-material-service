"""HTTP and WebSocket transport for the mature-material catalogue service."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.catalog.assets import publish_png_assets
from src.catalog.narration import (
    stream_authoritative_markdown,
    stream_markdown_rows,
)
from src.catalog.presentation import comparison_markdown, conclusion_markdown, resolution_markdown
from src.fluid_lubricant.api import router_for as fluid_screen_router_for
from src.settings import MatureMaterialSettings
from src.service_identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_DESCRIPTION, ROLE_NAME, ROLE_PROFILE, SERVICE_ID
from src.team_config import MaterialMature

SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT / ".env")
SETTINGS = MatureMaterialSettings.from_env()
SERVICE = SETTINGS.service_name
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "成熟材料检索与性能对比"
PORT = SETTINGS.port
RESULTS = SETTINGS.results_root
RAW_DATA_ROOT = SETTINGS.raw_data_root
CATALOG_ROOT = SETTINGS.catalog_root
ORCHESTRATOR = MaterialMature(
    catalog_root=CATALOG_ROOT,
    raw_data_root=RAW_DATA_ROOT,
    results_root=RESULTS,
    service_name=SERVICE,
)
app = FastAPI(title="Mature Material Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
FLUID_EVIDENCE_DATABASE = SERVICE_ROOT / "data/processed/fluid_lubricant/2026-08-04_v1/fluid_property_evidence.sqlite"
app.include_router(fluid_screen_router_for(FLUID_EVIDENCE_DATABASE, RESULTS / "fluid_lubricant"))
logger = logging.getLogger("mature_material")


def _run_log(taskid: str, event: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    suffix = f" {details}" if details else ""
    print(f"[MATURE][{taskid}] {event}{suffix}", flush=True)


def _log_input_audit(payload: dict[str, Any], constraints: dict[str, Any]) -> None:
    """Make upstream loss of a numeric user turn visible in service logs.

    Log only the recognised message envelopes and truncated text.  This is
    enough to distinguish an upstream forwarding failure from a parser error,
    without dumping an entire conversation or arbitrary request metadata.
    """
    message_keys = (
        "current_user_message", "latest_user_message", "user_input", "user_message",
        "follow_up_message", "latest_message", "prompt", "requirement", "question", "query",
        "idea", "instruction", "previous_step", "task", "messages", "history", "conversation", "data", "input",
    )
    def preview(value: Any, limit: int = 500) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

    forwarded = {
        key: preview(payload.get(key))
        for key in message_keys
        if payload.get(key) is not None and key not in {"messages", "history", "conversation", "data", "input"}
    }
    detected_user_text = ORCHESTRATOR._direct_user_requirement(payload)
    screening_request = constraints.get("screening_request") or {}
    _run_log(
        constraints["taskid"],
        "upstream input audit",
        payload_keys=sorted(payload.keys()),
        forwarded_message_fields=forwarded,
        detected_latest_user_text=preview(detected_user_text),
        selected_raw_requirement=preview(constraints.get("raw_requirement")),
        workflow=constraints.get("workflow_kind", "catalogue"),
        parsed_screening_constraints=screening_request.get("property_constraints", []),
    )


# Private compatibility helpers keep local callers stable while all workflow
# logic lives in src.team_config.MaterialMature.
def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    constraints = ORCHESTRATOR.contract(payload)
    _log_input_audit(payload, constraints)
    return constraints


async def _manifest(constraints: dict[str, Any]) -> dict[str, Any]:
    return await ORCHESTRATOR.run(constraints)


def _task_storage_key(taskid: str) -> str:
    return ORCHESTRATOR.task_storage_key(taskid)


def _summary(result: dict[str, Any]) -> str:
    return ORCHESTRATOR.summary(result)


def _save(manifest: dict[str, Any]) -> None:
    ORCHESTRATOR.save(manifest)


def _render_assets(result: dict[str, Any]) -> list[dict[str, str]]:
    return ORCHESTRATOR.render_assets(result)


@app.get("/")
def root():
    return {"service": SERVICE, "status": "ok", "port": PORT, "contract": "mature_material v1"}


@app.get("/roles")
def roles():
    """Gateway discovery metadata, preserving the existing external shape."""
    profile = ROLE_PROFILE
    return {
        profile: {
            "name": ROLE_NAME,
            "profile": profile,
            "goal": "整理已有材料证据，并从本服务目录返回可追溯的性质和核验结果。",
            "constraints": "仅使用本服务已入库的结构化材料数据作为已核验事实；上游信息须标注为待核验，缺失、单位不一致和温度超范围须如实返回。",
            "desc": ROLE_DESCRIPTION,
            "is_human": False,
            "role_id": f"{SERVICE_ID}_v1",
            "states": ["0. Query catalog"],
            "actions": [{
                "name": ACTION_NAME,
                "i_context": "",
                "prefix": f"You are a {profile}",
                "desc": ACTION_DESCRIPTION,
                "__module_class_name": "src.team_config.MatureMaterialCatalogQuery",
            }],
            "rc": {"memory": {"storage": [], "index": {}, "ignore_id": False}, "working_memory": {"storage": [], "index": {}, "ignore_id": False}, "state": -1, "watch": ["alpha.actions.add_requirement.UserRequirement"], "react_mode": "react", "max_react_loop": 1},
            "addresses": ["src.team_config.MaterialMature", "已有材料检索与性质核验"],
            "planner": {"plan": {"goal": "", "context": "", "tasks": [], "task_map": {}, "current_task_id": ""}, "working_memory": {"storage": [], "index": {}, "ignore_id": False}, "auto_run": False, "use_tools": False},
            "routing": {
                "service_id": "mature_material_catalog",
                "priority": 2,
                "match_when": "上游需要核验已有商品材料，或在自定义合金成分优化前先获取已入库商品金属/合金的可追溯基准；导电润滑油/导电润滑介质的数值初筛也由本服务处理。",
                "include_keywords": ["材料筛选与计算", "材料筛选", "材料选型", "候选材料", "性质对比", "导电润滑", "导电润滑油", "润滑介质", "旋转黏度", "旋转粘度", "电导率", "电阻率", "商用耗材", "丝材", "FDM", "FFF", "PLA", "PETG", "ASA", "ABS", "PC", "PA", "PEEK", "商品名", "牌号", "标准号", "UNS", "AMS", "MIL", "ASTM", "GB/T", "Inconel", "TIMETAL", "316L", "高温合金", "高熵合金", "HEA", "MPEA", "合金基准", "商品合金"],
                "exclude_keywords": ["严格化学式", "元素比例", "原子百分比", "成分优化", "成分空间"],
                "route_before": ["alloy_composition_optimization"],
                "workflow_hint": "若任务先要查询商品金属/合金基准，再优化自定义成分，先调用本服务；本服务输出的基准记录不等于目标成分，随后再调用 alloy_composition_optimization。",
                "input_contract": {
                    "required_any": ["material_queries/材料名称", "厂家或牌号", "标准号", "upstream_evidence（性质、工况、来源）", "合金体系或明确的金属基准需求", "导电润滑应用 + 电导率/电阻率或动态黏度数值条件"],
                    "optional": ["material_families", "service_temperature_C", "property_constraints", "current_user_message/latest_user_message，或含 role/sender=user 的 messages/history"],
                    "multi_turn_requirement": "多轮数值筛选允许传递压缩摘要；摘要须保留可执行的电阻率/电导率、黏度和温度具体数值及单位。“严格指标”“按用户要求”等概述不足以执行筛选。",
                },
                "output_contract": {
                    "catalog_matched": "目录匹配材料、已核验性质、来源和缺失项。",
                    "alloy_reference_catalogued": "按合金体系映射的商品金属/合金基准；仅用于对照，不是目标自定义成分的精确匹配。",
                    "fluid_initial_screen_completed": "导电液体候选的可追溯数值初筛证据；不等同于导电润滑油推荐、长期润滑或耐温验证通过。",
                    "upstream_evidence_only": "仅整理上游材料证据；目录未核验。",
                    "needs_literature_screening": "无可承接材料证据或目录记录；向用户显示文献筛选建议。",
                },
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.MaterialMature",
        }
    }


@app.get("/health")
def health():
    return {"status": "ok", "catalog_ready": (CATALOG_ROOT / "materials.csv").is_file(), "raw_data_root_available": RAW_DATA_ROOT.exists()}


@app.post("/mature-material/constraints")
def preview(payload: dict = Body(...)):
    try:
        return _contract(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/mature-material/query")
async def query(payload: dict = Body(...)):
    try:
        result = await _manifest(_contract(payload))
        _save(result)
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/mature-material/tasks/{taskid}")
def task(taskid: str):
    result = ORCHESTRATOR.load_task(taskid)
    if result is None:
        raise HTTPException(404, "task manifest not found")
    return result


@app.get("/mature-material/tasks/{taskid}/assets/{asset_name}")
def asset(taskid: str, asset_name: str):
    try:
        path = ORCHESTRATOR.asset_path(taskid, asset_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "asset not found")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.websocket("/start")
@app.websocket("/mature-material/start")
async def start(websocket: WebSocket):
    await websocket.accept()
    peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    print(f"[WS /mature-material/start] accepted peer={peer}; waiting for initial JSON")
    try:
        event = await websocket.receive()
        if event["type"] == "websocket.disconnect":
            print(f"[WS /mature-material/start] peer={peer} disconnected before initial JSON (code={event.get('code')})")
            return
        raw_payload = event.get("text")
        if raw_payload is None and event.get("bytes") is not None:
            raw_payload = event["bytes"].decode("utf-8")
        if raw_payload is None:
            raise ValueError("initial WebSocket message must be JSON text")
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("initial WebSocket JSON must be an object")
        constraints = _contract(payload)
        upstream_preview = re.sub(r"\s+", " ", str(constraints.get("upstream_context") or constraints.get("raw_requirement") or ""))[:600]
        print(
            f"[WS /mature-material/start] upstream received taskid={constraints['taskid']} "
            f"keys={constraints.get('upstream_context_keys', [])} user={str(payload.get('user_name') or '-')[:80]!r} "
            f"files={len(payload.get('file_metadata') or [])} context_chars={len(str(constraints.get('upstream_context') or constraints.get('raw_requirement') or ''))} "
            f"preview={upstream_preview!r}"
        )
        _run_log(
            constraints["taskid"],
            "accepted catalog query",
            workflow=constraints.get("workflow_kind", "catalogue"),
            material_queries=constraints.get("material_queries", []),
            material_families=constraints.get("material_families", []),
            standards=constraints.get("standards", []),
            property_constraints=len(constraints.get("property_constraints", constraints.get("screening_request", {}).get("property_constraints", []))),
            service_temperature_K=constraints.get("service_temperature_K"),
            top_k=constraints.get("top_k"),
        )
        print(f"[WS /mature-material/start] received taskid={constraints['taskid']} peer={peer}; querying catalog")
        await websocket.send_text("[start]")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": constraints["taskid"], "type": "progress", "data": {"id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "status": "in_progress", "description": "正在规范化别名、核验材料状态和温度条件，并读取可追溯性质证据。"}})
        result = await _manifest(constraints)
        _save(result)
        resolutions = result.get("name_resolution") or []
        resolution_counts: dict[str, int] = {}
        for item in resolutions:
            status = str(item.get("status") or "unknown")
            resolution_counts[status] = resolution_counts.get(status, 0) + 1
        candidates = result.get("results") or []
        candidate_names = [
            str(item.get("material", {}).get("display_name") or item.get("material", {}).get("material_id") or "-")
            for item in candidates[:8]
        ]
        _run_log(
            result["taskid"],
            "catalog query completed",
            status=result.get("status"),
            candidates=len(candidates),
            eligible=(sum(bool(item.get("eligible")) for item in candidates) if constraints.get("property_constraints") else "not_evaluated"),
            name_resolution=resolution_counts,
            outcome=result.get("data_status", {}).get("outcome"),
            candidate_names=candidate_names,
        )
        assets = _render_assets(result)
        _run_log(result["taskid"], "presentation prepared", assets=[item["name"] for item in assets])
        result["presentation"] = {"summary_markdown": _summary(result), "assets": assets}
        _save(result)
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>")
        first_section, second_section, conclusion = ORCHESTRATOR.presentation_sections(result)
        await stream_markdown_rows(websocket, first_section)
        await websocket.send_text(f"<<<CONTENT_END:{FRONTEND_STEP_ID}>>>")
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>")
        await stream_authoritative_markdown(websocket, second_section, section="catalogue_result")
        await websocket.send_text("\n")
        if assets:
            try:
                logger.info("[mature-assets] publishing taskid=%s assets=%s", result["taskid"], [item["name"] for item in assets])
                public_urls = await publish_png_assets(result["taskid"], assets)
                for item in assets:
                    item["url"] = public_urls.get(item["name"], "")
                    item.pop("local_path", None)
                    if not item["url"]:
                        continue
                    logger.info("[mature-assets] emitting asset event taskid=%s name=%s url=%s", result["taskid"], item["name"], item["url"])
                    await websocket.send_json({
                        "step_id": FRONTEND_STEP_ID,
                        "stepId": FRONTEND_STEP_ID,
                        "title": FRONTEND_STEP_TITLE,
                        "name": item["name"],
                        "docs": item["description"],
                        "url": item["url"],
                        "type": item["type"],
                    })
            except Exception as exc:
                logger.exception("[mature-assets] publishing failed taskid=%s error=%s", result["taskid"], exc)
        # Reuse the service's existing LLM-backed factual Markdown relay for
        # the conclusion, including the fluid workflow's priority-material
        # table.  It streams tokens while requiring the table text unchanged.
        await stream_authoritative_markdown(websocket, conclusion, section="catalogue_conclusion")
        result["presentation"]["customer_conclusion"] = conclusion
        result["presentation"]["assets"] = [item for item in assets if item.get("url")]
        _save(result)
        await websocket.send_text(f"<<<CONTENT_END:{FRONTEND_STEP_ID}>>>")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": result["taskid"], "type": "result", "data": result})
        await websocket.send_text("[end]")
        _run_log(
            result["taskid"],
            "completed",
            assets=[item["name"] for item in result["presentation"]["assets"]],
            manifest=str(RESULTS / _task_storage_key(result["taskid"]) / "manifest.json"),
        )
        print(f"[WS /mature-material/start] completed taskid={result['taskid']} peer={peer}")
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[WS /mature-material/start] invalid initial message from peer={peer}: {exc}")
        try:
            await websocket.send_text(f"\n输入有误：{exc}。请发送 JSON 请求；可使用 mature_material.material_queries、material_families 和 property_constraints。\n")
        except (RuntimeError, WebSocketDisconnect):
            pass
    except WebSocketDisconnect:
        print(f"[WS /mature-material/start] peer={peer} disconnected during response")
        return
    except Exception as exc:
        print(f"[WS /mature-material/start] failed: {exc!r}")
        taskid = locals().get("constraints", {}).get("taskid", "unknown")
        _run_log(str(taskid), "failed", error=repr(exc))
        try:
            await websocket.send_text(f"\n材料目录查询失败：{exc}\n")
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            # 审查器或浏览器可能已完成关闭握手；不再发送第二个 close 帧。
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
