#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from datetime import datetime
from typing import List, Dict, Any

def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def run_cmd(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
    return p.stdout.strip()

def load_json_from_stdout(s: str) -> Dict[str, Any]:
    return json.loads(s)

def main():
    ap = argparse.ArgumentParser(description="Batch run mp_export_assets for multiple inputs.")
    ap.add_argument("--taskid", required=True)
    ap.add_argument("--env", default="mp-api-py311")
    ap.add_argument("--mamba", default="micromamba")

    ap.add_argument("--formulas", nargs="*", default=None)
    ap.add_argument("--elements-sets", nargs="*", default=None,
                    help='Each set like "Li P S Cl" (quote it). Example: --elements-sets "Li P S Cl" "Li P S"')

    ap.add_argument("--poly-mode", default="p")
    ap.add_argument("--poly-alpha", type=int, default=85)
    ap.add_argument("--poly-cn", default="4")

    args = ap.parse_args()

    items = []
    idx = 0

    if args.formulas:
        for f in args.formulas:
            idx += 1
            jobid = f"batch_{idx:03d}_{f}"
            cmd = [
                args.mamba, "run", "-n", args.env,
                "python", "tools/mp_export_assets.py",
                "--taskid", args.taskid,
                "--jobid", jobid,
                "--formula", f,
                "--prefer-stable",
                "--poly-mode", args.poly_mode,
                "--poly-cn", args.poly_cn,
                "--poly-alpha", str(args.poly_alpha),
            ]
            out = load_json_from_stdout(run_cmd(cmd))
            items.append(out)

    if args.elements_sets:
        for s in args.elements_sets:
            idx += 1
            els = s.split()
            jobid = f"batch_{idx:03d}_{'-'.join(els)}"
            cmd = [
                args.mamba, "run", "-n", args.env,
                "python", "tools/mp_export_assets.py",
                "--taskid", args.taskid,
                "--jobid", jobid,
                "--elements", *els,
                "--prefer-stable",
                "--poly-mode", args.poly_mode,
                "--poly-cn", args.poly_cn,
                "--poly-alpha", str(args.poly_alpha),
            ]
            out = load_json_from_stdout(run_cmd(cmd))
            items.append(out)

    print(json.dumps({
        "ok": True,
        "generated_at": datetime.now().isoformat(),
        "count": len(items),
        "items": items,
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
