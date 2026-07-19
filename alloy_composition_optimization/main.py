"""Web/LLM layer for alloy composition optimization.

Numerical HEA work is deliberately delegated to the isolated micromamba runner.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from matplotlib.ft2font import FT2Font
from matplotlib.font_manager import FontProperties
from src.alloy_workflow.presentation import emit_result_content, final_conclusion_block
from src.alloy_workflow.assets import publish_png_assets

load_dotenv()
SERVICE = "alloy-composition-optimization"
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "耗材选型和计算优化"
FRONTEND_TEAM_TYPE = "Robot_Materials"
RESULTS = Path(os.getenv("ALLOY_RESULTS_ROOT", "results/alloy_composition_optimization"))
SURROGATE_ROOT = Path(os.getenv("HEA_SURROGATE_ROOT", "/data/se42/hea_surrogate"))
SURROGATE_ENV = Path(os.getenv("HEA_SURROGATE_ENV_PREFIX", "/data/mamba/envs/mattergen-py310"))
MODEL_REPORTS = SURROGATE_ROOT / "reports" / "models"
MICROMAMBA = os.getenv("MICROMAMBA_EXECUTABLE", "micromamba")
CJK_FONT_PATH = Path(os.getenv(
    "ALLOY_CJK_FONT_PATH",
    str(Path(__file__).resolve().parent.parent / "inorganic_new_material/assets/fonts/NotoSansCJKsc-Regular.otf"),
))
app = FastAPI(title="Alloy Composition Optimization Service", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def _taskid(payload: dict[str, Any]) -> str:
    external_taskid = str(payload.get("taskid") or f"alloy-{datetime.now(timezone.utc):%Y%m%d%H%M%S}").strip()
    if not external_taskid or len(external_taskid) > 512:
        raise ValueError("invalid taskid")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", external_taskid):
        return external_taskid
    # Gateway conversation IDs commonly contain '/'.  The runner and asset
    # paths need one safe segment, so preserve a stable correlation instead of
    # rejecting an otherwise valid upstream request.
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", external_taskid).strip("_.-")[:72]
    digest = hashlib.sha256(external_taskid.encode("utf-8")).hexdigest()[:16]
    safe_taskid = f"{readable or 'alloy'}-{digest}"
    print(f"[alloy] mapped external taskid={external_taskid!r} to safe_taskid={safe_taskid!r}")
    return safe_taskid


def _template_label(template: str) -> str:
    return {
        "aerospace_high_temperature_hea_exploration": "航空发动机高温高熵/多主元合金探索模板",
        "generic_hea_exploration": "通用高熵/多主元合金探索模板",
    }.get(template, template)


def _context_text(value: Any, limit: int = 12000) -> str:
    """Collect current request and upstream conversational context."""
    chunks: list[str] = []
    def visit(item: Any) -> None:
        if len("\n".join(chunks)) >= limit:
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            try:
                decoded = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                chunks.append(text)
            else:
                visit(decoded)
        elif isinstance(item, dict):
            for key in ("idea", "content", "text", "query", "requirement", "summary", "message", "history", "messages", "conversation", "upstream_context", "previous_results"):
                if item.get(key) is not None:
                    visit(item[key])
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return "\n\n".join(chunks)[:limit]


def _upstream_requirement(payload: dict[str, Any]) -> tuple[str, list[str]]:
    keys = [key for key in ("idea", "content", "query", "history", "messages", "conversation", "upstream_context", "previous_results") if payload.get(key) is not None]
    return _context_text({key: payload[key] for key in keys}), keys


def _is_alloy_request(text: str, scope: dict[str, Any]) -> bool:
    if scope.get("composition") or scope.get("allowed_elements") or scope.get("element_bounds_at_pct"):
        return True
    lowered = text.casefold()
    hea_system = any(token in lowered for token in ("hea", "mpea", "高熵", "多主元"))
    composition_intent = any(token in lowered for token in ("配比", "成分", "元素比例", "原子百分比", "at.%", "优化"))
    high_temp_alloy = ("高温合金" in lowered or "high-temperature alloy" in lowered) and composition_intent
    return (hea_system and composition_intent) or high_temp_alloy


def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    scope = payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {}
    if not isinstance(scope, dict): raise ValueError("alloy_optimization must be an object")
    upstream_context, upstream_keys = _upstream_requirement(payload)
    if not _is_alloy_request(upstream_context, scope):
        raise ValueError("本服务仅处理合金/高温合金的成分或配比优化；已有材料查询请使用成熟材料服务，非合金新材料生成请使用新材料服务")
    domain = scope.get("model_domain", "hea_mpea")
    if domain not in {"hea_mpea", "conventional_alloy", "refractory_calculated"}: raise ValueError("unsupported model_domain")
    return {"taskid":_taskid(payload),"raw_requirement":upstream_context,"upstream_context":upstream_context,"upstream_context_keys":upstream_keys,"model_domain":domain,"composition":scope.get("composition"),"allowed_elements":scope.get("allowed_elements",[]),"element_bounds_at_pct":scope.get("element_bounds_at_pct",{}),"processing_method":scope.get("processing_method"),"test_temperature_C":scope.get("test_temperature_C",25),"objectives":scope.get("objectives",{}),"constraints":scope.get("constraints",{})}


def _requirement_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied = dict(payload.get("alloy_optimization") or payload.get("hea_optimization") or payload.get("constraints") or {})
    idea, upstream_keys = _upstream_requirement(payload)
    if not _is_alloy_request(idea, supplied):
        raise ValueError("本服务仅适用于合金或高温合金的成分优化，不适用于一般高温材料查询或非合金新材料生成")
    text = idea.lower(); engine = any(token in text for token in ("航空","发动机","aero","engine","turbine","热端","高温"))
    if engine:
        template="aerospace_high_temperature_hea_exploration"; inferred={"model_domain":"hea_mpea","allowed_elements":["Ni","Co","Cr","Al","Ti"],"element_bounds_at_pct":{"Ni":[20,40],"Co":[10,30],"Cr":[10,25],"Al":[5,15],"Ti":[5,20]},"processing_method":"CAST","test_temperature_C":900,"screening_mode":"conservative_adaptive","objectives":{"yield_strength_MPa":{"goal":"maximize"},"phase_risk":{"goal":"minimize"}}}; questions=["请确认部件类型、服役温度与保温时间。","请确认氧化环境、密度上限、制造路线和元素禁限。"]
    else:
        template="generic_hea_exploration"; inferred={"model_domain":"hea_mpea","allowed_elements":["Co","Cr","Fe","Mn","Ni"],"element_bounds_at_pct":{"Co":[10,30],"Cr":[10,30],"Fe":[10,30],"Mn":[10,30],"Ni":[10,30]},"processing_method":"CAST","test_temperature_C":25,"screening_mode":"conservative_adaptive","objectives":{"yield_strength_MPa":{"goal":"maximize"},"phase_risk":{"goal":"minimize"}}}; questions=["请确认目标服役温度、允许元素体系、工艺和成本约束。"]
    effective=dict(inferred); effective.update({k:v for k,v in supplied.items() if v not in (None,[],{},"")})
    provenance={key:("user" if key in supplied and supplied[key] not in (None,[],{},"") else "template_inference") for key in effective}
    return effective,{"parser":"rule_template_v0","raw_requirement":idea,"upstream_context_keys":upstream_keys,"template":template,"effective_model_input":effective,"field_provenance":provenance,"default_assumptions":[{"field":k,"value":v,"status":"requires_confirmation"} for k,v in inferred.items() if provenance[k]=="template_inference"],"questions_to_confirm":questions,"evidence_notice":"Template inference is exploratory only, not an engineering conclusion."}


def _runner_ready() -> bool:
    return (SURROGATE_ENV / "bin/python").is_file() and all((SURROGATE_ROOT / "models" / f"{name}_ensemble.joblib").is_file() for name in ("yield_strength","hardness","phase"))


def _model_evidence() -> dict[str, Any]:
    """Small, factual training summary for every user-facing result."""
    reports: dict[str, Any] = {}
    for key in ("yield_strength", "hardness", "phase"):
        path = MODEL_REPORTS / f"{key}_training_report.json"
        try:
            reports[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reports[key] = {"status": "report_unavailable"}
    return {
        "model_version": "hea_mpea_baseline_v0.1",
        "data_type": "仅使用实验 HEA/MPEA 数据；NbCrVWZr 计算数据未参与本轮训练",
        "validation": "按规范化成分分组划分训练/验证/测试集（同一成分不会同时出现在训练和测试中），并用 5 个随机种子形成集成预测",
        "reports": reports,
    }


def _run_runner(taskid: str, operation: str, constraints: dict, candidates: list | None = None) -> dict:
    if not _runner_ready(): raise RuntimeError("HEA surrogate runner is not ready; create hea-surrogate-py310 and retrain models with tools/setup_hea_surrogate_env.sh")
    task_dir=RESULTS/taskid; task_dir.mkdir(parents=True,exist_ok=True); request=(task_dir/"runner_request.json").resolve(); response=(task_dir/"runner_response.json").resolve()
    request.write_text(json.dumps({"operation":operation,"constraints":constraints,"candidates":candidates or []},ensure_ascii=False),encoding="utf-8")
    command=[MICROMAMBA,"run","-p",str(SURROGATE_ENV),"python",str(SURROGATE_ROOT/"tools/service_runner.py"),"--request",str(request),"--response",str(response)]
    completed=subprocess.run(command,cwd=SURROGATE_ROOT,capture_output=True,text=True,timeout=int(os.getenv("HEA_RUNNER_TIMEOUT_SECONDS","90")))
    if not response.is_file(): raise RuntimeError(f"HEA runner did not return JSON: {completed.stderr[-1000:]}")
    data=json.loads(response.read_text(encoding="utf-8"))
    if not data.get("ok"): raise RuntimeError(data.get("error","HEA runner failed"))
    return data["result"]


def _enrich(result: dict, plan: dict) -> dict:
    result["requirement_interpretation"]=plan; result["model_evidence"]=_model_evidence(); result["next_actions"]=["确认或修改模板假设","查看候选表和图表","由上游架构决定是否继续数学优化"]
    result["nonlinear_response_function"]={
        "name":"hea_mpea_surrogate_response_v0_1",
        "meaning":"成分、工艺和温度到材料性质的非线性响应关系；不是固定线性配比公式。",
        "mathematical_form":"F(composition_at_pct, processing_method, test_temperature_C) -> {yield_strength_MPa_mean_std, hardness_HV_mean_std, phase_probabilities, applicability_domain}",
        "batch_call":"POST /alloy/evaluate-batch",
        "input":{"composition_at_pct":"元素原子百分比，总和为 100", "processing_method":"如 CAST", "test_temperature_C":"测试或目标服役温度（°C）"},
        "output":{"yield_strength_MPa":"预测均值和集成标准差", "hardness_HV":"预测均值和集成标准差", "phase_probabilities":"AM/IM/SS/SS+IM 概率", "applicability_domain":"训练域内、边界或域外"},
    }
    feasible=int(result.get("sampling",{}).get("feasible",0)); generated=int(result.get("sampling",{}).get("generated",0)); initial=result.get("initial_candidates",[])
    if initial:
        top=initial[0]; phase_text="较低" if top["phase_risk"]=="low" else "较高"; domain_text={"inside":"训练数据范围内", "boundary":"训练数据边界附近", "outside":"训练数据范围外"}.get(top["applicability_domain"]["level"], top["applicability_domain"]["level"])
        result["user_conclusion"]=f"在 {generated} 个满足成分边界的候选中，有 {feasible} 个通过当前模型的初步筛选。排在前面的候选预测屈服强度为 {top['yield_strength_MPa']['mean']:.0f} ± {top['yield_strength_MPa']['std']:.0f} MPa、硬度为 {top['hardness_HV']['mean']:.0f} ± {top['hardness_HV']['std']:.0f} HV；析出相相关风险{phase_text}，但它位于{domain_text}。它适合进入下一步验证，不等同于已确认的工程用材。"
    else: result["user_conclusion"]=f"在当前目标和约束下，{generated} 个候选均未通过初筛。建议检查温度/工艺假设、元素边界或目标阈值。"
    result["downstream_handoff_text"]="本服务提供可批量调用的非线性成分—性能评价函数。上游架构如需继续数学优化，可读取搜索范围、初始候选和筛选条件，并对新成分调用该评价函数。"
    result["downstream_handoff"]={"decision_variables":"search_space","initial_population":"initial_candidates","evaluation_contract":"HEA runner evaluate/evaluate_batch","do_not_treat_as_hard_bounds":"derived_candidate_percentiles_at_pct"}
    return result


def _save(manifest: dict) -> None:
    path=RESULTS/manifest["taskid"]; path.mkdir(parents=True,exist_ok=True); (path/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")


def _chinese_chart_font() -> FontProperties:
    """Use a verified CJK font rather than matplotlib's uncertain fallback."""
    if not CJK_FONT_PATH.is_file():
        raise RuntimeError(f"Chinese chart font is unavailable: {CJK_FONT_PATH}")
    chart_text = "候选筛选漏斗生成候选通过初筛候选数量筛选候选强度硬度分布训练数据范围内边界附近预测屈服强度MPa元素含量atP5P50P95成分区间非最终配方0123456789NiCoCrAlTi—；.%（）"
    font_file = FT2Font(str(CJK_FONT_PATH))
    missing = sorted({character for character in chart_text if not character.isspace() and not font_file.get_char_index(ord(character))})
    if missing:
        raise RuntimeError(f"Chinese chart font is missing glyphs: {''.join(missing)}")
    plt.rcParams["axes.unicode_minus"] = False
    return FontProperties(fname=str(CJK_FONT_PATH))


