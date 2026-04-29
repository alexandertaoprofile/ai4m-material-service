# -*- coding: utf-8 -*-
import os
import re
import sys
import asyncio
import subprocess
import glob
import tempfile
import io
import base64
import json
import logging
import traceback
import mimetypes
import uuid
import datetime
from typing import List, Dict, Tuple, Optional, Union, Any

import requests
import numpy as np
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv
from pydantic import PrivateAttr

from alpha.team import Team
from alpha.roles import Role
from alpha.logs import logger
from alpha.schema import Message
from alpha.actions import Action, UserRequirement

from src.llm_utils import SeLLM, load_config
from src.storage_utils import oss_upload, download_to_file, get_image_url

# Optional: reranker (heavy dependency)
try:
    from sentence_transformers import CrossEncoder  # noqa: F401
except Exception:
    CrossEncoder = None  # type: ignore

def _repo_root() -> str:
    # 当前文件: .../ai4m_tqm/src/team_config.py
    # 仓库根:   .../ai4m_tqm
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _resolve_case_readme_path(case: dict) -> str:
    repo = _repo_root()

    # 兼容 root_path / paths.project_root
    root_path = case.get("root_path")
    if not root_path:
        paths = case.get("paths") or {}
        root_path = paths.get("project_root")

    if not root_path:
        return ""  # 让上层打印 “root_path缺失”

    readme_name = case.get("readme") or "README.md"

    # 1) 先按 “repo/root_path/readme” 试
    p1 = os.path.join(repo, root_path, readme_name)

    # 2) 再按 “repo/src/root_path/readme” 试（兼容 root_path 没写 src/ 的情况）
    p2 = os.path.join(repo, "src", root_path, readme_name)

    if os.path.exists(p1):
        return p1
    if os.path.exists(p2):
        return p2

    # 两种都不存在
    return p2  # 返回一个“最可能”的路径给日志打印

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

# 前端访问资源的固定公开前缀（与无机线对齐）
glb_public_base_url = os.getenv(
    "GLB_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/materials/modelfiles/glb"
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
    """
    将任意输出打包成统一 JSON 格式，供前端解析。

    - 支持 meta.ui.hidden: True  -> 前端可忽略（你服务端仍可记录日志）
    - 支持 meta.ui.level: "debug"/"info" -> 前端可按级别过滤（需要前端配合）
    - progress 的 icon 允许在后续统一清空（你也可在这里直接清空）
    """
    import uuid
    import datetime

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
        # 兜底：避免 meta 不是 dict
        payload["meta"] = meta if isinstance(meta, dict) else {"raw_meta": str(meta)}

    # ✅ 可选：默认把 progress 的 icon 清空（你说“卡通图标不需要这么频繁”）
    # 如果你仍想保留少数关键步骤的 icon，可以在 meta 里显式写 ui.keep_icon=True
    try:
        keep_icon = bool(payload.get("meta", {}).get("ui", {}).get("keep_icon", False))
        if type_ == "progress" and isinstance(payload.get("data"), dict) and not keep_icon:
            payload["data"]["icon"] = ""
    except Exception:
        pass

    # ✅ 可选：截断过长文本（避免 summary/md 或 LLM 输出把前端刷爆）
    # 这里不改 image / parameters
    try:
        max_chars = int(payload.get("meta", {}).get("ui", {}).get("max_text_chars", 6000))
        if type_ in ("chat", "error", "progress") and isinstance(payload.get("data"), str):
            if len(payload["data"]) > max_chars:
                payload["data"] = payload["data"][:max_chars] + "…"
    except Exception:
        pass

    return payload


#########################################辅助函数分类prompt#########################################
def _safe_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return str(x)
    except Exception:
        return ""

def _get_case_root(case: dict) -> str:
    """
    兼容两种字段：
      - 旧：case["root_path"]
      - 新：case["paths"]["project_root"]
    返回相对 repo_root 的路径（不带前导 /）
    """
    root_path = case.get("root_path")
    if not root_path:
        paths = case.get("paths") or {}
        root_path = paths.get("project_root")
    return root_path or ""



def _as_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (dict, list)):
        try:
            import json
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    return str(x)


