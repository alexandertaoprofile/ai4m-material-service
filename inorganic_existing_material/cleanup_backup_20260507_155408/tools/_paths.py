# tools/_paths.py
import os

def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def material_discovery_results_root() -> str:
    return os.path.join(
        repo_root(),
        "src",
        "MNS_CaseHub",
        "cases",
        "material_discovery_demo",
        "results",
    )

def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path

def task_results_dir(taskid_fs: str) -> str:
    # .../results/mp/<taskid>/
    base = material_discovery_results_root()
    return ensure_dir(os.path.join(base, "mp", taskid_fs))

def formula_dir(taskid_fs: str, formula_tag: str) -> str:
    # .../results/mp/<taskid>/<formula_tag>/
    return ensure_dir(os.path.join(task_results_dir(taskid_fs), formula_tag))

def candidate_dir(taskid_fs: str, formula_tag: str, material_id: str) -> str:
    # .../results/mp/<taskid>/<formula_tag>/candidates/<material_id>/
    return ensure_dir(os.path.join(formula_dir(taskid_fs, formula_tag), "candidates", material_id))

def safe_taskid(taskid: str) -> str:
    taskid = (taskid or "").strip() or "no_taskid"
    return taskid.replace("/", "_").replace("\\", "_")

def safe_formula_tag(formula: str) -> str:
    # e.g. "Li6PS5Cl" or "Li3PS4" -> keep as-is, but strip weird chars
    f = (formula or "").strip() or "unknown_formula"
    return "".join([c for c in f if c.isalnum() or c in ("-", "_")])
