"""Frontend-safe authoritative Markdown relay.

Production uses the same token-stream pattern as ports 1111 and 1115: the
renderer is asked to preserve the already-computed Markdown verbatim.  The
small deterministic fallback exists solely for local contract tests before an
LLM relay is configured; it never changes scientific values.
"""
from __future__ import annotations

import os
from typing import Any


async def _fallback_stream(websocket: Any, markdown: str) -> None:
    # Avoid one oversized text frame in local development too.
    for paragraph in markdown.split("\n\n"):
        await websocket.send_text(paragraph + "\n\n")


async def stream_authoritative_markdown(websocket: Any, markdown: str, *, step_id: str) -> None:
    """Relay pre-rendered Markdown without letting prose generation alter facts."""
    await websocket.send_text(f"<<<CONTENT_START:{step_id}>>>")
    try:
        base_url = os.getenv("LLM_BASE_URL", "").strip()
        api_key = os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        if not (base_url and api_key and model):
            await _fallback_stream(websocket, markdown)
            return
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        prompt = (
            "逐字输出以下 Markdown，不得改写、翻译、增删或解释。"
            "必须保留所有表格、公式、链接、数值和换行。\n\n" + markdown
        )
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": "You are a verbatim Markdown relay."}, {"role": "user", "content": prompt}],
            stream=True,
            temperature=0,
        )
        async for chunk in stream:
            text = (chunk.choices[0].delta.content if chunk.choices else None) or ""
            if text:
                await websocket.send_text(text)
    finally:
        await websocket.send_text(f"<<<CONTENT_END:{step_id}>>>")
