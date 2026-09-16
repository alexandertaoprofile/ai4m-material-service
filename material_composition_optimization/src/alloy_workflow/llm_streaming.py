"""Alloy-owned WebSocket streaming helper for presentation-only LLM output.

This is intentionally separate from ``material_workflow``: the alloy service
may share a transport pattern with other services, but it must not depend on
the inorganic generation workflow to render an already-computed alloy result.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from starlette.websockets import WebSocketDisconnect


logger = logging.getLogger(__name__)


async def _send_text(websocket: Any, text: str) -> bool:
    """Send one frame, treating a normal client disconnect as terminal."""
    if websocket is None:
        return False
    try:
        await websocket.send_text(text)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if "close message has been sent" in str(exc):
            return False
        raise


async def stream_llm_response(llm: Any, messages: list[dict[str, str]], websocket: Any) -> str:
    """Forward an OpenAI-compatible stream without altering its text.

    The caller owns ``<<<CONTENT_START/END>>>`` markers.  Keeping them out of
    this helper prevents a second, nested presentation protocol.
    """
    stream = await llm.acompletion_text(messages, timeout=30)
    chunks: list[str] = []
    opening_buffer = ""
    opening_pending = True

    def clean_opening(text: str) -> str:
        """Drop a known relay artefact without touching Markdown body text."""
        return re.sub(r"^\s*\}\}\s*(?=#+\s)", "", text)

    async def forward(text: str) -> bool:
        nonlocal opening_buffer, opening_pending
        if opening_pending:
            opening_buffer += text
            # Wait briefly so a split ``}}`` + ``###`` artefact can be
            # recognised before either fragment reaches the frontend.
            compact = opening_buffer.lstrip()
            if len(compact) < 4 and not compact.startswith("#"):
                return True
            opening_pending = False
            text = clean_opening(opening_buffer)
            opening_buffer = ""
        if not text:
            return True
        chunks.append(text)
        return await _send_text(websocket, text)

    iterator = stream.__aiter__()
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=30)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                await _send_text(websocket, "\n❗ 大模型响应超时，已收集部分结果。\n")
                break
            text = ""
            try:
                choices = getattr(chunk, "choices", None) or []
                delta = getattr(choices[0], "delta", None) if choices else None
                text = getattr(delta, "content", "") or ""
            except (AttributeError, IndexError, TypeError):
                logger.warning("unable to read an LLM stream chunk", exc_info=True)
            if text:
                if not await forward(text):
                    break
    finally:
        if opening_pending and opening_buffer:
            trailing = clean_opening(opening_buffer)
            if trailing:
                chunks.append(trailing)
                await _send_text(websocket, trailing)
        close = getattr(stream, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.debug("could not close LLM stream", exc_info=True)
    return "".join(chunks)
