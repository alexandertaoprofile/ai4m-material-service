"""Mature commodity-material service, using the inorganic_new_material envelope."""
from __future__ import annotations

import json
import os
import re
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from src.catalog.query import MatureMaterialCatalog, parse_property_constraints
from src.catalog.presentation import (
    comparison_markdown,
    conclusion_markdown,
    render_property_comparison,
    resolution_markdown,
)
from src.catalog.assets import publish_png_assets
from src.catalog.narration import (
    generate_llm_material_fallback,
    recommend_catalog_material_ids,
    stream_authoritative_markdown,
    stream_customer_conclusion,
    stream_markdown_rows,
)

load_dotenv()
SERVICE = "mature-material"
PORT = int(os.getenv("PORT", "1105"))
RESULTS = Path(os.getenv("MATURE_MATERIAL_RESULTS_ROOT", "results/mature_material"))
RAW_DATA_ROOT = Path(os.getenv("PROPERTY_DATA_ROOT", "/data/se42/backend/property datasets"))
CATALOG_ROOT = Path(os.getenv("MATURE_MATERIAL_CATALOG_ROOT", "data/processed"))
app = FastAPI(title="Mature Material Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
logger = logging.getLogger("mature_material")


def _taskid(payload: dict[str, Any]) -> str:
    taskid = str(payload.get("taskid") or f"mature-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
    # The shared gateway supplies opaque task IDs.  They can contain colons,
    # spaces or Chinese text, so rejecting them prevents an otherwise valid
    # material query.  Path safety is handled separately by _task_storage_key.
    if not taskid.strip() or len(taskid) > 512:
        raise ValueError("taskid must be a non-empty string no longer than 512 characters")
    return taskid


def _task_storage_key(taskid: str) -> str:
    """Map an opaque external task ID to a safe, stable result directory."""
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", taskid):
        return taskid
    return "opaque-" + hashlib.sha256(taskid.encode("utf-8")).hexdigest()[:32]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str): return [value]
    return [str(item) for item in value] if isinstance(value, list) else []


def _context_text(value: Any, *, limit: int = 12000) -> str:
    """Extract meaningful text from the gateway's idea/history envelope."""
    fragments: list[str] = []

    def visit(item: Any) -> None:
        if len("\n".join(fragments)) >= limit:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                # Some gateways embed a previous turn as JSON inside idea.
                try:
                    decoded = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    fragments.append(text)
                else:
                    visit(decoded)
        elif isinstance(item, dict):
            for key in ("idea", "content", "text", "query", "summary", "message", "requirement"):
                if item.get(key) is not None:
                    visit(item[key])
            # Chat histories frequently carry role/content pairs.
            for key in ("messages", "history", "conversation", "upstream_context", "previous_results"):
                if item.get(key) is not None:
                    visit(item[key])
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return "\n\n".join(fragments)[:limit]


def _upstream_context(payload: dict[str, Any]) -> tuple[str, list[str]]:
    keys = [key for key in ("idea", "content", "query", "history", "messages", "conversation", "upstream_context", "previous_results") if payload.get(key) is not None]
    return _context_text({key: payload[key] for key in keys}), keys


_EXECUTION_MARKER = re.compile(
    r"(?:接下来(?:需要)?进行执行的任务|接下来执行的任务|当前(?:需要)?执行任务|执行任务)\s*[：:]\s*",
    flags=re.IGNORECASE,
)
_NON_MATERIAL_TOKENS = frozenset({
    "MNS", "XIMUALPHA", "LLM", "RAG", "PDF", "CIF", "MP", "HFE", "HTCC", "CTE", "IPC",
})
_MATERIAL_ACRONYMS = frozenset({"ABS", "ASA", "PA", "PEEK", "PEI", "PETG", "PLA", "PPS", "PTFE", "PVC"})


def _material_extraction_text(text: str) -> str:
    """Use the final orchestration instruction, not acronyms in RAG prose."""
    matches = list(_EXECUTION_MARKER.finditer(text or ""))
    return (text or "")[matches[-1].end():].strip() if matches else (text or "").strip()


def _formula_like_terms(text: str) -> list[str]:
    """Find formula-like candidate names so unknown upstream materials are explicit.

    The upstream summary contains system/team identifiers as well as material
    names.  Do not present those identifiers as customer-supplied materials.
    """
    terms = re.findall(
        r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])",
        _material_extraction_text(text),
    )
    return list(dict.fromkeys(
        term for term in terms
        if len(term) >= 3
        and term.upper() not in _NON_MATERIAL_TOKENS
        and (term.upper() in _MATERIAL_ACRONYMS or any(char.isdigit() for char in term) or any(char.islower() for char in term))
    ))


