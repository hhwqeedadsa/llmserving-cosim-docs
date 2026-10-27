"""Preserve the failed network and replay diagnosis from hash-admitted bytes."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from experiments.static_dag_transfer.prepare import admit, save


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def create(repo,stage,output):
    assert not output.exists();files={}
    assert json.loads((stage/'execution.json').read_bytes())['closed']
    for path in stage.rglob('*'):
        if path.is_file():
            assert not path.is_symlink();files['evidence/'+str(path.relative_to(stage))]=path
    for path in (repo/'experiments/static_dag_transfer').glob('*'):
        if path.is_file() and path.suffix in ('.py','.md'):files['source/'+str(path.relative_to(repo))]=path
    for path in (repo/'experiments/static_dag_transfer/results/20261027').iterdir():
        if path.is_file():files['expected/'+path.name]=path
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)<2048 and sum(x['bytes'] for x in entries.values())<128*2**20
    assert max(x['bytes'] for x in entries.values())<=16*2**20
    manifest=dict(schema_version='static-dag-failure-checkpoint/1',files=entries,network_executions=1,
        network_completed=False,scope='failed baseline and offline projection diagnosis; no GPU or generic DAG limit claim')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in sorted(files.items()):
            raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644
            archive.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(raw)
        archive.addfile(info,io.BytesIO(raw))
    digest=sha(output);admit(output,digest)
    save(output.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=output.stat().st_size,uncompressed_bytes=sum(x['bytes'] for x in entries.values())))


def restore(archive,output):
    assert not output.exists();expected=json.loads(archive.with_suffix('.receipt.json').read_bytes())['sha256']
    blobs=admit(archive,expected);output.mkdir()
    for name,raw in blobs.items():
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    source=output/'source';empty=output/'empty-cwd';empty.mkdir()
    env=os.environ|{'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    command=[sys.executable,'-B','-m','experiments.static_dag_transfer.analyze_failure',
        '--stage',str(output/'evidence'),'--output-dir',str(output/'reanalysis')]
    done=subprocess.run(command,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
    save(output/'replay-command.json',dict(command=command,cwd=str(empty),exit_code=done.returncode,stderr=done.stderr))
    assert done.returncode==0,done.stderr
    same={name:(output/'reanalysis'/name).read_bytes()==blobs['expected/'+name] for name in ('summary.json','report.md')}
    assert all(same.values())
    save(output/'recovery.json',dict(passed=True,hash_verified_members=len(blobs),archive_sha256=expected,
        copied_source_root=True,empty_cwd=True,byte_identical=same,new_networks=0))


def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
    c=s.add_parser('create')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    r=s.add_parser('restore')
    for name in ('archive','output'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
