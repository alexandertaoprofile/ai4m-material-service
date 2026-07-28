# -*- coding: utf-8 -*-
import os
import re
import sys
import asyncio
import subprocess
import tempfile
import io
import base64
import json
import requests
import logging
import traceback
import base64
from typing import List, Dict, Tuple, Optional
from sentence_transformers import CrossEncoder
import numpy as np
from fastapi.concurrency import run_in_threadpool
import uuid
from alpha.team import Team
from alpha.roles import Role
from alpha.logs import logger
from alpha.schema import Message
from alpha.actions import Action, UserRequirement
import mimetypes
from src.llm_utils import SeLLM
from src.llm_utils import load_config
from pydantic import PrivateAttr
import asyncio
from dotenv import load_dotenv
from typing import Union,  Any
import datetime
load_dotenv()
today = datetime.datetime.now().strftime("%Y%m%d")
logger.configure(handlers=[
    {"sink": sys.stdout, "level": "INFO"},
    {"sink": f"logs/{today}.txt", "level": "INFO", "enqueue": True}
])
from alpha.schema import Message
# from src.oss_utils import download_to_file, oss_upload_by_path, get_image_url
from src.storage_utils import *
import uuid
from datetime import datetime



os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:10240"

# 读取环境变量
server_base = os.getenv('server_base')
config = load_config("config/config.yaml")
backend_url = config["BACKEND_URL"]
source_path = config['SOURCE_CODE_PATH']

minio_addr = "http://36.103.203.113:2300"
https_vip_addr = "http://36.103.203.113:2300"

base_dir = '/data/XIMUAlpha_MNS/src'
########################################
# 工具函数
########################################

# 修改正则，提取所有 python 代码块
CODE_BLOCK_PATTERN = re.compile(
    r"```python(.*?)```",
    re.DOTALL | re.IGNORECASE
)

