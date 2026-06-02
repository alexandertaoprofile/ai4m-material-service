import datetime
import uuid


def build_payload(data, type_: str = "chat", request_id: str = None, meta: dict = None) -> dict:
    """
    将任意输出打包成统一 JSON 格式，供前端解析。

    - 支持 meta.ui.hidden: True  -> 前端可忽略（你服务端仍可记录日志）
    - 支持 meta.ui.level: "debug"/"info" -> 前端可按级别过滤（需要前端配合）
    - progress 的 icon 允许在后续统一清空（你也可在这里直接清空）
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    payload = {
        "version": "1.0.0",
        "agent": "XIMUAlpha_MNS",
        "request_id": request_id,
        "time": datetime.datetime.now().isoformat(),
        "type": type_,
        "data": data
    }

    if meta:
        # 兜底：避免 meta 不是 dict
        payload["meta"] = meta if isinstance(meta, dict) else {"raw_meta": str(meta)}

    # ✅ 可选：默认把 progress 的 icon 清空（你说“卡通图标不需要这么频繁”）
    # 如果你仍想保留少数关键步骤的 icon，可以在 meta 里显式写 ui.keep_icon=True
    try:
        keep_icon = bool(payload.get("meta", {}).get("ui", {}).get("keep_icon", False))
        if type_ == "progress" and isinstance(payload.get("data"), dict) and not keep_icon:
            payload["data"]["icon"] = ""
    except Exception:
        pass

    # ✅ 可选：截断过长文本（避免 summary/md 或 LLM 输出把前端刷爆）
    # 这里不改 image / parameters
    try:
        max_chars = int(payload.get("meta", {}).get("ui", {}).get("max_text_chars", 6000))
        if type_ in ("chat", "error", "progress") and isinstance(payload.get("data"), str):
            if len(payload["data"]) > max_chars:
                payload["data"] = payload["data"][:max_chars] + "…"
    except Exception:
        pass

    return payload
