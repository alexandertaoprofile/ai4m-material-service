"""Freeze a release candidate without writing to either production repository."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HEA = Path('/data/se42/hea_surrogate')
HUB = Path('/data/se42/alpha_project/material_service_hub')
PATENT = Path('/data/se42/experiments/patent_optimization_v0_20260922')
V10 = PATENT / 'process_semantics_canonical_v10_20260923'
GLASS = Path('/data/se42/experiments/oxide_glass_transformer_v0_20260922')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def repo_state(root):
    status = git(root, 'status', '--porcelain=v1')
    changed = {}
    for line in status.splitlines():
        name = line[3:]
        p = root / name
        if p.is_file(): changed[name] = sha(p)
    return dict(branch=git(root, 'branch', '--show-current'), commit=git(root, 'rev-parse', 'HEAD'),
                status=status.splitlines(), changed_file_hashes=changed,
                diff_sha256=hashlib.sha256(git(root, 'diff', '--binary').encode()).hexdigest())

def copy_file(source, target, manifest):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest.append(dict(source=str(source), frozen=str(target), bytes=target.stat().st_size,
                         sha256=sha(source)))
    assert sha(source) == sha(target)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    args = ap.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(Path('/data/se42/experiments')):
        raise ValueError('release freeze must stay inside experiments')
    if (root / 'FREEZE.json').exists():
        raise ValueError('refusing to overwrite completed freeze')
    root.mkdir(parents=True, exist_ok=True)
    before = {str(repo): repo_state(repo) for repo in (HEA, HUB)}
    source = HUB / 'material_composition_optimization'
    shutil.copytree(source, root / 'service_snapshot', ignore=shutil.ignore_patterns(
        'results', 'logs', '.env', '.env.*', '__pycache__', '.cache', '.pytest_cache', '.git', '*.pyc'))
    entries = []
    pairs = [
        (V10 / 'selected_single_checkpoint/model_state.pt', 'models/alloy_v10/model_state.pt'),
        (V10 / 'selected_single_checkpoint/preprocessor.json', 'models/alloy_v10/preprocessor.json'),
        (GLASS / 'transformer_composition_5fold/oxide_glass_multitask_transformer_v0.pt', 'models/glass_v0/model_state.pt'),
        (GLASS / 'code/train_oxide_glass_transformer.py', 'models/glass_v0/code/train_oxide_glass_transformer.py'),
        (GLASS / 'code/infer_oxide_glass_transformer.py', 'models/glass_v0/code/infer_oxide_glass_transformer.py'),
        (V10 / 'code_snapshot/train_v8_canonical.py', 'models/alloy_v10/code/train_v8_canonical.py'),
        (PATENT / 'repair_process_data_v6.py', 'models/alloy_v10/code/repair_process_data_v6.py'),
        (PATENT / 'data_repair_v8_20260923/dataset_repaired.csv', 'frozen_data/alloy_v8.csv'),
        (GLASS / 'dataset/sciglass_v2_strict_database_observations.csv.gz', 'frozen_data/glass_v0.csv.gz'),
    ]
    for name in ('frozen_descriptors.py', 'frozen_process.py', 'service_adapters.py'):
        pairs.append((HEA / 'models/unified_alloy_tensile_v2_20260921/runtime' / name,
                      'models/alloy_v10/code/' + name))
    pairs.append((HEA / 'models/unified_alloy_tensile_v2_20260921/runtime/assets/descriptor_properties.json',
                  'models/alloy_v10/code/descriptor_properties.json'))
    for name in ('train_process_adapter_v2.py', 'run_physics_informed_cv.py',
                 'run_hard_constraint_transformer_cv.py', 'run_family_adapter_multitask.py'):
        pairs.append((Path('/data/se42/experiments/alloy_foundation_v0_20260916') / name,
                      'models/alloy_v10/foundation/' + name))
    for source, relative in pairs:
        copy_file(source, root / relative, entries)
    after = {str(repo): repo_state(repo) for repo in (HEA, HUB)}
    if before != after: raise RuntimeError('repository changed concurrently during freeze')
    data = dict(created_utc=datetime.now(timezone.utc).isoformat(), repositories=before,
                artifacts=entries, service_snapshot=str(root / 'service_snapshot'),
                excluded=['credentials', 'production results', 'logs', 'git internal directories'],
                production_repositories_mutated=False, production_release_authorized=False)
    (root / 'FREEZE.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(freeze='passed', artifacts=len(entries), root=str(root)), ensure_ascii=False))

if __name__ == '__main__': main()
