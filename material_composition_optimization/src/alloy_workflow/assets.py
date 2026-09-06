"""Publish alloy presentation images through the shared MinIO/OSS path."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _load_storage_environment() -> None:
    """Load only this service's local storage credentials."""
    service_root = Path(__file__).resolve().parents[2]
    load_dotenv(service_root / ".env", override=False)


async def publish_png_assets(taskid: str, assets: list[dict]) -> dict[str, str]:
    """Upload generated PNGs and return public HTTPS URLs keyed by asset name.

    This intentionally follows inorganic_existing_material's convention:
    bucket ``alpha`` and the public ``materials/modelfiles/image`` prefix.
    """
    _load_storage_environment()
    required = ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY_ID", "MINIO_ACCESS_KEY_SECRET")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"MinIO asset publishing is not configured: missing {', '.join(missing)}")

    from src.storage_utils import oss_upload

    public_base = os.getenv(
        "PICTURE_PUBLIC_BASE_URL",
        "https://www.science42.tech/alpha/materials/modelfiles/image",
    ).rstrip("/")
    task_key = str(taskid).replace("/", "_").replace("\\", "_")
    pipeline = "alloy_composition_optimization"
    urls: dict[str, str] = {}
    for item in assets:
        local_path = Path(str(item.get("local_path") or ""))
        if not local_path.is_file() or local_path.suffix.lower() != ".png":
            continue
        object_key = f"materials/modelfiles/image/{task_key}/{pipeline}/{local_path.name}"
        result = await oss_upload("alpha", object_key, local_path.read_bytes())
        if not isinstance(result, dict) or result.get("status") != 200:
            raise RuntimeError(f"MinIO upload failed for {local_path.name}: {result}")
        urls[str(item["name"])] = f"{public_base}/{task_key}/{pipeline}/{local_path.name}"
    return urls
