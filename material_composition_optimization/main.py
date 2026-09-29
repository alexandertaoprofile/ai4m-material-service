"""合金成分优化的 HTTP/WebSocket 传输入口。

高熵合金/多主元合金（HEA/MPEA）数值计算委托给隔离 runner。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from src.alloy_workflow.presentation import emit_result_content, hot_end_input_guide_block, planned_alloy_method_block, stream_authoritative_markdown
from src.alloy_workflow.contracts import MaterialContextError, requirement_plan as _requirement_plan, task_id as _taskid, upstream_requirement as _upstream_requirement
from src.alloy_workflow.fallback import generic_fallback_plan, generic_fallback_result
from src.alloy_workflow.identity import (
    ACTION_DESCRIPTION,
    ACTION_NAME,
    MODEL_CAPABILITIES,
    ROLE_NAME,
    ROLE_PROFILE,
    ROUTING_MATCH_WHEN,
    SERVICE_BOUNDARY,
    SERVICE_ID,
    role_model_routes,
)
from src.alloy_workflow.protocol import prepare_public_assets, result_event_data
from src.alloy_workflow.runtime import RUNTIME
from src.alloy_workflow.recipe_primary import mode_for, effective_mode, primary_description, primary_preflight

SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT / ".env")
SERVICE = "alloy-composition-optimization"
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "材料配方设计与性能筛选"
RESULTS = RUNTIME.results_root
RUNNER = RUNTIME.runner
APPLICATION = RUNTIME.application
_NATIVE_IDEA_LOG_LIMIT = 4_000
app = FastAPI(title="Material Composition Optimization Service", version="0.2.0")
logger = logging.getLogger("alloy.service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def _native_idea_trace(value: object, *, limit: int = _NATIVE_IDEA_LOG_LIMIT) -> tuple[str, int, str, bool]:
    """Log the received top-level ``idea`` itself, rather than a context preview."""
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    truncated = len(encoded) > limit
    return encoded[:limit], len(encoded), hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16], truncated


def _template_label(template: str) -> str:
    return {
        "alloy_domain_confirmation": "高温合金材料体系确认",
        "aerospace_high_temperature_hea_exploration": "航空发动机高温高熵/多主元合金探索模板",
        "generic_hea_exploration": "通用高熵/多主元合金探索模板",
        "hot_end_ni_superalloy_screening": "高温镍基合金成分设计与服役性能筛选模板",
        "reusable_rocket_stainless_screening": "可回收火箭不锈钢配方设计模板",
        "chip_glass_thermomechanical_local_screening": "芯片玻璃基板配方与热机械筛选模板",
        "short_cf_thermomechanical_rve_screening": "短碳纤维复合材料本构筛选模板",
        "perovskite_transport_stability_grid_screening": "高稳定性钙钛矿电输运配比初筛模板",
        "aerospace_aluminum_wt_percent_screening": "航空铝合金 wt.% 配方与室温性能筛选模板",
        "copper_hot_end_conditioned_local_composition_screening": "铜基再生冷却热壁局部配比筛选模板",
        "generic_composition_design_fallback": "材料家族特定的通用配比与试验设计框架",
    }.get(template, template)


def _runner_ready() -> dict[str, bool]:
    return {
        item["model_domain"]: RUNNER.ready(item["model_domain"])
        for item in MODEL_CAPABILITIES
    }


def _safe_requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return _requirement_plan(payload)
    except ValueError as exc:
        return generic_fallback_plan(payload, str(exc))


def _proposal(payload: dict) -> dict:
    effective, plan = _safe_requirement_plan(payload)
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
def root(): return {"service":SERVICE,"status":"ok","service_python":"ai4m-service-py310 required","runner_ready":_runner_ready()}


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
            "goal": "围绕性能目标开展受约束的材料配方与配比优化，给出已接入模型支持的候选及性质评价；其他材料给出配方变量与试验设计。",
            "constraints": SERVICE_BOUNDARY,
            "desc": ACTION_DESCRIPTION,
            "is_human": False,
            "role_id": f"{SERVICE_ID}_v1",
            "states": ["0. Coding"],
            "actions": [{
                "name": ACTION_NAME,
                "i_context": "",
                "prefix": (
                    f"You are a {profile}, named {ROLE_NAME}. Your goal is to produce "
                    "traceable, constrained material-composition candidates, model-supported "
                    "property screening, and validation priorities within the selected model domain."
                ),
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
                "match_when": ROUTING_MATCH_WHEN,
                "include_keywords": ["高熵合金", "多主元合金", "HEA", "MPEA", "高温合金", "镍基合金", "单晶镍基", "定向凝固", "蠕变", "Inconel", "CMSX", "Rene", "铜基热端", "GRCop", "NARloy", "CuCrZr", "再生冷却", "燃烧室内衬", "火箭不锈钢", "可回收火箭", "火箭贮箱", "承压壳体", "奥氏体不锈钢", "304L", "301LN", "30X", "航空铝合金", "铝锂", "Al-Li", "2024", "6061", "7050", "7075", "玻璃基板", "芯片玻璃", "封装玻璃", "低硼无碱", "铝硼硅酸盐", "氧化物 mol%", "短碳纤维", "短纤维增强热塑性", "复合耗材", "RVE", "E11", "正交各向异性", "钙钛矿", "perovskite", "卤化物钙钛矿", "Cs/FA/MA", "碘溴", "离子迁移", "电输运稳定", "合金配比", "合金成分", "元素比例", "原子百分比", "质量百分比", "wt.%", "成分优化", "配比优化", "成分空间", "配方优化", "配方设计", "配比设计", "组分优化", "组分设计", "调整组分比例", "调整配方", "优化配方"],
                "exclude_keywords": [],
                "model_routes": role_model_routes(),
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.AlloyCompositionOptimizationRole",
        }
    }

@app.get("/health")
def health(): return {"status":"ok","runner_ready":_runner_ready(),"runner_prefix":str(RUNNER.environment_prefix),"unified_tensile_shadow_ready":RUNNER.tensile_shadow_ready(),"recipe_release_modes":{item["model_domain"]:mode_for(item["model_domain"]) for item in MODEL_CAPABILITIES}}
@app.post("/alloy/requirements/preview")
def requirement_preview(payload:dict=Body(...)): return _safe_requirement_plan(payload)[1]
@app.post("/alloy/propose-space")
def propose(payload:dict=Body(...)):
    try:return _proposal(payload)
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate")
def evaluate(payload:dict=Body(...)):
    try:
        result, constraints = APPLICATION.evaluate(payload); manifest={"taskid":constraints["taskid"],"status":"completed","service":SERVICE,"result":result}; RUNTIME.save(manifest); return manifest
    except ValueError as exc:
        result, constraints = generic_fallback_result(payload, str(exc), SERVICE)
        manifest={"taskid":constraints["taskid"],"status":result.get("status", "completed_with_fallback"),"service":SERVICE,"result":result}; RUNTIME.save(manifest); return manifest
    except RuntimeError as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate-batch")
def evaluate_batch(payload:dict=Body(...)):
    try:
        candidates=payload.get("candidates") or []; result, constraints = APPLICATION.evaluate(payload, candidates); return {"taskid":constraints["taskid"],"status":"completed","service":SERVICE,**result}
    except ValueError as exc:
        result, constraints = generic_fallback_result(payload, str(exc), SERVICE)
        return {"taskid":constraints["taskid"],"status":"completed_with_fallback","service":SERVICE,**result}
    except RuntimeError as exc: raise HTTPException(422,str(exc)) from exc
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
        try:
            context, context_keys = _upstream_requirement(payload)
        except MaterialContextError as exc:
            context, context_keys = exc.request, exc.keys
        context_preview = re.sub(r"\s+", " ", context)[:600]
        # Use stdout as well as the named logger: tmux deployments normally
        # show Uvicorn's logger but may not attach handlers to child loggers.
        print(f"[WS /alloy/start] preflight taskid={_taskid(payload)} keys={context_keys} context_chars={len(context)} preview={context_preview!r}", flush=True)
        logger.info("WS preflight taskid=%s keys=%s context_chars=%s preview=%r", _taskid(payload), context_keys, len(context), context_preview)
        native_idea, native_idea_chars, native_idea_sha256, native_idea_truncated = _native_idea_trace(payload.get("idea"))
        print(
            f"[WS /alloy/start] native_idea type={type(payload.get('idea')).__name__} "
            f"json_chars={native_idea_chars} sha256_16={native_idea_sha256} "
            f"truncated={native_idea_truncated} value={native_idea}",
            flush=True,
        )
        effective,plan=_safe_requirement_plan(payload); taskid=_taskid(payload)
        print(f"[WS /alloy/start] upstream received taskid={taskid} peer={peer} keys={context_keys} context_chars={len(context)} preview={context_preview!r}", flush=True)
        print(f"[ALLOY][{taskid}] accepted template={plan.get('template')!r} domain={effective.get('model_domain')!r}", flush=True)
        await websocket.send_text("[start]")
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"completed","description":"已生成可覆盖的探索模板和待确认项。","result":plan}})
        await stream_authoritative_markdown(
            websocket,
            primary_preflight(effective.get("model_domain")) if effective_mode(effective) == "primary" else f"{planned_alloy_method_block(payload)}\n\n待确认：{'；'.join(plan['questions_to_confirm'])}",
            step_id=FRONTEND_STEP_ID,
        )
        if plan.get("requires_domain_confirmation"):
            waiting = {
                "taskid": taskid, "status": "waiting_for_input", "service": SERVICE,
                "model_domain": "routing_confirmation", "requirement_interpretation": plan,
                "user_conclusion": plan.get("confirmation_message") or "请确认采用高温镍基合金还是 HEA/MPEA 路线后开始配方筛选。",
            }
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"completed","description":plan.get("confirmation_message") or "已识别高温合金任务，等待确认材料体系。"}})
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"result","data":waiting})
            await websocket.send_text("[end]")
            return
        if effective.get("model_domain") == "ni_superalloy_hot_end" and plan.get("missing_required_inputs"):
            waiting = {
                "taskid": taskid, "status": "waiting_for_input", "service": SERVICE,
                "model_domain": "ni_superalloy_hot_end", "requirement_interpretation": plan,
                "user_conclusion": "已识别为高温镍基合金成分设计任务；补齐路线、热处理、温度、载荷和 wt.% 边界后即可开始条件筛选。",
            }
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"completed","description":"已识别高温镍基合金任务，等待补齐条件后开始计算。"}})
            await stream_authoritative_markdown(websocket, hot_end_input_guide_block(plan), step_id=FRONTEND_STEP_ID)
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"result","data":waiting})
            await websocket.send_text("[end]")
            return
        runner_description = "本次输入尚未匹配到可执行的预测模型，正在整理配方变量、工艺条件和首轮试验方案。" if effective.get("model_domain") == "generic_composition_design_fallback_v1" else "正在按航空铝合金 wt.% 成分与已记录工艺类别生成候选并预测名义室温强度—延伸率。" if effective.get("model_domain") == "aerospace_aluminum_composition_v1" else "正在通过钙钛矿专项 runner 在可追溯 Cs/FA/MA–Pb(I,Br)3 组分网格内进行变温电输运稳定性初筛。" if effective.get("model_domain") == "perovskite_transport_stability_v2" else "正在通过短碳纤维 RVE 专项 runner 生成正交各向异性线弹性本构候选。" if effective.get("model_domain") == "short_cf_thermomechanical_rve_v1" else "正在通过芯片玻璃基板专项 runner 在同家族氧化物邻域内生成候选并预测热机械性质。" if effective.get("model_domain") == "chip_glass_thermomechanical_family_v1" else "正在通过隔离的高温镍基合金专项 runner 进行受约束候选筛选与条件预测。" if effective.get("model_domain") == "ni_superalloy_hot_end" else "正在通过可回收火箭不锈钢专项 runner 进行候选筛选与短时拉伸预测。" if effective.get("model_domain") == "reusable_rocket_stainless" else "正在通过隔离的高熵/多主元合金（HEA/MPEA）专项 runner 进行采样和批量预测。"
        if effective_mode(effective) == "primary":
            runner_description = primary_description(effective.get("model_domain"))
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"in_progress","description":runner_description}})
        result=await asyncio.to_thread(_proposal,payload); result["_summary_path"]=RESULTS/taskid/"presentation"/"summary.md"
        public_urls,asset_docs,_asset_titles,visual_assets=await prepare_public_assets(websocket,taskid,result,RESULTS)
        # PNG 图表只嵌入流式 Markdown；不再额外发送 MaterialsPNG JSON，避免
        # 前端把同一资产渲染两次。
        await emit_result_content(websocket,result,step_id=FRONTEND_STEP_ID,visual_assets=visual_assets)
        result.pop("_summary_path",None)
        await websocket.send_json({
            "version": "1.0.0", "agent": "alloy_composition_optimization",
            "request_id": taskid, "type": "result", "data": result_event_data(result),
            "meta": {"step_id": FRONTEND_STEP_ID, "ui": {"visible": False}},
        })
        await websocket.send_text("[end]")
        print(f"[ALLOY][{taskid}] completed generated={result.get('sampling', {}).get('generated', 0)} feasible={result.get('sampling', {}).get('feasible', 0)} assets={len(result.get('presentation', {}).get('assets', []))}", flush=True)
    except WebSocketDisconnect as exc:
        print(f"[WS /alloy/start] peer={peer} disconnected during response (code={exc.code})", flush=True)
    except Exception as exc:
        print(f"[WS /alloy/start] failed peer={peer} error={exc!r}", flush=True)
        try:
            await websocket.send_text(f"\n处理失败：{exc}\n")
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

if __name__=="__main__": uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","1111")))