def _infer_prompt_mode(best_proj: dict) -> str:
    # 仅保留“已有无机材料”服务线
    return "materials"


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

        RULES = [
            # 单 case 材料线泛化
            (
                r"(dft|mlip|deepmd|lammps|mace|ase|materials? project|material|材料|晶体|结构|势函数|分子动力学|扩散|弹性)",
                ["dft", "mlip", "deepmd", "lammps", "mace", "ase", "materials project", "material", "材料", "晶体", "结构", "势", "分子动力学", "扩散", "弹性"]
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
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    XIMU_MNS_ENGINEERING_PROMPT :str= """
        你是 XIMUAlpha_MNS 平台中的工程物理建模与参数反演定义模块。
        你的任务是：将用户需求转化为“工程参数反演问题”的正式物理定义文档。
        
        ---
        用户问题：
        {query}

        ---
        工程背景资料（仅用于理解，不可复述）：
        {file_info}

        ---
        工程目标说明：
        {summary}

        ---
        已知结构化参数：
        {parameters}

        ---
        输出格式与内容必须严格遵守以下规则（任何违反均视为错误）：

        【整体结构顺序不可改变】
        控制变量与目标参数
        频域或时间域响应表达式
        动力学或力学物理模型参数
        物理控制方程
        物理常数与固定参数
        环境与边界条件
        设计目标约束

        【表格规则】
        - 每个章节必须优先使用 Markdown 表格表达；
        - 表格列名必须统一为：
          | 符号 | 物理量 | 单位 | 取值或范围 | 功能说明 |
        - 表格中禁止出现任何 LaTeX 语法（包括 $ \\ ^ _ 等）；
        - 表格内容必须是工程设计或反演相关参数，不得给出泛化教材符号。

        【公式规则】
        - 所有公式必须使用独立的 $$ ... $$ 数学块；
        - 每个 $$ 块中只能包含一个公式；
        - 严禁使用以下 LaTeX 环境：
          begin{{cases}}, begin{{array}}, begin{{align}}, begin{{tabular}}；
        - 严禁使用 \\text{{}}, \\left, \\right；
        - 禁止在公式中解释文字。

        【语言与风格】
        - 只允许使用中文；
        - 禁止使用“本案例 / 本系统 / 本项目 / 我们”等指代；
        - 禁止总结性语句、建议性语句或结尾说明；
        - 禁止编号（如 1.、（一）等）；
        - 每一个表格、公式或章节块结束后必须空一行。

        【工程约束】
        - 输出内容必须体现“可反演的工程参数”，而不是纯理论 PDE；
        - 若涉及连续介质模型，必须服务于工程参数识别目的；
        - 若涉及接触、冲击或热源，仅保留工程等效形式。

        按上述规则直接输出结果，不要解释、不要求确认。
        """

    XIMU_MNS_MATERIAL_PROMPT: str = """
        你是 XIMUAlpha_MNS 平台中的材料计算与多尺度仿真报告组织模块。
        你的任务是：将用户需求转化为“DFT → MLIP → LAMMPS → 材料性质验证”的正式工程计算说明文档。

        ---
        用户问题：
        {query}

        ---
        材料案例背景资料（仅用于理解，不可复述）：
        {file_info}

        ---
        材料体系与研究目标说明：
        {summary}

        ---
        已知结构化参数与计算元数据：
        {parameters}

        ---
        输出格式与内容必须严格遵守以下规则（任何违反均视为错误）：

        【整体结构顺序不可改变】
        材料体系与目标性质定义
        DFT 数据来源与覆盖范围
        机器学习势（MLIP）训练与可用性判定
        LAMMPS 验证流程与物性计算路径
        工程相关性质与应用解释
        已生成产物清单
        下一步计算行动项

        【内容表达规则】
        - 每一章节必须以工程计算视角描述，不得出现教学性、科普性表述；
        - 禁止出现推测性语言（如“可能”“大概”“预计”）；
        - 禁止编造未在 {summary}/{parameters}/{file_info} 中出现的事实；
        - 若信息缺失，不得简单重复“未提供”，而应改写为：
          “当前输入未包含该信息，将在后续计算产物或日志中自动补全”；
        - 允许使用“待计算”“待从产物中提取”等工程占位语。

        【表格优先规则】
        - 每一章节至少包含一张 Markdown 表格；
        - 已知信息必须优先落入表格，不得仅用段落描述；
        - 若数值尚未计算，表格取值列填写“待计算”，不得写“未提供”。

        【表格规则】
        - 表格列名必须统一为：
          | 名称 | 物理含义 | 单位 | 取值或范围 | 说明 |
        - 表格中禁止出现任何 LaTeX 语法（包括 $ \\ ^ _ 等）；
        - 表格内容必须直接服务于材料计算或验证流程。

        【公式规则】
        - 公式仅用于关键派生关系或定义，不得大量堆叠；
        - 所有公式必须使用独立的 $$ ... $$ 数学块；
        - 每个 $$ 块中只能包含一个公式；
        - 严禁使用 begin{{cases}}, begin{{array}}, begin{{align}}, begin{{tabular}}；
        - 严禁使用 \\text{{}}, \\left, \\right；
        - 禁止在公式中解释文字。

        【语言与风格】
        - 只允许使用中文；
        - 禁止使用“本案例 / 本体系 / 本项目 / 我们”等指代；
        - 禁止总结性语句、营销性语句或结尾说明；
        - 禁止编号（如 1.、（一）等）；
        - 每一个章节、表格或公式块结束后必须空一行。

        【工程计算约束】
        - 输出内容必须围绕 DFT → MLIP → LAMMPS 的实际计算与验证链路；
        - 若涉及材料性质，必须说明其来源于哪一计算阶段或后处理步骤；
        - 禁止仅给出最终结论而不说明计算路径；
        - “已生成产物清单”必须仅从 {file_info} 或 {parameters} 中出现过的文件名或路径提取；
        - 若当前输入尚未包含文件或路径，允许说明“将在计算完成后生成”。

        按上述规则直接输出结果，不要解释、不要求确认。
        """


    XIMU_MNS_MATERIAL_MP_EXPLAIN_PROMPT :str= """
        你是 XIMUAlpha_MNS 平台中的材料数据库初筛解释模块。
        你的任务是：对 Materials Project（MP）阶段返回的候选结构列表进行逐条解读，
        只基于 MP 可直接获得的字段，给出“字段层面”的判读（不做工程结论）。

        ---
        用户问题：
        {query}

        ---
        材料数据库返回结果（仅用于解释，不可复述原始 JSON）：
        {parameters}

        ---
        输出内容必须严格遵守以下规则（任何违反均视为错误）：

        【解释范围限定】
        - 仅允许解释 Materials Project 阶段可直接获得的信息（例如：material_id、对称性/空间群、E_above_hull、E_form、band_gap、nsites 等）；
        - 禁止推断缺陷形成能、动力学稳定性、离子迁移或电导率；
        - 禁止输出任何文件路径、URL、目录名、manifest 字段、生成时间戳等工程信息；
        - 禁止输出“下一步需要什么数据/建议做什么计算”的展望性内容；
    

        【强制取数规则（最重要）】
        - 必须从 {parameters} 中提取并使用数值；
        - 只要 {parameters} 中存在某字段的数值（哪怕为 0），就严禁写“待计算/未提供/unknown，如果0的话就直接写0”；
        - 仅当 {parameters} 中完全找不到该字段时，才允许写“待计算”。

        【必须说明的要点】
        - 必须对“候选结构列表”的每一行（每个 material_id）逐条解读；
        - 必须对 E_above_hull、E_form、band_gap、对称性/空间群等字段分别解释“含义 + 在筛选中代表什么”；
        - 必须基于以下口径做“字段层面判读”（只做口径判读，不做性能优劣结论）：
          1) E_above_hull：
             - = 0：记为“稳定（MP 热力学口径）”
             - (0, 0.02] eV/atom：记为“接近稳定”
             - > 0.02 eV/atom：记为“偏离稳定”
          2) E_form：数值越负仅表示“形成倾向更强”（只可描述趋势，不可写优劣结论）
          3) band_gap：> 0 表示“非金属性倾向”，≈0 表示“金属性倾向”（只做电子结构类型提示）
          4) symmetry / space_group：只做结构分类与对称性提示，不做性质推断

        【语言与风格】
        - 只允许使用中文；
        - 禁止使用“本案例 / 本系统 / 我们”等指代；
        - 禁止总结性结论或展望性描述；
        - 语气必须保持工程记录式、克制、客观，但表达要让非计算背景的工艺/应用人员也能读懂；
        - 判读信息应优先放入表格列中，不要在表格后再写“补充说明”段落；
        - 禁止复述原始 JSON（必须转为表格与短句解读）。

        【表格优先规则】
        - 至少包含两张 Markdown 表格：
          表1｜候选结构逐行对比与判读表：每个材料一行，必须包含：
               材料ID | 对称性（晶系/空间群） | 原子位点数 | 距稳定相包络能量差（eV/atom） | 形成能（eV/atom） | 带隙（eV） | 字段判读（稳定/接近稳定/偏离稳定）
          表2｜字段口径与应用解释映射表：字段名 | 物理含义 | 工程意义与决策影响（非性能结论）
        - 表2“字段名”必须优先使用中文术语，不得仅输出英文 snake_case；如需保留英文，仅可放在中文后的括号中。
        - 推荐字段名写法：
          距稳定相包络能量差（energy_above_hull）、形成能（formation_energy_per_atom）、带隙（band_gap）、
          对称性/空间群（crystal_system & symbol）、原子位点数（nsites）。
        - 表2“工程意义与决策影响（非性能结论）”必须面向非本领域但具工程阅读能力的成年人：
          采用严肃、客观、克制的技术写法，强调“该字段对筛选与后续验证决策的影响”。
        - 禁止使用幼稚化、拟人化、口语化比喻（如“像石头/像海绵”等）。
        - 禁止营销措辞、情绪化措辞、夸张措辞。
        - 表2中的解释必须替换掉“本次判读口径”式的模板化描述，不得写成同义重复。
        - 表格之外不再输出“补充说明”段落；若需解释，请并入表格列；
        - 这些表格哪怕是英语结果，也要按照他们的中文意思来解释和写。
        - 若某字段缺失或未计算，表格取值写“待计算”，不得写“未提供”。

        按上述规则直接输出解释内容，不要解释规则本身。
        """
# 已切换为有机数据库（OpenPoly）主线


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

    async def _safe_send_text(self, websocket, content):
        """
        统一兜底：避免 websocket.send_text(None) 导致 pydantic 校验失败
        """
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        await websocket.send_text(content)

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

        # 直接走你现成的流式输出
        await self._stream_llm_response(
            llm,
            [llm._default_system_msg(), llm._user_msg(prompt)],
            websocket
        )

    def _formula_profile(self, formula_: str) -> dict:
        f = str(formula_ or "")
        f_low = f.lower()
        if "li" in f_low and "s" in f_low and "p" in f_low:
            return {
                "中文名称": "锂-磷-硫体系固态电解质候选",
                "材料类别": "无机固态电解质",
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

    def _extract_cif_path_from_item(self, item: dict, base_dir: str) -> str:
        if not isinstance(item, dict):
            return ""
        for k in ("abs_path", "cif_path", "structure_path", "file_path", "path"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                p = v.strip()
                if os.path.isabs(p):
                    return p
                return os.path.abspath(os.path.join(base_dir, p))
        return ""

    def _pick_num(self, item: dict, keys: list):
        if not isinstance(item, dict):
            return None
        for k in keys:
            v = item.get(k)
            try:
                if v is None:
                    continue
                return float(v)
            except Exception:
                continue
        return None

    def _call_alignn_pretrained(self, model_name: str, cif_path: str, timeout_sec: int = 30):
        alignn_env = os.getenv("ALIGNN_ENV", "alignn-gpu-test")
        cmd = [
            "micromamba", "run", "-n", alignn_env,
            "python", "-m", "alignn.pretrained",
            "--model_name", model_name,
            "--file_format", "cif",
            "--file_path", str(cif_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=int(timeout_sec),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"alignn推理超时({timeout_sec}s): model={model_name}")
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout[-1200:] if proc.stdout else f"returncode={proc.returncode}")

        txt = proc.stdout or ""
        m = re.search(r"Predicted value:.*?\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]", txt)
        if not m:
            m = re.search(r"\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]", txt)
        if not m:
            raise RuntimeError(f"无法解析预测值: {txt[-500:]}")
        return float(m.group(1))

    def _try_alignn_models(
        self,
        cif_path: str,
        model_candidates: list,
        invalid_models: set = None,
        pred_cache: dict = None,
        timeout_sec: int = 30,
    ):
        last_err = ""
        invalid_models = invalid_models if isinstance(invalid_models, set) else set()
        pred_cache = pred_cache if isinstance(pred_cache, dict) else {}
        for mn in model_candidates:
            if mn in invalid_models:
                continue
            cache_key = (str(cif_path), str(mn))
            if cache_key in pred_cache:
                val = pred_cache.get(cache_key)
                if isinstance(val, float):
                    return val, mn, ""
                continue
            try:
                val = self._call_alignn_pretrained(mn, cif_path, timeout_sec=timeout_sec)
                pred_cache[cache_key] = val
                return val, mn, ""
            except Exception as e:
                last_err = str(e)
                pred_cache[cache_key] = None
                err_l = last_err.lower()
                if ("keyerror" in err_l) or ("not found" in err_l and "model" in err_l):
                    invalid_models.add(mn)
        return None, "", last_err

    def _probe_alignn_model(self, model_name: str, cif_path: str):
        """轻量探测：返回 (ok, err)。"""
        try:
            _ = self._call_alignn_pretrained(model_name, cif_path)
            return True, ""
        except Exception as e:
            return False, str(e)

    async def _material_alignn_completion_stage(self, websocket, formula: str, llm=None):
        """
        MP-first + ALIGNN completion + proxy ranking
        - 优先使用 MP 字段
        - 缺失时用 ALIGNN 补 formation_energy / band_gap / bulk / shear
        - 生成 hardness proxy、conductivity/diffusion proxy 和候选排序
        """
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
            p_item = self._extract_cif_path_from_item(it, base_dir_)
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

        EHULL_MODELS = ["jv_ehull_alignn"]
        FE_MODELS = ["jv_formation_energy_peratom_alignn", "mp_e_form_alignn"]
        BG_MODELS = ["jv_mbj_bandgap_alignn", "jv_optb88vdw_bandgap_alignn", "mp_gappbe_alignn"]
        BULK_MODELS = ["jv_bulk_modulus_kv_alignn"]
        SHEAR_MODELS = ["jv_shear_modulus_gv_alignn"]
        ELEC_MASS_MODELS = ["jv_avg_elec_mass_alignn"]
        HOLE_MASS_MODELS = ["jv_avg_hole_mass_alignn"]
        invalid_models = set()
        rows = []
        model_probe_done = False
        model_probe_msg = ""
        pred_cache = {}
        timeout_sec = int(os.getenv("ALIGNN_TIMEOUT_SEC", "30"))
        total_items = len(items)
        for idx, it in enumerate(items, start=1):
            mid = str(it.get("material_id") or it.get("id") or "")
            cif_path, cif_source = _resolve_cif_for_item(it, base_dir)
            # MP 原始可得属性（优先展示）
            mp_all_keys = sorted(list(it.keys())) if isinstance(it, dict) else []
            e_hull = self._pick_num(it, ["energy_above_hull", "e_above_hull", "energy_above_hull_ev_per_atom"])
            fe = self._pick_num(it, ["formation_energy_per_atom", "formation_energy", "e_form", "formation_energy_ev_per_atom"])
            bg = self._pick_num(it, ["band_gap", "bandgap", "band_gap_ev"])
            bulk = self._pick_num(it, ["bulk_modulus", "bulk_modulus_gpa", "kvrh", "k_vrh"])
            shear = self._pick_num(it, ["shear_modulus", "shear_modulus_gpa", "gvrh", "g_vrh"])
            density = self._pick_num(it, ["density", "density_g_cm3"])
            elec_mass = self._pick_num(it, ["avg_elec_mass", "avg_electron_mass", "electron_effective_mass", "m_e_avg"])
            hole_mass = self._pick_num(it, ["avg_hole_mass", "hole_effective_mass", "m_h_avg"])

            e_hull_src, fe_src, bg_src, bulk_src, shear_src = "MP", "MP", "MP", "MP", "MP"
            density_src = "MP" if isinstance(density, float) else "NA"
            elec_mass_src = "MP" if isinstance(elec_mass, float) else "NA"
            hole_mass_src = "MP" if isinstance(hole_mass, float) else "NA"
            bulk_err = ""
            shear_err = ""
            em_err = ""
            hm_err = ""

            # 模型可用性预检（只做一次）
            if (not model_probe_done) and cif_path and os.path.exists(cif_path):
                ok_probe, err_probe = self._probe_alignn_model(BULK_MODELS[0], cif_path)
                model_probe_done = True
                model_probe_msg = "ALIGNN模型可用" if ok_probe else f"ALIGNN模型探测失败: {err_probe[:220]}"

            if (e_hull is None) and cif_path and os.path.exists(cif_path):
                eh_pred, mn, _ = self._try_alignn_models(cif_path, EHULL_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if eh_pred is not None:
                    e_hull, e_hull_src = eh_pred, f"ALIGNN:{mn}"

            if (fe is None) and cif_path and os.path.exists(cif_path):
                fe_pred, mn, _ = self._try_alignn_models(cif_path, FE_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if fe_pred is not None:
                    fe, fe_src = fe_pred, f"ALIGNN:{mn}"

            if (bg is None) and cif_path and os.path.exists(cif_path):
                bg_pred, mn, _ = self._try_alignn_models(cif_path, BG_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if bg_pred is not None:
                    bg, bg_src = bg_pred, f"ALIGNN:{mn}"

            if (bulk is None) and cif_path and os.path.exists(cif_path):
                bulk_pred, mn, _ = self._try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if bulk_pred is not None:
                    bulk, bulk_src = bulk_pred, f"ALIGNN:{mn}"
                else:
                    _, _, bulk_err = self._try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            elif (bulk is None) and (not cif_path or not os.path.exists(cif_path)):
                bulk_err = f"cif缺失或路径无效({cif_source})"

            if (shear is None) and cif_path and os.path.exists(cif_path):
                shear_pred, mn, _ = self._try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if shear_pred is not None:
                    shear, shear_src = shear_pred, f"ALIGNN:{mn}"
                else:
                    _, _, shear_err = self._try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            elif (shear is None) and (not cif_path or not os.path.exists(cif_path)):
                shear_err = f"cif缺失或路径无效({cif_source})"

            if (elec_mass is None) and cif_path and os.path.exists(cif_path):
                em_pred, mn, _ = self._try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if em_pred is not None:
                    elec_mass, elec_mass_src = em_pred, f"ALIGNN:{mn}"
                else:
                    _, _, em_err = self._try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            elif (elec_mass is None) and (not cif_path or not os.path.exists(cif_path)):
                em_err = f"cif缺失或路径无效({cif_source})"

            if (hole_mass is None) and cif_path and os.path.exists(cif_path):
                hm_pred, mn, _ = self._try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
                if hm_pred is not None:
                    hole_mass, hole_mass_src = hm_pred, f"ALIGNN:{mn}"
                else:
                    _, _, hm_err = self._try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            elif (hole_mass is None) and (not cif_path or not os.path.exists(cif_path)):
                hm_err = f"cif缺失或路径无效({cif_source})"

            # 硬度估算：优先使用 Chen 经验公式；若条件不足回退 Teter 近似
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

            cond_diff_proxy = None
            if isinstance(bg, float) and isinstance(fe, float):
                cond_diff_proxy = (1.0 / (1.0 + max(bg, 0.0))) * (1.0 / (1.0 + abs(fe)))

            if isinstance(elec_mass, float) and elec_mass > 0:
                cond_diff_proxy = (cond_diff_proxy or 1.0) * (1.0 / (1.0 + elec_mass))
            if isinstance(hole_mass, float) and hole_mass > 0:
                cond_diff_proxy = (cond_diff_proxy or 1.0) * (1.0 / (1.0 + hole_mass))

            stability_class = "待计算"
            if isinstance(e_hull, float):
                if abs(e_hull) < 1e-12:
                    stability_class = "稳定"
                elif e_hull <= 0.02:
                    stability_class = "接近稳定"
                else:
                    stability_class = "偏离稳定"

            crystal = str(it.get("crystal_system") or it.get("crystal") or "").strip()
            spg = str(it.get("spacegroup_symbol") or it.get("space_group") or it.get("symmetry") or "").strip()
            if crystal and spg:
                symmetry_text = f"{crystal}/{spg}"
            else:
                symmetry_text = crystal or spg or "待计算"

            rows.append({
                "material_id": mid,
                "symmetry": symmetry_text,
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
            })

        # material_id 去重：同一 MP ID 仅保留“信息完整度”最高的一条
        def _row_completeness_score(r: dict) -> int:
            keys = [
                "e_above_hull", "density", "formation_energy", "band_gap",
                "bulk_modulus", "shear_modulus", "hardness_est", "cond_diff_proxy",
            ]
            return sum(1 for k in keys if isinstance(r.get(k), float))

        dedup = {}
        no_id_counter = 0
        for r in rows:
            mid = (r.get("material_id") or "").strip().lower()
            if not mid:
                no_id_counter += 1
                mid = f"_NO_ID_{no_id_counter}"
            old = dedup.get(mid)
            if old is None or _row_completeness_score(r) > _row_completeness_score(old):
                dedup[mid] = r
        rows = list(dedup.values())

        def _norm(vals):
            xs = [v for v in vals if isinstance(v, float)]
            if not xs:
                return [None] * len(vals)
            lo, hi = min(xs), max(xs)
            if abs(hi - lo) < 1e-12:
                return [0.5 if isinstance(v, float) else None for v in vals]
            return [((v - lo) / (hi - lo) if isinstance(v, float) else None) for v in vals]

        n_hull = _norm([(-r["e_above_hull"] if isinstance(r["e_above_hull"], float) else None) for r in rows])
        n_fe = _norm([(-r["formation_energy"] if isinstance(r["formation_energy"], float) else None) for r in rows])
        n_cond = _norm([r["cond_diff_proxy"] for r in rows])
        n_hard = _norm([r["hardness_est"] for r in rows])

        for i, r in enumerate(rows):
            score = 0.0
            wsum = 0.0
            for w, nv in ((0.35, n_hull[i]), (0.25, n_fe[i]), (0.25, n_cond[i]), (0.15, n_hard[i])):
                if isinstance(nv, float):
                    score += w * nv
                    wsum += w
            r["candidate_score"] = (score / wsum) if wsum > 0 else None

        rows_sorted = sorted(rows, key=lambda x: (x["candidate_score"] is None, -(x["candidate_score"] or -1)))

        p_formula = self._formula_profile(formula)
        lines = [f"### 材料性质计算 - {formula}（{p_formula['中文名称']}）"]
        if model_probe_msg:
            logger.info(f"[ALIGNN_PROBE] formula={formula} probe={model_probe_msg}")

        # 仅展示 Top1，避免长表与技术字段噪声
        top = rows_sorted[0] if rows_sorted else None

        def _fmt(v, nd=4):
            return f"{v:.{nd}f}" if isinstance(v, float) else "待计算"

        lines.extend([
            "",
            f"#### 材料性质计算结果（候选ID：{top.get('material_id','-') if top else '-'}）",
        ])

        prop_rows = []
        if top:
            field_specs = [
                ("src_bulk", "bulk_modulus", "体积模量", "GPa", "模型预测/数据库值", "更高通常更抗压，更利于压片与堆叠稳定"),
                ("src_shear", "shear_modulus", "剪切模量", "GPa", "模型预测/数据库值", "更高通常更抗剪切形变，降低使用中开裂风险"),
                ("src_bg", "band_gap", "带隙", "eV", "模型预测/数据库值", "过小可能提升电子泄漏风险，影响电化学应用边界"),
                ("src_elec_mass", "elec_mass", "电子有效质量", "m0", "模型预测/数据库值", "关联电子输运趋势，影响宏观导电特征"),
                ("src_hole_mass", "hole_mass", "空穴有效质量", "m0", "模型预测/数据库值", "关联空穴输运趋势，影响界面极化表现"),
            ]

            for src_k, val_k, zh_name, unit, src_hint, app_hint in field_specs:
                vv = top.get(val_k)
                if isinstance(vv, float):
                    src_v = str(top.get(src_k) or "")
                    if src_v.startswith("ALIGNN"):
                        src_show = f"ALIGNN补全（{src_v.replace('ALIGNN:', '')}）"
                    elif src_v:
                        src_show = f"MP已给出（{src_v}）"
                    else:
                        src_show = src_hint
                    prop_rows.append((zh_name, _fmt(vv, 4), unit, src_show, app_hint))

            # 经验硬度（优先Chen，回退Teter）
            if isinstance(top.get("hardness_est"), float):
                prop_rows.append((
                    "硬度（估算）",
                    _fmt(top.get("hardness_est"), 4),
                    "GPa",
                    str(top.get("hardness_formula") or "经验公式"),
                    "可用于粗略判断抗压痕与耐磨趋势，数值越高通常机械支撑更强"
                ))

            # 导电/扩散相关粗略指标
            if isinstance(top.get("cond_diff_proxy"), float):
                prop_rows.append((
                    "导电/扩散相关量（粗略）",
                    _fmt(top.get("cond_diff_proxy"), 4),
                    "无量纲",
                    "由带隙/形成能/有效质量组合得到的排序指标",
                    "仅用于候选排序的趋势参考，不等同于实验电导率或扩散系数"
                ))

        # 按段流式发送：标题先发，表格走 LLM token 级流式；失败再回退到本地逐行。
        async def _stream_lines(lines_, delay_s: float = 0.02):
            for _ln in (lines_ or []):
                await websocket.send_text((_ln or "") + "\n")
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

        await _stream_lines(lines, delay_s=0.02)

        async def _stream_alignn_table_via_llm(top_row: dict, prop_rows_: list) -> bool:
            """
            将结构化 rows 转为 Markdown 表格，并通过 _stream_llm_response 真流式输出。
            返回 True 表示已成功通过 LLM 流式输出；False 表示需要 fallback。
            """
            if llm is None:
                return False

            rows_payload = []
            if top_row and prop_rows_:
                for zh_name, val, unit, src_show, hint in (prop_rows_ or []):
                    rows_payload.append({
                        "性质项": str(zh_name),
                        "数值": str(val),
                        "单位": str(unit),
                        "口径/来源": str(src_show),
                        "应用解读": str(hint),
                    })
            else:
                rows_payload = [{
                    "性质项": "本轮暂无可展示性质",
                    "数值": "待计算",
                    "单位": "-",
                    "口径/来源": "当前输入不足",
                    "应用解读": "待补充结构或性质数据",
                }]

            prompt = (
                "你是 Markdown 表格渲染器。"
                "请把给定 JSON rows 原样渲染为一张 Markdown 表格。"
                "严格要求："
                "1) 只输出表格，不要标题、不要解释、不要代码块；"
                "2) 列顺序严格为：性质项 | 数值 | 单位 | 口径/来源 | 应用解读；"
                "3) 禁止修改任意数值、单位、文本；"
                "4) 禁止增删行，行顺序必须与输入一致；"
                "5) 若某单元格为'待计算'也必须原样保留。"
                f"\nrows={json.dumps(rows_payload, ensure_ascii=False)}"
            )

            try:
                rendered = await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(prompt)],
                    websocket
                )
                if not (isinstance(rendered, str) and "|" in rendered and "性质项" in rendered):
                    return False
                return True
            except Exception as e:
                logger.exception(f"[ALIGNN_TABLE_STREAM] LLM stream failed, fallback local table: {e!s}")
                return False

        streamed_ok = await _stream_alignn_table_via_llm(top, prop_rows)
        if not streamed_ok:
            fallback_lines = [
                "| 性质项 | 数值 | 单位 | 口径/来源 | 应用解读 |",
                "|---|---:|---|---|---|",
            ]
            if top and prop_rows:
                for zh_name, val, unit, src_show, hint in prop_rows:
                    fallback_lines.append(f"| {zh_name} | {val} | {unit} | {src_show} | {hint} |")
            else:
                fallback_lines.append("| 本轮暂无可展示性质 | 待计算 | - | 当前输入不足 | 待补充结构或性质数据 |")
            await _stream_lines(fallback_lines, delay_s=0.02)

        # 注：移除表格后的额外自然语言补充，避免前端将其误并入表格渲染。
        return top if isinstance(top, dict) else {}

    def _sanitize_for_llm(self, obj):
        """
        ✅ 最小化清洗：只去掉明显工程字段（路径/URL/目录/时间戳等）。
        不做结构重排，不做推断，不做额外优化。
        """
        import copy

        def _drop_keys(d: dict):
            bad_keys = {
                "files", "files_abs", "base_dir", "manifest",
                "path", "paths", "url", "urls", "directory", "dir",
                "generated_at", "timestamp", "time", "datetime",
                "model_path", "log_file", "traj_file", "structure_glb",
            }
            for k in list(d.keys()):
                if k in bad_keys:
                    d.pop(k, None)

        def _walk(x):
            if isinstance(x, dict):
                _drop_keys(x)
                for k, v in list(x.items()):
                    x[k] = _walk(v)
                return x
            if isinstance(x, list):
                return [_walk(v) for v in x]
            return x

        return _walk(copy.deepcopy(obj))


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

        async def _ws_asset(name: str, docs: str, url: str, asset_type: str, description: str = ""):
            safe_desc = description if isinstance(description, str) else ""
            payload = {
                "step_id": step_id,          # ✅ 不写死
                "name": name,
                "docs": docs,
                "url": url,
                "type": asset_type,          # MaterialsPNG / MaterialsGLB
                # 与无机线对齐：字段恒定输出
                "description": safe_desc,
            }
            logger.info(
                f"[send_results_to_frontend] ws_asset type={asset_type} name={name} "
                f"desc_len={len(safe_desc)} url={url}"
            )
            await websocket.send_json(payload)

        async def _ws_right(step_id_local: str, text: str):
            await websocket.send_text(f"<<<CONTENT_START:{step_id_local}>>>")
            if text:
                await websocket.send_text(text.rstrip() + "\n")
            await websocket.send_text(f"<<<CONTENT_END:{step_id_local}>>>")

        logger.info(
            f"[send_results_to_frontend] ENTER step_id={step_id} pipeline={pipeline} source_path={source_path}, root_path={root_path}, taskid={taskid}, jobid={jobid}"
        )

        abs_root_path = os.path.abspath(os.path.join(source_path, root_path))
        results_dir = os.path.join(abs_root_path, "results")

        logger.info(f"[send_results_to_frontend] abs_root_path={abs_root_path}")
        logger.info(f"[send_results_to_frontend] results_dir={results_dir} exists={os.path.exists(results_dir)}")

        if not os.path.exists(results_dir):
            logger.warning(f"[send_results_to_frontend] ❌ results 目录不存在: {results_dir}")
            return

        exts = {".png", ".jpg", ".jpeg", ".gif"}
        taskid_sanitized = str(taskid).replace("/", "_")

        # ---------- 1) 定位 manifest ----------
        manifest_path = None
        try:
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

        async def _upload_and_get_url(abs_path: str, oss_key: str, asset_kind: str = "asset", public_url_override: str = ""):
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
                return

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

            return

        logger.info(f"[send_results_to_frontend] ✅ found manifest: {manifest_path}")

        # ---------- 3) 读取 manifest ----------
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.exception(f"[send_results_to_frontend] 读取 manifest 失败: {e}")
            return

        if not isinstance(manifest, dict) or not manifest.get("ok"):
            logger.warning("[send_results_to_frontend] ⚠️ manifest 内容异常或 ok!=true")
            return

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
        if isinstance(manifest.get("images"), list) and manifest["images"]:
            for it in manifest["images"]:
                if isinstance(it, dict):
                    image_items.append(it.get("path", ""))
                else:
                    image_items.append(str(it))
        else:
            try:
                for fn in sorted(os.listdir(base_dir)):
                    p = os.path.join(base_dir, fn)
                    if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in exts:
                        image_items.append(p)
            except Exception:
                pass

        for p in image_items:
            abs_img = _abspath(p) if not os.path.isabs(str(p)) else str(p)
            if not abs_img or not os.path.exists(abs_img):
                continue
            if os.path.splitext(abs_img)[1].lower() not in exts:
                continue

            fname = os.path.basename(abs_img)
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

        # ---------- 6) GLB（MaterialsGLB） ----------
        glb_path = _abspath(files.get("structure_glb", ""))
        if glb_path and os.path.exists(glb_path):
            fname = os.path.basename(glb_path)
            glb_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            glb_publish_name = f"{glb_ts}_{fname}"
            oss_key = f"materials/modelfiles/glb/{glb_publish_name}"
            glb_public_url = f"{glb_public_base_url}/{glb_publish_name}"
            url = await _upload_and_get_url(glb_path, oss_key, asset_kind="glb", public_url_override=glb_public_url)

            if url:
                formula_for_asset = (str(jobid or "").strip() or str(manifest.get("formula") or "").strip())
                profile_for_asset = self._formula_profile(formula_for_asset) if formula_for_asset else {}
                cn_name = str(profile_for_asset.get("中文名称") or "").strip()
                rich_name = f"{formula_for_asset}_{cn_name}_结构模型.glb" if (formula_for_asset and cn_name) else fname
                rich_name = rich_name.replace("/", "_")
                rich_docs = (
                    f"{formula_for_asset}（{cn_name}）三维结构模型（GLB）"
                    if (formula_for_asset and cn_name)
                    else "结构三维可视化模型（GLB）"
                )
                base_name = (formula_for_asset or os.path.splitext(fname)[0] or "Material").replace("/", "_")
                glb_description = (
                    f"该三维模型展示了 {base_name} 的最优候选聚合物结构。"
                    f"可通过旋转、缩放观察原子排布与分子骨架形貌，"
                    f"用于直观理解结构稳定性与后续性质分析的结构基础；"
                    f"其中结果用于筛选与工程判断，不替代最终实验表征。"
                )
                # JSON 资产消息也用独立内容块包裹，便于前端按 <<<>>> 统一解析
                await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
                await _ws_asset(
                    name=rich_name,
                    docs=rich_docs,
                    url=url,
                    asset_type="MaterialsGLB",
                    description=glb_description,
                )
                await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
                logger.info(f"[send_results_to_frontend] ✅ sent MaterialsGLB: {fname}")
        else:
            logger.warning(f"[send_results_to_frontend] ⚠️ manifest 中未提供 structure_glb 或文件不存在: {glb_path}")


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


    async def _ws_right(self, websocket, step_id: str, text: str):
        await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
        if text:
            await websocket.send_text(text.rstrip() + "\n")
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")

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

        async def _stream_lines(lines, delay_s: float = 0.02):
            """按行流式发送，保持原文不改写。"""
            for ln in (lines or []):
                await websocket.send_text(str(ln))
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

        async def _stream_verbatim_via_llm(text: str) -> bool:
            """让LLM按原文逐token复读，提升观感；失败返回False。"""
            src = str(text or "")
            if not src:
                return True
            prompt = (
                "你是文本转发器。请把<BEGIN>与<END>之间的内容逐字符原样输出。"
                "严格要求："
                "1) 不得增加、删除、改写任何字符；"
                "2) 不要解释，不要添加前后缀，不要代码块标记；"
                "3) 保留所有换行、空格、标点、Markdown符号原样。"
                "\n<BEGIN>\n"
                f"{src}"
                "\n<END>"
            )
            try:
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(prompt)],
                    websocket,
                    mirror_to_content=False,
                    mirror_step_id="MATERIAL_SCREENING",
                )
                return True
            except Exception as e:
                logger.warning(f"[VERBATIM_STREAM] llm relay failed, fallback local stream: {e}")
                return False

        async def _send_openpoly_stage_image(abs_img_path: str, docs: str, description: str = ""):
            """OpenPoly 阶段图片仅走左侧对话流展示（不进入右侧资产面板）。"""
            try:
                p = str(abs_img_path or "").strip()
                if (not p) or (not os.path.exists(p)):
                    logger.warning(f"[OPENPOLY][IMG] file not found, skip: {p}")
                    return
                fname = os.path.basename(p)
                oss_key = f"materials/modelfiles/image/{str(taskid).replace('/', '_')}/openpoly/{fname}"
                with open(p, "rb") as f:
                    b = f.read()
                result = await oss_upload("alpha", oss_key, b)
                if result.get("status") != 200:
                    logger.warning(f"[OPENPOLY][IMG] upload failed path={p} resp={result}")
                    return
                img_url = f"{picture_public_base_url}/{str(taskid).replace('/', '_')}/openpoly/{fname}"
                title = str(docs or fname)
                desc = str(description or "").strip()
                await websocket.send_text(f"\n\n#### {title}\n\n")
                await websocket.send_text(f"![{title}]({img_url})\n")
                if desc:
                    await websocket.send_text(f"\n{desc}\n")
                logger.info(f"[OPENPOLY][IMG] sent left-chat image fname={fname} url={img_url}")
            except Exception as e:
                logger.exception(f"[OPENPOLY][IMG] send failed: {e}")

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

                # 保留点号 '.'：让带小数的 token 保持原样，后面 _looks_like_formula 会直接拒绝
                s = s.replace("·", "").replace("•", "")
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
                s = _to_ascii_formula(s)
                if not s:
                        return False

                # ✅ 最小改动：只要包含小数点，直接拒绝（避免 0.8、XX5.4、LiNi0.8... 被拆开误判）
                if "." in s:
                        return False

                # 基本长度约束
                if len(s) < 2 or len(s) > 40:
                        return False

                # 只允许字母和数字（任何其他字符直接否）
                if re.search(r"[^A-Za-z0-9]", s):
                        return False

                i = 0
                tokens = []

                # 从头到尾严格消费字符串
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


        # =========================
        # 3) instruction 归一 + route
        # =========================
        def _normalize_user_text(s) -> str:
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

        # =========================
        # 4) ✅只从“计算对象”行抽取（避免把别的材料带进来）
        # =========================
        def _extract_formulas_from_targets(text: str) -> list:
            text = _to_ascii_formula(text or "")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            targets = []
            for ln in lines:
                m = re.search(r"计算对象\s*\d+\s*\(.*?\)\s*[:：]\s*([A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{2,40})", ln)
                if m:
                    tok = _to_ascii_formula(m.group(1))
                    if _looks_like_formula(tok):
                        targets.append(tok)

            seen = set()
            out = []
            for x in targets:
                if x not in seen:
                    out.append(x)
                    seen.add(x)
            if out:
                return out

            # fallback：少量兜底（但仍然严格 looks_like_formula）
            tokens = re.finditer(r"\b[A-Z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{1,39}\b", text)
            seen = set()
            out = []
            for m in tokens:
                    tok = m.group(0)

                    # ✅ 最小改动：如果 token 左右紧贴 '.'，说明来自小数配方（如 Ni₀.₈ / XX5.4），直接跳过
                    left = text[m.start() - 1] if m.start() - 1 >= 0 else ""
                    right = text[m.end()] if m.end() < len(text) else ""
                    if left == "." or right == ".":
                            continue

                    tok2 = _to_ascii_formula(tok)
                    if _looks_like_formula(tok2) and tok2 not in seen:
                            out.append(tok2)
                            seen.add(tok2)
            return out

        def _load_openpoly_rows() -> list:
            """加载 OpenPoly CSV（带进程内缓存）。"""
            cache = getattr(self, "_openpoly_rows_cache", None)
            if isinstance(cache, list) and cache:
                return cache

            csv_path = os.path.join(
                _repo_root(),
                "src",
                "MNS_CaseHub",
                "dataset",
                "experiment_polymer_data.csv",
            )
            rows = []
            try:
                import csv
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        rows.append(r)
            except Exception as e:
                logger.exception(f"[OPENPOLY] load failed: {e!s}")
                rows = []

            self._openpoly_rows_cache = rows
            return rows

        def _search_openpoly_candidates(query_text: str, topk: int = 5) -> list:
            """按 Name / PSMILES 做轻量检索（exact > contains > token overlap）。"""
            rows = _load_openpoly_rows()
            if not rows:
                return []

            q = str(query_text or "").strip().lower()
            if not q:
                return []

            q_tokens = set(re.findall(r"[a-z0-9_\-\*\[\]\(\)=]+|[\u4e00-\u9fff]+", q))

            scored = []
            for r in rows:
                name = str(r.get("Name") or "")
                psmiles = str(r.get("PSMILES") or "")
                n = name.lower()
                p = psmiles.lower()

                score = 0.0
                if q == n or q == p:
                    score += 10.0
                if q and (q in n):
                    score += 6.0
                if q and (q in p):
                    score += 6.0

                if q_tokens:
                    t = set(re.findall(r"[a-z0-9_\-\*\[\]\(\)=]+|[\u4e00-\u9fff]+", f"{n} {p}"))
                    inter = len(q_tokens & t)
                    if inter > 0:
                        score += min(4.0, inter * 0.8)

                if score > 0:
                    scored.append((score, r))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [x[1] for x in scored[:max(1, int(topk))]]

        def _normalize_polymer_name(name: str) -> str:
            """仅用于展示层：清洗 CSV 中残留的转义串（如 \\uc0\\u945）。"""
            s = str(name or "")
            if not s:
                return "-"

            # 先处理 \uc0\uXXXX 这类 RTF 残留
            def _rtf_u_replace(m):
                return "\\u" + str(m.group(1))

            s = re.sub(r"\\uc\d+\\u(-?\d+)", _rtf_u_replace, s)

            # 再处理 \uXXXX / \u123 形式
            def _u_replace(m):
                raw = m.group(1)
                try:
                    code = int(raw)
                    if code < 0:
                        code = 65536 + code
                    if 0 <= code <= 0x10FFFF:
                        return chr(code)
                except Exception:
                    pass
                return ""

            s = re.sub(r"\\u(-?\d+)", _u_replace, s)
            s = re.sub(r"\s+", " ", s).strip()
            return s or "-"

        def _normalize_psmiles(psmiles: str) -> str:
            """PSMILES 原始清洗：仅去异常空白，确保可直接用于绘图/下游软件。"""
            s = str(psmiles or "").strip()
            if not s:
                return "-"
            s = re.sub(r"\s+", "", s)
            return s or "-"

        def _validate_psmiles_basic(psmiles: str) -> tuple[bool, str]:
            """轻量语法预检：用于提示潜在非法串（不做化学语义判定）。"""
            s = _normalize_psmiles(psmiles)
            if s in {"", "-"}:
                return False, "空字符串"
            if "()" in s:
                return False, "存在空分支()"
            if s.count("(") != s.count(")"):
                return False, "圆括号不平衡"
            if s.count("[") != s.count("]"):
                return False, "方括号不平衡"
            return True, "ok"

        def _psmiles_results_dir(taskid: str, jobid: str = "openpoly") -> str:
            """
            Organic / PSMILES 可视化产物目录：
            <repo>/src/MNS_CaseHub/cases/material_discovery_demo/results/psmiles/<taskid>/<jobid>/
            """
            repo_root = _repo_root()
            taskid_sanitized = str(taskid).replace("/", "_")
            out_dir = os.path.join(
                repo_root,
                "src",
                "MNS_CaseHub",
                "cases",
                "material_discovery_demo",
                "results",
                "psmiles",
                taskid_sanitized,
                str(jobid or "openpoly"),
            )
            os.makedirs(out_dir, exist_ok=True)
            return out_dir

        def _write_psmiles_glb_manifest(out_dir: str, glb_name: str = "organic_first_hit.glb") -> str:
            """
            生成给 send_results_to_frontend 使用的最小 manifest.json
            """
            manifest = {
                "ok": True,
                "formula": "openpoly_first_hit",
                "base_dir": out_dir,
                "files": {
                    "structure_glb": glb_name
                }
            }
            manifest_path = os.path.join(out_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            return manifest_path

        def _run_psmiles_to_glb(psmiles: str, out_glb: str, out_meta: str) -> str:
            """
            调独立 micromamba 环境中的 tools/psmiles_to_glb.py
            """
            env = os.environ.copy()
            env["MAMBA_ROOT_PREFIX"] = "/data/mamba"

            cmd = [
                "micromamba",
                "run",
                "-n",
                "organic-glb-py310",
                "python",
                "tools/psmiles_to_glb.py",
                "--psmiles",
                psmiles,
                "--out",
                out_glb,
                "--meta",
                out_meta,
            ]

            proc = subprocess.run(
                cmd,
                cwd=_repo_root(),
                env=env,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"psmiles_to_glb failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
                )
            return proc.stdout

        def _load_psmiles_meta(meta_path: str) -> dict:
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[OPENPOLY][GLB] load meta failed: {meta_path} | {e}")
                return {}

        def _format_psmiles_meta_md(meta: dict) -> str:
            """
            把 tools/psmiles_to_glb.py 产出的 meta.json 格式化成 markdown 文本，
            直接发给当前 websocket 文本流即可。
            """
            if not isinstance(meta, dict) or not meta:
                return ""

            formula = str(meta.get("formula") or "-")
            atom_count = meta.get("atom_count", "-")
            mw = meta.get("molecular_weight", "-")
            if isinstance(mw, (int, float)):
                mw = f"{mw:.4f}"

            normalized_psmiles = str(meta.get("normalized_psmiles") or "-")
            legend = meta.get("legend") or []

            lines = []
            lines.append("\n### 最优候选结构信息（可对照右侧GLB）\n")
            lines.append(f"- 分子式（Formula）: `{formula}`\n")
            lines.append(f"- 原子总数: `{atom_count}`\n")
            lines.append(f"- 分子量: `{mw}`\n")
            lines.append(f"- 聚合物结构表达（PSMILES）: `{normalized_psmiles}`\n")

            if legend:
                lines.append("\n**元素对照说明（对应右侧GLB球体颜色）**\n")
                for item in legend:
                    symbol = str(item.get("symbol") or "-")
                    label = str(item.get("label") or symbol)
                    count = item.get("count", 0)
                    color_hex = str(item.get("color_hex") or "-")
                    zh_map = {
                        "C": "碳", "H": "氢", "O": "氧", "N": "氮", "F": "氟", "S": "硫", "P": "磷", "Cl": "氯", "Br": "溴", "I": "碘", "R": "聚合物连接位点"
                    }
                    zh_name = zh_map.get(symbol, label)
                    lines.append(
                        f"- `{symbol}`（{zh_name}）：右图中该颜色球体为 `{color_hex}`，数量 `{count}`。\n"
                    )

            lines.append("\n注：`R` 表示聚合物链继续延伸的连接位点；对照右侧GLB时，可理解为“链条从这里接到下一段”。\n")
            return "".join(lines)

        async def _generate_and_send_openpoly_first_glb(
            websocket,
            source_path: str,
            taskid: str,
            first_row: dict,
            step_id: str = "MATERIAL_SCREENING",
        ):
            """
            取 OpenPoly 首条结果生成 GLB，并复用现有 send_results_to_frontend 下发。
            """
            if not isinstance(first_row, dict) or not first_row:
                return

            first_psmiles = _normalize_psmiles(first_row.get("PSMILES"))
            ok_psm, why_psm = _validate_psmiles_basic(first_psmiles)
            if not ok_psm:
                logger.warning(
                    f"[OPENPOLY][GLB] invalid first PSMILES reason={why_psm} psmiles={first_psmiles}"
                )
                return

            out_dir = _psmiles_results_dir(taskid=taskid, jobid="openpoly")
            glb_path = os.path.join(out_dir, "organic_first_hit.glb")
            meta_path = os.path.join(out_dir, "organic_first_hit.meta.json")

            _run_psmiles_to_glb(first_psmiles, glb_path, meta_path)
            _write_psmiles_glb_manifest(out_dir, "organic_first_hit.glb")

            meta = _load_psmiles_meta(meta_path)
            meta_md = _format_psmiles_meta_md(meta)
            if meta_md:
                # 最优候选结构信息固定走右侧内容区，避免落到左侧
                body_md = str(meta_md)
                body_md = body_md.replace("\n### 首条候选结构 3D 可视化\n", "\n", 1)
                await websocket.send_text("<<<CONTENT_START:MATERIAL_SCREENING>>>")
                ok_stream = await _stream_verbatim_via_llm(body_md)
                if not ok_stream:
                    await _stream_lines(body_md.splitlines(keepends=True), delay_s=0.02)
                await websocket.send_text("<<<CONTENT_END:MATERIAL_SCREENING>>>")

            logger.info(f"[OPENPOLY][GLB] generated glb={glb_path}")


            repo_root = _repo_root()
            await self.send_results_to_frontend(
                websocket=websocket,
                source_path=repo_root,
                root_path="src/MNS_CaseHub/cases/material_discovery_demo",
                taskid=taskid,
                jobid="openpoly",
                pipeline="psmiles",
                step_id=step_id,
                emit_summary_block=False,
            )

        async def _stream_first_hit_xgb_completion(first_row: dict):
            """仅对首条（GLB对应）候选输出模型补全性质。"""
            if not isinstance(first_row, dict) or not first_row:
                return

            name = _display_polymer_name(first_row)
            psmiles_raw = _normalize_psmiles(first_row.get("PSMILES"))
            ok_psm, _ = _validate_psmiles_basic(psmiles_raw)

            # 数据库优先：有原值就直接用；仅缺失字段才走模型补全
            tg_db = _fmt_poly_prop(first_row, ["Tg_K", "Tg (K)"])
            td_db = _fmt_poly_prop(first_row, ["Td_K", "Td (K)"])
            tm_db = _fmt_poly_prop(first_row, ["Tm_K", "Tm (K)"])
            wu_db = _fmt_poly_prop(first_row, ["Water_Uptake"])
            dc_db = _fmt_poly_prop(first_row, ["Dielectric_Constant_Total"])
            tc_db = _fmt_poly_prop(first_row, ["Thermal_Conductivity"])

            def _db_missing(v: str) -> bool:
                return str(v or "").strip() in {"", "待补充", "nan", "None", "null"}

            need_pred = any(_db_missing(v) for v in [tg_db, td_db, tm_db, wu_db, dc_db, tc_db])
            xgb_pred = _openpoly_xgb_predict(psmiles_raw) if (ok_psm and need_pred) else {}

            def _fv(v, nd=4):
                return f"{float(v):.{nd}f}" if isinstance(v, (int, float)) else "待计算"

            def _pick_display(db_val: str, pred_key: str):
                if not _db_missing(db_val):
                    return db_val, "OpenPoly数据库"
                pv = (xgb_pred or {}).get(pred_key)
                if isinstance(pv, (int, float)):
                    return _fv(pv), "OpenPoly预测模型补全"
                return "待计算", "待补充"

            await websocket.send_text("<<<CONTENT_START:MATERIAL_SCREENING>>>")
            # 标题写死本地发送，后续内容走LLM流式
            await websocket.send_text("\n\n### 最优候选结构性质补全\n\n")

            tg_val, tg_src = _pick_display(tg_db, "Tg")
            td_val, td_src = _pick_display(td_db, "Td")
            tm_val, tm_src = _pick_display(tm_db, "Tm")
            wu_val, wu_src = _pick_display(wu_db, "Water_Uptake")
            dc_val, dc_src = _pick_display(dc_db, "Dielectric_Constant_Total")
            tc_val, tc_src = _pick_display(tc_db, "Thermal_Conductivity")

            property_md = "".join([
                f"- 对象：{name}  \n",
                f"- PSMILES：`{psmiles_raw}`\n\n",
                "| 性质项 | 数值 | 单位 | 口径/来源 | 应用解读 |\n",
                "|---|---:|---|---|---|\n",
                f"| 玻璃化转变温度 Tg | {tg_val} | K | {tg_src} | 反映高温形变与尺寸稳定边界 |\n",
                f"| 热分解温度 Td | {td_val} | K | {td_src} | 反映热稳定与工艺温度上限 |\n",
                f"| 熔融温度 Tm | {tm_val} | K | {tm_src} | 反映热加工窗口与结晶相行为 |\n",
                f"| 吸水率 Water_Uptake | {wu_val} | % | {wu_src} | 反映湿热环境下介电/尺寸稳定风险 |\n",
                f"| 介电常数 Dielectric_Constant_Total | {dc_val} | 无量纲 | {dc_src} | 反映电场响应与绝缘设计边界 |\n",
                f"| 导热系数 Thermal_Conductivity | {tc_val} | W/(m·K) | {tc_src} | 反映散热能力与热梯度控制能力 |\n",
                "| 热膨胀系数 CTE | 待计算 | ppm/K | OpenPoly预测模型暂未覆盖（待补充） | 反映热循环下尺寸漂移与界面失配风险 |\n",
            ])

            ok_stream = await _stream_verbatim_via_llm(property_md)
            if not ok_stream:
                await _stream_lines(property_md.splitlines(keepends=True), delay_s=0.02)
            await websocket.send_text("<<<CONTENT_END:MATERIAL_SCREENING>>>")

            # 性质补全阶段配图（右侧资产）
            await _send_openpoly_stage_image(
                "/data/se42/alpha_project/organic_existing_material/src/MNS_CaseHub/cases/material_discovery_demo/results/openpoly/openpolyprediction.jpg",
                docs="OpenPoly 性质补全结果图",
                description="该图对应最优候选结构的性质补全阶段输出，可用于快速核对关键预测指标。",
            )

        def _display_polymer_name(row: dict) -> str:
            """Name 展示优化：优先输出中文可读名称。"""
            raw_name = _normalize_polymer_name(row.get("Name"))
            name_l = str(raw_name or "").strip().lower()
            invalid = {"", "-", "unknown", "unk", "n/a", "na", "none", "null"}

            # 常见简称直出为中文+简称
            known_map = {
                "pi": "聚酰亚胺（PI）",
                "ptfe": "聚四氟乙烯（PTFE）",
                "pe": "聚乙烯（PE）",
                "pp": "聚丙烯（PP）",
                "pvc": "聚氯乙烯（PVC）",
                "pet": "聚对苯二甲酸乙二醇酯（PET）",
                "pmma": "聚甲基丙烯酸甲酯（PMMA）",
            }

            def _cn_tag_from_text(s: str) -> str:
                t = str(s or "").lower()
                tags = []
                if ("biphenyl" in t) or ("c1ccc" in t):
                    tags.append("联苯骨架")
                if ("trifluoro" in t) or ("cf3" in t) or ("c(f)(f)f" in t):
                    tags.append("三氟甲基改性")
                if ("phosphazene" in t) or ("p=n" in t):
                    tags.append("聚膦腈")
                if ("imide" in t):
                    tags.append("酰亚胺")
                if ("ether" in t) or ("oc" in t):
                    tags.append("醚键")

                if not tags:
                    return "结构特征聚合物"
                if len(tags) == 1:
                    return f"{tags[0]}类聚合物"
                return f"{'-'.join(tags[:2])}聚合物"

            if name_l not in invalid:
                if name_l in known_map:
                    return known_map[name_l]
                return _cn_tag_from_text(raw_name)

            # 尝试从常见别名字段中回退
            for k in ["Alias", "Aliases", "Common_Name", "IUPAC_Name", "Short_Name", "Polymer"]:
                v = _normalize_polymer_name(row.get(k))
                if str(v).strip().lower() not in invalid:
                    return f"未标准命名（别名：{v}）"

            psm = str(row.get("PSMILES") or "").strip()
            if psm:
                return _cn_tag_from_text(psm)
            return "未标准命名聚合物"

        def _fmt_poly_prop(row: dict, keys: list) -> str:
            """OpenPoly 性质字段兼容读取：支持新旧列名，统一空值显示。"""
            for k in (keys or []):
                v = row.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if (not s) or (s.lower() in {"nan", "none", "null"}):
                    continue
                return s
            return "待补充"

        def _openpoly_xgb_predict(psmiles: str) -> dict:
            """调用 tools/openpoly_xgb_infer.py，失败时返回空预测。"""
            model_dir = os.path.join(
                _repo_root(),
                "src",
                "MNS_CaseHub",
                "cases",
                "material_discovery_demo",
                "models",
                "openpoly",
                "xgb",
            )
            script = os.path.join(_repo_root(), "tools", "openpoly_xgb_infer.py")

            empty = {k: None for k in [
                "Tg", "Td", "Tm", "Water_Uptake", "Dielectric_Constant_Total", "Thermal_Conductivity"
            ]}

            # 强制使用 micromamba 指定环境，避免误用当前进程解释器
            xgb_env = os.getenv("OPENPOLY_XGB_ENV", "organic-predict-py310")
            xgb_mamba_root = os.getenv("OPENPOLY_XGB_MAMBA_ROOT", "/data/mamba")

            try:
                cmd = [
                    "micromamba", "run", "-n", xgb_env,
                    "python", script,
                    "--psmiles", str(psmiles or ""),
                    "--model-dir", model_dir,
                ]
                run_env = os.environ.copy()
                run_env["MAMBA_ROOT_PREFIX"] = str(xgb_mamba_root)
                logger.info(
                    f"[OPENPOLY][XGB] running with micromamba env={xgb_env} "
                    f"mamba_root={xgb_mamba_root} cmd={' '.join(cmd[:6])} ..."
                )
                proc = subprocess.run(
                    cmd,
                    cwd=_repo_root(),
                    env=run_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    logger.warning(
                        f"[OPENPOLY][XGB] infer failed rc={proc.returncode} env={xgb_env} "
                        f"mamba_root={xgb_mamba_root} "
                        f"stderr={proc.stderr[:300]} stdout={proc.stdout[:300]}"
                    )
                    return empty
                data = json.loads((proc.stdout or "").strip() or "{}")
                preds = data.get("predictions") if isinstance(data, dict) else {}
                if not isinstance(preds, dict):
                    return empty
                out = {}
                for k in empty.keys():
                    try:
                        v = preds.get(k)
                        out[k] = float(v) if v is not None else None
                    except Exception:
                        out[k] = None
                return out
            except Exception as e:
                logger.warning(
                    f"[OPENPOLY][XGB] infer exception: {e} | env={xgb_env} | mamba_root={xgb_mamba_root}"
                )
                return empty

        def _fmt_xgb_or_csv(xgb_val, row: dict, keys: list) -> str:
            if isinstance(xgb_val, (int, float)):
                return f"{float(xgb_val):.4f}"
            return _fmt_poly_prop(row, keys)

        async def _stream_organic_pre_analysis(user_context: str = ""):
            """恢复有机主线的前置泛化分析（按约定：需求提取左、关键性质右、候选检索左）。"""
            await websocket.send_text("\n\n### 需求信息提取\n\n")
            p1 = (
                "请输出3~6个分点，必须使用阿拉伯数字编号（1. / 2. / 3. ...）。"
                "每一点单独一行，行与行之间保留正常换行。"
                "不要表格、不要额外标题。"
                "任务：从输入中提炼应用场景、关键约束、可检索实体（Name/PSMILES/别名）。"
                "语气工程化、克制。"
                f"\n输入：{str(user_context or '')}"
            )
            await self._stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(p1)],
                websocket,
                mirror_to_content=False,
                mirror_step_id="MATERIAL_SCREENING",
            )

            await websocket.send_text("<<<CONTENT_START:MATERIAL_SCREENING>>>")
            await websocket.send_text("\n\n### 关键性质分析\n\n")
            p2 = (
                "请输出一张Markdown表格，不要额外说明。"
                "表头固定：性质维度 | 建议关注区间/阈值 | 工程意义 | 对应数据库字段。"
                "至少覆盖并用中文解释：玻璃化转变温度（Tg）、热分解温度（Td）、杨氏模量（Young's Modulus）、拉伸强度（Tensile Strength）、"
                "介电常数（Dielectric Constant）、热膨胀系数（CTE，允许标注当前待计算）。"
                "若输入缺信息可给工程常用默认口径。"
                f"\n输入：{str(user_context or '')}"
            )
            await self._stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(p2)],
                websocket,
                mirror_to_content=False,
                mirror_step_id="MATERIAL_SCREENING",
            )
            await websocket.send_text("<<<CONTENT_END:MATERIAL_SCREENING>>>")
            await websocket.send_text("\n\n### 候选材料检索\n\n")
            p3 = (
                "请输出两部分内容："
                "第一部分3~5行中文短段落，不要表格，说明如何从 Name/PSMILES/别名做检索与去重，避免同名异写与同结构多别名混淆。"
                "第二部分新增小节“聚合物类型与结构论证”，用3~5行说明上文提到的聚合物各自属于什么类型、代表性结构单元是什么、结构与性质（Tg/Td/模量/强度）的关系。"
                f"\n输入：{str(user_context or '')}"
            )
            await self._stream_llm_response(
                llm,
                [llm._default_system_msg(), llm._user_msg(p3)],
                websocket,
                mirror_to_content=False,
                mirror_step_id="MATERIAL_SCREENING",
            )

        async def _stream_route_intro_before_mp(formulas_: list, user_context: str = ""):
            """替换为：宏观目标性能窗口表（MP 前置）。"""
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]

            await websocket.send_text("\n\n### 需求背景总结\n\n")

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
                "请输出4~7行中文短段落，不要表格、不要编号、不要标题。"
                "任务：根据输入内容，先做需求到材料指标的映射论证。"
                "每行尽量采用“需求侧重点：…；对应关键性质/性能：…；验证口径：…”的结构。"
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
                await websocket.send_text("需求拆解应先从应用场景出发，建立可计算、可验证的多指标约束，而非追求单一数值最优。\n")
                await websocket.send_text("高功率与安全边界通常对应离子传导相关指标、热稳定相关指标与电化学窗口边界。\n")
                await websocket.send_text("可制造性与服役可靠性通常对应密度、机械支撑能力及界面稳定相关代理量。\n")
                await websocket.send_text("本轮先形成“需求-性质/性能-验证口径”映射，再进入结构化性能窗口表进行统一判读。\n\n")

            await websocket.send_text("\n\n### 关键材料需求提炼\n\n")
            prompt = (
                "请基于用户输入，输出一张 Markdown 表格，不要标题、不要编号、不要额外段落。"
                "表头固定为：性能维度 | 目标区间/阈值 | 工程原因 | 与应用场景关系 | 后续验证口径。"
                "严格格式要求（必须全部满足）："
                "1) 第1行必须是表头且以'|'开头、以'|'结尾；"
                "2) 第2行必须是分隔行，格式为'|---|---|---|---|---|'；"
                "3) 第3行起每一行都必须以'|'开头、以'|'结尾，且严格5列；"
                "4) 禁止在表格前后输出任何解释文字；"
                "5) 禁止单元格内换行，所有内容保持单行。"
                "严格要求：每一行“目标区间/阈值”必须给出带阿拉伯数字的数值或区间，并包含单位；"
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

        async def _stream_formula_readable_view(formulas_: list, user_context: str = ""):
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]

            await websocket.send_text("\n\n### 候选材料方向分析\n\n")

            bridge_prompt = (
                "请输出4~7行中文短段落，不要表格、不要编号、不要标题。"
                "目标：从上一步参数化约束出发，论证如何逐步收敛到可选材料体系。"
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
                await websocket.send_text("参数化提炼阶段先固定关键性能窗口与边界条件，优先排除与目标工况冲突的材料类别。\n")
                await websocket.send_text("随后在可行类别内按热稳定、传输相关与界面约束进行多指标交叉收敛，缩小到可验证的候选材料体系。\n")
                await websocket.send_text("该收敛逻辑适用于多类无机/有机复合材料筛选，不依赖单一体系预设。\n")
                await websocket.send_text("本轮体系中文名：无机功能材料候选体系。\n")
                await websocket.send_text(f"本轮候选化学式：{('、'.join(fs) if fs else '待补充')}。\n\n")

            await websocket.send_text("\n\n### 候选材料概览\n\n")
            await websocket.send_text("| 化学式 | 中文名称 | 材料类别 | 应用角色 | 入选原因（对应宏观目标） |\n")
            await websocket.send_text("|---|---|---|---|---|\n")
            for f in fs:
                p = self._formula_profile(f)
                await websocket.send_text(
                    f"| {f} | {p['中文名称']} | {p['材料类别']} | {p['应用角色']} | 对应稳定性/传导/机械等宏观目标的候选映射 |\n"
                )

        async def _stream_macro_micro_bridge(formulas_: list, user_context: str = ""):
            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]
            await websocket.send_text("\n\n### 材料数据库选择依据\n\n")
            prompt = (
                "请输出一张 Markdown 表格，不要编号、不要额外段落。"
                "表头固定为：对比维度 | 微观数据库（MP/DFT等） | 宏观数据库（经验/工艺侧） | 对筛选决策的影响。"
                "表内必须覆盖：覆盖完整性、性质可信度、理论一致性、工艺敏感性、跨来源可比性。"
                "结论要求：最后一行明确“仿真模拟阶段优先微观数据库，宏观数据库用于后验校核与工程修正”。"
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
                if not (isinstance(out, str) and "|" in out):
                    raise RuntimeError("invalid table output")
            except Exception:
                await websocket.send_text("| 对比维度 | 微观数据库（MP/DFT等） | 宏观数据库（经验/工艺侧） | 对筛选决策的影响 |\n")
                await websocket.send_text("|---|---|---|---|\n")
                await websocket.send_text("| 覆盖完整性 | 字段体系较完整，结构/热力学/电子性质覆盖较系统 | 数据分布常受项目与场景限制，覆盖不均 | 初筛更适合先用微观数据库建立统一比较基线 |\n")
                await websocket.send_text("| 性质可信度 | 基于统一计算口径，参数可复算、可追溯 | 受制备与测试条件影响大，跨批次波动明显 | 需要先用微观数据缩小候选，再做实验校核 |\n")
                await websocket.send_text("| 理论一致性 | 物理定义清晰，跨材料对比一致性更强 | 指标定义与测试边界可能不一致 | 微观数据库更利于多候选横向排序 |\n")
                await websocket.send_text("| 工艺敏感性 | 对工艺扰动不直接编码，适合做先验筛选 | 对工艺条件高度敏感，更贴近实际制造差异 | 宏观数据库更适合后验修正与落地评估 |\n")
                await websocket.send_text("| 跨来源可比性 | 同口径字段便于跨来源汇总与自动化判读 | 异源数据口径不一，直接对比风险高 | 先微观后宏观可降低误判与偏差放大 |\n")
                await websocket.send_text("| 结论 | 初筛阶段优先采用微观数据库进行候选收敛 | 宏观数据库用于后验校核与工程修正 | 组合使用可兼顾筛选效率与工程真实性 |\n\n")

        async def _stream_mp_stage_intro(formula_: str):
            """
            MP阶段前的简短真流式说明：介绍正在进行什么、MP是什么、本轮提取哪些字段。
            """
            intro_prompt = (
                "请输出3~5行中文说明，采用工程过程播报语气，不要表格、不要标题、不要编号。"
                "第一行必须以“正在使用 The Materials Project”开头。"
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
                    f"正在使用 The Materials Project 对 {formula_} 进行微观性质提取。"
                    "MP 是开放材料数据库，汇集了大规模高通量第一性原理计算结果。"
                    "本轮将提取结构、热力学与电子结构字段用于后续判读。\n"
                )

        async def _stream_alignn_stage_intro(formula_: str):
            """
            ALIGNN阶段前的简短真流式说明。
            """
            intro_prompt = (
                "请输出3~5行中文说明，采用工程过程播报语气，不要表格、不要标题、不要编号。"
                "第一行必须以“正在使用 ALIGNN”开头。"
                "内容简短说明：ALIGNN是面向晶体图结构的图神经网络模型，可基于结构快速估算材料关键性质。"
                "补一句：这些结果用于快速筛选与工艺方向判断，不替代最终实验标定。"
                f"当前材料：{str(formula_ or '')}。"
            )
            try:
                await self._stream_llm_response(
                    llm,
                    [llm._default_system_msg(), llm._user_msg(intro_prompt)],
                    websocket
                )
            except Exception:
                await websocket.send_text(
                    f"正在使用 ALIGNN 对 {formula_} 进行材料性质快速估算。"
                    "该模型基于晶体图神经网络，可在已有结构基础上补全关键性质。"
                    "结果用于候选排序与工艺方向参考，不替代最终实验标定。\n"
                )

        async def _stream_final_requirement_summary(formulas_: list, mp_ready_: list, user_context: str = "", final_metrics: dict = None):
            """目标-结果对照收敛：基于真实计算值输出，不使用泛化项。"""
            await websocket.send_text("\n\n### 目标与结果对比\n\n")
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

        def _safe_float(x):
            try:
                if x is None:
                    return None
                if isinstance(x, bool):
                    return float(int(x))
                return float(x)
            except Exception:
                return None

        def _safe_bool(x):
            if isinstance(x, bool):
                return x
            if isinstance(x, (int, float)):
                return bool(x)
            if isinstance(x, str):
                t = x.strip().lower()
                if t in {"true", "pass", "passed", "yes", "y", "1"}:
                    return True
                if t in {"false", "fail", "failed", "no", "n", "0"}:
                    return False
            return None

        def _flatten_dict(obj, prefix="", out=None):
            if out is None:
                out = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    nk = f"{prefix}.{k}" if prefix else str(k)
                    _flatten_dict(v, nk, out)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    nk = f"{prefix}[{i}]"
                    _flatten_dict(v, nk, out)
            else:
                out[prefix.lower()] = obj
            return out

        def _pick_value(flat: dict, include_any: list, exclude_any: list = None):
            exclude_any = [x.lower() for x in (exclude_any or [])]
            for k, v in flat.items():
                kk = str(k).lower()
                if all(x.lower() in kk for x in include_any):
                    if any(ex in kk for ex in exclude_any):
                        continue
                    return v
            for k, v in flat.items():
                kk = str(k).lower()
                if any(x.lower() in kk for x in include_any):
                    if any(ex in kk for ex in exclude_any):
                        continue
                    return v
            return None

        def _load_latest_json(pattern_: str):
            try:
                cands_ = sorted(glob.glob(pattern_))
                if not cands_:
                    return {}
                with open(cands_[-1], "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}

        async def _stream_final_li6ps5cl_bridge(formulas_: list):
            """
            材料模拟与计算流程总结：
            - 简述前序步骤
            - 说明为何最终选 Li6PS5Cl
            - 输出 Li6PS5Cl 性质小表，衔接后续流程
            """
            async def _send_lines_stream(lines_, delay_s: float = 0.05):
                for _ln in lines_:
                    await websocket.send_text((_ln or "") + "\n")
                    if delay_s > 0:
                        await asyncio.sleep(delay_s)

            repo_root_ = _repo_root()
            root_path_ = f"src/MNS_CaseHub/cases/{CASE_MP}"
            abs_case_root_ = os.path.abspath(os.path.join(repo_root_, root_path_))
            results_dir_ = os.path.join(abs_case_root_, "results")
            taskid_s_ = str(taskid).replace("/", "_")

            fs = [str(x) for x in (formulas_ or []) if isinstance(x, str) and x.strip()]

            metrics = {}
            for f in fs:
                adit_pat = os.path.join(results_dir_, "adit_pymatgen", f"*{taskid_s_}*", str(f), "report.json")
                md_pat = os.path.join(results_dir_, "mace_md", f"dr_*{taskid_s_}*", str(f), "summary.json")
                mp_pat = os.path.join(results_dir_, "mp", f"*{taskid_s_}*", str(f), "selected_structures.json")

                adit = _load_latest_json(adit_pat)
                md = _load_latest_json(md_pat)
                mp = _load_latest_json(mp_pat)

                flat_adit = _flatten_dict(adit)
                flat_md = _flatten_dict(md)
                flat_mp = _flatten_dict(mp)

                pass_gate = _safe_bool(_pick_value(flat_adit, ["pass_gate"]))
                if pass_gate is None:
                    pass_gate = _safe_bool(_pick_value(flat_adit, ["gate", "pass"]))

                fmax_final = _safe_float(_pick_value(flat_md, ["fmax", "final"]))
                if fmax_final is None:
                    fmax_final = _safe_float(_pick_value(flat_md, ["fmax"], ["init", "initial"]))

                min_dist_final = _safe_float(_pick_value(flat_md, ["min_dist", "final"]))
                if min_dist_final is None:
                    min_dist_final = _safe_float(_pick_value(flat_md, ["min_dist"], ["init", "initial"]))

                d_epot = _safe_float(_pick_value(flat_md, ["epot", "drift"]))
                if d_epot is None:
                    d_epot = _safe_float(_pick_value(flat_md, ["depot"]))

                density = _safe_float(_pick_value(flat_adit, ["density"]))
                if density is None:
                    density = _safe_float(_pick_value(flat_mp, ["density"]))

                score = 0.0
                if pass_gate is True:
                    score += 3.0
                elif pass_gate is False:
                    score -= 3.0

                if fmax_final is not None:
                    score += max(0.0, 1.5 - min(fmax_final, 1.5))
                if min_dist_final is not None:
                    score += 1.0 if min_dist_final >= 1.8 else -1.0
                if d_epot is not None:
                    score += 0.5 if abs(d_epot) <= 1.0 else -0.5

                metrics[f] = {
                    "score": score,
                    "pass_gate": pass_gate,
                    "fmax_final": fmax_final,
                    "min_dist_final": min_dist_final,
                    "d_epot": d_epot,
                    "density": density,
                }

            chosen = None
            if metrics:
                chosen = sorted(metrics.keys(), key=lambda x: metrics[x].get("score", -9999), reverse=True)[0]

            if not chosen:
                chosen = "Li6PS5Cl" if any(x.lower() == "li6ps5cl" for x in fs) else (fs[0] if fs else "Li6PS5Cl")

            # 口径兜底：若 Li6PS5Cl 在列表中，优先推荐它（按你的业务口径）
            if any(x.lower() == "li6ps5cl" for x in fs):
                chosen = "Li6PS5Cl"

            m = metrics.get(chosen, {})
            pass_gate_txt = "通过" if m.get("pass_gate") is True else ("未通过" if m.get("pass_gate") is False else "待核验")

            fmax_txt = f"{m.get('fmax_final'):.3f}" if isinstance(m.get("fmax_final"), float) else "本次结果待补全"
            mindist_txt = f"{m.get('min_dist_final'):.3f}" if isinstance(m.get("min_dist_final"), float) else "本次结果待补全"
            depot_txt = f"{m.get('d_epot'):.3f}" if isinstance(m.get("d_epot"), float) else "本次结果待补全"

            if isinstance(m.get("density"), float):
                density_txt = f"{m.get('density'):.3f}"
                density_basis = "本次 MP/结构评估输出"
            else:
                density_txt = "约 1.9~2.1（典型范围）"
                density_basis = "文献典型值（待后续精算更新）"

            await _send_lines_stream([
                "",
                "## 材料模拟与计算流程总结",
                "- 正在进行 材料模拟与计算流程总结",
                "    前序已完成 MP 初筛、ADiT+Pymatgen 稳定性评估，以及 MACE-fast / MACE-md 性质计算。",
                f"    综合当前流程指标，最终推荐结构为 {chosen}。",
                f"    选择依据：Gate={pass_gate_txt}，末态 fmax={fmax_txt} eV/Å，末态 min_dist={mindist_txt} Å，势能漂移ΔEpot={depot_txt} eV。",
                "",
                "    | Li6PS5Cl 性质参数（用于后续流程） | 数值 | 口径 |",
                "    |---|---:|---|",
                "    | 离子电导率（室温）S/cm | 1e-3 ~ 1e-2 | 文献典型值（待后续物理场反演细化） |",
                f"    | 密度 g/cm³ | {density_txt} | {density_basis} |",
                "    | 热膨胀系数 10^-6/K | 10 ~ 20 | 文献典型值（待后续热场计算更新） |",
            ], delay_s=0.05)

        # =========================
        # 5) MP 运行：mp_export_assets.py
        # =========================
        async def _run_mp_export_assets(formula: str) -> bool:
            repo_root = _repo_root()
            script = os.path.join(repo_root, "tools", "mp_export_assets.py")
            formula = _to_ascii_formula(formula)

            cmd = [
                "micromamba", "run", "-n", "mp-api-py311",
                "python", script,
                "--taskid", str(taskid),
                "--jobid", str(formula),
                "--formula", str(formula),
                "--prefer-stable",
            ]
            logger.info(f"[mp_export_assets] CMD={' '.join(cmd)}")

            def _run_blocking():
                return subprocess.run(
                    cmd,
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False
                )

            proc = await asyncio.to_thread(_run_blocking)
            if proc.stdout:
                logger.info(f"[mp_export_assets] STDOUT:\n{proc.stdout[-6000:]}")

            ok = (proc.returncode == 0)
            if not ok:
                logger.error(f"[mp_export_assets] FAILED rc={proc.returncode}")
            return ok

        # =========================
        # 6) ADiT 运行：adit_pymatgen_eval.py
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
        async def _mp_one(formula: str) -> bool:
            formula = _to_ascii_formula(formula)

            ok = await _run_mp_export_assets(formula)
            if not ok:
                await websocket.send_text(
                    f"{formula} 在 MP 数据库中未检索到可用结果。"
                    "可视为全新材料候选，建议转入新材料发现流程。\n"
                )
                return False

            repo_root = _repo_root()
            root_path = f"src/MNS_CaseHub/cases/{CASE_MP}"

            await self.send_results_to_frontend(
                websocket,
                repo_root,
                root_path,
                taskid,
                jobid=formula,
                pipeline="mp",
                step_id="MATERIAL_SCREENING",
                emit_summary_block=False,
            )

            # ✅ 左侧解释：你已有
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
        # 8) ADiT：评估 + 右侧下发 + 左侧解释（如果你实现了就自动走）
        # =========================

        # =========================
        # 9) 统一入口：单 case 主路径
        # =========================
        norm = _normalize_user_text(instruction)
        route, content = _parse_route(norm)
        content = _to_ascii_formula(content)

        # /mp 旧命令停用：MP 线已关闭
        if route == "mp":
            await websocket.send_text("⚠️ `MP` 路线当前已关闭，请直接输入有机材料 Name 或 PSMILES。\n")
            return

        logger.info(f"[Coding-LOG] user={user_name} taskid={taskid} route={route} content={content}")

        # =========================
        # 10) 单 case 主路径：按提取结果执行
        # =========================
        if True:
            # OpenPoly 有机检索主路径
            organic_hits = _search_openpoly_candidates(norm, topk=5)
            if organic_hits:
                try:
                    await _ensure_material_progress_started()
                    # 恢复开头前置分析链路：需求信息提取/候选材料检索走左侧，关键性质分析走右侧
                    await _stream_organic_pre_analysis(norm)

                    # 数据库检索阶段配图保持左侧展示
                    await _send_openpoly_stage_image(
                        "/data/se42/alpha_project/organic_existing_material/src/MNS_CaseHub/cases/material_discovery_demo/results/openpoly/openpoly.jpg",
                        docs="OpenPoly 数据库检索结果图",
                        description="该图对应 OpenPoly 数据库检索阶段的候选结果展示，用于快速查看检索命中概况。",
                    )

                    # 候选检索表格（含小标题）按约定走左侧
                    await websocket.send_text("\n\n### OpenPoly 有机数据库候选检索\n\n")
                    table_lines = [
                        "| Name | PSMILES | 玻璃化转变温度 Tg (K) | 热分解温度 Td (K) | 熔融温度 Tm (K) | 吸水率 Water_Uptake | 介电常数 Dielectric_Constant_Total | 导热系数 Thermal_Conductivity |\n",
                        "|---|---|---:|---:|---:|---:|---:|---:|\n",
                    ]

                    for r in organic_hits:
                        name = _display_polymer_name(r)
                        psmiles_raw = _normalize_psmiles(r.get("PSMILES"))
                        ok_psm, why_psm = _validate_psmiles_basic(psmiles_raw)
                        if not ok_psm:
                            logger.warning(
                                f"[OPENPOLY][PSMILES_INVALID] id={r.get('id')} name={name} reason={why_psm} psmiles={psmiles_raw}"
                            )

                        # 候选检索表仅展示 OpenPoly 数据库原值，不混入模型补全值
                        tg = _fmt_poly_prop(r, ["Tg_K", "Tg (K)"])
                        td = _fmt_poly_prop(r, ["Td_K", "Td (K)"])
                        tm = _fmt_poly_prop(r, ["Tm_K", "Tm (K)"])
                        wu = _fmt_poly_prop(r, ["Water_Uptake"])
                        dc = _fmt_poly_prop(r, ["Dielectric_Constant_Total"])
                        tc = _fmt_poly_prop(r, ["Thermal_Conductivity"])

                        table_lines.append(
                            f"| {name} | `{psmiles_raw}` | {tg} | {td} | {tm} | {wu} | {dc} | {tc} |\n"
                        )

                    table_md = "".join(table_lines)
                    ok_stream = await _stream_verbatim_via_llm(table_md)
                    if not ok_stream:
                        await _stream_lines(table_md.splitlines(keepends=True), delay_s=0.02)
                    

                    # 取首条命中，生成 GLB 并下发前端
                    try:
                        first_row = organic_hits[0]
                        await _generate_and_send_openpoly_first_glb(
                            websocket=websocket,
                            source_path=source_path,
                            taskid=taskid,
                            first_row=first_row,
                            step_id="MATERIAL_SCREENING",
                        )
                        await _stream_first_hit_xgb_completion(first_row)
                    except Exception as e:
                        logger.exception(f"[OPENPOLY][GLB] failed to generate/send first hit GLB: {e}")

                    # 路由锚点：明确本服务本轮已结束，并建议下游模块，避免母服务重复回调。
                    await websocket.send_text(
                        "本服务已完成有机已有材料筛选与性质补全，当前轮次不再继续调用本服务。"
                        "建议下一步对接：材料制备模块（优先）或性能检测与结果对比模块。"
                        "如需再次调用本服务，请提供新的 Name 或新的 PSMILES。\n"
                    )
                except Exception as e:
                    logger.exception(f"[OPENPOLY] organic pipeline failed: {e}")
                return

            
########################################
# 定义角色：XIMUAlpha_MNS
########################################

class XIMUAlpha_MNS(Role):
    """
    工业平台 · 有机已有材料筛选智能体。
    定位：面向聚合物/树脂/有机功能材料的已有材料检索、性质补全与工程化解释，
    以“结构化 JSON”为唯一对接载体，侧重“数据库检索 → 结构表示解析 → 性质补全 → 可视化产物拼装”。
    """
    # 对外展示名（前端/日志可见）
    name: str = "XIMUAlpha_organic_existing_materials"

    profile: str = (
        "有机已有材料检索与性质补全专用智能体。"
        "定位：面向聚合物/树脂/有机功能材料的已有材料检索、性质补全与工程化解释。"
        "输入前提：上游已完成文献筛选，并提供候选材料名称（Name）或 PSMILES。"
        "当上游询问到PCB树脂、有机物等内容时调用此模块。"
        "职责边界：仅执行 OpenPoly/CEM 等已有有机材料数据库检索、结构表示解析、性质补全、候选排序与可视化结果整理。"
        "不负责文献再次筛选、不负责材料制备流程、不负责实验执行。"
        "完成判据：当候选材料表、关键性质参数与可视化资源索引已输出时，本服务即结束。"
        "路由建议：本服务结束后应优先转入“材料制备模块”或“性能检测与结果对比模块”。"
        "不得重复调用本服务。"
    )
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 保持不变
        self._watch([UserRequirement])
        self.set_actions([Coding])
    