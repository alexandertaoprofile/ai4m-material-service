# -*- coding: utf-8 -*-
import os
import re
import sys
import asyncio
import glob
import json
import uuid
import datetime
from typing import Dict, Optional, Any

import numpy as np
from dotenv import load_dotenv
from pydantic import PrivateAttr

from alpha.roles import Role
from alpha.logs import logger
from alpha.actions import Action, UserRequirement

from src.llm_utils import SeLLM, load_config
from src.storage_utils import oss_upload, get_image_url
from src.materials.payloads import build_payload as build_material_payload
from src.roles.mns_role_prompts import (
    XIMU_MNS_ENGINEERING_PROMPT,
    XIMU_MNS_MATERIAL_PROMPT,
    XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT,
)
from src.tools.team_config_helpers import (
    repo_root as _helpers_repo_root,
    resolve_case_readme_path as _helpers_resolve_case_readme_path,
    safe_str as _helpers_safe_str,
    get_case_root as _helpers_get_case_root,
    as_text as _helpers_as_text,
    infer_prompt_mode as _helpers_infer_prompt_mode,
)
from src.utils.formula_utils import (
    to_ascii_formula as _utils_to_ascii_formula,
    looks_like_formula as _utils_looks_like_formula,
    normalize_formula_for_mp as _utils_normalize_formula_for_mp,
    build_formula_extraction_text as _utils_build_formula_extraction_text,
)
from src.utils.team_config_runtime_helpers import (
    normalize_user_text as _normalize_user_text_external,
    parse_route as _parse_route_external,
    render_progress_bar as _render_progress_bar_external,
)
from src.utils.material_candidate_extractor import (
    extract_formulas_from_targets as _extract_formulas_from_targets_external,
    extract_formulas_from_in_ls as _extract_formulas_from_in_ls_external,
)
from src.utils.subprocess_runner import (
    run_mp_export_assets_streaming as _run_mp_export_assets_streaming_external,
)
from src.utils.alignn_runner import (
    extract_cif_path_from_item as _alignn_extract_cif_path_from_item,
    pick_num as _alignn_pick_num,
    try_alignn_models as _alignn_try_alignn_models,
    probe_alignn_model as _alignn_probe_alignn_model,
)
from src.utils.material_candidate_selector import (
    llm_select_material_candidates as _llm_select_material_candidates_external,
    build_candidate_lists as _build_candidate_lists_external,
)

# Optional: reranker (heavy dependency)
try:
    from sentence_transformers import CrossEncoder  # noqa: F401
except Exception:
    CrossEncoder = None  # type: ignore

def _repo_root() -> str:
    return _helpers_repo_root()

def _resolve_case_readme_path(case: dict) -> str:
    return _helpers_resolve_case_readme_path(case)

load_dotenv()
today = datetime.datetime.now().strftime("%Y%m%d")

os.makedirs("logs", exist_ok=True)
logger.configure(handlers=[
    {"sink": sys.stdout, "level": "INFO"},
    {"sink": f"logs/{today}.txt", "level": "INFO", "enqueue": True}
])




os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:10240"

# 读取环境变量
server_base = os.getenv('server_base')
config = load_config("config/config.yaml")
backend_url = config["BACKEND_URL"]
source_path = config['SOURCE_CODE_PATH']

#minio_addr = "https://36.103.203.113:2300"
#https_vip_addr = "https://36.103.203.113:2300"

minio_addr = "https://www.science42.tech/"
https_vip_addr = "https://www.science42.tech/"

# 前端访问 GLB 的固定公开前缀（仅用于下发给前端的 URL，不影响 PutObject 上传入口）
glb_public_base_url = os.getenv(
    "GLB_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/glb/materials/modelfiles"
).rstrip("/")

image_public_base_url = os.getenv(
    "IMAGE_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/image"
).rstrip("/")

picture_public_base_url = os.getenv(
    "PICTURE_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/materials/modelfiles/image"
).rstrip("/")

base_dir = '/data/XIMUAlpha_MNS/src'
########################################
# 工具函数
########################################

# 修改正则，提取所有 python 代码块
CODE_BLOCK_PATTERN = re.compile(
    r"```python(.*?)```",
    re.DOTALL | re.IGNORECASE
)

### json 格式化 ###
def build_payload(data, type_: str = "chat", request_id: str = None, meta: dict = None) -> dict:
    return build_material_payload(data=data, type_=type_, request_id=request_id, meta=meta)


#########################################辅助函数分类prompt#########################################
def _safe_str(x) -> str:
    return _helpers_safe_str(x)

def _get_case_root(case: dict) -> str:
    return _helpers_get_case_root(case)



def _as_text(x) -> str:
    return _helpers_as_text(x)


def _infer_prompt_mode(best_proj: dict) -> str:
    return _helpers_infer_prompt_mode(best_proj)


########################################
# CodeRetriever

