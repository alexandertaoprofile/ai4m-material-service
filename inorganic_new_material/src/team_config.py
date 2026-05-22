# -*- coding: utf-8 -*-
import os
import re
import sys
import asyncio
import subprocess
import json
import datetime

from dotenv import load_dotenv

from alpha.roles import Role
from alpha.logs import logger
from alpha.actions import Action, UserRequirement

from src.llm_utils import SeLLM, load_config
from src.material_workflow.prompts import MATERIAL_MP_EXPLAIN_PROMPT
from src.material_workflow.material_profiles import formula_profile
from src.material_workflow.mp_results import build_material_parameters, collect_material_outputs
from src.material_workflow.payloads import build_payload
from src.material_workflow.llm_streaming import stream_llm_response
from src.material_workflow.frontend_assets import send_results_to_frontend
from src.material_workflow.database_pics import resolve_public_pic_path, upload_database_pic_for_markdown
from src.material_workflow.alignn_completion import run_alignn_completion_stage
from src.material_workflow.formula_router import (
    build_candidate_lists,
    build_formula_extraction_text,
    extract_formulas_from_in_ls,
    extract_formulas_from_targets,
    looks_like_formula,
    normalize_user_text,
    parse_route,
    to_ascii_formula,
)


def _repo_root() -> str:
    # 当前文件: .../ai4m_tqm/src/team_config.py
    # 仓库根:   .../ai4m_tqm
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


load_dotenv()
today = datetime.datetime.now().strftime("%Y%m%d")

os.makedirs("logs", exist_ok=True)
logger.configure(handlers=[
    {"sink": sys.stdout, "level": "INFO"},
    {"sink": f"logs/{today}.txt", "level": "INFO", "enqueue": True}
])


os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:10240"


