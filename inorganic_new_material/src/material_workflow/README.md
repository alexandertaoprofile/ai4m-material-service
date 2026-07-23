# material_workflow

`material_workflow` holds the domain building blocks for the active inorganic
new-material service. `src/team_config.py` is the orchestration layer: it owns
the WebSocket order, role/action lifecycle and calls into the modules below.

## Module Boundaries

- `constraints.py`, `llm_constraint_inference.py`: normalize explicit and
  upstream constraints; the LLM may only propose a validated exploration
  element system when deterministic extraction is insufficient.
- `schemas.py`, `generation.py`, `validation.py`, `mattersim.py`, `ranking.py`,
  `pipeline.py`, `upstream_api.py`: MatterGen generation, pymatgen admission,
  MatterSim/MP screening, ranking and manifest-backed pipeline execution.
- `presentation.py`: renders and publishes PNG/GIF/GLB evidence through the
  established `MaterialsPNG` / `MaterialsGLB` frontend protocol.
- `emitters.py`, `payloads.py`: build HTTP/WebSocket payloads and manifests.
- `llm_streaming.py`: relays authoritative Markdown through the existing token
  stream, with a deterministic text fallback.

The former MP lookup, ALIGNN completion, static database-picture and generic
formula-routing modules were removed with the retired `Coding` action. They
belonged to existing-material retrieval, not database-external crystal discovery.

`src/service_paths.py` is the only owner of the task-artifact root. The current
path retains the historical `MNS_CaseHub` directory only for compatibility;
callers must import the path constant rather than reconstruct it.

## Naming

This package is intentionally not named `utils`.
Most modules here encode domain workflow semantics rather than generic helpers, so keeping
them under `material_workflow` makes dependencies and ownership clearer.

## Dependency Rule

Prefer lightweight imports at module load time.
Heavy optional dependencies, network SDKs, and vendor clients should be imported lazily inside
the functions that need them so service startup remains stable.
