"""Deterministic query engine for the fluid-property evidence store.

Natural-language understanding belongs outside this module.  Callers must pass
the validated request shape below, so every candidate and chart can be traced
to explicit conditions rather than to an implicit model judgement.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


PROPERTY_COLUMNS = {
    "conductivity": ("conductivity_s_m_min", "conductivity_s_m_max", "S/m"),
    "dynamic_viscosity": ("dynamic_viscosity_mpa_s_min", "dynamic_viscosity_mpa_s_max", "mPa*s"),
    "resistivity": ("resistivity_ohm_m_min", "resistivity_ohm_m_max", "ohm*m"),
}


@dataclass(frozen=True)
class Constraint:
    name: Literal["conductivity", "dynamic_viscosity", "resistivity"]
    operator: Literal[">=", ">", "<=", "<"]
    value: float
    unit: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Constraint":
        try:
            constraint = cls(name=str(value["name"]), operator=str(value["operator"]), value=float(value["value"]), unit=str(value["unit"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("property_constraints 中每项需要 name、operator、value、unit") from exc
        if constraint.name not in PROPERTY_COLUMNS or constraint.operator not in {">=", ">", "<=", "<"}:
            raise ValueError(f"不支持的性质或运算符：{constraint.name} {constraint.operator}")
        expected_unit = PROPERTY_COLUMNS[constraint.name][2]
        if constraint.unit != expected_unit:
            raise ValueError(f"{constraint.name} 当前只接受规范单位 {expected_unit}，收到 {constraint.unit}")
        return constraint


@dataclass(frozen=True)
class PreferenceGoal:
    name: Literal["conductivity", "dynamic_viscosity", "resistivity"]
    direction: Literal["maximize", "minimize"]

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "PreferenceGoal":
        try:
            goal = cls(name=str(value["name"]), direction=str(value["direction"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("preference_goals 中每项需要 name、direction") from exc
        if goal.name not in PROPERTY_COLUMNS or goal.direction not in {"maximize", "minimize"}:
            raise ValueError(f"不支持的偏好目标：{goal.name} {goal.direction}")
        return goal


@dataclass(frozen=True)
class QueryRequest:
    task_id: str
    temperature_min_k: float | None
    temperature_max_k: float | None
    property_constraints: tuple[Constraint, ...]
    preference_goals: tuple[PreferenceGoal, ...]
    composition_policy: Literal["complete_only", "include_flagged"]
    manual_review_policy: Literal["exclude", "include_flagged"]
    limit: int

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "QueryRequest":
        conditions = payload.get("conditions") or {}
        temp = conditions.get("temperature_k") or {}
        lower = float(temp["min"]) if temp.get("min") is not None else None
        upper = float(temp["max"]) if temp.get("max") is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("temperature_k.min 不能大于 temperature_k.max")
        constraints = tuple(Constraint.parse(item) for item in payload.get("property_constraints") or [])
        preferences = tuple(PreferenceGoal.parse(item) for item in payload.get("preference_goals") or [])
        if not constraints and not preferences:
            raise ValueError("初筛请求至少需要一项 property_constraints 或 preference_goals")
        task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(payload.get("task_id") or "fluid_screen"))[:80]
        evidence = payload.get("evidence_policy") or {}
        composition = evidence.get("composition", "include_flagged")
        review = evidence.get("manual_review", "include_flagged")
        if composition not in {"complete_only", "include_flagged"} or review not in {"exclude", "include_flagged"}:
            raise ValueError("evidence_policy 仅支持 composition=complete_only/include_flagged，manual_review=exclude/include_flagged")
        limit = int(payload.get("limit", 200))
        if not 1 <= limit <= 2000:
            raise ValueError("limit 必须在 1 到 2000 之间")
        return cls(task_id, lower, upper, constraints, preferences, composition, review, limit)


def _passes(value_min: float, value_max: float, constraint: Constraint) -> bool:
    # A range passes only when its conservative bound meets the requested limit.
    value = value_min if constraint.operator in {">=", ">"} else value_max
    return {">=": value >= constraint.value, ">": value > constraint.value, "<=": value <= constraint.value, "<": value < constraint.value}[constraint.operator]


def _composition_quality(connection: sqlite3.Connection, record_ids: str) -> tuple[str, list[str]]:
    ids = [item for item in record_ids.split("|") if item]
    if not ids:
        return "missing", []
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(f"SELECT composition_complete, manual_review_required FROM composition_evidence WHERE record_id IN ({placeholders})", ids).fetchall()
    values = [str(row[0]).lower() for row in rows]
    review = [str(row[1]).lower() for row in rows]
    if len(rows) != len(ids):
        return "missing", ["composition record missing"]
    if all(value == "yes" for value in values):
        level = "complete"
    elif any(value == "no" for value in values):
        level = "incomplete"
    else:
        level = "unknown"
    return level, (["composition manual review"] if any(value == "yes" for value in review) else [])


def _candidate(row: sqlite3.Row, request: QueryRequest, connection: sqlite3.Connection) -> dict[str, Any]:
    values = dict(row)
    numeric_pass = all(_passes(float(values[PROPERTY_COLUMNS[c.name][0]]), float(values[PROPERTY_COLUMNS[c.name][1]]), c) for c in request.property_constraints)
    record_ids = "|".join((values["conductivity_record_ids"], values["viscosity_record_ids"]))
    composition_status, composition_flags = _composition_quality(connection, record_ids)
    review = values["conductivity_review_required"] == "yes" or values["viscosity_review_required"] == "yes"
    flags = composition_flags + (["property manual review"] if review else [])
    if not numeric_pass:
        state = "outside_requested_conditions"
    elif not request.property_constraints:
        state = "evidence_available_for_preference"
    elif composition_status == "complete" and not review:
        state = "evidence_complete_for_initial_screen"
    else:
        state = "flagged_for_review"
    return {
        "composition": {key: values[key] for key in ("component_1", "component_2", "component_3", "composition_basis", "component_1_fraction", "component_2_fraction", "component_3_fraction")},
        "conditions": {"temperature_k": values["temperature_k"], "pressure_pa": values["pressure_pa"]},
        "properties": {
            "conductivity_s_m": {"min": values["conductivity_s_m_min"], "max": values["conductivity_s_m_max"]},
            "resistivity_ohm_m": {"min": values["resistivity_ohm_m_min"], "max": values["resistivity_ohm_m_max"]},
            "dynamic_viscosity_mpa_s": {"min": values["dynamic_viscosity_mpa_s_min"], "max": values["dynamic_viscosity_mpa_s_max"]},
        },
        "evidence": {"source_id": values["source_id"], "conductivity_record_ids": values["conductivity_record_ids"], "viscosity_record_ids": values["viscosity_record_ids"], "composition_status": composition_status, "manual_review_required": review, "flags": flags},
        "status": state,
        "numeric_conditions_pass": numeric_pass,
    }


def run_query(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    request = QueryRequest.parse(payload)
    if not database.is_file():
        raise ValueError(f"证据库不存在：{database}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM transport_pair_evidence").fetchall()
        funnel: list[dict[str, Any]] = [{"step": "Exact transport evidence pairs", "count": len(rows)}]
        if request.temperature_min_k is not None:
            rows = [row for row in rows if row["temperature_k"] >= request.temperature_min_k]
            funnel.append({"step": f"Temperature >= {request.temperature_min_k:g} K", "count": len(rows)})
        if request.temperature_max_k is not None:
            rows = [row for row in rows if row["temperature_k"] <= request.temperature_max_k]
            funnel.append({"step": f"Temperature <= {request.temperature_max_k:g} K", "count": len(rows)})
        # Preserve the complete temperature-aligned population for visual
        # context.  The funnel continues to apply property constraints below.
        plot_rows = list(rows)
        for constraint in request.property_constraints:
            minimum, maximum, _ = PROPERTY_COLUMNS[constraint.name]
            rows = [row for row in rows if _passes(float(row[minimum]), float(row[maximum]), constraint)]
            funnel.append({"step": f"{constraint.name} {constraint.operator} {constraint.value:g} {constraint.unit}", "count": len(rows)})
        candidates = [_candidate(row, request, connection) for row in rows]
        if request.composition_policy == "complete_only":
            candidates = [item for item in candidates if item["evidence"]["composition_status"] == "complete"]
            funnel.append({"step": "Complete composition", "count": len(candidates)})
        if request.manual_review_policy == "exclude":
            candidates = [item for item in candidates if not item["evidence"]["manual_review_required"]]
            funnel.append({"step": "No manual review flag", "count": len(candidates)})
        def preference_key(item: dict[str, Any]) -> tuple[float, ...]:
            values = []
            for goal in request.preference_goals:
                # Candidate property keys use their human-readable database
                # names, so select the conservative comparable endpoint here.
                lookup = {
                    "conductivity": item["properties"]["conductivity_s_m"],
                    "dynamic_viscosity": item["properties"]["dynamic_viscosity_mpa_s"],
                    "resistivity": item["properties"]["resistivity_ohm_m"],
                }[goal.name]
                value = float(lookup["min"] if goal.direction == "maximize" else lookup["max"])
                values.append(-value if goal.direction == "maximize" else value)
            return tuple(values)

        candidates.sort(key=lambda item: (item["status"] not in {"evidence_complete_for_initial_screen", "evidence_available_for_preference"}, preference_key(item), item["properties"]["dynamic_viscosity_mpa_s"]["max"], -item["properties"]["conductivity_s_m"]["min"]))
        for rank, candidate in enumerate(candidates, start=1):
            candidate["preference_rank"] = rank if request.preference_goals else None
        total_before_limit = len(candidates)
        plot_candidates = [_candidate(row, request, connection) for row in plot_rows]
        plot_points = [{
            "dynamic_viscosity_mpa_s": item["properties"]["dynamic_viscosity_mpa_s"]["max"],
            "conductivity_s_m": item["properties"]["conductivity_s_m"]["min"],
            "status": item["status"],
        } for item in plot_candidates]
        return {
            "task_id": request.task_id,
            "request": {**asdict(request), "property_constraints": [asdict(c) for c in request.property_constraints], "preference_goals": [asdict(goal) for goal in request.preference_goals]},
            "database": str(database),
            "funnel": funnel,
            "summary": {"matched_before_limit": total_before_limit, "returned": min(total_before_limit, request.limit), "status_counts": {status: sum(item["status"] == status for item in candidates) for status in sorted({item["status"] for item in candidates})}, "scope": "initial_screening_evidence_not_mechanism_validation", "mode": "preference_landscape" if request.preference_goals and not request.property_constraints else "hard_filter_with_preference" if request.preference_goals else "hard_filter"},
            "plot_points": plot_points,
            "plot_population_count": len(plot_points),
            "candidates": candidates[:request.limit],
        }
    finally:
        connection.close()


def save_result(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "summary.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
