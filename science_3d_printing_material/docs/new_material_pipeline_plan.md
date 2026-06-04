# Inorganic New-Material Pipeline Plan

## Positioning

`inorganic_new_material` is the future mainline for inorganic new-material discovery. It should not behave like the existing-material MP lookup service. The target chain is:

1. Parse user requirements into generation constraints.
2. Generate candidate structures with MatterGen or a compatible generator.
3. Validate and complete properties with ADiT/pymatgen or a later validation backend.
4. Rank candidates using validated properties and user goals.
5. Emit concise frontend payloads and durable manifests.

## Current Safe Refactor

This pass only adds a normalized contract layer under `src/material_workflow/`:

- `schemas.py`: shared dataclasses and manifest shapes.
- `generation.py`: MatterGen boundary, currently returns `not_configured` unless a runner is injected.
- `validation.py`: ADiT/pymatgen boundary, currently returns explicit missing/not-configured states.
- `ranking.py`: deterministic ranking helpers with no random tie-breaking.
- `emitters.py`: frontend payload and manifest writers.
- `pipeline.py`: orchestration skeleton that is safe before backends are wired.
- `prompts.py`: prompt rules for requirement parsing and output summaries.

No running service path is changed in this pass.

## Runtime Contract

The pipeline must never invent generated structures or validation values. Missing fields should remain absent or be shown as `N/A` in presentation layers. This is especially important before MatterGen and validation backends are configured.

## Result Layout

Recommended future layout:

```text
results/new_material/<taskid>/
  generation/generation_manifest.json
  validation/<candidate_id>.validation.json
  new_material_pipeline_manifest.json
```

## Next Implementation Steps

1. Connect requirement extraction in `team_config.py` to `GenerationConstraint` without changing user-visible behavior.
2. Add a real MatterGen runner that writes CIF/structure assets into the generation directory.
3. Adapt ADiT/pymatgen validation so generated CIFs can be evaluated independently of MP selected-structure manifests.
4. Add frontend GIF/summary assets only after candidate and validation manifests exist.
5. Keep `team_config_en.py` available for future English flow design, but do not route traffic to it yet.
