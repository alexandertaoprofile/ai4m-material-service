"""Customer-facing conclusion narration with optional LLM token streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _config() -> dict[str, Any]:
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


async def _stream_text(websocket, text: str, delay: float = 0.012) -> str:
    """Visible incremental fallback when the configured LLM is unavailable."""
    for index in range(0, len(text), 24):
        chunk = text[index:index + 24]
        await websocket.send_text(chunk)
        await asyncio.sleep(delay)
    return text


async def stream_markdown_rows(websocket, markdown: str) -> None:
    """Stream headings and every Markdown table row as separate WS messages."""
    delay = float(os.getenv("MATURE_MATERIAL_TABLE_ROW_DELAY_SECONDS", "0.08"))
    for line in markdown.splitlines():
        await websocket.send_text(line + "\n")
        # A blank line or a table row is a useful rendering boundary in the
        # existing frontend. Small pacing keeps the table visibly streaming.
        if not line or line.startswith("|"):
            await asyncio.sleep(delay)


async def stream_authoritative_markdown(websocket, markdown: str, *, section: str) -> str:
    """Relay a compact Markdown block through the LLM token stream unchanged.

    The frontend assembles token-streamed Markdown into one renderable table.
    The prompt makes the LLM a formatter/relay only: catalogue names, values,
    units and test conditions must not be rewritten.  If that stream is not
    available, send one complete Markdown block rather than isolated table
    rows, so a client can still parse the table consistently.
    """
    if os.getenv("MATURE_MATERIAL_LLM_STREAM", "true").lower() not in {"1", "true", "yes"}:
        await websocket.send_text(markdown.rstrip() + "\n")
        return markdown
    config = _config()
    base_url = os.getenv("MATURE_MATERIAL_LLM_BASE_URL") or config.get("base_url_1")
    api_key = os.getenv("MATURE_MATERIAL_LLM_API_KEY") or config.get("api_key")
    if not base_url or not api_key:
        logger.warning("[mature-llm] %s relay unavailable; sending complete Markdown", section)
        await websocket.send_text(markdown.rstrip() + "\n")
        return markdown
    prompt = (
        "你是工业材料服务的 Markdown 表格转发器。请逐字输出 <TABLE> 内的内容，"
        "不得增加标题、解释、代码围栏或空行，不得改写、删减、翻译任何材料名、数值、单位、范围和测试条件。"
        "每一条性质已经独立成行；绝不能合并单元格或把表格改成列表。\n"
        f"<TABLE>\n{markdown.rstrip()}\n</TABLE>"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        stream = await client.chat.completions.create(
            model=os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[
                {"role": "system", "content": "只原样输出 TABLE 内的 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=max(700, min(3000, len(markdown) * 2)),
            stream=True,
            timeout=float(os.getenv("MATURE_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        parts: list[str] = []
        logger.info("[mature-llm] streaming authoritative %s table", section)
        async for chunk in stream:
            token = chunk.choices[0].delta.content if chunk.choices else None
            if token:
                parts.append(token)
                await websocket.send_text(token)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("LLM returned no table text")
        return text
    except Exception as exc:
        logger.warning("[mature-llm] %s relay failed (%s); sending complete Markdown", section, exc)
        await websocket.send_text(markdown.rstrip() + "\n")
        return markdown


async def stream_customer_conclusion(websocket, result: dict[str, Any]) -> str:
    """Stream an LLM-written conclusion; fall back to factual incremental text."""
    candidates = result.get("results", [])
    eligible = sum(bool(item.get("eligible")) for item in candidates)
    if result.get("llm_fallback"):
        fallback = (
            "当前结构化目录没有可核验的对应记录。下方内容是基于需求文本生成的 LLM 托底建议，"
            "不是目录检索结果，也不能替代原厂数据表、标准或实验记录。"
        )
    elif result.get("recommendation"):
        fallback = (
            f"本轮未找到名称的精确匹配，系统仅从已入库目录中列出 {len(candidates)} 种参考材料供后续核验。"
            f"{result.get('data_status', {}).get('message', '')}"
            "这些条目不代表名称匹配或性能达标；请结合目标工况、材料状态和具体性质阈值进一步确认。"
        )
    else:
        fallback = (
            f"本轮从已入库目录中比较了 {len(candidates)} 种候选材料，其中 {eligible} 种满足当前可比较的条件。"
            f"{result.get('data_status', {}).get('message', '')}"
            "建议结合目标工况和材料状态进一步确认；未收录、温度不适用或来源不足的数据不应直接用于选型。"
        )
    if os.getenv("MATURE_MATERIAL_LLM_STREAM", "true").lower() not in {"1", "true", "yes"}:
        logger.info("[mature-llm] disabled; streaming deterministic conclusion")
        return await _stream_text(websocket, fallback)
    config = _config()
    base_url = os.getenv("MATURE_MATERIAL_LLM_BASE_URL") or config.get("base_url_1")
    api_key = os.getenv("MATURE_MATERIAL_LLM_API_KEY") or config.get("api_key")
    if not base_url or not api_key:
        logger.warning("[mature-llm] configuration unavailable; using deterministic conclusion")
        return await _stream_text(websocket, fallback)
    prompt = (
        "你是面向工业客户的材料数据顾问。基于下列已核验事实，输出一段不超过180字的中文结论。"
        "语言自然、克制、可执行；不要出现内部系统术语，不要编造数值、来源或结论；"
        "必须说明结果仅针对已入库数据及其材料状态/温度条件。\n\n"
        f"上游任务背景：{str(result.get('constraints', {}).get('upstream_context') or '未提供')[:600]}\n\n"
        f"已核验事实：{fallback}"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        stream = await client.chat.completions.create(
            model=os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=350,
            stream=True,
            timeout=float(os.getenv("MATURE_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        parts: list[str] = []
        logger.info("[mature-llm] streaming customer conclusion model=%s", os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"))
        async for chunk in stream:
            token = chunk.choices[0].delta.content if chunk.choices else None
            if token:
                parts.append(token)
                await websocket.send_text(token)
        text = "".join(parts).strip()
        if text:
            return text
        raise RuntimeError("LLM returned no text")
    except Exception as exc:
        logger.warning("[mature-llm] stream failed (%s); using deterministic conclusion", exc)
        return await _stream_text(websocket, fallback)


async def recommend_catalog_material_ids(
    requirement: str, catalogue: list[dict[str, str]], *, max_items: int = 3
) -> list[str]:
    """Select fallback candidates strictly from the already-ingested catalogue.

    This is not web search and it must never manufacture a material record.
    The model receives a compact, authoritative catalogue and may return only
    IDs from that catalogue.  An empty selection is the correct answer when
    the current catalogue does not contain a meaningful reference material.
    """
    if not catalogue or os.getenv("MATURE_MATERIAL_LLM_RECOMMEND", "true").lower() not in {"1", "true", "yes"}:
        return []
    config = _config()
    base_url = os.getenv("MATURE_MATERIAL_LLM_BASE_URL") or config.get("base_url_1")
    api_key = os.getenv("MATURE_MATERIAL_LLM_API_KEY") or config.get("api_key")
    if not base_url or not api_key:
        logger.info("[mature-llm] catalogue recommendation unavailable: no LLM configuration")
        return []
    allowed_ids = {str(item.get("material_id")) for item in catalogue if item.get("material_id")}
    if not allowed_ids:
        return []
    compact_catalogue = [
        {
            "material_id": item.get("material_id"),
            "名称": item.get("display_name"),
            "材料族": item.get("family"),
            "牌号或标准": item.get("grade") or item.get("UNS/standard"),
            "状态": item.get("product_state"),
            "数据用途": item.get("data_role"),
            "温度覆盖": item.get("temperature_coverage"),
        }
        for item in catalogue
    ]
    prompt = (
        "你是已有材料目录的受限检索助手。用户提供的名称没有命中目录时，判断目录中是否存在可作为后续核验参考的材料。"
        "你只能从下面目录选择，不能使用外部知识、不能编造材料、不能把参考推荐说成名称匹配或性能满足。"
        f"最多选择 {max_items} 项；若目录没有与需求相关的材料，返回空数组。\n"
        "只输出 JSON，格式为 {\"material_ids\":[\"目录中的 material_id\"]}。\n\n"
        f"需求：{requirement[:1800]}\n\n目录：{json.dumps(compact_catalogue, ensure_ascii=False)}"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[
                {"role": "system", "content": "只输出合法 JSON，不要输出 Markdown 或解释。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=180,
            timeout=float(os.getenv("MATURE_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        text = (response.choices[0].message.content if response.choices else "") or ""
        payload = json.loads(text.strip())
        selected = payload.get("material_ids", []) if isinstance(payload, dict) else []
        if not isinstance(selected, list):
            return []
        result = []
        for material_id in selected:
            material_id = str(material_id)
            if material_id in allowed_ids and material_id not in result:
                result.append(material_id)
            if len(result) >= max_items:
                break
        logger.info("[mature-llm] catalogue fallback selected=%s", result)
        return result
    except Exception as exc:
        logger.warning("[mature-llm] catalogue fallback failed (%s)", exc)
        return []


async def generate_llm_material_fallback(requirement: str) -> dict[str, Any] | None:
    """Generate a clearly-labelled advisory when the local catalogue is empty.

    This is deliberately an LLM reasoning fallback, not a network or database
    query.  It never creates catalogue records and is presented separately from
    verified material data.
    """
    if os.getenv("MATURE_MATERIAL_LLM_FALLBACK", "true").lower() not in {"1", "true", "yes"}:
        return None
    config = _config()
    base_url = os.getenv("MATURE_MATERIAL_LLM_BASE_URL") or config.get("base_url_1")
    api_key = os.getenv("MATURE_MATERIAL_LLM_API_KEY") or config.get("api_key")
    if not base_url or not api_key:
        logger.info("[mature-llm] advisory fallback unavailable: no LLM configuration")
        return None
    disclaimer = "此为 LLM 托底建议，需以原厂数据表、标准或实验记录二次确认。"
    prompt = (
        "你是一名材料科学、工程应用与商品材料选型方向的 AI Scientist。"
        "当前本地已有材料目录没有可核验的命中。请根据用户需求给出最多 3 条后续选材线索，"
        "帮助用户明确应向供应商或实验室确认什么，而不是替代数据库检索。\n\n"
        "严格输出以下 Markdown 结构，不要输出分析过程：\n"
        "| 建议材料/材料类别 | 可关注的商品牌号或形态 | 与需求的关系 | 需由数据表确认的关键性质 |\n"
        "|---|---|---|---|\n"
        "| ... | ... | ... | ... |\n\n"
        f"最后单独一行必须原样输出：{disclaimer}\n\n"
        "约束：只能基于通用材料知识和需求文本推理；不得声称已联网、已查询数据库、已入库、"
        "已找到原厂数据或已验证性能；没有充分依据时写“待确认”；不要编造精确数值、标准号、"
        "供应商型号或材料性能。\n\n"
        f"用户需求：{requirement[:1800]}"
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
            timeout=float(os.getenv("MATURE_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        markdown = ((response.choices[0].message.content if response.choices else "") or "").strip()
        if not markdown or "|" not in markdown:
            raise RuntimeError("LLM fallback returned no Markdown table")
        if disclaimer not in markdown:
            markdown += f"\n\n{disclaimer}"
        logger.info("[mature-llm] advisory fallback generated chars=%s", len(markdown))
        return {
            "mode": "llm_material_advice",
            "markdown": markdown,
            "message": "当前目录未找到可核验记录；以下为 LLM 托底建议，不属于已入库材料数据。",
        }
    except Exception as exc:
        logger.warning("[mature-llm] advisory fallback failed (%s)", exc)
        return None
