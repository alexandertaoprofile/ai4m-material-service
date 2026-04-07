# src/MNS_CaseHub/cases/material_discovery_demo/pipeline.py
# -*- coding: utf-8 -*-

import os
import re
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[3]  # .../src/MNS_CaseHub/cases/material_discovery_demo -> repo
TOOLS_DIR = REPO_ROOT / "tools"
MP_EXPORT = TOOLS_DIR / "mp_export_assets.py"

# 你机器上的 python 位置（按你给的）
SERVICE_PY = "/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python"
MPAPI_PY = "/home/ubuntu/.local/share/mamba/envs/mp-api-py311/bin/python"

# 如果你希望统一走 mp-api 环境跑 mp_export_assets.py，就把这个置 True
# （前提：mp-api env 里也装了 export_glb_mpstyle 需要的依赖）
DEFAULT_USE_MPAPI_PY = False


# ----------------------------
# Parsing
# ----------------------------
_MP_ID_RE = re.compile(r"\bmp-[a-z0-9]+\b", re.IGNORECASE)
# 很宽松的化学式：Li6PS5Cl / Li3PS4 / Na3PS4 等
_FORMULA_RE = re.compile(r"\b([A-Z][a-z]?\d*){2,}\b")
# 元素列表：Li P S Cl 这种
_ELEMENTS_TOKEN_RE = re.compile(r"^[A-Z][a-z]?$")


@dataclass
class ParsedQuery:
    material_id: Optional[str] = None
    formula: Optional[str] = None
    elements: Optional[List[str]] = None


def parse_idea_to_query(idea: str) -> ParsedQuery:
    s = (idea or "").strip()
    if not s:
        return ParsedQuery()

    # 1) mp-id 优先
    m = _MP_ID_RE.search(s)
    if m:
        return ParsedQuery(material_id=m.group(0))

    # 2) 看看有没有显式的 elements 列表（用空格分词）
    toks = [t for t in re.split(r"[\s,;，；]+", s) if t]
    elem_toks = [t for t in toks if _ELEMENTS_TOKEN_RE.match(t)]
    # 如果元素 token 占比够高，认为是 elements 模式
    if len(elem_toks) >= 2 and len(elem_toks) >= max(2, int(0.6 * len(toks))):
        # 去重保持顺序
        seen = set()
        out = []
        for e in elem_toks:
            e = e.strip()
            if e and e not in seen:
                seen.add(e)
                out.append(e)
        return ParsedQuery(elements=out)

    # 3) 抓一个最像化学式的 token
    # 这里选“最长的一个”，避免把普通英文单词误判
    cands = _FORMULA_RE.findall(s)
    if cands:
        # findall 对这种 regex 可能返回子组，不稳定；因此我们重新用 finditer
        matches = [m.group(0) for m in _FORMULA_RE.finditer(s)]
        matches = sorted(matches, key=len, reverse=True)
        return ParsedQuery(formula=matches[0])

    return ParsedQuery()


# ----------------------------
# Subprocess helpers
# ----------------------------
def _run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 600) -> Tuple[int, str, str]:
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    out, err = p.communicate(timeout=timeout)
    return p.returncode, out, err


def _pick_python(use_mpapi: bool) -> str:
    if use_mpapi and os.path.exists(MPAPI_PY):
        return MPAPI_PY
    if os.path.exists(SERVICE_PY):
        return SERVICE_PY
    # fallback
    return "python"


# ----------------------------
# Main pipeline
# ----------------------------
def run_mp_export_assets(
    *,
    taskid: str,
    jobid: str,
    formula: Optional[str],
    elements: Optional[List[str]],
    prefer_stable: bool = True,
    max_candidates: int = 5,
    whitelist_path: Optional[str] = None,
    use_mpapi_python: bool = DEFAULT_USE_MPAPI_PY,
) -> Dict[str, Any]:
    """
    调 tools/mp_export_assets.py，返回 manifest dict（脚本 stdout 输出的 JSON）。
    """
    py = _pick_python(use_mpapi_python)

    cmd = [py, str(MP_EXPORT), "--taskid", taskid, "--jobid", jobid, "--max-candidates", str(max_candidates)]
    if prefer_stable:
        cmd.append("--prefer-stable")
    if whitelist_path:
        cmd += ["--whitelist", whitelist_path]

    if formula:
        cmd += ["--formula", formula]
    elif elements:
        cmd += ["--elements"] + list(elements)
    else:
        raise RuntimeError("Need formula or elements for mp_export_assets.")

    # 你的 mp_export_assets.py 默认 relative-paths=True（我上次给你那版）
    # 这里不额外加参数也可以。如果你以后改成默认 False，再在这儿显式加：
    # cmd.append("--relative-paths")

    code, out, err = _run_cmd(cmd, cwd=str(REPO_ROOT), timeout=1200)
    if code != 0:
        raise RuntimeError(f"mp_export_assets failed (code={code}).\nSTDERR:\n{err}\nSTDOUT:\n{out}")

    try:
        manifest = json.loads(out)
    except Exception as e:
        raise RuntimeError(f"mp_export_assets output is not JSON: {e}\nSTDOUT:\n{out}\nSTDERR:\n{err}")

    if not manifest.get("ok", False):
        raise RuntimeError(f"mp_export_assets returned ok=false: {manifest}")

    return manifest


