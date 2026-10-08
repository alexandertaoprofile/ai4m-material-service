"""Isolated glass Transformer shadow CLI; never returns a public service result.

Install next to oxide_glass_shadow_contract.py. The trained research inference
files remain in --model-code-dir. The caller must place --response in a private
shadow directory, outside public manifests and asset uploads.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid

from oxide_glass_shadow_contract import ContractError, normalize_request


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_frozen(
    checkpoint_path: Path,
    model_code_dir: Path,
    expected_sha256: str,
    expected_infer_sha256: str,
    expected_train_sha256: str,
):
    actual = digest(checkpoint_path)
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError("glass checkpoint SHA-256 mismatch")
    for filename, expected in (
        ("infer_oxide_glass_transformer.py", expected_infer_sha256),
        ("train_oxide_glass_transformer.py", expected_train_sha256),
    ):
        if digest(model_code_dir / filename).lower() != expected.lower():
            raise RuntimeError(f"glass source code SHA-256 mismatch: {filename}")
    import torch

    # The frozen checkpoint contains tensors and primitive dictionaries only.
    # Never use weights_only=False for an untrusted or unchecked artifact.
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or not {"constructor", "state_dict", "preprocessor", "model_name"} <= set(state):
        raise RuntimeError("glass checkpoint has the wrong structure")
    sys.path.insert(0, str(model_code_dir.resolve()))
    research = importlib.import_module("infer_oxide_glass_transformer")
    model = research.OxideGlassTransformer(**state["constructor"])
    model.load_state_dict(state["state_dict"], strict=True)
    model.eval()
    torch.set_num_threads(1)
    return state, model, research, torch, actual


def infer_many(requests, state, model, research, torch):
    """Batch the frozen scalar encoder without changing descriptor algorithms.

    Its tensors and inverse transforms are checked against both the frozen
    scalar inference and the independent training encoder in the release audit.
    A bounded microbatch limits memory even for the maximum service payload.
    """
    import numpy as np
    pre = state['preprocessor']
    outputs = []
    for offset in range(0, len(requests), 256):
        part = requests[offset:offset + 256]
        validated = [research.validate_request(request, pre) for request in part]
        n, tokens = len(part), int(pre['max_tokens'])
        species = np.zeros((n, tokens), dtype=np.int64)
        fractions = np.zeros((n, tokens), dtype=np.float32)
        descriptors = []
        tasks, conditions, masks = [], [], []
        for i, (components, task, condition, _) in enumerate(validated):
            for j, (name, fraction) in enumerate(components):
                species[i, j] = int(pre['vocab'][name])
                fractions[i, j] = fraction
            descriptors.append(research.descriptor_vector(components))
            tasks.append(pre['tasks'].index(task))
            conditions.append(0.0 if condition is None else
                              (condition - float(pre['condition_mean'])) / float(pre['condition_std']))
            masks.append(0.0 if condition is None else 1.0)
        descriptor = np.asarray(descriptors, dtype=np.float32)
        descriptor = ((descriptor - np.asarray(pre['descriptor_mean'], dtype=np.float32)) /
                      np.asarray(pre['descriptor_std'], dtype=np.float32))
        batch = {'species': torch.from_numpy(species), 'fractions': torch.from_numpy(fractions),
                 'descriptors': torch.from_numpy(descriptor),
                 'task': torch.tensor(tasks, dtype=torch.long),
                 'condition': torch.tensor(conditions, dtype=torch.float32),
                 'condition_mask': torch.tensor(masks, dtype=torch.float32)}
        with torch.inference_mode():
            pred_z = model(batch).cpu().numpy()
        for i, (_, task, condition, _) in enumerate(validated):
            stats = pre['target_stats'][task]
            transformed = float(pred_z[i]) * float(stats['std']) + float(stats['mean'])
            value = float(research.target_inverse(task, np.asarray([transformed]))[0])
            if not math.isfinite(value):
                raise RuntimeError('nonfinite glass prediction')
            outputs.append({'prediction': value, 'unit': research.UNIT[task],
                            'task': task, 'condition_temperature_C': condition})
    return outputs


def run_batch(payload, state, model, research, torch, checkpoint_sha256):
    if not isinstance(payload, dict) or set(payload) != {"requests"}:
        raise ValueError("batch must contain exactly one requests array")
    requests = payload["requests"]
    if not isinstance(requests, list) or not 1 <= len(requests) <= 4096:
        raise ValueError("requests must contain 1-4096 items")
    started = time.perf_counter()
    results = []
    admitted_requests = []
    admitted_rows = []
    for index, request in enumerate(requests):
        row = {
            "index": index,
            "request_id": request.get("request_id") if isinstance(request, dict) else None,
            "status": "skipped",
            "display_allowed": False,
            "comparison_allowed": False,
            "production_release_authorized": False,
        }
        try:
            normalized = normalize_request(request, state["preprocessor"])
            row["input_audit"] = {
                "composition_mol_percent": normalized["composition_mol_percent"],
                "composition_total_received_mol_percent": normalized["composition_total_received_mol_percent"],
                "task": normalized["task"],
                "condition_temperature_C": normalized["condition_temperature_C"],
                "empirical_support_status": "not_yet_calibrated_for_service",
            }
            if not normalized["shadow_task_admitted"]:
                row["reason"] = "property_semantics_or_quality_not_admitted_to_service_shadow"
            else:
                model_request = {
                    "composition_mol_percent": normalized["composition_mol_percent"],
                    "task": normalized["task"],
                    "condition_temperature_C": normalized["condition_temperature_C"],
                }
                admitted_requests.append(model_request)
                admitted_rows.append(row)
        except ContractError as exc:
            row["status"] = "invalid_input"
            row["error"] = {"code": exc.code, "message": str(exc)}
        except Exception as exc:
            row["status"] = "inference_error"
            row["error"] = {"code": "internal_shadow_failure", "message": type(exc).__name__}
        results.append(row)
    try:
        predictions = infer_many(admitted_requests, state, model, research, torch)
        for row, raw in zip(admitted_rows, predictions):
            row['status'] = 'predicted_diagnostic'
            row['prediction'] = {'value': raw['prediction'], 'unit': raw['unit'], 'task': raw['task']}
            row['uncertainty'] = {'status': 'not_calibrated_for_candidate', 'interval': None}
    except Exception as exc:
        for row in admitted_rows:
            row['status'] = 'inference_error'
            row['error'] = {'code': 'internal_shadow_failure', 'message': type(exc).__name__}
    return {
        "ok": True,
        "mode": "shadow",
        "model": {
            "name": state["model_name"],
            "checkpoint_sha256": checkpoint_sha256,
            "training_data_sha256": state.get("training_data_sha256"),
            "training_rows": state.get("training_rows"),
        },
        "display_allowed": False,
        "production_release_authorized": False,
        "license_review_required": True,
        "results": results,
        "elapsed_seconds": time.perf_counter() - started,
    }


def verify_health(output, expected) -> None:
    rows = output.get("results")
    if not output.get("ok") or not isinstance(rows, list) or len(rows) != len(expected):
        raise RuntimeError("glass health response shape mismatch")
    actual = {row.get("request_id"): row for row in rows}
    if set(actual) != set(expected):
        raise RuntimeError("glass health request identifiers mismatch")
    for request_id, reference in expected.items():
        row = actual[request_id]
        if "status" in reference:
            if row.get("status") != reference["status"]:
                raise RuntimeError(f"glass health skip mismatch: {request_id}")
            continue
        prediction = row.get("prediction") or {}
        if row.get("status") != "predicted_diagnostic" or prediction.get("task") != reference["task"] or prediction.get("unit") != reference["unit"]:
            raise RuntimeError(f"glass health prediction contract mismatch: {request_id}")
        value = prediction.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value - reference["value"]) > 0.0001:
            raise RuntimeError(f"glass health numeric mismatch: {request_id}")
    if output.get("display_allowed") or output.get("production_release_authorized"):
        raise RuntimeError("glass health privacy contract mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-code-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-infer-sha256", required=True)
    parser.add_argument("--expected-train-sha256", required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    if args.health_check == (args.request is not None):
        parser.error("choose either --health-check or --request")
    state, model, research, torch, sha = load_frozen(
        args.checkpoint, args.model_code_dir, args.expected_sha256,
        args.expected_infer_sha256, args.expected_train_sha256,
    )
    if args.health_check:
        payload = json.loads((Path(__file__).resolve().parent / "glass_shadow_example_batch.json").read_text(encoding="utf-8"))
    else:
        if args.request.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("glass shadow request exceeds 16 MiB")
        payload = json.loads(args.request.read_text(encoding="utf-8"))
    output = run_batch(payload, state, model, research, torch, sha)
    if args.health_check:
        expected = json.loads((Path(__file__).resolve().parent / "glass_shadow_health_expected.json").read_text(encoding="utf-8"))
        verify_health(output, expected)
        output["health_check"] = "real_raw_input_inference_passed"
    args.response.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.response.with_name(args.response.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, args.response)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(json.dumps({
        "ok": output["ok"], "mode": output["mode"],
        "predicted_diagnostic": sum(row["status"] == "predicted_diagnostic" for row in output["results"]),
        "skipped": sum(row["status"] == "skipped" for row in output["results"]),
        "errors": sum(row["status"] in {"invalid_input", "inference_error"} for row in output["results"]),
        "response": str(args.response),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
