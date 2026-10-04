"""Compatibility adapter for the existing Alloy WebSocket event contract."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.alloy_workflow.assets import publish_png_assets


ASSET_DOCS = {
    "recipe_tradeoff": "比较本次优化目标的可行配方及优选方案。",
    "recipe_composition_0": "展示本次优选配方的组分比例；完整数值见组成表。",
    "recipe_composition_1": "展示另一独立组分位点的比例；各位点分别归一化。",
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
    "short_cf_screening_funnel": "展示短碳纤维 RVE 原始记录、输入预检、当前适用域、刚度矩阵正定性与优先短名单的实际保留数量。",
    "short_cf_stiffness_anisotropy": "每个点代表一个短纤维 RVE 候选；横轴为横向模量 E22、纵轴为纵向模量 E11，颜色表示各向异性比 E11/E22。",
    "short_cf_constitutive_card": "展示当前优先候选的六个弹性模量常数；完整 6×6 刚度矩阵与全部泊松比在结构化结果中可直接读取。",
    "perovskite_screening_funnel": "展示可追溯的钙钛矿训练组分如何按 220 K 与 300 K 的低电输运响应收缩为验证短名单。",
    "perovskite_transport_tradeoff": "每个点代表一个文档化组分；横轴为 300 K 的 ln[sigmaT]，越低表示当前输运代理下更优，星形为优先项。",
    "perovskite_response_curve": "展示优先候选在 170–330 K 的预测 ln[sigmaT] 曲线及经验 Q90 误差带；不是寿命曲线。",
    "aluminum_screening_funnel": "展示航空铝合金候选从局部 wt.% 成分生成、工艺类别匹配到训练成分邻域保留的数量；用于确认本轮没有把远域成分写成已验证候选。",
    "aluminum_strength_ductility_tradeoff": "每个点代表一个航空铝合金候选；横轴为预测延伸率、纵轴为预测 UTS，颜色表示预测 0.2% 屈服强度，星形为当前优先项。",
    "aluminum_composition_card": "展示优先候选的元素质量百分比；该成分是当前工艺类别与训练邻域内的试制优先项，不是已定型牌号或热处理制度。",
}
ASSET_TITLES = {
    "recipe_tradeoff": "目标性质权衡与优选方案",
    "recipe_composition_0": "优选配方组成",
    "recipe_composition_1": "卤素位组成",
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
    "short_cf_screening_funnel": "短碳纤维复合材料候选筛选路径",
    "short_cf_stiffness_anisotropy": "纵向刚度—各向异性取舍",
    "short_cf_constitutive_card": "优先候选的工程弹性常数",
    "perovskite_screening_funnel": "钙钛矿候选筛选路径",
    "perovskite_transport_tradeoff": "电输运响应—有效 Ea 取舍",
    "perovskite_response_curve": "优先候选的变温电输运响应",
    "aluminum_screening_funnel": "航空铝合金候选筛选路径",
    "aluminum_strength_ductility_tradeoff": "强度—延伸率候选比较",
    "aluminum_composition_card": "优先候选成分卡（wt.%）",
}
logger = logging.getLogger("alloy.protocol")


def result_event_data(result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, non-renderable handoff for the terminal WS event.

    The complete numerical result is retained in the task manifest/API and the
    customer sees the complete Markdown report in the content stream.  Sending
    the entire nested result on a chat-facing ``result`` event causes some
    Alpha parent views to stringify internal fields such as ``model_evidence``
    and ``presentation`` into the conversation.
    """
    candidates = result.get("initial_candidates") or result.get("nearest_candidates") or []
    top = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    sampling = result.get("sampling") or {}
    taskid = str(result.get("taskid") or "")
    return {
        "taskid": taskid,
        "status": result.get("status", "completed"),
        "service": result.get("service", "alloy-composition-optimization"),
        "model_domain": result.get("model_domain"),
        "model_version": result.get("model_version"),
        "recipe_release": result.get("recipe_release"),
        "user_conclusion": result.get("user_conclusion"),
        "sampling": {
            "generated": sampling.get("generated"),
            "feasible": sampling.get("feasible"),
        },
        "priority_candidate_id": top.get("candidate_id") or top.get("formula"),
        "full_result_endpoint": f"/alloy/tasks/{taskid}" if taskid else None,
    }


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
    titles, docs = dict(ASSET_TITLES), dict(ASSET_DOCS)
    report = result.get("_recipe_report") or {}
    if not report.get("candidates") and report.get("nearest_candidates"):
        titles.update(recipe_tradeoff="目标性质与接近要求的方案", recipe_composition_0="接近要求的配方组成")
        docs.update(recipe_tradeoff="比较当前计算中接近性能要求的配方；未满足项见要求对照表。",
                    recipe_composition_0="展示接近要求的配方组分比例；完整组成和性能缺口见表格。")
    if result.get('recipe_release', {}).get('mode') == 'primary':
        selected = result.get('initial_candidates') or result.get('nearest_candidates') or []
        if selected:
            label = ('优选配方' if result.get('initial_candidates') else '接近目标配方') + '（' + str(selected[0]['candidate_id']) + '）'
            titles.update(recipe_tradeoff='目标性能比较与'+label,
                          recipe_composition_0=label+'的完整配比',
                          recipe_composition_1=label+'的卤素位组成')
            docs.update(recipe_tradeoff='比较各配方的目标性能，星形标出'+label+'。',
                        recipe_composition_0='展示'+label+'的组分比例；具体含量见配比表。')
    visual_assets = [{"name": item["name"], "url": public_urls[item["name"]], "title": titles.get(item["name"], item["name"]), "description": docs.get(item["name"], "")} for item in result["presentation"]["assets"] if item["name"] in public_urls]
    return public_urls, docs, titles, visual_assets
