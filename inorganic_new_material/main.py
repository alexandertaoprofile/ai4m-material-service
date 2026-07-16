import os
import json
import re
import uvicorn
import asyncio
import traceback
import logging
from logging.handlers import RotatingFileHandler

from alpha.team import Team
from alpha.schema import Message
from team_config import *
from pathlib import Path
from dotenv import load_dotenv

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, File, UploadFile, Form, Body
from fastapi.encoders import jsonable_encoder
from src.material_workflow.constraints import constraint_from_payload
from src.material_workflow.llm_constraint_inference import enrich_payload_with_llm_elements
from src.material_workflow.emitters import build_frontend_payload
from src.material_workflow.upstream_api import run_upstream_request

# Always load this service's own configuration, independent of the tmux cwd.
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
PORT = int(os.getenv("PORT", "1107"))
# 设置静态文件目录
def setup_science_backend_logger():
    """Set up science_backend logger with automatic log rotation"""
    # Create logs directory if it doesn't exist
    logs_dir = 'logs'
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
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
        os.path.join(logs_dir, 'science_backend.log'),
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

UPLOAD_DIR="upload"
NEW_MATERIAL_RESULTS_ROOT = Path(__file__).resolve().parent / "src/MNS_CaseHub/cases/material_discovery_demo/results/new_material"
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

async def start_round(websocket: WebSocket, team, idea, n_round, user_name, taskid, file_metadata):
    team.run_project(idea)
    await websocket.send_text("【XXX 开始: xxxx】")
    workflow_failed = False
    while n_round > 0:
        
        n_round -= 1
        for single_role in team.env.roles.values():
            observe_result = single_role._observe()
            if observe_result:
                single_role._no_think()
                if single_role.is_human:
                    human_str_s = f'【{single_role._setting} 等待您 {single_role.rc.todo.desc}】'
                    await websocket.send_text(human_str_s)
                    if single_role.rc.todo.PROMPT_TEMPLATE is not None:
                        await websocket.send_text(single_role.rc.todo.PROMPT_TEMPLATE)
                    # 发送等待标志
                    await websocket.send_text("[Pending]")
                    user_input = await websocket.receive_text()
                    full_reply_content = user_input
                    human_str_end = f'【{single_role._setting} 已完成 {single_role.rc.todo.desc}】'
                    await websocket.send_text(human_str_end)
                else:
                    
                    # if not response:
                    #     await websocket.send_text("本轮不发言！")
                    #     full_reply_content="本轮不发言！"
                    # elif isinstance(response, str):
                    #     full_reply_content=response

                    s = f"【{single_role._setting} 在做 : {single_role.rc.todo.desc}】"
                    await websocket.send_text("[start]")
                    # await websocket.send_text(s)
                    try:
                        # 可能引发异常的代码
                        full_reply_content = await single_role.rc.todo.run(single_role.rc.history, websocket, user_name, taskid, file_metadata)
                    except Exception as e:
                        # 使用traceback.print_exc()来打印异常堆栈信息
                        traceback.print_exc()
                        print(f"代码出错，请查看日志: {e}")
                        workflow_failed = True
                        if isinstance(e, ValueError):
                            await websocket.send_text(
                                "### 需要补充生成条件\n\n"
                                f"{e}\n\n"
                                "收到元素体系后，服务将继续执行候选结构生成、稳定性初筛和已知竞争相比较。"
                            )
                        else:
                            await websocket.send_text(
                                "### 新材料生成未能启动\n\n"
                                "服务在初始化生成流程时遇到异常，已记录详细日志；请稍后重试。"
                            )
                        # Balance the action's earlier ``[start]`` marker so
                        # the frontend does not remain in a running state.
                        await websocket.send_text("[end]")
                        break

                    status_match = re.match(r"\[\[WORKFLOW_STATUS:(ok|failed|unavailable|timeout)\]\]\s*", str(full_reply_content or ""))
                    if status_match:
                        workflow_failed = status_match.group(1) != "ok"
                        full_reply_content = str(full_reply_content)[status_match.end():]

                    await websocket.send_text("[end]")    
                    f = f'【{single_role._setting} 已经完成 : {single_role.rc.todo.desc}】'
                    # await websocket.send_text(f)
                
                if full_reply_content is None:
                    full_reply_content = ""
                elif not isinstance(full_reply_content, str):
                    full_reply_content = str(full_reply_content)

                msg = Message(
                    content=full_reply_content,
                    role=single_role.profile,
                    cause_by=single_role.rc.todo,
                    sent_from=single_role
                )              
                
                single_role.rc.memory.add(msg)
                single_role._set_state(state=-1)
                # Reset the next action to be taken.
                single_role.set_todo(None)
                # Send the response message to the Environment object to have it relay the message to the subscribers.
                single_role.publish_message(msg)
                break
    if workflow_failed:
        await websocket.send_text("【XXX 未完成: 新材料生成未得到可用候选，请查看失败原因与下一步建议】")
    else:
        await websocket.send_text("【XXX 已完成: xxxx】")

