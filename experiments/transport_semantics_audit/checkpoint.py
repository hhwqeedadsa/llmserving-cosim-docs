"""Local evidence archive and offline reproduction; no network execution."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def create(root,archive):
    assert not archive.exists() and json.loads((root/'verification.json').read_bytes())['passed']
    files={}
    for directory in ['observed','truth','analysis','preserved',*[p.name for p in root.glob('*-monitor') if (p/'probe.json').exists()]]:
        for path in sorted((root/directory).rglob('*')):
            assert not path.is_symlink()
            if path.is_file():files['evidence/'+path.relative_to(root).as_posix()]=path
    for name in ('inputs.json','verification.json'):files['evidence/'+name]=root/name
    for path in sorted(Path(__file__).parent.iterdir()):
        if path.is_file() and path.suffix in ('.py','.md'):files['source/'+path.name]=path
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert sum(r['bytes'] for r in manifest.values())<48*2**20
    save(root/'checkpoint-manifest.json',manifest)
    with tarfile.open(archive,'w:gz') as target:
        target.add(root/'checkpoint-manifest.json',arcname='manifest.json',recursive=False)
        for name,path in sorted(files.items()):target.add(path,arcname=name,recursive=False)
    save(archive.with_suffix('.receipt.json'),dict(sha256=sha(archive),bytes=archive.stat().st_size,
        content_members=len(files),uncompressed_content_bytes=sum(r['bytes'] for r in manifest.values())))


def restore(archive,expected,output):
    assert sha(archive)==expected and not output.exists();output.mkdir()
    with tarfile.open(archive) as source:
        members=source.getmembers();assert len(members)<500
        manifest=json.load(source.extractfile('manifest.json'))
        assert len({m.name for m in members})==len(members) and set(manifest)|{'manifest.json'}=={m.name for m in members}
        assert sum(m.size for m in members)<48*2**20
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            raw=source.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name];assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
            target=output/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    empty=output/'empty-cwd';empty.mkdir();data=output/'evidence';code=output/'source'
    commands=[
        [sys.executable,'-I','-B',str(code/'study.py'),'analyze','--run',str(data),'--output',str(data/'reanalysis')],
        [sys.executable,'-I','-B',str(code/'verify.py'),'--run',str(data),'--output',str(data/'reverification.json')]]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,capture_output=True,timeout=25)
        (output/f'command-{i}.stdout').write_bytes(result.stdout);(output/f'command-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr.decode())
    for name in ('analysis.json','summary.json','report.md','report.html'):assert sha(data/'analysis'/name)==sha(data/'reanalysis'/name)
    assert sha(data/'verification.json')==sha(data/'reverification.json')
    save(output/'recovery.json',dict(passed=True,archive_sha256=expected,content_members_verified=len(manifest),
        copied_source=True,empty_cwd=True,byte_equal_analysis=True,byte_equal_reports=True,byte_equal_verification=True,
        new_networks=0,downloads=0,builds=0,GPU_runs=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('create');a.add_argument('--run',type=Path,required=True);a.add_argument('--archive',type=Path,required=True)
    b=s.add_parser('restore');b.add_argument('--archive',type=Path,required=True);b.add_argument('--sha256',required=True);b.add_argument('--output',type=Path,required=True)
    x=p.parse_args()
    if x.action=='create':create(x.run.resolve(),x.archive.resolve())
    else:restore(x.archive.resolve(),x.sha256,x.output.resolve())
