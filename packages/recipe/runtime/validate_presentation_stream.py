"""Render and capture final streamed Markdown OFFLINE, with mocked upload URLs.
Does not start the web service, call an external LLM or upload a real object.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
os.environ.update(ALLOY_RESULTS_ROOT=str(ROOT/'private_rendered_results'),
                  ALLOY_V10_SHADOW_ENABLED='0',GLASS_TRANSFORMER_SHADOW_ENABLED='0',NSGA_SHADOW_ENABLED='0')
sys.path.insert(0,str(ROOT/'service_snapshot'))
from src.alloy_workflow.runtime import AlloyRuntime
from src.alloy_workflow import presentation
TASKROOT=Path('/data/se42/alpha_project/material_service_hub/material_composition_optimization/results/alloy_composition_optimization')
domains=['hea_mpea','ni_superalloy_hot_end','reusable_rocket_stainless','aerospace_aluminum_composition_v1',
         'copper_hot_end_local_composition_v1','chip_glass_thermomechanical_family_v1','short_cf_thermomechanical_rve_v1',
         'perovskite_transport_stability_v2']
tasks={}
for p in sorted(TASKROOT.rglob('manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True):
    try:d=json.loads(p.read_text())
    except (OSError,ValueError):continue
    domain=d.get('model_domain')
    if domain in domains and domain not in tasks and d.get('initial_candidates'):tasks[domain]=(p,d)

class Socket:
    def __init__(self):self.messages=[]
    async def send_text(self,text):self.messages.append(text)
class FakeLLM:
    def _default_system_msg(self):return {}
    def _user_msg(self,text):return {'content':text}
async def token_replay(llm,messages,socket):
    text=messages[-1]['content'].split('<AUTHORITATIVE_MARKDOWN>\n',1)[1].rsplit('\n</AUTHORITATIVE_MARKDOWN>',1)[0]
    for i in range(0,len(text),64):await socket.send_text(text[i:i+64])

runtime=AlloyRuntime();records=[]
for domain in domains:
    try:
        p,data=tasks[domain];before=hashlib.sha256(p.read_bytes()).hexdigest();result=copy.deepcopy(data)
        # Inspect the REAL HEA/Ni task Markdown before validating its renderer.
        original_summary=p.parent/'presentation/summary.md'
        source_summary_read=original_summary.is_file()
        if source_summary_read:original_summary.read_text()
        assets=runtime._render(result)
        result['_summary_path']=next(path for path in assets.values() if path.suffix=='.md')
        visual=[dict(name=name,url='https://assets.example/materials/modelfiles/image/'+result['taskid']+'/'+path.name)
                for name,path in assets.items() if path.suffix=='.png']
        socket=Socket()
        with patch.object(presentation,'_llm',return_value=FakeLLM()),patch('src.alloy_workflow.llm_streaming.stream_llm_response',token_replay):
            asyncio.run(presentation.emit_result_content(socket,result,visual_assets=visual))
        assert socket.messages[0].startswith('<<<CONTENT_START:') and socket.messages[-1].startswith('<<<CONTENT_END:')
        content=''.join(socket.messages[1:-1]);tokens=re.findall(r'__ALLOY_[A-Z0-9_]+__|__IMAGE_[A-Z0-9_]+__',content)
        assert not tokens
        assert all(x not in content for x in ('production_release_authorized','diagnostic_outside','NSGA_SHADOW_','alloy_v10','glass_v1'))
        assert '<AUTHORITATIVE_MARKDOWN>' not in content
        assert not re.search(r'\bNaN\b|\bInfinity\b',content)
        assert hashlib.sha256(p.read_bytes()).hexdigest()==before
        streamed=ROOT/'streamed_markdown';streamed.mkdir(exist_ok=True)
        (streamed/(domain+'.md')).write_text(content)
        record=dict(domain=domain,passed=True,source_summary_read=source_summary_read,source_manifest=str(p),
            local_png_count=len(visual),stream_chunks=len(socket.messages)-2,unresolved_image_tokens=0,
            real_asset_upload_performed=False,real_external_llm_called=False,
            tests_cover='local renderer -> public HTTPS placeholders -> final emitter -> token stream capture')
    except Exception as exc:record=dict(domain=domain,passed=False,error=f'{type(exc).__name__}: {exc}')
    records.append(record);print(domain,record.get('error','passed'),flush=True)
runtime.runner._new_shadows.close();runtime.runner._shadow_pool.shutdown(wait=True,cancel_futures=True)
(ROOT/'PRESENTATION_STREAM_VALIDATION.json').write_text(json.dumps(dict(records=records,
    passed=all(r['passed'] for r in records),production_release_authorized=False),indent=2))
raise SystemExit(0 if all(r['passed'] for r in records) else 1)
