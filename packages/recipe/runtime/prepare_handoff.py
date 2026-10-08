"""Generate an internal handoff, dependency guard and a nonautomatic merge plan."""
from pathlib import Path
from datetime import datetime,timezone
import difflib
import hashlib
import json
from freeze_release import sha
ROOT=Path(__file__).resolve().parent
HEA=Path('/data/se42/hea_surrogate')
TARGET=Path('/data/se42/alpha_project/material_service_hub/material_composition_optimization')
models=['yield_strength_ensemble.joblib','hardness_ensemble.joblib','phase_ensemble.joblib',
        'ni_superalloy_cast_ds_sc_strength_v0','ni_superalloy_cast_ds_sc_creep_rupture_v0',
        'ni_superalloy_cast_ds_sc_elongation_v0','chip_glass_cte_family_v1_20260902',
        'chip_glass_family_thermomechanical_candidate_v2_20260904','short_cf_thermomechanical_rve_v1_20260907',
        'copper_hot_end_local_composition_v1_20260911','aerospace_aluminum_composition_v1_20260914']
paths=list((HEA/'src/api').rglob('*.py'))+[HEA/'src/__init__.py']
for name in models:
    p=HEA/'models'/name
    paths.extend([p] if p.is_file() else [f for f in p.rglob('*') if f.suffix in {'.json','.joblib','.csv','.py'}])
for name in ('reusable_rocket_stainless_design/20260902_v2_extratrees',
             'reusable_rocket_stainless_reference_baseline/20260902_v0',
             'chip_glass_substrate_us20250026678_gate_v1'):
    paths.extend(p for p in (HEA/'data/processed'/name).rglob('*') if p.suffix in {'.csv','.json','.joblib'})
paths.append(HEA/'data/processed/ni_superalloy_training/20260831_cambridge_cast_ds_sc_full_v3/cast_ds_sc_short_time_strength_admitted.csv')
deps=dict(created_utc=datetime.now(timezone.utc).isoformat(),files={str(p):sha(p) for p in sorted(set(paths)) if p.is_file()},
    scope='verified existing specialist implementations, model weights, support metadata and anchor tables; no credentials',
    mutable_external_dependencies_are_checked_not_bundled=True)
(ROOT/'EXTERNAL_DEPENDENCIES.json').write_text(json.dumps(deps,indent=2))
plan=[];diff=[]
for p in sorted((ROOT/'1111_patch').glob('*.py')):
    target=TARGET/'src/alloy_workflow'/p.name
    old=target.read_text() if target.exists() else ''
    new=p.read_text()
    plan.append(dict(source=str(p),target=str(target),base_sha256=sha(target) if target.exists() else None,
                     candidate_sha256=sha(p)))
    diff.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
        fromfile=str(target) if target.exists() else '/dev/null',tofile=str(target)))
(ROOT/'MERGE_PLAN.json').write_text(json.dumps(dict(target_root=str(TARGET),files=plan,automatic_apply=False),indent=2))
(ROOT/'1111_shadow_candidate.patch').write_text(''.join(diff))
print(json.dumps(dict(dependency_files=len(deps['files']),guarded_patch_files=len(plan),production_modified=False)))