@app.websocket("/start")
@app.websocket("/new-material/start")
async def websocket_endpoint(websocket: WebSocket):

    team = Team()
    team.hire(
        [
            InorganicNewMaterialDiscoveryRole(),
        ]
    )
    await websocket.accept()
    try:
        # 接收初始化数据
        init_data = await websocket.receive_json()
        idea = init_data["idea"]
        n_round = int(len(team.env.roles))
        taskid = init_data["taskid"]
        user_name = init_data["user_name"]
        file_metadata = list(init_data["file_metadata"])
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
        await start_round(websocket, team, json.dumps(init_data, ensure_ascii=False), n_round, user_name, taskid, file_metadata)
    except WebSocketDisconnect:
        # 在这里可以添加当客户端断开连接时的处理逻辑
        print("客户端已经主动断开连接！")
    except Exception as e:
        exception_type, exception_value, exception_traceback = sys.exc_info()
        print(f"Exception Type: {exception_type.__name__}")
        print(f"Exception Message: {exception_value}")
    try:
        await websocket.close()
        print("正常关闭连接！")
    except Exception as e:
        print("非正常关闭连接！")

@app.get("/roles")
async def get_teams():
    team = Team()
    team.hire(
        [
            InorganicNewMaterialDiscoveryRole(),
        ]
    )
    # ``get_roles`` returns Role instances, not dictionaries.  Convert them
    # before enriching the API response with router metadata.
    roles = {}
    for role_name, role in team.env.get_roles().items():
        metadata = jsonable_encoder(role)
        if not isinstance(metadata, dict):
            metadata = {"name": str(role_name)}
        metadata["role_id"] = "inorganic_new_material_generation_v1"
        metadata["routing"] = {
            "service_id": "inorganic_new_material_generation",
            "priority": 2,
            "match_when": "请求含明确化学式或元素体系，并要求生成/发现/验证数据库外的新无机晶体。",
            "include_keywords": ["明确化学式", "元素体系", "全新材料", "新晶体", "新无机材料", "MatterGen", "晶体生成", "化学式生成", "数据库外材料"],
            "exclude_keywords": ["合金配比", "高熵合金", "HEA", "MPEA", "元素比例优化", "原子百分比", "已有材料查询", "商品材料", "牌号查询"],
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

    # 确保上传目录存在
    SAVE_DIR = os.path.join(UPLOAD_DIR, taskid)
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)
    
    try:
        # 将上传的文件保存到服务器
        for file in files:
            # 构建保存文件的完整路径
            file_path = os.path.join(SAVE_DIR, file.filename)
            contents = await file.read()
            with open(file_path, "wb") as f:
                f.write(contents)
        upload_results = ",".join([file.filename for file in files])
        
        return JSONResponse(content=f"文件 {upload_results} 上传成功", status_code=200)
    
    except HTTPException as http_exc:
        # 如果是已知的HTTP异常，直接抛出
        raise http_exc
    
    except Exception as e:
        # 处理其他异常情况
        return JSONResponse(content={"error": str(e)}, status_code=500)
 

@app.post("/files")
def list_files(taskid: str = Form(...)):
    # 构建文件夹路径
    folder_path = os.path.join("upload", taskid)
    
    # 检查路径是否存在
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    # 获取文件夹中的文件列表
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    
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
        try:
            constraint_from_payload(payload)
        except ValueError as exc:
            if "无法确定待生成的元素体系" not in str(exc):
                raise
            enriched_payload = await enrich_payload_with_llm_elements(payload)
            if not enriched_payload:
                raise
            payload = enriched_payload
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
        try:
            return constraint_from_payload(payload).to_dict()
        except ValueError as exc:
            if "无法确定待生成的元素体系" not in str(exc):
                raise
            enriched_payload = await enrich_payload_with_llm_elements(payload)
            if not enriched_payload:
                raise
            return constraint_from_payload(enriched_payload).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/new-material/tasks/{taskid}")
async def get_new_material_task(taskid: str):
    """Return the final manifest, or durable in-progress state for polling clients."""
    if not taskid or taskid in {".", ".."} or "/" in taskid or "\\" in taskid:
        raise HTTPException(status_code=422, detail="invalid taskid")
    manifest = NEW_MATERIAL_RESULTS_ROOT / taskid / "new_material_pipeline_manifest.json"
    if manifest.exists():
        return JSONResponse(content=json.loads(manifest.read_text(encoding="utf-8")))
    progress = NEW_MATERIAL_RESULTS_ROOT / taskid / "progress.json"
    if progress.exists():
        return JSONResponse(content=json.loads(progress.read_text(encoding="utf-8")), status_code=202)
    raise HTTPException(status_code=404, detail="task manifest not found")


@app.get("/new-material/tasks/{taskid}/assets/{asset_name}")
async def get_new_material_asset(taskid: str, asset_name: str):
    """Serve a rendered asset from this service to avoid browser/OSS URL failures."""
    if (
        not taskid or taskid in {".", ".."} or "/" in taskid or "\\" in taskid
        or not asset_name or Path(asset_name).name != asset_name
    ):
        raise HTTPException(status_code=422, detail="invalid asset path")
    asset = NEW_MATERIAL_RESULTS_ROOT / taskid / "presentation" / asset_name
    if not asset.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(asset, filename=asset.name, content_disposition_type="inline")

if __name__ == "__main__":
    uvicorn.run(app='main:app', host="0.0.0.0", port=int(PORT), reload=False)
