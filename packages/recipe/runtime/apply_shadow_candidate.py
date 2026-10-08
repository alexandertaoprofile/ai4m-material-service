"""Guarded maintainer merge; dry run by default, NEVER restarts or pushes.

This script is delivered but is not executed with --apply during validation.
Review 1111_shadow_candidate.patch and obtain deployment approval first.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
import shutil
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    plan=json.loads((ROOT/'MERGE_PLAN.json').read_text());target_root=Path(plan['target_root']).resolve()
    checks=[]
    for row in plan['files']:
        target=Path(row['target']);candidate=ROOT/'1111_patch'/target.name
        if target.is_symlink() or not target.resolve().is_relative_to(target_root):raise RuntimeError('unsafe target')
        if not candidate.is_file() or sha(candidate)!=row['candidate_sha256']:raise RuntimeError('candidate patch changed')
        actual=sha(target) if target.exists() else None
        if actual!=row['base_sha256']:raise RuntimeError('live file changed since review: '+str(target))
        checks.append((target,candidate))
    print(json.dumps({'preconditions_passed':True,'files':len(checks),'apply_requested':args.apply}))
    if not args.apply:return
    # All preconditions are checked before the first write. Backups are kept
    # in this versioned experiment, not hidden in a live repository.
    backup=ROOT/'merge_backup'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True,exist_ok=False)
    for target,candidate in checks:
        if target.exists():shutil.copy2(target,backup/target.name)
        else:(backup/(target.name+'.previously_absent')).touch()
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.shadow-merge.tmp')
        if temporary.exists():raise RuntimeError('merge temporary path already exists')
        shutil.copy2(candidate,temporary);os.replace(temporary,target)
    print(json.dumps({'merged':len(checks),'backup':str(backup),'restart_performed':False,'git_push_performed':False}))
if __name__=='__main__':main()
