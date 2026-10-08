"""Private 1111 chip-glass → research Transformer diagnostic mapping.

The existing chip-glass family model remains the formal source. These rows
never modify a candidate or claim that source labels/conditions are equivalent.
"""

from __future__ import annotations

from typing import Any


DOMAIN = "chip_glass_thermomechanical_family_v1"
PROPERTY_MAP = {
    "DENSITY_20C": "density_g_per_cm3",
    "YOUNG_MODULUS": "young_modulus_GPa",
}


def build_glass_shadow_batch(result: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    if not isinstance(result, dict) or result.get("model_domain") != DOMAIN:
        return {"requests": []}, []
    candidates = result.get("all_candidates")
    if not isinstance(candidates, list):
        return {"requests": []}, []
    if len(candidates) > 500:
        raise ValueError("glass shadow comparison is limited to 500 candidates / 1000 property requests")
    requests, mapping = [], []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        composition = candidate.get("composition_mol_percent")
        properties = candidate.get("predicted_properties")
        if not isinstance(composition, dict) or not isinstance(properties, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or f"glass-{index + 1}")
        for task, legacy_name in PROPERTY_MAP.items():
            legacy = properties.get(legacy_name)
            if not isinstance(legacy, dict):
                continue
            value = legacy.get("prediction")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            request_id = f"{candidate_id}:{task}"
            requests.append({
                "request_id": request_id,
                "composition_mol_percent": dict(composition),
                "task": task,
                "condition_temperature_C": 20.0 if task == "DENSITY_20C" else None,
            })
            mapping.append({
                "request_id": request_id,
                "candidate_id": candidate_id,
                "research_task": task,
                "formal_property": legacy_name,
                "formal_prediction": float(value),
                "formal_unit": legacy.get("unit"),
                "comparison_allowed": False,
                "display_allowed": False,
            })
    return {"requests": requests}, mapping


def merge_private_diagnostics(mapping: list[dict[str, Any]], shadow_response: dict[str, Any]) -> dict[str, Any]:
    rows = shadow_response.get("results")
    if not isinstance(rows, list) or len(rows) != len(mapping):
        raise ValueError("glass response does not align with request mapping")
    comparisons = []
    for expected, predicted in zip(mapping, rows):
        if predicted.get("request_id") != expected["request_id"]:
            raise ValueError("glass response request_id order mismatch")
        record = {**expected, "status": predicted.get("status"), "shadow_result": predicted,
                  "comparison_allowed": False, "display_allowed": False,
                  "production_release_authorized": False}
        if predicted.get("status") == "predicted_diagnostic":
            value = predicted["prediction"]["value"]
            record["transformer_minus_formal_diagnostic"] = value - expected["formal_prediction"]
            record["difference_is_measured_error"] = False
        comparisons.append(record)
    return {"ok": bool(shadow_response.get("ok")), "mode": "shadow",
            "display_allowed": False, "production_release_authorized": False,
            "comparisons": comparisons}
