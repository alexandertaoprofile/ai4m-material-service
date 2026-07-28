"""Compatibility adapter for the existing Alloy WebSocket event contract."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from src.alloy_workflow.assets import publish_png_assets


SERVICE_AGENT = "alloy_composition_optimization"
FRONTEND_STEP_ID = "FILAMENT_SELECTION_OPTIMIZATION"
FRONTEND_STEP_TITLE = "合金成分优化与候选初筛"

ASSET_DOCS = {
    "screening_funnel": "左柱为生成的候选数，右柱为通过初筛的候选数，用于判断当前条件的筛选严格程度。",
    "strength_hardness_tradeoff": "每个点代表一个通过初筛的候选：横轴越右表示预测屈服强度越高，纵轴越上表示预测硬度越高；蓝色为训练数据覆盖较好，橙色为训练数据边界附近。",
    "composition_percentiles": "这张图展示保留候选中每种元素的常见含量区间：竖线下端为 P5、圆点为 P50（中位数）、上端为 P95。它用于了解下一步可继续探索的配比区域，不代表最终推荐配方。",
}
ASSET_TITLES = {
    "screening_funnel": "候选筛选概览",
    "strength_hardness_tradeoff": "强度与硬度的候选分布",
    "composition_percentiles": "候选成分区间图（非最终配方）",
}
logger = logging.getLogger("alloy.protocol")


def _local_asset_url(websocket: Any, item: dict[str, Any]) -> str:
    """Return a browser-reachable task asset URL when object storage is down."""
    local_path = str(item["url"])
    configured = os.getenv("ALLOY_ASSET_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return f"{configured}{local_path}"
    ws_url = getattr(websocket, "url", None)
    if ws_url:
        raw = str(ws_url)
        if raw.startswith("wss://"):
            return "https://" + raw[6:].split("/", 1)[0] + local_path
        if raw.startswith("ws://"):
            return "http://" + raw[5:].split("/", 1)[0] + local_path
    return local_path


async def prepare_public_assets(websocket: Any, taskid: str, result: dict[str, Any], results_root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[dict[str, str]]]:
    assets = [{"name": item["name"], "local_path": results_root / taskid / "presentation" / Path(item["url"]).name} for item in result["presentation"]["assets"]]
    try:
        public_urls = await publish_png_assets(taskid, assets)
    except Exception as exc:
        # Do not suppress charts just because MinIO is unavailable.  The task
        # asset endpoint remains part of the existing HTTP contract.
        public_urls = {item["name"]: _local_asset_url(websocket, item) for item in result["presentation"]["assets"]}
        print(f"[ALLOY][{taskid}] MinIO publication failed; using local task-asset URLs", flush=True)
        logger.exception("MinIO publication failed; using local task-asset URLs taskid=%s", taskid)
        await websocket.send_json({"version": "1.0.0", "agent": SERVICE_AGENT, "request_id": taskid, "type": "progress", "data": {"id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "status": "failed", "description": str(exc)}})
    else:
        print(f"[ALLOY][{taskid}] published PNG assets count={len(public_urls)} names={sorted(public_urls)}", flush=True)
        logger.info("published %s alloy PNG asset(s) taskid=%s", len(public_urls), taskid)
    visual_assets = [{"url": public_urls[item["name"]], "title": ASSET_TITLES.get(item["name"], item["name"]), "description": ASSET_DOCS.get(item["name"], "")} for item in result["presentation"]["assets"] if item["name"] in public_urls]
    return public_urls, ASSET_DOCS, ASSET_TITLES, visual_assets


async def emit_public_asset_events(websocket: Any, result: dict[str, Any], public_urls: dict[str, str], asset_docs: dict[str, str]) -> None:
    for item in result["presentation"]["assets"]:
        url = public_urls.get(item["name"])
        if url:
            await websocket.send_json({"step_id": FRONTEND_STEP_ID, "stepId": FRONTEND_STEP_ID, "title": FRONTEND_STEP_TITLE, "name": item["name"], "docs": asset_docs.get(item["name"], item["name"]), "url": url, "type": "MaterialsPNG", "description": asset_docs.get(item["name"], "")})
            await asyncio.sleep(0.15)
