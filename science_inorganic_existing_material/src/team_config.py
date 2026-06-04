# -*- coding: utf-8 -*-
import os
import re
import sys
import asyncio
import glob
import html
import json
import math
import uuid
import datetime
from typing import Optional

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
from src.utils.team_config_helpers import (
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
from src.utils.markdown_sanitizer import (
    normalize_currency_symbols_for_markdown as _normalize_currency_symbols_for_markdown,
)
from src.utils.material_visual_assets import (
    resolve_database_pic_path as _resolve_database_pic_path_external,
    upload_alignn_dynamic_or_static as _upload_alignn_dynamic_or_static_external,
    upload_periodic_dynamic_or_static as _upload_periodic_dynamic_or_static_external,
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
# CodeRetriever（接口保留）
#
# 说明：
# - 该能力在当前主链（化学式 -> MP -> ALIGNN）中不启用；
# - 保留最小接口壳，作为后续恢复“项目检索/路由匹配”能力的稳定扩展点；
# - 当前默认返回空结果，不参与运行分支决策。
########################################


class CodeRetriever:
    """项目检索能力接口壳（当前停用）。"""

    def find_matching_project(self, query: str):
        return None, 0.0, None

    def get_parameters(self, idx: int) -> Optional[dict]:
        return None

    def get_root_path(self, idx: int) -> Optional[str]:
        return None

    def get_main_entry(self, idx: int) -> Optional[str]:
        return None

    def get_summary(self, idx: int) -> Optional[str]:
        return None


########################################
# Coding Action 模块
# - 功能：根据用户输入生成可运行的代码、选择模型脚本、执行与反馈运行结果
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

    _code_retriever: Optional[CodeRetriever] = PrivateAttr(default=None)
    _emitted_glb_keys: set = PrivateAttr(default_factory=set)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    # Prompt 常量已迁移到 src/roles/mns_role_prompts.py

    def _get_code_retriever(self) -> Optional[CodeRetriever]:
        """
        预留接口：后续若恢复“项目检索/路由匹配”能力，可在此懒加载 CodeRetriever。
        当前主链不依赖该能力，保持返回 None。
        """
        return None


    async def _safe_send_text(self, websocket, content):
        if not websocket or content is None:
            return
        try:
            await websocket.send_text(_normalize_currency_symbols_for_markdown(str(content)))
        except Exception:
            logger.exception("[WS] send_text failed")

    async def _send_content_start(self, websocket, step_id: str):
        await self._safe_send_text(websocket, f"<<<CONTENT_START:{step_id}>>>")

    async def _send_content_end(self, websocket, step_id: str):
        await self._safe_send_text(websocket, f"<<<CONTENT_END:{step_id}>>>")

    async def _send_content_block(self, websocket, step_id: str, text: str):
        await self._send_content_start(websocket, step_id)
        if text:
            await self._safe_send_text(websocket, text.rstrip() + "\n")
        await self._send_content_end(websocket, step_id)

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
            await self._send_content_start(websocket, step_id)
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
                        await websocket.send_text(_normalize_currency_symbols_for_markdown(chunk_msg))
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
                    await self._send_content_end(websocket, step_id)
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
        await self._send_content_start(websocket, "MATERIAL_SCREENING")
        await self._safe_send_text(websocket, "### 数据库获取信息总览\n\n")
        await self._stream_llm_response(
            llm,
            [llm._default_system_msg(), llm._user_msg(prompt)],
            websocket
        )
        await self._safe_send_text(websocket, "\n以上表格汇总了从 Materials Project 数据库中检索到的相关化学式候选结构与关键字段，用于说明当前候选为什么会进入后续筛选与性质分析流程。\n")
        await self._send_content_end(websocket, "MATERIAL_SCREENING")

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

    async def _material_alignn_placeholder_stage(self, websocket, formula: str, llm=None, user_context: str = ""):
        """兼容旧调用名，实际已接入 ALIGNN 补全。"""
        return await self._material_alignn_completion_stage(websocket, formula, llm=llm, user_context=user_context)


    async def _material_alignn_completion_stage(self, websocket, formula: str, llm=None, user_context: str = ""):
        """
        MP-first + ALIGNN completion + proxy ranking
        - 优先使用 MP 字段
        - 缺失时用 ALIGNN 补 formation_energy / band_gap / bulk / shear
        - 按上文需求生成 hardness / thermal / dielectric / transport proxies
        """
        async def _stream_table_header_once():
            await websocket.send_text("\n")
            await websocket.send_text("| 项目 | 状态 | 本轮证据 | 来源 | 可信度 | 作用 |\n")
            await websocket.send_text("|---|---|---|---|---|---|\n")

        async def _stream_property_row(name: str, status: str, evidence: str, source_confidence: str, role: str):
            source_text = str(source_confidence or "").strip()
            confidence_text = ""
            if "；" in source_text:
                source_text, confidence_text = [x.strip() for x in source_text.rsplit("；", 1)]
            elif ";" in source_text:
                source_text, confidence_text = [x.strip() for x in source_text.rsplit(";", 1)]
            if not confidence_text:
                confidence_text = "待补充"
            await websocket.send_text(f"| {name} | {status} | {evidence} | {source_text} | {confidence_text} | {role} |\n")

        def _fmt_value(value, unit: str = "", nd: int = 4):
            if isinstance(value, float):
                text = f"{value:.{nd}f}"
            elif value is None:
                text = "待补充"
            else:
                text = str(value)
            return f"{text} {unit}".strip()

        def _source_confidence(raw_source: str, fallback_source: str = "模型预测/数据库值", fallback_conf: str = "中") -> tuple:
            src_v = str(raw_source or "")
            if src_v.startswith("ALIGNN"):
                return "ALIGNN图神经网络预测补全", "较高"
            if src_v:
                return "MP数据库第一性原理结果", "高"
            return fallback_source, fallback_conf

        def _invalidate_nonpositive_physical_value(value, src_name: str, prop_name: str):
            if isinstance(value, float) and value <= 0:
                return None, "", f"{prop_name}模型输出为非正值({value:.4f})，已判为越界并转为待补充"
            return value, src_name, ""

        def _resolve_symmetry_text(item: dict) -> str:
            crystal = str(item.get("crystal_system") or item.get("crystal") or "").strip()
            spg = str(item.get("spacegroup_symbol") or item.get("space_group") or item.get("symmetry") or "").strip()
            if crystal and spg:
                return f"{crystal}/{spg}"
            return crystal or spg or "待计算"

        def _infer_property_needs(text: str) -> dict:
            txt = str(text or "").lower()
            def has(keys):
                return any(k in txt for k in keys)
            return {
                "thermal": has(["导热", "散热", "热管理", "热流", "热导", "thermal", "heat", "cooling"]),
                "cte": has(["热膨胀", "cte", "热应力", "热失配", "界面开裂"]),
                "mechanical": has(["力学", "强度", "应力", "应变", "疲劳", "刚度", "硬度", "可靠性", "mechanical"]),
                "dielectric": has(["绝缘", "介电", "击穿", "雷达", "高频", "毫米波", "低损耗", "band", "dielectric"]),
                "piezo": has(["压电", "致动", "传感", "机电耦合", "piezo"]),
                "transport": has(["电导", "扩散", "迁移", "离子", "输运", "seebeck", "功率因子", "transport", "diffusion"]),
            }

        def _alignn_zip_ready(model_name: str) -> bool:
            try:
                import zipfile
                alignn_env = os.getenv("ALIGNN_ENV", "alignn-gpu-test")
                py = f"/home/ubuntu/.local/share/mamba/envs/{alignn_env}/bin/python"
                site_root = os.path.dirname(os.path.dirname(py))
                zip_path = os.path.join(site_root, "lib", "python3.10", "site-packages", "alignn", f"{model_name}.zip")
                if not os.path.exists(zip_path) or os.path.getsize(zip_path) <= 0:
                    return False
                with zipfile.ZipFile(zip_path) as zp:
                    names = zp.namelist()
                return any(name.endswith("config.json") for name in names) and any(name.endswith(".pt") for name in names)
            except Exception:
                return False

        def _avg_atomic_mass_from_formula(formula_: str):
            masses = {
                "H": 1.008, "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998,
                "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06,
                "Cl": 35.45, "K": 39.098, "Ca": 40.078, "Ti": 47.867, "Cr": 51.996, "Mn": 54.938,
                "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Ga": 69.723,
                "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904, "Sr": 87.62, "Zr": 91.224,
                "Mo": 95.95, "Ag": 107.868, "Cd": 112.414, "In": 114.818, "Sn": 118.710,
                "I": 126.904, "Ba": 137.327, "La": 138.905, "Ce": 140.116, "W": 183.84,
                "Pb": 207.2,
            }
            s = _utils_to_ascii_formula(str(formula_ or ""))
            # 对 AlN-SiC 这类系统做快速平均；括号/水合物等复杂式只作为 proxy 输入。
            tokens = re.findall(r"([A-Z][a-z]?)(\d*\.?\d*)", s)
            total_n = 0.0
            total_m = 0.0
            for el, n_s in tokens:
                if el not in masses:
                    continue
                try:
                    n = float(n_s) if n_s else 1.0
                except Exception:
                    n = 1.0
                total_n += n
                total_m += masses[el] * n
            if total_n <= 0:
                return None
            return total_m / total_n

        def _level_from_score(score):
            if not isinstance(score, float):
                return "待补充"
            if score >= 0.72:
                return "高"
            if score >= 0.45:
                return "中"
            return "低"

        def _thermal_proxy(formula_: str, density_, bulk_, shear_, e_hull_, formation_energy_):
            """
            秒级热导潜力估算：用弹性模量/密度估声速趋势，再叠加轻元素、稳定性和键强信号。
            输出仅用于快速排序，不给 W/mK 绝对值。
            """
            out = {
                "score": None,
                "level": "待补充",
                "mean_sound_velocity": None,
                "debye_proxy": None,
                "avg_atomic_mass": _avg_atomic_mass_from_formula(formula_),
                "confidence": "中",
                "source": "弹性模量/密度声速proxy + 稳定性/键强经验估算",
            }
            if not (isinstance(density_, float) and density_ > 0 and isinstance(bulk_, float) and bulk_ > 0 and isinstance(shear_, float) and shear_ > 0):
                out["confidence"] = "低"
                return out
            try:
                rho = density_ * 1000.0  # g/cm3 -> kg/m3
                k_pa = bulk_ * 1e9
                g_pa = shear_ * 1e9
                vt = math.sqrt(g_pa / rho)
                vl = math.sqrt((k_pa + 4.0 * g_pa / 3.0) / rho)
                vm = ((1.0 / 3.0) * ((2.0 / (vt ** 3)) + (1.0 / (vl ** 3)))) ** (-1.0 / 3.0)
                mass = out["avg_atomic_mass"]
                sound_score = max(0.0, min(1.0, (vm - 1800.0) / 5200.0))
                stiffness_score = max(0.0, min(1.0, ((bulk_ / 220.0) * 0.45 + (shear_ / 140.0) * 0.55)))
                light_score = 0.55
                if isinstance(mass, float):
                    light_score = max(0.0, min(1.0, (90.0 - mass) / 75.0))
                stability_score = 0.55
                if isinstance(e_hull_, float):
                    if e_hull_ <= 0.02:
                        stability_score = 1.0
                    elif e_hull_ <= 0.08:
                        stability_score = 0.65
                    else:
                        stability_score = 0.25
                bond_score = 0.5
                if isinstance(formation_energy_, float):
                    bond_score = max(0.0, min(1.0, abs(formation_energy_) / 2.5))
                score = 0.34 * sound_score + 0.24 * stiffness_score + 0.18 * light_score + 0.16 * stability_score + 0.08 * bond_score
                out["score"] = float(score)
                out["level"] = _level_from_score(float(score))
                out["mean_sound_velocity"] = float(vm)
                out["debye_proxy"] = float(vm * ((1.0 / max(mass or 45.0, 1e-6)) ** (1.0 / 3.0)))
                return out
            except Exception:
                out["confidence"] = "低"
                return out

        def _thermal_diffusivity_proxy(thermal_score, density_):
            if not (isinstance(thermal_score, float) and isinstance(density_, float) and density_ > 0):
                return None, "待补充", "低"
            # 缺少 Cp 时只能用 density 做归一化惩罚，作为热扩散趋势 proxy。
            score = max(0.0, min(1.0, thermal_score * (3.5 / max(density_, 1e-6)) ** 0.35))
            return float(score), _level_from_score(float(score)), "中低"

        def _thermal_expansion_risk_proxy(bulk_, shear_, formation_energy_, e_hull_):
            if not (isinstance(bulk_, float) and bulk_ > 0):
                return None, "待补充", "低"
            stiffness = max(0.0, min(1.0, (bulk_ / 220.0) * 0.65 + ((shear_ or 0.0) / 140.0) * 0.35))
            bond = 0.5
            if isinstance(formation_energy_, float):
                bond = max(0.0, min(1.0, abs(formation_energy_) / 2.5))
            stable = 0.65
            if isinstance(e_hull_, float):
                stable = 1.0 if e_hull_ <= 0.02 else (0.55 if e_hull_ <= 0.08 else 0.25)
            low_cte_score = 0.55 * stiffness + 0.30 * bond + 0.15 * stable
            if low_cte_score >= 0.72:
                risk = "低"
            elif low_cte_score >= 0.45:
                risk = "中"
            else:
                risk = "高"
            return float(low_cte_score), risk, "低-中"

        def _thermal_shock_risk_proxy(thermal_score, shear_, hardness_, density_):
            if not isinstance(thermal_score, float):
                return None, "待补充", "低"
            mech = 0.5
            if isinstance(shear_, float):
                mech = max(0.0, min(1.0, shear_ / 140.0))
            hard_s = 0.5
            if isinstance(hardness_, float):
                hard_s = max(0.0, min(1.0, hardness_ / 18.0))
            density_penalty = 0.75
            if isinstance(density_, float) and density_ > 0:
                density_penalty = max(0.55, min(1.0, (4.5 / density_) ** 0.25))
            score = max(0.0, min(1.0, (0.50 * thermal_score + 0.30 * mech + 0.20 * hard_s) * density_penalty))
            if score >= 0.70:
                risk = "低"
            elif score >= 0.45:
                risk = "中"
            else:
                risk = "高"
            return float(score), risk, "中低"

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
        EHULL_MODELS = ["jv_ehull_alignn"]
        BG_MODELS = ["jv_mbj_bandgap_alignn", "jv_optb88vdw_bandgap_alignn", "mp_gappbe_alignn"]
        BULK_MODELS = ["jv_bulk_modulus_kv_alignn"]
        SHEAR_MODELS = ["jv_shear_modulus_gv_alignn"]
        ELEC_MASS_MODELS = ["jv_avg_elec_mass_alignn"]
        HOLE_MASS_MODELS = ["jv_avg_hole_mass_alignn"]
        DIELECTRIC_MODELS = ["jv_dfpt_piezo_max_dielectric_alignn", "jv_epsx_alignn", "jv_mepsx_alignn"]
        PIEZO_MODELS = ["jv_dfpt_piezo_max_dij_alignn"]
        SEEBECK_MODELS = ["jv_n-Seebeck_alignn"]
        POWER_FACTOR_MODELS = ["jv_n-powerfact_alignn"]
        property_needs = _infer_property_needs(user_context)
        if not any(property_needs.values()):
            # 没有明确上文需求时，保留基础稳定/电子/力学/传输初筛。
            property_needs.update({"mechanical": True, "dielectric": True, "transport": True})
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

        e_hull_src = "MP" if isinstance(e_hull, float) else ""
        fe_src = "MP" if isinstance(fe, float) else ""
        bg_src = "MP" if isinstance(bg, float) else ""
        bulk_src = "MP" if isinstance(bulk, float) else ""
        shear_src = "MP" if isinstance(shear, float) else ""
        density_src = "MP" if isinstance(density, float) else "NA"
        elec_mass_src = "MP" if isinstance(elec_mass, float) else "NA"
        hole_mass_src = "MP" if isinstance(hole_mass, float) else "NA"
        dielectric = None
        dielectric_src = ""
        piezo = None
        piezo_src = ""
        seebeck = None
        seebeck_src = ""
        power_factor = None
        power_factor_src = ""
        dielectric_err = ""
        piezo_err = ""
        seebeck_err = ""
        power_factor_err = ""
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
            f"- 已命中候选结构：`{mid or formula}`，正在按上游需求补全关键性质与代理指标。",
            "- 下方流式表将合并展示计算证据、满足状态、来源可信度和下一步验证口径。",
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

        if (e_hull is None) and cif_path and os.path.exists(cif_path):
            eh_pred, mn, _ = _alignn_try_alignn_models(cif_path, EHULL_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if eh_pred is not None:
                e_hull, e_hull_src = eh_pred, f"ALIGNN:{mn}"

        if (fe is None) and cif_path and os.path.exists(cif_path):
            fe_pred, mn, _ = _alignn_try_alignn_models(cif_path, FE_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if fe_pred is not None:
                fe, fe_src = fe_pred, f"ALIGNN:{mn}"

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
            "满足" if isinstance(e_hull, float) and e_hull <= 0.02 else ("部分满足" if isinstance(e_hull, float) else "待补充"),
            _fmt_value(e_hull, "eV/atom"),
            f"{src_ehull}；{conf_ehull}",
            f"热力学稳定性快速筛选，当前判读：{stability_class}",
        )
        src_fe, conf_fe = _source_confidence(fe_src, "MP数据库第一性原理结果", "高")
        await _stream_property_row(
            "形成能",
            "已计算" if isinstance(fe, float) else "待补充",
            _fmt_value(fe, "eV/atom"),
            f"{src_fe}；{conf_fe}",
            "用于候选排序，数值越负通常表示形成倾向更强",
        )
        src_density, conf_density = _source_confidence(density_src, "MP数据库第一性原理结果", "高")
        await _stream_property_row(
            "密度",
            "已计算" if isinstance(density, float) else "待补充",
            _fmt_value(density, "g/cm3"),
            f"{src_density}；{conf_density}",
            "判断压实、堆叠与宏观结构设计的体积负载趋势",
        )

        if (bg is None) and cif_path and os.path.exists(cif_path):
            bg_pred, mn, _ = _alignn_try_alignn_models(cif_path, BG_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bg_pred is not None:
                bg, bg_src = bg_pred, f"ALIGNN:{mn}"
        src_bg, conf_bg = _source_confidence(bg_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "带隙",
            "满足" if isinstance(bg, float) and bg >= 1.5 else ("部分满足" if isinstance(bg, float) and bg > 0 else "待补充"),
            _fmt_value(bg, "eV"),
            f"{src_bg}；{conf_bg}",
            "电子绝缘/窗口边界判断，需结合工作电压和击穿强度复核",
        )

        if property_needs.get("dielectric") and cif_path and os.path.exists(cif_path):
            ready_models = [m for m in DIELECTRIC_MODELS if _alignn_zip_ready(m)]
            if ready_models:
                dielectric_pred, mn, dielectric_err = _alignn_try_alignn_models(
                    cif_path,
                    ready_models,
                    invalid_models=invalid_models,
                    pred_cache=pred_cache,
                    timeout_sec=timeout_sec,
                )
                if dielectric_pred is not None:
                    dielectric, dielectric_src = dielectric_pred, f"ALIGNN:{mn}"
            else:
                dielectric_err = "介电相关ALIGNN模型权重未缓存或zip不可用"
        if property_needs.get("dielectric"):
            src_dielectric, conf_dielectric = _source_confidence(dielectric_src, "ALIGNN预训练模型预测", "中")
            await _stream_property_row(
                "介电常数代理",
                "满足" if isinstance(dielectric, float) and dielectric > 0 else "待补充",
                _fmt_value(dielectric),
                f"{src_dielectric if dielectric is not None else '模型权重待补充'}；{conf_dielectric if dielectric is not None else '待补充'}",
                dielectric_err or "用于绝缘/介电/高频趋势判断，不等同于频率相关介电损耗tanδ",
            )

        if property_needs.get("piezo") and cif_path and os.path.exists(cif_path):
            ready_models = [m for m in PIEZO_MODELS if _alignn_zip_ready(m)]
            if ready_models:
                piezo_pred, mn, piezo_err = _alignn_try_alignn_models(
                    cif_path,
                    ready_models,
                    invalid_models=invalid_models,
                    pred_cache=pred_cache,
                    timeout_sec=timeout_sec,
                )
                if piezo_pred is not None:
                    piezo, piezo_src = piezo_pred, f"ALIGNN:{mn}"
            else:
                piezo_err = "压电相关ALIGNN模型权重未缓存或zip不可用"
            src_piezo, conf_piezo = _source_confidence(piezo_src, "ALIGNN预训练模型预测", "中")
            await _stream_property_row(
                "压电响应代理",
                "已计算" if isinstance(piezo, float) else "待补充",
                _fmt_value(piezo),
                f"{src_piezo if piezo is not None else '模型权重待补充'}；{conf_piezo if piezo is not None else '待补充'}",
                piezo_err or "用于压电/传感/致动趋势判断，需结合晶向和实验压电系数验证",
            )

        if (bulk is None) and cif_path and os.path.exists(cif_path):
            bulk_pred, mn, _ = _alignn_try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bulk_pred is not None:
                bulk, bulk_src = bulk_pred, f"ALIGNN:{mn}"
            else:
                _, _, bulk_err = _alignn_try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (bulk is None) and (not cif_path or not os.path.exists(cif_path)):
            bulk_err = f"cif缺失或路径无效({cif_source})"
        bulk, bulk_src, bulk_invalid_err = _invalidate_nonpositive_physical_value(bulk, bulk_src, "体积模量")
        if bulk_invalid_err:
            bulk_err = bulk_invalid_err
        src_bulk, conf_bulk = _source_confidence(bulk_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "体积模量",
            "满足" if isinstance(bulk, float) and bulk >= 15 else ("部分满足" if isinstance(bulk, float) else "待补充"),
            _fmt_value(bulk, "GPa"),
            f"{src_bulk if bulk is not None else '模型越界/待补充'}；{conf_bulk if bulk is not None else '待补充'}",
            bulk_err or "抗压与成形支撑判断，模型补全值建议后续复核",
        )

        if (shear is None) and cif_path and os.path.exists(cif_path):
            shear_pred, mn, _ = _alignn_try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if shear_pred is not None:
                shear, shear_src = shear_pred, f"ALIGNN:{mn}"
            else:
                _, _, shear_err = _alignn_try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (shear is None) and (not cif_path or not os.path.exists(cif_path)):
            shear_err = f"cif缺失或路径无效({cif_source})"
        shear, shear_src, shear_invalid_err = _invalidate_nonpositive_physical_value(shear, shear_src, "剪切模量")
        if shear_invalid_err:
            shear_err = shear_invalid_err
        src_shear, conf_shear = _source_confidence(shear_src, "ALIGNN图神经网络预测补全", "较高")
        await _stream_property_row(
            "剪切模量",
            "满足" if isinstance(shear, float) and shear >= 8 else ("部分满足" if isinstance(shear, float) else "待补充"),
            _fmt_value(shear, "GPa"),
            f"{src_shear if shear is not None else '模型越界/待补充'}；{conf_shear if shear is not None else '待补充'}",
            shear_err or "抗剪切形变和开裂风险判断，模型补全值建议后续复核",
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
            "已计算" if isinstance(elec_mass, float) else "待补充",
            _fmt_value(elec_mass, "m0"),
            f"{src_em}；{conf_em}",
            "电子输运趋势参考，主要用于快速排序",
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
            "已计算" if isinstance(hole_mass, float) else "待补充",
            _fmt_value(hole_mass, "m0"),
            f"{src_hm}；{conf_hm}",
            "空穴输运和界面极化趋势参考，主要用于快速排序",
        )

        hardness_est = None
        hardness_formula = "待计算"
        if isinstance(shear, float) and isinstance(bulk, float) and bulk > 1e-12 and shear > 0:
            try:
                k_ratio = shear / bulk
                hv_chen = 2.0 * ((k_ratio * k_ratio * shear) ** 0.585) - 3.0
                if hv_chen > 0:
                    hardness_est = float(hv_chen)
                    hardness_formula = "Chen经验公式 Hv=2(k^2G)^0.585-3"
            except Exception:
                hardness_est = None
        if (hardness_est is None or hardness_est <= 0) and isinstance(shear, float) and shear > 0:
            hardness_est = max(0.0, 0.151 * shear)
            hardness_formula = "Teter近似 Hv≈0.151G（Chen公式为非正值时回退）"
        if isinstance(hardness_est, float) and hardness_est <= 0:
            hardness_est = None
            hardness_formula = "待补充"
        await _stream_property_row(
            "硬度（估算）",
            "已估算" if isinstance(hardness_est, float) else "待补充",
            _fmt_value(hardness_est, "GPa"),
            f"{hardness_formula}；中高" if isinstance(hardness_est, float) else "经验公式越界；待补充",
            "抗压痕/耐磨趋势快速比较，不等同于标准硬度测试",
        )

        thermal_proxy = _thermal_proxy(formula, density, bulk, shear, e_hull, fe)
        thermal_diffusivity_proxy = None
        thermal_diffusivity_level = "待补充"
        thermal_diffusivity_confidence = "低"
        thermal_expansion_proxy = None
        thermal_expansion_risk = "待补充"
        thermal_expansion_confidence = "低"
        thermal_shock_proxy = None
        thermal_shock_risk = "待补充"
        thermal_shock_confidence = "低"

        if property_needs.get("thermal"):
            await _stream_property_row(
                "热导潜力（估算）",
                "满足" if thermal_proxy.get("level") in {"高", "中"} else ("部分满足" if isinstance(thermal_proxy.get("score"), float) else "待补充"),
                f"level={thermal_proxy.get('level')}；score={_fmt_value(thermal_proxy.get('score'))}；v_m≈{_fmt_value(thermal_proxy.get('mean_sound_velocity'), 'm/s', nd=1)}",
                f"{thermal_proxy.get('source')}；{thermal_proxy.get('confidence')}",
                "仅用于快速排序，不等同于实验热导率或声子/MD深算结果",
            )
            thermal_diffusivity_proxy, thermal_diffusivity_level, thermal_diffusivity_confidence = _thermal_diffusivity_proxy(
                thermal_proxy.get("score"),
                density,
            )
            await _stream_property_row(
                "热扩散潜力（估算）",
                "满足" if thermal_diffusivity_level in {"高", "中"} else ("部分满足" if isinstance(thermal_diffusivity_proxy, float) else "待补充"),
                f"level={thermal_diffusivity_level}；score={_fmt_value(thermal_diffusivity_proxy)}",
                f"热导潜力proxy/密度惩罚估算；{thermal_diffusivity_confidence}",
                "缺少定量热容Cp，不能作为热扩散率实测值",
            )

        if property_needs.get("cte"):
            thermal_expansion_proxy, thermal_expansion_risk, thermal_expansion_confidence = _thermal_expansion_risk_proxy(bulk, shear, fe, e_hull)
            await _stream_property_row(
                "热膨胀风险（估算）",
                "满足" if thermal_expansion_risk in {"低", "中"} else ("部分满足" if isinstance(thermal_expansion_proxy, float) else "待补充"),
                f"risk={thermal_expansion_risk}；score={_fmt_value(thermal_expansion_proxy)}",
                f"弹性刚度/键强/稳定性经验估算；{thermal_expansion_confidence}",
                "不直接输出CTE，需热膨胀系数或热循环实验闭环",
            )
            thermal_shock_proxy, thermal_shock_risk, thermal_shock_confidence = _thermal_shock_risk_proxy(
                thermal_proxy.get("score"),
                shear,
                hardness_est,
                density,
            )
            await _stream_property_row(
                "热震/热循环风险（估算）",
                "满足" if thermal_shock_risk in {"低", "中"} else ("部分满足" if isinstance(thermal_shock_proxy, float) else "待补充"),
                f"risk={thermal_shock_risk}；score={_fmt_value(thermal_shock_proxy)}",
                f"热导潜力proxy + 剪切模量/硬度/密度经验估算；{thermal_shock_confidence}",
                "不能替代热循环、热冲击或界面可靠性测试",
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
            "满足" if isinstance(cond_diff_proxy, float) and cond_diff_proxy >= 0.2 else ("部分满足" if isinstance(cond_diff_proxy, float) else "待补充"),
            f"proxy={_fmt_value(cond_diff_proxy)}",
            "带隙/形成能/有效质量组合proxy；中",
            "仅用于候选排序，不等同于实验电导率或扩散系数",
        )

        if property_needs.get("transport") and cif_path and os.path.exists(cif_path):
            ready_seebeck = [m for m in SEEBECK_MODELS if _alignn_zip_ready(m)]
            if ready_seebeck:
                seebeck_pred, mn, seebeck_err = _alignn_try_alignn_models(
                    cif_path,
                    ready_seebeck,
                    invalid_models=invalid_models,
                    pred_cache=pred_cache,
                    timeout_sec=timeout_sec,
                )
                if seebeck_pred is not None:
                    seebeck, seebeck_src = seebeck_pred, f"ALIGNN:{mn}"
            else:
                seebeck_err = "Seebeck相关ALIGNN模型权重未缓存或zip不可用"
            ready_pf = [m for m in POWER_FACTOR_MODELS if _alignn_zip_ready(m)]
            if ready_pf:
                pf_pred, mn, power_factor_err = _alignn_try_alignn_models(
                    cif_path,
                    ready_pf,
                    invalid_models=invalid_models,
                    pred_cache=pred_cache,
                    timeout_sec=timeout_sec,
                )
                if pf_pred is not None:
                    power_factor, power_factor_src = pf_pred, f"ALIGNN:{mn}"
            else:
                power_factor_err = "功率因子相关ALIGNN模型权重未缓存或zip不可用"

            src_seebeck, conf_seebeck = _source_confidence(seebeck_src, "ALIGNN预训练模型预测", "中")
            await _stream_property_row(
                "Seebeck系数代理",
                "已计算" if isinstance(seebeck, float) else "待补充",
                _fmt_value(seebeck),
                f"{src_seebeck if seebeck is not None else '模型权重待补充'}；{conf_seebeck if seebeck is not None else '待补充'}",
                seebeck_err or "热电/输运趋势判断；正负号代表响应方向或载流子类型倾向，需结合温度和载流子浓度解释",
            )
            src_pf, conf_pf = _source_confidence(power_factor_src, "ALIGNN预训练模型预测", "中")
            await _stream_property_row(
                "功率因子代理",
                "已计算" if isinstance(power_factor, float) else "待补充",
                _fmt_value(power_factor),
                f"{src_pf if power_factor is not None else '模型权重待补充'}；{conf_pf if power_factor is not None else '待补充'}",
                power_factor_err or "热电候选快速排序，不等同于完整ZT评估",
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
            "dielectric_proxy": dielectric,
            "piezo_proxy": piezo,
            "seebeck_proxy": seebeck,
            "power_factor_proxy": power_factor,
            "thermal_conductivity_proxy": thermal_proxy.get("score"),
            "thermal_conductivity_level": thermal_proxy.get("level"),
            "thermal_mean_sound_velocity": thermal_proxy.get("mean_sound_velocity"),
            "thermal_debye_proxy": thermal_proxy.get("debye_proxy"),
            "thermal_proxy_confidence": thermal_proxy.get("confidence"),
            "thermal_diffusivity_proxy": thermal_diffusivity_proxy,
            "thermal_diffusivity_level": thermal_diffusivity_level,
            "thermal_diffusivity_confidence": thermal_diffusivity_confidence,
            "thermal_expansion_proxy": thermal_expansion_proxy,
            "thermal_expansion_risk": thermal_expansion_risk,
            "thermal_expansion_confidence": thermal_expansion_confidence,
            "thermal_shock_proxy": thermal_shock_proxy,
            "thermal_shock_risk": thermal_shock_risk,
            "thermal_shock_confidence": thermal_shock_confidence,
            "property_needs": property_needs,
            "src_ehull": e_hull_src,
            "src_density": density_src,
            "src_fe": fe_src,
            "src_bg": bg_src,
            "src_bulk": bulk_src,
            "src_shear": shear_src,
            "src_elec_mass": elec_mass_src,
            "src_hole_mass": hole_mass_src,
            "src_dielectric": dielectric_src,
            "src_piezo": piezo_src,
            "src_seebeck": seebeck_src,
            "src_power_factor": power_factor_src,
            "err_bulk": bulk_err,
            "err_shear": shear_err,
            "err_elec_mass": em_err,
            "err_hole_mass": hm_err,
            "err_dielectric": dielectric_err,
            "err_piezo": piezo_err,
            "err_seebeck": seebeck_err,
            "err_power_factor": power_factor_err,
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
            await self._send_content_block(websocket, step_id_local, text)

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
                    await self._send_content_end(websocket, step_id)
                    await self._send_content_start(websocket, step_id)

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
                    await self._send_content_end(websocket, step_id)
                    await self._send_content_start(websocket, step_id)
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
        await self._send_content_block(websocket, step_id, text)

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
            await self._ws_right(websocket, step_id, text)

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
            await self._send_content_start(websocket, step_id)
            material_block_opened = True

        async def _close_material_block(step_id: str = "MATERIAL_SCREENING"):
            nonlocal material_block_opened
            if not material_block_opened:
                return
            await self._send_content_end(websocket, step_id)
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

        async def _upload_alignn_dynamic_or_static(formula_: str) -> str:
            return await _upload_alignn_dynamic_or_static_external(
                repo_root=_repo_root(),
                taskid=str(taskid),
                formula=formula_,
                upload_database_pic_for_markdown=_upload_database_pic_for_markdown,
                logger=logger,
            )

        async def _upload_periodic_dynamic_or_static(formulas_: list) -> str:
            return await _upload_periodic_dynamic_or_static_external(
                repo_root=_repo_root(),
                taskid=str(taskid),
                formulas=formulas_,
                upload_database_pic_for_markdown=_upload_database_pic_for_markdown,
                logger=logger,
            )

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

        def _extract_inline_formula_tokens(text: str) -> list:
            """
            补充抽取：处理中文连续文本中夹带的化学式（如 SiC/AlN/BN）。
            避免仅依赖 \\b 导致边界识别失败。
            """
            s = _to_ascii_formula(text or "")
            if not s:
                return []
            out, seen = [], set()
            for m in re.finditer(r"[A-Za-z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉]{1,24}", s):
                tok = _to_ascii_formula(m.group(0)).strip()
                if not tok:
                    continue
                if _is_primary_formula_token(tok) and tok not in seen:
                    out.append(tok)
                    seen.add(tok)
            return out

        def _is_primary_formula_token(tok: str) -> bool:
            t = _to_ascii_formula(tok).strip()
            if not t:
                return False
            domain_tokens = {"PCB", "DBC", "LTCC", "HTCC", "IC", "IGBT", "CMP", "CTE", "DK", "DF", "RA", "TG"}
            if t.upper() in domain_tokens:
                return False
            return _looks_like_formula(t)

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
                "涉及货币或成本单位时，禁止使用 $ 符号；美元写 USD，人民币写 CNY。"
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
                "涉及货币或成本单位时，禁止使用 $ 符号；美元写 USD，人民币写 CNY；例如单位电导率成本 ≤ 0.5 USD/(S/cm·m^3)。"
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
                "涉及货币或成本单位时，禁止使用 $ 符号；美元写 USD，人民币写 CNY。"
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
            period_url = await _upload_periodic_dynamic_or_static(fs)
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
                "涉及货币或成本单位时，禁止使用 $ 符号；美元写 USD，人民币写 CNY。"
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

            intro_prompt = (
                "请输出3~5条中文分条内容，采用工程过程播报语气，不要表格、不要标题。"
                "必须使用阿拉伯数字编号（1. 2. 3. ...）。"
                "每条之间必须空一行。"
                "第一条必须以“正在使用 The Materials Project”开头。"
                "内容需要非常简短，说明：MP是开放材料数据库、规模较大、基于高通量第一性原理计算。"
                "语言尽量通俗但要严肃，补一句这些字段和后续制备可行性、应用场景判断有什么关系，不要使用比喻，是面向成年人专家的解释。"
                "最后一行说明本轮将提取的字段类型：结构（对称性/位点数）、热力学（E_above_hull/E_form）、电子结构（band_gap）。"
                "涉及货币或成本单位时，禁止使用 $ 符号；美元写 USD，人民币写 CNY。"
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
                alignn_url = await _upload_alignn_dynamic_or_static(formula_)
                if alignn_url:
                    await websocket.send_text(f"![ALIGNN 图神经网络分析示意]({alignn_url})\n\n")
                await websocket.send_text(
                    f"1. 正在使用 ALIGNN 对 {formula_} 的晶体结构进行图神经网络分析，快速补全关键性质与应用相关代理指标。\n\n"
                    "2. 模型基于原子位置与化学键关系自动提取结构特征，实现毫秒级性质预测。\n\n"
                    "3. 这些结果用于快速筛选与工艺方向判断，不替代最终实验标定。\n\n"
                )
            except Exception:
                await websocket.send_text(
                    f"1. 正在使用 ALIGNN 对 {formula_} 进行材料性质快速估算。\n\n"
                    "2. 该模型基于晶体图神经网络，可在已有结构基础上补全关键性质。\n\n"
                    "3. 结果用于候选排序与工艺方向参考，不替代最终实验标定。\n\n"
                )

        def _infer_requirement_focus(user_context: str) -> list:
            """从上游需求文本中抽取本轮需要优先解释的工程关注点。"""
            txt = str(user_context or "").lower()
            checks = [
                (
                    "stability",
                    "稳定性/环境窗口",
                    ["稳定", "空气", "水分", "分解", "h2s", "氧化", "还原", "热力学", "stability", "stable"],
                    "需结合空气/水分暴露、应用温度、气氛和化学势边界做二次验证。",
                ),
                (
                    "thermal",
                    "散热/热管理",
                    ["导热", "散热", "热管理", "热流", "thermal", "heat", "cooling"],
                    "本轮未直接得到热导率/界面热阻，需补充热导率、热扩散率和界面热阻测试。",
                ),
                (
                    "cte",
                    "热膨胀匹配",
                    ["热膨胀", "cte", "热应力", "界面开裂", "热失配"],
                    "本轮未直接得到 CTE，需补充热膨胀系数和热循环界面可靠性验证。",
                ),
                (
                    "mechanical",
                    "力学可靠性",
                    ["力学", "强度", "应力", "应变", "疲劳", "刚度", "硬度", "可靠性", "mechanical"],
                    "需补充疲劳寿命、断裂韧性和实际结构件循环载荷测试。",
                ),
                (
                    "electronic",
                    "绝缘/电子窗口",
                    ["绝缘", "介电", "击穿", "带隙", "电压", "漏电", "雷达", "高频", "毫米波", "低损耗", "band", "dielectric"],
                    "需结合工作电压窗口、界面反应和击穿强度做联合评估。",
                ),
                (
                    "piezo",
                    "压电/机电耦合",
                    ["压电", "致动", "传感", "机电耦合", "piezo"],
                    "需结合晶向、器件结构和实验压电系数验证。",
                ),
                (
                    "manufacturing",
                    "成本/加工集成",
                    ["成本", "量产", "加工", "成型", "集成", "装配", "公差", "制造"],
                    "成本、良率和加工窗口不属于本轮 MP/ALIGNN 直接输出，需工艺与供应链侧补充。",
                ),
                (
                    "transport",
                    "传输/扩散潜力",
                    ["电导", "扩散", "迁移", "离子", "输运", "seebeck", "功率因子", "transport", "diffusion"],
                    "导电/扩散代理值仅能排序，需 EIS、迁移数或扩散系数实测闭环。",
                ),
            ]
            focus = []
            for code, label, keys, gap in checks:
                if any(k in txt for k in keys):
                    focus.append({"code": code, "label": label, "gap": gap})
            if not focus:
                focus = [
                    {"code": "stability", "label": "稳定性初筛", "gap": "需结合应用温度、气氛和化学势边界做二次验证。"},
                    {"code": "mechanical", "label": "基础力学支撑", "gap": "需补充实际工况下的力学可靠性测试。"},
                ]
            return focus

        def _build_metric_rows(final_metrics: dict) -> list:
            m = final_metrics if isinstance(final_metrics, dict) else {}
            eh = m.get("e_above_hull")
            fe = m.get("formation_energy")
            bg = m.get("band_gap")
            bulk = m.get("bulk_modulus")
            shear = m.get("shear_modulus")
            hard = m.get("hardness_est")
            cond = m.get("cond_diff_proxy")
            dielectric = m.get("dielectric_proxy")
            piezo = m.get("piezo_proxy")
            thermal_score = m.get("thermal_conductivity_proxy")
            thermal_level = m.get("thermal_conductivity_level")
            thermal_vm = m.get("thermal_mean_sound_velocity")
            thermal_diff = m.get("thermal_diffusivity_proxy")
            thermal_diff_level = m.get("thermal_diffusivity_level")
            cte_proxy = m.get("thermal_expansion_proxy")
            cte_risk = m.get("thermal_expansion_risk")
            shock_proxy = m.get("thermal_shock_proxy")
            shock_risk = m.get("thermal_shock_risk")
            seebeck = m.get("seebeck_proxy")
            power_factor = m.get("power_factor_proxy")
            needs = m.get("property_needs") if isinstance(m.get("property_needs"), dict) else {}

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
            sat_dielectric = _sat(isinstance(dielectric, float) and dielectric > 0, partial=bool(m.get("err_dielectric")))
            sat_piezo = _sat(isinstance(piezo, float), partial=bool(m.get("err_piezo")))
            sat_thermal = _sat(isinstance(thermal_score, float) and thermal_score >= 0.45, partial=isinstance(thermal_score, float))
            sat_diff = _sat(isinstance(thermal_diff, float) and thermal_diff >= 0.45, partial=isinstance(thermal_diff, float))
            sat_cte = _sat(str(cte_risk) in {"低", "中"}, partial=isinstance(cte_proxy, float))
            sat_shock = _sat(str(shock_risk) in {"低", "中"}, partial=isinstance(shock_proxy, float))
            sat_seebeck = _sat(isinstance(seebeck, float), partial=bool(m.get("err_seebeck")))
            sat_power = _sat(isinstance(power_factor, float), partial=bool(m.get("err_power_factor")))

            rows = [
                {
                    "code": "stability",
                    "label": "热力学稳定性窗口",
                    "proxy": "E_above_hull / 形成能",
                    "result": f"E_hull={_sf(eh)} eV/atom；E_form={_sf(fe)} eV/atom",
                    "sat": sat_stab,
                    "next": "需结合应用温度、气氛和化学势边界做二次验证",
                    "source_confidence": "MP数据库/ALIGNN补全；高-较高",
                    "chart": "Thermodynamic Stability",
                    "detail": f"E_hull={_sf(eh)} eV/atom，E_form={_sf(fe)} eV/atom",
                },
                {
                    "code": "electronic",
                    "label": "电子绝缘与窗口边界",
                    "proxy": "带隙 band_gap",
                    "result": f"band_gap={_sf(bg)} eV",
                    "sat": sat_bg,
                    "next": "需与工作电压窗口、击穿强度和界面副反应联合评估",
                    "source_confidence": "MP数据库/ALIGNN补全；高-较高",
                    "chart": "Electronic Window",
                    "detail": f"band_gap={_sf(bg)} eV",
                },
            ]
            if needs.get("dielectric"):
                rows.append(
                    {
                        "code": "dielectric",
                        "label": "介电/绝缘性能代理",
                        "proxy": "ALIGNN dielectric / eps proxy",
                        "result": f"dielectric_proxy={_sf(dielectric)}",
                        "sat": sat_dielectric,
                        "next": "需补充目标频率下介电损耗tanδ、击穿强度和界面介电测试",
                        "source_confidence": "ALIGNN介电预训练模型；中",
                        "chart": "Dielectric Proxy",
                        "detail": f"dielectric_proxy={_sf(dielectric)}",
                    }
                )
            if needs.get("piezo"):
                rows.append(
                    {
                        "code": "piezo",
                        "label": "压电/机电耦合代理",
                        "proxy": "ALIGNN piezo max_dij proxy",
                        "result": f"piezo_proxy={_sf(piezo)}",
                        "sat": sat_piezo,
                        "next": "需结合晶向、器件结构和实验压电系数验证",
                        "source_confidence": "ALIGNN压电预训练模型；中",
                        "chart": "Piezo Proxy",
                        "detail": f"piezo_proxy={_sf(piezo)}",
                    }
                )
            mech_conf = "MP数据库/ALIGNN补全 + Chen/Teter经验硬度；中高"
            mech_next = "需补充致密化、断裂韧性、疲劳寿命和循环后裂纹演化测试"
            if not isinstance(shear, float) or not isinstance(hard, float):
                mech_conf = "部分力学模型越界或缺失；低-中"
                mech_next = "剪切模量/硬度证据不完整，需先用实验或更高精度计算复核，再判断抗裂和成形可靠性"
            rows.append(
                {
                    "code": "mechanical",
                    "label": "机械支撑与成形风险",
                    "proxy": "体积模量/剪切模量/硬度估算",
                    "result": f"K={_sf(bulk)} GPa；G={_sf(shear)} GPa；Hv≈{_sf(hard)} GPa",
                    "sat": sat_mech,
                    "next": mech_next,
                    "source_confidence": mech_conf,
                    "chart": "Mechanical Reliability",
                    "detail": f"K={_sf(bulk)} GPa，G={_sf(shear)} GPa，Hv≈{_sf(hard)} GPa",
                }
            )
            if needs.get("thermal"):
                rows.extend(
                    [
                        {
                            "code": "thermal",
                            "label": "热导潜力估算",
                            "proxy": "声速/键强/稳定性 proxy",
                            "result": f"level={thermal_level or '待补充'}；score={_sf(thermal_score)}；v_m≈{_sf(thermal_vm, 1)} m/s",
                            "sat": sat_thermal,
                            "next": "该值仅用于快速排序，需热导率实测或声子/MD深算给出 W/mK",
                            "source_confidence": "弹性模量/密度声速proxy + 稳定性/键强经验估算；中",
                            "chart": "Thermal Conductivity Proxy",
                            "detail": f"thermal level={thermal_level or '待补充'}，score={_sf(thermal_score)}",
                        },
                        {
                            "code": "thermal_diffusivity",
                            "label": "热扩散潜力估算",
                            "proxy": "热导潜力/密度惩罚 proxy",
                            "result": f"level={thermal_diff_level or '待补充'}；score={_sf(thermal_diff)}",
                            "sat": sat_diff,
                            "next": "缺少定量热容Cp，需热扩散率或瞬态热测试验证",
                            "source_confidence": "热导潜力proxy/密度惩罚估算；中低",
                            "chart": "Thermal Diffusivity Proxy",
                            "detail": f"thermal diffusivity level={thermal_diff_level or '待补充'}，score={_sf(thermal_diff)}",
                        },
                    ]
                )
            if needs.get("cte"):
                rows.extend(
                    [
                        {
                            "code": "cte",
                            "label": "热膨胀风险估算",
                            "proxy": "刚度/键强/稳定性 proxy",
                            "result": f"risk={cte_risk or '待补充'}；score={_sf(cte_proxy)}",
                            "sat": sat_cte,
                            "next": "不直接输出 CTE，需热膨胀系数和热循环界面可靠性验证",
                            "source_confidence": "弹性刚度/键强/稳定性经验估算；低-中",
                            "chart": "CTE Risk Proxy",
                            "detail": f"CTE risk={cte_risk or '待补充'}，score={_sf(cte_proxy)}",
                        },
                        {
                            "code": "thermal_shock",
                            "label": "热震/热循环风险估算",
                            "proxy": "热导潜力/力学支撑 proxy",
                            "result": f"risk={shock_risk or '待补充'}；score={_sf(shock_proxy)}",
                            "sat": sat_shock,
                            "next": "需热冲击、热循环和界面开裂测试闭环",
                            "source_confidence": "热导潜力proxy + 剪切模量/硬度/密度经验估算；中低",
                            "chart": "Thermal Shock Risk Proxy",
                            "detail": f"thermal shock risk={shock_risk or '待补充'}，score={_sf(shock_proxy)}",
                        },
                    ]
                )
            rows.append(
                {
                    "code": "transport",
                    "label": "传输潜力代理",
                    "proxy": "导电/扩散相关量（粗略）",
                    "result": f"proxy={_sf(cond)}（无量纲）",
                    "sat": sat_trans,
                    "next": "仅用于排序，需 EIS/迁移测试给出实测值",
                    "source_confidence": "带隙/形成能/有效质量组合proxy；中",
                    "chart": "Transport Potential",
                    "detail": f"transport proxy={_sf(cond)}",
                }
            )
            if needs.get("transport"):
                rows.extend(
                    [
                        {
                            "code": "seebeck",
                            "label": "Seebeck系数代理",
                            "proxy": "ALIGNN thermoelectric proxy",
                            "result": f"Seebeck_proxy={_sf(seebeck)}",
                            "sat": sat_seebeck,
                            "next": "正负号代表响应方向或载流子类型倾向，需结合温度、载流子浓度和实验输运测试解释",
                            "source_confidence": "ALIGNN热电预训练模型；中",
                            "chart": "Seebeck Proxy",
                            "detail": f"Seebeck_proxy={_sf(seebeck)}",
                        },
                        {
                            "code": "power_factor",
                            "label": "功率因子代理",
                            "proxy": "ALIGNN thermoelectric proxy",
                            "result": f"power_factor_proxy={_sf(power_factor)}",
                            "sat": sat_power,
                            "next": "不等同于完整 ZT，仍需热导率和温度依赖输运数据",
                            "source_confidence": "ALIGNN热电预训练模型；中",
                            "chart": "Power Factor Proxy",
                            "detail": f"power_factor_proxy={_sf(power_factor)}",
                        },
                    ]
                )
            return rows

        def _ordered_metric_rows(rows: list, focus: list) -> list:
            priority = []
            focus_codes = [f.get("code") for f in focus if isinstance(f, dict)]
            if "thermal" in focus_codes:
                priority.extend(["thermal", "thermal_diffusivity", "stability", "mechanical"])
            if "cte" in focus_codes:
                priority.extend(["cte", "thermal_shock", "stability", "mechanical"])
            for code in focus_codes:
                if code in {"stability", "electronic", "dielectric", "piezo", "mechanical", "transport", "seebeck", "power_factor"}:
                    priority.append(code)
                if code == "electronic":
                    priority.append("dielectric")
                if code == "transport":
                    priority.extend(["seebeck", "power_factor"])
            priority.extend(["stability", "electronic", "dielectric", "piezo", "mechanical", "thermal", "thermal_diffusivity", "cte", "thermal_shock", "transport", "seebeck", "power_factor"])
            rank = {code: idx for idx, code in enumerate(dict.fromkeys(priority))}
            return sorted(rows, key=lambda r: rank.get(r.get("code"), 999))

        def _render_requirement_radar_svg(rows: list, focus: list, out_svg_path: str):
            row_by_code = {r.get("code"): r for r in rows if isinstance(r, dict)}
            focus_codes = [f.get("code") for f in focus if isinstance(f, dict) and f.get("code")]

            def _sat_score(sat: str) -> int:
                if sat == "满足":
                    return 7
                if sat == "部分满足":
                    return 4
                return 2

            def _score_for(code: str) -> int:
                if code == "thermal":
                    return max(_sat_score((row_by_code.get("thermal") or {}).get("sat")), _sat_score((row_by_code.get("thermal_diffusivity") or {}).get("sat")))
                if code == "cte":
                    return max(_sat_score((row_by_code.get("cte") or {}).get("sat")), _sat_score((row_by_code.get("thermal_shock") or {}).get("sat")))
                if code == "electronic":
                    return max(_sat_score((row_by_code.get("electronic") or {}).get("sat")), _sat_score((row_by_code.get("dielectric") or {}).get("sat")))
                if code == "manufacturing":
                    return 2
                return _sat_score((row_by_code.get(code) or {}).get("sat"))

            label_map = {
                "stability": "稳定性",
                "thermal": "热管理",
                "cte": "热匹配",
                "mechanical": "力学支撑",
                "electronic": "电化学窗口",
                "dielectric": "绝缘介电",
                "piezo": "压电响应",
                "transport": "离子传输",
                "manufacturing": "工艺成本",
            }
            preferred = []
            for code in focus_codes:
                mapped = "electronic" if code == "dielectric" else code
                if mapped not in preferred and mapped in label_map:
                    preferred.append(mapped)
            for code in ["transport", "electronic", "mechanical", "stability", "manufacturing", "thermal", "cte", "piezo"]:
                if code not in preferred:
                    preferred.append(code)
            axes = preferred[:5]
            if len(axes) < 3:
                return

            width, height = 360, 250
            cx, cy, radius = 180.0, 125.0, 68.0
            n = len(axes)

            def _point(idx: int, score: float):
                ang = -math.pi / 2 + 2 * math.pi * idx / n
                rr = radius * (float(score) / 7.0)
                return cx + rr * math.cos(ang), cy + rr * math.sin(ang)

            def _poly(score: float) -> str:
                return " ".join(f"{_point(i, score)[0]:.1f},{_point(i, score)[1]:.1f}" for i in range(n))

            data_points = []
            label_nodes = []
            bubble_nodes = []
            axis_nodes = []
            for idx, code in enumerate(axes):
                score = _score_for(code)
                data_points.append(_point(idx, score))
                outer_x, outer_y = _point(idx, 7)
                axis_nodes.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{outer_x:.1f}" y2="{outer_y:.1f}" stroke="#E6ECF3" stroke-width="1"/>')

                ang = -math.pi / 2 + 2 * math.pi * idx / n
                lx = cx + (radius + 31) * math.cos(ang)
                ly = cy + (radius + 31) * math.sin(ang)
                anchor = "middle"
                if math.cos(ang) > 0.35:
                    anchor = "start"
                elif math.cos(ang) < -0.35:
                    anchor = "end"
                label_nodes.append(
                    f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" dominant-baseline="middle" '
                    f'font-size="15" fill="#374151">{html.escape(label_map.get(code, code))}</text>'
                )

                bx = cx + (radius + 8) * math.cos(ang)
                by = cy + (radius + 8) * math.sin(ang)
                bubble_nodes.append(
                    f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="12" fill="#050505"/>'
                    f'<text x="{bx:.1f}" y="{by + 0.8:.1f}" text-anchor="middle" dominant-baseline="middle" '
                    f'font-size="14" font-weight="700" fill="#FFFFFF">{score}</text>'
                )

            data_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_points)
            svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#F8FAFC"/>
<g font-family="-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans CJK SC','Microsoft YaHei','PingFang SC',Arial,sans-serif">
  <polygon points="{_poly(7)}" fill="#F3F6FA" stroke="#DCE5EE" stroke-width="1.4"/>
  <polygon points="{_poly(5)}" fill="none" stroke="#E4EAF1" stroke-width="1"/>
  <polygon points="{_poly(3)}" fill="none" stroke="#E4EAF1" stroke-width="1"/>
  <polygon points="{_poly(1)}" fill="none" stroke="#E4EAF1" stroke-width="1"/>
  {''.join(axis_nodes)}
  <polygon points="{data_poly}" fill="#333333" fill-opacity="0.34" stroke="#2F2F2F" stroke-width="3" stroke-linejoin="round"/>
  {''.join(label_nodes)}
  {''.join(bubble_nodes)}
</g>
</svg>'''
            with open(out_svg_path, "w", encoding="utf-8") as f:
                f.write(svg)

        def _build_final_decision_summary(formulas_: list, mp_ready_: list, user_context: str, final_metrics: dict, rows: list, focus: list) -> str:
            selected = (mp_ready_ or formulas_ or [""])[0] if isinstance((mp_ready_ or formulas_ or [""]), list) else ""
            selected = str(selected or "当前候选材料")
            try:
                pf = self._formula_profile(selected)
                selected_name = pf.get("中文名称") or selected
            except Exception:
                selected_name = selected

            focus_labels = [f.get("label") for f in focus if isinstance(f, dict) and f.get("label")]
            focus_text = "、".join(dict.fromkeys(focus_labels)) if focus_labels else "材料稳定性与工程可行性"

            row_by_code = {r.get("code"): r for r in rows if isinstance(r, dict)}

            def _row(code: str) -> dict:
                return row_by_code.get(code) or {}

            def _row_result(code: str) -> str:
                r = row_by_code.get(code) or {}
                return str(r.get("result") or "本轮未直接计算")

            def _row_sat(code: str) -> str:
                r = row_by_code.get(code) or {}
                return str(r.get("sat") or "待补充")

            def _evidence_short(code: str) -> str:
                r = _row(code)
                result = str(r.get("result") or "").strip()
                if not result or "待补充" in result:
                    return "本轮没有形成可用数值"
                return result

            friendly_label = {
                "thermal": "导热/散热能力",
                "cte": "冷热变化下的尺寸稳定性",
                "mechanical": "强度和抗开裂能力",
                "electronic": "绝缘和电压承受能力",
                "dielectric": "绝缘/介电表现",
                "piezo": "受力发电或传感响应",
                "transport": "导电或离子通过能力",
                "stability": "材料本身稳定性",
                "manufacturing": "成本和加工难度",
            }

            def _judge_focus(item: dict) -> dict:
                code = item.get("code")
                label = friendly_label.get(code) or item.get("label") or code or "需求项"
                gap = item.get("gap") or "需补充应用工况下的验证数据。"
                if code == "manufacturing":
                    return {
                        "bucket": "boundary",
                        "label": label,
                        "text": "是否便宜、好加工、适合量产，还需要结合供应链、制备工艺和良率数据再判断。",
                    }
                if code == "thermal":
                    sat = _row_sat("thermal")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "初步看起来有较好的散热潜力，可以继续比较。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "散热能力只有初步迹象，还不能说明它真的能满足高散热场景。"}
                    return {"bucket": "missing", "label": label, "text": "这轮没有拿到能判断散热能力的结果。"}
                if code == "cte":
                    sat = _row_sat("cte")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "冷热变化带来的变形风险暂时没有明显警讯，可以继续验证。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "尺寸稳定性还只是初步判断，不能说明它在冷热循环中一定可靠。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断冷热变化下是否容易变形或开裂。"}
                if code == "mechanical":
                    sat = _row_sat("mechanical")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "基础力学表现较好，说明它有继续作为承载材料评估的价值。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "强度和抗开裂能力还没有被充分证明，不能只凭当前结果判断它能长期承受载荷。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断它是否足够结实、抗裂或耐疲劳。"}
                if code == "electronic":
                    sat = _row_sat("electronic")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "初步看起来绝缘性较好，适合继续评估电压相关应用。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "绝缘和耐电压能力还没有完全说清楚，关键电压场景需要再验证。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断它是否适合绝缘或高电压环境。"}
                if code == "piezo":
                    sat = _row_sat("piezo")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "它可能具有受力产生电响应的潜力，可以继续作为传感/致动方向候选。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断它是否适合压电、传感或致动应用。"}
                if code == "transport":
                    sat = _row_sat("transport")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "初步看起来有让电荷或离子通过的潜力，可以继续作为电池/导电相关候选。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "导电或离子通过能力只有弱信号，不能说明它已经满足电池或导电应用。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断它的导电或离子通过能力。"}
                if code == "stability":
                    sat = _row_sat("stability")
                    if sat == "满足":
                        return {"bucket": "support", "label": label, "text": "数据库结果显示它本身比较稳定，这是继续评估的基础。"}
                    if sat == "部分满足":
                        return {"bucket": "risk", "label": label, "text": "材料稳定性接近可用，但还需要看它在空气、水分或实际环境中会不会变差。"}
                    return {"bucket": "missing", "label": label, "text": "这轮还不能判断材料本身是否稳定。"}
                return {"bucket": "missing", "label": label, "text": gap}

            judgements = []
            seen_focus = set()
            for item in focus:
                if not isinstance(item, dict):
                    continue
                code = item.get("code")
                if code in seen_focus:
                    continue
                seen_focus.add(code)
                judgements.append(_judge_focus(item))

            support = [j for j in judgements if j.get("bucket") == "support"]
            risk = [j for j in judgements if j.get("bucket") == "risk"]
            missing = [j for j in judgements if j.get("bucket") == "missing"]
            boundary = [j for j in judgements if j.get("bucket") == "boundary"]

            stable_ok = _row_sat("stability") == "满足"
            if stable_ok and not any("稳定" in str(j.get("label") or "") for j in support):
                support.insert(0, {"label": "材料本身稳定性", "text": "数据库里能找到这个结构，而且初步看起来稳定，说明它不是凭空假设的材料。"})

            critical_risk = bool(risk or missing)
            if critical_risk:
                decision = f"`{selected}` 有继续评估价值，但目前还不能说它已经满足全部需求。"
                confidence_line = "当前结论偏保守：可以保留为候选，但需要补关键验证后再比较。"
            else:
                decision = f"`{selected}` 的初步表现比较正向，可以进入下一轮应用验证。"
                confidence_line = "当前结论相对积极：可以安排样品或场景测试，但最终仍要看实际测试结果。"

            lines = [
                "### 本轮结论与建议",
                "",
                f"**总体结论**：{decision}",
                "",
                f"**结论可信度**：{confidence_line}",
                "",
            ]

            if support:
                lines.append("**已经看到的积极信号**")
                lines.append("")
                seen_labels = set()
                for item in support:
                    label = str(item.get("label") or "")
                    if label in seen_labels:
                        continue
                    seen_labels.add(label)
                    lines.append(f"- {item.get('label')}：{item.get('text')}")
                    lines.append("")
                    if len(seen_labels) >= 3:
                        break

            if risk:
                lines.append("**还需要小心的地方**")
                lines.append("")
                seen_labels = set()
                for item in risk:
                    label = str(item.get("label") or "")
                    if label in seen_labels:
                        continue
                    seen_labels.add(label)
                    lines.append(f"- {item.get('label')}：{item.get('text')}")
                    lines.append("")
                    if len(seen_labels) >= 3:
                        break

            if missing:
                lines.append("**这轮还没回答的问题**")
                lines.append("")
                seen_labels = set()
                for item in missing:
                    label = str(item.get("label") or "")
                    if label in seen_labels:
                        continue
                    seen_labels.add(label)
                    lines.append(f"- {item.get('label')}：{item.get('text')}")
                    lines.append("")
                    if len(seen_labels) >= 3:
                        break

            if boundary:
                lines.append("**还需要补充的信息**")
                lines.append("")
                for item in boundary[:2]:
                    lines.append(f"- {item.get('label')}：{item.get('text')}")
                    lines.append("")

            next_steps = []
            focus_codes = {f.get("code") for f in focus if isinstance(f, dict)}
            if "transport" in focus_codes:
                next_steps.append("先测实际导电或离子通过能力，这是判断它能不能用于电池/导电场景的关键。")
            if "electronic" in focus_codes:
                next_steps.append("补耐电压和界面稳定性测试，确认它在实际工作电压下不会失效。")
            if "mechanical" in focus_codes:
                next_steps.append("补强度、抗裂和长期受力测试，确认它不是只在计算里表现好。")
            if "stability" in focus_codes:
                next_steps.append("补空气、水分或目标环境下的稳定性测试，确认储存和使用时不会明显劣化。")
            if not next_steps:
                next_steps.append("补充最贴近实际使用场景的关键测试，再决定是否继续推进。")

            lines.append("**建议下一步**")
            lines.append("")
            for idx, text in enumerate(next_steps[:3], start=1):
                lines.append(f"{idx}. {text}")
                lines.append("")

            if critical_risk:
                final_text = f"{selected_name}现在更像一个“有希望但还没确认”的候选材料。是否采用它，关键取决于后续能否补上上面这些短板。"
            else:
                final_text = f"{selected_name}可以进入下一轮验证；最终是否采用，还要看真实使用场景测试和成本是否合适。"
            lines.append(f"**最终判断**：{final_text}")
            return "\n".join(lines) + "\n\n"

        async def _stream_final_requirement_summary(formulas_: list, mp_ready_: list, user_context: str = "", final_metrics: dict = None):
            """目标-结果对照收敛：基于真实计算值输出，并结合上游需求生成结论。"""
            await _open_material_block("MATERIAL_SCREENING")
            await websocket.send_text("\n\n### 材料性能判读\n\n")
            focus = _infer_requirement_focus(user_context)
            rows = _ordered_metric_rows(_build_metric_rows(final_metrics), focus)
            try:
                chart_abs = f"/tmp/requirement_radar_{str(taskid).replace('/', '_')}.svg"
                _render_requirement_radar_svg(rows, focus, chart_abs)
                chart_url = await _upload_database_pic_for_markdown(chart_abs, "requirement_radar.svg")
                if chart_url:
                    await websocket.send_text("#### 需求雷达\n\n")
                    await websocket.send_text(f"![需求雷达]({chart_url})\n\n")
                    await websocket.send_text("注：图中 1-7 分为快速筛选判读分，不是实验性能绝对值；低分代表本轮证据不足或不属于本轮可计算范围。\n\n")
            except Exception as e:
                logger.exception(f"[REQ_RADAR] render/upload failed: {e!s}")
            await websocket.send_text(_build_final_decision_summary(formulas_, mp_ready_, user_context, final_metrics, rows, focus))
            await _close_material_block("MATERIAL_SCREENING")

        async def _stream_final_li6ps5cl_bridge(formulas_: list):
            """ADiT/MACE 旧桥接函数已下线，保留空壳以保持接口稳定。"""
            return

        async def _stream_non_mp_material_route_summary(formulas_: list, notes: list, user_context: str = "", in_ls_summary: dict = None):
            """聚合物/复合耗材等非晶体化学式候选：不走 MP/ALIGNN，给出工程路由说明。"""
            await _open_material_block("MATERIAL_SCREENING")
            materials = [str(x).strip() for x in (formulas or []) if str(x).strip()]
            material_text = "、".join(f"`{m}`" for m in materials[:5]) or "当前候选材料体系"
            ctx = str(user_context or "")
            is_printing = bool(re.search(r"3d打印|3D打印|增材|FDM|FFF|耗材|打印|层间|连续碳纤维|短切|纤维增强", ctx, flags=re.IGNORECASE))
            await websocket.send_text("\n\n### 材料路线判定\n\n")
            if is_printing:
                await websocket.send_text(
                    f"**一句话结论**：{material_text} 更像 3D 打印聚合物/纤维增强复合耗材体系，不适合按无机晶体化学式进入 MP/ALIGNN；"
                    "本轮应转为工艺-结构-力学性能筛选。\n\n"
                )
            else:
                await websocket.send_text(
                    f"**一句话结论**：{material_text} 不是标准无机晶体化学式，本轮不强行进入 MP/ALIGNN，避免得到误导性结果。\n\n"
                )
            await websocket.send_text("**为什么不走化学式路线**\n\n")
            await websocket.send_text("- `PA12/C`、`SCF-PA12`、`CF-PA6` 代表基体、增强相和工艺形态的组合，不是 Materials Project 可直接检索的单相晶体结构。\n\n")
            await websocket.send_text("- 对这类材料，决定性能的核心通常是纤维长度/取向、体积分数、层间结合、打印路径、孔隙率、热历史和后处理，而不是单一化学式。\n\n")
            await websocket.send_text("**建议路由**\n\n")
            if is_printing:
                await websocket.send_text("1. 走 3D 打印复合耗材/结构件筛选：PA12-CF、PA6-CF、连续碳纤维增强 PA/PEEK 等按材料体系比较。\n\n")
                await websocket.send_text("2. 核心指标改为拉伸强度、弯曲模量、疲劳寿命、层间剪切强度、尺寸稳定性、打印窗口和成本。\n\n")
                await websocket.send_text("3. 若后续要计算，应接复合材料/聚合物性能模型或实验数据库，而不是 MP 晶体结构检索。\n\n")
            else:
                await websocket.send_text("1. 若目标是已知无机晶体，请补充标准化学式，例如 `Li6PS5Cl`、`AlN`。\n\n")
                await websocket.send_text("2. 若目标是复合材料、聚合物或工艺体系，请进入工程性能筛选路线，按宏观性能和工艺参数评估。\n\n")
            if notes:
                await websocket.send_text("**路由备注**\n\n")
                for note in notes[:4]:
                    await websocket.send_text(f"- {note}\n\n")
            await _close_material_block("MATERIAL_SCREENING")

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

            async def _on_progress_event(ev: dict):
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

            run_res = await _run_mp_export_assets_streaming_external(
                repo_root=repo_root,
                taskid=str(taskid),
                formula=str(formula),
                eta_seconds=float(_mp_eta_seconds),
                progress_emit_interval_s=int(progress_emit_interval_s),
                progress_callback=_on_progress_event,
            )

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
            inline_tokens = _extract_inline_formula_tokens(formula_extract_text)
            if inline_tokens:
                raw_tokens = list(dict.fromkeys((raw_tokens or []) + inline_tokens))

            has_primary_formula = any(_is_primary_formula_token(t) for t in (raw_tokens or []))
            in_ls_tokens, in_ls_summary = _extract_formulas_from_in_ls(_repo_root())
            if has_primary_formula:
                # 当前输入已包含明确化学式时，避免被历史 in-LS 结果“劫持”。
                in_ls_tokens, in_ls_summary = [], {}
                logger.info("[ROUTER] skip in-LS merge because primary formulas exist in current input")
            elif in_ls_tokens:
                # 仅在正文未抽到有效化学式时，回退使用 in-LS 结果
                raw_tokens = list(dict.fromkeys((raw_tokens or []) + in_ls_tokens))
                logger.info(f"[ROUTER] merged in-LS fallback tokens={in_ls_tokens}")
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
                    await _stream_non_mp_material_route_summary(formulas, non_mp_notes, user_context=norm, in_ls_summary=in_ls_summary)
                    await websocket.send_text("当前候选不适合无机晶体数据库检索，已完成材料路线判定，建议接入复合材料/3D打印工程筛选流程。\n")
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
                    await websocket.send_text("\n\n#### 材料性质补充分析\n\n")
                    # ALIGNN阶段说明保持在左侧
                    await _stream_alignn_stage_intro(selected_formula)
                    await self._send_content_start(websocket, "MATERIAL_SCREENING")
                    selected_metrics = await self._material_alignn_placeholder_stage(websocket, selected_formula, llm=llm, user_context=norm)
                    await self._send_content_end(websocket, "MATERIAL_SCREENING")
                else:
                    await websocket.send_text("\n无可用于材料性质计算的候选结构，已结束本轮计算。\n")

                # 最终需求对照总结（右侧）
                await _stream_final_requirement_summary(formulas, mp_ready_formulas, user_context=norm, final_metrics=selected_metrics)

                # 左侧：流程完成播报，具体选型结论已在右侧结果区输出
                await websocket.send_text("\n本轮材料模拟与需求对照已完成，具体初筛结论和待补充验证项已写入，正在接入下一流程。\n")
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
    "职责边界：仅执行已有无机材料数据库检索、结构与性质补全、候选排序与结果整理；"
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
    
