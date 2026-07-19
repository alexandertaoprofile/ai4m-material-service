"""Frontend payload helpers for websocket messages."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, Optional


def build_payload(
    data: Any,
    type_: str = "chat",
    request_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pack service output into the frontend JSON envelope."""
    if request_id is None:
        request_id = str(uuid.uuid4())

    payload: Dict[str, Any] = {
        "version": "1.0.0",
        "agent": "inorganic_new_material",
        "request_id": request_id,
        "time": datetime.datetime.now().isoformat(),
        "type": type_,
        "data": data,
    }

    if meta:
        payload["meta"] = meta if isinstance(meta, dict) else {"raw_meta": str(meta)}

    try:
        keep_icon = bool(payload.get("meta", {}).get("ui", {}).get("keep_icon", False))
        if type_ == "progress" and isinstance(payload.get("data"), dict) and not keep_icon:
            payload["data"]["icon"] = ""
    except Exception:
        pass

    # The service has one frontend workflow step.  Normalize every progress
    # payload here so lower-level emitters cannot drift from that contract.
    if type_ == "progress" and isinstance(payload.get("data"), dict):
        payload["data"].update({
            "id": "FILAMENT_SELECTION_OPTIMIZATION",  # legacy client field
            "stepId": "FILAMENT_SELECTION_OPTIMIZATION",
            "title": "耗材选型和计算优化",
            "teamType": "Robot_Materials",
        })

    try:
        max_chars = int(payload.get("meta", {}).get("ui", {}).get("max_text_chars", 6000))
        if type_ in ("chat", "error", "progress") and isinstance(payload.get("data"), str):
            if len(payload["data"]) > max_chars:
                payload["data"] = payload["data"][:max_chars] + "…"
    except Exception:
        pass

    return payload