def _apply_chart_font(ax, font: FontProperties) -> None:
    for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        label.set_fontproperties(font)


def _render(result: dict) -> dict:
    task_dir=RESULTS/result["taskid"]/"presentation"; task_dir.mkdir(parents=True,exist_ok=True); os.environ.setdefault("MPLCONFIGDIR","/tmp/matplotlib"); assets={}; candidates=result.get("_presentation_candidates",result.get("initial_candidates",[])); sampling=result.get("sampling",{}); font=_chinese_chart_font()
    fig,ax=plt.subplots(figsize=(6.5,4)); bars=ax.bar(["生成候选","通过初筛"],[int(sampling.get("generated",0)),int(sampling.get("feasible",0))],color=["#9ecae1","#2ca25f"]); ax.bar_label(bars,padding=3); ax.set_ylabel("候选数量",fontproperties=font); ax.set_title("候选筛选漏斗",fontproperties=font); _apply_chart_font(ax,font); ax.grid(axis="y",alpha=.25); path=task_dir/"screening_funnel.png"; fig.tight_layout(); fig.savefig(path,dpi=160); plt.close(fig); assets["screening_funnel"]=path
    if candidates:
        fig,ax=plt.subplots(figsize=(7,4.5)); colors=["#1f77b4" if item["applicability_domain"]["level"]=="inside" else "#ff7f0e" for item in candidates]; ax.scatter([item["yield_strength_MPa"]["mean"] for item in candidates],[item["hardness_HV"]["mean"] for item in candidates],c=colors,alpha=.65); ax.scatter([],[],c="#1f77b4",label="训练数据范围内"); ax.scatter([],[],c="#ff7f0e",label="训练数据边界附近"); ax.legend(prop=font); ax.set_xlabel("预测屈服强度（MPa）",fontproperties=font); ax.set_ylabel("预测硬度（HV）",fontproperties=font); ax.set_title("筛选候选：强度—硬度分布",fontproperties=font); _apply_chart_font(ax,font); ax.grid(alpha=.25); path=task_dir/"strength_hardness_tradeoff.png"; fig.tight_layout(); fig.savefig(path,dpi=160); plt.close(fig); assets["strength_hardness_tradeoff"]=path
        ranges=result.get("derived_candidate_percentiles_at_pct",{}); names=list(ranges); fig,ax=plt.subplots(figsize=(7,4.5)); low=[ranges[n]["p05"] for n in names]; mid=[ranges[n]["p50"] for n in names]; high=[ranges[n]["p95"] for n in names]; ax.errorbar(names,mid,yerr=[np.subtract(mid,low),np.subtract(high,mid)],fmt="o",capsize=7,color="#4c78a8"); ax.set_ylabel("元素含量（at.%；P5—P50—P95）",fontproperties=font); ax.set_title("候选成分区间（非最终配方）",fontproperties=font); _apply_chart_font(ax,font); ax.grid(axis="y",alpha=.25); path=task_dir/"composition_percentiles.png"; fig.tight_layout(); fig.savefig(path,dpi=160); plt.close(fig); assets["composition_percentiles"]=path
    lines=["### 合金配比探索结果","",final_conclusion_block(result)]
    summary=task_dir/"summary.md"; summary.write_text("\n".join(lines),encoding="utf-8"); assets["summary_markdown"]=summary; return assets


