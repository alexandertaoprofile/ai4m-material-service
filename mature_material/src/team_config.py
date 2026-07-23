"""Service orchestration for the mature-material catalogue.

This module is deliberately independent of the historical Alpha framework.
``main.py`` owns HTTP/WebSocket transport; this module owns the material-query
workflow from normalized request through manifest and presentation assets.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.catalog.narration import (
    generate_llm_material_fallback,
    recommend_catalog_material_ids,
)
from src.catalog.presentation import (
    comparison_markdown,
    conclusion_markdown,
    render_property_comparison,
    resolution_markdown,
)
from src.catalog.query import MatureMaterialCatalog, parse_property_constraints


class MatureMaterialCatalogQuery:
    """Describe the deterministic existing-material lookup action."""

    name: str = "MatureMaterialCatalogQuery"
    desc: str = (
        "在已清洗的成熟材料目录中，按名称、牌号、标准、材料族、温度和性质条件检索，"
        "返回来源可追溯的性质证据、候选对比和数据缺口；不生成或模拟新材料。"
    )

    async def run(self, instruction: str, *args, **kwargs) -> str:
        return (
            "已有材料查询由 mature_material 的 /start 或 /mature-material/query 执行。"
            "请提供材料名称/牌号、服役温度及需要核验的性质条件。"
        )


class MaterialMature:
    """Orchestrate one traceable mature-material catalogue query."""

    name: str = "MaterialMature"
    profile: str = (
        "已有成熟材料数据库检索与性质核验智能体。面向已入库的商品材料、牌号和标准号，"
        "核验材料状态、温度范围、性质值与来源，并输出候选比较。"
        "边界：仅查询本服务已清洗的结构化目录；不进行材料生成、数值模拟或外部数据库检索，"
        "也不从未入库 PDF 或缺失数据推断性质。"
    )

    _EXECUTION_MARKER = re.compile(
        r"(?:接下来(?:需要)?进行执行的任务|接下来执行的任务|当前(?:需要)?执行任务|执行任务)\s*[：:]\s*",
        flags=re.IGNORECASE,
    )
    _NON_MATERIAL_TOKENS = frozenset({
        "XIMUALPHA", "LLM", "RAG", "PDF", "CIF", "MP", "HFE", "HTCC", "CTE", "IPC",
    })
    _MATERIAL_ACRONYMS = frozenset({"ABS", "ASA", "PA", "PEEK", "PEI", "PETG", "PLA", "PPS", "PTFE", "PVC"})

    def __init__(
        self,
        *,
        catalog_root: Path | str = "data/processed",
        raw_data_root: Path | str = ".",
        results_root: Path | str = "results/mature_material",
        service_name: str = "mature-material",
        **metadata: Any,
    ) -> None:
        self.catalog_root = Path(catalog_root)
        self.raw_data_root = Path(raw_data_root)
        self.results_root = Path(results_root)
        self.service_name = service_name
        self.metadata = metadata
        self.actions = [MatureMaterialCatalogQuery]

    @staticmethod
    def _taskid(payload: dict[str, Any]) -> str:
        taskid = str(payload.get("taskid") or f"mature-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
        if not taskid.strip() or len(taskid) > 512:
            raise ValueError("taskid must be a non-empty string no longer than 512 characters")
        return taskid

    @staticmethod
    def task_storage_key(taskid: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", taskid):
            return taskid
        return "opaque-" + hashlib.sha256(taskid.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value] if isinstance(value, list) else []

    @classmethod
    def _context_text(cls, value: Any, *, limit: int = 12000) -> str:
        fragments: list[str] = []

        def visit(item: Any) -> None:
            if len("\n".join(fragments)) >= limit:
                return
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    return
                try:
                    decoded = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    fragments.append(text)
                else:
                    visit(decoded)
            elif isinstance(item, dict):
                for key in ("idea", "content", "text", "query", "summary", "message", "requirement"):
                    if item.get(key) is not None:
                        visit(item[key])
                for key in ("messages", "history", "conversation", "upstream_context", "previous_results"):
                    if item.get(key) is not None:
                        visit(item[key])
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return "\n\n".join(fragments)[:limit]

    @classmethod
    def _upstream_context(cls, payload: dict[str, Any]) -> tuple[str, list[str]]:
        keys = [
            key for key in ("idea", "content", "query", "history", "messages", "conversation", "upstream_context", "previous_results")
            if payload.get(key) is not None
        ]
        return cls._context_text({key: payload[key] for key in keys}), keys

    @classmethod
    def _material_extraction_text(cls, text: str) -> str:
        matches = list(cls._EXECUTION_MARKER.finditer(text or ""))
        return (text or "")[matches[-1].end():].strip() if matches else (text or "").strip()

    @classmethod
    def _formula_like_terms(cls, text: str) -> list[str]:
        terms = re.findall(
            r"(?<![A-Za-z0-9])(?:[A-Z][a-z]?\d*(?:\.\d*)?){2,}(?![A-Za-z0-9])",
            cls._material_extraction_text(text),
        )
        return list(dict.fromkeys(
            term for term in terms
            if len(term) >= 3
            and term.upper() not in cls._NON_MATERIAL_TOKENS
            and (term.upper() in cls._MATERIAL_ACRONYMS or any(char.isdigit() for char in term) or any(char.islower() for char in term))
        ))

    def contract(self, payload: dict[str, Any]) -> dict[str, Any]:
        taskid = self._taskid(payload)
        scope = payload.get("mature_material") or payload.get("constraints") or {}
        if not isinstance(scope, dict):
            raise ValueError("mature_material must be an object")
        temperature_c = scope.get("service_temperature_C", scope.get("temperature_C"))
        try:
            default_temperature_K = float(temperature_c) + 273.15 if temperature_c is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("temperature_C must be numeric") from exc
        properties = scope.get("property_constraints", scope.get("property_filters", {}))
        upstream_context, upstream_keys = self._upstream_context(payload)
        raw_requirement = str(scope.get("query") or payload.get("idea") or upstream_context)
        return {
            "taskid": taskid,
            "raw_requirement": raw_requirement,
            "upstream_context": upstream_context,
            "upstream_context_keys": upstream_keys,
            "material_queries": self._as_list(scope.get("material_queries", scope.get("materials", scope.get("names", [])))),
            "material_families": self._as_list(scope.get("material_families", scope.get("families", []))),
            "standards": self._as_list(scope.get("standards", [])),
            "property_constraints": [item.__dict__ for item in parse_property_constraints(properties, default_temperature_K)],
            "service_temperature_K": default_temperature_K,
            "top_k": max(1, min(int(scope.get("top_k", 10)), 50)),
            "source_preference": str(scope.get("source_preference", "all")),
        }

    async def run(self, constraints: dict[str, Any]) -> dict[str, Any]:
        """Run one catalogue query without performing transport work."""
        catalog = MatureMaterialCatalog(self.catalog_root)
        if not catalog.ready:
            return {
                "taskid": constraints["taskid"],
                "status": "accepted_pending_catalog_ingestion",
                "service": self.service_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "constraints": constraints,
                "results": [],
                "data_status": {
                    "catalog_ready": False,
                    "raw_data_root_available": self.raw_data_root.exists(),
                    "message": "Structured catalogue is unavailable; raw PDFs were not queried.",
                },
            }
        parsed_constraints = parse_property_constraints(constraints["property_constraints"], constraints["service_temperature_K"])
        names = constraints["material_queries"] or catalog.aliases_mentioned_in(constraints["raw_requirement"])
        if not names:
            names = self._formula_like_terms(constraints["raw_requirement"])
        if names or constraints["material_families"] or constraints["standards"] or parsed_constraints:
            search = catalog.search(
                names=names,
                families=constraints["material_families"],
                standards=constraints["standards"],
                constraints=parsed_constraints,
                top_k=constraints["top_k"],
            )
        else:
            search = {"name_resolution": [], "candidates": []}
        recommendation: dict[str, Any] | None = None
        llm_fallback: dict[str, Any] | None = None
        if not search["candidates"]:
            selected_ids = await recommend_catalog_material_ids(
                constraints.get("upstream_context") or constraints.get("raw_requirement") or "",
                catalog.materials,
                max_items=min(3, constraints["top_k"]),
            )
            if selected_ids:
                selected_names = [catalog._by_id[material_id]["display_name"] for material_id in selected_ids]
                fallback = catalog.search(
                    names=selected_names,
                    families=[],
                    standards=[],
                    constraints=parsed_constraints,
                    top_k=constraints["top_k"],
                )
                if fallback["candidates"]:
                    search["candidates"] = fallback["candidates"]
                    recommendation = {
                        "mode": "catalog_llm_fallback",
                        "material_ids": selected_ids,
                        "message": "未找到名称的精确匹配；以下为模型仅从当前已入库目录选出的后续核验参考材料，并非名称匹配结果。",
                    }
        if not search["candidates"]:
            llm_fallback = await generate_llm_material_fallback(
                constraints.get("upstream_context") or constraints.get("raw_requirement") or ""
            )
        eligible = sum(item["eligible"] for item in search["candidates"])
        if recommendation:
            message = recommendation["message"]
        elif llm_fallback:
            message = llm_fallback["message"]
        elif not search["candidates"]:
            message = "目录中暂未找到与本轮指定材料、牌号或标准相匹配的已入库记录；未展示无关候选材料。"
        elif not parsed_constraints:
            message = f"已在结构化材料目录中匹配到 {len(search['candidates'])} 种候选；本轮未提供量化性质阈值。"
        else:
            message = f"已在结构化材料目录中评估 {len(search['candidates'])} 种候选，其中 {eligible} 种满足当前可比较的性质条件。"
        return {
            "taskid": constraints["taskid"],
            "status": "completed",
            "service": self.service_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "constraints": constraints,
            "results": search["candidates"],
            "name_resolution": search["name_resolution"],
            "recommendation": recommendation,
            "llm_fallback": llm_fallback,
            "data_status": {
                "catalog_ready": True,
                "raw_data_root_available": self.raw_data_root.exists(),
                "message": message,
                "scope": "仅查询已清洗的结构化目录数据；Markdown 解析数据将按来源和表格逐步入库。",
            },
        }

    @staticmethod
    def summary(result: dict[str, Any]) -> str:
        return "\n\n".join([resolution_markdown(result), comparison_markdown(result), conclusion_markdown(result)])

    def save(self, manifest: dict[str, Any]) -> None:
        path = self.results_root / self.task_storage_key(manifest["taskid"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_task(self, taskid: str) -> dict[str, Any] | None:
        path = self.results_root / self.task_storage_key(taskid) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def asset_path(self, taskid: str, asset_name: str) -> Path:
        if Path(asset_name).name != asset_name:
            raise ValueError("invalid asset path")
        return self.results_root / self.task_storage_key(taskid) / "presentation" / asset_name

    def render_assets(self, result: dict[str, Any]) -> list[dict[str, str]]:
        presentation_dir = self.results_root / self.task_storage_key(result["taskid"]) / "presentation"
        chart = render_property_comparison(result, presentation_dir)
        if not chart:
            return []
        has_property_constraint = bool(result.get("constraints", {}).get("property_constraints"))
        return [{
            "name": "property_comparison" if has_property_constraint else "catalog_coverage",
            "title": "候选材料性质对比" if has_property_constraint else "候选材料数据覆盖度",
            "description": "柱状图仅比较本轮有相同性质、单位和可比温度证据的候选。" if has_property_constraint else "未指定性质条件时，图表展示每个候选已有的可追溯性质种类数。",
            "local_path": str(chart),
            "url": "",
            "type": "MaterialsPNG",
        }]
