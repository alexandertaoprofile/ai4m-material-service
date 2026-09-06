"""Compatibility adapter for the existing Alloy WebSocket event contract."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.alloy_workflow.assets import publish_png_assets


ASSET_DOCS = {
    "screening_funnel": "以分层漏斗展示生成、相风险、性能与不确定性、最终可比候选四个阶段的数量和保留率，用于定位筛选收缩发生的位置。",
    "strength_hardness_tradeoff": "每个点代表一个通过初筛的候选；颜色表示训练数据适用域，星形标出当前综合排序第一的候选，虚线为本轮采用的性能门槛。",
    "composition_percentiles": "上半部分展示最优候选的精确元素原子百分比，下半部分展示保留候选的 P5–P50–P95 探索区间；两者不能混作最终配方。",
    "microstructure_tendency": "根据当前候选的 SS、IM 与 SS+IM 相分类概率生成的组织倾向示意；风险标记只表达分类风险层级，不对应真实析出相的位置、尺寸或数量。",
    "hot_end_screening_funnel": "展示满足当前成分约束的候选数量如何收缩为优先短名单，用于直观看到本轮筛选范围。",
    "hot_end_strength_life_tradeoff": "展示指定温度、载荷和热处理下的短时抗拉强度与预测蠕变断裂寿命的相对位置；星形为当前综合优先项。",
    "hot_end_composition_traceability": "多个参考合金同时参与筛选时，并排展示优先候选及其参考合金的 wt.% 配方差异。",
    "rocket_strength_ductility_tradeoff": "每个点代表当前温度和固溶处理条件下的一个不锈钢候选；横轴为延伸率、纵轴为屈服强度，误差线为分组验证的典型筛选误差。",
    "rocket_screening_funnel": "展示当前成分边界内的候选如何收缩为训练成分邻域内、可优先验证的短名单。",
    "rocket_composition_comparison": "展示优先候选的元素质量百分比；Fe 是自动平衡元素。该图表示本次筛选配方，不是商品牌号或 30X 化学成分。",
    "glass_screening_funnel": "展示同家族来源锚点、局部氧化物扰动、性质门槛和优先短名单的实际保留数量。",
    "glass_cte_modulus_tradeoff": "每个点代表一个同家族玻璃候选；横轴为 CTE、纵轴为杨氏模量，星形为当前综合优先候选。",
    "glass_composition_traceability": "并排展示优先候选与其可追溯来源锚点的氧化物 mol% 配方；用于复核局部调整，不是全球玻璃成分窗口。",
}
ASSET_TITLES = {
    "screening_funnel": "候选筛选漏斗",
    "strength_hardness_tradeoff": "强度、硬度与最优候选位置",
    "composition_percentiles": "最优候选配方与探索区间",
    "microstructure_tendency": "预测组织倾向示意图",
    "hot_end_screening_funnel": "高温镍基候选筛选路径",
    "hot_end_strength_life_tradeoff": "短时强度—蠕变寿命对比",
    "hot_end_composition_traceability": "优先候选的配方调整对照",
    "rocket_strength_ductility_tradeoff": "不锈钢候选的强度—延性取舍",
    "rocket_screening_funnel": "可回收火箭不锈钢候选筛选路径",
    "rocket_composition_comparison": "优先候选成分（wt.%）",
    "glass_screening_funnel": "玻璃基板候选筛选路径",
    "glass_cte_modulus_tradeoff": "CTE—杨氏模量取舍",
    "glass_composition_traceability": "优先候选与来源锚点的配方对照",
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
        await websocket.send_text("\n图片发布失败，已改用本服务任务资产链接继续展示。\n")
    else:
        print(f"[ALLOY][{taskid}] published PNG assets count={len(public_urls)} names={sorted(public_urls)}", flush=True)
        logger.info("published %s alloy PNG asset(s) taskid=%s", len(public_urls), taskid)
    visual_assets = [{"name": item["name"], "url": public_urls[item["name"]], "title": ASSET_TITLES.get(item["name"], item["name"]), "description": ASSET_DOCS.get(item["name"], "")} for item in result["presentation"]["assets"] if item["name"] in public_urls]
    return public_urls, ASSET_DOCS, ASSET_TITLES, visual_assets
