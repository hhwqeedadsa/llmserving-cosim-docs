"""Hash archive and copied-source offline recovery; no network or compilation."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def create(repo,stage,archive):
    assert not archive.exists() and json.loads((stage/'verification.json').read_bytes())['passed']
    assert json.loads((stage/'execution.json').read_bytes())['networks']==3
    files={}
    for path in (repo/'experiments/rc_counterfactual_reuse').iterdir():
        if path.is_file() and path.suffix in ('.py','.md'):files['source/'+path.name]=path
    for folder in ('observed','truth','runtime','source-before-guard-review','report'):
        for path in sorted((stage/folder).rglob('*')):
            assert not path.is_symlink()
            if path.is_file():files['evidence/'+path.relative_to(stage).as_posix()]=path
    for path in sorted(stage.iterdir()):
        if path.is_file() and path.suffix in ('.json','.stdout','.stderr'):
            files['evidence/'+path.name]=path
    for folder in sorted((stage/'monitor').iterdir()):
        if not (folder/'probe.json').is_file():continue
        for path in folder.rglob('*'):
            if path.is_file():files['evidence/'+path.relative_to(stage).as_posix()]=path
    manifest={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(files.items())}
    assert sum(row['bytes'] for row in manifest.values())<96*2**20
    save(stage/'manifest.json',manifest)
    with tarfile.open(archive,'w:gz') as stream:
        stream.add(stage/'manifest.json',arcname='manifest.json',recursive=False)
        for name,path in sorted(files.items()):stream.add(path,arcname=name,recursive=False)
    save(stage/'checkpoint.json',dict(sha256=sha(archive),members=len(manifest),
        compressed_bytes=archive.stat().st_size,content_bytes=sum(row['bytes'] for row in manifest.values()),
        contains_local_raw_data=True,published=False))


def restore(archive,digest,output):
    assert sha(archive)==digest and not output.exists();output.mkdir()
    with tarfile.open(archive,'r:gz') as stream:
        members=stream.getmembers();manifest=json.loads(stream.extractfile('manifest.json').read())
        assert len(members)==len(manifest)+1 and len({m.name for m in members})==len(members)
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            data=stream.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name]
                assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            destination=output/member.name;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
    source=output/'source';stage=output/'evidence';empty=output/'empty-cwd';empty.mkdir()
    commands=[
        [sys.executable,'-I','-B',str(source/'predict.py'),'--stage',str(stage),'--output',str(output/'prediction.json')],
        [sys.executable,'-I','-B',str(source/'verify.py'),'--stage',str(stage),'--output',str(output/'verification.json')],
        [sys.executable,'-I','-B',str(source/'report.py'),'--stage',str(stage),'--output',str(output/'report')]]
    for index,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,capture_output=True,timeout=30)
        (output/f'command-{index}.stdout').write_bytes(result.stdout)
        (output/f'command-{index}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(index,result.stderr.decode())
    assert sha(output/'prediction.json')==sha(stage/'prediction.json')
    assert sha(output/'verification.json')==sha(stage/'verification.json')
    for name in ('report.md','report.html'):
        assert sha(output/'report'/name)==sha(stage/'report'/name)
    save(output/'recovery.json',dict(passed=True,members_verified=len(manifest),copied_source=True,
        empty_cwd=True,isolated_python=True,byte_equal_prediction=True,byte_equal_verification=True,
        byte_equal_markdown=True,byte_equal_html=True,network_runs=0,builds=0,HTTP=0,GPU=0,
        backend_execution_reproduced=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('create')
    for key in ('repository','stage','archive'):a.add_argument('--'+key,type=Path,required=True)
    a=sub.add_parser('restore');a.add_argument('--archive',type=Path,required=True)
    a.add_argument('--sha256',required=True);a.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.action=='create':create(args.repository.resolve(),args.stage.resolve(),args.archive.resolve())
    else:restore(args.archive.resolve(),args.sha256,args.output.resolve())
