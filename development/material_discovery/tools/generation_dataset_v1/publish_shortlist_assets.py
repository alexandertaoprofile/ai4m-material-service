#!/usr/bin/env python3
"""Publish the retained shortlist structure images through the service MinIO path.

This intentionally uses the same alpha bucket/object-key/public-URL convention
as the material-discovery frontend.  It emits a local manifest with the exact
public URLs only after the objects are verified through the S3-compatible API.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

os.chdir(SERVICE_ROOT)  # Ensures src.storage_utils loads this service's .env.

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT / ".env", override=False)

from src.storage_utils import get_storage_client  # noqa: E402


BRIEF_ROOT = Path("/data/se42/hea_surrogate/analysis/shortlist_property_screen_v1/candidate_briefs")
ASSETS = (
    "hea_mn3crfe4co2ni2_relaxed_structure.png",
    "sialon_alsi3n3o3_relaxed_structure.png",
)
PUBLIC_BASE = os.environ["PICTURE_PUBLIC_BASE_URL"].rstrip("/")
KEY_PREFIX = f"materials/modelfiles/image/hea_shortlist/{date.today():%Y%m%d}"


async def main() -> None:
    client = get_storage_client()
    published: list[dict[str, str]] = []
    for name in ASSETS:
        source = BRIEF_ROOT / "assets" / name
        if not source.is_file():
            raise FileNotFoundError(source)
        key = f"{KEY_PREFIX}/{name}"
        await client.aput_object(
            "alpha",
            key,
            source.read_bytes(),
            content_type="image/png",
            content_disposition="inline",
            cache_control="public, max-age=31536000, immutable",
        )
        if not await client.aobject_exists("alpha", key):
            raise RuntimeError(f"MinIO upload verification failed: alpha/{key}")
        published.append({"name": name, "object_key": key, "url": f"{PUBLIC_BASE}/hea_shortlist/{date.today():%Y%m%d}/{name}"})

    manifest = BRIEF_ROOT / "published_assets.json"
    manifest.write_text(json.dumps({"bucket": "alpha", "assets": published}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest)
    for asset in published:
        print(asset["url"])


if __name__ == "__main__":
    asyncio.run(main())
