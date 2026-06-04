"""ALIGNN-based property completion for material screening."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import subprocess

from alpha.logs import logger

from src.material_workflow.material_profiles import formula_profile


def extract_cif_path_from_item(item: dict, base_dir: str) -> str:
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


def pick_num(item: dict, keys: list):
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


def call_alignn_pretrained(model_name: str, cif_path: str, timeout_sec: int = 30):
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


def try_alignn_models(
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
            val = call_alignn_pretrained(mn, cif_path, timeout_sec=timeout_sec)
            pred_cache[cache_key] = val
            return val, mn, ""
        except Exception as e:
            last_err = str(e)
            pred_cache[cache_key] = None
            err_l = last_err.lower()
            if ("keyerror" in err_l) or ("not found" in err_l and "model" in err_l):
                invalid_models.add(mn)
    return None, "", last_err


def probe_alignn_model(model_name: str, cif_path: str):
    """轻量探测：返回 (ok, err)。"""
    try:
        _ = call_alignn_pretrained(model_name, cif_path)
        return True, ""
    except Exception as e:
        return False, str(e)


async def run_alignn_completion_stage(
    *,
    stream_llm_response,
    websocket,
    formula: str,
    llm=None,
    repo_root: str = "",
    current_taskid: str = "",
):
    """
    MP-first + ALIGNN completion + proxy ranking
    - 优先使用 MP 字段
    - 缺失时用 ALIGNN 补 formation_energy / band_gap / bulk / shear
    - 生成 hardness proxy、conductivity/diffusion proxy 和候选排序
    """


    repo_root = os.path.abspath(repo_root or os.getcwd())
    root_path = f"src/MNS_CaseHub/cases/material_discovery_demo"
    abs_root = os.path.abspath(os.path.join(repo_root, root_path))
    results_dir = os.path.join(abs_root, "results")

    # 优先使用当前会话 taskid，避免命中历史目录导致候选共用旧 structure
    taskid_s = str(current_taskid or "").replace("/", "_")
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
        p_item = extract_cif_path_from_item(it, base_dir_)
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
        e_hull = pick_num(it, ["energy_above_hull", "e_above_hull", "energy_above_hull_ev_per_atom"])
        fe = pick_num(it, ["formation_energy_per_atom", "formation_energy", "e_form", "formation_energy_ev_per_atom"])
        bg = pick_num(it, ["band_gap", "bandgap", "band_gap_ev"])
        bulk = pick_num(it, ["bulk_modulus", "bulk_modulus_gpa", "kvrh", "k_vrh"])
        shear = pick_num(it, ["shear_modulus", "shear_modulus_gpa", "gvrh", "g_vrh"])
        density = pick_num(it, ["density", "density_g_cm3"])
        elec_mass = pick_num(it, ["avg_elec_mass", "avg_electron_mass", "electron_effective_mass", "m_e_avg"])
        hole_mass = pick_num(it, ["avg_hole_mass", "hole_effective_mass", "m_h_avg"])

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
            ok_probe, err_probe = probe_alignn_model(BULK_MODELS[0], cif_path)
            model_probe_done = True
            model_probe_msg = "ALIGNN模型可用" if ok_probe else f"ALIGNN模型探测失败: {err_probe[:220]}"

        if (e_hull is None) and cif_path and os.path.exists(cif_path):
            eh_pred, mn, _ = try_alignn_models(cif_path, EHULL_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if eh_pred is not None:
                e_hull, e_hull_src = eh_pred, f"ALIGNN:{mn}"

        if (fe is None) and cif_path and os.path.exists(cif_path):
            fe_pred, mn, _ = try_alignn_models(cif_path, FE_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if fe_pred is not None:
                fe, fe_src = fe_pred, f"ALIGNN:{mn}"

        if (bg is None) and cif_path and os.path.exists(cif_path):
            bg_pred, mn, _ = try_alignn_models(cif_path, BG_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bg_pred is not None:
                bg, bg_src = bg_pred, f"ALIGNN:{mn}"

        if (bulk is None) and cif_path and os.path.exists(cif_path):
            bulk_pred, mn, _ = try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if bulk_pred is not None:
                bulk, bulk_src = bulk_pred, f"ALIGNN:{mn}"
            else:
                _, _, bulk_err = try_alignn_models(cif_path, BULK_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (bulk is None) and (not cif_path or not os.path.exists(cif_path)):
            bulk_err = f"cif缺失或路径无效({cif_source})"

        if (shear is None) and cif_path and os.path.exists(cif_path):
            shear_pred, mn, _ = try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if shear_pred is not None:
                shear, shear_src = shear_pred, f"ALIGNN:{mn}"
            else:
                _, _, shear_err = try_alignn_models(cif_path, SHEAR_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (shear is None) and (not cif_path or not os.path.exists(cif_path)):
            shear_err = f"cif缺失或路径无效({cif_source})"

        if (elec_mass is None) and cif_path and os.path.exists(cif_path):
            em_pred, mn, _ = try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if em_pred is not None:
                elec_mass, elec_mass_src = em_pred, f"ALIGNN:{mn}"
            else:
                _, _, em_err = try_alignn_models(cif_path, ELEC_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
        elif (elec_mass is None) and (not cif_path or not os.path.exists(cif_path)):
            em_err = f"cif缺失或路径无效({cif_source})"

        if (hole_mass is None) and cif_path and os.path.exists(cif_path):
            hm_pred, mn, _ = try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
            if hm_pred is not None:
                hole_mass, hole_mass_src = hm_pred, f"ALIGNN:{mn}"
            else:
                _, _, hm_err = try_alignn_models(cif_path, HOLE_MASS_MODELS, invalid_models=invalid_models, pred_cache=pred_cache, timeout_sec=timeout_sec)
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

    p_formula = formula_profile(formula)
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
            rendered = await stream_llm_response(
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

