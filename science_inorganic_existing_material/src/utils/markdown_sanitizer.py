# -*- coding: utf-8 -*-
"""Markdown text sanitizers shared by websocket and streaming outputs."""

from __future__ import annotations

import re

_CURRENCY_SKIP_PATTERN = re.compile(r"(```.*?```|`[^`\n]*`|\$\$.*?\$\$)", re.DOTALL)


def normalize_currency_symbols_for_markdown(text: str) -> str:
    """Avoid front-end confusion between currency dollars and LaTeX delimiters.

    Only plain-text segments are normalized. Markdown code blocks, inline code,
    LaTeX blocks, and escaped dollar signs are left untouched.
    """
    if not isinstance(text, str) or "$" not in text:
        return text

    def _normalize_plain_segment(segment: str) -> str:
        s = segment
        s = re.sub(r"(人民币|RMB|CNY)\s*(?<!\\)\$\s*(\d+(?:\.\d+)?)", r"\2 CNY", s, flags=re.IGNORECASE)
        s = re.sub(r"(美元|美金|USD)\s*(?<!\\)\$\s*(\d+(?:\.\d+)?)", r"\2 USD", s, flags=re.IGNORECASE)
        s = re.sub(r"(人民币|RMB|CNY)\s*(?<!\\)\$", "CNY", s, flags=re.IGNORECASE)
        s = re.sub(r"(美元|美金|USD)\s*(?<!\\)\$", "USD", s, flags=re.IGNORECASE)
        s = re.sub(r"(?<!\\)\$\s*(\d+(?:\.\d+)?)", r"\1 USD", s)
        s = re.sub(r"(\d+(?:\.\d+)?)\s*(?<!\\)\$(?=\s*(?:/|\)|，|,|。|；|;|\s|$))", r"\1 USD", s)
        s = re.sub(r"(?<!\\)\$\s*(?=/)", "USD", s)
        return s

    parts = _CURRENCY_SKIP_PATTERN.split(text)
    return "".join(
        part if _CURRENCY_SKIP_PATTERN.fullmatch(part or "") else _normalize_plain_segment(part)
        for part in parts
    )
