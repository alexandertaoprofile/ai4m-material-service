# Inorganic New-Material Discovery Pipeline

## Positioning

`inorganic_new_material` is a generative inorganic-discovery service, not an
existing-material lookup service.  Its executable chain is:

1. Normalize an upstream envelope into a `GenerationConstraint`.
2. Run conditional MatterGen generation from an element system and supported
   numerical property target.
3. Perform pymatgen structural admission checks on generated CIFs.
4. Relax admitted candidates with MatterSim.
5. Query only the candidate chemical system and its subsystems from Materials
   Project, construct a local phase diagram, and calculate the MatterSim--MP
   hybrid formation energy and energy above hull.
6. Rank candidates, emit a conservative stage conclusion, frontend payload and
   durable manifests.

## Runtime Contract

The normal inputs are `taskid`, `idea`, `user_name`, `file_metadata`, and an
optional explicit `new_material` object.  Explicit constraints are preferred:

```json
{
  "new_material": {
    "allowed_elements": ["Nb", "Mo", "Ta", "W"],
    "target_properties": {"energy_above_hull": 0.05},
    "validation_targets": {"high_temperature_strength": null},
    "max_candidates": 4
  }
}
```

Natural language is used only for conservative formula extraction.  The
service never invents a numerical property target from prose.

`energy_above_hull` supplied to MatterGen is a *generation condition*, not a
validated property.  The post-generation values are labelled as
MatterSim--MP hybrid estimates and can rank candidates for DFT.  They must not
be represented as DFT, experimental stability, or proof of high-temperature,
electrochemical, transport, or mechanical performance.

## Result Layout

```text
results/new_material/<taskid>/
  generation/
    generation_manifest.json
    generated_crystals_cif.zip
    cifs/*.cif
  validation/<candidate_id>.validation.json
  mattersim/
    relaxed_structures.extxyz
    mattersim_relaxation.json
    mattersim_results.json
  new_material_pipeline_manifest.json
```

## Service and Agent Entry Points

- `POST /new-material/generate`: synchronous HTTP API.
- `WS /new-material/start`: upstream-agent-compatible WebSocket API.
- `XIMUAlpha_MNS`: repository role dedicated to this chain.  Its profile tells
  a parent agent to supply explicit element/property constraints and describes
  the evidence and limitations of the returned conclusion.

Set `MATTERSIM_ENABLED=0` only for an explicit generation-only/debug run.
`MATTERSIM_REFERENCE_MODE=mp_api` is the default online-service mode; the
full `official` reference mode is resource-heavy and not recommended on the
current VM.
