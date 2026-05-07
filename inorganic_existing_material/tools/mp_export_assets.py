#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ====== ensure repo root in sys.path ======
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# =========================================

from dotenv import load_dotenv
from mp_api.client import MPRester
from pymatgen.core import Composition, Structure
from pymatgen.core.periodic_table import Element

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from tools.structure_to_glb import export_glb_mpstyle


DEFAULT_FIELDS = [
    "material_id",
    "formula_pretty",
    "elements",
    "chemsys",
    "nsites",
    "symmetry",
    "density",
    "volume",
    "energy_above_hull",
    "is_stable",
    "energy_per_atom",
    "formation_energy_per_atom",
    "band_gap",
    "database_IDs",
]

LEGACY_MP_ID_RE = re.compile(r"^mp-\d+$")
MP_ID_RE = re.compile(r"^mp-[A-Za-z0-9]+$")


# -----------------------------
# Paths
# -----------------------------
def repo_root() -> str:
    return REPO_ROOT


def results_root() -> str:
    return os.path.join(
        repo_root(),
        "src",
        "MNS_CaseHub",
        "cases",
        "material_discovery_demo",
        "results",
        "mp",
    )


def ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p


def safe_fs(s: str) -> str:
    s = (s or "").strip() or "no_id"
    return s.replace("/", "_")


def job_dir(taskid: str, jobid: str) -> str:
    # .../results/mp/<taskid>/<jobid>/
    return ensure_dir(os.path.join(results_root(), safe_fs(taskid), safe_fs(jobid)))


def write_json(path: str, obj: Any) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_text(path: str, text: str) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# -----------------------------
# MP key
# -----------------------------
def load_key() -> str:
    load_dotenv(os.path.join(repo_root(), ".env"))
    key = os.getenv("MP_API_KEY") or os.getenv("MAPI_KEY") or os.getenv("MP_API_TOKEN")
    if not key:
        raise RuntimeError("MP_API_KEY not found. Put it in repo_root/.env or export MP_API_KEY.")
    return key.strip()


# -----------------------------
# Utils
# -----------------------------
def to_dict(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if hasattr(x, "dict"):
        return x.dict()
    if isinstance(x, dict):
        return x
    return {"_raw": str(x)}


def is_mp_id(s: Optional[str]) -> bool:
    if not s:
        return False
    return bool(MP_ID_RE.match(str(s).strip()))


def reduced_formula(formula: str) -> str:
    comp = Composition(formula)
    return comp.reduced_formula


def extract_legacy_ids(database_IDs: Any) -> List[str]:
    out: List[str] = []
    if database_IDs is None:
        return out

    candidates: List[str] = []
    if isinstance(database_IDs, str):
        candidates = [database_IDs]
    elif isinstance(database_IDs, list):
        candidates = [str(i) for i in database_IDs]
    elif isinstance(database_IDs, dict):
        for v in database_IDs.values():
            if v is None:
                continue
            if isinstance(v, list):
                candidates.extend([str(i) for i in v])
            else:
                candidates.append(str(v))

    for c in candidates:
        c = str(c).strip()
        if LEGACY_MP_ID_RE.match(c):
            out.append(c)
    return sorted(set(out))


def doc_all_ids(d: Dict[str, Any]) -> List[str]:
    """
    返回这条 summary doc 所有可能 ID（material_id + legacy ids）
    """
    ids = []
    mid = d.get("material_id")
    if mid:
        ids.append(str(mid).strip())
    ids.extend(extract_legacy_ids(d.get("database_IDs")))
    return sorted(set([i for i in ids if i]))


def match_any_id(d: Dict[str, Any], wanted_ids: List[str]) -> bool:
    if not wanted_ids:
        return False
    wanted = set([str(x).strip() for x in wanted_ids if str(x).strip()])
    have = set(doc_all_ids(d))
    return len(wanted.intersection(have)) > 0


def pick_symmetry_str(sym: Any) -> str:
    if isinstance(sym, dict):
        cs = sym.get("crystal_system")
        sg = sym.get("symbol")
        no = sym.get("number")
        parts = [str(x) for x in [cs, sg, no] if x is not None and str(x) != ""]
        return "/".join(parts)
    return str(sym) if sym is not None else ""


def sort_key(d: Dict[str, Any], prefer_stable: bool) -> Tuple:
    """
    prefer_stable=True: stable first
    then e_above_hull asc, formation_energy asc, nsites asc
    """
    stable = d.get("is_stable")
    stable_rank = 0 if stable else 1
    if not prefer_stable:
        stable_rank = 0

    eh = d.get("energy_above_hull")
    fe = d.get("formation_energy_per_atom")
    nsites = d.get("nsites")

    ehv = float(eh) if eh is not None else 1e9
    fev = float(fe) if fe is not None else 1e9
    nsv = int(nsites) if nsites is not None else 10**9

    return (stable_rank, ehv, fev, nsv)


def summary_item(d: Dict[str, Any]) -> Dict[str, Any]:
    sym = d.get("symmetry") or {}
    sym_out = sym
    if isinstance(sym, dict):
        sym_out = {
            "crystal_system": sym.get("crystal_system"),
            "symbol": sym.get("symbol"),
            "number": sym.get("number"),
        }

    ids = doc_all_ids(d)
    legacy = [x for x in ids if LEGACY_MP_ID_RE.match(x)]
    legacy_id = legacy[0] if legacy else None

    return {
        "material_id": d.get("material_id"),
        "legacy_numeric_id": legacy_id,
        "all_ids": ids,
        "formula_pretty": d.get("formula_pretty"),
        "elements": d.get("elements"),
        "chemsys": d.get("chemsys"),
        "nsites": d.get("nsites"),
        "density": d.get("density"),
        "volume": d.get("volume"),
        "energy_above_hull": d.get("energy_above_hull"),
        "is_stable": d.get("is_stable"),
        "energy_per_atom": d.get("energy_per_atom"),
        "formation_energy_per_atom": d.get("formation_energy_per_atom"),
        "band_gap": d.get("band_gap"),
        "symmetry": sym_out,
        "database_IDs": d.get("database_IDs"),
    }


def md_table(items: List[Dict[str, Any]], title: str) -> str:
    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"- 生成时间：{datetime.now().isoformat()}\n")
    lines.append(f"- 展示条目数：{len(items)}\n\n")

    lines.append("| 序号 | 材料ID | 对称性（晶系/空间群） | 原子位点数 | 是否稳定 | 距稳定相包络能量差（eV/atom） | 形成能（eV/atom） | 带隙（eV） | 筛选原因 |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---|")
    
    for i, it in enumerate(items, start=1):
        sym = pick_symmetry_str(it.get("symmetry"))
        reason = it.get("selected_reason", "")
        lines.append(
            f"| {i} | {it.get('material_id','')} | {sym} | "
            f"{it.get('nsites','')} | {it.get('is_stable','')} | {it.get('energy_above_hull','')} | "
            f"{it.get('formation_energy_per_atom','')} | {it.get('band_gap','')} | {reason} |"
        )
    lines.append("\n")
    return "\n".join(lines)


