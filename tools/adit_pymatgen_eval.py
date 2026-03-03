#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
from datetime import datetime


def _read_json(p: str):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(p: str, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _write_text(p: str, s: str):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


# =========================
# 中文字段名映射
# =========================
KEY_ZH = {
    "num_sites": "原子位点数",
    "charge": "结构净电荷",
    "volume": "体积(Å³)",
    "density": "密度(g/cm³)",
    "min_interatomic_dist": "最小原子间距(Å)",
    "spacegroup": "空间群",
    "atomic_numbers": "原子序数集合",
    "has_partial_occupancy": "是否部分占位",
    "supported_elements": "元素是否在支持范围",
    "pass_gate": "是否通过门槛",
    "gate_reason": "门槛原因",
}

METRIC_EXPLAIN_ZH = {
    "num_sites": "单胞内原子位点总数",
    "charge": "结构整体净电荷（若可得）",
    "volume": "晶胞体积，用于计算密度等 sanity 指标",
    "density": "质量密度（g/cm³）",
    "min_interatomic_dist": "最短原子间距；过小通常提示结构不合理/原子过近",
    "spacegroup": "空间群（符号 + 编号）用于对称性分类",
    "atomic_numbers": "元素原子序数集合（用于支持范围判定）",
    "has_partial_occupancy": "是否存在部分占位/无序（常影响下游假设）",
    "supported_elements": "是否超出支持元素范围（当前用 Z<=96 规则）",
    "pass_gate": "是否通过轻量准入门槛",
    "gate_reason": "未通过门槛时的原因标签",
}


def _zh_key(k: str) -> str:
    return KEY_ZH.get(k, k)


def _zh_explain(k: str) -> str:
    return METRIC_EXPLAIN_ZH.get(k, "")


def _fmt_value(v):
    if v is None:
        return "待计算"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        # 控制长度：避免在窄表格里被拆开
        # 你可以把 4 改成 3 更短
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        # 让它更短：原子序数集合最多显示前 6 个
        s = ", ".join(str(x) for x in v[:6])
        if len(v) > 6:
            s += f", ... (+{len(v)-6})"
        return s
    return str(v)


def pymatgen_checks(structure_cif: str):
    out = {"ok": False, "errors": [], "warnings": [], "metrics": {}}

    try:
        from pymatgen.core import Structure
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    except Exception as e:
        out["errors"].append(f"pymatgen import failed: {e}")
        return out

    if not structure_cif or not os.path.exists(structure_cif):
        out["errors"].append(f"structure_cif not found: {structure_cif}")
        return out

    try:
        s = Structure.from_file(structure_cif)
    except Exception as e:
        out["errors"].append(f"Structure.from_file failed: {e}")
        return out

    # --- 核心指标（按展示顺序写入） ---
    out["metrics"]["num_sites"] = int(len(s))
    out["metrics"]["charge"] = _safe_float(getattr(s, "charge", None))
    out["metrics"]["volume"] = _safe_float(getattr(s.lattice, "volume", None))
    try:
        out["metrics"]["density"] = _safe_float(s.density)
    except Exception:
        out["metrics"]["density"] = None

    # --- min_dist ---
    try:
        dm = s.distance_matrix
        min_dist = float(dm[dm > 1e-8].min())
        out["metrics"]["min_interatomic_dist"] = min_dist
        if min_dist < 0.6:
            out["warnings"].append(f"最小原子间距过小（{min_dist:.3f} Å）：可能存在原子重叠/结构无效。")
        elif min_dist < 1.0:
            out["warnings"].append(f"最小原子间距偏小（{min_dist:.3f} Å）：建议复核结构合理性。")
    except Exception as e:
        out["warnings"].append(f"最小距离检查失败：{e}")

    # --- spacegroup（合并成一个短字段，避免蛇形英文挤爆表头） ---
    try:
        sga = SpacegroupAnalyzer(s, symprec=0.1)
        sym = sga.get_space_group_symbol()
        num = int(sga.get_space_group_number())
        out["metrics"]["spacegroup"] = f"{sym} ({num})"
    except Exception as e:
        out["warnings"].append(f"空间群分析失败：{e}")
        out["metrics"]["spacegroup"] = None

    out["ok"] = True if not out["errors"] else False
    return out


def adit_legality_checks(structure_cif: str):
    out = {"ok": False, "errors": [], "warnings": [], "metrics": {}}

    try:
        from pymatgen.core import Structure
    except Exception as e:
        out["errors"].append(f"pymatgen import failed (for adit check): {e}")
        return out

    if not structure_cif or not os.path.exists(structure_cif):
        out["errors"].append(f"structure_cif not found: {structure_cif}")
        return out

    try:
        s = Structure.from_file(structure_cif)
    except Exception as e:
        out["errors"].append(f"Structure.from_file failed: {e}")
        return out

    # --- atomic_numbers ---
    try:
        zs = sorted({int(sp.Z) for sp in s.species})
        out["metrics"]["atomic_numbers"] = zs
    except Exception as e:
        out["warnings"].append(f"原子序数统计失败：{e}")
        out["metrics"]["atomic_numbers"] = []

    # --- occupancy ---
    is_ordered = bool(s.is_ordered)
    out["metrics"]["has_partial_occupancy"] = (not is_ordered)
    if not is_ordered:
        out["warnings"].append("检测到部分占位/无序结构：许多管线默认有序结构，可能影响可用性。")

    # --- supported elements ---
    supported_elements = True
    try:
        if out["metrics"].get("atomic_numbers"):
            max_z = max(out["metrics"]["atomic_numbers"])
            if max_z > 96:
                supported_elements = False
                out["warnings"].append(f"包含原子序数 Z>96 的元素（maxZ={max_z}），可能超出支持范围。")
    except Exception as e:
        out["warnings"].append(f"元素支持范围检查失败：{e}")
    out["metrics"]["supported_elements"] = supported_elements

    # --- min distance ---
    min_dist = None
    try:
        import numpy as np
        dm = np.array(s.distance_matrix, dtype=float)
        np.fill_diagonal(dm, 1e9)
        min_dist = float(dm.min())
        # 放在 ADiT 表里也展示一下（你想更短的话这里也可以去掉）
        out["metrics"]["min_interatomic_dist"] = min_dist
        if min_dist < 1.2:
            out["warnings"].append(f"最小原子间距过小（{min_dist:.3f} Å）：结构可能不合理。")
    except Exception as e:
        out["warnings"].append(f"最小原子间距计算失败：{e}")

    # --- gate decision ---
    pass_gate = True
    gate_reason = []

    if not is_ordered:
        pass_gate = False
        gate_reason.append("partial_occupancy")
    if not supported_elements:
        pass_gate = False
        gate_reason.append("unsupported_elements")
    if min_dist is not None and min_dist < 1.2:
        pass_gate = False
        gate_reason.append("min_dist_too_small")

    out["metrics"]["pass_gate"] = bool(pass_gate)
    out["metrics"]["gate_reason"] = ",".join(gate_reason) if gate_reason else "basic_checks_passed"

    out["ok"] = True if not out["errors"] else False
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp_manifest", required=True, help="path to mp manifest.json")
    ap.add_argument("--taskid", required=False, default="", help="override taskid (optional)")
    ap.add_argument("--jobid", required=False, default="", help="override jobid (optional)")
    ap.add_argument("--out_root", required=False, default="", help="output root dir (optional)")
    args = ap.parse_args()

    mp_manifest = os.path.abspath(args.mp_manifest)
    mp = _read_json(mp_manifest)

    taskid = args.taskid or mp.get("taskid") or "unknown_task"
    jobid = args.jobid or mp.get("jobid") or mp.get("formula") or "unknown_job"

    files = (mp.get("files_abs") or mp.get("files") or {})
    structure_cif = files.get("structure_cif") or ""

    if args.out_root:
        base_dir = os.path.abspath(args.out_root)
    else:
        mp_manifest_dir = os.path.dirname(os.path.abspath(mp_manifest))
        case_results = os.path.abspath(os.path.join(mp_manifest_dir, "..", "..", ".."))
        base_dir = os.path.join(
            case_results,
            "adit_pymatgen",
            str(taskid).replace("/", "_"),
            str(jobid),
        )

    os.makedirs(base_dir, exist_ok=True)

    pmg = pymatgen_checks(structure_cif)
    adit = adit_legality_checks(structure_cif)

    # ----------------------------
    # 写 summary.md（纵向 KV，避免窄屏竖排）
    # ----------------------------
    def _md_kv_table(title: str, kv: dict) -> list:
        out = []
        out.append(f"### {title}")
        out.append("")
        if not kv:
            out.append("（无）")
            out.append("")
            return out
        out.append("| 指标 | 数值 | 含义/关注点 |")
        out.append("|---|---:|---|")
        for k, v in kv.items():
            out.append(f"| {_zh_key(str(k))} | {_fmt_value(v)} | {_zh_explain(str(k))} |")
        out.append("")
        return out

    def _md_list_block(title: str, items: list, prefix: str) -> list:
        out = []
        if items:
            out.append(f"### {title}")
            out.append("")
            for x in items:
                out.append(f"- {prefix} {x}")
            out.append("")
        return out

    lines = []
    lines.append("### 稳定性评估（ADiT + Pymatgen）")
    lines.append("")
    lines.append(f"- taskid: `{taskid}`")
    lines.append(f"- jobid: `{jobid}`")
    lines.append(f"- generated_at: `{datetime.utcnow().isoformat()}Z`")
    lines.append("")

    lines.append("### Pymatgen 快速体检")
    lines.append("")
    lines.extend(_md_kv_table("核心指标", pmg.get("metrics") or {}))
    lines.extend(_md_list_block("Warnings（警告）", pmg.get("warnings") or []))
    lines.extend(_md_list_block("Errors（错误）", pmg.get("errors") or []))

    lines.append("### ADiT 合法性检查（轻量版本）")
    lines.append("")
    lines.extend(_md_kv_table("核心指标", adit.get("metrics") or {}))

    pass_gate = bool((adit.get("metrics") or {}).get("pass_gate", False))
    gate_reason = str((adit.get("metrics") or {}).get("gate_reason", "")).strip()

    lines.append("### Gate（准入判定）")
    lines.append("")
    lines.append("| 项目 | 结果 |")
    lines.append("|---|---|")
    lines.append(f"| 是否通过门槛 | {'是' if pass_gate else '否'} |")
    lines.append(f"| 门槛原因 | {gate_reason if gate_reason else '待计算'} |")
    lines.append("")

    lines.append("**总体结论（轻量）**：")
    lines.append("")
    lines.append(f"- {'通过' if pass_gate else '未通过'} 合法性门槛检查（{gate_reason if gate_reason else '待计算'}）。")
    lines.append("")

    lines.extend(_md_list_block("Warnings（警告）", adit.get("warnings") or []))
    lines.extend(_md_list_block("Errors（错误）", adit.get("errors") or []))

    summary_md = os.path.join(base_dir, "summary.md")
    _write_text(summary_md, "\n".join(lines) + "\n")

    report_json = os.path.join(base_dir, "report.json")
    _write_json(report_json, {"pymatgen": pmg, "adit": adit})

    manifest = {
        "ok": True,
        "pipeline": "adit_pymatgen",
        "taskid": taskid,
        "jobid": jobid,
        "base_dir": base_dir,
        "files": {
            "summary_md": "summary.md",
            "report_json": "report.json",
            "manifest_json": "manifest.json",
        },
        "files_abs": {
            "summary_md": summary_md,
            "report_json": report_json,
            "manifest_json": os.path.join(base_dir, "manifest.json"),
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    _write_json(os.path.join(base_dir, "manifest.json"), manifest)

    print(f"[OK] wrote manifest: {os.path.join(base_dir, 'manifest.json')}")
    print(f"[OK] wrote summary: {summary_md}")
    print(f"[OK] wrote report: {report_json}")


if __name__ == "__main__":
    main()
