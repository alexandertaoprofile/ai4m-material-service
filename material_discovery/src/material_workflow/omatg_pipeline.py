"""CSP or direct DNG screening; fixed-composition exploration is opt-in."""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import replace
from pathlib import Path

from .alignn import enrich_validations_with_alignn
from .emitters import write_pipeline_manifest
from .mattersim import enrich_validations_with_mattersim
from .omatg_generation import generated_admission, run_omatg_generation
from .ranking import rank_candidates
from .schemas import NewMaterialPipelineResult, GenerationManifest
from .validation import write_validation_result


def stage(task_dir, name, text, percent=None):
    if percent is None:
        percent = {"generation": 15, "validation": 35, "properties": 85, "rendering": 95}.get(name, 5)
    path = task_dir / "omatg_stage.json"
    try:
        previous = json.loads(path.read_text())
        percent = max(percent, previous.get("percent", 0))
    except (OSError, json.JSONDecodeError):
        pass
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps({"stage": name, "text": text, "percent": percent}, ensure_ascii=False), encoding="utf-8")
    staging.replace(path)


def evaluate(constraints, generation, directory):
    from .omatg_sampling import composition_errors
    for candidate in generation.candidates:
        amounts = candidate.metadata.get("element_counts")
        if amounts:
            failures = composition_errors(amounts, constraints.to_dict())
            if failures:
                candidate.metadata["composition_ok"] = False
                candidate.metadata["composition_errors"] = list(dict.fromkeys(candidate.metadata.get("composition_errors", []) + failures))
    validations = [generated_admission(candidate) for candidate in generation.candidates]
    for validation in validations:
        write_validation_result(validation, directory / "validation")
    candidates = [candidate for candidate, validation in zip(generation.candidates, validations) if validation.is_valid]
    enrich_validations_with_mattersim(validations, candidates, directory / "validation", reference_system="-".join(constraints.allowed_elements))
    return validations


def screening_state(validation, constraints):
    failures, missing = [], []
    if not validation.is_valid:
        failures.extend(validation.errors or ["结构或组成未通过"])
    thermo = validation.metadata.get("mattersim", {})
    if thermo.get("relaxation_converged") is not True:
        missing.append("松弛收敛")
    hull = validation.energy_above_hull
    reference = constraints.target_properties.get("energy_above_hull", 0.05)
    hard_limit = constraints.parameter_sources.get("energy_above_hull_mode") == "硬性上限"
    stability_review = False
    if hull is None or not math.isfinite(hull):
        missing.append("凸包能估算")
    elif hull > reference and hard_limit:
        failures.append("凸包能超过上限")
    elif hull > reference:
        stability_review = validation.is_valid is True and thermo.get("relaxation_converged") is True
    for key, expected in constraints.symmetry_constraints.items():
        actual = validation.metadata.get(key)
        if actual is None:
            missing.append(key)
        elif actual != expected:
            failures.append("对称性条件未满足")
    for key, bounds in constraints.property_constraints.items():
        prediction = validation.property_predictions.get(key, {})
        value = prediction.get("value")
        if value is None or not math.isfinite(value):
            missing.append(key)
        elif value < float(bounds.get("min", -math.inf)) or value > float(bounds.get("max", math.inf)) or (bounds.get("min_inclusive") is False and value == float(bounds.get("min", -math.inf))) or (bounds.get("max_inclusive") is False and value == float(bounds.get("max", math.inf))):
            failures.append("性质范围未满足：" + key)
    return {"state": "failed" if failures else "incomplete" if missing else "passed",
        "failures": failures, "missing": missing, "stability_review": stability_review,
        "stability_reference_ev_per_atom": reference, "stability_limit_mode": "hard" if hard_limit else "reference"}


