"""Real private runners in a copied 1111 tree; no HTTP listener or upload."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'service_snapshot'),str(ROOT)]
from src.alloy_workflow.private_shadow_dispatcher import PrivateShadowDispatcher
from src.alloy_workflow.shadow_channel_configs import model_shadow_channels,optimization_shadow_channels
from src.alloy_workflow.tensile_shadow import build_comparison_items
from src.alloy_workflow.glass_shadow_compare import build_glass_shadow_batch,merge_private_diagnostics

TASKROOT=Path('/data/se42/alpha_project/material_service_hub/material_composition_optimization/results/alloy_composition_optimization')
DOMAINS=['aerospace_aluminum_composition_v1','ni_superalloy_hot_end','reusable_rocket_stainless','copper_hot_end_local_composition_v1',
         'hea_mpea','chip_glass_thermomechanical_family_v1','short_cf_thermomechanical_rve_v1']
tasks={}
for path in sorted(TASKROOT.rglob('manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True):
    try:d=json.loads(path.read_text())
    except (OSError,ValueError):continue
    key=d.get('model_domain')
    if key in DOMAINS and key not in tasks and d.get('initial_candidates') and d.get('requirement_interpretation',{}).get('effective_model_input'):tasks[key]=(path,d)
python=Path('/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python')
channels=model_shadow_channels(alloy_python=python,alloy_runtime=ROOT,alloy_items=build_comparison_items,
    glass_python=python,glass_runtime=ROOT,glass_checkpoint=ROOT/'models/glass_v1/model_state.pt',glass_code=ROOT/'models/glass_v1/code',
    glass_builder=build_glass_shadow_batch,glass_merger=merge_private_diagnostics)+optimization_shadow_channels(python,ROOT)
dispatcher=PrivateShadowDispatcher(ROOT/'private_e2e_results',channels)
records=[]
try:
    for ch in channels:
        path,task=tasks[ch.model_domain];before=hashlib.sha256(path.read_bytes()).hexdigest();frozen=copy.deepcopy(task)
        if ch.name.startswith('nsga_'):
            scope=task['requirement_interpretation']['effective_model_input']
            frozen['_optimization_request']=dict(model_domain=ch.model_domain,scope=scope,specialist_result=copy.deepcopy(task),
                limits=dict(population=8,model_evaluations=32,proposals=2560,seconds=25),seed=17)
        out=dispatcher.run_sync(ch.name,'e2e-'+ch.name,frozen)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
        if ch.name.startswith('nsga_'):frozen.pop('_optimization_request')
        assert frozen==task
        records.append(dict(source_manifest=str(path),formal_source_unchanged=True,**out))
        print(ch.name,out['status'],out.get('status_counts'),flush=True)
finally:dispatcher.close()
report=dict(records=records,passed=all(r['status']=='completed' for r in records),production_release_authorized=False,
            public_model_outputs_unchanged=True,external_http_service_started=False,public_asset_upload_performed=False)
(ROOT/'E2E_PRIVATE_INTEGRATION.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
