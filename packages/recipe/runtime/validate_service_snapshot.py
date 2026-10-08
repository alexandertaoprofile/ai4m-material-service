from pathlib import Path
import json
import os
import subprocess
ROOT=Path(__file__).resolve().parent
env={**os.environ,'PYTHONPATH':'.:/data/mamba/envs/mattergen-py310/lib/python3.10/site-packages',
     'MINIO_ENDPOINT':'invalid.test','MINIO_ACCESS_KEY_ID':'offline-test-only','MINIO_ACCESS_KEY_SECRET':'offline-test-only',
     'ALLOY_V10_SHADOW_ENABLED':'0','GLASS_TRANSFORMER_SHADOW_ENABLED':'0','NSGA_SHADOW_ENABLED':'0'}
# The existing publication test mocks the uploader, but requires nonempty
# config. These values are intentionally invalid; no real credentials copied.
r=subprocess.run(['/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python','-m','pytest','tests','-q','--disable-warnings',
                  '--junitxml='+str(ROOT/'SERVICE_SNAPSHOT_TESTS.xml')],cwd=ROOT/'service_snapshot',env=env,capture_output=True,text=True)
(ROOT/'SERVICE_SNAPSHOT_TESTS.log').write_text(r.stdout+'\n'+r.stderr)
print(r.stdout);print(r.stderr)
raise SystemExit(r.returncode)