def _species_color(el: str) -> str:
    """元素配色：优先用高对比固定色，其次 CPK，最后回退默认色。"""
    strong_map = {
        "Al": "#4F81BD",  # 蓝
        "N": "#E24A33",   # 橙红
        "Li": "#8E6CFF",
        "O": "#D62728",
        "S": "#F1C40F",
        "P": "#2CA02C",
        "Cl": "#17BECF",
        # 避免灰黑导致“脏”和“黑白化”观感
        "Si": "#6F8FB8",
        "C": "#B96D6D",
        "H": "#BDBDBD",
    }
    if str(el) in strong_map:
        return strong_map[str(el)]
    try:
        e = Element(str(el))
        c = getattr(e, "CPK_color", None)
        if c and len(c) == 3:
            r, g, b = c
            return (r / 255.0, g / 255.0, b / 255.0)
    except Exception:
        pass
    return "#8b5e5e"


def render_candidate_ortho_card(
    structure: Structure,
    out_png: str,
    title: str,
    material_id: str,
    symmetry_text: str,
) -> Dict[str, Any]:
    """
    生成风格化结构卡片：上方二维结构投影 + 下方分块参数信息。
    """
    ensure_dir(os.path.dirname(out_png))

    lat = structure.lattice
    a, b, c = lat.a, lat.b, lat.c
    alpha, beta, gamma = lat.alpha, lat.beta, lat.gamma
    nsites = len(structure.sites)

    fig = plt.figure(figsize=(4.4, 7.3), dpi=160)
    gs = fig.add_gridspec(100, 1)
    ax_view = fig.add_subplot(gs[:54, 0])
    ax_txt = fig.add_subplot(gs[56:, 0])

    # 页面底色
    fig.patch.set_facecolor("#f2f4f7")

    # ---- 顶部：2D 投影 ----
    ax_view.set_title("", pad=0)
    frac = [s.frac_coords for s in structure.sites]
    species = [str(s.specie) for s in structure.sites]

    # 在 a-b 平面投影，考虑点重复到邻近晶胞，视觉更完整
    pts = []
    for (fx, fy, _fz), sp in zip(frac, species):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                x = (fx + dx) * a
                y = (fy + dy) * b
                # 适度扩大邻胞可视范围，确保在简单晶胞里也能看到周围环境原子
                if -0.55 * a <= x <= 1.55 * a and -0.55 * b <= y <= 1.55 * b:
                    pts.append((x, y, sp))

    # 背景邻胞（浅）
    for x, y, sp in pts:
        in_cell = (0.0 <= x <= a) and (0.0 <= y <= b)
        if in_cell:
            continue
        ax_view.scatter(x, y, s=800, c=[_species_color(sp)], alpha=0.20, edgecolors="none", linewidths=0.0, zorder=1)

    # 主晶胞点准备：对重叠点做微小偏移，避免“压成一个颜色”
    in_cell_pts = []
    for x, y, sp in pts:
        in_cell = (0.0 <= x <= a) and (0.0 <= y <= b)
        if in_cell:
            in_cell_pts.append((x, y, sp))

    # 按近似坐标分组，给重叠点分配微偏移
    grouped = {}
    for i, (x, y, sp) in enumerate(in_cell_pts):
        key = (round(x / max(a, 1e-8), 3), round(y / max(b, 1e-8), 3))
        grouped.setdefault(key, []).append((i, x, y, sp))

    jittered = [None] * len(in_cell_pts)
    for _k, arr in grouped.items():
        m = len(arr)
        if m <= 1:
            i0, x0, y0_, sp0 = arr[0]
            jittered[i0] = (x0, y0_, sp0)
            continue
        r = 0.018 * min(a, b)
        for j, (ii, xx, yy, spx) in enumerate(arr):
            ang = 2.0 * 3.141592653589793 * (j / m)
            dx = r * (1.0 if m == 2 and j == 0 else -1.0 if m == 2 and j == 1 else 1.0) * (0.9 if m == 2 else 1.0)
            if m > 2:
                dx = r * __import__("math").cos(ang)
                dy = r * __import__("math").sin(ang)
            else:
                dy = 0.0
            jittered[ii] = (xx + dx, yy + dy, spx)

    # 主晶胞（深）+ 高光（提升层次感）
    for x, y, sp in jittered:
        col = _species_color(sp)
        ax_view.scatter(x, y, s=860, c=[col], alpha=0.72, edgecolors="#a98f8f", linewidths=0.25, zorder=4)
        ax_view.scatter(x - 0.04 * a, y + 0.04 * b, s=180, c=["#ffffff"], alpha=0.18, edgecolors="none", zorder=5)

    # 晶胞框（细实线）
    ax_view.plot([0, a, a, 0, 0], [0, 0, b, b, 0], "-", color="#5f6368", lw=0.95, alpha=0.9, zorder=6)

    ax_view.set_xlim(-0.55 * a, 1.55 * a)
    ax_view.set_ylim(-0.55 * b, 1.55 * b)
    ax_view.set_aspect("equal", adjustable="box")
    ax_view.set_xticks([])
    ax_view.set_yticks([])
    ax_view.set_facecolor("#f7f8fb")
    for s in ax_view.spines.values():
        s.set_visible(False)

    # 元素图例（高对比）
    uniq_species = []
    for sp in species:
        if sp not in uniq_species:
            uniq_species.append(sp)
    if uniq_species:
        handles = [
            plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=_species_color(sp),
                       markeredgecolor="#666", markeredgewidth=0.4, markersize=6.8, label=sp)
            for sp in uniq_species[:4]
        ]
        ax_view.legend(handles=handles, loc="upper right", fontsize=8.3, frameon=False, ncol=min(2, len(handles)))

    # ---- 底部：基本信息 ----
    ax_txt.axis("off")
    # 标题增强：主标题 + 关键信息副标题
    ax_txt.text(0.02, 0.96, f"{title}", fontsize=15.6, fontweight="bold", va="top", ha="left", color="#1f2330")
    ax_txt.text(0.02, 0.82, f"Symmetry: {symmetry_text}", fontsize=10.4, va="top", ha="left", color="#4a5568")

    def _draw_info_box(x: float, y: float, w: float, h: float, label: str, value: str):
        rect = FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.006,rounding_size=0.02",
            linewidth=0.0,
            facecolor="#e8ecf3",
            alpha=1.0,
            transform=ax_txt.transAxes,
            zorder=1,
        )
        ax_txt.add_patch(rect)
        ax_txt.text(x + 0.02, y + h / 2, label, fontsize=10.2, fontweight="bold", va="center", ha="left", color="#586274")
        ax_txt.text(x + w - 0.02, y + h / 2, value, fontsize=10.6, va="center", ha="right", color="#2d3748")

    # 两列参数块
    row_h = 0.10
    col_w = 0.46
    y0 = 0.62
    _draw_info_box(0.02, y0, col_w, row_h, "a:", f"{a:.3f} Å")
    _draw_info_box(0.52, y0, col_w, row_h, "α:", f"{alpha:.3f}°")
    _draw_info_box(0.02, y0 - 0.13, col_w, row_h, "b:", f"{b:.3f} Å")
    _draw_info_box(0.52, y0 - 0.13, col_w, row_h, "β:", f"{beta:.3f}°")
    _draw_info_box(0.02, y0 - 0.26, col_w, row_h, "c:", f"{c:.3f} Å")
    _draw_info_box(0.52, y0 - 0.26, col_w, row_h, "γ:", f"{gamma:.3f}°")
    _draw_info_box(0.02, y0 - 0.41, 0.96, row_h, "Atoms:", f"{nsites}")

    # 底部提示文案移除（按产品要求）

    fig.tight_layout()
    # 固定画布尺寸导出，避免每张卡片被 tight 裁切成不同长度
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)

    return {
        "a": round(float(a), 6),
        "b": round(float(b), 6),
        "c": round(float(c), 6),
        "alpha": round(float(alpha), 6),
        "beta": round(float(beta), 6),
        "gamma": round(float(gamma), 6),
        "atoms": int(nsites),
    }


