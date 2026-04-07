# src/utils/ui_emitter.py

class UIEmitter:
    """
    统一前端输出入口：
    - debug 信息只进日志（verbose=False 时不发前端）
    - progress 可统一开关
    """
    def __init__(self, websocket, logger, *, verbose=False, progress=True, max_text_chars=6000):
        self.ws = websocket
        self.logger = logger
        self.verbose = bool(verbose)
        self.progress = bool(progress)
        self.max_text_chars = int(max_text_chars or 6000)

    async def text(self, msg: str, *, level: str = "info"):
        msg = (msg or "").strip()
        if not msg:
            return

        # 日志永远记录
        if level == "debug":
            self.logger.info(f"[UI-DEBUG] {msg}")
            if self.verbose and self.ws:
                await self.ws.send_text((msg[: self.max_text_chars] + "\n"))
            return

        self.logger.info(f"[UI] {msg}")
        if self.ws:
            await self.ws.send_text((msg[: self.max_text_chars] + "\n"))

    async def json(self, payload: dict, *, level: str = "info"):
        # 日志永远记录摘要
        try:
            t = payload.get("type") if isinstance(payload, dict) else None
            did = (payload.get("data") or {}).get("id") if isinstance(payload, dict) else None
            self.logger.info(f"[UI-JSON] type={t!r} data.id={did!r} level={level}")
        except Exception:
            pass

        if level == "debug" and not self.verbose:
            return

        if self.ws:
            await self.ws.send_json(payload)