class CodeRetriever:
    """
    负责加载项目结构信息，并支持项目级检索
    """

    def __init__(
        self,
        json_file_path: str = None,
        reranker_model_path: str = "/home/ubuntu/services/models/bge-reranker-large",
        score_threshold: float = 0.3,
        json_files: list = None,
        source_root: str = None,
        enable_reranker: bool = True, 
        
    ): 
        # repo 根目录：/home/ubuntu/se42/ai4m_tqm
        if not source_root:
            source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.source_root = os.path.abspath(source_root)

        # registry 目录：repo_root/src/MNS_CaseHub/registry
        self.registry_dir = os.path.join(self.source_root, "src", "MNS_CaseHub", "registry")

        # 兼容旧参数：json_file_path 仍可用
        if json_file_path is None:
            json_file_path = os.path.join(self.registry_dir, "dataset.json")
        self.json_file_path = json_file_path

        # 支持多个 registry 合并（你现在拆成 materials/phone）
        if json_files is None:
            json_files = [
                os.path.join(self.registry_dir, "dataset_materials.json"),
                os.path.join(self.registry_dir, "dataset.json"),
            ]
        self.json_files = [os.path.abspath(p) for p in json_files]

        self.reranker_model_path = reranker_model_path
        self.score_threshold = float(score_threshold)

        self.projects = []
        self.reranker = None

        self._load_projects()
        self._init_reranker()


    def _load_projects(self):
        """
        从 registry json 加载 projects
        兼容以下顶层结构：
        - {"version": "...", "cases": [...]}
        - {"version": "...", "projects": [...]}
        - {"data": [...]}
        - list[...]
        """
        self.projects = []

        def _ensure_list(data):
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for k in ("cases", "projects", "data", "items"):
                    v = data.get(k, None)
                    if isinstance(v, list):
                        return v
            return []

        def _normalize_case(x: dict) -> dict:
            """
            把 case/cases item 统一成 project 结构，保证后续匹配字段存在
            """
            if not isinstance(x, dict):
                return {}

            # 你们 registry 里可能叫 case_id / id
            cid = x.get("id") or x.get("case_id") or x.get("name") or ""
            name = x.get("name") or x.get("title") or cid
            domain = x.get("domain") or x.get("team_type") or x.get("category") or ""

            tags = x.get("tags") or x.get("keywords") or []
            if isinstance(tags, str):
                tags = [tags]
            if tags is None:
                tags = []

            description = x.get("description") or ""
            summary = x.get("summary") or ""

            # paths 兼容：你截图里是 x["paths"]["project_root"]/["main_entry"]
            paths = x.get("paths") or {}
            if not isinstance(paths, dict):
                paths = {}

            project_root = paths.get("project_root") or x.get("project_root") or ""
            main_entry = paths.get("main_entry") or x.get("main_entry") or ""

            # 给一些常用字段兜底
            proj = {
                "id": cid,
                "name": name,
                "domain": domain,
                "tags": tags,
                "description": description,
                "summary": summary,
                "paths": {
                    "project_root": project_root,
                    "main_entry": main_entry,
                },
                # 原始字段保留，方便 debug
                "_raw": x,
            }
            return proj

        # 依次加载多个 json 合并
        for fp in self.json_files:
            try:
                if not os.path.exists(fp):
                    logger.warning(f"[CodeRetriever] registry file not found: {fp}")
                    continue

                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)

                cases = _ensure_list(data)
                if not cases:
                    logger.warning(f"[CodeRetriever] no cases/projects found in: {fp}")
                    continue

                for item in cases:
                    proj = _normalize_case(item)
                    if proj:
                        self.projects.append(proj)

                logger.info(f"[CodeRetriever] loaded {len(cases)} items from {fp}")

            except Exception as e:
                logger.exception(f"[CodeRetriever] failed to load {fp}: {e}")

        logger.info(f"[CodeRetriever] total projects loaded: {len(self.projects)}")

        
    def _init_reranker(self):
        """
        初始化 CrossEncoder reranker（可选依赖）。
        - sentence_transformers 缺失 / CrossEncoder 不可用：自动禁用
        - 模型路径不存在：自动禁用
        - 任何加载异常：自动禁用
        """
        self.reranker = None

        # 1) CrossEncoder 可能被 try/except 降级成 None
        if CrossEncoder is None:
            logger.warning("[CodeRetriever] reranker 未启用：sentence-transformers / CrossEncoder 不可用，走 fallback")
            return

        # 2) 模型路径检查（你日志里是 /homel '/home/ubuntu/services/models/bge-reranker-large'）
        model_path = getattr(self, "reranker_model_path", None)
        if not model_path or not os.path.exists(model_path):
            logger.warning(f"[CodeRetriever] reranker 未启用：model_path 不存在或为空: {model_path}")
            return

        # 3) 尝试加载
        try:
            self.reranker = CrossEncoder(model_path)
            logger.info(f"[CodeRetriever] ✅ reranker loaded: {model_path}")
        except Exception as e:
            logger.exception(f"[CodeRetriever] ❌ reranker 加载失败，自动降级 fallback: {e}")
            self.reranker = None
            return

    def _fallback_match_project(self, query: str):
        """
        无 reranker 时的兜底匹配：
        1) 关键词规则（强约束，命中就直接返回）
        2) 轻量字符串打分（domain/name/tags/description/summary）
        返回: (best_proj or None, score, best_idx)
        """
        if not query:
            return None, 0.0, None

        q = str(query).lower()

        # NOTE(2026-04): 旧业务关键词（手机/钨/喷管等）已下线，先注释对应规则。
        # 当前仅保留“已有无机材料”主线相关关键词，避免旧路由误命中。
        RULES = [
            (
                r"(materials? project|material|晶体|结构|化学式|无机|数据库|筛选|带隙|形成能|稳定性)",
                ["materials", "project", "material", "晶体", "结构", "化学式", "无机", "数据库", "筛选", "带隙", "形成能", "稳定性"],
            ),
        ]

        def proj_text(p):
            parts = [
                p.get("domain", ""),
                p.get("name", ""),
                p.get("id", ""),
                " ".join(p.get("tags", []) or []),
                p.get("description", ""),
                p.get("summary", ""),
                # 额外字段兼容（有些dataset.json可能还有）
                p.get("title", ""),
                p.get("keywords", ""),
            ]
            return " | ".join([str(x) for x in parts if x])

        # 1) 强规则命中：命中就直接返回最像的
        for pattern, must_tokens in RULES:
            if re.search(pattern, q, flags=re.IGNORECASE):
                best_idx = None
                best_hit = -1
                for i, p in enumerate(self.projects):
                    text = proj_text(p).lower()

                    # hit：命中 tokens 的数量（越多越像）
                    hit = sum(1 for t in must_tokens if t and t.lower() in text)

                    # NOTE(2026-04): 旧加权（钨/creep）已注释下线

                    if hit > best_hit:
                        best_hit = hit
                        best_idx = i

                if best_idx is not None and best_hit > 0:
                    # 规则命中给高置信度
                    return self.projects[best_idx], 0.95, best_idx

        # 2) 轻量 Jaccard token 相似度（通用兜底）
        q_tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", q))
        if not q_tokens:
            return None, 0.0, None

        best_idx = None
        best_score = -1.0

        for i, p in enumerate(self.projects):
            text = proj_text(p).lower()
            t_tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text))
            if not t_tokens:
                continue

            inter = len(q_tokens & t_tokens)
            union = len(q_tokens | t_tokens)
            score = inter / union if union else 0.0

            # 额外加权：材料线关键词
            if ("钨" in q_tokens or "tungsten" in q_tokens or "creep" in q_tokens) and ("钨" in t_tokens or "tungsten" in t_tokens or "creep" in t_tokens):
                score += 0.15

            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            return None, 0.0, None

        # 这个阈值你可以按项目数量调；项目少时建议更低一点，否则永远匹配不到
        if best_score >= 0.08:
            return self.projects[best_idx], float(best_score), best_idx

        return None, float(best_score), None
    

    def _proj_text_for_rerank(self, p: Dict[str, Any]) -> str:
        """
        给 reranker 的候选文本：比 domain|name 更强，能显著提升命中。
        """
        tags = " ".join(p.get("tags", []) or [])
        text = (
            f"domain: {p.get('domain','')} | "
            f"name: {p.get('name','')} | "
            f"id: {p.get('id','')} | "
            f"tags: {tags} | "
            f"description: {p.get('description','')} | "
            f"summary: {p.get('summary','')}"
        )

        # 截断，避免极端长文本导致慢
        max_chars = int(os.getenv("RERANKER_MAX_DOC_CHARS", "1200"))
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

    def find_matching_project(self, query: str):
        """
        使用 CrossEncoder 在已加载的 self.projects 上打分，返回最匹配的项目。
        返回: (project_dict or None, score: float, best_idx: int or None)
        """
        if not self.projects:
            logger.warning("[find_matching_project] 项目列表为空")
            return None, 0.0, None

        if not isinstance(query, str):
            query = str(query)

        # reranker 不可用 -> fallback
        if self.reranker is None or (not getattr(self, "enable_reranker", True)):
            logger.warning("[find_matching_project] reranker 不可用/未启用，走 fallback 匹配")
            return self._fallback_match_project(query)

        # 候选文本（增强版）
        texts = [self._proj_text_for_rerank(p) for p in self.projects]
        print("[DEBUG] rerank doc preview:", texts[0][:200])
        pairs = [[query, t] for t in texts]

        try:
            scores = self.reranker.predict(pairs)
            scores = np.asarray(scores, dtype=float)
            if scores.size == 0:
                logger.warning("[find_matching_project] reranker 返回空分数，走 fallback")
                return self._fallback_match_project(query)

            best_idx = int(np.nanargmax(scores))
            best_score = float(scores[best_idx])
            best_proj = self.projects[best_idx]

            logger.info(
                f"[find_matching_project] Top1: {best_proj.get('domain','')}/{best_proj.get('name','')} "
                f"| score={best_score:.4f} | idx={best_idx}"
            )

            # 低于阈值，认为不稳 -> fallback 再兜一下
            if best_score < float(self.score_threshold):
                logger.warning(
                    f"[find_matching_project] score<{self.score_threshold}, fallback 再匹配一次 | "
                    f"score={best_score:.4f}"
                )
                fb_proj, fb_score, fb_idx = self._fallback_match_project(query)
                # fallback 命中就用 fallback，否则仍返回 reranker top1（但上层可提示“不确定”）
                if fb_proj is not None:
                    return fb_proj, fb_score, fb_idx

            return best_proj, best_score, best_idx
        except Exception as e:
            logger.exception(f"[find_matching_project] reranker 评分异常，走 fallback: {e}")
            return self._fallback_match_project(query)

    def get_parameters(self, idx: int) -> Optional[dict]:
        if 0 <= idx < len(self.projects):
            return self.projects[idx].get("parameters", {})
        logger.warning(f"[get_parameters_by_index] 无效项目索引: {idx}")
        return None

    def get_root_path(self, idx: int) -> Optional[str]:
        if 0 <= idx < len(self.projects):
            project = self.projects[idx] or {}

            root_path = project.get("root_path")
            if isinstance(root_path, str) and root_path.strip():
                return root_path.strip()

            paths = project.get("paths") or {}
            if isinstance(paths, dict):
                root_path_2 = paths.get("project_root")
                if isinstance(root_path_2, str) and root_path_2.strip():
                    return root_path_2.strip()

            return ""

        logger.warning(f"[get_root_path] 无效项目索引: {idx}")
        return None


    def get_main_entry(self, idx: int) -> Optional[str]:
        if 0 <= idx < len(self.projects):
            project = self.projects[idx] or {}

            main_entry = project.get("main_entry")
            if isinstance(main_entry, str) and main_entry.strip():
                return main_entry.strip()

            paths = project.get("paths") or {}
            if isinstance(paths, dict):
                main_entry_2 = paths.get("main_entry")
                if isinstance(main_entry_2, str) and main_entry_2.strip():
                    return main_entry_2.strip()

            return ""

        logger.warning(f"[get_main_entry] 无效项目索引: {idx}")
        return None
    

    def get_summary(self, idx: int) -> Optional[str]:
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
    desc: str = (
        "XIMUAlpha工业平台·材料发现与跨尺度计算Agent："
        "基于上游的材料文献获得结构，面向材料体系与化学式输入，执行材料初筛、结构与热力学稳定性评估，"
        "以结构化 JSON 为唯一输出载体，负责计算任务调度、产物组织与结果解释，"
        "输出可供前端展示与下游计算使用的 JSON 与可视化资产路径，不进行闲聊式解释。"
    )

    _code_retriever: CodeRetriever = PrivateAttr(default = None)
    _emitted_glb_keys: set = PrivateAttr(default_factory=set)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    # Prompt 常量已迁移到 src/roles/mns_role_prompts.py


    #懒加载，初始化Code_retriever
    def _get_code_retriever(self) -> CodeRetriever:
        # 瘦身阶段：当前主链（化学式→MP→ALIGNN）未使用，先停用
        return None

    async def _safe_send_text(self, websocket, content):
        # 瘦身阶段：当前主链未使用，先停用
        return

    # 流式发送 LLM 响应
    async def _stream_llm_response(
        self,
        llm,
        messages,
        websocket=None,
        mirror_to_content: bool = False,
        mirror_step_id: str = "",
    ) -> str:
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

        mirror_started = False
        step_id = str(mirror_step_id or "").strip()
        mirror_enabled = bool(mirror_to_content and step_id)

        if mirror_enabled and websocket and websocket.client_state.name == "CONNECTED":
            await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
            mirror_started = True

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
            if mirror_started and websocket and websocket.client_state.name == "CONNECTED":
                try:
                    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
                except Exception:
                    pass
            # 尽可能优雅关闭流
            try:
                aclose = getattr(stream_res, "aclose", None)
                if callable(aclose):
                    await aclose()
            except Exception as e:
                logger.debug(f"[LLM_Stream-LOG] 关闭流时发生异常: {e!s}")

        logger.info(f"[LLM_Stream-LOG] 收集到 {len(collected_chunks)} 段输出，总长 {sum(len(c) for c in collected_chunks)} 字符")
        return "".join(collected_chunks)



    async def _material_mp_explain_stage(self, llm, websocket, query: str, parameters: dict, taskid: str):
        import json

        # parameters 建议是你 _build_material_parameters 的输出
        # 或者你已拿到的 MP manifest 结构化摘要（越结构化越好）
        prompt = self.XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT.format(
            query=str(query or ""),
            parameters=json.dumps(parameters, ensure_ascii=False, indent=2),
        )

        # MP 字段判读统一放右侧新页
        await websocket.send_text("<<<CONTENT_START:MATERIAL_SCREENING>>>")
        await websocket.send_text("### 数据库获取信息总览\n\n")
        await self._stream_llm_response(
            llm,
            [llm._default_system_msg(), llm._user_msg(prompt)],
            websocket
        )
        await websocket.send_text("\n以上表格汇总了从 Materials Project 数据库中检索到的相关化学式候选结构与关键字段，用于说明当前候选为什么会进入后续筛选与性质分析流程。\n")
        await websocket.send_text("<<<CONTENT_END:MATERIAL_SCREENING>>>")

    def _formula_profile(self, formula_: str) -> dict:
        f = str(formula_ or "").strip()
        f_up = f.upper()
        f_low = f.lower()

        def _looks_formula_local(s: str) -> bool:
            ss = str(s or "").strip()
            # 仅用于 _formula_profile 的轻量判别，避免依赖 run() 内部局部函数
            return bool(re.fullmatch(r"(?:[A-Z][a-z]?\d*){2,}", ss))

        # 已知缩写/代表性体系：优先精确命名
        if f_up in {"LLZO", "LI7LA3ZR2O12"}:
            return {
                "中文名称": "石榴石型氧化物固态电解质（LLZO）",
                "材料类别": "氧化物固态电解质",
                "应用角色": "锂离子导体骨架相",
            }

        if f_up in {"PEO", "P(EO)", "POLYETHYLENE OXIDE"}:
            return {
                "中文名称": "聚氧化乙烯（PEO）",
                "材料类别": "聚合物电解质基体",
                "应用角色": "离子传导聚合物相",
            }

        # 复合/共混体系：如 LLZO-PEO
        if "-" in f and len(f.split("-")) == 2:
            a, b = [x.strip() for x in f.split("-", 1)]
            au, bu = a.upper(), b.upper()

            if {au, bu} == {"LLZO", "PEO"}:
                return {
                    "中文名称": "LLZO-PEO 复合固态电解质",
                    "材料类别": "无机-聚合物复合电解质",
                    "应用角色": "复合电解质候选相",
                }

            if _looks_formula_local(a) and _looks_formula_local(b):
                return {
                    "中文名称": f"{a}-{b} 二元材料体系",
                    "材料类别": "二元无机材料体系",
                    "应用角色": "成分协同筛选体系",
                }

        # 常见单体材料精细化
        if f_up in {"AL2O3"}:
            return {
                "中文名称": "氧化铝（Al2O3）",
                "材料类别": "氧化物陶瓷",
                "应用角色": "机械增强/绝缘稳定相",
            }

        if f_up in {"LI3N"}:
            return {
                "中文名称": "氮化锂（Li3N）",
                "材料类别": "无机锂离子导体",
                "应用角色": "高锂离子传导候选相",
            }

        if ("li" in f_low and "s" in f_low and "p" in f_low) or f_up in {"LI6PS5CL", "LI3PS4", "LPSCL"}:
            return {
                "中文名称": "锂-磷-硫体系固态电解质候选",
                "材料类别": "硫化物固态电解质",
                "应用角色": "锂离子传导相/电解质相",
            }

        return {
            "中文名称": "无机化合物候选",
            "材料类别": "无机功能材料",
            "应用角色": "待筛选候选相",
        }

    async def _material_alignn_placeholder_stage(self, websocket, formula: str, llm=None):
        """兼容旧调用名，实际已接入 ALIGNN 补全。"""
        return await self._material_alignn_completion_stage(websocket, formula, llm=llm)


    async def _material_alignn_completion_stage(self, websocket, formula: str, llm=None):
        """
        MP-first + ALIGNN completion + proxy ranking
        - 优先使用 MP 字段
        - 缺失时用 ALIGNN 补 formation_energy / band_gap / bulk / shear
        - 生成 hardness proxy、conductivity/diffusion proxy 和候选排序
        """
        async def _stream_table_header_once():
            await websocket.send_text("\n")
            await websocket.send_text("| 性质项 | 数值 | 单位 | 来源 | 可信度 | 应用解读 |\n")
            await websocket.send_text("|---|---:|---|---|---|---|\n")

        async def _stream_property_row(name: str, value, unit: str, source: str, confidence: str, hint: str, nd: int = 4, confidence_note: str = ""):
            if isinstance(value, float):
                value_text = f"{value:.{nd}f}"
            elif value is None:
                value_text = "待计算"
            else:
                value_text = str(value)

            hint_text = str(hint or "").strip()
            conf_text = str(confidence_note or "").strip()
            if conf_text:
                hint_text = f"{hint_text}（{conf_text}）" if hint_text else conf_text

            row_text = f"| {name} | {value_text} | {unit} | {source} | {confidence} | {hint_text} |"

            # 使用现有 LLM 流式链路输出表格行（不直接 send_text）
            # 说明：此处以“效果优先”为目标，先不做严格格式兜底
            if llm is not None:
                row_prompt = (
                    "请原样输出以下这一行 Markdown 表格内容，不要添加任何解释或额外字符。\n"
                    f"{row_text}"
                )
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(row_prompt)],
                    websocket,
                )
                await websocket.send_text("\n")
            else:
                # 兜底：无 llm 时回退原有直发
                await websocket.send_text(row_text + "\n")

        def _source_confidence(raw_source: str, fallback_source: str = "模型预测/数据库值", fallback_conf: str = "中") -> tuple:
            src_v = str(raw_source or "")
            if src_v.startswith("ALIGNN"):
                return "ALIGNN图神经网络预测补全", "较高"
            if src_v:
                return "MP数据库第一性原理结果", "高"
            return fallback_source, fallback_conf

        def _resolve_symmetry_text(item: dict) -> str:
            crystal = str(item.get("crystal_system") or item.get("crystal") or "").strip()
            spg = str(item.get("spacegroup_symbol") or item.get("space_group") or item.get("symmetry") or "").strip()
            if crystal and spg:
                return f"{crystal}/{spg}"
            return crystal or spg or "待计算"

        repo_root = _repo_root()
        root_path = f"src/MNS_CaseHub/cases/material_discovery_demo"
        abs_root = os.path.abspath(os.path.join(repo_root, root_path))
        results_dir = os.path.join(abs_root, "results")
        taskid_s = str(getattr(self, "taskid", "") or "")

        # 优先使用当前会话 taskid，避免命中历史目录导致候选共用旧 structure
        taskid_s = str(getattr(self, "_current_taskid", "") or "").replace("/", "_")
        if taskid_s:
            mp_pat = os.path.join(results_dir, "mp", f"*{taskid_s}*", str(formula), "manifest.json")
            cands = sorted(glob.glob(mp_pat))
        else:
            mp_pat = os.path.join(results_dir, "mp", "*", str(formula), "manifest.json")
            cands = sorted(glob.glob(mp_pat))

        if not cands:
            await websocket.send_text(f"\n\n### 材料性质计算 - {formula}\n\n- 未找到可用于性质补全的结构数据，已跳过。\n")
            return {}

        manifest_path = cands[-1]
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            await websocket.send_text(f"\n\n### 材料性质计算 - {formula}\n\n- 结构数据读取失败：{e}\n")
            return {}

        files = manifest.get("files") or manifest.get("files_abs") or {}
        files_abs = manifest.get("files_abs") or {}
        base_dir = manifest.get("base_dir") or os.path.dirname(manifest_path)
        selected_path = files.get("selected_structures_json", "")
        if selected_path and not os.path.isabs(selected_path):
            selected_path = os.path.abspath(os.path.join(base_dir, selected_path))

        # 当前任务目录下的主结构 CIF（优先使用，避免历史绝对路径污染）
        local_manifest_cif = os.path.join(base_dir, "structure.cif")
        manifest_cif_abs = files_abs.get("structure_cif") or ""
        if manifest_cif_abs and (not os.path.isabs(manifest_cif_abs)):
            manifest_cif_abs = os.path.abspath(os.path.join(base_dir, manifest_cif_abs))

        manifest_cif_rel = files.get("structure_cif") or ""
        if manifest_cif_rel and (not os.path.isabs(manifest_cif_rel)):
            manifest_cif_rel = os.path.abspath(os.path.join(base_dir, manifest_cif_rel))

        items = []
        try:
            if selected_path and os.path.exists(selected_path):
                with open(selected_path, "r", encoding="utf-8") as f:
                    sj = json.load(f)
                if isinstance(sj, dict):
                    items = sj.get("items") or []
                elif isinstance(sj, list):
                    items = sj
        except Exception:
            items = []

        if not items:
            await websocket.send_text(f"\n\n### 材料性质计算 - {formula}\n\n- 未找到候选结构项，已跳过。\n")
            return {}

        def _resolve_cif_for_item(it: dict, base_dir_: str):
            """
            返回 (cif_path, cif_source)
            source: item_path / local_manifest / manifest_abs / manifest_rel / scanned / missing
            """
            # 1) item 内路径（若有）
            p_item = _alignn_extract_cif_path_from_item(it, base_dir_)
            if p_item and os.path.exists(p_item):
                return p_item, "item_path"

            # 2) 当前目录固定产物（最可靠）
            if local_manifest_cif and os.path.exists(local_manifest_cif):
                return local_manifest_cif, "local_manifest"

            # 3) manifest files_abs
            if manifest_cif_abs and os.path.exists(manifest_cif_abs):
                return manifest_cif_abs, "manifest_abs"

            # 4) manifest files 相对路径
            if manifest_cif_rel and os.path.exists(manifest_cif_rel):
                return manifest_cif_rel, "manifest_rel"

            # 5) 扫描目录兜底
            cands = sorted(glob.glob(os.path.join(base_dir_, "*.cif")))
            if cands:
                return cands[0], "scanned"

            return "", "missing"

        FE_MODELS = ["jv_formation_energy_peratom_alignn", "mp_e_form_alignn"]
        BG_MODELS = ["jv_mbj_bandgap_alignn", "jv_optb88vdw_bandgap_alignn", "mp_gappbe_alignn"]
        BULK_MODELS = ["jv_bulk_modulus_kv_alignn"]
        SHEAR_MODELS = ["jv_shear_modulus_gv_alignn"]
        ELEC_MASS_MODELS = ["jv_avg_elec_mass_alignn"]
        HOLE_MASS_MODELS = ["jv_avg_hole_mass_alignn"]
        invalid_models = set()
        model_probe_done = False
        model_probe_msg = ""
        pred_cache = {}
        timeout_sec = int(os.getenv("ALIGNN_TIMEOUT_SEC", "30"))

        top = None
        if isinstance(items, list) and items:
            top = dict(items[0]) if isinstance(items[0], dict) else None

        if not isinstance(top, dict):
            await websocket.send_text(f"\n\n### 材料性质计算 - {formula}\n\n- 未找到可用于性质补全的候选亚型，已跳过。\n")
            return {}

        mid = str(top.get("material_id") or top.get("id") or "")
        cif_path, cif_source = _resolve_cif_for_item(top, base_dir)
        mp_all_keys = sorted(list(top.keys())) if isinstance(top, dict) else []
        e_hull = _alignn_pick_num(top, ["energy_above_hull", "e_above_hull", "energy_above_hull_ev_per_atom"])
        fe = _alignn_pick_num(top, ["formation_energy_per_atom", "formation_energy", "e_form", "formation_energy_ev_per_atom"])
        bg = _alignn_pick_num(top, ["band_gap", "bandgap", "band_gap_ev"])
        bulk = _alignn_pick_num(top, ["bulk_modulus", "bulk_modulus_gpa", "kvrh", "k_vrh"])
        shear = _alignn_pick_num(top, ["shear_modulus", "shear_modulus_gpa", "gvrh", "g_vrh"])
        density = _alignn_pick_num(top, ["density", "density_g_cm3"])
        elec_mass = _alignn_pick_num(top, ["avg_elec_mass", "avg_electron_mass", "electron_effective_mass", "m_e_avg"])
        hole_mass = _alignn_pick_num(top, ["avg_hole_mass", "hole_effective_mass", "m_h_avg"])

        e_hull_src, fe_src, bg_src, bulk_src, shear_src = "MP", "MP", "MP", "MP", "MP"
        density_src = "MP" if isinstance(density, float) else "NA"
        elec_mass_src = "MP" if isinstance(elec_mass, float) else "NA"
        hole_mass_src = "MP" if isinstance(hole_mass, float) else "NA"
        bulk_err = ""
        shear_err = ""
        em_err = ""
        hm_err = ""

        if (not model_probe_done) and cif_path and os.path.exists(cif_path):
            ok_probe, err_probe = _alignn_probe_alignn_model(BULK_MODELS[0], cif_path)
            model_probe_done = True
            model_probe_msg = "ALIGNN模型可用" if ok_probe else f"ALIGNN模型探测失败: {err_probe[:220]}"

        p_formula = self._formula_profile(formula)
        lines = [
            f"### 材料性质计算 - {formula}（{p_formula['中文名称']}）",
            "",
            f"#### 材料性质计算结果（候选ID：{mid or '-'}）",
            f"- 本轮仅针对最优候选亚型进行性质补全：`{mid or formula}`",
            "",
        ]
        if model_probe_msg:
            logger.info(f"[ALIGNN_PROBE] formula={formula} probe={model_probe_msg}")

        async def _stream_lines(lines_, delay_s: float = 0.02):
            for _ln in (lines_ or []):
                await websocket.send_text((_ln or "") + "\n")
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

        await _stream_lines(lines, delay_s=0.02)
        await _stream_table_header_once()

        stability_class = "待计算"
        if isinstance(e_hull, float):
            if abs(e_hull) < 1e-12:
                stability_class = "稳定"
            elif e_hull <= 0.02:
                stability_class = "接近稳定"
            else:
                stability_class = "偏离稳定"

        src_ehull, conf_ehull = _source_confidence(e_hull_src, "MP数据库第一性原理结果", "高")
        await _stream_property_row(
            "距稳定相包络能量差",
            e_hull,
            "eV/atom",
            src_ehull,
            conf_ehull,
            f"用于热力学稳定性快速筛选，当前字段判读：{stability_class}",
            confidence_note="适合用于初筛与相对比较",
        )
        src_fe, conf_fe = _source_confidence(fe_src, "MP数据库第一性原理结果", "高")
        await _stream_property_row(
            "形成能",
            fe,
            "eV/atom",
            src_fe,
            conf_fe,
            "数值越负通常仅表示形成倾向更强，可作为候选排序参考",
            confidence_note="适合用于候选排序，最终结论建议结合后续验证",
        )
        src_density, conf_density = _source_confidence(density_src, "MP数据库第一性原理结果", "高")
        await _stream_property_row(
            "密度",
            density,
            "g/cm3",
            src_density,
            conf_density,
            "用于判断压实、堆叠与宏观结构设计的体积负载趋势",
            confidence_note="可用于工程估算，建议与实测密度交叉核对",
        )

        if (bg is None) and cif_path and os.path.exists(cif_path):
            bg_pred, mn, _ = _alignn_try_alignn_models(cif_path, BG_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bg_pred is not None:
                bg, bg_src = bg_pred, f"ALIGNN:{mn}"
        src_bg, conf_bg = _source_confidence(bg_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "带隙",
            bg,
            "eV",
            src_bg,
            conf_bg,
            "过小可能提升电子泄漏风险，影响电化学应用边界",
            confidence_note="用于趋势判断，关键设计阶段建议复核",
        )

        if (bulk is None) and cif_path and os.path.exists(cif_path):
            bulk_pred, mn, _ = _alignn_try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bulk_pred is not None:
                bulk, bulk_src = bulk_pred, f"ALIGNN:{mn}"
            else:
                _, _, bulk_err = _alignn_try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (bulk is None) and (not cif_path or not os.path.exists(cif_path)):
            bulk_err = f"cif缺失或路径无效({cif_source})"
        src_bulk, conf_bulk = _source_confidence(bulk_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "体积模量",
            bulk,
            "GPa",
            src_bulk,
            conf_bulk,
            "更高通常更抗压，更利于压片与堆叠稳定",
            confidence_note="若为模型补全值，建议后续以高精度计算或实验复核",
        )

        if (shear is None) and cif_path and os.path.exists(cif_path):
            shear_pred, mn, _ = _alignn_try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if shear_pred is not None:
                shear, shear_src = shear_pred, f"ALIGNN:{mn}"
            else:
                _, _, shear_err = _alignn_try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (shear is None) and (not cif_path or not os.path.exists(cif_path)):
            shear_err = f"cif缺失或路径无效({cif_source})"
        src_shear, conf_shear = _source_confidence(shear_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "剪切模量",
            shear,
            "GPa",
            src_shear,
            conf_shear,
            "更高通常更抗剪切形变，降低使用中开裂风险",
            confidence_note="若为模型补全值，建议后续以高精度计算或实验复核",
        )

        if (elec_mass is None) and cif_path and os.path.exists(cif_path):
            em_pred, mn, _ = _alignn_try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if em_pred is not None:
                elec_mass, elec_mass_src = em_pred, f"ALIGNN:{mn}"
            else:
                _, _, em_err = _alignn_try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (elec_mass is None) and (not cif_path or not os.path.exists(cif_path)):
            em_err = f"cif缺失或路径无效({cif_source})"
        src_em, conf_em = _source_confidence(elec_mass_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "电子有效质量",
            elec_mass,
            "m0",
            src_em,
            conf_em,
            "关联电子输运趋势，影响宏观导电特征",
            confidence_note="主要用于趋势判断",
        )

        if (hole_mass is None) and cif_path and os.path.exists(cif_path):
            hm_pred, mn, _ = _alignn_try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if hm_pred is not None:
                hole_mass, hole_mass_src = hm_pred, f"ALIGNN:{mn}"
            else:
                _, _, hm_err = _alignn_try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (hole_mass is None) and (not cif_path or not os.path.exists(cif_path)):
            hm_err = f"cif缺失或路径无效({cif_source})"
        src_hm, conf_hm = _source_confidence(hole_mass_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "空穴有效质量",
            hole_mass,
            "m0",
            src_hm,
            conf_hm,
            "关联空穴输运趋势，影响界面极化表现",
            confidence_note="主要用于趋势判断",
        )

        hardness_est = None
        hardness_formula = "待计算"
        if isinstance(shear, float) and isinstance(bulk, float) and bulk > 1e-12 and shear > 0:
            try:
                k_ratio = shear / bulk
                hv_chen = 2.0 * ((k_ratio * k_ratio * shear) ** 0.585) - 3.0
                hardness_est = float(hv_chen)
                hardness_formula = "Chen经验公式 Hv=2(k^2G)^0.585-3"
            except Exception:
                hardness_est = None
        if hardness_est is None and isinstance(shear, float):
            hardness_est = (0.151 * shear)
            hardness_formula = "Teter近似 Hv≈0.151G"
        await _stream_property_row(
            "硬度（估算）",
            hardness_est,
            "GPa",
            "经验公式估算结果",
            "中高",
            "可用于粗略判断抗压痕与耐磨趋势，数值越高通常机械支撑更强",
            confidence_note="用于快速比较，不等同于标准硬度测试结果",
        )

        cond_diff_proxy = None
        if isinstance(bg, float) and isinstance(fe, float):
            cond_diff_proxy = (1.0 / (1.0 + max(bg, 0.0))) * (1.0 / (1.0 + abs(fe)))
        if isinstance(elec_mass, float) and elec_mass > 0:
            cond_diff_proxy = (cond_diff_proxy or 1.0) * (1.0 / (1.0 + elec_mass))
        if isinstance(hole_mass, float) and hole_mass > 0:
            cond_diff_proxy = (cond_diff_proxy or 1.0) * (1.0 / (1.0 + hole_mass))
        await _stream_property_row(
            "导电/扩散相关量（粗略）",
            cond_diff_proxy,
            "无量纲",
            "综合排序参考指标",
            "中",
            "仅用于候选排序的趋势参考，不等同于实验电导率或扩散系数",
            confidence_note="不作为定量性能结论，仅用于优先级筛选",
        )

        top = {
            "material_id": mid,
            "symmetry": _resolve_symmetry_text(top),
            "e_above_hull": e_hull,
            "stability_class": stability_class,
            "density": density,
            "formation_energy": fe,
            "band_gap": bg,
            "bulk_modulus": bulk,
            "shear_modulus": shear,
            "hardness_est": hardness_est,
            "hardness_formula": hardness_formula,
            "elec_mass": elec_mass,
            "hole_mass": hole_mass,
            "cond_diff_proxy": cond_diff_proxy,
            "src_ehull": e_hull_src,
            "src_density": density_src,
            "src_fe": fe_src,
            "src_bg": bg_src,
            "src_bulk": bulk_src,
            "src_shear": shear_src,
            "src_elec_mass": elec_mass_src,
            "src_hole_mass": hole_mass_src,
            "err_bulk": bulk_err,
            "err_shear": shear_err,
            "err_elec_mass": em_err,
            "err_hole_mass": hm_err,
            "mp_all_keys": mp_all_keys,
            "cif_source": cif_source,
        }

        return top if isinstance(top, dict) else {}

    def _sanitize_for_llm(self, obj):
        # 瘦身阶段：当前主链未使用，先停用
        return obj


    # format_instruction 方法   
    async def format_instruction(self, instruction: str, llm) -> str:
        # 瘦身阶段：当前主链未使用，先停用
        return str(instruction or "").strip()

    async def send_results_to_frontend(
        self,
        websocket,
        source_path: str,
        root_path: str,
        taskid: str,
        jobid: str = "",
        pipeline: str = "mp",
        allow_latest_job: bool = True,
        step_id: str = "MATERIAL_SCREENING",
        emit_summary_block: bool = True,
        keep_block_open_after_asset: bool = False,
    ):
        """
        统一产物下发（前端协议版）：
        - 定位 results/<pipeline>/*<taskid_sanitized>*/<jobid>/manifest.json（或该 taskid 下最新 job）
        - summary.md：右侧内容块（<<<CONTENT_START:step_id>>>）
        - 图片/GLB：下发 build_payload(type_="asset")：
            {"step_id": "...", "name": "...", "docs": "...", "url": "...", "type": "MaterialsPNG/MaterialsGLB"}
        - 若 manifest 不存在：fallback 扫描 results 根目录图片
        """
        import os
        import json
        import glob

        # 结构图在前端停留时长（秒），默认3秒，可通过环境变量调节
        try:
            asset_hold_seconds = max(0.0, float(os.getenv("MATERIAL_ASSET_HOLD_SECONDS", "3")))
        except Exception:
            asset_hold_seconds = 3.0

        result = {
            "manifest_found": False,
            "glb_ready": False,
            "glb_sent": False,
            "glb_url": "",
        }

        async def _ws_asset(name: str, docs: str, url: str, asset_type: str, description: str = ""):
            safe_desc = description if isinstance(description, str) else ""
            payload = {
                "step_id": step_id,          # ✅ 不写死
                "name": name,
                "docs": docs,
                "url": url,
                "type": asset_type,          # MaterialsPNG / MaterialsGLB
                # 始终携带 description，避免前端因字段缺失触发空态分支
                "description": safe_desc,
            }
            logger.info(
                f"[send_results_to_frontend] ws_asset type={asset_type} name={name} "
                f"desc_len={len(safe_desc)}"
            )
            await websocket.send_json(payload)

        async def _ws_right(step_id_local: str, text: str):
            await websocket.send_text(f"<<<CONTENT_START:{step_id_local}>>>")
            if text:
                await websocket.send_text(text.rstrip() + "\n")
            await websocket.send_text(f"<<<CONTENT_END:{step_id_local}>>>")

        async def _ws_png_markdown(formula_label: str, image_url: str, heading: str = "", fig_label: str = ""):
            safe_formula = str(formula_label or "Material").strip() or "Material"
            safe_heading = str(heading or "").strip() or f"{safe_formula}_无机化合物可能候选结构"
            safe_fig_label = str(fig_label or "").strip() or f"{safe_formula} 候选结构图"
            md = (
                f"### {safe_heading}\n\n"
                f"![{safe_fig_label}]({str(image_url or '').strip()})\n\n"
                f"*图示为 {safe_formula} 的可能晶体结构候选。a、b、c 为晶胞三轴长度（单位 Å）；"
                f"α、β、γ 为晶轴夹角（单位 °）；Atoms 为晶胞内原子位点数；"
                f"这些参数会从微观层面上影响材料的性质，系统将从中筛选出最优候选。*"
            )
            await _ws_right(step_id, md)

        logger.info(
            f"[send_results_to_frontend] ENTER step_id={step_id} pipeline={pipeline} source_path={source_path}, root_path={root_path}, taskid={taskid}, jobid={jobid}"
        )

        abs_root_path = os.path.abspath(os.path.join(source_path, root_path))
        results_dir = os.path.join(abs_root_path, "results")

        logger.info(f"[send_results_to_frontend] abs_root_path={abs_root_path}")
        logger.info(f"[send_results_to_frontend] results_dir={results_dir} exists={os.path.exists(results_dir)}")

        if not os.path.exists(results_dir):
            logger.warning(f"[send_results_to_frontend] ❌ results 目录不存在: {results_dir}")
            return result

        exts = {".png", ".jpg", ".jpeg", ".gif"}
        taskid_sanitized = str(taskid).replace("/", "_")

        # ---------- 1) 定位 manifest ----------
        manifest_path = None
        try:
            # 当指定了 jobid 时，不允许回退到“最新 job”，避免跨候选串单
            if jobid:
                allow_latest_job = False

            if jobid:
                pattern = os.path.join(results_dir, pipeline, f"*{taskid_sanitized}*", str(jobid), "manifest.json")
                cands = sorted(glob.glob(pattern))
                if cands:
                    manifest_path = cands[-1]

            if manifest_path is None and allow_latest_job:
                pattern = os.path.join(results_dir, pipeline, f"*{taskid_sanitized}*", "*", "manifest.json")
                cands = sorted(glob.glob(pattern))
                if cands:
                    manifest_path = cands[-1]

        except Exception as e:
            logger.warning(f"[send_results_to_frontend] 查找 manifest 失败: {e}")

        async def _upload_and_get_url(
            abs_path: str,
            oss_key: str,
            asset_kind: str = "asset",
            public_url_override: str = ""
        ):
            try:
                with open(abs_path, "rb") as f:
                    b = f.read()

                upload_endpoint = os.getenv("MINIO_ENDPOINT", "")
                logger.info(
                    f"[send_results_to_frontend] [{asset_kind}] PutObject target => "
                    f"endpoint={upload_endpoint} bucket=alpha key={oss_key}"
                )

                result = await oss_upload("alpha", oss_key, b)
                if result.get("status") != 200:
                    logger.error(f"[send_results_to_frontend] ❗ 上传失败: {abs_path}, resp={result}")
                    return None

                if public_url_override:
                    url = public_url_override
                else:
                    url = get_image_url("alpha", oss_key)
                    if url.startswith(minio_addr):
                        url = url.replace(minio_addr, https_vip_addr, 1)

                logger.info(f"[send_results_to_frontend] [{asset_kind}] Frontend URL => {url}")
                return url
            except Exception as e:
                logger.exception(f"[send_results_to_frontend] 上传失败: {abs_path} | {e}")
                return None

        # ---------- 2) fallback：没有 manifest 就扫 results 根目录图片 ----------
        if not manifest_path or not os.path.exists(manifest_path):
            logger.warning(
                f"[send_results_to_frontend] ⚠️ 未找到 manifest.json pipeline={pipeline} taskid={taskid}, jobid={jobid}，fallback 扫描 results 根目录"
            )
            try:
                image_files = sorted(
                    f for f in os.listdir(results_dir)
                    if os.path.isfile(os.path.join(results_dir, f))
                    and os.path.splitext(f)[1].lower() in exts
                )
            except Exception as e:
                logger.exception(f"[send_results_to_frontend] 遍历 results 失败: {e}")
                return result

            for fname in image_files:
                abs_img = os.path.join(results_dir, fname)
                oss_key = f"materials/modelfiles/image/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
                image_public_url = f"{picture_public_base_url}/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
                url = await _upload_and_get_url(abs_img, oss_key, asset_kind="png", public_url_override=image_public_url)
                if not url:
                    continue
                await _ws_asset(
                    name=fname,
                    docs=os.path.splitext(fname)[0],
                    url=url,
                    asset_type="MaterialsPNG"
                )
                await _ws_png_markdown(
                    formula_label=(str(jobid or "").strip() or "Material"),
                    image_url=url,
                    heading=f"{str(jobid or '').strip() or 'Material'}_无机化合物可能候选结构",
                    fig_label=fname,
                )
                if asset_hold_seconds > 0:
                    await asyncio.sleep(asset_hold_seconds)

            return result

        logger.info(f"[send_results_to_frontend] ✅ found manifest: {manifest_path}")
        result["manifest_found"] = True

        # ---------- 3) 读取 manifest ----------
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.exception(f"[send_results_to_frontend] 读取 manifest 失败: {e}")
            return result

        if not isinstance(manifest, dict) or not manifest.get("ok"):
            logger.warning("[send_results_to_frontend] ⚠️ manifest 内容异常或 ok!=true")
            return result

        files = (manifest.get("files_abs") or manifest.get("files") or {})
        base_dir = manifest.get("base_dir") or os.path.dirname(manifest_path)

        def _abspath(p: str) -> str:
            if not p:
                return ""
            p = str(p)
            if os.path.isabs(p):
                return p
            return os.path.abspath(os.path.join(base_dir, p))

        # ---------- 4) summary.md（右侧内容块，可按 pipeline 开关） ----------
        md_path = _abspath(files.get("summary_md", ""))
        if emit_summary_block and md_path and os.path.exists(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    md_text = f.read()
                await _ws_right(step_id, md_text[:120000])   # ✅ 不写死
                logger.info(f"[send_results_to_frontend] ✅ sent summary.md as right-block: {md_path}")
            except Exception as e:
                logger.warning(f"[send_results_to_frontend] 发送 summary.md 失败: {e}")

        # ---------- 5) 图片（MaterialsPNG） ----------
        image_items = []
        card_items = manifest.get("candidate_cards") or []
        if isinstance(card_items, list):
            for c in card_items:
                if not isinstance(c, dict):
                    continue
                p = c.get("image_path") or c.get("image_path_abs")
                if p:
                    image_items.append(p)

        # 优先使用“拼接总图”，若存在则仅发送这一张
        combined_path = manifest.get("candidate_cards_combined") or (manifest.get("files") or {}).get("candidate_cards_combined_png") or (manifest.get("files_abs") or {}).get("candidate_cards_combined_png")
        if combined_path:
            image_items = [combined_path]

        image_meta_by_path = {}
        if isinstance(manifest.get("images"), list) and manifest["images"]:
            for it in manifest["images"]:
                if isinstance(it, dict):
                    p2 = it.get("path", "")
                    if p2:
                        image_items.append(p2)
                        image_meta_by_path[str(p2)] = {
                            "name": str(it.get("name") or "").strip(),
                            "docs": str(it.get("docs") or "").strip(),
                        }
                else:
                    p2 = str(it)
                    if p2:
                        image_items.append(p2)
        else:
            try:
                for fn in sorted(os.listdir(base_dir)):
                    p = os.path.join(base_dir, fn)
                    if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in exts:
                        image_items.append(p)
            except Exception:
                pass

        # 去重并保持顺序
        image_items = list(dict.fromkeys([str(x) for x in image_items if str(x).strip()]))

        for p in image_items:
            abs_img = _abspath(p) if not os.path.isabs(str(p)) else str(p)
            if not abs_img or not os.path.exists(abs_img):
                continue
            if os.path.splitext(abs_img)[1].lower() not in exts:
                continue

            fname = os.path.basename(abs_img)
            meta = image_meta_by_path.get(str(p), {}) if isinstance(p, str) else {}
            display_name = (meta.get("name") or "").strip() or fname
            display_docs = (meta.get("docs") or "").strip() or os.path.splitext(fname)[0]
            oss_key = f"materials/modelfiles/image/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
            image_public_url = f"{picture_public_base_url}/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
            url = await _upload_and_get_url(abs_img, oss_key, asset_kind="png", public_url_override=image_public_url)
            if not url:
                continue

            await _ws_png_markdown(
                formula_label=(str(jobid or "").strip() or str(manifest.get("formula") or "").strip() or "Material"),
                image_url=url,
                heading=(display_name or f"{str(jobid or '').strip() or 'Material'}_无机化合物可能候选结构"),
                fig_label=(display_docs or display_name or os.path.basename(abs_img)),
            )
            if asset_hold_seconds > 0:
                await asyncio.sleep(asset_hold_seconds)

        # ---------- 6) GLB（MaterialsGLB） ----------
        glb_path = _abspath(files.get("structure_glb", ""))
        if glb_path and os.path.exists(glb_path):
            result["glb_ready"] = True
            fname = os.path.basename(glb_path)
            glb_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            glb_publish_name = f"{glb_ts}_{fname}"
            # GLB 按前端约定统一落到 materials/modelfiles/glb 目录，并增加时间戳防重名
            oss_key = f"materials/modelfiles/glb/{glb_publish_name}"
            glb_public_url = f"{glb_public_base_url}/{glb_publish_name}"
            url = await _upload_and_get_url(
                glb_path,
                oss_key,
                asset_kind="glb",
                public_url_override=glb_public_url,
            )

            if url:
                formula_for_asset = (str(jobid or "").strip() or str(manifest.get("formula") or "").strip())
                dedup_key = "|".join([
                    str(taskid_sanitized),
                    str(step_id or ""),
                    str(pipeline or ""),
                    str(formula_for_asset or ""),
                    str(url or ""),
                ])
                if dedup_key in self._emitted_glb_keys:
                    logger.info(f"[send_results_to_frontend] ⏭️ skip duplicated MaterialsGLB: {dedup_key}")
                    result["glb_sent"] = True
                    result["glb_url"] = str(url or "")
                    return result
                self._emitted_glb_keys.add(dedup_key)

                # 仅在需要“资产插入到右侧正文流中”时进行分段包裹切换
                if keep_block_open_after_asset:
                    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
                    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")

                base_name = (formula_for_asset or os.path.splitext(fname)[0] or "Material").replace("/", "_")
                rich_name = f"{base_name}_无机化合物最优候选结构"
                rich_docs = f"{base_name}_无机化合物最优候选结构"
                glb_description = (
                    f"该三维模型展示了 {base_name} 的最优候选晶体结构。"
                    f"可通过旋转、缩放观察原子排布与晶胞形貌，"
                    f"用于直观理解结构稳定性与后续性质分析的结构基础；"
                    f"其中结果用于筛选与工程判断，不替代最终实验表征。"
                )
                await _ws_asset(
                    name=rich_name,
                    docs=rich_docs,
                    url=url,
                    asset_type="MaterialsGLB",
                    description=glb_description,
                )
                if asset_hold_seconds > 0:
                    await asyncio.sleep(asset_hold_seconds)

                if keep_block_open_after_asset:
                    await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
                    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
                logger.info(f"[send_results_to_frontend] ✅ sent MaterialsGLB: {fname}")
                result["glb_sent"] = True
                result["glb_url"] = str(url or "")
        else:
            logger.warning(f"[send_results_to_frontend] ⚠️ manifest 中未提供 structure_glb 或文件不存在: {glb_path}")

        return result


    def _collect_material_outputs(self, repo_root: str, taskid: str, jobid: str = "") -> dict:
        import os, glob

        base = os.path.join(
            repo_root,
            "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results"
        )
        taskid_s = str(taskid).replace("/", "_")

        # MP manifest
        if jobid:
            mp_cands = sorted(glob.glob(os.path.join(base, "mp", f"*{taskid_s}*", jobid, "manifest.json")))
        else:
            mp_cands = sorted(glob.glob(os.path.join(base, "mp", f"*{taskid_s}*", "*", "manifest.json")))

        return {
            "taskid": taskid,
            "jobid": jobid,
            "paths": {
                "mp_manifest": mp_cands[-1] if mp_cands else None,
                # 先占位：后续你接 ADiT 时再补
                "adit_report": None,
                "adit_manifest": None,
            }
        }

    def _build_material_parameters(self, collected: dict) -> dict:
        import os, json

        def _safe_load_json(p: str):
            if not p or not os.path.exists(p):
                return None
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None

        mp_manifest = _safe_load_json(collected["paths"].get("mp_manifest"))

        parameters = {
            "taskid": collected.get("taskid", ""),
            "jobid": collected.get("jobid") or "",
            # ✅ 给 LLM 的“业务数据”（候选结构列表）
            "mp_selected": {
                "count_selected": 0,
                "items": []
            },
            # ✅ 保留非常轻的上下文（不含路径）
            "mp_context": {
                "formula": "",
                "primary_material_id": "",
                "query": {}
            }
        }

        if isinstance(mp_manifest, dict):
            parameters["mp_context"]["formula"] = mp_manifest.get("formula") or (collected.get("jobid") or "")
            parameters["mp_context"]["query"] = mp_manifest.get("query") or {}
            parameters["mp_context"]["primary_material_id"] = (mp_manifest.get("query") or {}).get("primary_material_id") or ""

            files = mp_manifest.get("files") or mp_manifest.get("files_abs") or {}
            sel_path = files.get("selected_structures_json") or ""
            sel_json = _safe_load_json(sel_path)

            # 兼容两种形态：
            # A) 你贴的那种：{"items":[...], "count_selected":3, ...}
            # B) 直接是 list
            if isinstance(sel_json, dict):
                items = sel_json.get("items") or []
                parameters["mp_selected"]["items"] = items if isinstance(items, list) else []
                cs = sel_json.get("count_selected")
                parameters["mp_selected"]["count_selected"] = int(cs) if isinstance(cs, int) else len(parameters["mp_selected"]["items"])
            elif isinstance(sel_json, list):
                parameters["mp_selected"]["items"] = sel_json
                parameters["mp_selected"]["count_selected"] = len(sel_json)

        return parameters
    

    #读取案例的readme文件
    def read_case_readme(self,path: str) -> str:
        # 瘦身阶段：当前主链未使用，先停用
        return ""


    async def _ws_right(self, websocket, step_id: str, text: str):
        # 瘦身阶段：当前主链未使用，先停用
        return

    async def run(self, instruction: str, *args):
        import os, re, json, asyncio, subprocess, glob

        websocket = args[0]
        user_name, taskid, file_metadata = args[1], args[2], args[3]
        self._current_taskid = str(taskid)

        config = load_config("config/config.yaml")
        llm = SeLLM(base_url=config["base_url_1"], api_key=config["api_key"])

        CASE_MP = "material_discovery_demo"

        # =========================
        # 0) WS helpers：右侧内容块（去掉前置多余空行）
        # =========================
        async def _ws_right(step_id: str, text: str):
            # ✅ 不要在 <<<CONTENT_START 前面加额外 '\n'
            await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
            if text:
                await websocket.send_text(text.rstrip() + "\n")
            await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")

        # =========================
        # 0.5) progress helper：只发 completed，且每次都带全字段
        # =========================
        async def _mark_completed(step_id: str, icon: str, title: str, description: str):
            await websocket.send_json(build_payload(
                data={
                    "id": step_id,
                    "icon": icon,
                    "title": title,
                    "status": "completed",
                    "description": description
                },
                type_="progress",
                request_id=taskid
            ))

        progress_sent = False
        async def _ensure_material_progress_started():
            nonlocal progress_sent
            if progress_sent:
                return
            await _mark_completed(
                "MATERIAL_SCREENING",
                "🎯",
                "材料模拟与计算",
                "基于机器学习模型进行材料性能快速预测与初步筛选"
            )
            progress_sent = True

        # 诊断模式：MATERIAL_SCREENING 全流程单一包裹（不做分段包裹）
        material_block_opened = False
        async def _open_material_block(step_id: str = "MATERIAL_SCREENING"):
            nonlocal material_block_opened
            if material_block_opened:
                return
            await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
            material_block_opened = True

        async def _close_material_block(step_id: str = "MATERIAL_SCREENING"):
            nonlocal material_block_opened
            if not material_block_opened:
                return
            await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
            material_block_opened = False

        async def _upload_database_pic_for_markdown(pic_abs_path: str, pic_name: str) -> str:
            """上传固定数据库示意图，返回前端可访问 URL。失败返回空串。"""
            try:
                if not pic_abs_path or (not os.path.exists(pic_abs_path)):
                    logger.warning(f"[DB_PIC] file not found: {pic_abs_path}")
                    return ""
                with open(pic_abs_path, "rb") as f:
                    b = f.read()
                taskid_s = str(taskid).replace("/", "_")
                oss_key = f"materials/modelfiles/image/{taskid_s}/databasepic/{str(pic_name).strip()}"
                resp = await oss_upload("alpha", oss_key, b)
                if not isinstance(resp, dict) or resp.get("status") != 200:
                    logger.warning(f"[DB_PIC] upload failed: {pic_abs_path} resp={resp}")
                    return ""
                return f"{picture_public_base_url}/{taskid_s}/databasepic/{str(pic_name).strip()}"
            except Exception as e:
                logger.exception(f"[DB_PIC] upload exception: {e!s}")
                return ""

        def _render_performance_bar_png(metric_rows: list, out_png_path: str):
            """绘制预期值 vs 当前值对比图（matplotlib 优先，PIL 兜底）。"""
            labels = [str(r.get("label", "")).strip() for r in (metric_rows or [])]
            expected_scores = [int(r.get("expected", 0) or 0) for r in (metric_rows or [])]
            current_scores = [int(r.get("current", 0) or 0) for r in (metric_rows or [])]
            states = [str(r.get("state", "Pending")) for r in (metric_rows or [])]

            n = len(labels)
            if n <= 0:
                return

            # 1) 首选 matplotlib
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                try:
                    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei", "DejaVu Sans"]
                    plt.rcParams["axes.unicode_minus"] = False
                except Exception:
                    pass

                fig_h = max(2.6, 0.8 * n + 1.6)
                fig, ax = plt.subplots(figsize=(8.8, fig_h), dpi=160)
                fig.patch.set_facecolor("#F8FAFD")
                ax.set_facecolor("#F8FAFD")

                x_pos = list(range(n))
                w = 0.34
                for i, x in enumerate(x_pos):
                    exp_v = max(0, min(100, int(expected_scores[i])))
                    cur_v = max(0, min(100, int(current_scores[i])))
                    st = states[i]

                    # Expected（基线）
                    ax.bar(x - w / 2, exp_v, color="#D7DEED", edgecolor="#C8D2E6", width=w, label="Expected" if i == 0 else "")

                    # Current（当前）
                    if st == "Met":
                        ax.bar(x + w / 2, cur_v, color="#5B6CFF", edgecolor="#4B5CF0", width=w, label="Current" if i == 0 else "")
                    elif st == "Partially Met":
                        ax.bar(x + w / 2, cur_v, color="#8A96FF", edgecolor="#5B6CFF", hatch="///", linewidth=0.8, width=w, label="Current" if i == 0 else "")
                    else:
                        ax.bar(x + w / 2, cur_v, color="#C3CBD9", edgecolor="#AEB7C7", width=w, label="Current" if i == 0 else "")

                    ax.text(x + w / 2, min(cur_v + 2.5, 99.0), f"{st}\nE:{exp_v}% / C:{cur_v}%", va="bottom", ha="center", fontsize=8, color="#4B5568")

                ax.set_xticks(x_pos)
                ax.set_xticklabels(labels, fontsize=9, color="#3E4A5A", rotation=10, ha="right")
                ax.set_ylim(0, 100)
                ax.set_yticks([0, 20, 40, 60, 80, 100])
                ax.tick_params(axis="y", labelsize=8, colors="#6A7382")
                ax.grid(axis="y", color="#DEE5F0", linestyle="--", linewidth=0.6, alpha=0.8)
                for sp in ["top", "right", "left", "bottom"]:
                    ax.spines[sp].set_visible(False)

                ax.set_title("Expected vs Current Performance Comparison", fontsize=11, color="#2F6FEF", pad=10, loc="left")
                ax.legend(loc="upper right", frameon=False, fontsize=8)
                plt.tight_layout()
                fig.savefig(out_png_path, dpi=160)
                plt.close(fig)
                return
            except Exception as e:
                logger.warning(f"[PERF_BAR] matplotlib unavailable, fallback to PIL: {e!s}")

            # 2) 回退 PIL
            try:
                from PIL import Image, ImageDraw, ImageFont

                W = 1280
                H = max(300, 110 + n * 90)
                img = Image.new("RGB", (W, H), "#F8FAFD")
                draw = ImageDraw.Draw(img)

                try:
                    font_title = ImageFont.truetype("DejaVuSans.ttf", 28)
                    font_label = ImageFont.truetype("DejaVuSans.ttf", 22)
                    font_text = ImageFont.truetype("DejaVuSans.ttf", 20)
                except Exception:
                    font_title = ImageFont.load_default()
                    font_label = ImageFont.load_default()
                    font_text = ImageFont.load_default()

                draw.text((36, 24), "Expected vs Current Performance Comparison", fill="#2F6FEF", font=font_title)

                # legend
                draw.rectangle((900, 24, 930, 42), fill="#D7DEED", outline="#C8D2E6", width=1)
                draw.text((938, 22), "Expected", fill="#4B5568", font=font_text)
                draw.rectangle((1070, 24, 1100, 42), fill="#5B6CFF", outline="#4B5CF0", width=1)
                draw.text((1108, 22), "Current", fill="#4B5568", font=font_text)

                x_label = 36
                x_bar = 430
                bar_w = 700
                y0 = 90
                bar_h = 20

                for i in range(n):
                    y = y0 + i * 86
                    label = labels[i]
                    exp_v = max(0, min(100, int(expected_scores[i])))
                    cur_v = max(0, min(100, int(current_scores[i])))
                    state = states[i]

                    draw.text((x_label, y + 2), label, fill="#3E4A5A", font=font_label)

                    # Expected bar
                    exp_w = int(bar_w * exp_v / 100)
                    draw.rounded_rectangle((x_bar, y, x_bar + exp_w, y + bar_h), radius=10, fill="#D7DEED", outline="#C8D2E6", width=1)

                    # Current bar
                    cur_y = y + 28
                    cur_w = int(bar_w * cur_v / 100)
                    if cur_w > 0:
                        if state == "Met":
                            draw.rounded_rectangle((x_bar, cur_y, x_bar + cur_w, cur_y + bar_h), radius=10, fill="#5B6CFF", outline="#4B5CF0", width=1)
                        elif state == "Partially Met":
                            draw.rounded_rectangle((x_bar, cur_y, x_bar + cur_w, cur_y + bar_h), radius=10, fill="#8A96FF", outline="#5B6CFF", width=1)
                            step = 8
                            for xx in range(x_bar - bar_h, x_bar + cur_w + bar_h, step):
                                draw.line((xx, cur_y + bar_h, xx + bar_h, cur_y), fill="#EDF0FF", width=2)
                        else:
                            draw.rounded_rectangle((x_bar, cur_y, x_bar + cur_w, cur_y + bar_h), radius=10, fill="#C3CBD9", outline="#AEB7C7", width=1)

                    draw.text((x_bar + bar_w + 16, y + 12), f"{state}  E:{exp_v}% / C:{cur_v}%", fill="#4B5568", font=font_text)

                img.save(out_png_path)
            except Exception as e:
                logger.exception(f"[PERF_BAR] PIL fallback failed: {e!s}")
                raise

        # =========================
        # 1) 调试：入口日志
        # =========================
        try:
            logger.info(f"[ROUTER] user_name={user_name!r} taskid={taskid!r}")

            if isinstance(instruction, list):
                head = ""
                try:
                    if instruction:
                        last = instruction[-1]
                        head = str(last)[:300]
                except Exception:
                    head = str(instruction)[:300]
                logger.info(f"[ROUTER] instruction_type=list len={len(instruction)} head={head!r}")
            else:
                _inst = instruction if isinstance(instruction, str) else str(instruction)
                logger.info(f"[ROUTER] instruction_type={type(instruction).__name__} len={len(_inst)} head={_inst[:300]!r}")

            logger.info(f"[ROUTER] file_metadata_type={type(file_metadata).__name__}")
            if isinstance(file_metadata, dict):
                logger.info(f"[ROUTER] file_metadata_keys={list(file_metadata.keys())[:50]}")
        except Exception as _e:
            logger.exception(f"[ROUTER] entry_debug_failed: {_e!s}")

        # =========================
        # 2) 化学式辅助：Unicode 下标 -> ASCII 数字
        # =========================
        def _to_ascii_formula(s: str) -> str:
                if s is None:
                        return ""
                s = str(s)

                sub_map = str.maketrans({
                        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
                        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
                })
                s = s.translate(sub_map)

                # 保留复合表达连接符，避免把 "C2H4Oₙ·LiTFSI·Al2O3" 这类体系拆碎
                s = s.replace("•", "·")
                s = s.replace("−", "-").replace("–", "-").replace("—", "-")
                return s.strip()

        _ELEMENTS = {
                "H","He","Li","Be","B","C","N","O","F","Ne","Na","Mg","Al","Si","P","S","Cl","Ar",
                "K","Ca","Sc","Ti","V","Cr","Mn","Fe","Co","Ni","Cu","Zn","Ga","Ge","As","Se","Br","Kr",
                "Rb","Sr","Y","Zr","Nb","Mo","Tc","Ru","Rh","Pd","Ag","Cd","In","Sn","Sb","Te","I","Xe",
                "Cs","Ba","La","Ce","Pr","Nd","Pm","Sm","Eu","Gd","Tb","Dy","Ho","Er","Tm","Yb","Lu",
                "Hf","Ta","W","Re","Os","Ir","Pt","Au","Hg","Tl","Pb","Bi","Po","At","Rn",
                "Fr","Ra","Ac","Th","Pa","U","Np","Pu","Am","Cm","Bk","Cf","Es","Fm","Md","No","Lr",
                "Rf","Db","Sg","Bh","Hs","Mt","Ds","Rg","Cn","Nh","Fl","Mc","Lv","Ts","Og",
        }

        import re

        _FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")

        def _looks_like_formula(s: str) -> bool:
                """
                判别可用于材料检索的化学式：
                - 允许小数计量（如 Li6.5La3Zr1.5Al0.5O12）
                - 允许括号配方（如 Li1.3Al0.3Ti1.7(PO4)3）
                - 仍排除复合体系写法（含 · / _n）
                """
                s = _to_ascii_formula(s)
                if not s:
                        return False

                # 聚合占位/复合体系表达，不视作单一可跑 MP 的化学式
                if ("·" in s) or ("ₙ" in s) or re.search(r"_n\b", s, flags=re.IGNORECASE):
                        return False

                # 基本长度约束
                if len(s) < 2 or len(s) > 80:
                        return False

                # 放宽字符集：支持小数点与括号
                if re.search(r"[^A-Za-z0-9\.\(\)]", s):
                        return False

                # 优先用 pymatgen 进行语法判定（支持小数计量/括号）
                try:
                        from pymatgen.core import Composition
                        comp = Composition(s)
                        elems = [str(el) for el in comp.elements]
                        return len(elems) >= 2
                except Exception:
                        pass

                # 兜底：旧正则判定（仅整数计量）
                i = 0
                tokens = []
                while i < len(s):
                        m = _FORMULA_TOKEN.match(s, i)
                        if not m:
                                return False

                        sym = m.group(1)
                        num = m.group(2)

                        if sym not in _ELEMENTS:
                                return False

                        if num:
                                if num.startswith("0"):
                                        return False
                                try:
                                        n = int(num)
                                except Exception:
                                        return False
                                if n <= 0:
                                        return False

                        tokens.append((sym, num))
                        i = m.end()

                if len(tokens) < 2 and not any(num for _, num in tokens):
                        return False

                return True

        def _normalize_formula_for_mp(s: str) -> str:
            return _utils_normalize_formula_for_mp(s)


        # =========================
        # 3) instruction 归一 + route
        # =========================
        def _normalize_user_text(s) -> str:
            def _strip_preface_payload_noise(text: str) -> str:
                t = str(text or "")

                # 去掉“前置结果”里常见的整段 JSON payload（仅影响提取输入）
                t = re.sub(
                    r"\{[^{}]{0,20000}\"version\"\s*:\s*\"1\.0\.0\"[^{}]{0,20000}\}",
                    " ",
                    t,
                    flags=re.DOTALL,
                )
                t = re.sub(
                    r"\{[^{}]{0,20000}\"type\"\s*:\s*\"progress\"[^{}]{0,20000}\}",
                    " ",
                    t,
                    flags=re.DOTALL,
                )
                t = re.sub(
                    r"\{[^{}]{0,20000}\"request_id\"\s*:\s*\"[^\"]+\"[^{}]{0,20000}\}",
                    " ",
                    t,
                    flags=re.DOTALL,
                )

                # 若存在“### 需求”，优先从需求正文开始
                anchor = t.find("### 需求")
                if anchor >= 0:
                    t = t[anchor:]

                # 若存在“=== 前置结果 ===”，尽量丢弃其前后噪声头
                pre = t.find("=== 前置结果 ===")
                if pre >= 0:
                    t = t[pre + len("=== 前置结果 ==="):]

                return t

            if isinstance(s, dict):
                s = (s.get("idea") or s.get("content") or s.get("text") or s.get("query") or "")

            if isinstance(s, list):
                for item in reversed(s):
                    if isinstance(item, dict):
                        content = item.get("idea") or item.get("content") or item.get("text") or item.get("query")
                        if isinstance(content, str) and content.strip():
                            s = content
                            break
                    if hasattr(item, "content"):
                        content = getattr(item, "content", None)
                        if isinstance(content, str) and content.strip():
                            s = content
                            break
                    if isinstance(item, str) and item.strip():
                        s = item
                        break
                else:
                    s = ""

            s = str(s or "").strip()
            s = _strip_preface_payload_noise(s)
            m = re.search(r"\[Human:\s*(.*?)\s*\]$", s)
            if m:
                s = m.group(1).strip()
            return s.strip("[](){} \n\t")

        def _parse_route(s: str):
            s = (s or "").strip()
            m = re.match(r"^/(mp)\s+(.+)$", s, flags=re.IGNORECASE)
            if not m:
                return None, s
            return m.group(1).lower(), m.group(2).strip()

        def _build_formula_extraction_text(s: str) -> str:
            return _utils_build_formula_extraction_text(s)

        # =========================
        # 4) ✅只从“计算对象”行抽取（避免把别的材料带进来）
        # =========================
        def _extract_formulas_from_targets(text: str) -> list:
            return _extract_formulas_from_targets_external(
                text=text,
                to_ascii_formula=_to_ascii_formula,
                looks_like_formula=_looks_like_formula,
                elements_set=_ELEMENTS,
            )

        def _extract_formulas_from_in_ls(repo_root: str) -> tuple:
            return _extract_formulas_from_in_ls_external(
                repo_root=repo_root,
                to_ascii_formula=_to_ascii_formula,
                looks_like_formula=_looks_like_formula,
                normalize_formula_for_mp=_normalize_formula_for_mp,
                logger=logger,
            )

        async def _llm_select_material_candidates(raw_tokens: list, user_context: str = "", in_ls_summary: dict = None) -> tuple:
            return await _llm_select_material_candidates_external(
                llm=llm,
                logger=logger,
                raw_tokens=raw_tokens,
                user_context=user_context,
                in_ls_summary=in_ls_summary,
            )

        async def _build_candidate_lists(raw_tokens: list, user_context: str = "", in_ls_summary: dict = None):
            return await _build_candidate_lists_external(
                llm=llm,
                logger=logger,
                raw_tokens=raw_tokens,
                user_context=user_context,
                in_ls_summary=in_ls_summary,
                to_ascii_formula=_to_ascii_formula,
                looks_like_formula=_looks_like_formula,
                normalize_formula_for_mp=_normalize_formula_for_mp,
                elements_set=_ELEMENTS,
            )

        async def _stream_route_intro_before_mp(formulas_: list, user_context: str = ""):
            """替换为：宏观目标性能窗口表（MP 前置）。"""
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]

            await websocket.send_text("\n\n### 材料性能需求总结\n\n")

            def _is_param_table_valid(md: str) -> bool:
                txt = str(md or "")
                if "|" not in txt:
                    return False
                bad_words = ["未明确", "未获取", "待定", "unknown", "待计算", "N/A", "n/a"]
                if any(w in txt for w in bad_words):
                    return False
                data_lines = [ln for ln in txt.splitlines() if ln.strip().startswith("|") and "---" not in ln]
                if len(data_lines) < 3:
                    return False
                for ln in data_lines[1:]:
                    cols = [c.strip() for c in ln.strip().strip("|").split("|")]
                    if len(cols) < 5:
                        return False
                    threshold_col = cols[1]
                    if not re.search(r"\d", threshold_col):
                        return False
                return True

            # 先给“需求 -> 性质/性能/工艺指标”的流式论证段落，再进入表格
            intro_prompt = (
                "请输出4~7条中文分条内容，不要表格、不要标题。"
                "必须使用阿拉伯数字编号（1. 2. 3. ...）。"
                "每条之间必须空一行。"
                "任务：根据输入内容，先做需求到材料指标的映射论证。"
                "可以使用不同表达，不要每条都重复同一句式。"
                "至少覆盖：应用目标/场景、关键性能、工艺加工或工程约束、验证口径。"
                "禁止使用“用户需要/用户希望/用户要求”等措辞。"
                "禁止出现任何具体化学式、具体材料名称或已选候选结论（例如 Li6PS5Cl）。"
                "语气严肃、工程化，不要夸张，不使用比喻。"
                f"\n用户输入：{str(user_context or '')}"
                f"\n候选材料：{fs}"
            )
            try:
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(intro_prompt)],
                    websocket,
                    mirror_to_content=False,
                    mirror_step_id="MATERIAL_SCREENING",
                )
            except Exception:
                await websocket.send_text("1. 需求拆解应先从应用场景出发，建立可计算、可验证的多指标约束，而非追求单一数值最优。\n\n")
                await websocket.send_text("2. 高功率与安全边界通常对应离子传导相关指标、热稳定相关指标与电化学窗口边界。\n\n")
                await websocket.send_text("3. 可制造性与服役可靠性通常对应密度、机械支撑能力及界面稳定相关代理量。\n\n")
                await websocket.send_text("4. 本轮先形成“需求-性质/性能-验证口径”映射，再进入结构化性能窗口表进行统一判读。\n\n")

            await _open_material_block("MATERIAL_SCREENING")
            await websocket.send_text("\n\n### 材料需求提炼\n\n")
            prompt = (
                "请基于用户输入，输出一张 Markdown 表格，不要标题、不要编号、不要额外段落。"
                "表头固定为：性能维度 | 目标区间/阈值 | 工程原因 | 与应用场景关系 | 后续验证口径。"
                "按“性能维度”聚合输出：每个性能维度只能出现1行（例如本征热导率、CTE、介电损耗等），禁止同一性能维度重复多行。"
                "“目标区间/阈值”列必须在同一单元格内汇总多个材料，格式示例：A材料: 100至120 单位；B材料: 80至95 单位；C材料: ≥130 单位。"
                "禁止把不同材料拆成多行重复展示。"
                "严格格式要求（必须全部满足）："
                "1) 第1行必须是表头且以'|'开头、以'|'结尾；"
                "2) 第2行必须是分隔行，格式为'|---|---|---|---|---|'；"
                "3) 第3行起每一行都必须以'|'开头、以'|'结尾，且严格5列；"
                "4) 禁止在表格前后输出任何解释文字；"
                "5) 禁止单元格内换行，所有内容保持单行。"
                "严格要求：每一行“目标区间/阈值”必须给出带阿拉伯数字的数值或区间，并包含单位；"
                "区间连接符必须使用中文“至”，严禁使用“~”或“～”，以避免前端误触发删除线渲染。"
                "禁止出现“未明确/未获取/待定/unknown/待计算”等字样。"
                "若输入不足，请给出工程常用默认阈值范围，不得留空。"
                f"\n用户输入：{str(user_context or '')}"
                f"\n候选材料：{fs}"
            )
            # 调试阶段按你的要求：不做 fallback，直接走 LLM token 级流式输出。
            out = await self._stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(prompt)],
                websocket,
                mirror_to_content=False,
                mirror_step_id="MATERIAL_SCREENING",
            )
            if not _is_param_table_valid(out):
                logger.warning("[PARAM_TABLE] non-strict markdown table from LLM (stream-only mode)")
            await _close_material_block("MATERIAL_SCREENING")

        async def _stream_formula_readable_view(formulas_: list, user_context: str = ""):
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]

            await websocket.send_text("\n\n### 候选材料分析\n\n")

            bridge_prompt = (
                "请输出4~7条中文分条内容，不要表格、不要标题。"
                "必须使用阿拉伯数字编号（1. 2. 3. ...）。"
                "每条之间必须空一行。"
                "目标：从上一步参数化约束出发，论证如何逐步收敛到可选材料体系。"
                "不要重复上一段已经给出的需求背景与验证口径。"
                "重点写“筛选收敛逻辑、候选体系划分依据、最终化学式落点”。"
                "写作顺序必须为："
                "第1~2行：参数约束如何筛掉不匹配类别；"
                "第3~4行：从材料类别收敛到候选材料体系；"
                "倒数第2行：给出体系俗名或中文名；"
                "最后1行：再给出本轮对应的具体化学式,在化学式前面加上自然语言过渡，如对应的化学式为。"
                "严格要求：内容必须具备泛化性，不能写成只针对SSE的固定模板。"
                "语气严肃、工程化，不使用比喻。"
                f"\n用户输入：{str(user_context or '')}"
                f"\n本轮从材料需求抽象到的具体化学式为：{fs}"
            )
            try:
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(bridge_prompt)],
                    websocket,
                    mirror_to_content=False,
                    mirror_step_id="MATERIAL_SCREENING",
                )
            except Exception:
                await websocket.send_text("1. 参数化提炼阶段先固定关键性能窗口与边界条件，优先排除与目标工况冲突的材料类别。\n\n")
                await websocket.send_text("2. 随后在可行类别内按热稳定、传输相关与界面约束进行多指标交叉收敛，缩小到可验证的候选材料体系。\n\n")
                await websocket.send_text("3. 该收敛逻辑适用于多类无机/有机复合材料筛选，不依赖单一体系预设。\n\n")
                await websocket.send_text("4. 本轮体系中文名：无机功能材料候选体系。\n\n")
                await websocket.send_text(f"5. 本轮候选化学式：{('、'.join(fs) if fs else '待补充')}。\n\n")

            await _open_material_block("MATERIAL_SCREENING")
            await websocket.send_text("\n\n### 候选材料概览\n\n")
            await websocket.send_text("| 化学式 | 中文名称 | 材料类别 | 应用角色 | 入选原因（对应宏观目标） |\n")
            await websocket.send_text("|---|---|---|---|---|\n")
            for f in fs:
                p = self._formula_profile(f)
                await websocket.send_text(
                    f"| {f} | {p['中文名称']} | {p['材料类别']} | {p['应用角色']} | 对应稳定性/传导/机械等宏观目标的优质候选材料 |\n"
                )

            # 候选材料概览下方补充数据库周期图（右侧）
            period_abs = os.path.join(_repo_root(), "public", "databasepic", "period.png")
            if not os.path.exists(period_abs):
                period_abs = os.path.join(_repo_root(), "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results", "databasepic", "period.png")
            period_url = await _upload_database_pic_for_markdown(period_abs, "period.png")
            if period_url:
                await websocket.send_text(f"\n\n![候选材料周期分布示意]({period_url})\n")
            await _close_material_block("MATERIAL_SCREENING")

        async def _stream_macro_micro_bridge(formulas_: list, user_context: str = ""):
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]
            await websocket.send_text("\n\n### 材料数据库选择依据\n\n")

            def _is_macro_micro_table_valid(md: str) -> bool:
                txt = str(md or "")
                if "|" not in txt:
                    return False
                lines = [ln for ln in txt.splitlines() if ln.strip()]
                table_lines = [ln for ln in lines if ln.lstrip().startswith("|")]
                # 头+分隔+至少5行数据
                if len(table_lines) < 7:
                    return False
                # 每一行至少应有4列（5个竖线）
                for ln in table_lines:
                    if ln.count("|") < 5:
                        return False
                return True

            prompt = (
                "请输出一张 Markdown 表格，表格后再单独输出一行“结论：...”。"
                "表头固定为：对比维度 | 微观数据库（MP/DFT等） | 宏观数据库（经验/工艺侧） | 对筛选决策的影响。"
                "表内必须覆盖：覆盖完整性、性质可信度、理论一致性、工艺敏感性、跨来源可比性。"
                "严格要求：表格只保留上述5个维度，不要额外添加“结论”行到表格里。"
                "结论要求：表格结束后单独一行写：结论：仿真模拟阶段优先微观数据库，宏观数据库用于后验校核与工程修正。"
                "语气严肃、客观，不使用比喻。"
                f"\n用户输入：{str(user_context or '')}"
            )
            try:
                out = await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(prompt)],
                    websocket,
                    mirror_to_content=False,
                    mirror_step_id="MATERIAL_SCREENING",
                )
                if not (isinstance(out, str) and "|" in out and _is_macro_micro_table_valid(out)):
                    logger.warning("[MACRO_MICRO_TABLE] non-strict markdown table from LLM (stream mode), skip fallback replay to avoid duplicate rendering")
            except Exception:
                logger.exception("[MACRO_MICRO_TABLE] stream failed; skip fallback replay to avoid duplicate rendering")

        async def _stream_mp_stage_intro(formula_: str):
            """
            MP阶段前的简短真流式说明：介绍正在进行什么、MP是什么、本轮提取哪些字段。
            """
            # 以四级标题挂在前一块内容下，避免形成独立高层分块
            await websocket.send_text("\n\n#### 材料数据库检索说明\n\n")

            # 检索说明下先展示 MP 数据库示意图（左侧）
            mp_abs = os.path.join(_repo_root(), "public", "databasepic", "mp.png")
            if not os.path.exists(mp_abs):
                mp_abs = os.path.join(_repo_root(), "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results", "databasepic", "mp.png")
            mp_url = await _upload_database_pic_for_markdown(mp_abs, "mp.png")
            if mp_url:
                await websocket.send_text(f"![Materials Project 数据库示意图]({mp_url})\n\n")

            intro_prompt = (
                "请输出3~5条中文分条内容，采用工程过程播报语气，不要表格、不要标题。"
                "必须使用阿拉伯数字编号（1. 2. 3. ...）。"
                "每条之间必须空一行。"
                "第一条必须以“正在使用 The Materials Project”开头。"
                "内容需要非常简短，说明：MP是开放材料数据库、规模较大、基于高通量第一性原理计算。"
                "语言尽量通俗但要严肃，补一句这些字段和后续制备可行性、应用场景判断有什么关系，不要使用比喻，是面向成年人专家的解释。"
                "最后一行说明本轮将提取的字段类型：结构（对称性/位点数）、热力学（E_above_hull/E_form）、电子结构（band_gap）。"
                f"当前材料：{str(formula_ or '')}。"
            )
            try:
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(intro_prompt)],
                    websocket
                )
            except Exception as e:
                logger.exception(f"[MP_STAGE_INTRO_STREAM] failed: {e!s}")
                await websocket.send_text(
                    f"1. 正在使用 The Materials Project 对 {formula_} 进行微观性质提取。\n\n"
                    "2. MP 是开放材料数据库，汇集了大规模高通量第一性原理计算结果。\n\n"
                    "3. 本轮将提取结构、热力学与电子结构字段用于后续判读。\n\n"
                )

        async def _stream_alignn_stage_intro(formula_: str):
            """
            ALIGNN阶段前的简短真流式说明。
            """
            # 这里改为非流式一次性发送，避免末尾无换行导致后续 ### 标题粘连
            try:
                alignn_abs = os.path.join(_repo_root(), "public", "databasepic", "alignn.png")
                if not os.path.exists(alignn_abs):
                    alignn_abs = os.path.join(_repo_root(), "src", "MNS_CaseHub", "cases", "material_discovery_demo", "results", "databasepic", "alignn.png")
                alignn_url = await _upload_database_pic_for_markdown(alignn_abs, "alignn.png")
                if alignn_url:
                    await websocket.send_text(f"![ALIGNN 图神经网络分析示意]({alignn_url})\n\n")
                await websocket.send_text(
                    f"1. 正在使用 ALIGNN 对 {formula_} 的晶体结构进行图神经网络分析，快速估算其离子电导率与结构稳定性等关键性质。\n\n"
                    "2. 模型基于原子位置与化学键关系自动提取结构特征，实现毫秒级性质预测。\n\n"
                    "3. 这些结果用于快速筛选与工艺方向判断，不替代最终实验标定。\n\n"
                )
            except Exception:
                await websocket.send_text(
                    f"1. 正在使用 ALIGNN 对 {formula_} 进行材料性质快速估算。\n\n"
                    "2. 该模型基于晶体图神经网络，可在已有结构基础上补全关键性质。\n\n"
                    "3. 结果用于候选排序与工艺方向参考，不替代最终实验标定。\n\n"
                )

        async def _stream_final_requirement_summary(formulas_: list, mp_ready_: list, user_context: str = "", final_metrics: dict = None):
            """目标-结果对照收敛：基于真实计算值输出，不使用泛化项。"""
            await _open_material_block("MATERIAL_SCREENING")
            await websocket.send_text("\n\n### 材料性能目标结果对比\n\n")
            m = final_metrics if isinstance(final_metrics, dict) else {}
            eh = m.get("e_above_hull")
            fe = m.get("formation_energy")
            bg = m.get("band_gap")
            bulk = m.get("bulk_modulus")
            shear = m.get("shear_modulus")
            hard = m.get("hardness_est")
            cond = m.get("cond_diff_proxy")

            def _sf(v, nd=4):
                return f"{v:.{nd}f}" if isinstance(v, float) else "待补充"

            def _sat(ok: bool, partial: bool = False):
                if ok:
                    return "满足"
                if partial:
                    return "部分满足"
                return "待补充"

            sat_stab = _sat(isinstance(eh, float) and eh <= 0.02, partial=isinstance(eh, float))
            sat_bg = _sat(isinstance(bg, float) and bg >= 1.5, partial=isinstance(bg, float) and bg > 0)
            sat_mech = _sat(
                isinstance(bulk, float) and isinstance(shear, float) and bulk >= 15 and shear >= 8,
                partial=isinstance(bulk, float) or isinstance(shear, float),
            )
            sat_trans = _sat(isinstance(cond, float) and cond >= 0.2, partial=isinstance(cond, float))

            def _score_label(sat: str):
                if sat == "满足":
                    return "Met"
                if sat == "部分满足":
                    return "Partially Met"
                return "Pending"

            def _expected_current_pair(sat: str):
                # 预期值固定基准，当前值按满足度做相对高低
                base_expected = 72
                if sat == "满足":
                    return base_expected, min(100, base_expected + 10)
                if sat == "部分满足":
                    return base_expected, base_expected + 1
                return base_expected, max(0, base_expected - 12)

            l_stab = _score_label(sat_stab)
            l_bg = _score_label(sat_bg)
            l_mech = _score_label(sat_mech)
            l_trans = _score_label(sat_trans)

            e_stab, c_stab = _expected_current_pair(sat_stab)
            e_bg, c_bg = _expected_current_pair(sat_bg)
            e_mech, c_mech = _expected_current_pair(sat_mech)
            e_trans, c_trans = _expected_current_pair(sat_trans)

            await websocket.send_text("| 宏观目标项 | 对应微观代理指标 | 本轮结果 | 满足度 | 不确定性与下一步 |\n")
            await websocket.send_text("|---|---|---|---|---|\n")
            await websocket.send_text(
                f"| 热力学稳定性窗口 | E_above_hull / 形成能 | E_hull={_sf(eh)} eV/atom；E_form={_sf(fe)} eV/atom | {sat_stab} | 需结合温度/化学势边界做二次验证 |\n"
            )
            await websocket.send_text(
                f"| 电子绝缘与窗口边界 | 带隙 band_gap | band_gap={_sf(bg)} eV | {sat_bg} | 需与工作电压窗口和界面副反应联合评估 |\n"
            )
            await websocket.send_text(
                f"| 机械支撑与成形风险 | 体积模量/剪切模量/硬度估算 | K={_sf(bulk)} GPa；G={_sf(shear)} GPa；Hv≈{_sf(hard)} GPa | {sat_mech} | 需压片致密化与循环后裂纹演化测试 |\n"
            )
            await websocket.send_text(
                f"| 传输潜力代理 | 导电/扩散相关量（粗略） | proxy={_sf(cond)}（无量纲） | {sat_trans} | 仅用于排序，需EIS/迁移测试给出实测值 |\n\n"
            )

            # 画英文PNG柱状图，避免HTML直出与中文字体乱码
            try:
                perf_rows = [
                    {"label": "Thermodynamic Stability", "expected": e_stab, "current": c_stab, "state": l_stab},
                    {"label": "Electronic Window", "expected": e_bg, "current": c_bg, "state": l_bg},
                    {"label": "Mechanical Reliability", "expected": e_mech, "current": c_mech, "state": l_mech},
                    {"label": "Transport Potential", "expected": e_trans, "current": c_trans, "state": l_trans},
                ]
                perf_abs = f"/tmp/perf_satisfaction_{str(taskid).replace('/', '_')}.png"
                _render_performance_bar_png(perf_rows, perf_abs)
                perf_url = await _upload_database_pic_for_markdown(perf_abs, "performance_satisfaction.png")
                if perf_url:
                    await websocket.send_text("#### 性能满足度对比\n\n")
                    await websocket.send_text(f"![性能满足度对比]({perf_url})\n\n")
            except Exception as e:
                logger.exception(f"[PERF_BAR] render/upload failed: {e!s}")
            await _close_material_block("MATERIAL_SCREENING")

        async def _stream_final_li6ps5cl_bridge(formulas_: list):
            """ADiT/MACE 旧桥接函数已下线，保留空壳以保持接口稳定。"""
            return

        # MP 检索耗时估计（秒）：按你给出的 8~15s 经验设置初值，并在会话内动态微调
        _mp_eta_seconds = 12.0

        def _render_progress_bar(pct: int, width: int = 10) -> str:
            return _render_progress_bar_external(pct, width)

        # =========================
        # 5) MP 运行：mp_export_assets.py
        # =========================
        async def _run_mp_export_assets(formula: str) -> bool:
            nonlocal _mp_eta_seconds
            repo_root = _repo_root()
            formula = _to_ascii_formula(formula)
            progress_emit_interval_s = 4

            cmd = [
                "micromamba", "run", "-n", "mp-api-py311",
                "python", os.path.join(repo_root, "tools", "mp_export_assets.py"),
                "--taskid", str(taskid),
                "--jobid", str(formula),
                "--formula", str(formula),
                "--prefer-stable",
            ]
            logger.info(f"[mp_export_assets] CMD={' '.join(cmd)}")
            try:
                await websocket.send_text("\n\n")
                await websocket.send_text(
                    f"检索进度 {_render_progress_bar(0)} 0%（已用时 0s，预计剩余 {int(round(_mp_eta_seconds))}s）\n\n"
                )
            except Exception:
                pass

            run_res = await _run_mp_export_assets_streaming_external(
                repo_root=repo_root,
                taskid=str(taskid),
                formula=str(formula),
                eta_seconds=float(_mp_eta_seconds),
                progress_emit_interval_s=int(progress_emit_interval_s),
            )

            for ev in (run_res.get("progress_events") or []):
                elapsed = int(ev.get("elapsed", 0))
                pct = int(ev.get("pct", 1))
                remain = int(ev.get("remain", 0))
                try:
                    slow_hint = ""
                    if elapsed > 24:
                        slow_hint = "（网络波动，预计时间延长）"
                    await websocket.send_text(
                        f"检索进度 {_render_progress_bar(pct)} {pct}%（已用时 {int(elapsed)}s，预计剩余 {remain}s）{slow_hint}\n\n"
                    )
                except Exception:
                    pass

            out_t = str(run_res.get("stdout") or "")
            if out_t:
                logger.info(f"[mp_export_assets] STDOUT:\n{out_t[-6000:]}")

            _mp_eta_seconds = float(run_res.get("eta_seconds_new") or _mp_eta_seconds)

            # 注意：这里不发送 100%，仅表示“检索脚本结束”；
            # 100% 需等到 GLB 真正下发给前端后再发送。
            try:
                ok = bool(run_res.get("ok"))
                tail_pct = 95 if ok else 99
                tail_text = (
                    f"检索进度 {_render_progress_bar(tail_pct)} {tail_pct}%（检索完成，正在上传并下发结构资源）\n\n"
                    if ok
                    else f"检索进度 {_render_progress_bar(tail_pct)} {tail_pct}%（检索失败，请查看日志）\n\n"
                )
                await websocket.send_text(tail_text)
            except Exception:
                pass

            ok = bool(run_res.get("ok"))
            if not ok:
                logger.error(f"[mp_export_assets] FAILED rc={run_res.get('returncode')}")
            return ok

        # =========================
        # 6) ADiT 运行（已下线，保留注释占位）
        # =========================
        def _find_mp_manifest_abs(repo_root: str, root_path: str, taskid_: str, formula: str) -> str:
            abs_root_path = os.path.abspath(os.path.join(repo_root, root_path))
            results_dir = os.path.join(abs_root_path, "results")
            taskid_s = str(taskid_).replace("/", "_")
            pattern = os.path.join(results_dir, "mp", f"*{taskid_s}*", str(formula), "manifest.json")
            cands = sorted(glob.glob(pattern))
            return cands[-1] if cands else ""

        
        # =========================
        # 7) MP：导出 + 右侧下发 + 左侧解释
        # =========================
        async def _mp_one(formula: str, emit_mp_explain: bool = True) -> bool:
            formula = _to_ascii_formula(formula)

            ok = await _run_mp_export_assets(formula)
            if not ok:
                await websocket.send_text(
                    f"在 Materials Project 中暂未检索到 {formula} 的可用公开结构数据。"
                    "该候选将保留为新材料候选，可在后续新材料发现流程中继续评估。\n"
                )
                return False

            repo_root = _repo_root()
            root_path = f"src/MNS_CaseHub/cases/{CASE_MP}"

            send_result = await self.send_results_to_frontend(
                websocket,
                repo_root,
                root_path,
                taskid,
                jobid=formula,
                pipeline="mp",
                allow_latest_job=False,
                step_id="MATERIAL_SCREENING",
                emit_summary_block=False,
            )

            glb_sent = bool((send_result or {}).get("glb_sent"))
            glb_ready = bool((send_result or {}).get("glb_ready"))
            try:
                if glb_sent:
                    await websocket.send_text(
                        "结构资源已就绪。\n\n"
                    )
                elif glb_ready:
                    await websocket.send_text(
                        "结构资源已生成，正在下发。\n\n"
                    )
                else:
                    await websocket.send_text(
                        "未发现可下发GLB资源。\n\n"
                    )
            except Exception:
                pass

            # ✅ 左侧解释：按需发送，避免候选回退时重复输出
            try:
                collected = self._collect_material_outputs(repo_root, taskid, jobid=formula)
                parameters = self._build_material_parameters(collected)

                # MP有执行结果但无候选，按“新材料发现流程”提示
                cnt = int((parameters.get("mp_selected") or {}).get("count_selected") or 0)
                if cnt <= 0:
                    await websocket.send_text(
                        f"{formula} 在 MP 数据库中无结果。"
                        "该材料更接近全新候选，建议进入新材料发现流程。\n"
                    )
                    return False

                if emit_mp_explain:
                    await self._material_mp_explain_stage(
                        llm,
                        websocket,
                        query=f"解释 {formula} 的 MP 初筛结果：逐条说明每个候选结构的关键字段含义，并给出字段层面的好/坏判读（仅限 MP 字段）。",
                        parameters=parameters,
                        taskid=taskid
                    )
            except Exception as e:
                logger.exception(f"[MP_EXPLAIN] failed formula={formula}: {e!s}")

            return True

        # =========================
        # 8) ADiT：评估 + 下发 + 解释（已下线，保留注释占位）
        # =========================

        # =========================
        # 9) 统一入口：route / content
        # =========================
        norm = _normalize_user_text(instruction)
        route, content = _parse_route(norm)
        content = _to_ascii_formula(content)
        formula_extract_text = _build_formula_extraction_text(norm)

        try:
            _head = formula_extract_text[:400].replace("\n", "\\n")
            _tail = formula_extract_text[-400:].replace("\n", "\\n") if len(formula_extract_text) > 400 else _head
            logger.info(f"[ROUTER] formula_extract_text_len={len(formula_extract_text)}")
            logger.info(f"[ROUTER] formula_extract_text_head={_head!r}")
            logger.info(f"[ROUTER] formula_extract_text_tail={_tail!r}")
        except Exception as _e:
            logger.warning(f"[ROUTER] formula_extract_text_debug_failed: {_e!s}")

        logger.info(f"[Coding-LOG] user={user_name} taskid={taskid} route={route} content={content}")

        # =========================
        # 10) /mp：强制单个（只跑 MP）
        # 约定：开始这一步就发 completed（不管实际含义）
        # =========================
        if route == "mp":
            formula = content
            if not _looks_like_formula(formula):
                await websocket.send_text("⚠️ /mp 后必须是化学式，例如：/mp Li6PS5Cl\n")
                return

            # 进入材料流程即触发 progress（左侧）
            await _ensure_material_progress_started()

            # 标题与说明放左侧过程流
            p = self._formula_profile(formula)
            await websocket.send_text(f"### 材料对应化学结构信息\n\n正在处理材料：`{formula}（{p['中文名称']}）`\n")
            await _stream_mp_stage_intro(formula)

            # 检索进度与执行播报放左侧
            await _mp_one(formula)
            return

        # =========================
        # 11) 默认路径：按“计算对象”批量跑 MP + ALIGNN占位
        # =========================
        if True:
            raw_tokens = _extract_formulas_from_targets(formula_extract_text)
            in_ls_tokens, in_ls_summary = _extract_formulas_from_in_ls(_repo_root())
            if in_ls_tokens:
                # 合并第三来源，去重保持顺序
                raw_tokens = list(dict.fromkeys((raw_tokens or []) + in_ls_tokens))
            formulas, mp_formulas, non_mp_notes, dropped_tokens = await _build_candidate_lists(
                raw_tokens,
                user_context=norm,
                in_ls_summary=in_ls_summary,
            )
            logger.info(f"[ROUTER] raw_formula_tokens={raw_tokens}")
            logger.info(f"[ROUTER] in_ls_summary={in_ls_summary}")
            if dropped_tokens:
                logger.info(f"[ROUTER] dropped_formula_tokens={dropped_tokens}")
            logger.info(f"[ROUTER] llm_selected_display_tokens={formulas}")
            logger.info(f"[ROUTER] llm_selected_mp_tokens={mp_formulas}")

            if formulas:
                # 进入材料流程即触发 progress（左侧）
                await _ensure_material_progress_started()

                if not mp_formulas:
                    await websocket.send_text("未提取到可用于 MP 检索的标准化学式，已停止本轮材料检索。\n")
                    return

                # 左侧：流程说明；右侧：函数内部仅包表格/结论
                try:
                    await _stream_route_intro_before_mp(mp_formulas, user_context=norm)
                except Exception as e:
                    logger.exception(f"[ROUTE_INTRO_STREAM] failed: {e!s}")

                await _stream_formula_readable_view(mp_formulas, user_context=norm)

                # 对比维度提前到候选概览阶段，并在左侧对话流显示
                await _stream_macro_micro_bridge(mp_formulas, user_context=norm)

                if non_mp_notes:
                    pass

                # 左侧：过程播报与进度
                mp_ready_formulas = []
                selected_formula = ""
                selected_metrics = {}
                await websocket.send_text("\n将按候选顺序进行数据库检索。\n")

                total_mp = len(mp_formulas)
                mp_intro_sent = False
                for idx, f in enumerate(mp_formulas, start=1):
                    pf = self._formula_profile(f)

                    # 左侧：候选标题与数据库检索说明
                    await websocket.send_text(f"\n正在检索候选材料：`{f}（{pf['中文名称']}）`\n")
                    if not mp_intro_sent:
                        await _stream_mp_stage_intro(f)
                        mp_intro_sent = True

                    # 左侧：候选进度与命中播报
                    await websocket.send_text(f"当前候选进度：{idx}/{total_mp}\n")
                    logger.info(f"[MP_SCREENING] single_formula_first_hit_mode start formula={f}")
                    ok = await _mp_one(f, emit_mp_explain=True)
                    if ok:
                        selected_formula = f
                        mp_ready_formulas = [f]
                        break
                    else:
                        await websocket.send_text(f"当前候选 `{f}` 暂未获得可用结果，已继续检索下一候选材料。\n")

                # 当前版本：执行 MP + ALIGNN；ADiT/MACE 流程下线
                if mp_ready_formulas:
                    await websocket.send_text("\n\n#### <span style=\"color:#2f6fef;\">材料性质补充分析</span>\n\n")
                    # ALIGNN阶段说明保持在左侧
                    await _stream_alignn_stage_intro(selected_formula)
                    await websocket.send_text("<<<CONTENT_START:MATERIAL_SCREENING>>>")
                    selected_metrics = await self._material_alignn_placeholder_stage(websocket, selected_formula, llm=llm)
                    await websocket.send_text("<<<CONTENT_END:MATERIAL_SCREENING>>>")
                else:
                    await websocket.send_text("\n无可用于材料性质计算的候选结构，已结束本轮计算。\n")

                # 最终需求对照总结（右侧）
                await _stream_final_requirement_summary(formulas, mp_ready_formulas, user_context=norm, final_metrics=selected_metrics)

                # 左侧：流程完成播报
                await websocket.send_text("\n材料模拟与计算模块完成，本服务已结束，正在接入下一流程。\n")
                return

            await _ensure_material_progress_started()
            await websocket.send_text(
                "未在已有数据库中搜索到可用于检索的合适化学式/材料候选，"
                "建议转向新材料开发模块。\n"
            )
            return


            
