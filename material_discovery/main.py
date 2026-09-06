import os
import json
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
        metadata["goal"] = "形成全新的候选材料结构、稳定性与性能评估结果，以及合成路径预测。"
        metadata["constraints"] = "结合当前请求与完整上文，从目标性能、元素体系、材料类别或应用场景中归纳受限生成条件，在元素空间或晶体结构空间中组织新材料探索任务。"
        metadata["desc"] = "面向材料候选尚未确定的探索性研发，结合当前任务与完整上文中的材料方向、应用场景、性能目标、元素体系或材料类别，归纳受限生成条件，生成全新的候选材料结构并评估其稳定性、电子结构及潜在性能。"
        metadata["routing"] = {
            "service_id": SERVICE_ID,
            "priority": 3,
            "match_when": "材料候选尚未确定，并需要围绕超导、超高强等目标性能，在元素空间或晶体结构空间中探索全新材料时。",
            "include_keywords": ["新材料发现", "新材料探索", "候选材料生成", "超导", "超高强", "从头计算", "First-principles", "高通量筛选", "元素空间", "晶体结构空间", "晶体结构", "新晶体", "新无机材料", "MatterGen", "晶体生成", "化学式生成", "数据库外材料", "固态电解质", "陶瓷晶体", "热力学稳定性", "电子结构", "潜在性能", "合成路径"],
            "exclude_keywords": [],
            "input_contract": {
                "required": "当前请求或完整上文中至少包含一种可理解的材料探索线索。",
                "required_any": ["材料方向或已有材料结论", "目标性能", "元素体系", "材料类别", "晶体结构空间", "应用场景"],
                "optional": ["new_material.allowed_elements", "new_material.target_properties", "new_material.validation_targets", "new_material.max_candidates", "new_material.structure_space"],
            },
            "output_contract": {
                "generation_started": "全新候选材料结构、稳定性与性能评估结果、合成路径预测和 manifest。",
                "waiting_for_input": "只有完全没有材料、应用、性能、元素体系或材料类别线索时，才返回所需的材料探索信息。",
                "screening_limit": "候选材料探索结果与计算评估记录。",
            },
        }
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
