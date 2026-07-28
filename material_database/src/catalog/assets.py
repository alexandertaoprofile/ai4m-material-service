"""Publish mature-material charts through the shared MinIO public-image path."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _load_storage_environment() -> None:
    service_root = Path(__file__).resolve().parents[2]
    load_dotenv(service_root / ".env", override=False)


async def publish_png_assets(taskid: str, assets: list[dict]) -> dict[str, str]:
    """Use the same bucket/key/public URL convention as the alloy service."""
    _load_storage_environment()
    required = ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY_ID", "MINIO_ACCESS_KEY_SECRET")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"MinIO 未配置：缺少 {', '.join(missing)}")
    from src.storage_utils import oss_upload

    public_base = os.getenv(
        "PICTURE_PUBLIC_BASE_URL",
        "https://www.science42.tech/alpha/materials/modelfiles/image",
    ).rstrip("/")
    task_key = str(taskid).replace("/", "_").replace("\\", "_")
    pipeline = "mature_material"
    urls: dict[str, str] = {}
    for item in assets:
        local_path = Path(str(item.get("local_path") or ""))
        if not local_path.is_file() or local_path.suffix.lower() != ".png":
            continue
        object_key = f"materials/modelfiles/image/{task_key}/{pipeline}/{local_path.name}"
        logger.info("[mature-assets] uploading local=%s bucket=alpha key=%s", local_path, object_key)
        response = await oss_upload("alpha", object_key, local_path.read_bytes())
        if response.get("status") != 200:
            raise RuntimeError(f"MinIO 上传失败：{local_path.name}，响应={response}")
        urls[str(item["name"])] = f"{public_base}/{task_key}/{pipeline}/{local_path.name}"
        logger.info("[mature-assets] published name=%s url=%s", item["name"], urls[str(item["name"])])
    return urls
