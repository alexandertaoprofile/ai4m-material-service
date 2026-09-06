"""Publish task PNGs using the same public object-storage convention as 1111/1115."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any


DEFAULT_PUBLIC_BASE = "https://www.science42.tech/alpha/materials/modelfiles/image"


async def publish_png_assets(taskid: str, assets: list[dict[str, Any]]) -> dict[str, str]:
    """Upload PNG assets to bucket ``alpha`` and return browser-safe HTTPS URLs.

    Credentials are intentionally deployment-only.  A failure is surfaced to
    the caller, which then retains the existing task-asset fallback.
    """
    required = ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY_ID", "MINIO_ACCESS_KEY_SECRET")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("object storage is not configured")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MINIO_ACCESS_KEY_SECRET"],
        region_name=os.getenv("MINIO_REGION", "us-east-1"),
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2, "mode": "standard"}),
    )
    public_base = os.getenv("PICTURE_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE).rstrip("/")
    safe_taskid = str(taskid).replace("/", "_").replace("\\", "_")
    uploaded: dict[str, str] = {}
    for item in assets:
        path = Path(str(item.get("path") or ""))
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        key = f"materials/modelfiles/image/{safe_taskid}/refractory_multiscale_validation/{path.name}"
        await asyncio.to_thread(
            client.put_object,
            Bucket="alpha", Key=key, Body=path.read_bytes(), ContentType="image/png", ContentDisposition="inline",
        )
        uploaded[str(item["name"])] = f"{public_base}/{safe_taskid}/refractory_multiscale_validation/{path.name}"
    return uploaded