def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    taskid = _taskid(payload)
    scope = payload.get("mature_material") or payload.get("constraints") or {}
    if not isinstance(scope, dict): raise ValueError("mature_material must be an object")
    temperature_c = scope.get("service_temperature_C", scope.get("temperature_C"))
    try:
        default_temperature_K = float(temperature_c) + 273.15 if temperature_c is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature_C must be numeric") from exc
    properties = scope.get("property_constraints", scope.get("property_filters", {}))
    upstream_context, upstream_keys = _upstream_context(payload)
    raw_requirement = str(scope.get("query") or payload.get("idea") or upstream_context)
    return {
        "taskid": taskid,
        "raw_requirement": raw_requirement,
        "upstream_context": upstream_context,
        "upstream_context_keys": upstream_keys,
        "material_queries": _as_list(scope.get("material_queries", scope.get("materials", scope.get("names", [])))),
        "material_families": _as_list(scope.get("material_families", scope.get("families", []))),
        "standards": _as_list(scope.get("standards", [])),
        "property_constraints": [item.__dict__ for item in parse_property_constraints(properties, default_temperature_K)],
        "service_temperature_K": default_temperature_K,
        "top_k": max(1, min(int(scope.get("top_k", 10)), 50)),
        "source_preference": str(scope.get("source_preference", "all")),
    }


async def _manifest(constraints: dict[str, Any]) -> dict[str, Any]:
    """Run only against the traceable structured catalogue, never raw PDFs."""
    catalog = MatureMaterialCatalog(CATALOG_ROOT)
    if not catalog.ready:
        return {"taskid": constraints["taskid"], "status": "accepted_pending_catalog_ingestion", "service": SERVICE, "created_at": datetime.now(timezone.utc).isoformat(), "constraints": constraints, "results": [], "data_status": {"catalog_ready": False, "raw_data_root_available": RAW_DATA_ROOT.exists(), "message": "Structured catalogue is unavailable; raw PDFs were not queried."}}
    # Rebuild the dataclasses through the public parser so the query path and
    # preview path share exactly the same validation rules.
    parsed_constraints = parse_property_constraints(constraints["property_constraints"], constraints["service_temperature_K"])
    names = constraints["material_queries"] or catalog.aliases_mentioned_in(constraints["raw_requirement"])
    # Formula-like upstream candidates (for example Na3SbS4) are also treated
    # as requested names. If they are absent from the catalogue, search() must
    # return no candidates rather than unrelated catalogue rows.
    if not names:
        names = _formula_like_terms(constraints["raw_requirement"])
    # With no concrete material identifier, family, standard or property
    # condition, a normal catalogue query must not become a full catalogue
    # listing.  The constrained LLM fallback below gets the first chance to
    # choose a small relevant subset, or truthfully returns none.
    if names or constraints["material_families"] or constraints["standards"] or parsed_constraints:
        search = catalog.search(
            names=names, families=constraints["material_families"], standards=constraints["standards"],
            constraints=parsed_constraints, top_k=constraints["top_k"],
        )
    else:
        search = {"name_resolution": [], "candidates": []}
    recommendation: dict[str, Any] | None = None
    llm_fallback: dict[str, Any] | None = None
    # A failed exact lookup must not fall back to an unfiltered list. The LLM
    # may only select a small, explicitly labelled set from this catalogue.
    if not search["candidates"]:
        selected_ids = await recommend_catalog_material_ids(
            constraints.get("upstream_context") or constraints.get("raw_requirement") or "",
            catalog.materials,
            max_items=min(3, constraints["top_k"]),
        )
        if selected_ids:
            selected_names = [catalog._by_id[material_id]["display_name"] for material_id in selected_ids]
            fallback = catalog.search(
                names=selected_names, families=[], standards=[], constraints=parsed_constraints,
                top_k=constraints["top_k"],
            )
            if fallback["candidates"]:
                search["candidates"] = fallback["candidates"]
                recommendation = {
                    "mode": "catalog_llm_fallback",
                    "material_ids": selected_ids,
                    "message": "未找到名称的精确匹配；以下为模型仅从当前已入库目录选出的后续核验参考材料，并非名称匹配结果。",
                }
    # If no catalogue data exists even after the restricted catalogue-only
    # recommender, offer an explicitly non-verified LLM advisory.  This does
    # not write data, does not call the web, and must never become a candidate.
    if not search["candidates"]:
        llm_fallback = await generate_llm_material_fallback(
            constraints.get("upstream_context") or constraints.get("raw_requirement") or ""
        )
    eligible = sum(item["eligible"] for item in search["candidates"])
    if recommendation:
        message = recommendation["message"]
    elif llm_fallback:
        message = llm_fallback["message"]
    elif not search["candidates"]:
        message = "目录中暂未找到与本轮指定材料、牌号或标准相匹配的已入库记录；未展示无关候选材料。"
    else:
        message = f"已在结构化材料目录中评估 {len(search['candidates'])} 种候选，其中 {eligible} 种满足当前可比较的性质条件。"
    return {"taskid": constraints["taskid"], "status": "completed", "service": SERVICE, "created_at": datetime.now(timezone.utc).isoformat(), "constraints": constraints, "results": search["candidates"], "name_resolution": search["name_resolution"], "recommendation": recommendation, "llm_fallback": llm_fallback, "data_status": {"catalog_ready": True, "raw_data_root_available": RAW_DATA_ROOT.exists(), "message": message, "scope": "仅查询已清洗的结构化目录数据；Markdown 解析数据将按来源和表格逐步入库。"}}