def run_omatg_pipeline(constraints, results_root, max_candidates=10):
    task_dir = Path(results_root) / constraints.taskid
    task_dir.mkdir(parents=True, exist_ok=True)
    # The progress streamer may create progress.json before the worker arrives.
    # Claim independently and atomically; repeated task IDs must not overwrite outputs.
    with (task_dir / ".omatg_task_claim").open("x") as claim:
        claim.write(constraints.taskid)
    from .runtime_lock import gpu_job_lock
    try:
        with gpu_job_lock():
            return _run_claimed(constraints, task_dir, max_candidates)
    except TimeoutError:
        message = "计算队列等待超过时限，结构生成尚未启动；计算资源可用后可按已保留的组成与目标重试。"
        result = NewMaterialPipelineResult(taskid=constraints.taskid, status="timeout", constraints=constraints,
            generation=GenerationManifest(taskid=constraints.taskid, status="timeout", backend="omatg", message=message),
            message=message, artifacts={"result_dir": str(task_dir)})
        write_pipeline_manifest(result, task_dir)
        return result


def _run_claimed(constraints, task_dir, max_candidates):
    started = time.monotonic()
    original_constraints = constraints
    dng_record = None
    selected = None
    dng_space = "元素白名单" if constraints.allowed_elements else "已确认的开放化学空间"
    stage(task_dir, "generation", "正在生成固定组成晶体。" if constraints.generation_mode == "csp" else f"正在{dng_space}中生成组成与晶体。")
    generation = run_omatg_generation(constraints, task_dir / "generation", max_candidates)
    stage(task_dir, "validation", "正在检查组成和周期几何，并以 MatterSim 松弛评估稳定性。" if generation.candidates else "结构生成没有完整候选输入，后续检查与评价未启动；正在整理生成阶段原因。")
    validations = evaluate(constraints, generation, task_dir)
    if constraints.generation_mode == "dng" and constraints.refine_with_csp:
        pool = [item for item in rank_candidates(generation.candidates, validations) if item.validation.is_valid and item.validation.energy_above_hull is not None and item.validation.metadata.get("mattersim", {}).get("relaxation_converged") is True]
        dng_record = {"generation": generation.to_dict(), "validations": [item.to_dict() for item in validations]}
        if pool:
            seed = pool[0]
            counts = seed.candidate.metadata["element_counts"]
            # Literal cell composition preserves every count; do not change Z silently.
            formula = "".join(symbol + (str(number) if number > 1 else "") for symbol, number in counts.items())
            selected = {"candidate_id": seed.candidate.candidate_id, "formula": formula,
                "display_formula": seed.candidate.formula_pretty, "element_counts": counts,
                "energy_above_hull": seed.validation.energy_above_hull,
                "relaxed_structure_path": seed.validation.artifacts.get("relaxed_structure_path"),
                "criterion": "组成和结构合规、松弛收敛的 DNG 候选中，按混合凸包能估算从低到高选一个组成"}
            constraints = replace(constraints, generation_mode="csp", target_formula=formula, formula_units=1, seed=(constraints.seed+1) % 2**32, refine_with_csp=False)
            stage(task_dir, "generation", f"DNG 已选出 {seed.candidate.formula_pretty} 组成，正在生成 {max_candidates} 个固定计量 CSP 构型。", 50)
            generation = run_omatg_generation(constraints, task_dir / "csp" / "generation", max_candidates)
            stage(task_dir, "validation", "正在对 CSP 构型松弛、计算稳定性，并按结构相似性去重。", 70)
            validations = evaluate(constraints, generation, task_dir / "csp")
    # Property heads evaluate the retained relaxed geometry, not another candidate
    # or an obsolete unrelaxed input. Duplicate structure families are evaluated once.
    eligible = []
    for candidate, validation in zip(generation.candidates, validations):
        thermo = validation.metadata.get("mattersim", {})
        if validation.is_valid and thermo.get("relaxation_converged") is True and (constraints.property_constraints or not thermo.get("duplicate_of")) and validation.artifacts.get("relaxed_structure_path"):
            eligible.append(replace(candidate, cif_path=Path(validation.artifacts["relaxed_structure_path"])))
    property_message = "正在对去重后的松弛结构进行 ALIGNN 性质快速预测。" if eligible else "本轮没有可供性质预测的收敛结构；正在整理各候选的停止阶段与原因。"
    if os.getenv("ALIGNN_ENABLED", "1").lower() not in {"1", "true", "yes"}:
        property_message = "本次配置关闭了性质预测；正在整理已完成的结构与稳定性结果。"
    stage(task_dir, "properties", property_message)
    final_dir = task_dir / "csp" if selected else task_dir
    enrich_validations_with_alignn(validations, eligible, final_dir / "validation",
        target_properties=constraints.target_properties, validation_targets=constraints.validation_targets)
    by_name = {Path(candidate.cif_path).name: validation for candidate, validation in zip(generation.candidates, validations) if candidate.cif_path}
    for validation in validations:
        duplicate = validation.metadata.get("mattersim", {}).get("duplicate_of")
        if duplicate and duplicate in by_name:
            # Same approximate family is not the same exact geometry: do not copy
            # predictions onto another structure. Only its representative is shortlisted.
            validation.metadata["duplicate_of"] = by_name[duplicate].candidate_id
        validation.metadata["screening"] = screening_state(validation, constraints)
        write_validation_result(validation, final_dir / "validation")
    # Select each family representative after hard property/symmetry screening,
    # so a lower-energy failing member cannot hide another member that passes.
    families = {}
    for candidate, validation in zip(generation.candidates, validations):
        thermo = validation.metadata.get("mattersim", {})
        if not thermo:
            continue
        group = thermo.get("duplicate_of") or thermo.get("source_name") or candidate.candidate_id
        families.setdefault(group, []).append(validation)
    for members in families.values():
        members.sort(key=lambda item: (item.metadata["screening"]["state"] != "passed", item.energy_above_hull if item.energy_above_hull is not None else math.inf, item.candidate_id))
        representative = members[0]
        representative.metadata.pop("duplicate_of", None)
        for member in members[1:]:
            member.metadata["duplicate_of"] = representative.candidate_id
        for member in members:
            write_validation_result(member, final_dir / "validation")
    ranked = rank_candidates(generation.candidates, validations)
    def key(item):
        validation = item.validation
        passed = validation.metadata["screening"]["state"] == "passed"
        duplicate = bool(validation.metadata.get("duplicate_of"))
        return (not passed, duplicate, validation.is_valid is not True,
            validation.energy_above_hull if validation.energy_above_hull is not None else math.inf, item.candidate.candidate_id)
    ranked.sort(key=key)
    for index, item in enumerate(ranked, 1):
        item.rank = index
    generation.metadata.update(dng_stage=dng_record, selected_dng_composition=selected,
        csp_refinement_requested=original_constraints.refine_with_csp,
        executed_generation_modes=(["dng", "csp"] if selected else [original_constraints.generation_mode]),
        calculation_settings={"relax_max_steps": int(os.getenv("OMATG_RELAX_MAX_STEPS", "500"))},
        final_constraints=constraints.to_dict(), scientific_wall_seconds=time.monotonic()-started)
    if generation.status != "ok":
        message = generation.message or "结构生成未完成，已保留输入条件供复核。"
    elif original_constraints.generation_mode == "dng" and original_constraints.refine_with_csp and not selected:
        message = "DNG 尚未形成组成合规、结构收敛且已有稳定性结果的起点，追加 CSP 未启动；请按候选明细恢复对应评价，再选定组成。"
    else:
        from .omatg_report import evaluation_completion_text
        message = evaluation_completion_text(generation.candidates, validations)
    result = NewMaterialPipelineResult(taskid=constraints.taskid, status=generation.status,
        constraints=original_constraints, generation=generation, validations=validations, ranked_candidates=ranked,
        message=message, artifacts={"result_dir": str(task_dir)})
    write_pipeline_manifest(result, task_dir)
    stage(task_dir, "rendering", "计算结果已保存，正在整理报告与结构可视化。")
    return result
