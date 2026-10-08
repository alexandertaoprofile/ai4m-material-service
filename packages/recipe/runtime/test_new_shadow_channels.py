from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from src.alloy_workflow.runner import HEASurrogateRunner


def _runner(tmp_path):
    return HEASurrogateRunner(
        results_root=tmp_path / "results", surrogate_root=tmp_path / "surrogate",
        environment_prefix=tmp_path / "environment", executable="micromamba",
    )


def test_new_model_shadows_default_off_and_formal_result_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOY_V10_SHADOW_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("GLASS_TRANSFORMER_SHADOW_RUNTIME_ROOT", raising=False)
    runner = _runner(tmp_path)
    result = {"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": []}
    original = deepcopy(result)
    runner.submit_new_model_shadows("task-1", result)
    assert result == original
    assert not (tmp_path / "results").exists()
    assert not runner._new_shadows.channels


def test_new_model_shadows_do_not_reuse_old_tensile_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOY_V10_SHADOW_RUNTIME_ROOT", str(tmp_path / "alloy"))
    monkeypatch.setenv("GLASS_TRANSFORMER_SHADOW_RUNTIME_ROOT", str(tmp_path / "glass"))
    monkeypatch.setenv("ALLOY_V10_SHADOW_ENABLED", "0")
    monkeypatch.setenv("GLASS_TRANSFORMER_SHADOW_ENABLED", "0")
    runner = _runner(tmp_path)
    assert set(runner._new_shadows.channels) == {"alloy_v10", "alloy_v10_stainless", "alloy_v10_nickel", "glass_v1"}
    runner.submit_new_model_shadows("task-2", {"model_domain": "chip_glass_thermomechanical_family_v1", "all_candidates": []})
    assert not (tmp_path / "results").exists()


def test_tensile_health_checks_selected_runtime_and_caches_real_inference(tmp_path, monkeypatch):
    surrogate = tmp_path / "surrogate"
    tool = surrogate / "tools" / "unified_alloy_tensile_shadow_runner.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# test-only", encoding="utf-8")
    v10 = tmp_path / "v10"
    v10.mkdir()
    (v10 / "runner.py").write_text("# test-only", encoding="utf-8")
    python = tmp_path / "python"
    python.write_text("# test-only", encoding="utf-8")
    monkeypatch.setenv("UNIFIED_TENSILE_RUNTIME_ROOT", str(v10))
    monkeypatch.setenv("UNIFIED_TENSILE_PYTHON", str(python))
    monkeypatch.delenv("ALLOY_V10_SHADOW_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("GLASS_TRANSFORMER_SHADOW_RUNTIME_ROOT", raising=False)
    runner = _runner(tmp_path)
    calls = []

    def healthy(command, **kwargs):
        calls.append(command)
        response = Path(command[command.index("--response") + 1])
        response.write_text(json.dumps({"ok": True, "health_check": "real_raw_input_inference_passed"}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.alloy_workflow.runner.subprocess.run", healthy)
    assert runner.tensile_shadow_ready()
    assert runner.tensile_shadow_ready()
    assert len(calls) == 1
    assert "--health-check" in calls[0]

    other = tmp_path / "other_runtime"
    other.mkdir()
    (other / "runner.py").write_text("# test-only", encoding="utf-8")
    monkeypatch.setenv("UNIFIED_TENSILE_RUNTIME_ROOT", str(other))

    def unhealthy(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, "", "")

    monkeypatch.setattr("src.alloy_workflow.runner.subprocess.run", unhealthy)
    assert not runner.tensile_shadow_ready()