def build_frontend_table(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从 selected_structures.json 里抽一个前端表格数组（你前端的表格就吃这个）。
    """
    base_dir = manifest.get("base_dir")
    sel_rel = manifest.get("files", {}).get("selected_structures_json")
    if not base_dir or not sel_rel:
        return []

    sel_path = Path(base_dir) / sel_rel
    if not sel_path.exists():
        return []

    data = json.loads(sel_path.read_text(encoding="utf-8"))
    items = data.get("items") or []

    table = []
    for it in items:
        # MP 通常 energy_above_hull 是 eV/atom，你前端表格用 meV/atom 就乘 1000
        eh = it.get("energy_above_hull")
        eh_mev = None
        try:
            if eh is not None:
                eh_mev = float(eh) * 1000.0
        except Exception:
            eh_mev = None

        table.append({
            "material": it.get("formula_pretty") or manifest.get("formula") or manifest.get("jobid"),
            "mp_id": it.get("material_id"),
            "ehull_mev_atom": eh_mev,
            "band_gap_ev": it.get("band_gap"),
            "is_stable": it.get("is_stable"),
            "selected_reason": it.get("selected_reason", ""),
            "symmetry": it.get("symmetry"),
            "nsites": it.get("nsites"),
            "density": it.get("density"),
        })
    return table


def run_material_discovery(
    *,
    idea: str,
    taskid: str,
    user_name: str = "",
    file_metadata: Optional[List[Dict[str, Any]]] = None,
    max_candidates: int = 5,
    prefer_stable: bool = True,
    whitelist_path: Optional[str] = None,
    use_mpapi_python: bool = DEFAULT_USE_MPAPI_PY,
) -> Dict[str, Any]:
    """
    对外主函数：给 team_config 的 todo/agent 调用。
    返回：一个“前端友好”的结果包，包含 manifest + table + glb 相对路径等。
    """
    q = parse_idea_to_query(idea)
    file_metadata = file_metadata or []

    # jobid：优先 formula；否则用 elements 拼；否则 mp-id
    if q.formula:
        jobid = q.formula
    elif q.elements:
        jobid = "-".join(q.elements)
    elif q.material_id:
        jobid = q.material_id
    else:
        jobid = "query"

    # 目前你的 mp_export_assets.py 还没实现 material_id 模式（只 formula/elements）
    # 所以如果用户直接给 mp-xxx，我们先把它当 jobid，同时把 formula/elements 留空会报错
    # 👉 最稳：你先告诉前端/用户“请给化学式或元素”，或者我们下一步把 mp_export_assets.py 加 material-id 分支
    if q.material_id and (not q.formula and not q.elements):
        return {
            "ok": False,
            "error": "Detected MP material_id, but exporter currently requires --formula or --elements. Please provide a formula like Li6PS5Cl, or elements like 'Li P S Cl'.",
            "taskid": taskid,
            "idea": idea,
        }

    manifest = run_mp_export_assets(
        taskid=taskid,
        jobid=jobid,
        formula=q.formula,
        elements=q.elements,
        prefer_stable=prefer_stable,
        max_candidates=max_candidates,
        whitelist_path=whitelist_path,
        use_mpapi_python=use_mpapi_python,
    )

    table = build_frontend_table(manifest)

    # glb 路径（相对 out_dir）
    glb_rel = (manifest.get("files") or {}).get("structure_glb")

    return {
        "ok": True,
        "taskid": taskid,
        "user_name": user_name,
        "idea": idea,
        "query_parsed": {
            "material_id": q.material_id,
            "formula": q.formula,
            "elements": q.elements,
        },
        "manifest": manifest,
        "glb_relpath": glb_rel,
        "table": table,
    }
