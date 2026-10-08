from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from private_shadow_dispatcher import Channel, PrivateShadowDispatcher, run_bounded_process

def channel(command, builder=None):
    return Channel(name='release_test', model_domain='test', enabled_env='RELEASE_TEST_ENABLED',
        builder=builder or (lambda result: ({'requests':[{'a':1}]},None)),
        command=command, timeout_seconds=1, workers=1, queue_size=1)

def test_real_timeout_kills_child_process_group(tmp_path):
    pid = tmp_path/'child.pid'
    child="import os,time,pathlib;pathlib.Path("+repr(str(pid))+").write_text(str(os.getpid()));time.sleep(30)"
    leader="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(30)"
    started=time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process([sys.executable,'-c',leader],timeout=1,env=os.environ.copy())
    assert time.perf_counter()-started<5
    assert pid.is_file()
    child_pid=int(pid.read_text())
    status=Path(f'/proc/{child_pid}/stat')
    assert not status.exists() or status.read_text().split()[2]=='Z'

def test_real_process_output_is_bounded():
    done=run_bounded_process([sys.executable,'-c',"print('x'*1000000)"],timeout=3,env=os.environ.copy())
    assert done.returncode==0 and len(done.stdout)<=4096

@pytest.mark.parametrize('payload',[
    {'ok':True,'results':[]},
    {'ok':True,'display_allowed':False,'production_release_authorized':True,'results':[]},
    {'ok':True,'display_allowed':False,'production_release_authorized':False,'results':[{'status':'good'}]},
    {'ok':True,'display_allowed':False,'production_release_authorized':False,'results':[{'status':'good','display_allowed':False,'production_release_authorized':False,'public_effect':True}]},
])
def test_missing_or_public_flags_fail_closed(tmp_path,payload):
    def process(command,**kwargs):
        Path(command[0]).write_text(json.dumps(payload)); return SimpleNamespace(returncode=0)
    dispatcher=PrivateShadowDispatcher(tmp_path,[channel(lambda rq,rs:[str(rs)])],process_runner=process)
    result={'model_domain':'test','rank':[2,1]}; original=deepcopy(result)
    assert dispatcher.run_sync('release_test','safe',result)['status']=='failed'
    assert result==original
    assert not (tmp_path/'safe'/'manifest.json').exists()
    dispatcher.close()

@pytest.mark.parametrize('taskid',['..','../bad','a/b','a\\b','', '/absolute', 'a'*129])
def test_private_task_path_cannot_escape(tmp_path,taskid):
    dispatcher=PrivateShadowDispatcher(tmp_path,[channel(lambda *x:[])])
    with pytest.raises(ValueError): dispatcher.run_sync('release_test',taskid,{'model_domain':'test'})
    dispatcher.close()

def test_real_timeout_is_private_and_does_not_mutate_formal_result(tmp_path):
    dispatcher=PrivateShadowDispatcher(tmp_path,[channel(lambda rq,rs:[sys.executable,'-c','import time;time.sleep(30)'])])
    result={'model_domain':'test','properties':{'ys':123}}; original=deepcopy(result)
    record=dispatcher.run_sync('release_test','safe',result)
    assert record['status']=='failed' and record['error_type']=='TimeoutExpired' and result==original
    assert record['display_allowed'] is False and not (tmp_path/'safe'/'presentation').exists()
    dispatcher.close()

def test_empty_candidates_never_start_process(tmp_path):
    def fail(*a,**k): raise AssertionError('must not run')
    dispatcher=PrivateShadowDispatcher(tmp_path,[channel(lambda *x:[],lambda r:({'requests':[]},None))],process_runner=fail)
    assert dispatcher.run_sync('release_test','empty',{'model_domain':'test'})['status']=='skipped_no_candidates'
    dispatcher.close()

def test_closed_dispatcher_does_not_submit(tmp_path,monkeypatch):
    monkeypatch.setenv('RELEASE_TEST_ENABLED','1')
    dispatcher=PrivateShadowDispatcher(tmp_path,[channel(lambda *x:[])])
    dispatcher.close()
    assert dispatcher.submit('release_test','closed',{'model_domain':'test'}) is None
