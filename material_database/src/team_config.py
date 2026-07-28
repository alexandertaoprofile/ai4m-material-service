"""成熟材料目录服务的编排层。

``main.py`` 负责 HTTP/WebSocket 传输与前端事件；本模块负责从需求规范化、
目录检索到 manifest 和展示资产准备的完整服务流程，不依赖历史 Alpha 链路。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.catalog.presentation import (
    comparison_markdown,
    conclusion_markdown,
    render_property_comparison,
    resolution_markdown,
)
from src.catalog.query import MatureMaterialCatalog, parse_property_constraints
from src.service_identity import ACTION_DESCRIPTION, ACTION_NAME, ROLE_NAME, ROLE_PROFILE


class MatureMaterialCatalogQuery:
    """描述确定性的已有成熟材料目录查询动作。"""

    name: str = ACTION_NAME
    desc: str = ACTION_DESCRIPTION

    async def run(self, instruction: str, *args, **kwargs) -> str:
        return (
            "已有材料查询由 mature_material 的 /start 或 /mature-material/query 执行。"
            "请提供材料名称、厂家/牌号、标准号，或包含性质、工况和来源的 upstream_evidence；"
            "若尚无材料证据，建议先进入文献筛选。"
        )


class MaterialMature:
    """编排一次可追溯的成熟材料目录查询。"""

    name: str = ROLE_NAME
    profile: str = ROLE_PROFILE

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
        # 阶段 1：规范化母服务/前端输入，只保留可核验的材料名称、牌号、标准、
        # 性质条件与上游证据；上游证据不能直接提升为目录事实。
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
        upstream_evidence = scope.get("upstream_evidence", payload.get("upstream_evidence", []))
        if isinstance(upstream_evidence, dict):
            upstream_evidence = [upstream_evidence]
        if not isinstance(upstream_evidence, list) or not all(isinstance(item, dict) for item in upstream_evidence):
            raise ValueError("upstream_evidence must be an object or a list of objects")
        material_queries = self._as_list(scope.get("material_queries", scope.get("materials", scope.get("names", []))))
        if not material_queries:
            for item in upstream_evidence:
                for key in ("material", "name", "grade", "standard"):
                    value = str(item.get(key) or "").strip()
                    if value and value not in material_queries:
                        material_queries.append(value)
        return {
            "taskid": taskid,
            "raw_requirement": raw_requirement,
            "upstream_context": upstream_context,
            "upstream_context_keys": upstream_keys,
            "material_queries": material_queries,
            "material_families": self._as_list(scope.get("material_families", scope.get("families", []))),
            "standards": self._as_list(scope.get("standards", [])),
            "property_constraints": [item.__dict__ for item in parse_property_constraints(properties, default_temperature_K)],
            "service_temperature_K": default_temperature_K,
            "top_k": max(1, min(int(scope.get("top_k", 10)), 50)),
            "source_preference": str(scope.get("source_preference", "all")),
            # 上游证据只原样保留和展示；没有目录匹配时绝不写成已核验事实。
            "upstream_evidence": upstream_evidence,
        }

    async def run(self, constraints: dict[str, Any]) -> dict[str, Any]:
        """阶段 2：执行目录检索与可比性判断，不处理任何传输协议。"""
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
        eligible = sum(item["eligible"] for item in search["candidates"])
        has_upstream_evidence = bool(constraints["upstream_evidence"])
        if not search["candidates"]:
            message = "目录中暂未找到与本轮指定材料、牌号或标准相匹配的已入库记录；未展示或推断替代候选材料。"
            outcome = "upstream_evidence_only" if has_upstream_evidence else "needs_literature_screening"
        elif not parsed_constraints:
            message = f"已在结构化材料目录中匹配到 {len(search['candidates'])} 种候选；本轮未提供量化性质阈值。"
            outcome = "catalog_matched"
        else:
            message = f"已在结构化材料目录中评估 {len(search['candidates'])} 种候选，其中 {eligible} 种满足当前可比较的性质条件。"
            outcome = "catalog_matched"
        return {
            "taskid": constraints["taskid"],
            "status": "completed",
            "service": self.service_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "constraints": constraints,
            "results": search["candidates"],
            "name_resolution": search["name_resolution"],
            "data_status": {
                "catalog_ready": True,
                "raw_data_root_available": self.raw_data_root.exists(),
                "outcome": outcome,
                "message": message,
                "scope": "仅查询已清洗的结构化目录数据；Markdown 解析数据将按来源和表格逐步入库。",
            },
        }

    @staticmethod
    def summary(result: dict[str, Any]) -> str:
        # 阶段 3：仅由已保存的结果生成可读结论，不补充目录外事实。
        return "\n\n".join([resolution_markdown(result), comparison_markdown(result), conclusion_markdown(result)])

    def save(self, manifest: dict[str, Any]) -> None:
        # 阶段 4：将可追溯结果落盘，供任务查询接口和展示层复用。
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
        # 阶段 5：生成目录事实的对比图；WebSocket/MinIO 发布仍属于 main.py 的适配层。
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
