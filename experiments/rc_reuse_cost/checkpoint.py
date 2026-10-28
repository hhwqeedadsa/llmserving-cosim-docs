"""Local archive, regular-file hash recovery and offline result regeneration."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')


def create(repo,stage,archive):
    assert not archive.exists() and json.loads((stage/'verification.json').read_bytes())['passed']
    ledger=json.loads((stage/'execution.json').read_bytes()); assert ledger['closed'] and ledger['networks_executed']==11
    files={}
    for path in (repo/'experiments/rc_reuse_cost').iterdir():
        if path.is_file() and path.suffix in ('.py','.md'):files['source/'+path.name]=path
    for name in ('inputs','runtime','algorithms','workflows','source-before-measurement','source-before-archive-repair','monitor','report'):
        for path in sorted((stage/name).rglob('*')):
            assert not path.is_symlink()
            if name=='monitor' and path.is_file() and not (stage/name/path.relative_to(stage/name).parts[0]/'probe.json').is_file():
                continue  # A running monitor's logs are mutable, not a snapshot.
            if path.is_file():files['evidence/'+path.relative_to(stage).as_posix()]=path
    for path in sorted(stage.iterdir()):
        if path.is_file() and path.suffix=='.json' and path.name not in ('manifest.json','checkpoint.json'):
            files['evidence/'+path.name]=path
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(files.items())}
    assert len(manifest)<2048 and sum(r['bytes'] for r in manifest.values())<160*2**20
    save(stage/'manifest.json',manifest)
    # Query views use local hard links. Store regular members, never links.
    with tarfile.open(archive,'w:gz',dereference=True) as stream:
        stream.add(stage/'manifest.json',arcname='manifest.json',recursive=False)
        for name,path in sorted(files.items()):stream.add(path,arcname=name,recursive=False)
    save(stage/'checkpoint.json',dict(sha256=sha(archive),members=len(manifest),
        content_bytes=sum(r['bytes'] for r in manifest.values()),compressed_bytes=archive.stat().st_size,
        contains_local_raw_data=True,published=False,comparison_complete=False))


def restore(archive,digest,output):
    assert sha(archive)==digest and not output.exists(); output.mkdir()
    with tarfile.open(archive,'r:gz') as stream:
        members=stream.getmembers(); manifest=json.loads(stream.extractfile('manifest.json').read())
        assert len(members)==len(manifest)+1 and len({m.name for m in members})==len(members)<2049
        assert sum(m.size for m in members)<164*2**20
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            payload=stream.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name]; assert len(payload)==row['bytes'] and hashlib.sha256(payload).hexdigest()==row['sha256']
            destination=output/member.name; destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(payload)
    source=output/'source'; stage=output/'evidence'; empty=output/'empty-cwd'; empty.mkdir()
    commands=[
        [sys.executable,'-I','-B',str(source/'replay_predictions.py'),'--stage',str(stage),'--output',str(output/'predictions.json')],
        [sys.executable,'-I','-B',str(source/'verify.py'),'--stage',str(stage),'--output',str(output/'verification.json')],
        [sys.executable,'-I','-B',str(source/'report.py'),'--stage',str(stage),'--output',str(output/'report')]]
    for i,command in enumerate(commands):
        process=subprocess.run(command,cwd=empty,capture_output=True,timeout=30)
        (output/f'command-{i}.stdout').write_bytes(process.stdout); (output/f'command-{i}.stderr').write_bytes(process.stderr)
        assert process.returncode==0,(i,process.stderr.decode())
    assert sha(output/'verification.json')==sha(stage/'verification.json')
    for name in ('report.md','report.html'):assert sha(output/'report'/name)==sha(stage/'report'/name)
    save(output/'recovery.json',dict(passed=True,members_verified=len(manifest),copied_source=True,
        empty_cwd=True,isolated_python=True,predictions_recomputed=6,byte_equal_predictions=True,
        byte_equal_verification=True,byte_equal_markdown=True,byte_equal_html=True,
        network_runs=0,performance_measurements=0,builds=0,HTTP=0,GPU=0,comparison_complete=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='action',required=True)
    create_parser=sub.add_parser('create')
    for name in ('repository','stage','archive'):create_parser.add_argument('--'+name,type=Path,required=True)
    restore_parser=sub.add_parser('restore'); restore_parser.add_argument('--archive',type=Path,required=True)
    restore_parser.add_argument('--sha256',required=True); restore_parser.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.sha256,a.output.resolve())
