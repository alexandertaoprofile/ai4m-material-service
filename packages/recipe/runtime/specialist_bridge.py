"""Persistent local JSON bridge into the matching specialist Python environment.
Copper and glass specialists use sklearn 1.8. CF and the other specialist
predictors use sklearn 1.7. Cross-version warnings are fatal in the worker.
"""
import atexit
import json
import os
from pathlib import Path
import subprocess
import tempfile

class SpecialistBridge:
    def __init__(self,space):
        self.log=tempfile.TemporaryFile()
        python=(os.environ.get('MATERIAL_RESEARCH_PYTHON','/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python')
                if space.family in {'copper','glass'} else os.environ.get('MATERIAL_LEGACY_PYTHON','/data/mamba/envs/mattergen-py310/bin/python'))
        self.process=subprocess.Popen([python,str(Path(__file__).with_name('specialist_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1,
            env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','MATERIAL_SPECIALIST_LOCAL':'1'})
        # Inherit the owning shadow process group: the hard timeout kills this
        # worker as well. No separate unowned process session is created.
        atexit.register(self.close)
        self.exchange({'init':{'domain':space.domain,'scope':space.scope,'task':space.task,
                               'reference_policy':space.reference_policy}})

    def exchange(self,payload):
        self.process.stdin.write(json.dumps(payload,allow_nan=False)+'\n');self.process.stdin.flush()
        line=self.process.stdout.readline(16*1024*1024+1)
        if not line or len(line)>16*1024*1024:raise RuntimeError('specialist bridge EOF or oversized response')
        out=json.loads(line)
        if out.get('ok') is not True:raise RuntimeError('specialist bridge: '+str(out.get('error')))
        return out

    def evaluate(self,candidates):
        return self.exchange({'evaluate':candidates})['rows']

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        for stream in (self.process.stdin,self.process.stdout):
            if stream and not stream.closed:stream.close()
        if not self.log.closed:self.log.close()
