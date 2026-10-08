"""Full native maximum glass payload and scalar-to-batch numerical parity."""
from pathlib import Path
import json
import time
import numpy as np
from oxide_glass_shadow_runner import load_frozen,run_batch,infer_many
from glass_shadow_compare import build_glass_shadow_batch
ROOT=Path(__file__).resolve().parent
d=json.loads((ROOT/'GLASS_V1_MANIFEST.json').read_text())
loaded=load_frozen(Path(d['checkpoint']),Path(d['model_code_dir']),d['checkpoint_sha256'],d['infer_sha256'],d['train_sha256'])
state,model,research,torch,_=loaded
batch=json.loads((ROOT/'glass_shadow_example_batch.json').read_text())
request=[r for r in batch['requests'] if r['task'] in {'DENSITY_20C','YOUNG_MODULUS'}]
with torch.inference_mode():
    scalar=[research.infer(state,model,r,torch.device('cpu'))['prediction'] for r in request]
many=infer_many(request,state,model,research,torch)
diff=max(abs(x-r['prediction']) for x,r in zip(scalar,many))
assert np.allclose(scalar,[r['prediction'] for r in many],rtol=2e-5,atol=1e-4)
formal={'model_domain':'chip_glass_thermomechanical_family_v1','all_candidates':[
    {'candidate_id':f'capacity-fixture-{i}','composition_mol_percent':request[0]['composition_mol_percent'],
     'predicted_properties':{'density_g_per_cm3':{'prediction':2.5},'young_modulus_GPa':{'prediction':70.0}}}
    for i in range(500)]}
payload,mapping=build_glass_shadow_batch(formal)
assert len(mapping)==len(payload['requests'])==1000
start=time.monotonic();out=run_batch(payload,*loaded);elapsed=time.monotonic()-start
assert len(out['results'])==1000 and all(r['status']=='predicted_diagnostic' for r in out['results'])
assert all(r['display_allowed'] is False and r['production_release_authorized'] is False for r in out['results'])
try:run_batch({'requests':[request[0]]*4097},*loaded)
except ValueError:pass
else:raise AssertionError('oversized input admitted')
report=dict(passed=True,maximum_native_candidates=500,properties_per_candidate=2,
            maximum_native_batch_requests=1000,elapsed_seconds=elapsed,scalar_batch_maximum_difference=diff,
            oversized_payload_rejected=True,production_release_authorized=False)
(ROOT/'GLASS_BATCH_VALIDATION.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
