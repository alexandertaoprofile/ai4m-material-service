#!/usr/bin/env python3
"""Start a service with explicit local resource configuration.

The 1111 adapter uses the existing dispatcher's dependency-injection arguments;
the deployed source snapshots and their historical default paths stay intact.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys

SERVICES = {"1105": "material_database", "1111": "material_composition_optimization",
            "1115": "material_discovery", "1116": "material_validation"}


def configure_recipe():
    from src.alloy_workflow import recipe_shadow
    names = ("MATERIAL_RECIPE_DISPLAY_ROOT", "MATERIAL_RECIPE_RUNTIME", "MATERIAL_RECIPE_PYTHON")
    if not all(os.getenv(name) for name in names):
        raise ValueError("1111 needs configured report, optimization runtime and Python paths")
    display, runtime, python = (Path(os.environ[name]) for name in names)
    recipe_shadow.DISPLAY_ROOT = display
    original = recipe_shadow.RecipeShadowDispatcher

    class ConfiguredRecipeDispatcher(original):
        def __init__(self, results_root, **kwargs):
            kwargs.setdefault("display_root", display)
            kwargs.setdefault("runtime", runtime)
            kwargs.setdefault("python", python)
            super().__init__(results_root, **kwargs)

    recipe_shadow.RecipeShadowDispatcher = ConfiguredRecipeDispatcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Import the application and check /roles registration without listening")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error(f"Configuration file does not exist: {args.env_file}")
    from dotenv import dotenv_values
    for key, value in dotenv_values(args.env_file).items():
        if value is not None:
            os.environ[key] = value
    if args.check:
        # Role construction initializes an OpenAI client even though /roles
        # performs no model request. The placeholder applies only to this check.
        os.environ.setdefault("OPENAI_API_KEY", "local-import-check")
    service = Path(__file__).resolve().parents[1] / SERVICES[args.service]
    os.chdir(service)
    sys.path.insert(0, str(service))
    if args.service == "1111":
        configure_recipe()
    if args.check:
        import importlib
        from fastapi.testclient import TestClient
        app = importlib.import_module("main").app
        with TestClient(app) as client:
            response = client.get("/roles")
            response.raise_for_status()
        print(f"{args.service}: application imported, /roles HTTP {response.status_code}")
    else:
        sys.argv = [str(service / "main.py")]
        runpy.run_path(str(service / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
