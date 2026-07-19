"""Utilities for reading Materials Project result manifests."""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, Optional


JsonDict = Dict[str, Any]


def collect_material_outputs(repo_root: str, taskid: str, jobid: str = "") -> JsonDict:
    """Locate MP result manifests for the current task/formula."""
    base = os.path.join(
        repo_root,
        "src",
        "MNS_CaseHub",
        "cases",
        "material_discovery_demo",
        "results",
    )
    taskid_s = str(taskid).replace("/", "_")

    if jobid:
        mp_cands = sorted(glob.glob(os.path.join(base, "mp", f"*{taskid_s}*", jobid, "manifest.json")))
    else:
        mp_cands = sorted(glob.glob(os.path.join(base, "mp", f"*{taskid_s}*", "*", "manifest.json")))

    return {
        "taskid": taskid,
        "jobid": jobid,
        "paths": {
            "mp_manifest": mp_cands[-1] if mp_cands else None,
            "adit_report": None,
            "adit_manifest": None,
        },
    }


def _safe_load_json(path: Optional[str]) -> Optional[Any]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_material_parameters(collected: JsonDict) -> JsonDict:
    """Build the compact MP explanation payload used by the LLM stage."""
    paths = collected.get("paths") or {}
    mp_manifest = _safe_load_json(paths.get("mp_manifest"))

    parameters: JsonDict = {
        "taskid": collected.get("taskid", ""),
        "jobid": collected.get("jobid") or "",
        "mp_selected": {
            "count_selected": 0,
            "items": [],
        },
        "mp_context": {
            "formula": "",
            "primary_material_id": "",
            "query": {},
        },
    }

    if isinstance(mp_manifest, dict):
        query = mp_manifest.get("query") or {}
        parameters["mp_context"]["formula"] = mp_manifest.get("formula") or (collected.get("jobid") or "")
        parameters["mp_context"]["query"] = query
        parameters["mp_context"]["primary_material_id"] = query.get("primary_material_id") or ""

        files = mp_manifest.get("files") or mp_manifest.get("files_abs") or {}
        selected_path = files.get("selected_structures_json") or ""
        selected_json = _safe_load_json(selected_path)

        if isinstance(selected_json, dict):
            items = selected_json.get("items") or []
            parameters["mp_selected"]["items"] = items if isinstance(items, list) else []
            count_selected = selected_json.get("count_selected")
            parameters["mp_selected"]["count_selected"] = (
                int(count_selected) if isinstance(count_selected, int) else len(parameters["mp_selected"]["items"])
            )
        elif isinstance(selected_json, list):
            parameters["mp_selected"]["items"] = selected_json
            parameters["mp_selected"]["count_selected"] = len(selected_json)

    return parameters
