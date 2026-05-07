# -*- coding: utf-8 -*-
import os
import json


def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def resolve_case_readme_path(case: dict) -> str:
    repo = repo_root()
    root_path = case.get("root_path")
    if not root_path:
        paths = case.get("paths") or {}
        root_path = paths.get("project_root")

    if not root_path:
        return ""

    readme_name = case.get("readme") or "README.md"
    p1 = os.path.join(repo, root_path, readme_name)
    p2 = os.path.join(repo, "src", root_path, readme_name)

    if os.path.exists(p1):
        return p1
    if os.path.exists(p2):
        return p2
    return p2


def safe_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return str(x)
    except Exception:
        return ""


def get_case_root(case: dict) -> str:
    root_path = case.get("root_path")
    if not root_path:
        paths = case.get("paths") or {}
        root_path = paths.get("project_root")
    return root_path or ""


def as_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (dict, list)):
        try:
            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    return str(x)


def infer_prompt_mode(best_proj: dict) -> str:
    return "materials"