###json 格式化###
def build_payload(data, type_: str = "chat", request_id: str = None, meta: dict = None) -> dict:
    """
    将任意输出打包成统一 JSON 格式，供前端解析。

    参数:
        data: 任意需要返回的数据（文本/图片URL/参数字典等）
        type_: 输出类型 (chat, image, parameters, error, progress,root ...)
        request_id: 可选，任务请求的唯一ID；若为空则自动生成
        meta: 可选，附加元信息（如 case_id, step, tags 等）

    返回:
        dict: 标准化 JSON payload
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    payload = {
        "version": "1.0.0",
        "agent": "XIMUAlpha_MNS",
        "request_id": request_id,
        "time": datetime.datetime.now().isoformat(),
        "type": type_,
        "data": data
    }

    if meta:
        payload["meta"] = meta

    return payload


########################################
# CodeRetriever

class CodeRetriever:
    """
    负责加载项目结构信息，并支持项目级检索
    """

    def __init__(self, 
                 json_file_path: str = "./src/MNS_CaseHub/registry/dataset_en.json",
                 reranker_model_path: str = "/mnt/sdb/bge/bge-reranker-large",
                 score_threshold: float = 0.01):
        
        self.json_file_path = json_file_path
        self.reranker_model_path = reranker_model_path
        self.score_threshold = score_threshold

        # 项目存储
        self.projects: List[Dict[str, Any]] = []
        
        #建立索引
        self.path_desc_cache: Dict[str, str] = {}          # 全局: "PINN4Science/.../xxx" -> description
        self._project_desc_indexed: Dict[int, bool] = {}   # 每个 project_idx 是否已建立索引
        # 1. 加载所有项目
        self._load_projects()

        # 2. 初始化 reranker
        try:
            self.reranker = CrossEncoder(self.reranker_model_path)
            logger.info(f"[CodeRetriever] 成功加载 reranker: {self.reranker_model_path}")
        except Exception as e:
            logger.exception(f"[CodeRetriever] reranker 加载失败: {str(e)}")
            self.reranker = None
    

    def _load_projects(self) -> None:
        """读取 JSON，提取顶层 cases，使用 parameters 字段替换原 io"""
        if not os.path.exists(self.json_file_path):
            logger.error(f"[CodeRetriever] JSON 文件不存在: {self.json_file_path}")
            return

        try:
            with open(self.json_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._json_root = data
            projects = []

            for c in data.get("cases", []):
                domain = c.get("domain", "")
                name   = c.get("name", c.get("id", ""))
                desc   = c.get("description", "")
                paths  = c.get("paths", {})
                summary = c.get("summary", "")
                project_root = paths.get("project_root", "")
                main_entry   = paths.get("main_entry", "")

                projects.append({
                    "domain": domain,
                    "name": name,
                    "id": c.get("id", ""),
                    "tags": c.get("tags", []),
                    "description": desc,
                    "project_root": project_root,
                    "main_entry": main_entry,
                    "parameters": c.get("parameters", {}),
                    "summary": summary
                })

            self.projects = projects
            logger.info(f"[CodeRetriever] 已加载项目数量: {len(self.projects)}")
            for proj in self.projects:
                print(f"[Project] {proj['domain']} / {proj['name']}  -> {proj['main_entry']}")
        except Exception as e:
            logger.exception(f"[CodeRetriever] 加载 JSON 出错: {e}")

    def find_matching_project(self, query: str):
        """
        使用 CrossEncoder 在已加载的 self.projects 上打分，返回最匹配的项目。
        仅使用 domain + name 作为检索文本。
        返回: (project_dict or None, score: float, best_idx: int or None)
        """
        if not self.projects:
            logger.warning("[find_matching_project] 项目列表为空")
            return None, 0.0, None

        if self.reranker is None:
            logger.error("[find_matching_project] reranker 未初始化")
            return None, 0.0, None

        if not isinstance(query, str):
            query = str(query)

        # 只拼接 domain + name
        texts = [f"{p.get('domain','')} | {p.get('name','')}" for p in self.projects]
        pairs = [[query, t] for t in texts]

        try:
            scores = self.reranker.predict(pairs)
            scores = np.asarray(scores, dtype=float)
            if scores.size == 0:
                logger.warning("[find_matching_project] reranker 返回空分数")
                return None, 0.0, None

            best_idx = int(np.nanargmax(scores))
            best_score = float(scores[best_idx])
            best_proj = self.projects[best_idx]

            logger.info(
                f"[find_matching_project] Top1: {best_proj.get('domain','')}/{best_proj.get('name','')} "
                f"| score={best_score:.4f} | idx={best_idx}"
            )

            return best_proj, best_score, best_idx
        except Exception as e:
            logger.exception(f"[find_matching_project] reranker 评分异常: {e}")
            return None, 0.0, None

    def get_parameters(self, idx: int) -> Optional[dict]:
        if 0 <= idx < len(self.projects):
            return self.projects[idx].get("parameters", {})
        logger.warning(f"[get_parameters_by_index] 无效项目索引: {idx}")
        return None

    def get_main_entry(self, idx: int) -> Optional[str]:
        """
        根据项目索引 idx 返回 main_entry 路径（如 main.py 文件）
        """
        if 0 <= idx < len(self.projects):
            return self.projects[idx].get("main_entry", "")
        
        logger.warning(f"[get_main_entry] 无效项目索引: {idx}")
        return None
    
    def get_root_path(self, idx: int) -> Optional[str]:
        """
        根据项目索引 idx 返回 project_root 路径
        """
        if 0 <= idx < len(self.projects):
            return self.projects[idx].get("project_root", "")
        
        logger.warning(f"[get_root_path] 无效项目索引: {idx}")
        return None

    def get_summary(self, idx: int) -> Optional[str]:
        """
        根据项目索引 idx 返回 summary 字段
        """
        if 0 <= idx < len(self.projects):
            return self.projects[idx].get("summary", "")
        
        logger.warning(f"[get_summary] 无效项目索引: {idx}")
        return None

########################################
# Coding Action 模块
# - 功能：根据用户输入生成可运行的代码、选择模型脚本、执行与反馈运行结果
# - 支持 reranker 和 fallback 模式选择主程序入口
# - 执行模式支持 quick/train，数据支持 simul/load
########################################

class Coding(Action):
    # 智能体名称
    name: str = "XIMUAlpha_MNS"
    # 智能体简要描述
    desc: str =(
        "XIMUAlpha工业平台·微纳米系统Agent：以结构化JSON为唯一输出，"
        "专注模型检索、任务调度与结果拼装，服务器件设计/仿真/加工/质控等生产问题。"
        "倾向少文本，不进行长篇解释。"
    )

    _code_retriever: CodeRetriever = PrivateAttr(default = None)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    # XIMU_MNS_PROMPT_TEMPLATE: str = """
    #     你是工业智能平台 XIMUAlpha_MNS 的领域智能体。
    #     你的职责是解决微纳米系统（MNS）与微纳米器件/半导体设备相关的工业问题，
    #     包括但不限于器件设计、工艺仿真、制造加工、性能分析、质控与优化。
    #     ---
    #     用户提出的问题如下：

    #     {query}
    #     ---

    #     {file_info}
    #     ---
    #     回答要求：
    #     1. 风格定位：
    #     - 避免闲聊或科普风格；
    #     - 使用专业、工业化的表达方式，类似技术报告或实验室测试文档；
    #     - 输出应条理清晰，分段、分标题，必要时包含列表、表格、公式。

    #     2. 技术深度：
    #     - 使用专业术语与标准化表达；
    #     - 在可能时加入公式（LaTeX 格式）、参数范围、数值示例；
    #     - 涉及数据时，应给出数值结果或表格，而不是泛泛的描述。

    #     3. 文件输入处理（如有）：
    #     - 用户可能上传以下两类文件：
    #     a) 参数/设计需求文件：需要解析并提取设计目标、材料参数、几何结构、工艺条件等；
    #     b) 数据文件：可能包含实验测量、仿真输出、频谱/波形等，需要进行清洗、统计或拟合建模。
    #     - 若上传了文件，请先说明其结构（字段/格式），再提炼出关键物理量、约束条件、分析指标；
    #     - 最后明确这些数据如何用于反演、建模或诊断。

    #     4. 输出格式：
    #     - 输出直接为文本，由系统自动封装成 JSON；
    #     - 不需输出 JSON 格式；
    #     - 保持输出结构清晰、专业，整体风格类似工程技术简报或工业分析报告。

    #     5. 专业性优先：
    #     - 输出面向工程师/科研人员；
    #     - 若存在信息缺失，应指出所缺参数/数据，并避免生成虚构内容。

    #     目标：
    #     - 通过结构化、数据驱动、专业化的技术性回答，辅助用户在 MNS 场景中完成器件设计与仿真优化任务。
    #     """

    # XIMU_MNS_PREPROCESS_PROMPT:str = """
    #     你是 XIMUAlpha_MNS 平台中的结构识别模块，任务是从用户提供的问题和工程资料中提取参数反演任务的基本建模结构。

    #     ---

    #     用户问题如下：
    #     {query}

    #     ---

    #     以下为工程资料，仅供你理解建模背景与物理量定义：
    #     {file_info}

    #     ---

    #     输出要求：

    #     - 直接给出参数反演问题中涉及的控制变量、目标参数、频域表达式、建模所需参数表；
    #     - **禁止输出如下内容**：标题、注释、prompt标签、工程手册格式等字样；
    #     - **禁止**使用“本案例、本系统、本项目、本文、我们”等代词或引用；
    #     - 不得说明如何实现或训练，只需定义公式与量纲；
    #     - 所有内容以表格或 LaTeX 数学表达为主，极简语句辅助组织；
    #     - 表格要求包含：符号、物理量、单位、取值或范围；
    #     - 禁止输出任何“总结性语言”或“建议性结尾”。
    #     - 回答中每一段或每个结构块结束后必须加换行符，以保持清晰结构。

    #     你的目标是提取出参数反演任务的必要结构定义，用于后续建模步骤调用。  

    #     """
    
    # XIMU_MNS_FINAL_ANSWER_PROMPT:str = """
    #     你是 XIMUAlpha_MNS 平台中的工程答复模块，任务是基于问题结构识别结果与结构化参数信息，补充并生成完整的反演建模配置说明。

    #     ---

    #     用户问题结构定义如下（由前置模块提供）：
    #     {preprocess_answer}
    #     ---
    #     该任务的工程目标描述如下：
    #     {summary}

    #     ---
    #     该任务涉及的结构化参数信息如下：
    #     {parameters}

    #     ---

    #     输出要求：

    #     - 禁止重复或重新定义任何已经在 preprocess_answer 中出现的内容；
    #     - 输出内容仅限于补充工程控制条件、边界约束、设计目标、环境参数、材料工艺、部署条件等；
    #     - 禁止使用“本系统、该模型、我们提出的、用于”等表述；
    #     - 禁止回答建议或解释，不做结论性语句；
    #     - 使用表格 + 简洁段落，无需编号、无标题、无注释；
    #     - 所有量纲应一致，值域范围必须具体；所有控制量需体现物理意义与约束逻辑；
    #     - 所有内容必须使用 **格式清晰的表格或公式进行组织，禁止连续性文字描述**；
    #     - 所有表格与公式需**居中对齐显示**，以确保工程输出格式规范性；
    #     - 回答中每一段或每个结构块结束后必须加换行符，以保持清晰结构。

    #     最终输出将作为反演模型部署前的任务配置文件片段，要求结构清晰、格式规范、内容完整。

    #     """

    XIMU_MNS_ENGINEERING_PROMPT :str= """
        你是 XIMUAlpha_MNS 平台中的结构识别模块，任务是从用户提供的问题、工程资料、工程目标说明以及结构化参数中，构建完整的参数反演任务定义与建模配置内容。
        注意: 要求英文回答
        ---

        用户问题如下：
        {query}

        ---

        以下为工程资料，仅供你理解建模背景与物理量定义：
        {file_info}

        ---

        该任务的工程目标描述如下：
        {summary}

        ---

        该任务涉及的结构化参数信息如下：
        {parameters}

        ---

        输出要求：

        - 明确给出参数反演任务中涉及的控制变量、目标参数、频域表达式、物理模型公式与约束条件；
        - 提取建模所需的结构参数、材料参数、电气激励边界、环境边界等内容；
        - 所有输出内容必须通过结构化表格或 LaTeX 数学表达式给出，禁止使用连续性文字描述；
        - 表格内容需包括：符号、物理量、单位、取值或范围、功能说明；
        - 所有表格与公式必须**居中对齐**，格式清晰，结构分明；
        - **禁止输出如下内容**：标题、注释、prompt标签、工程手册格式等字样；
        - **禁止使用**“本案例、本系统、本项目、本文、我们”等代词或引用；
        - 不得说明如何实现、建模或训练，仅聚焦于物理量定义、工程约束与反演目标；
        - 禁止使用数字编号（如 "1.", "2." 等），所有内容段落请自然分段表达；
        - 禁止总结性语言、建议性语言或结尾说明；
        - 回答中每一段或每个结构块结束后必须加换行符，以保持清晰结构。
        - 请用英文回答问题
        """


    #懒加载，初始化Code_retriever
    def _get_code_retriever(self) -> CodeRetriever:
        """懒初始化 CodeRetriever"""
        if self._code_retriever is None:
            import time
            t0 = time.time()
            print("[DEBUG] 初始化 CodeRetriever 中...")
            self._code_retriever = CodeRetriever()  # 使用默认参数
            print(f"[DEBUG] CodeRetriever 初始化完成，用时 {time.time() - t0:.2f} 秒")
        return self._code_retriever

    # 流式发送 LLM 响应
    async def _stream_llm_response(self, llm, messages, websocket=None) -> str:
        import sys
        import asyncio
        import openai
        import httpcore
        import httpx

        collected_chunks = []
        retries = 0
        max_retries = 3
        stream_res = None

        # ===== 1) 先获取流（带重试）=====
        while retries < max_retries:
            try:
                # 如果 llm 支持显式 stream 参数，可加上 stream=True
                stream_res = await llm.acompletion_text(messages, timeout=30)
                break
            except (openai.APITimeoutError, httpcore.ReadTimeout, httpx.ReadTimeout) as e:
                retries += 1
                logger.warning(f"[LLM_Stream-LOG] 请求超时，重试 {retries}/{max_retries}: {type(e).__name__}")
                await asyncio.sleep(1.0 * retries)
            except Exception as e:
                logger.exception(f"[LLM_Stream-LOG] LLM 请求异常: {e!s}")
                if retries < max_retries - 1:
                    retries += 1
                    await asyncio.sleep(0.5)
                    continue
                raise

        if stream_res is None:
            logger.error("[LLM_Stream-LOG] 达到最大重试次数，未获得 LLM 响应")
            raise TimeoutError("LLM 请求超时，已放弃重试")

        # ===== 2) 逐 chunk 读取（兼容 3.10-，使用 wait_for 包装 __anext__）=====
        chunk_timeout = 30.0  # 每个 chunk 的超时时间（秒）
        max_total_chars = 2_000_000  # 安全阈值，防止意外的无限流
        total_chars = 0

        ait = stream_res.__aiter__()  # 显式拿到异步迭代器
        logger.info("[LLM_Stream-LOG] 开始流式读取...")

        try:
            while True:
                try:
                    # Python 3.10 及以下用 wait_for + __anext__ 实现“按 chunk 超时”
                    chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
                except asyncio.TimeoutError:
                    logger.error("[LLM_Stream-LOG] 流式读取超时（等待下一个 chunk 超过限制）")
                    if websocket and websocket.client_state.name == "CONNECTED":
                        await websocket.send_text("\n❗ 大模型响应超时，已收集部分结果。\n")
                    return "".join(collected_chunks)
                except StopAsyncIteration:
                    # 正常结束
                    break

                # 解析内容（按 OpenAI Chat Completions 风格）
                chunk_msg = ""
                try:
                    if getattr(chunk, "choices", None):
                        choice0 = chunk.choices[0]
                        delta = getattr(choice0, "delta", None)
                        if delta:
                            chunk_msg = getattr(delta, "content", "") or ""
                except Exception as parse_e:
                    logger.exception(f"[LLM_Stream-LOG] 解析 chunk 异常: {parse_e!s}")

                if chunk_msg:
                    collected_chunks.append(chunk_msg)
                    total_chars += len(chunk_msg)

                    if websocket and websocket.client_state.name == "CONNECTED":
                        await websocket.send_text(chunk_msg)
                    elif websocket:
                        logger.warning("[LLM_Stream-LOG] WebSocket 已关闭，终止发送")
                        break

                    # 防御性上限
                    if total_chars >= max_total_chars:
                        logger.warning("[LLM_Stream-LOG] 达到最大输出字符上限，终止流式读取")
                        break

        except (httpcore.ReadTimeout, httpx.ReadTimeout) as e:
            logger.exception(f"[LLM_Stream-LOG] 网络读取超时: {e!s}")
            if websocket and websocket.client_state.name == "CONNECTED":
                await websocket.send_text("\n❗ 网络连接超时，已收集部分结果。\n")
            return "".join(collected_chunks)
        except Exception as e:
            logger.exception(f"[LLM_Stream-LOG] LLM Stream 异常: {e!s}")
            if websocket and websocket.client_state.name == "CONNECTED":
                await websocket.send_text("\n❗ 大模型响应异常，已终止流式传输。\n")
            raise
        finally:
            # 尽可能优雅关闭流
            try:
                aclose = getattr(stream_res, "aclose", None)
                if callable(aclose):
                    await aclose()
            except Exception as e:
                logger.debug(f"[LLM_Stream-LOG] 关闭流时发生异常: {e!s}")

        logger.info(f"[LLM_Stream-LOG] 收集到 {len(collected_chunks)} 段输出，总长 {sum(len(c) for c in collected_chunks)} 字符")
        return "".join(collected_chunks)


    # format_instruction 方法   
    async def format_instruction(self, instruction: str, llm) -> str:
        """
        从用户指令中识别最可能匹配的【领域 | 项目名称】，用于 projects 层级检索。
        目标输出：<领域> | <项目名称>
        - 领域与项目名称之间以一个空格+竖线+空格分隔（" | "）
        - 不包含 description、不追加任何解释或多余字符
        """
        prompt = f"""
            你是一个熟悉 AI4PDE 与物理建模语义的助手。你的任务是：从给定的自然语言指令中，识别并“提炼”出**最可能匹配的 领域 与 项目名称**，并**仅输出一行**结果，格式严格为：

            <领域> | <项目名称>

            【输入形态与取数规则】
            1) 输入可能是一段普通文本，也可能是一个多轮消息列表（例如 JSON 数组，包含多条 role="user/assistant" 的消息）。
            2) 如果是多轮消息列表，**只使用最后一条 role 为 "user" 的消息的 "content"** 作为语义依据，其余内容全部忽略。
            3) 如果是普通文本，直接基于该文本进行理解与提炼。

            【提炼与命名规范】
            A. “项目名称”从用户语句中抽取与“任务/方程/场景/对象/方法”最相关且**语义最具体**的短语：
            - 优先保留维度（2D/3D）、对象限定（如 卫星结构）、具体方程名（波动方程/Poisson/Maxwell/Navier-Stokes/KdV/热传导/弹性力学 等）、或方法前缀（PINN/gPINN/XPINN 等）。
            B. 允许合理变体（顺序变换、方法前缀、下划线连接），但要与用户语义高度一致。
            C. 统一将空格、顿号等分隔符**替换为下划线 `_`**，避免冗余功能词（如“我想”“请问”“求解一下”）。
            D. 若出现多个候选短语，选择**信息量最大且不矛盾**的一个作为“项目名称”。

            【“领域”选择原则】
            - 领域用于匹配 projects 的 domain 字段，尽量从下列常见集合中择一（若语义明确但未在集合中，也可给出更贴切的领域词，但要保持简洁）：
            - MNS
            【输出格式（必须严格一致）】
            <领域> | <项目名称>

            - 仅一行、仅此内容；不要附加任何说明、前后缀或代码块标记。
            - 不要包含路径、文件名、描述、引号或其他符号。
            - “项目名称”按上述命名规范规整（使用下划线 `_`）。


            【禁止事项】
            - 禁止输出除结果本体以外的任何文字（如“下面是结果：”等）。
            - 禁止多行输出或多个候选；只能输出**一行唯一结果**。
            - 禁止输出文件名（如 *_main.py）或描述串。
            - 禁止添加无关标签、路径或环境信息。
            

            请基于以下指令生成格式化结果（仅返回结果本体，一行）：
            "{instruction}"
            """
                # 构造消息并请求 LLM
        messages = [llm._default_system_msg(), llm._user_msg(prompt)]
        response = await llm.acompletion_text(messages, timeout=10)

        # 流式拼接
        summary = []
        async for chunk in response:
            chunk_msg = chunk.choices[0].delta.content or ""
            if chunk_msg:
                summary.append(chunk_msg)

        # 返回一行、去首尾空白
        return "".join(summary).strip()

    #匹配对应的项目
    async def matching_project(self, instruction: str, llm) -> Tuple[Optional[Dict[str, Any]], Optional[int], float, str]:
        """
        基于用户指令进行项目级匹配：
        1) 调用 format_instruction 将自然语言压缩为 'domain | name | desc' 查询串
        2) 使用 CodeRetriever.find_matching_project 做 CrossEncoder 重排
        返回: (best_project_dict 或 None, best_idx 或 None, score: float, query_str: str)
        """
        # 1) 归一化查询串
        query_str = await self.format_instruction(instruction, llm)
        # 打印原始指令（便于调试）
        print(f"[Coding-Print] 📝 格式化指令为领域|项目名称: {query_str}")
        # 2) 项目级匹配
        retriever = self._get_code_retriever()
        best_proj, best_score, best_idx = retriever.find_matching_project(query_str)

        # 3) 日志与阈值判断（仅记录，不在此处拒绝；由上层决定是否接受）
        if best_proj:
            logger.info(
                f"[matching_project] query='{query_str}' -> "
                f"best=({best_proj.get('domain','')}/{best_proj.get('name','')}) "
                f"score={best_score:.4f} idx={best_idx}"
            )

            print(f"[matching_project-Print] query='{query_str}' -> "
                  f"best=({best_proj.get('domain','')}/{best_proj.get('name','')}) "
                  f"score={best_score:.4f} idx={best_idx}")
        else:
            logger.info(f"[matching_project] 未匹配到项目 | query='{query_str}'")

            print(f"[matching_project-Print] 未匹配到项目 | query='{query_str}'")

        return best_proj, best_idx, float(best_score or 0.0), query_str
    
    #运行本地脚本
    @staticmethod
    async def run_local_model(entry_path: str, websocket, source_path: str):
        """
        运行指定项目的主脚本（main_entry），并将 stdout 实时推送到前端 websocket。

        参数:
            entry_path: 相对路径，如 "PINN4Science/MNS/Cantilever/main.py"
            websocket: WebSocket 对象，用于 send_text
            source_path: 项目根路径，如 "/data/AI4PDE"（可选）

        返回:
            (stdout_str, stderr_str)
        """
        try:
            full_path = os.path.join(source_path, entry_path)
            if not os.path.exists(full_path):
                msg = f"\n❌ 脚本文件不存在: {full_path}"
                await websocket.send_text(msg)
                logger.error(f"[Run-LOG] {msg}")
                return "", msg

            # 构造运行命令
            cmd = f"python {full_path}"
            print(f"[Run] 🛠️ 正在执行命令: {cmd}")
            logger.info(f"[Run-LOG] 🛠️ 执行命令: {cmd}")

            # 启动子进程
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 实时读取 stdout，发送给前端
            async def stream_stdout(stream):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode().rstrip()
                    if decoded and websocket:
                        await websocket.send_text(decoded + "\n")

            await stream_stdout(process.stdout)

            # 处理 stderr
            stderr_output = await process.stderr.read()
            stderr_decoded = stderr_output.decode().strip()
            if stderr_decoded:
                logger.error(f"[Run-LOG] ❗ stderr 报错信息：\n{stderr_decoded}")
                print(f"[Run] ❗ stderr 报错信息：\n{stderr_decoded}")

            # 等待结束
            return_code = await process.wait()
            print(f"[Run] ✅ 脚本执行完成，退出码: {return_code}")
            logger.info(f"[Run-LOG] ✅ 脚本执行完成，退出码: {return_code}")
            return "", stderr_decoded

        except Exception as e:
            err_msg = f"\n❌ 本地执行失败: {str(e)}"
            if websocket:
                await websocket.send_text(err_msg)
            print(f"[Run] ❌ 异常捕获: {e}")
            logger.error(f"[Run-LOG] ❌ 异常捕获: {e}")
            return "", str(e)

    #发送结果到前端
    async def send_results_to_frontend(self,websocket, root_path: str, taskid: str):
        """
        遍历并上传项目 results 目录下所有图片文件，并发送到前端（markdown + json）

        参数:
            websocket: WebSocket 对象
            root_path: 当前项目的根目录路径
            taskid: 当前任务的唯一 ID（用于 OSS 路径与前端识别）
        """
        exts = {".png", ".jpg", ".jpeg", ".gif"}
        print(f"[Results] Source Path: {source_path}")
        logger.info(f"[send_results_to_frontend] Source Path: {source_path}")

        abs_root_path = os.path.join(source_path, root_path)
        results_dir = os.path.join(abs_root_path, "results")
        
        print(f"[Results] 结果目录: {results_dir}")
        logger.info(f"[send_results_to_frontend] 结果目录: {results_dir}")
        
        if not os.path.exists(results_dir):
            logger.warning(f"[send_results_to_frontend] ❌ results 目录不存在: {results_dir}")
            await websocket.send_text("⚠️ 当前项目未生成 results 目录。")
            return

        try:
            image_files = sorted(
                f for f in os.listdir(results_dir)
                if os.path.isfile(os.path.join(results_dir, f))
                and os.path.splitext(f)[1].lower() in exts
            )
        except Exception as e:
            logger.exception(f"[send_results_to_frontend] 遍历 results 失败: {e}")
            return

        if not image_files:
            logger.info(f"[send_results_to_frontend] ⚠️ results 中无可用图片: {results_dir}")
            await websocket.send_text("⚠️ 仿真结果中未找到可展示的图片。")
            return
        
        # await websocket.send_text("\n **📊 可视化结果已生成** \n")
        data = []
        for fname in image_files:
            abs_img = os.path.join(results_dir, fname)
            rel_path = os.path.relpath(abs_img, start=root_path).replace("\\", "/")
            oss_path = f"XIMUAlpha_MNS/{taskid}/{fname}"

            try:
                with open(abs_img, "rb") as f:
                    file_bytes = f.read()

                result = await oss_upload("alpha", oss_path, file_bytes)

                if result.get("status") != 200:
                    logger.error(f"[send_results_to_frontend] ❗ 上传失败: {fname}, 状态码: {result.get('status')}")
                    await websocket.send_text(f"❗ 上传 `{fname}` 到云端失败，请稍后重试。")
                    continue

                # 获取可访问链接
                oss_url = get_image_url("alpha", oss_path)
                print(f"[Results] 图片上传成功，URL: {oss_url}")
                # 替换为 https 地址
                if oss_url.startswith(minio_addr):
                    oss_url = oss_url.replace(minio_addr, https_vip_addr, 1)

                # ✅ 1. 发送 markdown 格式到聊天区
                # await websocket.send_text(f"\n ![{fname}]({oss_url})\n")

                # ✅ 2. 构建 JSON payload 并发送到 UI 展示图像模块
                    # 收集入 list
                data.append({
                    "src": oss_url,
                    "key": fname,
                    "desc": f"{os.path.splitext(fname)[0]}"
                })

                logger.info(f"[send_results_to_frontend] ✅ 图片已上传并发送: {fname} -> {oss_url}")
                print("上传图片文件的路径是", oss_url)

            except Exception as e:
                logger.exception(f"[send_results_to_frontend] 处理文件失败: {fname} | {e}")
                await websocket.send_text(f"❌ 发送图片 `{fname}` 时出错：{str(e)}")
        if data:
            payload = build_payload(
                data=data,
                type_="image",
                request_id=taskid
            )
            await websocket.send_json(payload)
            print(f"[Results] ✅ 已发送图片 JSON payload，图片数量: {len(data)}")
            logger.info(f"[send_results_to_frontend] ✅ 已发送图片 JSON payload，图片数量: {len(data)}")


    #读取案例的readme文件
    def read_case_readme(self,path: str) -> str:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            return content
        except UnicodeDecodeError:
            with open(path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
            return content
        except Exception as e:
            logger.warning(f"[read_case_readme] 无法读取 README 文件: {e}")
            return "（README 文件读取失败）"

    #拼接输出流，写run函数
    async def run(self, instruction: str, *args):
        """
        Action入口：
        - instruction: 用户问题
        - args[0]: websocket
        - args[1]: user_name
        - args[2]: taskid
        - args[3]: file_metadata (上传文件列表)
        """
        websocket = args[0]
        user_name, taskid, file_metadata = args[1], args[2], args[3]

        # 初始化 LLM 客户端
        config = load_config("config/config.yaml")
        llm = SeLLM(base_url=config["base_url_1"], api_key=config["api_key"])

        # ✅ 打印当前用户指令与执行模式
        print(f"[Coding] 💬 用户指令: {instruction}")
        logger.info(f"[Coding-LOG] 💬 用户指令: {instruction}")

        # Step 1 . 第一条：结构化 progress 消息（前端 UI 用）
        progress_msg = build_payload(
            data={
                    "id": 'upload',
                    "icon": '📥',
                    "title": '上传设计需求',
                    "status": 'completed',
                    "description": '用户已提交设计说明文档与结构草图。'
            },
            type_="progress",
            request_id=taskid
        )
        await websocket.send_json(progress_msg)
        print(f"[Progress] 发送 progress 消息: {progress_msg}")
        logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")

        # Step 1 .第二条：Markdown 格式的用户提示文本（对话框用）
        markdown_msg = "\n **📁 已接收上传信息，正在尝试解析...** \n "
        markdown_msg_en = "\n **📁 Upload received. Attempting to parse...** \n "
        await websocket.send_text(markdown_msg_en)
        print(f"[Markdown] 发送 markdown 消息: {markdown_msg_en}")
        logger.info(f"[Markdown-LOG] 发送 markdown 消息: {markdown_msg_en}")


        # Step 2 .构造 Prompt（暂时不填文件信息）
        # readme_path = "/data/XIMUAlpha_MNS/src/MNS_CaseHub/cases/微悬臂梁谐振器_几何参数wtlt反演/README.md"
        # readme_content = self.read_case_readme(readme_path)
        # prompt = self.XIMU_MNS_PREPROCESS_PROMPT.format(
        #     query=instruction,
        #     file_info=readme_content  # 来自第二步读取的 README.md 内容
        # )

        # # 调用 LLM，推理并流式输出
        # pre_context = await self._stream_llm_response(
        #         llm,
        #         [llm._default_system_msg(), llm._user_msg(prompt)],
        #         websocket
        #     )   

        # Step 3. 模型匹配提示（对话框提示）
        # await websocket.send_text(" \n **🧠 正在尝试匹配模型，请稍候...** \n")
        print(f"[Markdown] 发送模型匹配提示")
        logger.info(f"[Markdown-LOG] 发送模型匹配提示") 

        # Step 4. 调用匹配器 CodeRetriever（使用 reranker）
        best_proj, best_idx, best_score, query_str = await self.matching_project(instruction,llm)
        threshold = 0.35

        if best_proj and best_score >= threshold:
            logger.info(f"[Match] ✅ 匹配成功: {best_proj['name']} | Score={best_score:.4f}")
            print(f"[Match-Print] ✅ 匹配成功: {best_proj['name']} | Score={best_score:.4f}")
            
            readme_path = "/data/XIMUAlpha_MNS/src/MNS_CaseHub/cases/微悬臂梁谐振器_几何参数wtlt反演/README.md"
            readme_content = self.read_case_readme(readme_path)
            summary = self._get_code_retriever().get_summary(best_idx) or "（无案例总结）"
            parameters = self._get_code_retriever().get_parameters(best_idx) or {}
            


            # Step 5 .发送结构化进度：参数识别与提取阶段
            progress_msg = build_payload(
            data={
                    "id": 'recognition',
                    "icon": '🧠',
                    "title": '参数识别与提取',
                    "status": 'completed',
                    "description": '系统已识别结构类型为盘状谐振器，提取目标频率、工作温度、材料属性等。'
            },
                type_="progress",
                request_id=taskid
            )
            await websocket.send_json(progress_msg)

            # Step 5 .获取项目参数与入口路径
            params = self._get_code_retriever().get_parameters(idx=best_idx)
            entry_path = self._get_code_retriever().get_main_entry(best_idx)  # 如果后面需要执行 main

            # 打包参数为 payload
            param_msg = build_payload(
                data=params,
                type_="parameters",
                request_id=taskid
            )
            #Step 5 .发送参数到前端
            await websocket.send_json(param_msg)
            print(f"[Params] 发送参数 payload: {param_msg}")
            logger.info(f"[Params-LOG] 发送参数 payload: {param_msg}")

            # await self.run_local_model(entry_path, websocket, source_path=source_path)

            

            
            # Step 5.1 更新进度：几何建模
            progress_msg = build_payload(
                    data={
                            "id": 'modeling',
                            "icon": '📐',
                            "title": '几何建模',
                            "status": 'completed',
                            "description": '正在构建三维结构模型与边界条件，进行网格划分与建模检查。'
                        },
                        type_="progress",
                        request_id=taskid
                    )
            await websocket.send_json(progress_msg)
            print(f"[Progress] 发送 progress 消息: {progress_msg}")
            logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")

            
            # Step 5.2 更新进度：多物理场建模
            progress_msg = build_payload(
                    data={
                            "id": 'physics',
                            "icon": '🔬',
                            "title": '多物理场建模',
                            "status": 'completed',
                            "description": '待完成结构建模后，系统将匹配热、电、力场模型并构建耦合关系。'
                        },
                        type_="progress",
                        request_id=taskid
                    )
            await websocket.send_json(progress_msg)
            print(f"[Progress] 发送 progress 消息: {progress_msg}")
            logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")
            
            
            # Step 5.3 更新进度：模态仿真与数据处理
            progress_msg = build_payload(
                    data={
                            "id": 'simulation',
                            "icon": '⚙️',
                            "title": '模态仿真与数据处理',
                            "status": 'completed',
                            "description": '仿真任务排队中，将计算模态频率、峰值位移与应力分布等。'
                        },
                        type_="progress",
                        request_id=taskid
                    )
            await websocket.send_json(progress_msg)
            print(f"[Progress] 发送 progress 消息: {progress_msg}")
            logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")
            
            # 构造最终工程回答 Prompt
            final_prompt = self.XIMU_MNS_ENGINEERING_PROMPT.format(
                query=instruction,
                file_info=readme_content, 
                summary=summary,
                parameters=parameters
            )

            # # 调用 LLM，推理并流式输出
            await self._stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(final_prompt)],
                websocket
            )  
            # await websocket.send_text("\n **✅ 模型匹配成功，正在设置参数。请在仿真界面查看参数信息，并启动仿真程序** \n")

            
            # Step 7 更新进度：可视化结果
            progress_msg = build_payload(
                    data={
                            "id": 'visualization',
                            "icon": '📈',
                            "title": '可视化结果生成',
                            "status": 'completed',
                            "description": '将生成模态动画、3D结构响应图与参数分析图表。'
                        },
                        type_="progress",
                        request_id=taskid
                    )
            await websocket.send_json(progress_msg)
            print(f"[Progress] 发送 progress 消息: {progress_msg}")
            logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")

            # Step 6 .发送结果图
            root_path = self._get_code_retriever().get_root_path(best_idx)
            
            print(f"[Results] 准备发送结果图片，根目录: {root_path}")
            logger.info(f"[Results-LOG] 准备发送结果图片，根目录: {root_path}")

            await self.send_results_to_frontend(websocket, root_path, taskid)

            print(f"[Results] 结果图片发送完成")
            logger.info(f"[Results-LOG] 结果图片发送完成")

            
            #Step 7 更新进度：报告与模型导出
            progress_msg = build_payload(
                    data={
                            "id": 'export',
                            "icon": '📤',
                            "title": '报告与模型导出',
                            "status": 'completed',
                            "description": '最终导出 PDF 报告、动图、三维模型与结构数据文件。'
                        },
                        type_="progress",
                        request_id=taskid
                    )
            await websocket.send_json(progress_msg)
            print(f"[Progress] 发送 progress 消息: {progress_msg}")
            logger.info(f"[Progress-LOG] 发送 progress 消息: {progress_msg}")

            markdown_msg = "\n **✅ 仿真任务已完成，结果图与关键参数已生成，请在右侧模块查看。** \n"
            markdown_msg_en = "\n **✅ Simulation task completed. Result images and key parameters have been generated. Please check the right panel.** \n"

            await websocket.send_text(markdown_msg_en)



        else:
            logger.warning(f"[Match] ❌ 未匹配到合适模型 | Score={best_score:.4f}")
            print(f"[Match-Print] ❌ 未匹配到合适模型 | Score={best_score:.4f}")
            await websocket.send_text("\n **⚠️ 未识别到匹配模型，请检查描述是否清晰，或补充更多关键词** \n")
            return  # 提前中止后续流程




########################################
# 定义角色：XIMUAlpha_MNS
########################################

class XIMUAlpha_MNS(Role):
    """
    工业平台 · 微纳米系统（MNS）领域智能体。
    定位：面向微纳米器件的设计 / 仿真 / 加工 / 质控与产线优化等工业场景，
    以“结构化 JSON”为唯一对接载体，侧重“检索模型/算子 → 调度运行 → 拼装可渲染数据”。
    """
    # 对外展示名（前端/日志可见）
    name: str = "XIMUAlpha_MNS"
    # 简要画像（供框架/上游作为 system profile 使用）
    profile: str = (
        "XIMUAlpha工业平台·微纳米系统Agent：以结构化JSON为唯一输出，"
        "专注模型检索、任务调度与结果拼装，服务器件设计/仿真/加工/质控等生产问题。"
        "倾向少文本，不进行长篇解释。"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 保持不变
        self._watch([UserRequirement])
        self.set_actions([Coding])
    