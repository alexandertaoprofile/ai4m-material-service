"""Read-only preflight: hashes, actual raw-input health, environments, live state.

Never enables a channel, starts an HTTP listener, restarts tmux, or pushes Git.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from freeze_release import repo_state,sha
ROOT=Path(__file__).resolve().parent
PYTHON=Path('/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python')
ENVIRONMENTS=[PYTHON,Path('/data/mamba/envs/mattergen-py310/bin/python'),
              Path('/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python')]

def verify_dependencies():
    manifest=ROOT/'EXTERNAL_DEPENDENCIES.json'
    if not manifest.exists():raise RuntimeError('external dependency inventory absent')
    data=json.loads(manifest.read_text());bad=[]
    for path,expected in data['files'].items():
        p=Path(path)
        if not p.is_file() or sha(p)!=expected:bad.append(path)
    return dict(passed=not bad,checked=len(data['files']),mismatches=bad)

def run():
    checks={}
    before=json.loads((ROOT/'FREEZE.json').read_text())
    checks['production_repositories_preserved']={path:repo_state(Path(path))==state
                                                 for path,state in before['repositories'].items()}
    checks['artifact_sources_preserved']=[dict(source=r['source'],passed=sha(r['source'])==r['sha256'])
                                         for r in before['artifacts']]
    checks['external_dependencies']=verify_dependencies()
    environments=[]
    requirements=[['numpy','pandas','scikit-learn','torch','pymoo'],
                  ['numpy','pandas','scikit-learn'],['fastapi','pydantic','requests']]
    for python,required in zip(ENVIRONMENTS,requirements):
        code=("import json,sys,importlib.metadata as m; "
              "found={d.metadata['Name'].lower():d.version for d in m.distributions()}; "
              "print(json.dumps({'python':sys.version,'packages':{n:found.get(n) for n in "+repr(required)+"}}))")
        p=subprocess.run([str(python),'-c',code],capture_output=True,text=True,timeout=15)
        inventory=json.loads(p.stdout) if p.returncode==0 else p.stderr[-1000:]
        environments.append(dict(executable=str(python),returncode=p.returncode,required_packages=required,
            passed=p.returncode==0 and all(inventory['packages'][name] is not None for name in required),inventory=inventory))
    checks['environments']=environments
    healthdir=ROOT/'private_health';healthdir.mkdir(exist_ok=True)
    glass=json.loads((ROOT/'GLASS_V1_MANIFEST.json').read_text())
    commands=[('alloy',[str(PYTHON),str(ROOT/'runner.py'),'--health-check',
                        '--response',str(healthdir/'alloy.json'),'--threads','1']),
              ('glass',[str(PYTHON),str(ROOT/'oxide_glass_shadow_runner.py'),
                        '--checkpoint',glass['checkpoint'],'--model-code-dir',glass['model_code_dir'],
                        '--expected-sha256',glass['checkpoint_sha256'],'--expected-infer-sha256',glass['infer_sha256'],
                        '--expected-train-sha256',glass['train_sha256'],'--health-check',
                        '--response',str(healthdir/'glass.json')])]
    probes=[]
    for name,command in commands:
        p=subprocess.run(command,capture_output=True,text=True,timeout=30,
                         env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
        output=healthdir/(name+'.json')
        data=json.loads(output.read_text()) if output.is_file() else {}
        good=p.returncode==0 and data.get('health_check')=='real_raw_input_inference_passed'
        if name=='glass':good=p.returncode==0 and data.get('health_check')=='real_raw_input_inference_passed'
        probes.append(dict(name=name,passed=good,returncode=p.returncode,stdout=p.stdout[-1000:],stderr=p.stderr[-1500:]))
    checks['real_raw_input_model_health']=probes
    try:
        with urllib.request.urlopen('http://127.0.0.1:1111/health',timeout=30) as r:
            checks['existing_live_service_health']=json.load(r)
    except Exception as exc:checks['existing_live_service_health']={'read_only_check_failed':str(exc)}
    passed=(all(checks['production_repositories_preserved'].values()) and
            all(r['passed'] for r in checks['artifact_sources_preserved']) and
            checks['external_dependencies']['passed'] and all(p['passed'] for p in probes) and
            all(e['passed'] for e in environments))
    report=dict(created_utc=datetime.now(timezone.utc).isoformat(),offline_preflight_passed=passed,checks=checks,
                shadow_activated=False,production_repositories_mutated=False,production_release_authorized=False,
                live_service_is_existing_legacy_not_new_candidate=True)
    (ROOT/'FINAL_PREFLIGHT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(report,ensure_ascii=False))
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(run())