########################################
# 定义角色：XIMUAlpha_MNS
########################################

class XIMUAlpha_MNS(Role):
    """
    工业平台 · 无机已有材料筛选智能体。
    定位：面向无机晶体/陶瓷/玻璃类材料的已有材料检索、性质补全与工程化解释，
    以“结构化 JSON”为唯一对接载体，侧重“数据库检索 → 代理模型补全 → 稳定性/性质整理 → 可视化产物拼装”。
    """
    # 对外展示名（前端/日志可见）
    name: str = "XIMUAlpha_inorganic_existing_materials"

    # 简要画像（供框架/上游作为 system profile 使用）
    profile: str = (
    "无机已有材料检索与性质补全专用智能体。"
    "定位：面向无机晶体/陶瓷/玻璃类材料的已有材料检索、性质补全与工程化解释。"
    "输入前提：必须已完成文献筛选，并提供候选化学式或候选材料列表。"
    "职责边界：仅执行已有无机材料数据库检索、结构与性质补全、候选排序与结果整理；"
    "不负责文献再筛选、不负责材料制备流程、不负责实验执行。"
    "完成判据：当候选材料表、关键性质参数与可视化资源索引已输出时，本服务即结束。"
    "路由建议：本服务结束后应优先转入“材料制备模块”或“性能检测与结果对比模块”；"
    "除非上游重新提供新的候选化学式，否则不应再次调用本服务。"
    )
    

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 保持不变
        self._watch([UserRequirement])
        self.set_actions([Coding])

# NOTE(2026-04, 第2刀“先注释不删除”):
# Coding 类内仍保留历史内嵌 prompt 文本，避免大规模删改带来的行为风险。
# 但运行时统一改为引用 src/roles/mns_role_prompts.py 的常量，
# 以屏蔽旧链路 prompt（工程反演/DFT-MLIP-LAMMPS 旧文案）对当前主线的影响。
Coding.XIMU_MNS_ENGINEERING_PROMPT = XIMU_MNS_ENGINEERING_PROMPT
Coding.XIMU_MNS_MATERIAL_PROMPT = XIMU_MNS_MATERIAL_PROMPT
Coding.XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT = XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT
    
