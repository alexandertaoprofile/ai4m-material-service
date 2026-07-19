# material_workflow

`material_workflow` holds the reusable building blocks for the inorganic new-material service.
The top-level `src/team_config.py` should stay as the orchestration layer: websocket order,
stage transitions, and calls into these helpers.

## Module Boundaries

- `formula_router.py`: normalizes user text and extracts formula/system candidates.
- `material_profiles.py`: provides lightweight formula-facing labels and display metadata.
- `database_pics.py`: resolves and uploads static database-introduction images.
- `mp_results.py`: collects MP result files and builds structured material parameters.
- `alignn_completion.py`: runs ALIGNN property completion and formats the result section.
- `llm_streaming.py`: streams LLM output and normalizes frontend-safe text.
- `frontend_assets.py`: uploads result assets and emits frontend payloads.
- `payloads.py`: builds websocket payload envelopes.
- `prompts.py`: stores prompt templates shared by orchestration stages.
- `schemas.py`, `ranking.py`, `validation.py`, `generation.py`, `pipeline.py`: reserved workflow primitives for future MatterGen/validation expansion.

## Naming

This package is intentionally not named `utils`.
Most modules here encode domain workflow semantics rather than generic helpers, so keeping
them under `material_workflow` makes dependencies and ownership clearer.

## Dependency Rule

Prefer lightweight imports at module load time.
Heavy optional dependencies, network SDKs, and vendor clients should be imported lazily inside
the functions that need them so service startup remains stable.
