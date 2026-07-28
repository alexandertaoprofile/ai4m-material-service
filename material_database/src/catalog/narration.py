"""Streaming helpers for factual mature-material catalogue output."""
from __future__ import annotations

import asyncio
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


async def stream_markdown_rows(websocket, markdown: str) -> None:
    """Stream headings and every Markdown table row as existing WS text events."""
    delay = float(os.getenv("MATURE_MATERIAL_TABLE_ROW_DELAY_SECONDS", "0.08"))
    for line in markdown.splitlines():
        await websocket.send_text(line + "\n")
        if not line or line.startswith("|"):
            await asyncio.sleep(delay)


async def stream_authoritative_markdown(websocket, markdown: str, *, section: str) -> str:
    """Token-stream factual Markdown without allowing the model to alter it.

    The configured LLM is only a streaming relay. If unavailable, the same
    Markdown is sent directly through the existing WebSocket text channel.
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
        "你是工业材料服务的 Markdown 转发器。请逐字输出 <CONTENT> 内的内容，"
        "不得增加标题、解释、代码围栏或空行，不得改写、删减、翻译任何材料名、数值、单位、"
        "来源、工况、状态或下一步建议。\n"
        f"<CONTENT>\n{markdown.rstrip()}\n</CONTENT>"
    )
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        stream = await client.chat.completions.create(
            model=os.getenv("MATURE_MATERIAL_LLM_MODEL", "SE_V0.0"),
            messages=[
                {"role": "system", "content": "只原样输出 CONTENT 内的 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=max(700, min(3000, len(markdown) * 2)),
            stream=True,
            timeout=float(os.getenv("MATURE_MATERIAL_LLM_TIMEOUT_SECONDS", "20")),
        )
        parts: list[str] = []
        logger.info("[mature-llm] streaming authoritative %s", section)
        async for chunk in stream:
            token = chunk.choices[0].delta.content if chunk.choices else None
            if token:
                parts.append(token)
                await websocket.send_text(token)
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("LLM returned no Markdown")
        return text
    except Exception as exc:
        logger.warning("[mature-llm] %s relay failed (%s); sending complete Markdown", section, exc)
        await websocket.send_text(markdown.rstrip() + "\n")
        return markdown