########################################
# Coding Action 模块
# - 功能：组织材料初筛、MP 产物下发、ALIGNN 性质补全与前端流式输出
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def _stream_llm_response(
        self,
        llm,
        messages,
        websocket=None,
        mirror_to_content: bool = False,
        mirror_step_id: str = "",
    ) -> str:
        return await stream_llm_response(
            llm,
            messages,
            websocket=websocket,
            mirror_to_content=mirror_to_content,
            mirror_step_id=mirror_step_id,
            logger_obj=logger,
        )

    async def _material_mp_explain_stage(self, llm, websocket, query: str, parameters: dict, taskid: str):
        # parameters 建议是你 _build_material_parameters 的输出
        # 或者你已拿到的 MP manifest 结构化摘要（越结构化越好）
        prompt = MATERIAL_MP_EXPLAIN_PROMPT.format(
            query=str(query or ""),
            parameters=json.dumps(parameters, ensure_ascii=False, indent=2),
        )

        # 直接走你现成的流式输出
        await self._stream_llm_response(
            llm,
            [llm._default_system_msg(), llm._user_msg(prompt)],
            websocket
        )

    async def _material_alignn_completion_stage(self, websocket, formula: str, llm=None):
        return await run_alignn_completion_stage(
            stream_llm_response=self._stream_llm_response,
            websocket=websocket,
            formula=formula,
            llm=llm,
            repo_root=_repo_root(),
            current_taskid=str(getattr(self, "_current_taskid", "") or ""),
        )

    async def run(self, instruction: str, *args):
        websocket = args[0]
        user_name, taskid, file_metadata = args[1], args[2], args[3]
        self._current_taskid = str(taskid)

        config = load_config("config/config.yaml")
        llm = SeLLM(base_url=config["base_url_1"], api_key=config["api_key"])

        CASE_MP = "material_discovery_demo"

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

        # Formula routing helpers are implemented in material_workflow.formula_router.

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

            await websocket.send_text("\n\n#### 关键材料需求提炼\n\n")
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

            await websocket.send_text("\n\n#### 候选材料概览\n\n")
            await websocket.send_text("| 化学式 | 中文名称 | 材料类别 | 应用角色 | 入选原因（对应宏观目标） |\n")
            await websocket.send_text("|---|---|---|---|---|\n")
            for f in fs:
                p = formula_profile(f)
                await websocket.send_text(
                    f"| {f} | {p['中文名称']} | {p['材料类别']} | {p['应用角色']} | 对应稳定性/传导/机械等宏观目标的候选映射 |\n"
                )

        async def _stream_macro_micro_bridge(formulas_: list, user_context: str = ""):
            """Send a stable database-comparison table.

            This table is intentionally deterministic. Streaming an LLM-generated
            Markdown table can leave malformed partial rows in the frontend when
            the model emits the conclusion before the header or changes columns.
            """
            await websocket.send_text("\n\n### 材料数据库选择依据\n\n")
            lines = [
                "| 对比维度 | 微观数据库（MP/DFT等） | 宏观数据库（经验/工艺侧） | 对筛选决策的影响 |",
                "|---|---|---|---|",
                "| 覆盖完整性 | 字段体系较完整，结构/热力学/电子性质覆盖较系统 | 数据分布常受项目与场景限制，覆盖不均 | 初筛更适合先用微观数据库建立统一比较基线 |",
                "| 性质可信度 | 基于统一计算口径，参数可复算、可追溯 | 受制备与测试条件影响大，跨批次波动明显 | 需要先用微观数据缩小候选，再做实验校核 |",
                "| 理论一致性 | 物理定义清晰，跨材料对比一致性更强 | 指标定义与测试边界可能不一致 | 微观数据库更利于多候选横向排序 |",
                "| 工艺敏感性 | 对工艺扰动不直接编码，适合做先验筛选 | 对工艺条件高度敏感，更贴近实际制造差异 | 宏观数据库更适合后验修正与落地评估 |",
                "| 跨来源可比性 | 同口径字段便于跨来源汇总与自动化判读 | 异源数据口径不一，直接对比风险高 | 先微观后宏观可降低误判与偏差放大 |",
                "",
                "结论：仿真模拟阶段优先微观数据库，宏观数据库用于后验校核与工程修正。",
                "",
            ]
            await websocket.send_text("\n".join(lines))

        async def _stream_mp_stage_intro(formula_: str):
            """
            MP阶段前的简短真流式说明：介绍正在进行什么、MP是什么、本轮提取哪些字段。
            """
            # 先发送 MP 静态图，再进入文字说明，和前端展示顺序保持一致。
            mp_abs = resolve_public_pic_path(_repo_root(), "mp.png")
            mp_url = await upload_database_pic_for_markdown(
                pic_abs_path=mp_abs,
                pic_name="mp.png",
                taskid=str(taskid),
                logger_obj=logger,
            )
            if mp_url:
                await websocket.send_text(f"\n\n![Materials Project 数据库示意图]({mp_url})\n\n")

            await websocket.send_text("\n\n#### 材料数据库检索说明\n\n")

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


        # =========================
        # 5) MP 运行：mp_export_assets.py
        # =========================
        async def _run_mp_export_assets(formula: str) -> bool:
            repo_root = _repo_root()
            script = os.path.join(repo_root, "tools", "mp_export_assets.py")
            formula = to_ascii_formula(formula)

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
        # 7) MP：导出 + 右侧下发 + 左侧解释
        # =========================
        async def _mp_one(formula: str) -> bool:
            formula = to_ascii_formula(formula)

            ok = await _run_mp_export_assets(formula)
            if not ok:
                await websocket.send_text(
                    f"{formula} 在 MP 数据库中未检索到可用结果。"
                    "可视为全新材料候选，建议转入新材料发现流程。\n"
                )
                return False

            repo_root = _repo_root()
            root_path = f"src/MNS_CaseHub/cases/{CASE_MP}"

            await send_results_to_frontend(
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

            # ✅ 左侧解释：你已有
            try:
                collected = collect_material_outputs(repo_root, taskid, jobid=formula)
                parameters = build_material_parameters(collected)

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
        # 9) 统一入口：route / content
        # =========================
        norm = normalize_user_text(instruction)
        route, content = parse_route(norm)
        content = to_ascii_formula(content)
        formula_extract_text = build_formula_extraction_text(norm)

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
            if not looks_like_formula(formula):
                await websocket.send_text("⚠️ /mp 后必须是化学式，例如：/mp Li6PS5Cl\n")
                return

            await _open_material_block("MATERIAL_SCREENING")
            try:
                # 进入材料流程即触发 progress
                await _ensure_material_progress_started()
                p = formula_profile(formula)
                await websocket.send_text(f"### 材料对应化学结构信息\n\n正在处理材料：`{formula}（{p['中文名称']}）`\n")
                await _stream_mp_stage_intro(formula)

                await _mp_one(formula)
            finally:
                await _close_material_block("MATERIAL_SCREENING")
            return

        # =========================
        # 11) 默认路径：按“计算对象”批量跑 MP + ALIGNN
        # =========================
        if True:
            raw_tokens = extract_formulas_from_targets(formula_extract_text)
            in_ls_tokens = extract_formulas_from_in_ls(_repo_root())
            if in_ls_tokens:
                # 合并第三来源，去重保持顺序
                raw_tokens = list(dict.fromkeys((raw_tokens or []) + in_ls_tokens))
            formulas, mp_formulas, non_mp_notes, dropped_tokens = build_candidate_lists(raw_tokens)
            logger.info(f"[ROUTER] raw_formula_tokens={raw_tokens}")
            if dropped_tokens:
                logger.info(f"[ROUTER] dropped_formula_tokens={dropped_tokens}")
            logger.info(f"[ROUTER] extracted_display_tokens={formulas}")
            logger.info(f"[ROUTER] extracted_mp_tokens={mp_formulas}")

            if formulas:
                await _open_material_block("MATERIAL_SCREENING")
                try:
                    # 进入材料流程即触发 progress
                    await _ensure_material_progress_started()

                    # ✅ 流程3起始前置说明（在 MP 之前）
                    try:
                        await _stream_route_intro_before_mp(formulas, user_context=norm)
                    except Exception as e:
                        logger.exception(f"[ROUTE_INTRO_STREAM] failed: {e!s}")

                    await _stream_formula_readable_view(formulas, user_context=norm)
                    await _stream_macro_micro_bridge(formulas, user_context=norm)
                    await websocket.send_text("\n\n#### 候选材料的化学结构信息\n\n")

                    if non_mp_notes:
                        await websocket.send_text("以下候选仅用于体系展示，不直接作为 MP 化学式检索：\n")
                        for _n in non_mp_notes:
                            await websocket.send_text(f"- {_n}\n")
                        await websocket.send_text("\n")

                    # Phase A：仅计算一个候选，但按顺序逐个尝试，命中首个可用 MP 后停止
                    mp_ready_formulas = []
                    selected_formula = ""
                    selected_metrics = {}
                    await websocket.send_text("\n本轮按候选顺序搜索数据库中。\n")

                    for f in mp_formulas:
                        pf = formula_profile(f)
                        await websocket.send_text(f"\n#### {f}（{pf['中文名称']}）\n")
                        logger.info(f"[MP_SCREENING] single_formula_first_hit_mode start formula={f}")
                        await _stream_mp_stage_intro(f)
                        ok = await _mp_one(f)
                        if ok:
                            selected_formula = f
                            mp_ready_formulas = [f]
                            await websocket.send_text(f"材料 `{f}` 命中可用 MP 结果，进入后续性质补充分析并停止后续候选尝试。\n")
                            break
                        else:
                            await websocket.send_text(f"材料 `{f}` 未命中 MP 可用结果，继续尝试下一候选。\n")

                    # 当前版本执行 MP + ALIGNN，未命中 MP 的候选转入新材料发现提示。
                    if mp_ready_formulas:
                        await websocket.send_text("\n\n#### 材料性质补充分析\n\n")
                        await _stream_alignn_stage_intro(selected_formula)
                        selected_metrics = await self._material_alignn_completion_stage(websocket, selected_formula, llm=llm)
                    else:
                        await websocket.send_text("\n无可用于材料性质计算的候选结构，已结束本轮计算。\n")

                    # 最终需求对照总结（流式）
                    await _stream_final_requirement_summary(formulas, mp_ready_formulas, user_context=norm, final_metrics=selected_metrics)

                    await websocket.send_text("\n材料模拟与计算模块完成。\n")
                finally:
                    await _close_material_block("MATERIAL_SCREENING")
                return


            
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
    name: str = "XIMUAlpha_inoragnic_existing_materials"
    # 简要画像（供框架/上游作为 system profile 使用）
    profile: str = (
        "材料发现与跨尺度仿真专用智能体。"
        "能力覆盖材料初筛、结构与稳定性评估、"
        "以及基于上游文献筛选的 DFT、机器学习势（MLIP）和 LAMMPS 的材料性质计算与验证。"
        "擅长从已有计算产物（manifest / report / JSON）中组织工程化材料计算说明，"
        "并以结构化 JSON 形式输出结果与可视化资源索引，"
        "适用于固态电解质、功能材料与工程材料的计算评估场景。"
    )
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 保持不变
        self._watch([UserRequirement])
        self.set_actions([Coding])
    
