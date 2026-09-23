"""Private unified-Transformer comparison for existing alloy services.

This module maps the *resolved inputs and formal outputs* of each specialist
service into the common v2 comparison schema.  It never parses the user's raw
prompt and never mutates the result returned to presentation code.
"""
from __future__ import annotations

from typing import Any


SHADOW_DOMAINS = {
    "aerospace_aluminum_composition_v1",
    "reusable_rocket_stainless",
    "ni_superalloy_hot_end",
}

STAINLESS_SOLUTE_SCHEMA = {
    "Cr", "Ni", "Mo", "Mn", "Si", "Nb", "Ti", "Zr", "Ta", "V", "W",
    "Cu", "N", "C", "B", "P", "S", "Co", "Al", "Sn", "Pb",
}

# This metadata is private while shadow mode is active.  It is the single
# source of truth for a future reviewed switch of property attribution in the
# customer-facing report.  Only YS/UTS change owner; the other properties stay
# with their specialist models.
FUTURE_PRIMARY_PRESENTATION = {
    "aerospace_aluminum_composition_v1": {
        "model_label": "统一合金物理约束 Transformer（航空铝合金域）",
        "transformer_properties": ["0.2% 屈服强度", "抗拉强度"],
        "specialist_properties": ["延伸率", "成分适用域", "工艺类别证据"],
        "evaluation": {
            "protocol": "frozen_composition_group_test_selected_single_seed",
            "metric": "MAE_MPa",
            "values": {"yield_strength_0p2": 57.4, "ultimate_tensile_strength": 46.7},
        },
    },
    "reusable_rocket_stainless": {
        "model_label": "统一合金物理约束 Transformer（奥氏体不锈钢域）",
        "transformer_properties": ["0.2% 屈服强度", "抗拉强度"],
        "specialist_properties": ["延伸率", "深冷参考层", "焊接/疲劳/LOX 验证路径"],
        "evaluation": {
            "protocol": "frozen_composition_group_test_selected_single_seed",
            "metric": "MAE_MPa",
            "values": {"yield_strength_0p2": 19.5, "ultimate_tensile_strength": 27.6},
        },
    },
    "ni_superalloy_hot_end": {
        "model_label": "统一合金物理约束 Transformer（镍基高温合金域）",
        "transformer_properties": ["0.2% 屈服/规定塑性延伸强度", "短时抗拉强度"],
        "specialist_properties": ["蠕变断裂寿命", "延伸率辅助指标", "牌号/路线/热处理证据"],
        "evaluation": {
            "protocol": "frozen_composition_group_test_selected_single_seed",
            "metric": "MAE_MPa",
            "values": {"yield_strength_0p2": 90.5, "ultimate_tensile_strength": 156.6},
        },
    },
}


def _candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("all_candidates") or result.get("initial_candidates") or []
    return [row for row in rows if isinstance(row, dict)]


def build_comparison_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one batch from an already-computed specialist result."""
    domain = result.get("model_domain")
    if domain not in SHADOW_DOMAINS:
        return []
    conditions = result.get("screening_conditions") or {}
    search_space = result.get("search_space") or {}
    items: list[dict[str, Any]] = []
    for candidate in _candidates(result):
        candidate_id = str(candidate.get("candidate_id") or f"candidate-{len(items)+1}")
        composition = candidate.get("composition_wt_percent")
        if not isinstance(composition, dict):
            continue
        if domain == "aerospace_aluminum_composition_v1":
            properties = candidate.get("predicted_properties") or {}
            actual = {
                "composition_wt_percent": composition,
                "processing_category": candidate.get("processing_category") or conditions.get("processing_category"),
                "test_temperature_C": 25.0,
            }
            legacy = {
                "yield_strength_MPa": properties.get("yield_strength_MPa"),
                "uts_MPa": properties.get("uts_MPa"),
            }
        elif domain == "reusable_rocket_stainless":
            if result.get("mode") == "cryogenic_reference":
                continue
            tensile = candidate.get("short_time_tensile") or {}
            actual = {
                "composition_wt_percent": composition,
                "test_temperature_K": conditions.get("test_temperature_K"),
                "processing": conditions.get("processing"),
                "platform_defaults_used": conditions.get("platform_defaults_used") or [],
            }
            legacy = {
                "yield_0p2_MPa": (tensile.get("yield_0p2_MPa") or {}).get("mean"),
                "uts_MPa": (tensile.get("uts_MPa") or {}).get("mean"),
            }
        else:
            actual = {
                "composition_wt_percent": composition,
                "manufacturing_route": conditions.get("manufacturing_route"),
                "test_temperature_C": conditions.get("test_temperature_C"),
                "heat_treatment": conditions.get("heat_treatment"),
                "applied_stress_MPa": conditions.get("applied_stress_MPa"),
            }
            legacy = {
                "proof_strength_0p2_MPa": (candidate.get("proof_strength_0p2_MPa") or {}).get("mean"),
                "ultimate_tensile_strength_MPa": (candidate.get("ultimate_tensile_strength_MPa") or {}).get("mean"),
            }
        # Let the isolated adapter report malformed/missing numerical values as
        # skipped mappings; do not silently coerce them here.
        item = {
            "request_id": candidate_id,
            "service_domain": domain,
            "actual_inputs": actual,
            "legacy_properties": legacy,
        }
        if domain == "reusable_rocket_stainless":
            bounds = search_space.get("element_bounds_wt_percent") or {}
            generated = sorted(set(bounds) & STAINLESS_SOLUTE_SCHEMA)
            fixed_zero = sorted(
                element for element in generated
                if isinstance(bounds.get(element), (list, tuple))
                and len(bounds[element]) == 2
                and float(bounds[element][0]) == 0.0
                and float(bounds[element][1]) == 0.0
            )
            item["composition_semantics"] = {
                "basis": "wt_percent",
                "sampled_or_user_fixed_elements": generated,
                "balance_element": "Fe",
                "fixed_zero_by_search_constraint": fixed_zero,
                "not_in_search_space": sorted(STAINLESS_SOLUTE_SCHEMA - set(generated)),
                "omitted_interpretation": "not_in_search_space_not_confirmed_zero",
                "closure_policy": "Fe balances the nominal generated solutes to 100 wt.%",
            }
            item["specialist_applicability_domain"] = candidate.get("applicability_domain")
        items.append(item)
    return items


def future_primary_presentation(domain: str) -> dict[str, Any] | None:
    policy = FUTURE_PRIMARY_PRESENTATION.get(domain)
    return dict(policy) if policy else None
