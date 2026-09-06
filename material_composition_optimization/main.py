"""合金成分优化的 HTTP/WebSocket 传输入口。

高熵合金/多主元合金（HEA/MPEA）数值计算委托给隔离 runner。
"""
from __future__ import annotations

import asyncio
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
from src.alloy_workflow.contracts import requirement_plan as _requirement_plan, task_id as _taskid, upstream_requirement as _upstream_requirement
from src.alloy_workflow.identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE, SERVICE_BOUNDARY, SERVICE_ID
from src.alloy_workflow.protocol import prepare_public_assets
from src.alloy_workflow.runtime import RUNTIME

SERVICE_ROOT = Path(__file__).resolve().parent
load_dotenv(SERVICE_ROOT / ".env")
SERVICE = "alloy-composition-optimization"
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "材料配方设计与性能筛选"
RESULTS = RUNTIME.results_root
RUNNER = RUNTIME.runner
APPLICATION = RUNTIME.application
app = FastAPI(title="Material Composition Optimization Service", version="0.2.0")
logger = logging.getLogger("alloy.service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def _template_label(template: str) -> str:
    return {
        "alloy_domain_confirmation": "高温合金材料体系确认",
        "aerospace_high_temperature_hea_exploration": "航空发动机高温高熵/多主元合金探索模板",
        "generic_hea_exploration": "通用高熵/多主元合金探索模板",
        "hot_end_ni_superalloy_screening": "高温镍基合金成分设计与服役性能筛选模板",
        "reusable_rocket_stainless_screening": "可回收火箭不锈钢配方设计模板",
        "chip_glass_thermomechanical_local_screening": "芯片玻璃基板配方与热机械筛选模板",
    }.get(template, template)


def _runner_ready() -> dict[str, bool]:
    return {"hea_mpea": RUNNER.ready("hea_mpea"), "ni_superalloy_hot_end": RUNNER.ready("ni_superalloy_hot_end"), "reusable_rocket_stainless": RUNNER.ready("reusable_rocket_stainless"), "chip_glass_thermomechanical_family_v1": RUNNER.ready("chip_glass_thermomechanical_family_v1")}


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
            "goal": "形成 HEA/MPEA、热端镍基、可回收火箭不锈钢或芯片玻璃基板的受约束候选配方、条件性能初筛和验证优先级建议。",
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
                "match_when": "针对已接入材料域，需要生成受约束配方候选并比较模型支持性能时。高熵/多主元进入 HEA；发动机热端/蠕变进入镍基；可回收火箭不锈钢结构进入火箭不锈钢；芯片封装玻璃基板、低硼无碱玻璃或氧化物配方进入玻璃基板路线。",
                "include_keywords": ["铁基合金", "铝基合金", "高熵合金", "多主元合金", "HEA", "MPEA", "高温合金", "镍基合金", "单晶镍基", "定向凝固", "蠕变", "Inconel", "CMSX", "Rene", "难熔合金", "合金配比", "合金成分", "元素比例", "原子百分比", "质量百分比", "wt.%", "添加量", "微量元素", "成分优化", "配比优化", "成分空间", "组分设计", "候选配比", "元素组成", "微观组织", "组织演变", "宏观性能", "热力学", "动力学", "Ni-Co-Cr", "Nb-Mo-Ta-W", "不锈钢", "火箭不锈钢", "航天火箭", "可回收壳体", "可回收外壳", "不锈钢壳体", "火箭外壳", "火箭贮箱", "承压壳体", "奥氏体不锈钢", "304L", "301LN", "30X", "玻璃基板", "芯片玻璃", "封装玻璃", "玻璃配方", "低硼无碱", "铝硼硅酸盐", "氧化物 mol%"],
                "exclude_keywords": ["复合材料", "复材", "树脂", "环氧", "纤维", "碳纤维", "玻璃纤维", "填料", "增强相", "聚合物", "CFRP", "GFRP", "PEEK", "PEKK", "PEI", "PPS"],
                "model_routes": {
                    "hea_mpea": {"when": "出现 HEA/MPEA/高熵/多主元，或明确 at.% 多主元成分空间、强度—硬度与相稳定性探索", "input": "元素、at.% 边界、工艺和温度", "output": "屈服强度、硬度、相风险、数据适用域与候选排序"},
                    "ni_superalloy_hot_end": {"when": "出现高温合金、镍基高温合金、蠕变、持久寿命、单晶、定向凝固、涡轮/叶片、Inconel、CMSX 或 René；发动机与高温工况同时出现时优先进入此路线", "input": "元素 wt.% 边界、铸造/DS/单晶、热处理、温度、蠕变载荷", "output": "短时 UTS、0.2% proof strength、蠕变断裂寿命、延性辅助信息与候选排序"},
                    "reusable_rocket_stainless": {"when": "出现航天火箭与可回收壳体/外壳/贮箱不锈钢，或出现低温奥氏体不锈钢、301/304L、cryoforming 或 30X 背景", "input": "元素 wt.% 边界、目标温度、固溶处理、板厚和焊接状态", "output": "293–1273 K 短时屈服/UTS/延伸率筛选；低温参考与焊接、疲劳、LOX 验证优先级"},
                    "chip_glass_thermomechanical_family_v1": {"when": "出现芯片封装玻璃基板、低硼无碱铝硼硅酸盐、氧化物 mol% 配方、CTE/热失配/玻璃挠曲", "input": "氧化物 mol% 边界、CTE/E/SOC 目标或门槛、候选数；仿真时另输入层堆和热历史", "output": "CTE（0–300°C）、密度、E、SOC、两项黏度特征温度、同家族候选排序与来源锚点"},
                },
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.AlloyCompositionOptimizationRole",
        }
    }

