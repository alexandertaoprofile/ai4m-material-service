#!/usr/bin/env python3
"""Check restored 1111 prediction, optimization and local report generation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--numeric-python", type=Path, required=True)
    parser.add_argument("--recipe-python", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    environment = dict(os.environ, MATERIAL_LEGACY_PYTHON=str(args.numeric_python),
                       MATERIAL_RESEARCH_PYTHON=str(args.recipe_python), OMP_NUM_THREADS="1",
                       MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", TMPDIR=str(args.output_dir),
                       MPLCONFIGDIR=str(args.output_dir / "matplotlib"))
    (args.output_dir / "matplotlib").mkdir()
    request, response = args.output_dir / "propose.json", args.output_dir / "propose-response.json"
    scope = {"model_domain": "reusable_rocket_stainless", "num_candidates": 10, "test_temperature_K": 293}
    request.write_text(json.dumps({"operation": "propose", "constraints": scope}))
    subprocess.run([str(args.numeric_python), str(ROOT / "tools/worker_python.py"), "run", "-p",
                    str(args.numeric_python.parent.parent), "python",
                    str(args.resources / "packages/hea_surrogate/tools/service_runner.py"),
                    "--request", str(request), "--response", str(response)], env=environment, check=True, timeout=180)
    payload = json.loads(response.read_text())
    if payload.get("ok") is not True:
        raise RuntimeError(payload.get("error"))
    task = payload["result"]
    task["_resolved_scope"] = scope
    source = (
        "import json,sys;from pathlib import Path;sys.path.insert(0," + repr(str(ROOT / "packages/recipe_report")) + ");"
        "from shadow_recipe_runner import build_request,execute;task=" + repr(task) + ";"
        "request=build_request(task,limits={'population':8,'model_evaluations':32,'proposals':2560,'seconds':15});"
        "result=execute(task," + repr(str(args.resources / "packages/recipe/runtime")) + ",Path(" + repr(str(args.output_dir / "report")) + "),request=request);"
        "print(json.dumps(result,ensure_ascii=False));assert result['ok'] and result['results'][0]['status']=='completed'"
    )
    completed = subprocess.run([str(args.recipe_python), "-c", source], env=environment,
                               check=True, capture_output=True, text=True, timeout=120)
    artifacts = [str(path.relative_to(args.output_dir)) for path in args.output_dir.rglob("*")
                 if path.is_file() and path.suffix in {".md", ".png", ".json"}]
    if not any(name.endswith(".md") for name in artifacts) or not any(name.endswith(".png") for name in artifacts):
        raise RuntimeError("Optimization did not produce Markdown and image artifacts")
    report = {"ok": True, "seconds": round(time.monotonic() - started, 3), "artifacts": artifacts,
              "output": completed.stdout[-5000:]}
    (args.output_dir / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
