"""HTTP and WebSocket transport for the mature-material catalogue service."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.catalog.assets import publish_png_assets
from src.catalog.narration import (
    stream_authoritative_markdown,
    stream_customer_conclusion,
    stream_markdown_rows,
)
from src.catalog.presentation import comparison_markdown, resolution_markdown
from src.settings import MatureMaterialSettings
from src.team_config import MaterialMature

load_dotenv()
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
logger = logging.getLogger("mature_material")


def _run_log(taskid: str, event: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    suffix = f" {details}" if details else ""
    print(f"[MATURE][{taskid}] {event}{suffix}", flush=True)


# Private compatibility helpers keep local callers stable while all workflow
# logic lives in src.team_config.MaterialMature.
def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    return ORCHESTRATOR.contract(payload)


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
    profile = (
        "子流程：已有/商品/成熟材料数据库检索与性质核验（mature_material_catalog）。"
        "处理已存在材料的筛选、选型、牌号/标准核对、热物性或机械性质对比。"
        "根据材料名称、牌号、标准、材料族、服役温度和性质条件，在已清洗且可追溯的材料目录中返回候选、性质证据及来源。"
        "适用于‘材料筛选与计算’、商用耗材、FDM/FFF 丝材、已有材料类别或明确牌号的查询；没有牌号时可按目录中的材料类别给出候选对比。"
        "仅当请求同时给出元素体系/化学式且明确要求生成数据库外新晶体时，才应进入新材料服务；高熵合金/HEA/MPEA 的成分设计、元素比例优化、配方生成和成分空间搜索应进入合金配比优化服务。"
    )
    return {
        profile: {
            "name": "成熟材料数据库检索与性质核验",
            "profile": profile,
            "goal": "从已有材料数据库查询可追溯的性质和筛选结果。",
            "constraints": "仅使用本服务已入库的结构化材料数据；缺失、单位不一致和温度超范围须如实返回。",
            "desc": "仅用于已有商品材料的牌号核对、性质查询、性能对比与选型；不做新材料或高熵合金成分设计。",
            "is_human": False,
            "role_id": "mature_material_catalog_v1",
            "states": ["0. Query catalog"],
            "actions": [{
                "name": "成熟材料目录查询",
                "i_context": "",
                "prefix": f"You are a {profile}",
                "desc": "读取上游需求并查询已有材料目录，输出材料性质、工况、来源和筛选证据；不进行成分优化。",
                "__module_class_name": "src.team_config.MatureMaterialCatalogQuery",
            }],
            "rc": {"memory": {"storage": [], "index": {}, "ignore_id": False}, "working_memory": {"storage": [], "index": {}, "ignore_id": False}, "state": -1, "watch": ["alpha.actions.add_requirement.UserRequirement"], "react_mode": "react", "max_react_loop": 1},
            "addresses": ["src.team_config.MaterialMature", "已有材料检索与性质核验"],
            "planner": {"plan": {"goal": "", "context": "", "tasks": [], "task_map": {}, "current_task_id": ""}, "working_memory": {"storage": [], "index": {}, "ignore_id": False}, "auto_run": False, "use_tools": False},
            "routing": {
                "service_id": "mature_material_catalog",
                "priority": 2,
                "match_when": "已有材料筛选、选型、性质比较或商用 FDM/FFF 耗材计算；可含牌号/标准，也可只给材料类别、工况和性能条件。",
                "include_keywords": ["材料筛选与计算", "材料筛选", "材料选型", "候选材料", "性质对比", "商用耗材", "丝材", "FDM", "FFF", "PLA", "PETG", "ASA", "ABS", "PC", "PA", "PEEK", "商品名", "牌号", "标准号", "UNS", "AMS", "MIL", "ASTM", "GB/T", "Inconel", "TIMETAL", "316L"],
                "exclude_keywords": ["严格化学式", "高熵合金", "HEA", "MPEA", "合金配比", "元素比例", "成分优化", "成分空间"],
                "route_after": ["alloy_composition_optimization"],
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
        upstream_preview = re.sub(r"\s+", " ", constraints["upstream_context"])[:600]
        print(
            f"[WS /mature-material/start] upstream received taskid={constraints['taskid']} "
            f"keys={constraints['upstream_context_keys']} user={str(payload.get('user_name') or '-')[:80]!r} "
            f"files={len(payload.get('file_metadata') or [])} context_chars={len(constraints['upstream_context'])} "
            f"preview={upstream_preview!r}"
        )
        _run_log(
            constraints["taskid"],
            "accepted catalog query",
            material_queries=constraints["material_queries"],
            material_families=constraints["material_families"],
            standards=constraints["standards"],
            property_constraints=len(constraints["property_constraints"]),
            service_temperature_K=constraints["service_temperature_K"],
            top_k=constraints["top_k"],
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
            eligible=(sum(bool(item.get("eligible")) for item in candidates) if constraints["property_constraints"] else "not_evaluated"),
            name_resolution=resolution_counts,
            recommendation=bool(result.get("recommendation")),
            llm_fallback=bool(result.get("llm_fallback")),
            candidate_names=candidate_names,
        )
        assets = _render_assets(result)
        _run_log(result["taskid"], "presentation prepared", assets=[item["name"] for item in assets])
        result["presentation"] = {"summary_markdown": _summary(result), "assets": assets}
        _save(result)
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>")
        await stream_markdown_rows(websocket, resolution_markdown(result))
        await websocket.send_text(f"<<<CONTENT_END:{FRONTEND_STEP_ID}>>>")
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>")
        await stream_authoritative_markdown(websocket, comparison_markdown(result), section="catalogue_result")
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
                    await websocket.send_json({"step_id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "name": item["name"], "docs": item["description"], "url": item["url"], "type": item["type"]})
                    await websocket.send_text(f"\n![{item['title']}]({item['url']})\n")
            except Exception as exc:
                logger.exception("[mature-assets] publishing failed taskid=%s error=%s", result["taskid"], exc)
        await websocket.send_text("\n### 3. 本轮建议\n\n")
        narration = await stream_customer_conclusion(websocket, result)
        result["presentation"]["customer_conclusion"] = narration
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
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "type": "error", "data": {"message": str(exc), "hint": "请发送 JSON 请求；可使用 mature_material.material_queries、material_families 和 property_constraints。"}})
    except WebSocketDisconnect:
        print(f"[WS /mature-material/start] peer={peer} disconnected during response")
        return
    except Exception as exc:
        print(f"[WS /mature-material/start] failed: {exc!r}")
        taskid = locals().get("constraints", {}).get("taskid", "unknown")
        _run_log(str(taskid), "failed", error=repr(exc))
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "type": "error", "data": {"message": "catalog query failed", "detail": str(exc)}})
    finally:
        await websocket.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
