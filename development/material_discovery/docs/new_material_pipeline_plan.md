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
    "allowed_elements": ["Li", "P", "S", "Cl"],
    "target_properties": {"energy_above_hull": 0.05},
    "validation_targets": {"ionic_conductivity": null},
    "max_candidates": 4
  }
}
```

When the explicit object is absent, natural language and upstream conversation
context are a controlled fallback. The service extracts independent element
symbols (for example `Li-P-S-Cl` or `锂、磷、硫、氯`), a chemical formula,
an explicitly stated `E_hull` ceiling (including `meV/atom` conversion), and
qualitative validation concerns such as high-temperature strength, creep and
oxidation resistance. Explicit fields always win. It never invents a numerical
property target; if it cannot determine an element system, it asks for one
instead of launching an unconstrained generator.

For conversational requests without an explicit `max_candidates`, the default
is one candidate (`MATTERGEN_DEFAULT_CANDIDATES=1`) to keep interactive runtime
bounded. Structured callers may request 1–64 candidates.

`energy_above_hull` supplied to MatterGen is a *generation condition*, not a
validated property. The post-generation value is evaluated after MatterSim
relaxation against the MP2020-corrected MatterGen reference hull and can rank
candidates for DFT. It must not be represented as DFT, experimental stability,
or proof of high-temperature, electrochemical, transport, or mechanical
performance.

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
- `InorganicNewMaterialDiscoveryRole`: repository role dedicated to this chain. Its profile tells
  a parent agent to supply explicit element/property constraints and describes
  the evidence and limitations of the returned conclusion.

Set `MATTERSIM_ENABLED=0` only for an explicit generation-only/debug run.
`MATTERSIM_REFERENCE_MODE=official` is the default online-service mode. It
uses the complete MP2020-corrected reference so the candidate and convex hull
share a compatible energy convention. `mp_api` remains an explicit diagnostic
fallback only and must not be used for customer-facing stability values.
The first official run creates a persistent, read-only expanded LMDB cache
(about 3.85 GB; default `/data/mattersim_reference_cache`) so later service
requests do not unpack the reference archive again. Configure its persistent
storage with `MATTERSIM_REFERENCE_CACHE_DIR`.
The expensive convex hull is separately cached by allowed chemical system at
`/data/mattersim_phase_diagram_cache` (override with
`MATTERSIM_PHASE_DIAGRAM_CACHE_DIR`). The prewarm utility registers
Co-Cr-Fe-Mn-Ni, Nb-Mo-Ta-W, Li-P-S, Li-P-S-Cl, and Li-La-Zr-O as the current
common systems; a candidate that uses a subset of an allowed system reuses its
parent-system hull.