def _summary(result: dict[str, Any]) -> str:
    return "\n\n".join([resolution_markdown(result), comparison_markdown(result), conclusion_markdown(result)])


def _save(manifest: dict[str, Any]) -> None:
    path = RESULTS / _task_storage_key(manifest["taskid"]); path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_assets(result: dict[str, Any]) -> list[dict[str, str]]:
    """Produce only factual charts from catalogue values, never synthetic images."""
    presentation_dir = RESULTS / _task_storage_key(result["taskid"]) / "presentation"
    chart = render_property_comparison(result, presentation_dir)
    if not chart:
        return []
    has_property_constraint = bool(result.get("constraints", {}).get("property_constraints"))
    return [{
        "name": "property_comparison" if has_property_constraint else "catalog_coverage",
        "title": "候选材料性质对比" if has_property_constraint else "候选材料数据覆盖度",
        "description": "柱状图仅比较本轮有相同性质、单位和可比温度证据的候选。" if has_property_constraint else "未指定性质条件时，图表展示每个候选已有的可追溯性质种类数。",
        "local_path": str(chart),
        "url": "",
        "type": "MaterialsPNG",
    }]


@app.get("/")
def root(): return {"service": SERVICE, "status": "ok", "port": PORT, "contract": "mature_material v1"}


@app.get("/roles")
def roles():
    """Gateway discovery metadata, without importing the legacy Alpha Team."""
    profile = (
        "子流程：已有/商品/成熟材料数据库检索与性质核验（mature_material_catalog）。"
        "只处理已存在材料的查询、牌号/标准核对、材料选型、热物性或机械性质对比。"
        "根据材料名称、牌号、标准、材料族、服役温度和性质条件，在已清洗且可追溯的材料目录中返回候选、性质证据及来源。"
        "触发前提：上游给出明确的商品名、牌号、标准号或供应商材料型号，例如 Inconel 718、UNS N07718、316L、AMS/MIL/ASTM/GB 标准。"
        "仅有化学式应进入新材料生成服务；高熵合金/HEA/MPEA 的成分设计、元素比例优化、配方生成和成分空间搜索应进入合金配比优化服务。"
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
            "addresses": ["src.team_config.XIMUAlpha_MNS", "已有材料检索与性质核验"],
            "planner": {"plan": {"goal": "", "context": "", "tasks": [], "task_map": {}, "current_task_id": ""}, "working_memory": {"storage": [], "index": {}, "ignore_id": False}, "auto_run": False, "use_tools": False},
            "routing": {
                "service_id": "mature_material_catalog",
                "priority": 3,
                "match_when": "请求含明确商品名、牌号、标准号或供应商型号；性质词必须附随该已有材料标识。",
                "include_keywords": ["商品名", "牌号", "标准号", "UNS", "AMS", "MIL", "ASTM", "GB/T", "Inconel", "TIMETAL", "316L"],
                "exclude_keywords": ["严格化学式", "高熵合金", "HEA", "MPEA", "合金配比", "元素比例", "成分优化", "成分空间"],
                "route_after": ["alloy_composition_optimization", "inorganic_new_material_generation"],
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.XIMUAlpha_MNS",
        }
    }


