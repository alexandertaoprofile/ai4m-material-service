"""Native production-context replay through the real isolated subprocess."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import time

from recipe_display import DOMAINS
from recipe_shadow import RecipeShadowDispatcher
from verify_all_domains import latest_tasks

HERE=Path(__file__).resolve().parent


def main():
    records=latest_tasks()
    saved={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p,_ in records.values()}
    dispatcher=RecipeShadowDispatcher(HERE/'dispatch_validation/results')
    output={'domains':{},'public_effect':False,'formal_result_mutated':False,
            'display_allowed':False,'production_release_authorized':False}
    try:
        assert dispatcher.ready()
        for domain,(source,task) in records.items():
            original=deepcopy(task);started=time.perf_counter()
            result=dispatcher.run_sync('acceptance-'+DOMAINS[domain][2],task)
            assert task==original
            assert result['status']=='completed',result
            row=result['results'][0]
            directory=Path(row['presentation_directory'])
            text=(directory/'preview.md').read_text()
            assert '{{VISUAL:' not in text
            manifest=json.loads((directory/'display_manifest.json').read_text())
            assert manifest['formal_result_mutated'] is False
            output['domains'][domain]={'status':row['status'],'method':row['actual_method'],
                'selected_count':row['selected_count'],'assets':len(manifest['assets']),
                'seconds':time.perf_counter()-started,'preview':str(directory/'preview.md')}
            print(json.dumps({domain:output['domains'][domain]},ensure_ascii=False),flush=True)
    finally:dispatcher.close()
    output['production_results_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==s for p,s in saved.items())
    assert output['production_results_unchanged'] and len(output['domains'])==8
    (HERE/'NATIVE_DISPATCH_VALIDATION.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
