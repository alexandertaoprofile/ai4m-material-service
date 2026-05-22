"""Frontend asset emission for material workflow results."""

from __future__ import annotations

import glob
import os
from typing import Awaitable, Callable, Optional

from alpha.logs import logger

from src.storage_utils import get_image_url, oss_upload
from .material_profiles import formula_profile

MINIO_ADDR = "http://36.103.203.113:2300"
HTTPS_VIP_ADDR = "http://36.103.203.113:2300"


async def send_results_to_frontend(
    websocket,
    source_path: str,
    root_path: str,
    taskid: str,
    jobid: str = "",
    pipeline: str = "mp",
    allow_latest_job: bool = True,
    step_id: str = "MATERIAL_SCREENING",
    emit_summary_block: bool = True,
    upload_func: Callable[[str, str, bytes], Awaitable[dict]] = oss_upload,
    image_url_func: Callable[[str, str], str] = get_image_url,
) -> None:
    """Upload result images/GLB and notify the frontend using the existing protocol."""

    async def _ws_asset(name: str, docs: str, url: str, asset_type: str):
        await websocket.send_json({
            "step_id": step_id,
            "name": name,
            "docs": docs,
            "url": url,
            "type": asset_type,
        })

    async def _ws_right(step_id_local: str, text: str):
        await websocket.send_text(f"<<<CONTENT_START:{step_id_local}>>>")
        if text:
            await websocket.send_text(text.rstrip() + "\n")
        await websocket.send_text(f"<<<CONTENT_END:{step_id_local}>>>")

    logger.info(
        f"[send_results_to_frontend] ENTER step_id={step_id} pipeline={pipeline} "
        f"source_path={source_path}, root_path={root_path}, taskid={taskid}, jobid={jobid}"
    )

    abs_root_path = os.path.abspath(os.path.join(source_path, root_path))
    results_dir = os.path.join(abs_root_path, "results")

    logger.info(f"[send_results_to_frontend] abs_root_path={abs_root_path}")
    logger.info(f"[send_results_to_frontend] results_dir={results_dir} exists={os.path.exists(results_dir)}")

    if not os.path.exists(results_dir):
        logger.warning(f"[send_results_to_frontend] results dir missing: {results_dir}")
        return

    exts = {".png", ".jpg", ".jpeg", ".gif"}
    taskid_sanitized = str(taskid).replace("/", "_")

    manifest_path: Optional[str] = None
    try:
        if jobid:
            allow_latest_job = False
            pattern = os.path.join(results_dir, pipeline, f"*{taskid_sanitized}*", str(jobid), "manifest.json")
            cands = sorted(glob.glob(pattern))
            if cands:
                manifest_path = cands[-1]

        if manifest_path is None and allow_latest_job:
            pattern = os.path.join(results_dir, pipeline, f"*{taskid_sanitized}*", "*", "manifest.json")
            cands = sorted(glob.glob(pattern))
            if cands:
                manifest_path = cands[-1]
    except Exception as exc:
        logger.warning(f"[send_results_to_frontend] manifest lookup failed: {exc}")

    async def _upload_and_get_url(abs_path: str, oss_key: str):
        try:
            with open(abs_path, "rb") as f:
                payload = f.read()
            result = await upload_func("alpha", oss_key, payload)
            if result.get("status") != 200:
                logger.error(f"[send_results_to_frontend] upload failed: {abs_path}, resp={result}")
                return None
            url = image_url_func("alpha", oss_key)
            if url.startswith(MINIO_ADDR):
                url = url.replace(MINIO_ADDR, HTTPS_VIP_ADDR, 1)
            return url
        except Exception as exc:
            logger.exception(f"[send_results_to_frontend] upload failed: {abs_path} | {exc}")
            return None

    if not manifest_path or not os.path.exists(manifest_path):
        logger.warning(
            f"[send_results_to_frontend] manifest not found pipeline={pipeline} taskid={taskid}, "
            f"jobid={jobid}; fallback scan results dir"
        )
        try:
            image_files = sorted(
                f for f in os.listdir(results_dir)
                if os.path.isfile(os.path.join(results_dir, f))
                and os.path.splitext(f)[1].lower() in exts
            )
        except Exception as exc:
            logger.exception(f"[send_results_to_frontend] result scan failed: {exc}")
            return

        for fname in image_files:
            abs_img = os.path.join(results_dir, fname)
            oss_key = f"XIMUAlpha_MNS/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
            url = await _upload_and_get_url(abs_img, oss_key)
            if not url:
                continue
            await _ws_asset(
                name=fname,
                docs=os.path.splitext(fname)[0],
                url=url,
                asset_type="MaterialsPNG",
            )
        return

    logger.info(f"[send_results_to_frontend] found manifest: {manifest_path}")

    try:
        import json
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        logger.exception(f"[send_results_to_frontend] manifest read failed: {exc}")
        return

    if not isinstance(manifest, dict) or not manifest.get("ok"):
        logger.warning("[send_results_to_frontend] invalid manifest or ok!=true")
        return

    files = manifest.get("files_abs") or manifest.get("files") or {}
    base_dir = manifest.get("base_dir") or os.path.dirname(manifest_path)

    def _abspath(path: str) -> str:
        if not path:
            return ""
        path = str(path)
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(base_dir, path))

    md_path = _abspath(files.get("summary_md", ""))
    if emit_summary_block and md_path and os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                md_text = f.read()
            await _ws_right(step_id, md_text[:120000])
            logger.info(f"[send_results_to_frontend] sent summary.md: {md_path}")
        except Exception as exc:
            logger.warning(f"[send_results_to_frontend] summary send failed: {exc}")

    image_items = []

    def _add_image_item(path):
        if not path:
            return
        path_s = str(path)
        if os.path.splitext(path_s)[1].lower() in exts and path_s not in image_items:
            image_items.append(path_s)

    if isinstance(manifest.get("images"), list) and manifest["images"]:
        for item in manifest["images"]:
            _add_image_item(item.get("path", "") if isinstance(item, dict) else item)

    # Some manifests store generated images/GIFs under files/files_abs instead of images.
    for source in (manifest.get("files_abs"), manifest.get("files"), files):
        if isinstance(source, dict):
            for value in source.values():
                if isinstance(value, str):
                    _add_image_item(value)
                elif isinstance(value, list):
                    for sub_value in value:
                        _add_image_item(sub_value)

    try:
        for fname in sorted(os.listdir(base_dir)):
            path = os.path.join(base_dir, fname)
            if os.path.isfile(path) and os.path.splitext(fname)[1].lower() in exts:
                _add_image_item(path)
    except Exception:
        pass

    for item_path in image_items:
        abs_img = _abspath(item_path) if not os.path.isabs(str(item_path)) else str(item_path)
        if not abs_img or not os.path.exists(abs_img):
            continue
        if os.path.splitext(abs_img)[1].lower() not in exts:
            continue

        fname = os.path.basename(abs_img)
        oss_key = f"XIMUAlpha_MNS/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
        url = await _upload_and_get_url(abs_img, oss_key)
        if not url:
            continue
        await _ws_asset(
            name=fname,
            docs=os.path.splitext(fname)[0],
            url=url,
            asset_type="MaterialsPNG",
        )

    glb_path = _abspath(files.get("structure_glb", ""))
    if glb_path and os.path.exists(glb_path):
        fname = os.path.basename(glb_path)
        oss_key = f"XIMUAlpha_MNS/{taskid_sanitized}/{pipeline}/{jobid or 'job'}/{fname}"
        url = await _upload_and_get_url(glb_path, oss_key)
        if url:
            await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
            await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")

            formula_for_asset = str(jobid or "").strip() or str(manifest.get("formula") or "").strip()
            profile = formula_profile(formula_for_asset) if formula_for_asset else {}
            cn_name = str(profile.get("中文名称") or "").strip()
            rich_name = f"{formula_for_asset}_{cn_name}_结构模型.glb" if formula_for_asset and cn_name else fname
            rich_name = rich_name.replace("/", "_")
            rich_docs = (
                f"{formula_for_asset}（{cn_name}）三维结构模型（GLB）"
                if formula_for_asset and cn_name
                else "结构三维可视化模型（GLB）"
            )
            await _ws_asset(
                name=rich_name,
                docs=rich_docs,
                url=url,
                asset_type="MaterialsGLB",
            )

            await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
            await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
            logger.info(f"[send_results_to_frontend] sent MaterialsGLB: {fname}")
    else:
        logger.warning(f"[send_results_to_frontend] structure_glb missing: {glb_path}")