@app.get("/health")
def health(): return {"status": "ok", "catalog_ready": (CATALOG_ROOT / "materials.csv").is_file(), "raw_data_root_available": RAW_DATA_ROOT.exists()}


@app.post("/mature-material/constraints")
def preview(payload: dict = Body(...)):
    try: return _contract(payload)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.post("/mature-material/query")
async def query(payload: dict = Body(...)):
    try: result = await _manifest(_contract(payload)); _save(result); return result
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc


@app.get("/mature-material/tasks/{taskid}")
def task(taskid: str):
    path = RESULTS / _task_storage_key(taskid) / "manifest.json"
    if not path.is_file(): raise HTTPException(404, "task manifest not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/mature-material/tasks/{taskid}/assets/{asset_name}")
def asset(taskid: str, asset_name: str):
    if Path(asset_name).name != asset_name:
        raise HTTPException(422, "invalid asset path")
    path = RESULTS / _task_storage_key(taskid) / "presentation" / asset_name
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
        # Receive the ASGI event directly so a disconnect before the first
        # request is visible in the service log.  ``receive_json`` turns that
        # very useful distinction into a silent WebSocketDisconnect.
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
        print(f"[WS /mature-material/start] received taskid={constraints['taskid']} peer={peer}; querying catalog")
        # The existing service gateway uses these text markers to delimit one
        # subservice run.  Keep them even though this service also emits typed
        # progress/result JSON for newer clients.
        await websocket.send_text("[start]")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": constraints["taskid"], "type": "progress", "data": {"id": "MATURE_CATALOG_QUERY", "title": "材料目录检索与性质核验", "status": "in_progress", "description": "正在规范化别名、核验材料状态和温度条件，并读取可追溯性质证据。"}})
        result = await _manifest(constraints); _save(result)
        assets = _render_assets(result)
        result["presentation"] = {"summary_markdown": _summary(result), "assets": assets}
        _save(result)
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": result["taskid"], "type": "progress", "data": {"id": "MATURE_NAME_RESOLUTION", "title": "名称与牌号核验", "status": "completed", "description": "已完成别名、牌号和标准号的精确匹配；歧义名称不会自动合并。"}})
        await websocket.send_text("<<<CONTENT_START:MATURE_NAME_RESOLUTION>>>")
        await stream_markdown_rows(websocket, resolution_markdown(result))
        await websocket.send_text("<<<CONTENT_END:MATURE_NAME_RESOLUTION>>>")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": result["taskid"], "type": "progress", "data": {"id": "MATURE_CATALOG_QUERY", "title": "材料目录检索与性质核验", "status": "completed", "description": result["data_status"]["message"]}})
        await websocket.send_text("<<<CONTENT_START:MATURE_CATALOG_QUERY>>>")
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
                    await websocket.send_json({"step_id": "MATURE_CATALOG_QUERY", "name": item["name"], "docs": item["description"], "url": item["url"], "type": item["type"]})
                    await websocket.send_text(f"\n![{item['title']}]({item['url']})\n")
            except Exception as exc:
                logger.exception("[mature-assets] publishing failed taskid=%s error=%s", result["taskid"], exc)
                await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": result["taskid"], "type": "progress", "data": {"id": "MATURE_ASSETS", "title": "图表发布", "status": "failed", "description": "图表已生成，但暂时无法发布；不影响材料检索结果。"}})
        await websocket.send_text("\n### 3. 本轮建议\n\n")
        narration = await stream_customer_conclusion(websocket, result)
        result["presentation"]["customer_conclusion"] = narration
        result["presentation"]["assets"] = [item for item in assets if item.get("url")]
        _save(result)
        await websocket.send_text("<<<CONTENT_END:MATURE_CATALOG_QUERY>>>")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "request_id": result["taskid"], "type": "result", "data": result})
        await websocket.send_text("[end]")
        print(f"[WS /mature-material/start] completed taskid={result['taskid']} peer={peer}")
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[WS /mature-material/start] invalid initial message from peer={peer}: {exc}")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "type": "error", "data": {"message": str(exc), "hint": "请发送 JSON 请求；可使用 mature_material.material_queries、material_families 和 property_constraints。"}})
    except WebSocketDisconnect:
        print(f"[WS /mature-material/start] peer={peer} disconnected during response")
        return
    except Exception as exc:
        # Keep the server log useful when a gateway closes after an internal
        # failure, while returning a structured error to compatible clients.
        print(f"[WS /mature-material/start] failed: {exc!r}")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE, "type": "error", "data": {"message": "catalog query failed", "detail": str(exc)}})
    finally: await websocket.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
