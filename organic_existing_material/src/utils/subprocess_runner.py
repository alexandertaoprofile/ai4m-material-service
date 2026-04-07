import os
import sys
import json
import subprocess
from typing import List, Dict, Any, Optional

def run_in_micromamba(
    env_name: str,
    args: List[str],
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    在指定 micromamba 环境中运行命令，默认期望 stdout 为 JSON。
    env_name: 比如 "mp-api-py311"
    args: 例如 ["python", "tools/mp_export_assets.py", "--taskid", "...", ...]
    """
    cmd = ["micromamba", "run", "-n", env_name] + args

    p = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    if p.returncode != 0:
        return {
            "ok": False,
            "returncode": p.returncode,
            "cmd": cmd,
            "stdout": out[-20000:],
            "stderr": err[-20000:],
        }

    # 尝试解析 JSON
    try:
        return json.loads(out)
    except Exception:
        return {
            "ok": True,
            "cmd": cmd,
            "raw_stdout": out[-200000:],
            "raw_stderr": err[-20000:],
        }
