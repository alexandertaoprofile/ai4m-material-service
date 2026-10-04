import os
import json
import hashlib
import re
import uvicorn
import asyncio
import sys
import logging
from logging.handlers import RotatingFileHandler

from pathlib import Path
from dotenv import load_dotenv

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, File, UploadFile, Form, Body
from fastapi.encoders import jsonable_encoder
from src.material_workflow.constraints import normalize_taskid
from src.material_workflow.llm_constraint_inference import (
    GenerationInputRequired,
    resolve_generation_request,
)
from src.material_workflow.emitters import build_frontend_payload
from src.material_workflow.upstream_api import run_upstream_request
from src.service_paths import NEW_MATERIAL_RESULTS_ROOT
from src.service_identity import SERVICE_ID
from src.team_config import InorganicNewMaterialService

# Always load this service's own configuration, independent of the tmux cwd.
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
PORT = int(os.getenv("PORT", "1107"))
# 设置静态文件目录
def setup_science_backend_logger():
    """Set up science_backend logger with automatic log rotation"""
    # Create logs directory if it doesn't exist
    logs_dir = BASE_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure logger
    science_logger = logging.getLogger('science_backend')
    science_logger.setLevel(logging.INFO)
    
    # Don't propagate to parent logger to avoid duplicate logs
    science_logger.propagate = False
    
    # Clear existing handlers to avoid duplicates
    if science_logger.handlers:
        science_logger.handlers.clear()
    
    # Create file handler with rotation
    # 当日志文件超过20MB时自动拆分，保留10个备份文件
    file_handler = RotatingFileHandler(
        logs_dir / "science_backend.log",
        maxBytes=20*1024*1024,  # 20MB
        backupCount=10,
        encoding='utf-8'  # 支持中文
    )
    
    # Create console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Set formatter for both handlers
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    science_logger.addHandler(file_handler)
    science_logger.addHandler(console_handler)
    
    return science_logger

UPLOAD_DIR = BASE_DIR / "upload"
setup_science_backend_logger()
WORKFLOW_LOGGER = logging.getLogger("mattergen_workflow")
WORKFLOW_LOGGER.setLevel(logging.INFO)
WORKFLOW_LOGGER.propagate = False
if not WORKFLOW_LOGGER.handlers:
    for handler in logging.getLogger("science_backend").handlers:
        WORKFLOW_LOGGER.addHandler(handler)
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，也可以指定具体源列表如["http://example.com", "https://example.com"]
    allow_credentials=False,  # 如果你的API需要 cookies 或者认证信息，设置为True
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],  # 允许所有头部
)

ORCHESTRATOR = InorganicNewMaterialService()
_NATIVE_IDEA_LOG_LIMIT = 4_000


def _native_idea_trace(value: object, *, limit: int = _NATIVE_IDEA_LOG_LIMIT) -> tuple[str, int, str, bool]:
    """Log the received top-level ``idea`` itself, rather than a preview wrapper."""
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    truncated = len(encoded) > limit
    return encoded[:limit], len(encoded), hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16], truncated

@app.websocket("/start")
@app.websocket("/new-material/start")
async def websocket_endpoint(websocket: WebSocket):
    team = ORCHESTRATOR.create_team()
    await websocket.accept()
    try:
        # 接收初始化数据
        init_data = await websocket.receive_json()
        request = ORCHESTRATOR.websocket_request(init_data)
        idea = request["idea"]
        n_round = int(len(team.env.roles))
        taskid = request["taskid"]
        user_name = request["user_name"]
        file_metadata = request["file_metadata"]
        embedded_taskid = taskid
        try:
            embedded = json.loads(idea) if isinstance(idea, str) else {}
            if isinstance(embedded, dict):
                embedded_taskid = str(embedded.get("taskid") or taskid)
        except (TypeError, json.JSONDecodeError):
            pass
        context_keys = [key for key in ("idea", "content", "query", "history", "messages", "conversation", "upstream_context", "previous_results") if init_data.get(key) is not None]
        context_preview = re.sub(r"\s+", " ", json.dumps({key: init_data[key] for key in context_keys}, ensure_ascii=False))[:600]
        print(f"[WS /new-material/start] upstream received: session_taskid={taskid} material_taskid={embedded_taskid} user={user_name} files={len(file_metadata)} keys={context_keys} preview={context_preview!r}")
        native_idea, native_idea_chars, native_idea_sha256, native_idea_truncated = _native_idea_trace(init_data.get("idea"))
        print(
            f"[WS /new-material/start] native_idea type={type(init_data.get('idea')).__name__} "
            f"json_chars={native_idea_chars} sha256_16={native_idea_sha256} "
            f"truncated={native_idea_truncated} value={native_idea}"
        )

        # Preserve the full envelope for the action's existing context parser.
        # Passing only ``idea`` used to discard separately supplied history and
        # upstream messages before constraint extraction.
        await ORCHESTRATOR.run_round(websocket, team, request["project_idea"], n_round, user_name, taskid, file_metadata)
    except WebSocketDisconnect:
        # 在这里可以添加当客户端断开连接时的处理逻辑
        print("客户端已经主动断开连接！")
    except Exception as e:
        exception_type, exception_value, exception_traceback = sys.exc_info()
        print(f"Exception Type: {exception_type.__name__}")
        print(f"Exception Message: {exception_value}")
        try:
            await websocket.send_text(f"\n新材料发现任务失败：{e}\n")
        except (RuntimeError, WebSocketDisconnect):
            pass
    try:
        await websocket.close()
        print("正常关闭连接！")
    except Exception as e:
        print("非正常关闭连接！")

