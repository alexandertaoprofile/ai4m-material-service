"""合金成分优化的 HTTP/WebSocket 传输入口。

高熵合金/多主元合金（HEA/MPEA）数值计算委托给隔离 runner。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from src.alloy_workflow.presentation import emit_result_content
from src.alloy_workflow.contracts import requirement_plan as _requirement_plan, task_id as _taskid, upstream_requirement as _upstream_requirement
from src.alloy_workflow.identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE, SERVICE_ID
from src.alloy_workflow.protocol import emit_public_asset_events, prepare_public_assets
from src.alloy_workflow.runtime import RUNTIME

load_dotenv()
SERVICE = "alloy-composition-optimization"
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "合金成分优化与候选初筛"
RESULTS = RUNTIME.results_root
RUNNER = RUNTIME.runner
APPLICATION = RUNTIME.application
app = FastAPI(title="Alloy Composition Optimization Service", version="0.2.0")
logger = logging.getLogger("alloy.service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def _template_label(template: str) -> str:
    return {
        "aerospace_high_temperature_hea_exploration": "航空发动机高温高熵/多主元合金探索模板",
        "generic_hea_exploration": "通用高熵/多主元合金探索模板",
    }.get(template, template)


def _runner_ready() -> bool:
    return RUNNER.ready()


def _proposal(payload: dict) -> dict:
    effective, plan = _requirement_plan(payload)
    taskid = _taskid(payload)
    proposal_start = f"[ALLOY][{taskid}] proposal start template={plan['template']!r} elements={effective.get('allowed_elements')} temperature_C={effective.get('test_temperature_C')}"
    print(proposal_start, flush=True)
    logger.info(proposal_start)
    result = RUNTIME.propose(payload)
    proposal_done = f"[ALLOY][{taskid}] proposal complete generated={result.get('sampling', {}).get('generated', 0)} feasible={result.get('sampling', {}).get('feasible', 0)} assets={[item['name'] for item in result['presentation']['assets']]} elapsed_seconds={result['elapsed_seconds']}"
    print(proposal_done, flush=True)
    logger.info(proposal_done)
    return result


@app.get("/")
def root(): return {"service":SERVICE,"status":"ok","service_python":"ai4m-service-py310 required","hea_runner_ready":_runner_ready()}


@app.get("/roles")
def roles():
    """Compatibility discovery endpoint used by the shared service gateway.

    Keep this metadata-only: instantiating Alpha's generic ``Team`` here would
    create its default OpenAI client merely to list a role, which is unrelated
    to this service's SeLLM-compatible presentation path.
    """
    profile = ROLE_PROFILE
    return {
        profile: {
            "name": ROLE_NAME,
            "profile": profile,
            "goal": "",
            "constraints": "",
            "desc": "",
            "is_human": False,
            "role_id": f"{SERVICE_ID}_v1",
            "states": ["0. Coding"],
            "actions": [{
                "name": ACTION_NAME,
                "i_context": "",
                "prefix": f"You are a {profile}, named {ROLE_NAME}, your goal is . ",
                "desc": ACTION_DESCRIPTION,
                "__module_class_name": "src.team_config.Coding",
            }],
            "rc": {
                "memory": {"storage": [], "index": {}, "ignore_id": False},
                "working_memory": {"storage": [], "index": {}, "ignore_id": False},
                "state": -1,
                "watch": ["alpha.actions.add_requirement.UserRequirement"],
                "react_mode": "react",
                "max_react_loop": 1,
            },
            "addresses": ["src.team_config.AlloyCompositionOptimizationRole", ROLE_NAME],
            "planner": {
                "plan": {"goal": "", "context": "", "tasks": [], "task_map": {}, "current_task_id": ""},
                "working_memory": {"storage": [], "index": {}, "ignore_id": False},
                "auto_run": False,
                "use_tools": False,
            },
            "routing": {
                "service_id": SERVICE_ID,
                "priority": 1,
                "match_when": "请求明确涉及高熵合金/多主元合金（HEA/MPEA）或高温合金，并且要求配比、元素比例、成分空间或优化。",
                "include_keywords": ["高熵合金", "多主元合金", "HEA", "MPEA", "元素比例", "原子百分比", "成分优化", "高温合金配比"],
                "exclude_keywords": ["已有材料查询", "商品材料", "牌号查询", "材料选型", "材料筛选", "FDM", "FFF", "丝材", "商用耗材", "明确化学式的新材料生成"],
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.AlloyCompositionOptimizationRole",
        }
    }

@app.get("/health")
def health(): return {"status":"ok","hea_runner_ready":_runner_ready(),"runner_prefix":str(RUNNER.environment_prefix)}
@app.post("/alloy/requirements/preview")
def requirement_preview(payload:dict=Body(...)): return _requirement_plan(payload)[1]
@app.post("/alloy/propose-space")
def propose(payload:dict=Body(...)):
    try:return _proposal(payload)
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate")
def evaluate(payload:dict=Body(...)):
    try:
        result, constraints = APPLICATION.evaluate(payload); manifest={"taskid":constraints["taskid"],"status":"completed","service":SERVICE,"result":result}; RUNTIME.save(manifest); return manifest
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate-batch")
def evaluate_batch(payload:dict=Body(...)):
    try:
        candidates=payload.get("candidates") or []; result, constraints = APPLICATION.evaluate(payload, candidates); return {"taskid":constraints["taskid"],"status":"completed","service":SERVICE,**result}
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.get("/alloy/tasks/{taskid}")
def task(taskid:str):
    path=RESULTS/taskid/"manifest.json"
    if not path.is_file(): raise HTTPException(404,"task manifest not found")
    return json.loads(path.read_text(encoding="utf-8"))
@app.get("/alloy/tasks/{taskid}/assets/{asset_name}")
def asset(taskid:str,asset_name:str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",taskid) or Path(asset_name).name!=asset_name: raise HTTPException(422,"invalid asset path")
    path=RESULTS/taskid/"presentation"/asset_name
    if not path.is_file(): raise HTTPException(404,"asset not found")
    return FileResponse(path,filename=path.name,content_disposition_type="inline")


@app.websocket("/start")
@app.websocket("/alloy/start")
async def start(websocket:WebSocket):
    await websocket.accept()
    peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    print(f"[WS /alloy/start] accepted peer={peer}; waiting for initial JSON", flush=True)
    try:
        # Receive the raw ASGI event first so a client that opens then closes
        # before sending its request is visible in logs instead of becoming a
        # silent WebSocketDisconnect from receive_json().
        event = await websocket.receive()
        if event["type"] == "websocket.disconnect":
            print(
                f"[WS /alloy/start] peer={peer} disconnected before initial JSON "
                f"(code={event.get('code')})",
                flush=True,
            )
            return
        raw_payload = event.get("text")
        if raw_payload is None and event.get("bytes") is not None:
            raw_payload = event["bytes"].decode("utf-8")
        if raw_payload is None:
            raise ValueError("initial WebSocket message must be JSON text")
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("initial WebSocket JSON must be an object")
        context, context_keys = _upstream_requirement(payload)
        context_preview = re.sub(r"\s+", " ", context)[:600]
        # Use stdout as well as the named logger: tmux deployments normally
        # show Uvicorn's logger but may not attach handlers to child loggers.
        print(f"[WS /alloy/start] preflight taskid={_taskid(payload)} keys={context_keys} context_chars={len(context)} preview={context_preview!r}", flush=True)
        logger.info("WS preflight taskid=%s keys=%s context_chars=%s preview=%r", _taskid(payload), context_keys, len(context), context_preview)
        effective,plan=_requirement_plan(payload); taskid=_taskid(payload)
        print(f"[WS /alloy/start] upstream received taskid={taskid} peer={peer} keys={context_keys} context_chars={len(context)} preview={context_preview!r}", flush=True)
        print(f"[ALLOY][{taskid}] accepted template={plan.get('template')!r} domain={effective.get('model_domain')!r}", flush=True)
        await websocket.send_text("[start]")
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"completed","description":"已生成可覆盖的探索模板和待确认项。","result":plan}})
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>\n### 合金设计需求解读\n- 探索方案：{_template_label(plan['template'])}\n- 适用对象：高熵合金/多主元合金。\n- 待确认：{'；'.join(plan['questions_to_confirm'])}\n<<<CONTENT_END:{FRONTEND_STEP_ID}>>>")
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"in_progress","description":"正在通过隔离的高熵/多主元合金（HEA/MPEA）专项 runner 进行采样和批量预测。"}})
        result=await asyncio.to_thread(_proposal,payload); result["_summary_path"]=RESULTS/taskid/"presentation"/"summary.md"
        public_urls,asset_docs,_asset_titles,visual_assets=await prepare_public_assets(websocket,taskid,result,RESULTS)
        # Follow the neighboring 3D-material service: public image URLs are
        # embedded in the streamed Markdown *and* announced as asset events.
        # The first path works in chat renderers that do not render asset cards.
        await emit_result_content(websocket,result,step_id=FRONTEND_STEP_ID,visual_assets=visual_assets)
        result.pop("_summary_path",None)
        await emit_public_asset_events(websocket,result,public_urls,asset_docs)
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"result","data":result})
        await websocket.send_text("[end]")
        print(f"[ALLOY][{taskid}] completed generated={result.get('sampling', {}).get('generated', 0)} feasible={result.get('sampling', {}).get('feasible', 0)} assets={len(result.get('presentation', {}).get('assets', []))}", flush=True)
    except WebSocketDisconnect as exc:
        print(f"[WS /alloy/start] peer={peer} disconnected during response (code={exc.code})", flush=True)
    except Exception as exc:
        print(f"[WS /alloy/start] failed peer={peer} error={exc!r}", flush=True)
        try:
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","type":"error","data":str(exc)})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

if __name__=="__main__": uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","1111")))
