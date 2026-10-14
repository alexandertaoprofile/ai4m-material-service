# Material Database Execution Contract

Before changing this service, read `docs/mature_material_service_guide.md` and
the frontend-contract tests in `tests/test_mature_material_service.py`.

## Non-negotiable frontend protocol

- Keep `/start` and `/mature-material/start`.
- Keep the fixed step ID `FILAMENT_SELECTION_OPTIMIZATION` in progress events,
  content markers, and image events.
- Keep the event order `[start]` → `progress` → two existing content sections
  → optional `MaterialsPNG` → `result` → `[end]`.
- Do not introduce new frontend event types, step IDs, or image types for an
  internal workflow. New workflows may change only the factual Markdown,
  manifest content, and PNG assets emitted through the existing protocol.
- Run `PYTHONPATH=. pytest -q tests/test_mature_material_service.py` after any
  transport, workflow, asset, or routing change.

## Workflow boundary

- The conductive-lubricant workflow is an internal 1105 branch selected only
  when both lubrication and electrical intent are explicit.
- It queries traceable evidence and produces initial-screening reports; it does
  not run models, infer missing formulation fractions, or claim mechanism/
  long-term validation.
- All other requests continue through the existing mature-material catalogue
  workflow.
