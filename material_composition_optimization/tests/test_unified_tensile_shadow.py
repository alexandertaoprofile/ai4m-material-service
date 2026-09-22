from __future__ import annotations

from copy import deepcopy
import json
import time
from pathlib import Path
from types import SimpleNamespace

from src.alloy_workflow.runner import HEASurrogateRunner
from src.alloy_workflow.tensile_shadow import (
    build_comparison_items,
    future_primary_presentation,
)


def test_aluminum_shadow_uses_resolved_candidate_and_keeps_elongation_specialist_owned():
    result = {
        "model_domain": "aerospace_aluminum_composition_v1",
        "screening_conditions": {"processing_category": "Solutionised + Artificially peak aged"},
        "all_candidates": [{
            "candidate_id": "Al-1",
            "composition_wt_percent": {"Al": 90.0, "Zn": 5.6, "Mg": 2.5, "Cu": 1.6, "Cr": 0.3},
            "processing_category": "Solutionised + Artificially peak aged",
            "predicted_properties": {"yield_strength_MPa": 510.0, "uts_MPa": 575.0, "elongation_pct": 11.0},
        }],
    }
    original = deepcopy(result)
    items = build_comparison_items(result)
    assert result == original
    assert items[0]["actual_inputs"]["test_temperature_C"] == 25.0
    assert items[0]["legacy_properties"] == {"yield_strength_MPa": 510.0, "uts_MPa": 575.0}
    assert "elongation_pct" not in items[0]["legacy_properties"]


def test_stainless_shadow_preserves_resolved_process_and_temperature():
    result = {
        "model_domain": "reusable_rocket_stainless",
        "mode": "short_time_tensile_screening",
        "screening_conditions": {
            "test_temperature_K": 900.0,
            "processing": {"quench": "water", "product_form_code": 1, "solution_treatment_temperature_K": 1323},
            "platform_defaults_used": ["processing.quench"],
        },
        "all_candidates": [{
            "candidate_id": "RSS-1", "composition_wt_percent": {"Fe": 70.0, "Cr": 18.0, "Ni": 10.0, "Mn": 2.0},
            "short_time_tensile": {"yield_0p2_MPa": {"mean": 280.0}, "uts_MPa": {"mean": 590.0}, "elongation_pct": {"mean": 35.0}},
        }],
    }
    item = build_comparison_items(result)[0]
    assert item["actual_inputs"]["processing"]["solution_treatment_temperature_K"] == 1323
    assert item["legacy_properties"] == {"yield_0p2_MPa": 280.0, "uts_MPa": 590.0}


def test_nickel_shadow_keeps_creep_out_of_transformer_property_projection():
    result = {
        "model_domain": "ni_superalloy_hot_end",
        "screening_conditions": {"manufacturing_route": "cast", "test_temperature_C": 900.0, "heat_treatment": "solution+age", "applied_stress_MPa": 300.0},
        "all_candidates": [{
            "candidate_id": "NIH-1", "composition_wt_percent": {"Ni": 60.0, "Cr": 20.0, "Co": 10.0, "Al": 5.0, "Ti": 5.0},
            "proof_strength_0p2_MPa": {"mean": 610.0}, "ultimate_tensile_strength_MPa": {"mean": 950.0},
            "creep_rupture": {"predicted_hours": 400.0},
        }],
    }
    item = build_comparison_items(result)[0]
    assert item["legacy_properties"] == {"proof_strength_0p2_MPa": 610.0, "ultimate_tensile_strength_MPa": 950.0}
    presentation = future_primary_presentation("ni_superalloy_hot_end")
    assert presentation["specialist_properties"][0] == "蠕变断裂寿命"
    assert presentation["evaluation"]["protocol"] == "frozen_composition_group_test_selected_single_seed"


def test_cryogenic_stainless_and_unrelated_domains_do_not_enter_shadow_prediction():
    assert build_comparison_items({"model_domain": "reusable_rocket_stainless", "mode": "cryogenic_reference", "all_candidates": []}) == []
    assert build_comparison_items({"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": [{}]}) == []


