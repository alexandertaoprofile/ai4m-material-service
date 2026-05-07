# -*- coding: utf-8 -*-
import os
import re
import subprocess


def extract_cif_path_from_item(item: dict, base_dir: str) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("abs_path", "cif_path", "structure_path", "file_path", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            p = value.strip()
            if os.path.isabs(p):
                return p
            return os.path.abspath(os.path.join(base_dir, p))
    return ""


def pick_num(item: dict, keys: list):
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = item.get(key)
        try:
            if value is None:
                continue
            return float(value)
        except Exception:
            continue
    return None


def call_alignn_pretrained(model_name: str, cif_path: str, timeout_sec: int = 30):
    alignn_env = os.getenv("ALIGNN_ENV", "alignn-gpu-test")
    cmd = [
        "micromamba",
        "run",
        "-n",
        alignn_env,
        "python",
        "-m",
        "alignn.pretrained",
        "--model_name",
        model_name,
        "--file_format",
        "cif",
        "--file_path",
        str(cif_path),
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
    match = re.search(r"Predicted value:.*?\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]", txt)
    if not match:
        match = re.search(r"\[([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\]", txt)
    if not match:
        raise RuntimeError(f"无法解析预测值: {txt[-500:]}")
    return float(match.group(1))


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
    for model_name in model_candidates:
        if model_name in invalid_models:
            continue
        cache_key = (str(cif_path), str(model_name))
        if cache_key in pred_cache:
            val = pred_cache.get(cache_key)
            if isinstance(val, float):
                return val, model_name, ""
            continue
        try:
            val = call_alignn_pretrained(model_name, cif_path, timeout_sec=timeout_sec)
            pred_cache[cache_key] = val
            return val, model_name, ""
        except Exception as e:
            last_err = str(e)
            pred_cache[cache_key] = None
            err_l = last_err.lower()
            if ("keyerror" in err_l) or ("not found" in err_l and "model" in err_l):
                invalid_models.add(model_name)
    return None, "", last_err


def probe_alignn_model(model_name: str, cif_path: str):
    try:
        _ = call_alignn_pretrained(model_name, cif_path)
        return True, ""
    except Exception as e:
        return False, str(e)