@app.get("/roles")
async def get_teams():
    team = ORCHESTRATOR.create_team()
    # ``get_roles`` returns Role instances, not dictionaries.  Convert them
    # before enriching the API response with router metadata.
    roles = {}
    for role_name, role in team.env.get_roles().items():
        metadata = jsonable_encoder(role)
        if not isinstance(metadata, dict):
            metadata = {"name": str(role_name)}
        canonical_address = "src.team_config.InorganicNewMaterialDiscoveryRole"
        legacy_addresses = [str(address) for address in metadata.get("addresses", [])]
        metadata["addresses"] = [
            canonical_address,
            *[address for address in legacy_addresses if address != canonical_address],
        ]
        metadata["role_id"] = f"{SERVICE_ID}_v1"
        metadata["goal"] = "生成周期性无机晶体候选，并完成结构准入、已接入性质快速预测与热力学稳定性初筛。"
        metadata["constraints"] = "只在可形成明确无机元素体系的任务中启动；结果属于模型筛选，不替代 DFT、实验或真实合成路线验证。"
        metadata["desc"] = "使用 MatterGen（含准确 Co-Cr-Fe-Mn-Ni 体系的 HEA 微调模型）生成候选 CIF，经 pymatgen、ALIGNN 和 MatterSim 完成结构、性质与 E_hull 初筛并排序。"
        metadata["routing"] = {
            "service_id": SERVICE_ID,
            "priority": 3,
            "match_when": "候选尚未确定，需要在明确无机元素体系中生成新的周期性晶体结构，并进行结构或热力学初筛时。",
            "include_keywords": ["新材料发现", "新材料探索", "候选材料生成", "高通量晶体筛选", "元素体系", "晶体结构空间", "晶体结构", "新晶体", "新无机材料", "MatterGen", "晶体生成", "候选 CIF", "固态电解质晶体", "陶瓷晶体", "热力学稳定性", "E_hull"],
            "exclude_keywords": ["已有材料检索", "牌号查询", "元素配比优化", "聚合物配方", "复合材料配比", "合成路线预测"],
            "input_contract": {
                "required": "当前请求或完整上文中能够形成可追溯的无机元素体系。",
                "required_any": ["明确元素集合", "可解析为元素集合的无机化学式", "已配置且有证据的无机应用模板"],
                "optional": ["new_material.allowed_elements", "new_material.target_properties", "new_material.validation_targets", "new_material.max_candidates", "new_material.structure_space"],
            },
            "output_contract": {
                "generation_started": "候选 CIF、结构准入结果、实际完成的 ALIGNN/MatterSim 初筛、候选排序、可视化和 manifest。",
                "waiting_for_input": "只有完全没有材料、应用、性能、元素体系或材料类别线索时，才返回所需的材料探索信息。",
                "screening_limit": "模型结果只用于候选初筛；不提供已有材料事实、配比优化、严格化学计量保证、真实合成路线或 DFT/实验结论。",
            },
        }
        from src.material_workflow.omatg_contract import omatg_enabled
        if omatg_enabled():
            metadata["desc"] = "OMatG 固定化学式 CSP 或限定元素/已确认开放空间 DNG，直接经周期几何检查、MatterSim 松弛与混合参考凸包、ALIGNN 快速预测和结构去重交付候选；DNG 后追加 CSP 可选，默认关闭。"
            metadata["constraints"] = "周期无机晶体；每胞最多20原子；每阶段1–10个候选。组成需明确，或显式确认开放化学空间；性质目标通过后筛实现。"
            routing = metadata["routing"]
            routing["include_keywords"] = [word for word in routing["include_keywords"] if word != "MatterGen"]
            routing["include_keywords"].extend(["OMatG", "CSP", "DNG", "固定化学式结构预测"])
            routing["match_when"] = "需要生成周期无机晶体，已有确认化学式/元素集合，或已明确确认开放化学空间时。"
            routing["input_contract"] = {"required": "已确认整数化学式，或元素白名单；无组成时需显式确认开放化学空间与探索方向。",
                "required_any": ["target_formula", "allowed_elements", "allow_open_chemical_space=true + 探索方向"],
                "optional": ["generation_mode", "formula_units", "num_atoms", "required_elements", "excluded_elements", "target_properties", "property_constraints", "symmetry_constraints", "max_candidates", "seed", "refine_with_csp"]}
            routing["output_contract"]["waiting_for_input"] = "没有已确认组成且没有开放化学空间授权时，说明可做的任务、缺少的条件与补充方式。"
            routing["output_contract"]["screening_limit"] = "基础预训练模型生成 + 后筛，不保证命中必含元素/指定性能或新颖性；稳定性为混合模型估算，需DFT及实验验证。"
        roles[role_name] = metadata
    return roles