@app.get("/health")
def health(): return {"status":"ok","runner_ready":_runner_ready(),"runner_prefix":str(RUNNER.environment_prefix)}
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
        await stream_authoritative_markdown(
            websocket,
            f"{planned_alloy_method_block(payload)}\n\n待确认：{'；'.join(plan['questions_to_confirm'])}",
            step_id=FRONTEND_STEP_ID,
        )
        if plan.get("requires_domain_confirmation"):
            waiting = {
                "taskid": taskid, "status": "waiting_for_input", "service": SERVICE,
                "model_domain": "routing_confirmation", "requirement_interpretation": plan,
                "user_conclusion": "请确认采用高温镍基合金还是 HEA/MPEA 路线后开始配方筛选。",
            }
            await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"completed","description":"已识别高温合金任务，等待确认材料体系。"}})
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
        runner_description = "正在通过芯片玻璃基板专项 runner 在同家族氧化物邻域内生成候选并预测热机械性质。" if effective.get("model_domain") == "chip_glass_thermomechanical_family_v1" else "正在通过隔离的高温镍基合金专项 runner 进行受约束候选筛选与条件预测。" if effective.get("model_domain") == "ni_superalloy_hot_end" else "正在通过可回收火箭不锈钢专项 runner 进行候选筛选与短时拉伸预测。" if effective.get("model_domain") == "reusable_rocket_stainless" else "正在通过隔离的高熵/多主元合金（HEA/MPEA）专项 runner 进行采样和批量预测。"
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"status":"in_progress","description":runner_description}})
        result=await asyncio.to_thread(_proposal,payload); result["_summary_path"]=RESULTS/taskid/"presentation"/"summary.md"
        public_urls,asset_docs,_asset_titles,visual_assets=await prepare_public_assets(websocket,taskid,result,RESULTS)
        # PNG 图表只嵌入流式 Markdown；不再额外发送 MaterialsPNG JSON，避免
        # 前端把同一资产渲染两次。
        await emit_result_content(websocket,result,step_id=FRONTEND_STEP_ID,visual_assets=visual_assets)
        result.pop("_summary_path",None)
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"result","data":result})
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
