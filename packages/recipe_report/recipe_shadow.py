"""Opt-in private recipe reports; formal results and image publication untouched."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid

DOMAINS=frozenset({'hea_mpea','ni_superalloy_hot_end','copper_hot_end_local_composition_v1',
    'reusable_rocket_stainless','aerospace_aluminum_composition_v1','chip_glass_thermomechanical_family_v1',
    'short_cf_thermomechanical_rve_v1','perovskite_transport_stability_v2'})
DISPLAY_ROOT=Path('/data/se42/experiments/material_shadow_frontend_v2_20260928')
MODEL_RUNTIME=Path('/data/se42/experiments/material_rsi_prelaunch_20260928/package/runtime')
PYTHON=Path('/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python')


def bounded_process(command,timeout):
    with tempfile.TemporaryFile() as output:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
            start_new_session=os.name=='posix',env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name=='posix':
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            else:process.kill()
            process.wait(timeout=5);raise
        finally:
            if os.name=='posix':
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        return process.returncode


class RecipeShadowDispatcher:
    def __init__(self,results_root,*,display_root=DISPLAY_ROOT,runtime=MODEL_RUNTIME,python=PYTHON,process_runner=bounded_process):
        self.results_root=Path(results_root).resolve();self.display_root=Path(display_root).resolve()
        # Preserve a virtualenv's executable path: resolving its symlink would
        # launch the system interpreter without the model dependencies.
        self.runtime=Path(runtime).resolve();self.python=Path(python).absolute();self.process_runner=process_runner
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='material-recipe-shadow')
        self.slots=threading.BoundedSemaphore(4)

    def ready(self):
        return self.python.is_file() and (self.display_root/'shadow_recipe_runner.py').is_file() and (self.runtime/'rsi_api.py').is_file()

    def submit(self,taskid,result):
        if os.getenv('RECIPE_SHADOW_ENABLED','1').strip().casefold() not in {'1','true','yes','on'}:return None
        if result.get('model_domain') not in DOMAINS or not self.ready():return None
        self._taskid(taskid)
        if not self.slots.acquire(blocking=False):return None
        try:future=self.pool.submit(self.run_sync,taskid,deepcopy(result))
        except Exception:self.slots.release();raise
        future.add_done_callback(lambda _future:self.slots.release())
        return future

    @staticmethod
    def _taskid(taskid):
        if not isinstance(taskid,str) or taskid in {'.','..'} or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',taskid):
            raise ValueError('unsafe shadow taskid')

    def run_sync(self,taskid,result):
        self._taskid(taskid)
        if result.get('model_domain') not in DOMAINS:raise ValueError('unsupported private recipe domain')
        original=deepcopy(result);started=time.perf_counter()
        directory=(self.results_root/taskid/'shadow'/'recipe_v2'/(str(time.time_ns())+'-'+uuid.uuid4().hex[:8])).resolve()
        if not directory.is_relative_to(self.results_root):raise ValueError('shadow path escaped root')
        directory.mkdir(parents=True,exist_ok=False)
        record={'domain':result['model_domain'],'mode':'shadow','status':'running','public_effect':False,
                'formal_result_mutated':False,'display_allowed':False,'production_release_authorized':False}
        try:
            request,response=directory/'request.json',directory/'response.json'
            fields={'model_domain','screening_conditions','search_space','initial_candidates','all_candidates',
                    'requirement_interpretation','model_version','_recipe_optimization_request','_resolved_scope'}
            task={k:deepcopy(v) for k,v in result.items() if k in fields}
            request.write_text(json.dumps({'items':[{'task':task}]},ensure_ascii=False,allow_nan=False),encoding='utf-8')
            rc=self.process_runner([str(self.python),str(self.display_root/'shadow_recipe_runner.py'),
                '--request',str(request),'--response',str(response),'--runtime',str(self.runtime)],timeout=75)
            if rc!=0 or not response.is_file():raise RuntimeError('private recipe process did not complete')
            if response.is_symlink() or response.stat().st_size>16*1024*1024:raise ValueError('unsafe private response')
            payload=json.loads(response.read_text(encoding='utf-8'))
            if payload.get('ok') is not True or any(payload.get(k) is not False for k in ('public_effect','display_allowed','production_release_authorized')):
                raise ValueError('private response contract mismatch')
            rows=payload.get('results')
            if not isinstance(rows,list) or len(rows)!=1 or rows[0].get('domain')!=result['model_domain']:
                raise ValueError('private response domain mismatch')
            if any(any(row.get(k) is not False for k in ('public_effect','display_allowed','production_release_authorized')) for row in rows):
                raise ValueError('private row contract mismatch')
            record.update(status='completed',results=payload['results'])
        except Exception as exc:record.update(status='failed',error_type=type(exc).__name__)
        finally:
            if result!=original:raise RuntimeError('shadow mutated original')
            record['elapsed_seconds']=time.perf_counter()-started
            (directory/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        return record

    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)
