#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
from typing import Dict, Any, List, Optional

def run_cmd(cmd: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
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
    # 约定：stdout 最后一段是 JSON（mp_export_assets.py 已经做到）
    try:
        j = json.loads(out)
        return j
    except Exception:
        return {"ok": True, "raw_stdout": out, "raw_stderr": err, "cmd": cmd}

def main():
    """
    统一入口：给 entry_path + args_json，执行后打印 JSON（manifest 或错误）
    用途：后端 service 只需要调用这个脚本，而不用关心每个 case 细节
    """
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: run_case_entry.py <entry_path> <args_json>"}, ensure_ascii=False))
        sys.exit(1)

    entry_path = sys.argv[1]
    args_json = sys.argv[2]

    try:
        args = json.loads(args_json)
    except Exception:
        print(json.dumps({"ok": False, "error": "args_json is not valid json"}, ensure_ascii=False))
        sys.exit(1)

    python_bin = args.get("python", sys.executable)
    cmd = [python_bin, entry_path]

    # 支持传递 list[str] 类型的 cli_args
    cli_args = args.get("cli_args") or []
    if not isinstance(cli_args, list):
        cli_args = []
    cmd += [str(x) for x in cli_args]

    res = run_cmd(cmd, cwd=args.get("cwd"))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("ok") else 1)

if __name__ == "__main__":
    main()
