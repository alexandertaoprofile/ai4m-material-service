#!/usr/bin/env python3
"""Create one Linux environment from pinned, service-specific dependency groups."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# These environments intentionally use different Python/Torch versions.
PYTHON = {"web": "3.10", "numeric": "3.10", "recipe": "3.13", "omatg": "3.12", "alignn": "3.10"}
PACKAGES = {
    "web": "fastapi uvicorn python-dotenv loguru pydantic python-multipart openai numpy pandas matplotlib scipy pillow httpx requests websocket-client websockets boto3 pyyaml nest-asyncio aiohttp chardet rich tiktoken fire termcolor colorama markdown-it-py typing-extensions pydantic-settings tenacity aiofiles GitPython gitignore-parser parse beautifulsoup4 openpyxl nbclient nbformat jupyter-client python-docx ruamel.yaml zhipuai dashscope qianfan google-generativeai",
    "numeric": "mattersim torch-geometric numpy scipy pandas scikit-learn joblib pymatgen ase lmdb pyyaml hydra-core lightning-utilities torch-ema torch-runstats e3nn pynanoflann trimesh pillow matplotlib",
    "recipe": "numpy scipy pandas scikit-learn joblib pymoo matplotlib pyyaml pytest",
    "omatg": "numpy scipy lightning jsonargparse torch-geometric torchdiffeq torchsde lmdb loguru ase pytest tqdm smact wandb scikit-learn matplotlib pymatgen spglib pyyaml matminer huggingface-hub pyarrow mace-torch torch-sim-atomistic warp-lang docstring-parser typeshed-client",
    "alignn": "numpy scipy pandas scikit-learn alignn jarvis-tools pydantic pydantic-settings tqdm matplotlib networkx spglib ase pymatgen requests toolz lmdb pyparsing torchdata",
}


def normalize(name):
    return name.lower().replace("_", "-").replace(".", "-")


def pins(environment, snapshot):
    rows = snapshot[environment]["packages"]
    # ALIGNN's supplementary environment supplies its graph/model libraries.
    if environment == "alignn":
        base = {normalize(row["name"]): row for row in rows}
        supplement = {normalize(row["name"]): row for row in snapshot["alignn_supplement"]}
        for name in ("alignn", "jarvis-tools", "dgl", "torchdata"):
            if name in supplement:
                base[name] = supplement[name]
        rows = list(base.values())
    table = {}
    for row in rows:
        table.setdefault(normalize(row["name"]), []).append(row["version"])
    result = []
    names = PACKAGES[environment].split()
    if environment == "web":
        names += "google-api-core google-api-python-client google-auth google-auth-httplib2 googleapis-common-protos protobuf grpcio grpcio-status".split()
        names.append("semantic-kernel")
    for name in names:
        versions = table.get(normalize(name), [])
        if not versions:
            # Optional names absent from the actual environment are not invented.
            continue
        # Alpha's SK decorator uses the legacy dependency contract. Restore a
        # resolvable Web environment; numerical models use separate workers.
        web_compatibility = {"numpy": "1.26.4", "python-dotenv": "1.0.0", "pillow": "11.3.0"}
        if environment == "web" and name in web_compatibility:
            versions = [web_compatibility[name]]
        alignn_compatibility = {"numpy": "1.26.4", "matplotlib": "3.8.4", "pyparsing": "2.4.7"}
        if environment == "alignn" and name in alignn_compatibility:
            versions = [alignn_compatibility[name]]
        if len(set(versions)) != 1:
            observed_web = {"numpy": "1.26.4", "pillow": "11.3.0"}
            if environment == "web" and name in observed_web:
                versions = [observed_web[name]]
            else:
                raise ValueError(f"Ambiguous installed versions for {name}: {versions}")
        package = "jsonargparse[signatures]" if environment == "omatg" and name == "jsonargparse" else name
        result.append(f"{package}=={versions[0]}")
    return result


def commands(environment, prefix, snapshot):
    python = str(prefix / "bin/python")
    pip = [python, "-m", "pip", "install"]
    result = [["micromamba", "create", "-y", "-p", str(prefix), "-c", "conda-forge", f"python={PYTHON[environment]}", "pip"]]
    torch = {"web": None, "numeric": ("2.2.1", "cu118"), "recipe": ("2.10.0", "cpu"),
             "omatg": ("2.8.0", "cu126"), "alignn": ("2.2.1", "cu118")}[environment]
    if torch:
        result.append(pip + [f"torch=={torch[0]}", "--index-url", f"https://download.pytorch.org/whl/{torch[1]}"])
    if environment == "numeric":
        result.append(pip + ["torchaudio==2.2.1", "torchvision==0.17.1", "--no-deps",
                             "--index-url", "https://download.pytorch.org/whl/cu118"])
        result.append(pip + ["torch_scatter==2.1.2+pt22cu118", "torch_sparse==0.6.18+pt22cu118", "torch_cluster==1.6.3+pt22cu118",
                             "--no-deps", "--find-links", "https://data.pyg.org/whl/torch-2.2.1+cu118.html"])
    elif environment == "omatg":
        result.append(pip + ["torch_scatter==2.1.2+pt28cu126", "--no-deps", "--find-links", "https://data.pyg.org/whl/torch-2.8.0+cu126.html"])
    elif environment == "alignn":
        # ALIGNN 2025.4.1 declares DGL <=1.1.1; this version also avoids
        # GraphBolt's platform-specific binary loader.
        result.append(pip + ["dgl==1.1.1"])
    dependencies = pins(environment, snapshot)
    if environment == "numeric":
        # MatterSim accepts newer Torch stacks; constrain all three packages
        # together so its transitive dependencies retain the tested CUDA build.
        dependencies += ["torch==2.2.1", "torchaudio==2.2.1", "torchvision==0.17.1"]
    result.append(pip + dependencies)
    if environment == "web":
        result.append(pip + ["pytest==9.1.1", "pytest-asyncio==1.3.0"])
        result.append([python, str(ROOT / "tools/apply_environment_compatibility.py"), "--prefix", str(prefix)])
    if environment == "omatg":
        result.append(pip + ["--no-deps", "-e", str(ROOT / "third_party/OMatG")])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=PYTHON)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Create the environment; default prints the plan")
    parser.add_argument("--python", type=Path, help="Use an existing matching Python to create an isolated venv instead of micromamba")
    parser.add_argument("--index-url", default="https://pypi.org/simple", help="Python package index for ordinary dependencies")
    parser.add_argument("--resume", action="store_true", help="Resume dependency installation into a previously created environment")
    parser.add_argument("--cpu", action="store_true", help="Use CPU PyTorch for ALIGNN (generation and relaxation still require their GPU environments)")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("The current GPU deployment target is Linux; run in Linux or WSL2")
    prefix = args.prefix.resolve()
    if not prefix.is_relative_to(Path("/data")) or prefix == Path("/data"):
        parser.error("Use a dedicated environment directory below /data")
    snapshot = json.loads((ROOT / "env/current/installed-20261008.json").read_text())
    plan = commands(args.environment, prefix, snapshot)
    if args.cpu:
        if args.environment != "alignn":
            parser.error("--cpu applies to ALIGNN; recipe already uses CPU PyTorch")
        for command in plan:
            if "--index-url" in command:
                command[command.index("--index-url") + 1] = "https://download.pytorch.org/whl/cpu"
    label = "alignn-cpu" if args.cpu else args.environment
    constraints = ROOT / "env/validated" / (label + ".txt")
    if constraints.is_file():
        for command in plan:
            if command[1:4] == ["-m", "pip", "install"]:
                command += ["--constraint", str(constraints)]
    if args.python:
        expected = PYTHON[args.environment]
        observed = subprocess.check_output([str(args.python), "-c", "import sys;print('.'.join(map(str,sys.version_info[:2])))"], text=True).strip()
        if observed != expected:
            parser.error(f"{args.environment} requires Python {expected}; supplied {observed}")
        plan[0] = [str(args.python), "-m", "venv", str(prefix)]
    if args.resume:
        if not (prefix / "bin/python").is_file():
            parser.error("Resume requires an existing environment Python")
        plan = plan[1:]
    # Older base environments ship pip 23, which rejects some current wheel
    # metadata names. Upgrade within the new environment before installing.
    plan.insert(0 if args.resume else 1, [str(prefix / "bin/python"), "-m", "pip", "install", "pip==26.2.1"])
    if not args.execute:
        print(json.dumps({"environment": args.environment, "commands": plan}, indent=2))
        return
    if prefix.exists() and not args.resume:
        parser.error("Target environment already exists; use a new prefix")
    cache = prefix.parent / "install-cache"
    cache.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PIP_CACHE_DIR=str(cache / "pip"), TMPDIR=str(cache), XDG_CACHE_HOME=str(cache / "xdg"),
                       MAMBA_ROOT_PREFIX=str(cache / "mamba"), PIP_INDEX_URL=args.index_url)
    for command in plan:
        subprocess.run(command, check=True, env=environment)
    print(f"Installed {args.environment}: {prefix}. Run the corresponding preflight and real task next.")


if __name__ == "__main__":
    main()