def test_shadow_submission_is_background_and_does_not_mutate_formal_result(tmp_path, monkeypatch):
    runner = HEASurrogateRunner(
        results_root=tmp_path, surrogate_root=Path("/data/se42/hea_surrogate"),
        environment_prefix=Path("/data/mamba/envs/mattergen-py310"), executable="/home/ubuntu/micromamba",
    )
    result = {"model_domain": "aerospace_aluminum_composition_v1", "all_candidates": []}
    original = deepcopy(result)

    def delayed(_taskid, snapshot):
        time.sleep(.2)
        assert snapshot == original
        return {"status": "completed"}

    monkeypatch.setattr(runner, "run_tensile_shadow_sync", delayed)
    started = time.perf_counter()
    future = runner.submit_tensile_shadow("background-test", result)
    elapsed = time.perf_counter() - started
    assert future is not None
    assert elapsed < .1
    assert result == original
    assert future.result(timeout=2)["status"] == "completed"


def test_relative_results_root_becomes_absolute_before_model_subprocess(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = HEASurrogateRunner(
        results_root=Path("relative-results"), surrogate_root=Path("/data/se42/hea_surrogate"),
        environment_prefix=Path("/data/mamba/envs/mattergen-py310"), executable="/home/ubuntu/micromamba",
    )
    formal = {
        "model_domain": "aerospace_aluminum_composition_v1",
        "screening_conditions": {"processing_category": "Solutionised + Artificially peak aged"},
        "all_candidates": [{
            "candidate_id": "Al-absolute-path",
            "composition_wt_percent": {"Al": 90.0, "Zn": 5.6, "Mg": 2.5, "Cu": 1.6, "Cr": 0.3},
            "processing_category": "Solutionised + Artificially peak aged",
            "predicted_properties": {"yield_strength_MPa": 510.0, "uts_MPa": 575.0, "elongation_pct": 11.0},
        }],
    }

    def fake_run(command, **_kwargs):
        request = Path(command[command.index("--request") + 1])
        response = Path(command[command.index("--response") + 1])
        assert request.is_absolute()
        assert response.is_absolute()
        assert "--diagnostic-outside-support" in command
        response.write_text(json.dumps({
            "ok": True,
            "comparisons": [{
                "status": "diagnostic_outside_support",
                "display_allowed": False,
                "ranking_allowed": False,
                "production_release_authorized": False,
            }],
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("src.alloy_workflow.runner.subprocess.run", fake_run)
    record = runner.run_tensile_shadow_sync("relative-path", formal)
    assert record["status"] == "completed"
    assert record["diagnostic_outside_support_enabled"] is True
    assert record["comparison_status_counts"] == {"diagnostic_outside_support": 1}


def test_diagnostic_outside_support_can_be_disabled(tmp_path, monkeypatch):
    runner = HEASurrogateRunner(
        results_root=tmp_path, surrogate_root=Path("/data/se42/hea_surrogate"),
        environment_prefix=Path("/data/mamba/envs/mattergen-py310"), executable="/home/ubuntu/micromamba",
    )
    formal = {
        "model_domain": "aerospace_aluminum_composition_v1",
        "screening_conditions": {"processing_category": "Solutionised + Artificially peak aged"},
        "all_candidates": [{
            "candidate_id": "Al-diagnostic-disabled",
            "composition_wt_percent": {"Al": 90.0, "Zn": 5.6, "Mg": 2.5, "Cu": 1.6, "Cr": 0.3},
            "predicted_properties": {"yield_strength_MPa": 510.0, "uts_MPa": 575.0},
        }],
    }
    monkeypatch.setenv("UNIFIED_TENSILE_DIAGNOSTIC_OUTSIDE_SUPPORT_ENABLED", "0")

    def fake_run(command, **_kwargs):
        assert "--diagnostic-outside-support" not in command
        response = Path(command[command.index("--response") + 1])
        response.write_text(json.dumps({"ok": True, "comparisons": [{"status": "skipped"}]}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("src.alloy_workflow.runner.subprocess.run", fake_run)
    record = runner.run_tensile_shadow_sync("diagnostic-disabled", formal)
    assert record["status"] == "completed"
    assert record["diagnostic_outside_support_enabled"] is False
    assert record["comparison_status_counts"] == {"skipped": 1}
