"""Replay one real specialist batch per enabled shadow domain."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.alloy_workflow.runner import HEASurrogateRunner


CASES = {
    "aluminum": {
        "model_domain": "aerospace_aluminum_composition_v1",
        "num_candidates": 3,
        "processing_category": "Solutionised + Artificially peak aged",
        "random_seed": 20260921,
        "element_bounds_wt_percent": {},
    },
    "stainless": {
        "model_domain": "reusable_rocket_stainless",
        "element_bounds_wt_percent": {
            "Cr": [16.5, 19.5], "Ni": [8.5, 12.0], "Mn": [0.8, 2.0],
            "Si": [0.2, 0.8], "C": [0.02, 0.08], "N": [0.01, 0.08],
        },
        "test_temperature_K": 293,
        "processing": {
            "material_state": "solution_annealed", "solution_treatment_temperature_K": 1323,
            "solution_treatment_time_s": 3600, "quench": "water",
            "product_form_code": 1, "melting_route_code": 1,
        },
        "num_candidates": 20,
        "objectives": {"yield_strength": 1, "uts": 1, "elongation": 1},
    },
    "nickel": {
        "model_domain": "ni_superalloy_hot_end",
        "num_candidates": 20,
        "element_bounds_wt_percent": {
            "Ni": [30, 75], "Cr": [5, 12], "Co": [0, 12], "Re": [0, 4],
            "Al": [4, 7], "Ta": [2, 13], "W": [4, 12], "Ti": [0, 4],
            "Mo": [0, 2.5], "V": [0, 0.1], "C": [0, 0.1], "B": [0, 0.02],
            "Nb": [0, 1], "Hf": [0, 2],
        },
        "manufacturing_route": "single_crystal",
        "heat_treatment": "solution_stage_1_temp_C=1302; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=982; precipitation_stage_1_time_h=5; precipitation_stage_2_temp_C=871; precipitation_stage_2_time_h=20",
        "test_temperature_C": 950,
        "applied_stress_MPa": 250,
        "screening_thresholds": {"uts_min_MPa": 900, "proof_strength_min_MPa": 500, "rupture_life_min_h": 250},
    },
}


def digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    runner = HEASurrogateRunner(
        results_root=args.output_root,
        surrogate_root=Path("/data/se42/hea_surrogate"),
        environment_prefix=Path("/data/mamba/envs/mattergen-py310"),
        executable="/home/ubuntu/micromamba",
    )
    report = {"passed": True, "mode": "shadow", "public_effect": False, "domains": {}}
    for name, constraints in CASES.items():
        taskid = f"verify-{name}"
        formal = runner.run(taskid, "propose", {"taskid": taskid, **constraints})
        before = digest(formal)
        shadow = runner.run_tensile_shadow_sync(taskid, formal)
        after = digest(formal)
        response_path = args.output_root / taskid / "shadow" / shadow["batch_id"] / "response.json"
        response = json.loads(response_path.read_text(encoding="utf-8")) if response_path.is_file() else {}
        comparisons = response.get("comparisons") or []
        predicted = [row for row in comparisons if row.get("status") == "predicted"]
        diagnostic = [
            row for row in comparisons
            if row.get("status") == "diagnostic_outside_support"
        ]
        skipped = [
            row for row in comparisons
            if row.get("status") not in {"predicted", "diagnostic_outside_support"}
        ]
        reason_counts: dict[str, int] = {}
        canonical_level_counts: dict[str, int] = {}
        full_atomic_level_counts: dict[str, int] = {}
        trace_reporting_counts: dict[str, int] = {}
        for row in comparisons:
            audit = ((row.get("shadow_result") or {}).get("input_audit") or {})
            for reason in audit.get("review_reasons") or []:
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
            canonical = row.get("canonical_composition_support_audit") or {}
            if canonical:
                level = str(canonical.get("canonical_chemistry_level") or "unknown")
                canonical_level_counts[level] = canonical_level_counts.get(level, 0) + 1
                full_level = str(
                    (canonical.get("transformer_full_atomic_domain") or {}).get("level")
                    or "unknown"
                )
                full_atomic_level_counts[full_level] = full_atomic_level_counts.get(full_level, 0) + 1
                trace_status = str(
                    (canonical.get("trace_reporting") or {}).get("status") or "unknown"
                )
                trace_reporting_counts[trace_status] = trace_reporting_counts.get(trace_status, 0) + 1
        # A strict shadow gate is expected to skip unsupported candidates.  An
        # operational pass therefore means the complete formal batch was
        # audited without mutation; prediction coverage is reported separately
        # and is a future release criterion, not something to fake here.
        domain_passed = (
            shadow.get("status") == "completed"
            and before == after
            and len(comparisons) == len(formal.get("all_candidates") or formal.get("initial_candidates") or [])
        )
        report["passed"] = report["passed"] and domain_passed
        report["domains"][name] = {
            "passed": domain_passed,
            "formal_result_unchanged": before == after,
            "formal_candidate_count": len(formal.get("all_candidates") or formal.get("initial_candidates") or []),
            "shadow_manifest": shadow,
            "shadow_comparison_count": len(comparisons),
            "strict_shadow_predicted_count": len(predicted),
            "diagnostic_outside_support_count": len(diagnostic),
            "shadow_skipped_count": len(skipped),
            "strict_shadow_coverage": (len(predicted) / len(comparisons) if comparisons else 0.0),
            "diagnostic_comparison_coverage": (
                (len(predicted) + len(diagnostic)) / len(comparisons)
                if comparisons else 0.0
            ),
            "shadow_review_reason_counts": reason_counts,
            "canonical_chemistry_level_counts": canonical_level_counts,
            "transformer_full_atomic_tier_counts": full_atomic_level_counts,
            "trace_reporting_status_counts": trace_reporting_counts,
            "first_strict_prediction": (
                predicted[0].get("internal_property_projection_MPa") if predicted else None
            ),
            "first_strict_difference": (
                predicted[0].get("transformer_minus_legacy_MPa") if predicted else None
            ),
            "first_diagnostic_prediction": (
                diagnostic[0].get("diagnostic_property_projection_MPa") if diagnostic else None
            ),
            "first_diagnostic_difference": (
                diagnostic[0].get("diagnostic_transformer_minus_legacy_MPa")
                if diagnostic else None
            ),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
