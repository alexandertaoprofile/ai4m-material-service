"""HTTP transport for the deterministic fluid initial-screening engine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.fluid_lubricant.presentation import render_assets
from src.fluid_lubricant.query import run_query, save_result


def router_for(database: Path, results_root: Path) -> APIRouter:
    router = APIRouter(prefix="/fluid-initial-screen", tags=["fluid-initial-screen"])

    @router.post("/query")
    def query(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = run_query(database, payload)
            task_dir = results_root / result["task_id"]
            save_result(result, task_dir)
            assets = render_assets(result, task_dir / "assets")
            for asset in assets:
                asset["url"] = f"/fluid-initial-screen/tasks/{result['task_id']}/assets/{Path(asset['local_path']).name}"
                asset.pop("local_path", None)
            (task_dir / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
            result["assets"] = assets
            save_result(result, task_dir)
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/tasks/{task_id}")
    def task(task_id: str) -> dict[str, Any]:
        path = results_root / task_id / "summary.json"
        if not path.is_file():
            raise HTTPException(404, "fluid screening result not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @router.get("/tasks/{task_id}/assets/{asset_name}")
    def asset(task_id: str, asset_name: str):
        if Path(task_id).name != task_id or Path(asset_name).name != asset_name:
            raise HTTPException(422, "invalid task or asset name")
        path = results_root / task_id / "assets" / asset_name
        if path.suffix.lower() != ".png" or not path.is_file():
            raise HTTPException(404, "asset not found")
        return FileResponse(path, media_type="image/png", content_disposition_type="inline")

    return router