def _proposal(payload: dict) -> dict:
    effective,plan=_requirement_plan(payload); normalized=dict(payload); normalized["alloy_optimization"]=effective; constraints=_contract(normalized); constraints["raw_scope"]=effective
    started=time.perf_counter(); result=_run_runner(constraints["taskid"],"propose",constraints); result.update({"taskid":constraints["taskid"],"status":"completed","service":SERVICE,"elapsed_seconds":round(time.perf_counter()-started,3)}); _enrich(result,plan); assets=_render(result); result.pop("_presentation_candidates",None); result["presentation"]={"summary_markdown":f"/alloy/tasks/{constraints['taskid']}/assets/summary.md","assets":[{"name":name,"url":f"/alloy/tasks/{constraints['taskid']}/assets/{path.name}","type":"MaterialsPNG"} for name,path in assets.items() if path.suffix==".png"]}; _save(result); return result


@app.get("/")
def root(): return {"service":SERVICE,"status":"ok","service_python":"ai4m-service-py310 required","hea_runner_ready":_runner_ready()}


@app.get("/roles")
def roles():
    """Compatibility discovery endpoint used by the shared service gateway.

    Keep this metadata-only: instantiating Alpha's generic ``Team`` here would
    create its default OpenAI client merely to list a role, which is unrelated
    to this service's SeLLM-compatible presentation path.
    """
    profile = (
        "子流程：合金与高温合金成分配比优化（alloy_composition_optimization）。"
        "仅在需要调整元素种类、原子百分比或合金成分空间时调用，用于 HEA/MPEA 或高温合金的候选排序、"
        "性能预测、不确定性与适用域评价。典型触发词：合金配比、高熵合金、HEA、MPEA、元素比例、原子百分比、成分优化。"
        "排除：已有牌号/商品材料的性质查询或材料选型、FDM/FFF 丝材和商用耗材筛选；明确化学式的全新晶体生成；"
        "只有“高温、热稳定性、导热”等性能描述但没有高熵/高温合金成分设计意图的请求。"
    )
    return {
        profile: {
            "name": "合金配比优化和候选初筛",
            "profile": profile,
            "goal": "",
            "constraints": "",
            "desc": "",
            "is_human": False,
            "role_id": "alloy_composition_optimization_v1",
            "states": ["0. Coding"],
            "actions": [{
                "name": "Coding",
                "i_context": "",
                "prefix": f"You are a {profile}, named 合金配比优化和候选初筛, your goal is . ",
                "desc": (
                    "子流程：合金配比优化和候选初筛。用于接收自然语言或结构化合金需求，"
                    "构建 HEA/MPEA 成分搜索空间，执行屈服强度、硬度、相组成、不确定性和适用域评价，"
                    "输出候选排序、约束检查与供数学优化使用的结构化交接包。"
                ),
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
            "addresses": ["src.team_config.XIMUAlpha_MNS", "合金配比优化和候选初筛"],
            "planner": {
                "plan": {"goal": "", "context": "", "tasks": [], "task_map": {}, "current_task_id": ""},
                "working_memory": {"storage": [], "index": {}, "ignore_id": False},
                "auto_run": False,
                "use_tools": False,
            },
            "routing": {
                "service_id": "alloy_composition_optimization",
                "priority": 1,
                "match_when": "请求明确涉及 HEA/MPEA/高熵/多主元或高温合金，并且要求配比、元素比例、成分空间或优化。",
                "include_keywords": ["高熵合金配比", "HEA", "MPEA", "多主元成分", "元素比例", "原子百分比", "成分优化", "高温合金配比"],
                "exclude_keywords": ["已有材料查询", "商品材料", "牌号查询", "材料选型", "材料筛选", "FDM", "FFF", "丝材", "商用耗材", "明确化学式的新材料生成"],
            },
            "recovered": False,
            "latest_observed_msg": None,
            "__module_class_name": "src.team_config.XIMUAlpha_MNS",
        }
    }

@app.get("/health")
def health(): return {"status":"ok","hea_runner_ready":_runner_ready(),"runner_prefix":str(SURROGATE_ENV)}
@app.post("/alloy/requirements/preview")
def requirement_preview(payload:dict=Body(...)): return _requirement_plan(payload)[1]
@app.post("/alloy/propose-space")
def propose(payload:dict=Body(...)):
    try:return _proposal(payload)
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate")
def evaluate(payload:dict=Body(...)):
    try:
        constraints=_contract(payload); result=_run_runner(constraints["taskid"],"evaluate",constraints); manifest={"taskid":constraints["taskid"],"status":"completed","service":SERVICE,"result":result}; _save(manifest); return manifest
    except (ValueError,RuntimeError) as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/alloy/evaluate-batch")
def evaluate_batch(payload:dict=Body(...)):
    try:
        constraints=_contract(payload); candidates=payload.get("candidates") or []; result=_run_runner(constraints["taskid"],"evaluate_batch",constraints,candidates); return {"taskid":constraints["taskid"],"status":"completed","service":SERVICE,**result}
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


async def prepare_public_assets(websocket: WebSocket, taskid: str, result: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[dict[str, str]]]:
    """Shared MinIO/Markdown asset preparation for direct and Team routes."""
    assets_to_publish=[{"name":item["name"], "local_path":RESULTS/taskid/"presentation"/Path(item["url"]).name} for item in result["presentation"]["assets"]]
    try:
        public_urls=await publish_png_assets(taskid, assets_to_publish)
    except Exception as asset_exc:
        public_urls={}
        await websocket.send_json({"version":"1.0.0", "agent":"alloy_composition_optimization", "request_id":taskid, "type":"progress", "data":{"id":FRONTEND_STEP_ID, "stepId":FRONTEND_STEP_ID, "title":FRONTEND_STEP_TITLE, "teamType":FRONTEND_TEAM_TYPE, "status":"failed", "description":str(asset_exc)}})
    asset_docs={"screening_funnel":"左柱为生成的候选数，右柱为通过初筛的候选数，用于判断当前条件的筛选严格程度。","strength_hardness_tradeoff":"每个点代表一个通过初筛的候选：横轴越右表示预测屈服强度越高，纵轴越上表示预测硬度越高；蓝色为训练数据覆盖较好，橙色为训练数据边界附近。","composition_percentiles":"这张图展示保留候选中每种元素的常见含量区间：竖线下端为 P5、圆点为 P50（中位数）、上端为 P95。它用于了解下一步可继续探索的配比区域，不代表最终推荐配方。"}
    asset_titles={"screening_funnel":"候选筛选概览","strength_hardness_tradeoff":"强度与硬度的候选分布","composition_percentiles":"候选成分区间图（非最终配方）"}
    visual_assets=[{"url":public_urls[item["name"]], "title":asset_titles.get(item["name"], item["name"]), "description":asset_docs.get(item["name"], "")} for item in result["presentation"]["assets"] if item["name"] in public_urls]
    return public_urls, asset_docs, asset_titles, visual_assets


async def emit_public_asset_events(websocket: WebSocket, result: dict[str, Any], public_urls: dict[str, str], asset_docs: dict[str, str]) -> None:
    for item in result["presentation"]["assets"]:
        asset_url=public_urls.get(item["name"])
        if asset_url:
            await websocket.send_json({"step_id":FRONTEND_STEP_ID, "stepId":FRONTEND_STEP_ID, "title":FRONTEND_STEP_TITLE, "teamType":FRONTEND_TEAM_TYPE, "name":item["name"], "docs":asset_docs.get(item["name"], item["name"]), "url":asset_url, "type":"MaterialsPNG", "description":asset_docs.get(item["name"], "")})
            await asyncio.sleep(0.15)
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
        effective,plan=_requirement_plan(payload); taskid=_taskid(payload)
        context, context_keys = _upstream_requirement(payload)
        context_preview = re.sub(r"\s+", " ", context)[:600]
        print(f"[WS /alloy/start] upstream received taskid={taskid} peer={peer} keys={context_keys} context_chars={len(context)} preview={context_preview!r}", flush=True)
        print(f"[ALLOY][{taskid}] accepted template={plan.get('template')!r} domain={effective.get('model_domain')!r}", flush=True)
        await websocket.send_text("[start]")
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"teamType":FRONTEND_TEAM_TYPE,"status":"completed","description":"已生成可覆盖的探索模板和待确认项。","result":plan}})
        await websocket.send_text(f"<<<CONTENT_START:{FRONTEND_STEP_ID}>>>\n### 合金设计需求解读\n- 探索方案：{_template_label(plan['template'])}\n- 适用对象：高熵合金/多主元合金。\n- 待确认：{'；'.join(plan['questions_to_confirm'])}\n<<<CONTENT_END:{FRONTEND_STEP_ID}>>>")
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"progress","data":{"id":FRONTEND_STEP_ID,"stepId":FRONTEND_STEP_ID,"title":FRONTEND_STEP_TITLE,"teamType":FRONTEND_TEAM_TYPE,"status":"in_progress","description":"正在通过隔离的 HEA 专项 runner 进行采样和批量预测。"}})
        result=await asyncio.to_thread(_proposal,payload); result["_summary_path"]=RESULTS/taskid/"presentation"/"summary.md"
        public_urls,asset_docs,_asset_titles,visual_assets=await prepare_public_assets(websocket,taskid,result)
        # Follow the neighboring 3D-material service: public image URLs are
        # embedded in the streamed Markdown *and* announced as asset events.
        # The first path works in chat renderers that do not render asset cards.
        await emit_result_content(websocket,result,step_id=FRONTEND_STEP_ID,visual_assets=visual_assets)
        result.pop("_summary_path",None)
        await emit_public_asset_events(websocket,result,public_urls,asset_docs)
        await websocket.send_json({"version":"1.0.0","agent":"alloy_composition_optimization","request_id":taskid,"type":"result","data":result})
        await websocket.send_text("[end]")
        print(f"[ALLOY][{taskid}] completed candidates={len(result.get('candidates') or [])}", flush=True)
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

if __name__=="__main__": uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","20162")))