def compose_candidate_cards_image(
    card_paths: List[str],
    out_png: str,
    cols: int = 0,
    title: str = "",
    caption: str = "",
) -> Optional[str]:
    """将多张候选卡片横向优先拼成一张总图，返回输出路径。"""
    valid = [p for p in card_paths if p and os.path.exists(p)]
    if not valid:
        return None

    n = len(valid)
    # 默认横向单行；若显式传 cols>0 则使用该列数
    cols = n if int(cols or 0) <= 0 else max(1, int(cols))
    rows = (n + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(4.6 * cols, 7.6 * rows), dpi=160)
    if rows == 1 and cols == 1:
        axes = [[axes]]
    elif rows == 1:
        axes = [axes]
    elif cols == 1:
        axes = [[ax] for ax in axes]

    idx = 0
    for r in range(rows):
        for c in range(cols):
            ax = axes[r][c]
            ax.axis("off")
            if idx < n:
                img = plt.imread(valid[idx])
                ax.imshow(img)
            idx += 1

    fig.patch.set_facecolor("#f2f4f7")
    if title:
        fig.suptitle(title, fontsize=18, fontweight="bold", color="#1f3f75", y=0.995)
    if caption:
        fig.text(0.5, 0.012, caption, ha="center", va="bottom", fontsize=11, color="#4d5b6e")
    fig.tight_layout(pad=0.4)
    ensure_dir(os.path.dirname(out_png))
    fig.savefig(out_png, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_png


# -----------------------------
# MP fetch/query
# -----------------------------
def query_candidates(
    mpr: MPRester,
    formula: Optional[str],
    elements: Optional[List[str]],
    fields: List[str],
    fetch_n: int = 200,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    return: (query_mode, reduced_formula_target, docs_as_dict_list)
    """
    if not formula and not elements:
        raise ValueError("Need --formula or --elements.")

    if formula:
        # IMPORTANT: 如果用户误把 mp-id 传到了 formula，这里直接按 material_id 路径处理
        if is_mp_id(formula):
            mode = "material_id"
            rf = ""
            d = fetch_by_id(mpr, formula, fields=fields)
            docs = [d] if d else []
            return mode, rf, docs

        mode = "formula"
        rf = reduced_formula(formula)
        comp = Composition(formula)
        el_list = sorted([str(el) for el in comp.elements])
        chemsys = "-".join(sorted(set(el_list)))
    else:
        mode = "elements"
        el_list = sorted(set([e.strip() for e in (elements or []) if e.strip()]))
        if not el_list:
            raise ValueError("Empty elements list.")
        chemsys = "-".join(el_list)
        rf = ""  # unknown if only elements

    effective_fetch_n = max(20, min(int(fetch_n or 100), 20))
    print(
        f"[MP_QUERY] mode={mode} formula={formula} chemsys={chemsys} "
        f"fields_count={len(fields or [])} fetch_n={effective_fetch_n}",
        flush=True,
    )

    last_err: Optional[Exception] = None
    docs = []
    for attempt in range(1, 5):
        t0 = time.perf_counter()
        try:
            docs = mpr.materials.summary.search(
                chemsys=chemsys,
                fields=fields,
                num_chunks=1,
                chunk_size=effective_fetch_n,
            )
            docs = [to_dict(d) for d in list(docs)]
            dt = time.perf_counter() - t0
            print(
                f"[MP_QUERY] summary.search ok attempt={attempt} chemsys={chemsys} "
                f"count={len(docs)} elapsed={dt:.2f}s",
                flush=True,
            )
            break
        except Exception as e:
            dt = time.perf_counter() - t0
            last_err = e
            print(
                f"[MP_QUERY] summary.search failed attempt={attempt} chemsys={chemsys} "
                f"elapsed={dt:.2f}s err={type(e).__name__}: {e}",
                flush=True,
            )
            if attempt >= 4:
                raise
            sleep_s = min(8, 2 ** (attempt - 1))
            time.sleep(sleep_s)
    if not docs and last_err is not None:
        raise last_err

    if mode == "formula":
        filtered = []
        for d in docs:
            pretty = d.get("formula_pretty")
            if not pretty:
                continue
            try:
                if reduced_formula(pretty) == rf:
                    filtered.append(d)
            except Exception:
                continue
        docs = filtered

    return mode, rf, docs


def fetch_by_id(mpr: MPRester, material_id: str, fields: List[str]) -> Optional[Dict[str, Any]]:
    print(f"[MP_FETCH_BY_ID] material_id={material_id}", flush=True)
    last_err: Optional[Exception] = None
    docs = []
    for attempt in range(1, 4):
        t0 = time.perf_counter()
        try:
            docs = mpr.materials.summary.search(
                material_ids=[material_id],
                fields=fields,
                num_chunks=1,
                chunk_size=1,
            )
            docs = list(docs)
            dt = time.perf_counter() - t0
            print(f"[MP_FETCH_BY_ID] ok attempt={attempt} elapsed={dt:.2f}s", flush=True)
            break
        except Exception as e:
            dt = time.perf_counter() - t0
            last_err = e
            print(f"[MP_FETCH_BY_ID] failed attempt={attempt} elapsed={dt:.2f}s err={type(e).__name__}: {e}", flush=True)
            if attempt >= 3:
                raise
            time.sleep(2 ** (attempt - 1))
    if not docs and last_err is not None:
        raise last_err
    if not docs:
        return None
    return to_dict(docs[0])


def fetch_structure(mpr: MPRester, material_id: str) -> Structure:
    print(f"[MP_FETCH_STRUCTURE] material_id={material_id}", flush=True)
    last_err: Optional[Exception] = None
    docs = []
    for attempt in range(1, 4):
        t0 = time.perf_counter()
        try:
            docs = mpr.materials.summary.search(
                material_ids=[material_id],
                fields=["material_id", "structure"],
                num_chunks=1,
                chunk_size=1,
            )
            docs = list(docs)
            dt = time.perf_counter() - t0
            print(f"[MP_FETCH_STRUCTURE] ok attempt={attempt} elapsed={dt:.2f}s", flush=True)
            break
        except Exception as e:
            dt = time.perf_counter() - t0
            last_err = e
            print(f"[MP_FETCH_STRUCTURE] failed attempt={attempt} elapsed={dt:.2f}s err={type(e).__name__}: {e}", flush=True)
            if attempt >= 3:
                raise
            time.sleep(2 ** (attempt - 1))
    if not docs and last_err is not None:
        raise last_err
    if not docs:
        raise RuntimeError(f"material_id not found: {material_id}")

    d0 = docs[0]
    dd = to_dict(d0)
    s = dd.get("structure")
    if isinstance(s, dict):
        return Structure.from_dict(s)
    if isinstance(s, Structure):
        return s
    if hasattr(d0, "structure") and getattr(d0, "structure") is not None:
        return getattr(d0, "structure")
    raise RuntimeError(f"Cannot extract structure for: {material_id}")


# -----------------------------
# Selection logic
# -----------------------------
def load_whitelist(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def whitelist_ids_for_formula(whitelist: Dict[str, Any], formula: str) -> List[str]:
    """
    whitelist json supports:
      { "Li6PS5Cl": {"primary": [...], "keep": [...]} }
    """
    if not whitelist or not formula:
        return []
    cfg = whitelist.get(formula) or whitelist.get(reduced_formula(formula)) or {}
    ids: List[str] = []
    if isinstance(cfg, dict):
        for k in ["primary", "keep", "must_include"]:
            v = cfg.get(k)
            if isinstance(v, list):
                ids.extend([str(x).strip() for x in v if str(x).strip()])
            elif isinstance(v, str) and v.strip():
                ids.append(v.strip())
    return sorted(set(ids))


def choose_primary_id(whitelist: Dict[str, Any], formula: str, selected: List[Dict[str, Any]]) -> Optional[str]:
    """
    规则：
      1) 如果 whitelist 里有 primary 列表，优先在 selected 中找命中的那条，返回它的 material_id
      2) 否则返回 selected[0].material_id
    """
    if not selected:
        return None

    cfg = whitelist.get(formula) or whitelist.get(reduced_formula(formula)) or {}
    primary_ids: List[str] = []
    if isinstance(cfg, dict):
        v = cfg.get("primary")
        if isinstance(v, list):
            primary_ids = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            primary_ids = [v.strip()]

    if primary_ids:
        for it in selected:
            if match_any_id(it, primary_ids):
                return str(it.get("material_id"))
    return str(selected[0].get("material_id"))


def select_top_k(
    docs: List[Dict[str, Any]],
    k: int,
    prefer_stable: bool,
    must_include_ids: List[str],
    mpr: Optional[MPRester],
    fields: List[str],
) -> List[Dict[str, Any]]:
    """
    先把 docs 转成 summary_item
    - 强制把 must_include_ids 命中的条目标记 reason=whitelist
    - 如果 must_include_ids 在 docs 里找不到，而 mpr 不为空，则用 id 单独 fetch 补齐
    - 再按排序挑满 k
    """
    items = [summary_item(d) for d in docs if d]

    # 标记白名单命中
    for it in items:
        if match_any_id(it, must_include_ids):
            it["selected_reason"] = "whitelist"

    # 补齐白名单（如果没命中）
    if mpr and must_include_ids:
        have = set()
        for it in items:
            for i in (it.get("all_ids") or []):
                have.add(str(i))
        for wid in must_include_ids:
            wid = str(wid).strip()
            if not wid or wid in have:
                continue
            extra = fetch_by_id(mpr, wid, fields=fields)
            if extra:
                eit = summary_item(extra)
                eit["selected_reason"] = "whitelist_fetched"
                items.append(eit)

    # 去重：按 material_id 去重
    uniq: Dict[str, Dict[str, Any]] = {}
    for it in items:
        mid = str(it.get("material_id") or "").strip()
        if not mid:
            continue
        if mid not in uniq:
            uniq[mid] = it
        else:
            r0 = uniq[mid].get("selected_reason", "")
            r1 = it.get("selected_reason", "")
            if "whitelist" in (r1 or "") and "whitelist" not in (r0 or ""):
                uniq[mid]["selected_reason"] = r1

    items = list(uniq.values())

    # 排序
    items_sorted = sorted(items, key=lambda x: sort_key(x, prefer_stable=prefer_stable))

    # whitelist 置前
    wl = [it for it in items_sorted if "whitelist" in (it.get("selected_reason") or "")]
    non = [it for it in items_sorted if it not in wl]

    wl = sorted(wl, key=lambda x: sort_key(x, prefer_stable=prefer_stable))

    selected: List[Dict[str, Any]] = []
    for it in wl:
        it["selected_reason"] = it.get("selected_reason") or "whitelist"
        selected.append(it)

    for it in non:
        if len(selected) >= k:
            break
        if not it.get("selected_reason"):
            it["selected_reason"] = "ranked_topk"
        selected.append(it)

    return selected[:k]


# -----------------------------
# CLI
# -----------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="MP export: query candidates -> select <=K -> summary.md -> render primary glb.")
    ap.add_argument("--taskid", required=True, help="Alpha request_id/taskid")
    ap.add_argument("--jobid", required=True, help="Job label (e.g. Li6PS5Cl)")
    ap.add_argument("--formula", default=None, help="e.g. Li6PS5Cl (NOTE: mp-id will be treated as material-id)")
    ap.add_argument("--elements", nargs="*", default=None, help="e.g. Li P S Cl")
    ap.add_argument("--material-id", default=None, help="e.g. mp-1234 (direct fetch)")

    ap.add_argument("--prefer-stable", action="store_true", help="Stable first in ranking")
    ap.add_argument("--max-candidates", type=int, default=5, help="Max structures shown in markdown table")
    ap.add_argument("--fields", nargs="*", default=DEFAULT_FIELDS)

    ap.add_argument("--whitelist", default=None, help="Path to whitelist json (force-keep IDs)")

    ap.add_argument("--render-poly-mode", default="p", choices=["mp", "p", "li", "none"])
    ap.add_argument("--render-poly-cn", default="4", help="e.g. 4 or 4,6")
    ap.add_argument("--render-poly-alpha", type=int, default=85)

    ap.add_argument("--supercell", nargs=3, type=int, default=[1, 1, 1])
    ap.add_argument("--atom-radius", type=float, default=0.40)
    ap.add_argument("--bond-radius", type=float, default=0.07)
    ap.add_argument("--max-bonds-per-site", type=int, default=None)

    # relative paths: default False; pass flag to enable
    ap.add_argument(
        "--relative-paths",
        action="store_true",
        default=False,
        help="Write manifest.files as relative paths to out_dir. Default: False (absolute).",
    )

    args = ap.parse_args()

    # normalize input priority
    material_id = args.material_id
    if not material_id and args.formula and is_mp_id(args.formula):
        material_id = args.formula
        args.formula = None

    if not material_id and not args.formula and not args.elements:
        raise RuntimeError("Need --material-id or --formula or --elements.")

    api_key = load_key()
    out_dir = job_dir(args.taskid, args.jobid)

    whitelist = load_whitelist(args.whitelist)
    must_ids = []
    if args.formula:
        must_ids = whitelist_ids_for_formula(whitelist, args.formula or args.jobid)

    with MPRester(api_key) as mpr:
        if material_id:
            # direct fetch
            d = fetch_by_id(mpr, material_id, fields=args.fields)
            if not d:
                raise RuntimeError(f"material_id not found: {material_id}")
            query_mode, rf, docs = ("material_id", "", [d])
        else:
            query_mode, rf, docs = query_candidates(
                mpr=mpr,
                formula=args.formula,
                elements=args.elements,
                fields=args.fields,
                fetch_n=max(args.max_candidates * 20, 100),
            )

        # 全量候选（debug）
        all_items = [summary_item(d) for d in docs if d]
        write_json(os.path.join(out_dir, "summary_all.json"), {
            "ok": True,
            "query_mode": query_mode,
            "input": {"material_id": material_id, "formula": args.formula, "elements": args.elements, "reduced_formula": rf},
            "count_all": len(all_items),
            "items": all_items,
        })

        # 选 <=K（含 whitelist 强制保留；material_id 模式下也照样走）
        selected = select_top_k(
            docs=docs,
            k=int(args.max_candidates),
            prefer_stable=bool(args.prefer_stable),
            must_include_ids=must_ids,
            mpr=mpr,
            fields=args.fields,
        )

        # primary：material_id 模式直接用它；否则按 whitelist/排名选
        if material_id:
            primary_mid = material_id
        else:
            primary_mid = choose_primary_id(whitelist, args.formula or args.jobid, selected)

        if not primary_mid:
            raise RuntimeError("No primary material_id selected.")

        # 为每个候选抓取结构，供后续逐候选性质评估使用
        selected_structures: Dict[str, Structure] = {}
        for it in selected:
            mid = str(it.get("material_id") or "").strip()
            if not mid:
                continue
            try:
                selected_structures[mid] = fetch_structure(mpr, mid)
            except Exception:
                continue

        # primary 结构兜底
        structure = selected_structures.get(primary_mid)
        if structure is None:
            structure = fetch_structure(mpr, primary_mid)

    # 为每个候选写独立 CIF，并回填到 items
    structures_dir = ensure_dir(os.path.join(out_dir, "structures"))
    for it in selected:
        mid = str(it.get("material_id") or "").strip()
        if not mid:
            continue
        s = selected_structures.get(mid)
        if s is None:
            continue
        cif_item_abs = os.path.join(structures_dir, f"{safe_fs(mid)}.cif")
        write_text(cif_item_abs, s.to(fmt="cif"))
        it["cif_path"] = os.path.relpath(cif_item_abs, out_dir) if args.relative_paths else cif_item_abs
        it["cif_path_abs"] = cif_item_abs

    # 生成候选二维卡片
    cards_dir = ensure_dir(os.path.join(out_dir, "cards"))
    candidate_cards: List[Dict[str, Any]] = []
    card_png_abs_list: List[str] = []
    for i, it in enumerate(selected, start=1):
        mid = str(it.get("material_id") or "").strip()
        if not mid:
            continue
        s = selected_structures.get(mid)
        if s is None:
            continue
        sym_text = pick_symmetry_str(it.get("symmetry"))
        title = f"Candidate {i} ({mid})"
        card_png_abs = os.path.join(cards_dir, f"{safe_fs(mid)}_card.png")
        try:
            lattice_info = render_candidate_ortho_card(
                structure=s,
                out_png=card_png_abs,
                title=title,
                material_id=mid,
                symmetry_text=sym_text,
            )
            candidate_cards.append({
                "material_id": mid,
                "formula_pretty": str(it.get("formula_pretty") or ""),
                "symmetry": sym_text,
                "image_path": os.path.relpath(card_png_abs, out_dir) if args.relative_paths else card_png_abs,
                "image_path_abs": card_png_abs,
                "lattice": lattice_info,
            })
            card_png_abs_list.append(card_png_abs)
        except Exception:
            continue

    # 候选卡片拼图（单张总图，横向优先）
    combined_cards_abs = os.path.join(out_dir, "candidate_cards_combined.png")
    # 前端标题请通过 manifest.images 的 name/docs 控制，避免图片内文案乱码影响观感
    combo_title = ""
    combo_caption = ""
    combined_cards_abs = compose_candidate_cards_image(
        card_png_abs_list,
        combined_cards_abs,
        cols=0,
        title=combo_title,
        caption=combo_caption,
    )

    # 写 selected + markdown
    write_json(os.path.join(out_dir, "selected_structures.json"), {
        "ok": True,
        "taskid": args.taskid,
        "jobid": args.jobid,
        "material_id": material_id,
        "formula": args.formula,
        "elements": args.elements,
        "max_candidates": args.max_candidates,
        "prefer_stable": bool(args.prefer_stable),
        "whitelist_ids": must_ids,
        "primary_material_id": primary_mid,
        "count_selected": len(selected),
        "items": selected,
        "candidate_cards": candidate_cards,
    })

    md = md_table(selected, title=f"{args.jobid} (selected candidates <= {args.max_candidates})")
    write_text(os.path.join(out_dir, "summary.md"), md)

    # 写 structure
    cif_path = os.path.join(out_dir, "structure.cif")
    write_text(cif_path, structure.to(fmt="cif"))

    sjson_path = os.path.join(out_dir, "structure.json")
    write_json(sjson_path, structure.as_dict())

    # 渲染 glb
    glb_path = os.path.join(out_dir, "structure_mpstyle.glb")

    def parse_int_set(s: str) -> List[int]:
        parts = [p.strip() for p in (s or "").replace(";", ",").split(",") if p.strip()]
        return [int(p) for p in parts]

    poly_cn = set(parse_int_set(args.render_poly_cn)) if args.render_poly_cn else set()

    glb_info = export_glb_mpstyle(
        structure=structure,
        out_glb=glb_path,
        supercell=tuple(int(x) for x in args.supercell),
        atom_radius=float(args.atom_radius),
        bond_radius=float(args.bond_radius),
        max_bonds_per_site=args.max_bonds_per_site,
        poly_mode=str(args.render_poly_mode),
        poly_cn=poly_cn if poly_cn else set([4]),
        poly_alpha=int(args.render_poly_alpha),
    )

    # path helpers
    def rel_or_abs(p: str) -> str:
        return os.path.relpath(p, out_dir) if args.relative_paths else p

    files_abs = {
        "summary_all_json": os.path.join(out_dir, "summary_all.json"),
        "selected_structures_json": os.path.join(out_dir, "selected_structures.json"),
        "summary_md": os.path.join(out_dir, "summary.md"),
        "cards_dir": cards_dir,
        "candidate_cards_combined_png": combined_cards_abs or "",
        "structures_dir": structures_dir,
        "structure_cif": cif_path,
        "structure_json": sjson_path,
        "structure_glb": glb_path,
        "manifest_json": os.path.join(out_dir, "manifest.json"),
    }

    # manifest
    manifest = {
        "ok": True,
        "taskid": args.taskid,
        "jobid": args.jobid,
        "material_id": material_id,
        "formula": args.formula,
        "elements": args.elements,
        "query": {
            "query_mode": query_mode,
            "prefer_stable": bool(args.prefer_stable),
            "max_candidates": int(args.max_candidates),
            "whitelist": args.whitelist,
            "whitelist_ids": must_ids,
            "primary_material_id": primary_mid,
        },
        "render": {
            "supercell": list(args.supercell),
            "atom_radius": float(args.atom_radius),
            "bond_radius": float(args.bond_radius),
            "max_bonds_per_site": args.max_bonds_per_site,
            "poly_mode": str(args.render_poly_mode),
            "poly_cn": sorted(list(poly_cn if poly_cn else set([4]))),
            "poly_alpha": int(args.render_poly_alpha),
        },
        "base_dir": out_dir,
        "base_dir_rel_to_repo": os.path.relpath(out_dir, repo_root()),
        "files": {k: rel_or_abs(v) for k, v in files_abs.items()},
        "files_abs": files_abs,
        "candidate_cards": candidate_cards,
        "candidate_cards_combined": rel_or_abs(combined_cards_abs) if combined_cards_abs else "",
        "images": ([{
            "name": f"{str(args.jobid or args.formula or 'Material')}_无机化合物可能候选结构",
            "docs": f"{str(args.jobid or args.formula or 'Material')}_无机化合物可能候选结构（按空间群区分）",
            "path": rel_or_abs(combined_cards_abs),
        }] if combined_cards_abs else []),
        "glb_info": glb_info,
        "generated_at": datetime.now().isoformat(),
    }

    manifest_path = os.path.join(out_dir, "manifest.json")
    write_json(manifest_path, manifest)

    # stdout only manifest json
    sys.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stdout.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2) + "\n")
        sys.exit(1)
