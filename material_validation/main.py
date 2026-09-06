"""HTTP/WebSocket transport for refractory multiscale validation."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.application.request_normalization import normalize_request
from src.presentation.report import final_report, method_definition
from src.presentation.streaming import stream_authoritative_markdown
from src.presentation.assets import render_assets
from src.presentation.diagnostics import write_evidence_audit
from src.presentation.public_assets import publish_png_assets
from src.team_config import FRONTEND_STEP_ID, ROLE_NAME, SERVICE_ID, execute_refractory_validation

SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT / ".env")
RESULTS = SERVICE_ROOT / "results"
app = FastAPI(title="Refractory Multiscale Validation Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _persist_presentation(result: dict) -> None:
    """Persist URLs after local or public asset preparation."""
    destination = RESULTS / result["taskid"]
    (destination / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    presentation = destination / "presentation"
    (presentation / "presentation_manifest.json").write_text(
        json.dumps({"taskid": result["taskid"], "assets": result["presentation"]["assets"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save(result: dict) -> None:
    destination = RESULTS / result["taskid"]
    destination.mkdir(parents=True, exist_ok=True)
    assets = render_assets(result, RESULTS)
    audit_path = write_evidence_audit(result, RESULTS)
    result["presentation"] = {
        "summary_markdown": f"/refractory-validation/tasks/{result['taskid']}/assets/summary.md",
        "assets": assets,
    }
    result["diagnostics"] = {"evidence_audit_path": str(audit_path)}
    _persist_presentation(result)


def _write_summary(validated, result: dict) -> str:
    """Persist the complete customer report (method stream + result stream)."""
    assets = result.get("presentation", {}).get("assets", [])
    report = final_report(validated, assets)
    path = RESULTS / result["taskid"] / "presentation" / "summary.md"
    path.write_text(method_definition(validated, assets) + "\n\n" + report + "\n", encoding="utf-8")
    return report


async def _public_assets(websocket: WebSocket | None, result: dict) -> list[dict]:
    """Make task assets browser-reachable, following the 1111 fallback contract."""
    local_assets = result.get("presentation", {}).get("assets", [])
    try:
        published = await publish_png_assets(result["taskid"], local_assets)
    except Exception:
        published = {}
    configured = os.getenv("REFRACTORY_ASSET_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        base_url = configured
    else:
        raw = str(getattr(websocket, "url", ""))
        if raw.startswith("wss://"):
            base_url = "https://" + raw[6:].split("/", 1)[0]
        elif raw.startswith("ws://"):
            base_url = "http://" + raw[5:].split("/", 1)[0]
        else:
            base_url = ""
    public = []
    for item in local_assets:
        copy = dict(item)
        if item["name"] in published:
            copy["url"] = published[item["name"]]
        elif base_url:
            copy["url"] = f"{base_url}{item['url']}"
        public.append(copy)
    return public


def _run(payload: dict, *, publish_assets: bool = True) -> dict:
    validated = execute_refractory_validation(payload, SERVICE_ROOT)
    result = validated.to_dict()
    _save(result)
    if publish_assets:
        result["presentation"]["assets"] = asyncio.run(_public_assets(None, result))
        _persist_presentation(result)
    _write_summary(validated, result)
    return result


@app.get("/")
@app.get("/health")
def health():
    return {"service": SERVICE_ID, "status": "ok", "execution_enabled": os.getenv("ENABLE_EXECUTION", "false").lower() == "true"}


@app.get("/roles")
def roles():
    return {ROLE_NAME: {"name": ROLE_NAME, "role_id": f"{SERVICE_ID}_v1", "addresses": ["src.team_config.execute_refractory_validation"], "__module_class_name": "src.team_config.RefractoryMultiscaleValidationRole", "routing": {"service_id": SERVICE_ID, "match_when": "对 W 或已验证难熔金属材料执行 DFT、MLIP、MD 和实验/文献的跨尺度性能验证。"}}}


@app.post("/refractory-validation/requirements/preview")
def preview(payload: dict = Body(...)):
    try:
        return normalize_request(payload).to_dict()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/refractory-validation/evaluate")
def evaluate(payload: dict = Body(...)):
    try:
        return _run(payload)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/refractory-validation/tasks/{taskid}")
def task(taskid: str):
    path = RESULTS / taskid / "manifest.json"
    if not path.is_file():
        raise HTTPException(404, "task manifest not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/refractory-validation/tasks/{taskid}/assets/{asset_name}")
def asset(taskid: str, asset_name: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", taskid) or Path(asset_name).name != asset_name:
        raise HTTPException(422, "invalid asset path")
    path = RESULTS / taskid / "presentation" / asset_name
    if not path.is_file():
        raise HTTPException(404, "asset not found")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.websocket("/start")
@app.websocket("/refractory-validation/start")
async def start(websocket: WebSocket):
    await websocket.accept()
    try:
        payload = await websocket.receive_json()
        request = normalize_request(payload)
        await websocket.send_text("[start]")
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE_ID, "request_id": request.taskid, "type": "progress", "data": {"id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": "难熔金属跨尺度性能计算与验证", "status": "in_progress", "description": "正在核验 DFT、MLIP、MD 和实验/文献证据链。"}})
        validated = await asyncio.to_thread(execute_refractory_validation, payload, SERVICE_ROOT)
        result = validated.to_dict()
        _save(result)
        # Absolute URLs are needed when Markdown is rendered by the platform,
        # whose page origin can differ from this service's WebSocket origin.
        visual_assets = await _public_assets(websocket, result)
        result["presentation"]["assets"] = visual_assets
        _persist_presentation(result)
        _write_summary(validated, result)
        await stream_authoritative_markdown(websocket, method_definition(validated, visual_assets), step_id=FRONTEND_STEP_ID)
        await stream_authoritative_markdown(websocket, final_report(validated, visual_assets), step_id=FRONTEND_STEP_ID)
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE_ID, "request_id": request.taskid, "type": "result", "data": result})
        await websocket.send_text("[end]")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE_ID, "type": "error", "data": {"message": str(exc)}})
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "1116")))
