#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
材料发现与跨尺度模拟（Demo）

维护性版本：
- 作为 case main_entry：负责“产出可被前端展示的资产”
- 当前 demo：调用 tools/mp_export_assets.py
  生成：summary_all.json / selected_structures.json / summary.md / structure.cif / structure.json / structure_mpstyle.glb / manifest.json
- stdout：打印 manifest JSON（给上游/服务端接）
"""

import os
import sys
import json
import argparse
import subprocess

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", "..", "..", ".."))  # repo/src/MNS_CaseHub/cases/material_discovery_demo/ -> repo/

def run_cmd(cmd, cwd=None):
    p = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return {"ok": False, "returncode": p.returncode, "stdout": out, "stderr": err, "cmd": cmd}
    # mp_export_assets.py 会打印 JSON
    try:
        return json.loads(out)
    except Exception:
        return {"ok": True, "raw_stdout": out, "raw_stderr": err, "cmd": cmd}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taskid", required=True)
    ap.add_argument("--jobid", required=True)
    ap.add_argument("--formula", default=None)
    ap.add_argument("--elements", nargs="*", default=None)

    ap.add_argument("--prefer-stable", action="store_true", default=True)
    ap.add_argument("--max-candidates", type=int, default=5)
    ap.add_argument("--relative-paths", action="store_true", default=True)

    # 渲染参数（可按需暴露更多）
    ap.add_argument("--render-poly-mode", default="p")
    ap.add_argument("--render-poly-cn", default="4")
    ap.add_argument("--render-poly-alpha", type=int, default=85)

    ap.add_argument("--supercell", nargs=3, type=int, default=[1, 1, 1])
    ap.add_argument("--atom-radius", type=float, default=0.40)
    ap.add_argument("--bond-radius", type=float, default=0.07)

    args = ap.parse_args()

    tool = os.path.join(REPO_ROOT, "tools", "mp_export_assets.py")
    if not os.path.exists(tool):
        print(json.dumps({"ok": False, "error": f"mp_export_assets.py not found: {tool}"} , ensure_ascii=False, indent=2))
        sys.exit(1)

    cmd = [
        sys.executable, tool,
        "--taskid", args.taskid,
        "--jobid", args.jobid,
        "--max-candidates", str(args.max_candidates),
        "--render-poly-mode", str(args.render_poly_mode),
        "--render-poly-cn", str(args.render_poly_cn),
        "--render-poly-alpha", str(args.render_poly_alpha),
        "--supercell", str(args.supercell[0]), str(args.supercell[1]), str(args.supercell[2]),
        "--atom-radius", str(args.atom_radius),
        "--bond-radius", str(args.bond_radius),
        "--relative-paths",
    ]

    if args.prefer_stable:
        cmd.append("--prefer-stable")

    if args.formula:
        cmd += ["--formula", args.formula]
    elif args.elements:
        cmd += ["--elements"] + [str(x) for x in args.elements]
    else:
        print(json.dumps({"ok": False, "error": "Need --formula or --elements"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    res = run_cmd(cmd, cwd=REPO_ROOT)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("ok") else 1)

if __name__ == "__main__":
    main()
