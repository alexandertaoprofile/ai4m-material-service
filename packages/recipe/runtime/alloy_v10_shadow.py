"""Private raw-input v10 alloy diagnostic runner; never publishes a formal result.

The training checkpoint and preprocessor are immutable inputs. This adapter
reconstructs the 113-column v8 semantic view without inventing unreported
v6 microstructure/process fields. It deliberately has no primary mode or
empirical-support approval; those require a separately validated artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sys
import time
import uuid

import numpy as np


WEIGHT_SHA = "88109cd28ed056e07c893bc294c3000f5ac6dbb809de6a9046f233f478467f83"
PREPROCESSOR_SHA = "5d078f8239447c6996e7583fd2a03983029c483ffd58fe3dbfeb7edb4d5e1f35"
SHADOW_DOMAINS = frozenset({"aluminum", "stainless", "nickel_superalloy"})
FOUNDATION_DIR = Path("/data/se42/experiments/alloy_foundation_v0_20260916")
FROZEN_SOURCE_SHA = {
    "train_v8_canonical.py": "454122c12166053a50616df7ef12326bce472c3ee68e94117b547835bb72ef86",
    "repair_process_data_v6.py": "f6d113158a40890813888364a918055a4f5ffa6d7d971241a4a09e7f17bd7f56",
    "frozen_descriptors.py": "c5400e661b7c5c1415d397caa650c48489a634cf093441a784fbb0707b16da75",
    "frozen_process.py": "9ac4a6d2cc1539a7fcdce182723a71dad7ae321f3742c111adec6aa32cba80ff",
    "descriptor_properties.json": "1a35f9b946a97fcc675ded63d0426f0ef84fdb9a28ab528fc98af32586bd9602",
    "train_process_adapter_v2.py": "e0e120fede8acd258dc729fa8d1adf73305a47aff335e58e2855185e47d07e9c",
    "run_physics_informed_cv.py": "b343af92ffe4be856964d7dfd7bfbec81574df35d47722e7aca4b7ae42b121e2",
    "run_hard_constraint_transformer_cv.py": "c88424e0092411012ad1e3a304ed60279ad188a475580b8bf4e0a1d21d6c266d",
    "run_family_adapter_multitask.py": "0e9b47a783ccae89d320090d674862c7c0852e1fcf3a3f512684a5bcd4801458",
}
EVIDENCE_FLAGS = (
    "cast", "wrought", "additive", "powder", "solution", "age", "anneal",
    "homogenize", "coldwork", "quench", "aircool", "temper", "single_crystal",
    "directional",
)


class InputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("invalid_number", f"{name} must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise InputError("invalid_number", f"{name} must be finite")
    return result


def _object_keys(value, allowed, name: str) -> None:
    if not isinstance(value, dict):
        raise InputError("invalid_object", f"{name} must be an object")
    extras = set(value) - set(allowed)
    if extras:
        raise InputError("unsupported_fields", f"{name} has unsupported fields: {sorted(extras)}")


def encode_request(request, pre, properties, process_parser, descriptor_values):
    _object_keys(request, ("request_id", "domain", "composition", "composition_basis", "temperature", "process", "context", "measurements"), "request")
    request_id = request.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 256):
        raise InputError("invalid_request_id", "request_id must be a string of at most 256 characters")
    domain = request.get("domain")
    if domain not in pre["domains"]:
        raise InputError("unknown_domain", "explicit supported alloy domain is required")
    composition = request.get("composition")
    if not isinstance(composition, dict) or not 1 <= len(composition) <= len(properties):
        raise InputError("invalid_composition", "composition must be a nonempty element dictionary")
    basis = request.get("composition_basis")
    if basis not in ("at_fraction", "at_percent", "wt_fraction", "wt_percent"):
        raise InputError("composition_unit", "explicit atomic or weight fraction/percent is required")
    raw = {}
    for element, value in composition.items():
        if element not in properties or element not in pre["symbol_to_z"]:
            raise InputError("unknown_element", f"element outside frozen vocabulary: {element!r}")
        amount = _number(value, f"composition.{element}")
        if amount < 0:
            raise InputError("negative_composition", "composition amounts cannot be negative")
        if amount > 0:
            raw[element] = amount
    if not 1 <= len(raw) <= 20:
        raise InputError("element_count", "1-20 positive elements are required")
    expected = 1.0 if basis.endswith("fraction") else 100.0
    total = sum(raw.values())
    tolerance = 1e-5 if expected == 1.0 else 0.01
    if abs(total - expected) > tolerance:
        raise InputError("composition_closure", f"composition totals {total}, expected {expected}; include the balance element")
    moles = ({e: x / properties[e]["AtomicWeight"] for e, x in raw.items()}
             if basis.startswith("wt") else raw)
    mole_total = sum(moles.values())
    atomic = {e: x / mole_total for e, x in moles.items()}
    if any(x <= 1e-8 for x in atomic.values()):
        raise InputError("trace_below_resolution", "positive atomic fraction below the frozen 1e-8 cutoff")

    if "temperature" not in request:
        raise InputError("missing_temperature_field", "temperature is required; null means unknown in research replay")
    temperature = request["temperature"]
    missing_temperature = temperature is None
    if missing_temperature:
        temperature_C = 25.0  # training placeholder; the missing mask remains 1
    else:
        _object_keys(temperature, ("value", "unit"), "temperature")
        value = _number(temperature.get("value"), "temperature.value")
        if temperature.get("unit") not in ("C", "K"):
            raise InputError("temperature_unit", "temperature unit must be C or K")
        temperature_C = value - 273.15 if temperature["unit"] == "K" else value
        if temperature_C <= -273.15:
            raise InputError("temperature_physical_limit", "test temperature must exceed absolute zero")

    process = request.get("process")
    _object_keys(process, ("text", "parameters", "v6_stages"), "process")
    text = process.get("text")
    if not isinstance(text, str) or len(text) > 16384:
        raise InputError("process_text", "process.text must be a string of at most 16384 characters")
    params = process.get("parameters", {})
    _object_keys(params, process_parser.PARAMS, "process.parameters")
    for name, raw_value in params.items():
        if raw_value is None:
            continue
        value = _number(raw_value, f"process.parameters.{name}")
        if name.endswith("_temp_C") and value <= -273.15:
            raise InputError("process_temperature", "process temperature must exceed absolute zero")
        if name.endswith("_time_h") and value < 0:
            raise InputError("process_time", "process time cannot be negative")
        if name == "cold_reduction_pct" and not 0 <= value <= 100:
            raise InputError("process_reduction", "cold reduction must be between 0 and 100%")
    context = request.get("context", {})
    if not isinstance(context, dict):
        raise InputError("invalid_context", "context must be an audit object")
    stage_text = process.get("v6_stages")
    measurements = request.get("measurements", {})
    allowed_measurements = {"test_strain_rate_per_s", "grain_size_um", "grain_areal_density_per_mm2",
                            "phase_FCC", "phase_BCC", "phase_HCP", "phase_B2", "phase_L12"}
    _object_keys(measurements, allowed_measurements, "measurements")
    measured_values = {}
    for name, value in measurements.items():
        if value is None:
            continue  # unreported is not zero
        parsed = _number(value, "measurements." + name)
        if name.startswith("phase_") and parsed not in (0.0, 1.0):
            raise InputError("phase_presence", "reported phase indicators must be 0 or 1")
        if not name.startswith("phase_") and parsed <= 0:
            raise InputError("measured_context", "reported strain rate and grain metrics must be positive")
        measured_values["desc_v6_" + name] = parsed
    if stage_text is not None and (domain != "nickel_superalloy" or not isinstance(stage_text, str)
                                   or not stage_text.strip() or len(stage_text) > 16384):
        raise InputError("v6_stages", "v6_stages requires a nonempty nickel heat-treatment stage string")

    process_features, label, evidence, conflicts = process_parser.parse_process(text, params)
    stage_values = {}
    if stage_text is not None:
        from repair_process_data_v6 import parse_stages
        try:
            stage_values = parse_stages(stage_text)
        except ValueError as exc:
            raise InputError("v6_stages", "nickel heat-treatment stages are not in the frozen training format") from exc
        for suffix in ("temp_C", "time_h"):
            key = "solution_stage_1_" + suffix
            if key in stage_values:
                process_features["desc_v2_solution_" + suffix] = stage_values[key]
                process_features["desc_v2_solution_" + suffix + "_missing"] = 0.0
    descriptors = descriptor_values(atomic, properties)
    melting = descriptors["desc_MeltingT_mean"]
    values = {
        "test_temperature_C": temperature_C,
        "temperature_missing": float(missing_temperature),
        **descriptors,
        **process_features,
        "desc_v2_homologous_temperature_proxy": (
            (temperature_C + 273.15) / melting if not missing_temperature and melting else math.nan
        ),
        "desc_v2_test_temperature_known": float(not missing_temperature),
    }
    for flag in EVIDENCE_FLAGS:
        values[f"desc_v8_evidence_flag_{flag}"] = float(process_features[f"desc_v2_flag_{flag}"] == 1)
    # v6 source repair marks a treatment as positive evidence when an actual
    # stage temperature or duration is present, even if the coarse process
    # label is only e.g. "form=3|wq=1.0|aq=0.0". Keep that v8 semantic rule.
    stage_flags = {
        "solution": "solution", "aging": "age", "annealing": "anneal",
        "homogenization": "homogenize", "tempering": "temper",
    }
    for stage, flag in stage_flags.items():
        if domain in {"hea_mpea", "stainless", "nickel_superalloy", "steel"} and any(math.isfinite(process_features[f"desc_v2_{stage}_{suffix}"])
               for suffix in ("temp_C", "time_h")):
            values[f"desc_v8_evidence_flag_{flag}"] = 1.0
    if domain == "hea_mpea" and math.isfinite(process_features["desc_v2_cold_reduction_pct"]) and process_features["desc_v2_cold_reduction_pct"] > 0:
        values["desc_v8_evidence_flag_coldwork"] = 1.0
    if domain == "stainless":
        # These legacy source fields are numerical indicators, not keywords:
        # wq=0 is explicit zero, not evidence of a water quench. Do not alter
        # the checkpoint's saved categorical token while repairing evidence.
        for field, flag in (("wq", "quench"), ("aq", "aircool")):
            match = re.search(r"(?:^|\|)" + field + r"=(0(?:\.0)?|1(?:\.0)?)(?:\||$)", text)
            if match:
                values[f"desc_v8_evidence_flag_{flag}"] = float(match.group(1))
    if any(key.startswith("precipitation_") for key in stage_values):
        values["desc_v8_evidence_flag_age"] = 1.0
    # No v6 field is inferred from a coarse service process label. Preserve the
    # training pipeline's explicit missing/zero distinction for these columns.
    for column in pre["numeric_cols"]:
        if column.startswith("desc_v6_"):
            values[column] = 1.0 if column.endswith("_missing") else math.nan
    for key, value in stage_values.items():
        column = "desc_v6_" + key
        if column not in values or column + "_missing" not in values:
            raise InputError("v6_stages", f"unrecognized v10 stage: {key}")
        values[column] = value
        values[column + "_missing"] = 0.0
    for column, value in measured_values.items():
        if column not in values or column + "_missing" not in values:
            raise InputError("measured_context", "measurement not present in frozen model schema")
        values[column] = value
        values[column + "_missing"] = 0.0
    unavailable = set(pre["numeric_cols"]) - set(values)
    if unavailable:
        raise RuntimeError(f"unimplemented v10 columns: {sorted(unavailable)}")
    numeric = []
    for column in pre["numeric_cols"]:
        value = values[column]
        if not math.isfinite(value):
            value = pre["numeric_median"][column]
        numeric.append((value - pre["numeric_mean"][column]) / pre["numeric_std"][column])
    ids = np.zeros(20, dtype=np.int64)
    fractions = np.zeros(20, dtype=np.float32)
    for index, (element, fraction) in enumerate(sorted(atomic.items(), key=lambda item: item[1], reverse=True)):
        ids[index] = pre["symbol_to_z"][element]
        fractions[index] = fraction
    encoded = {
        "element_ids": ids,
        "fractions": fractions,
        "numeric": np.asarray(numeric, dtype=np.float32),
        "domain": np.int64(pre["domains"].index(domain)),
        "process": np.int64({name: index + 1 for index, name in enumerate(pre["processes"]) }.get(label, 0)),
    }
    audit = {
        "domain": domain,
        "composition_basis_received": basis,
        "composition_total_received": total,
        "atomic_fractions": atomic,
        "test_temperature_C": temperature_C,
        "temperature_missing": missing_temperature,
        "process_label_encoded": label,
        "process_id": int(encoded["process"]),
        "process_evidence": evidence,
        "process_conflicts": conflicts,
        "unmodeled_context_keys": sorted(context),
        "v6_stage_fields_mapped": sorted(stage_values),
        "v6_fields_unreported": not bool(stage_values),
        "measurements_mapped": sorted(measured_values),
        "v10_empirical_support": "not_yet_calibrated_for_service",
        "comparison_allowed": False,
        "display_allowed": False,
        "production_release_authorized": False,
    }
    if domain == "stainless" and {"C", "N"} <= set(composition) and not missing_temperature:
        from stainless_support import evaluate as evaluate_support
        mass = {e: x * properties[e]["AtomicWeight"] for e, x in atomic.items()}
        weight = {e: 100 * x / sum(mass.values()) for e, x in mass.items()}
        # Retain explicitly reported numerical zeros. Omitting P/S is a
        # nominal recipe statement, never proof of zero measured impurities.
        for e in composition:
            weight.setdefault(e, 0.0)
        support = evaluate_support(weight, temperature_C, context.get("composition_status"))
        audit["shared_support"] = support
        audit["v10_empirical_support"] = support["level"]
    return encoded, audit


def load_frozen(args):
    if digest(args.checkpoint) != WEIGHT_SHA or digest(args.preprocessor) != PREPROCESSOR_SHA:
        raise RuntimeError("v10 weight or preprocessor SHA-256 mismatch")
    source_paths = [
        (args.code_dir / "train_v8_canonical.py", "train_v8_canonical.py"),
        (args.support_code_dir / "repair_process_data_v6.py", "repair_process_data_v6.py"),
        (args.v2_runtime / "frozen_descriptors.py", "frozen_descriptors.py"),
        (args.v2_runtime / "frozen_process.py", "frozen_process.py"),
        (args.descriptor_properties, "descriptor_properties.json"),
    ]
    for filename in ("train_process_adapter_v2.py", "run_physics_informed_cv.py",
                     "run_hard_constraint_transformer_cv.py", "run_family_adapter_multitask.py"):
        source_paths.append((FOUNDATION_DIR / filename, filename))
    for path, name in source_paths:
        if digest(path) != FROZEN_SOURCE_SHA[name]:
            raise RuntimeError(f"v10 source dependency SHA-256 mismatch: {name}")
    pre = json.loads(args.preprocessor.read_text(encoding="utf-8"))
    if len(pre["numeric_cols"]) != 113 or pre["config"]["stable_process_label"]:
        raise RuntimeError("unexpected v10 feature view")
    import torch
    torch.set_num_threads(args.threads)
    sys.path.insert(0, str(args.v2_runtime.resolve()))
    sys.path.insert(0, str(args.support_code_dir.resolve()))
    sys.path.insert(0, str(args.code_dir.resolve()))
    # The historical training file prepends its old absolute experiment root.
    # Preload each checked dependency from the frozen bundle so that Python's
    # module cache cannot resolve it to a mutable copy in that old directory.
    import importlib.util
    for name in ('run_family_adapter_multitask','run_physics_informed_cv',
                 'run_hard_constraint_transformer_cv','train_process_adapter_v2'):
        path=FOUNDATION_DIR/(name+'.py')
        if name in sys.modules:
            origin=Path(sys.modules[name].__file__).resolve()
            if digest(origin)!=FROZEN_SOURCE_SHA[name+'.py']:
                raise RuntimeError('v10 cached source dependency mismatch: '+name)
            continue
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        spec.loader.exec_module(module)
    import frozen_descriptors
    import frozen_process
    from train_v8_canonical import CanonicalContinuityModel
    properties = json.loads(args.descriptor_properties.read_text(encoding="utf-8"))
    state = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    model = CanonicalContinuityModel(
        numeric_columns=pre["numeric_cols"], element_features=state["element_features"],
        n_numeric=len(pre["numeric_cols"]), n_domains=len(pre["domains"]),
        n_processes=len(pre["processes"]), ys_mean=state["ys_mean"],
        ys_std=state["ys_std"], delta_scale=state["delta_scale"],
    ).eval()
    model.load_state_dict(state, strict=True)
    return pre, properties, frozen_process, frozen_descriptors.descriptor_values, model, torch


def predict_many(requests, loaded, mode="shadow"):
    if mode not in ("shadow", "research"):
        raise InputError("mode_not_authorized", "no primary mode exists")
    if not isinstance(requests, list) or not 1 <= len(requests) <= 4096:
        raise InputError("batch_size", "requests must contain 1-4096 items")
    pre, properties, process_parser, descriptor_values, model, torch = loaded
    started = time.perf_counter()
    rows, valid = [], []
    for index, request in enumerate(requests):
        row = {
            "index": index,
            "request_id": request.get("request_id") if isinstance(request, dict) and isinstance(request.get("request_id"), str) else None,
            "status": "skipped",
            "display_allowed": False,
            "comparison_allowed": False,
            "production_release_authorized": False,
        }
        try:
            encoded, audit = encode_request(request, pre, properties, process_parser, descriptor_values)
            row["input_audit"] = audit
            if mode == "shadow" and audit["domain"] not in SHADOW_DOMAINS:
                row["reason"] = "domain_research_only"
            elif mode == "shadow" and audit["temperature_missing"]:
                row["reason"] = "unknown_test_temperature"
            elif mode == "shadow" and audit["process_conflicts"]:
                row["reason"] = "process_parameter_conflict"
            else:
                valid.append((index, encoded))
        except InputError as exc:
            row["status"] = "invalid_input"
            row["error"] = {"code": exc.code, "message": str(exc)}
        rows.append(row)
    with torch.inference_mode():
        for offset in range(0, len(valid), 128):
            chunk = valid[offset:offset + 128]
            batch = {key: torch.as_tensor(np.stack([encoded[key] for _, encoded in chunk])) for key in chunk[0][1]}
            outputs = model(batch).cpu().numpy()
            if not np.isfinite(outputs).all() or np.any(outputs[:, 0] < 0) or np.any(outputs[:, 1] < outputs[:, 0]):
                raise RuntimeError("v10 output failed finite/nonnegative/ordered-strength check")
            for (index, _), (ys, uts) in zip(chunk, outputs):
                rows[index]["status"] = "predicted_diagnostic"
                rows[index]["prediction"] = {
                    "yield_strength_0p2_MPa": float(ys),
                    "ultimate_tensile_strength_MPa": float(uts),
                }
                rows[index]["uncertainty"] = {"status": "not_calibrated_for_candidate", "interval_MPa": None}
                support = rows[index]["input_audit"].get("shared_support")
                if support is not None:
                    rows[index]["comparison_allowed"] = support["admitted"]
                    rows[index]["support_version"] = support["version"]
    return {
        "ok": True, "mode": mode,
        "model": {"version": "unified_alloy_tensile_v10_20260923", "source_checkpoint_sha256": WEIGHT_SHA,
                  "serving_weights_sha256": WEIGHT_SHA, "single_checkpoint": True},
        "display_allowed": False, "production_release_authorized": False,
        "results": rows, "elapsed_seconds": time.perf_counter() - started,
    }


def compare_many(items, loaded):
    """Keep the existing 1111 comparison envelope, but mark v10 as diagnostic."""
    if not isinstance(items, list) or not 1 <= len(items) <= 4096:
        raise InputError("batch_size", "items must contain 1-4096 objects")
    from service_adapters import OUTPUT_KEYS, adapt

    comparisons, requests, positions = [], [], []
    for index, item in enumerate(items):
        row = {"index": index, "status": "skipped_mapping", "display_allowed": False,
               "comparison_allowed": False, "production_release_authorized": False}
        try:
            if not isinstance(item, dict):
                raise InputError("invalid_item", "comparison item must be an object")
            service_domain = item.get("service_domain")
            actual = item.get("actual_inputs")
            request, audit = adapt(service_domain, actual, item.get("request_id"))
            if service_domain == "reusable_rocket_stainless" and actual.get("composition_status"):
                request["context"]["composition_status"] = actual["composition_status"]
            legacy = item.get("legacy_properties", {})
            if not isinstance(legacy, dict) or set(legacy) - set(OUTPUT_KEYS[service_domain].values()):
                raise InputError("legacy_properties", "legacy values must use the two mapped tensile property keys")
            for name, value in legacy.items():
                _number(value, f"legacy_properties.{name}")
            if service_domain == "reusable_rocket_stainless":
                process = actual["processing"]
                temperature_K = _number(process.get("solution_treatment_temperature_K"), "solution_treatment_temperature_K")
                duration_s = _number(process.get("solution_treatment_time_s"), "solution_treatment_time_s")
                if temperature_K <= 0 or duration_s < 0:
                    raise InputError("stainless_process", "invalid solution treatment temperature or time")
                request["process"]["parameters"] = {
                    "solution_temp_C": temperature_K - 273.15,
                    "solution_time_h": duration_s / 3600.0,
                }
                mapped = {"processing.solution_treatment_temperature_K", "processing.solution_treatment_time_s"}
                audit["mapped_fields"].extend(sorted(mapped))
                audit["unmodeled_fields"] = sorted(set(audit["unmodeled_fields"]) - mapped)
                audit["comparison_scope"] = "same_composition_temperature_form_quench_solution_temperature_time"
            elif service_domain == "ni_superalloy_hot_end":
                stage_text = actual.get("heat_treatment")
                if not isinstance(stage_text, str) or not stage_text.strip():
                    raise InputError("nickel_heat_treatment", "resolved heat_treatment stage string is required")
                request["process"]["v6_stages"] = stage_text
                audit["mapped_fields"].append("heat_treatment")
                audit["unmodeled_fields"] = sorted(set(audit["unmodeled_fields"]) - {"heat_treatment"})
                audit["comparison_scope"] = "same_composition_temperature_route_multistage_heat_treatment"
            row["mapping_audit"] = audit
            requests.append(request)
            positions.append(index)
        except Exception as exc:
            row["error"] = {"code": getattr(exc, "code", "mapping_error"), "message": str(exc)}
        comparisons.append(row)
    if requests:
        batch = predict_many(requests, loaded, mode="shadow")
        for index, prediction in zip(positions, batch["results"]):
            row = comparisons[index]
            row["shadow_result"] = prediction
            row["status"] = prediction["status"]
            row["comparison_allowed"] = prediction.get("comparison_allowed", False)
            if prediction["status"] == "predicted_diagnostic":
                mapping = OUTPUT_KEYS[items[index]["service_domain"]]
                projected = {mapping[key]: value for key, value in prediction["prediction"].items()}
                row["internal_property_projection_MPa"] = projected
                row["transformer_minus_legacy_MPa"] = {
                    key: value - items[index]["legacy_properties"][key]
                    for key, value in projected.items() if key in items[index]["legacy_properties"]
                }
                row["difference_is_measured_error"] = False
    return {
        "ok": True, "mode": "shadow", "display_allowed": False,
        "production_release_authorized": False,
        "model": {"version": "unified_alloy_tensile_v10_20260923", "source_checkpoint_sha256": WEIGHT_SHA,
                  "serving_weights_sha256": WEIGHT_SHA, "single_checkpoint": True},
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--preprocessor", type=Path, required=True)
    parser.add_argument("--descriptor-properties", type=Path, required=True)
    parser.add_argument("--code-dir", type=Path, required=True)
    parser.add_argument("--support-code-dir", type=Path, required=True)
    parser.add_argument("--v2-runtime", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--mode", choices=("shadow", "research"), default="shadow")
    parser.add_argument("--service-comparison", action="store_true")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    code = 0
    try:
        if not 1 <= args.threads <= 8 or (args.health_check and args.service_comparison):
            raise ValueError("invalid thread count or incompatible modes")
        loaded = load_frozen(args)
        if args.health_check:
            if args.request is not None:
                raise ValueError("health check uses only its frozen request")
            root = Path(__file__).resolve().parent
            payload = json.loads((root / "alloy_v10_health_request.json").read_text(encoding="utf-8"))
            expected = json.loads((root / "alloy_v10_health_expected.json").read_text(encoding="utf-8"))
            result = predict_many(payload["requests"], loaded, mode="shadow")
            row = result["results"][0]
            if row["status"] != "predicted_diagnostic" or any(
                abs(row["prediction"][key] - value) > 0.05 for key, value in expected.items()
            ):
                raise RuntimeError("frozen raw-input health prediction mismatch")
            result["health_check"] = "real_raw_input_inference_passed"
        else:
            if args.request is None or args.request.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("request is missing or exceeds 16 MiB")
            payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
            if args.service_comparison:
                if args.mode != "shadow" or not isinstance(payload, dict) or set(payload) != {"items"}:
                    raise InputError("request_envelope", "shadow comparison expects exactly one items array")
                result = compare_many(payload["items"], loaded)
            else:
                if not isinstance(payload, dict) or set(payload) != {"requests"}:
                    raise InputError("request_envelope", "expected exactly one requests array")
                result = predict_many(payload["requests"], loaded, mode=args.mode)
    except Exception as exc:
        result = {"ok": False, "mode": args.mode, "display_allowed": False,
                  "production_release_authorized": False, "error_type": type(exc).__name__,
                  "error": str(exc)}
        code = 2
    args.response.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.response.with_name(args.response.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, args.response)
    finally:
        if temporary.exists():
            temporary.unlink()
    rows = result.get("comparisons", result.get("results", []))
    print(json.dumps({"ok": result["ok"], "status_counts": {status: sum(row["status"] == status for row in rows)
                                                          for status in sorted({row["status"] for row in rows})},
                      "response": str(args.response)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
