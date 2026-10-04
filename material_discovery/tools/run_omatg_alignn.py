"""Load each cached ALIGNN property head once for retained relaxed structures."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    site = os.getenv("ALIGNN_SITE_PACKAGES", "/data/mamba/envs/alignn-gpu-test/lib/python3.10/site-packages")
    if site and site not in sys.path:
        sys.path.append(site)
    import torch
    import alignn.pretrained as pretrained
    from jarvis.core.atoms import Atoms
    from alignn.graphs import Graph
    rows = {item["candidate_id"]: {"predictions": {}, "errors": [], "property_status": {
        key: {"code": "checkpoint_missing"} for key in request["properties"]}} for item in request["candidates"]}
    structures, graphs = {}, {}
    for item in request["candidates"]:
        try:
            if not item.get("structure"):
                raise ValueError("Missing relaxed structure descriptors")
            atoms = Atoms(**item["structure"])
            structures[item["candidate_id"]] = atoms
            graph, line = Graph.atom_dgl_multigraph(atoms, cutoff=8.0, max_neighbors=12)
            graphs[item["candidate_id"]] = [graph.to(pretrained.device), line.to(pretrained.device), torch.tensor(atoms.lattice_mat).to(pretrained.device)]
        except Exception as exc:
            rows[item["candidate_id"]]["errors"].append(str(exc))
            rows[item["candidate_id"]]["property_status"] = {key: {"code": "graph_failed"} for key in request["properties"]}
    version = importlib.metadata.version("alignn")
    for key, spec in request["properties"].items():
        for name in spec["models"]:
            archive = Path(os.getenv("ALIGNN_CHECKPOINT_ROOT", "/data/se42/omatg_local/checkpoints/alignn")) / (name + ".zip")
            if not archive.is_file():
                archive = Path(pretrained.__file__).parent / (name + ".zip")
            if not archive.is_file():
                continue  # Never fetch a model into the environment on a service request.
            try:
                # The legacy Python executable is broken on this host. Reuse its
                # trusted cached package/weights with a healthy Python 3.10 runtime.
                # Load the model without the old loader's temporary-file/download path.
                import io
                import zipfile
                from alignn.models.alignn import ALIGNN, ALIGNNConfig
                with zipfile.ZipFile(archive) as zipped:
                    names = zipped.namelist()
                    config_name = next(item for item in names if item.endswith("config.json"))
                    checkpoints = [item for item in names if "checkpoint_" in item and item.endswith(".pt") or item.endswith("best_model.pt")]
                    config = json.loads(zipped.read(config_name))
                    model = ALIGNN(ALIGNNConfig(**config["model"]))
                    payload = torch.load(io.BytesIO(zipped.read(checkpoints[-1])), map_location=pretrained.device, weights_only=False)
                    model.load_state_dict(payload["model"])
                    model.to(pretrained.device).eval()
                checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            except Exception as exc:
                print(f"ALIGNN head {name} unavailable: {exc}", flush=True)
                for candidate_id, row in rows.items():
                    if candidate_id in graphs and key not in row["predictions"]:
                        row["property_status"][key] = {"code": "model_load_failed"}
                continue
            for item in request["candidates"]:
                candidate_id = item["candidate_id"]
                if candidate_id not in graphs or key in rows[candidate_id]["predictions"]:
                    continue
                try:
                    with torch.no_grad():
                        value = float(model(graphs[candidate_id]).detach().cpu().numpy().reshape(-1)[0])
                    if not math.isfinite(value):
                        raise ValueError("Nonfinite ALIGNN prediction")
                    rows[candidate_id]["predictions"][key] = {"value": value, "unit": spec["unit"], "label": spec["label"],
                        "model": name, "model_version": "ALIGNN " + version, "checkpoint_sha256": checksum,
                        "structure_path": item["cif_path"], "structure_state": "MatterSim 松弛后结构",
                        "descriptor": "原子图与键角线图；cutoff=8 Å；max_neighbors=12",
                        "display_method": spec["display_method"], "evidence_level": "C：结构模型快速预测"}
                    rows[candidate_id]["property_status"].pop(key, None)
                except Exception as exc:
                    rows[candidate_id]["errors"].append(f"{key}: {exc}")
                    rows[candidate_id]["property_status"][key] = {"code": "nonfinite" if "Nonfinite" in str(exc) else "failed"}
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if all(key in row["predictions"] for row in rows.values()):
                break
    Path(request["output_path"]).write_text(json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
