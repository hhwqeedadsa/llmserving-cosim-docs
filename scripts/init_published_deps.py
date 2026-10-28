#!/usr/bin/env python3
"""Initialize pinned submodules from the published same-repository mirrors.

Keep original .gitmodules/lock identities unchanged. URL rewriting is limited
to this Git command, and each module borrows the main clone's object cache.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def git(*args,cwd=ROOT,capture=True):
    result=subprocess.run(['git',*map(str,args)],cwd=cwd,text=True,
        stdout=subprocess.PIPE if capture else None,check=True,timeout=180)
    return result.stdout.strip() if capture else None


def spec():
    value=json.loads((ROOT/'publication/dependencies.json').read_bytes())
    if value['schema_version']!='moe-published-dependencies/1':raise ValueError('unsupported manifest')
    rows=value['dependencies'];paths=set()
    for row in rows:
        p=PurePosixPath(row['path'])
        if p.is_absolute() or '..' in p.parts or not str(p).startswith('third_party/') or str(p) in paths:
            raise ValueError('invalid or repeated dependency path')
        paths.add(str(p))
        if not re.fullmatch('[0-9a-f]{40}',row['commit']):raise ValueError('invalid fixed commit')
        if str(PurePosixPath(row['parent_path'])/row['submodule_path'])!=str(p):raise ValueError('invalid parent')
        for key in ('mirror_ref','local_ref'):git('check-ref-format',row[key])
    archive=value['simai_archive'];path=ROOT/archive['path']
    if path.stat().st_size!=archive['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=archive['sha256']:
        raise ValueError('fixed SimAI source archive hash differs')
    return value


def inspect(row,missing_ok=False):
    parent=ROOT/row['parent_path'];path=ROOT/row['path']
    if not (path/'.git').exists():
        if missing_ok:return None
        raise ValueError('uninitialized dependency: '+row['path'])
    head=git('rev-parse','HEAD',cwd=path)
    if head!=row['commit']:raise ValueError('existing dependency has another HEAD: '+row['path'])
    if git('status','--porcelain=v1','--untracked-files=normal','--ignore-submodules=all',cwd=path):
        raise ValueError('dependency contains local changes: '+row['path'])
    link=git('ls-tree','HEAD','--',row['submodule_path'],cwd=parent).split()
    if len(link)<3 or link[:3]!=['160000','commit',row['commit']]:raise ValueError('parent gitlink differs')
    tree=git('rev-parse','HEAD^{tree}',cwd=path)
    if tree!=row['tree']:raise ValueError('dependency source tree differs')
    return dict(path=row['path'],commit=head,tree=tree)


def initialize(mirror,verify_only):
    value=spec();rows=value['dependencies']
    for row in rows:inspect(row,missing_ok=not verify_only)
    if not verify_only:
        # Fetch each dependency into one shared object store, not 13 full copies.
        fetch=[row['mirror_ref']+':'+row['local_ref'] for row in rows]
        git('fetch','--no-tags',mirror,*fetch,capture=False)
        for row in rows:
            if git('rev-parse',row['local_ref'])!=row['commit']:
                raise ValueError('published dependency ref differs: '+row['name'])
        options=[]
        for url in sorted({row['original_url'] for row in rows}):
            options.extend(('-c',f'url.{mirror}.insteadOf={url}'))
        git(*options,'submodule','update','--init','--recursive','--reference',ROOT,'--jobs','2',capture=False)
    result=dict(passed=True,dependencies=[inspect(row) for row in rows],simai_archive_verified=True,
        global_git_config_changed=False,original_gitmodules_and_lock_unchanged=True,
        scope='source identity and initialization only; no build or network simulation')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mirror',help='Git URL of the repository carrying codex/deps/*; defaults to publication manifest')
    p.add_argument('--verify-only',action='store_true',help='Only verify already initialized source trees and archive')
    a=p.parse_args();initialize(a.mirror or spec()['repository'],a.verify_only)
