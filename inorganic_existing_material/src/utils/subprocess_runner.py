import os
import sys
import json
import asyncio
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


async def run_mp_export_assets_streaming(
    repo_root: str,
    taskid: str,
    formula: str,
    eta_seconds: float = 12.0,
    progress_emit_interval_s: int = 4,
    env_name: str = "mp-api-py311",
    script_relpath: str = "tools/mp_export_assets.py",
):
    """
    等价执行 mp_export_assets.py（异步流式），返回结构化结果供上层保持原有日志/WS行为。
    """
    script = os.path.join(repo_root, script_relpath)
    cmd = [
        "micromamba", "run", "-n", env_name,
        "python", script,
        "--taskid", str(taskid),
        "--jobid", str(formula),
        "--formula", str(formula),
        "--prefer-stable",
    ]

    start_ts = asyncio.get_event_loop().time()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=repo_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    progress_events = []
    while proc.returncode is None:
        await asyncio.sleep(progress_emit_interval_s)
        elapsed = asyncio.get_event_loop().time() - start_ts
        eta = max(8.0, float(eta_seconds))
        pct = min(95, max(1, int((elapsed / eta) * 90)))
        remain = max(0, int(round(eta - elapsed)))
        progress_events.append({"elapsed": int(elapsed), "pct": int(pct), "remain": int(remain)})

    out_b, _ = await proc.communicate()
    out_t = (out_b or b"").decode("utf-8", errors="ignore")
    elapsed_total = max(0.0, asyncio.get_event_loop().time() - start_ts)
    new_eta = max(8.0, min(20.0, 0.7 * float(eta_seconds) + 0.3 * float(elapsed_total)))

    return {
        "ok": proc.returncode == 0,
        "returncode": int(proc.returncode),
        "cmd": cmd,
        "stdout": out_t,
        "elapsed_total": float(elapsed_total),
        "eta_seconds_new": float(new_eta),
        "progress_events": progress_events,
    }
