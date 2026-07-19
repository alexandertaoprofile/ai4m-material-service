from __future__ import annotations

import re

_LATEX_BLOCK_HINT = re.compile(r"(\\begin\{|\\end\{|\\frac|\\nabla|\\partial|\\cdot|\\times|\\Omega|\\sigma|\\varepsilon)")
_TABLE_HINT = re.compile(r"^\s*\|")  # markdown table 行（我们要避免误包）

def wrap_latex_if_needed(s: str) -> str:
    s2 = (s or "").strip()
    if not s2:
        return s or ""

    # 已经是数学环境就不动
    if s2.startswith("$$") or s2.startswith("\\[") or s2.startswith("\\("):
        return s2

    # 像表格就别包
    if "|" in s2 or _TABLE_HINT.search(s2):
        return s2

    # 仅当“看起来是 LaTeX 块”才包
    if _LATEX_BLOCK_HINT.search(s2):
        slash_cnt = s2.count("\\")
        alpha_cnt = sum(ch.isalpha() for ch in s2)
        if slash_cnt >= 2 and alpha_cnt / max(len(s2), 1) < 0.55:
            return "$$\n" + s2 + "\n$$"

    return s2


async def ws_send_text_safe(websocket, text: str):
    """WebSocket 发送兜底：断开/异常时不再抛出，避免影响主流程"""
    if websocket is None:
        return
    try:
        # 如果你不想自动包 LaTeX，可以把下一行改成：payload = text
        payload = wrap_latex_if_needed(text)
        await websocket.send_text(payload)
    except Exception:
        pass