@app.post("/uploadFile")
async def upload_file(
                      files: list[UploadFile] = File(...),
                      taskid: str = Form(...), 
                      ):
    
    """
    文件上传接口，增加了文件类型和大小的验证
    """

    try:
        safe_taskid, _external_taskid = normalize_taskid(taskid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_dir = UPLOAD_DIR / safe_taskid
    save_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 将上传的文件保存到服务器
        for file in files:
            filename = str(file.filename or "")
            if not filename or "\\" in filename or Path(filename).name != filename:
                raise HTTPException(status_code=422, detail="invalid upload filename")
            file_path = save_dir / filename
            contents = await file.read()
            with file_path.open("wb") as f:
                f.write(contents)
        upload_results = ",".join(str(file.filename) for file in files)
        
        return JSONResponse(content=f"文件 {upload_results} 上传成功", status_code=200)
    
    except HTTPException as http_exc:
        # 如果是已知的HTTP异常，直接抛出
        raise http_exc
    
    except Exception as e:
        # 处理其他异常情况
        return JSONResponse(content={"error": str(e)}, status_code=500)
 

@app.post("/files")
def list_files(taskid: str = Form(...)):
    try:
        safe_taskid, _external_taskid = normalize_taskid(taskid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    folder_path = UPLOAD_DIR / safe_taskid
    folder_path.mkdir(parents=True, exist_ok=True)
    
    # 获取文件夹中的文件列表
    files = [path.name for path in folder_path.iterdir() if path.is_file()]
    
    # 返回JSON格式的结果
    return {"files": files}

@app.get("/")
def read_root():
    return {"message": "inorganic_new_material server running."}


@app.post("/new-material/generate")
async def generate_new_material(payload: dict = Body(...)):
    """Run the real MatterGen discovery workflow and return its manifest payload.

    MatterGen runs in the dedicated ``mattergen-py310`` environment by default,
    so the web process remains isolated from CUDA/PyG dependencies.
    """
    try:
        payload, _constraints = await resolve_generation_request(payload)
        result = await asyncio.to_thread(run_upstream_request, payload, NEW_MATERIAL_RESULTS_ROOT)
        return JSONResponse(content={"frontend": build_frontend_payload(result), "manifest": result.to_dict()})
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="taskid已存在；不会覆盖原结果。请查询该任务，或使用新的taskid重新采样。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"new-material pipeline failed: {exc}") from exc


@app.post("/new-material/constraints")
async def preview_new_material_constraints(payload: dict = Body(...)):
    """Normalize an upstream envelope without starting a GPU job."""
    try:
        _payload, constraints = await resolve_generation_request(payload)
        return constraints.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/new-material/tasks/{taskid}")
async def get_new_material_task(taskid: str):
    """Return the final manifest, or durable in-progress state for polling clients."""
    try:
        safe_taskid, _external_taskid = normalize_taskid(taskid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manifest = NEW_MATERIAL_RESULTS_ROOT / safe_taskid / "new_material_pipeline_manifest.json"
    if manifest.exists():
        return JSONResponse(content=json.loads(manifest.read_text(encoding="utf-8")))
    progress = NEW_MATERIAL_RESULTS_ROOT / safe_taskid / "progress.json"
    if progress.exists():
        return JSONResponse(content=json.loads(progress.read_text(encoding="utf-8")), status_code=202)
    raise HTTPException(status_code=404, detail="task manifest not found")


@app.get("/new-material/tasks/{taskid}/assets/{asset_name}")
async def get_new_material_asset(taskid: str, asset_name: str):
    """Serve a rendered asset from this service to avoid browser/OSS URL failures."""
    try:
        safe_taskid, _external_taskid = normalize_taskid(taskid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not asset_name or Path(asset_name).name != asset_name:
        raise HTTPException(status_code=422, detail="invalid asset path")
    asset = NEW_MATERIAL_RESULTS_ROOT / safe_taskid / "presentation" / asset_name
    if not asset.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(asset, filename=asset.name, content_disposition_type="inline")

if __name__ == "__main__":
    uvicorn.run(app='main:app', host="0.0.0.0", port=int(PORT), reload=False)
