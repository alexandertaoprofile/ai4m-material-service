# -*- coding: utf-8 -*-
"""Helpers for material-stage static images and generated GIF assets."""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from typing import Callable, Iterable


def find_current_selected_structures_json(
    *,
    repo_root: str,
    taskid: str,
    formula: str,
    logger=None,
) -> str:
    """Find the selected_structures.json produced by the current MP run."""
    try:
        abs_root = os.path.join(
            repo_root,
            "src",
            "MNS_CaseHub",
            "cases",
            "material_discovery_demo",
        )
        results_dir = os.path.join(abs_root, "results")
        taskid_s = str(taskid).replace("/", "_")
        formula_s = str(formula or "").strip()
        if not formula_s:
            return ""

        manifest_pat = os.path.join(results_dir, "mp", f"*{taskid_s}*", formula_s, "manifest.json")
        manifest_cands = sorted(glob.glob(manifest_pat))
        if not manifest_cands:
            return ""

        manifest_path = manifest_cands[-1]
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        base_dir = manifest.get("base_dir") or os.path.dirname(manifest_path)
        files = manifest.get("files") or {}
        files_abs = manifest.get("files_abs") or {}
        selected_path = (
            files_abs.get("selected_structures_json")
            or files.get("selected_structures_json")
            or ""
        )
        if selected_path and not os.path.isabs(selected_path):
            selected_path = os.path.abspath(os.path.join(base_dir, selected_path))
        if selected_path and os.path.exists(selected_path):
            return selected_path

        fallback = os.path.join(base_dir, "selected_structures.json")
        return fallback if os.path.exists(fallback) else ""
    except Exception as e:
        if logger:
            logger.exception(f"[VISUAL_ASSET] locate selected_structures failed: {e!s}")
        return ""


def render_alignn_json_gif(
    *,
    repo_root: str,
    taskid: str,
    formula: str,
    selected_json: str,
    timeout: int = 45,
    logger=None,
) -> str:
    """Render a per-run ALIGNN GIF from selected_structures.json."""
    try:
        if not selected_json or not os.path.exists(selected_json):
            return ""

        script = os.path.join(repo_root, "src", "utils", "alignn_gif_renderer.py")
        if not os.path.exists(script):
            if logger:
                logger.warning(f"[ALIGNN_GIF] renderer missing: {script}")
            return ""

        taskid_s = str(taskid).replace("/", "_")
        formula_s = str(formula or "material").replace("/", "_").strip() or "material"
        out_dir = os.path.join(
            repo_root,
            "src",
            "MNS_CaseHub",
            "cases",
            "material_discovery_demo",
            "results",
            "alignn_gif",
            taskid_s,
            formula_s,
        )
        os.makedirs(out_dir, exist_ok=True)
        out_gif = os.path.join(out_dir, "alignn_json_driven.gif")

        proc = subprocess.run(
            [sys.executable, script, "--selected-json", selected_json, "--out", out_gif],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            if logger:
                logger.warning(
                    f"[ALIGNN_GIF] render failed rc={proc.returncode} "
                    f"stderr={str(proc.stderr or '')[-1200:]}"
                )
            return ""
        if os.path.exists(out_gif):
            if logger:
                logger.info(f"[ALIGNN_GIF] rendered: {out_gif}")
            return out_gif
        return ""
    except Exception as e:
        if logger:
            logger.exception(f"[ALIGNN_GIF] render exception: {e!s}")
        return ""


def render_periodic_elements_gif(
    *,
    repo_root: str,
    taskid: str,
    formulas: Iterable[str],
    timeout: int = 45,
    logger=None,
) -> str:
    """Render a per-run periodic table GIF from candidate formulas."""
    try:
        fs = [str(x).strip() for x in (formulas or []) if str(x).strip()]
        if not fs:
            return ""

        script = os.path.join(repo_root, "src", "utils", "periodic_gif_renderer.py")
        if not os.path.exists(script):
            if logger:
                logger.warning(f"[PERIODIC_GIF] renderer missing: {script}")
            return ""

        taskid_s = str(taskid).replace("/", "_")
        out_dir = os.path.join(
            repo_root,
            "src",
            "MNS_CaseHub",
            "cases",
            "material_discovery_demo",
            "results",
            "periodic_gif",
            taskid_s,
        )
        os.makedirs(out_dir, exist_ok=True)
        out_gif = os.path.join(out_dir, "periodic_elements.gif")

        cmd = [sys.executable, script, "--out", out_gif]
        for formula in fs:
            cmd.extend(["--formula", formula])

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            if logger:
                logger.warning(
                    f"[PERIODIC_GIF] render failed rc={proc.returncode} "
                    f"stderr={str(proc.stderr or '')[-1200:]}"
                )
            return ""
        if os.path.exists(out_gif):
            if logger:
                logger.info(f"[PERIODIC_GIF] rendered: {out_gif}")
            return out_gif
        return ""
    except Exception as e:
        if logger:
            logger.exception(f"[PERIODIC_GIF] render exception: {e!s}")
        return ""


def resolve_database_pic_path(repo_root: str, filename: str) -> str:
    """Resolve a databasepic asset with the current public path first."""
    filename = str(filename or "").strip()
    if not filename:
        return ""
    candidates = [
        os.path.join(
            repo_root,
            "src",
            "MNS_CaseHub",
            "cases",
            "material_discovery_demo",
            "public",
            "databasepic",
            filename,
        ),
        os.path.join(repo_root, "public", "databasepic", filename),
        os.path.join(
            repo_root,
            "src",
            "MNS_CaseHub",
            "cases",
            "material_discovery_demo",
            "results",
            "databasepic",
            filename,
        ),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


async def upload_alignn_dynamic_or_static(
    *,
    repo_root: str,
    taskid: str,
    formula: str,
    upload_database_pic_for_markdown: Callable[[str, str], object],
    logger=None,
) -> str:
    """Prefer the JSON-driven GIF for this run, then fall back to the static asset."""
    try:
        selected_json = find_current_selected_structures_json(
            repo_root=repo_root,
            taskid=taskid,
            formula=formula,
            logger=logger,
        )
        alignn_gif = render_alignn_json_gif(
            repo_root=repo_root,
            taskid=taskid,
            formula=formula,
            selected_json=selected_json,
            logger=logger,
        )
        if alignn_gif and os.path.exists(alignn_gif):
            url = await upload_database_pic_for_markdown(alignn_gif, "alignn_json_driven.gif")
            if url:
                return url
    except Exception as e:
        if logger:
            logger.exception(f"[ALIGNN_GIF] dynamic upload failed: {e!s}")

    return await upload_database_pic_for_markdown(
        resolve_database_pic_path(repo_root, "alignn.png"),
        "alignn.png",
    )


async def upload_periodic_dynamic_or_static(
    *,
    repo_root: str,
    taskid: str,
    formulas: Iterable[str],
    upload_database_pic_for_markdown: Callable[[str, str], object],
    logger=None,
) -> str:
    """Prefer the formula-driven periodic GIF, then fall back to the static asset."""
    try:
        periodic_gif = render_periodic_elements_gif(
            repo_root=repo_root,
            taskid=taskid,
            formulas=formulas,
            logger=logger,
        )
        if periodic_gif and os.path.exists(periodic_gif):
            url = await upload_database_pic_for_markdown(periodic_gif, "periodic_elements.gif")
            if url:
                return url
    except Exception as e:
        if logger:
            logger.exception(f"[PERIODIC_GIF] dynamic upload failed: {e!s}")

    return await upload_database_pic_for_markdown(
        resolve_database_pic_path(repo_root, "period.png"),
        "period.png",
    )
