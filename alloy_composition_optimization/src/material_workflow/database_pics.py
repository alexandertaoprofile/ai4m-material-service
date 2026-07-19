"""Static database-picture upload helpers for markdown rendering."""

from __future__ import annotations

import os
from typing import Optional

from alpha.logs import logger

from src.storage_utils import oss_upload

PICTURE_PUBLIC_BASE_URL = os.getenv(
    "PICTURE_PUBLIC_BASE_URL",
    "https://www.science42.tech/alpha/materials/modelfiles/image",
).rstrip("/")


def resolve_public_pic_path(repo_root: str, filename: str) -> str:
    """Resolve static public picture assets for the material discovery demo."""
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
            filename,
        ),
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
        os.path.join(repo_root, "public", filename),
        os.path.join(repo_root, "public", "databasepic", filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


async def upload_database_pic_for_markdown(
    *,
    pic_abs_path: str,
    pic_name: str,
    taskid: str,
    logger_obj: Optional[object] = None,
) -> str:
    """Upload a static database image and return the public markdown URL."""
    log = logger_obj or logger
    try:
        if not pic_abs_path or not os.path.exists(pic_abs_path):
            log.warning(f"[DB_PIC] file not found: {pic_abs_path}")
            return ""
        pic_name_s = str(pic_name or "").strip()
        if not pic_name_s:
            return ""
        with open(pic_abs_path, "rb") as f:
            payload = f.read()
        taskid_s = str(taskid or "").replace("/", "_")
        oss_key = f"materials/modelfiles/image/{taskid_s}/databasepic/{pic_name_s}"
        resp = await oss_upload("alpha", oss_key, payload)
        if not isinstance(resp, dict) or resp.get("status") != 200:
            log.warning(f"[DB_PIC] upload failed: {pic_abs_path} resp={resp}")
            return ""
        return f"{PICTURE_PUBLIC_BASE_URL}/{taskid_s}/databasepic/{pic_name_s}"
    except Exception as exc:
        log.exception(f"[DB_PIC] upload exception: {exc!s}")
        return ""
