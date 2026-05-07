# -*- coding: utf-8 -*-
import json
import re
import glob


def normalize_user_text(s) -> str:
    def _strip_preface_payload_noise(text: str) -> str:
        t = str(text or "")
        t = re.sub(r"\{[^{}]{0,20000}\"version\"\s*:\s*\"1\.0\.0\"[^{}]{0,20000}\}", " ", t, flags=re.DOTALL)
        t = re.sub(r"\{[^{}]{0,20000}\"type\"\s*:\s*\"progress\"[^{}]{0,20000}\}", " ", t, flags=re.DOTALL)
        t = re.sub(r"\{[^{}]{0,20000}\"request_id\"\s*:\s*\"[^\"]+\"[^{}]{0,20000}\}", " ", t, flags=re.DOTALL)
        anchor = t.find("### 需求")
        if anchor >= 0:
            t = t[anchor:]
        pre = t.find("=== 前置结果 ===")
        if pre >= 0:
            t = t[pre + len("=== 前置结果 ==="):]
        return t

    if isinstance(s, dict):
        s = (s.get("idea") or s.get("content") or s.get("text") or s.get("query") or "")
    if isinstance(s, list):
        for item in reversed(s):
            if isinstance(item, dict):
                content = item.get("idea") or item.get("content") or item.get("text") or item.get("query")
                if isinstance(content, str) and content.strip():
                    s = content
                    break
            if hasattr(item, "content"):
                content = getattr(item, "content", None)
                if isinstance(content, str) and content.strip():
                    s = content
                    break
            if isinstance(item, str) and item.strip():
                s = item
                break
        else:
            s = ""
    s = str(s or "").strip()
    s = _strip_preface_payload_noise(s)
    m = re.search(r"\[Human:\s*(.*?)\s*\]$", s)
    if m:
        s = m.group(1).strip()
    return s.strip("[](){} \n\t")


def parse_route(s: str):
    s = (s or "").strip()
    m = re.match(r"^/(mp)\s+(.+)$", s, flags=re.IGNORECASE)
    if not m:
        return None, s
    return m.group(1).lower(), m.group(2).strip()


def safe_float(x):
    try:
        if x is None:
            return None
        if isinstance(x, bool):
            return float(int(x))
        return float(x)
    except Exception:
        return None


def safe_bool(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x)
    if isinstance(x, str):
        t = x.strip().lower()
        if t in {"true", "pass", "passed", "yes", "y", "1"}:
            return True
        if t in {"false", "fail", "failed", "no", "n", "0"}:
            return False
    return None


def flatten_dict(obj, prefix="", out=None):
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = f"{prefix}.{k}" if prefix else str(k)
            flatten_dict(v, nk, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            nk = f"{prefix}[{i}]"
            flatten_dict(v, nk, out)
    else:
        out[prefix.lower()] = obj
    return out


def pick_value(flat: dict, include_any: list, exclude_any: list = None):
    exclude_any = [x.lower() for x in (exclude_any or [])]
    for k, v in flat.items():
        kk = str(k).lower()
        if all(x.lower() in kk for x in include_any):
            if any(ex in kk for ex in exclude_any):
                continue
            return v
    for k, v in flat.items():
        kk = str(k).lower()
        if any(x.lower() in kk for x in include_any):
            if any(ex in kk for ex in exclude_any):
                continue
            return v
    return None


def load_latest_json(pattern_: str):
    try:
        cands_ = sorted(glob.glob(pattern_))
        if not cands_:
            return {}
        with open(cands_[-1], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def render_progress_bar(pct: int, width: int = 10) -> str:
    pct = max(0, min(100, int(pct)))
    filled = int(round((pct / 100.0) * width))
    return "[" + ("█" * filled) + ("░" * (width - filled)) + "]"
