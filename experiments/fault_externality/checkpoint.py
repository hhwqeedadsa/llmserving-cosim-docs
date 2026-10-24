"""Bounded offline evidence packaging and verified replay; no network execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def create(a):
    assert not a.output.exists()
    stage=a.stage.resolve();repo=a.repository.resolve();files={}
    for name in ('inputs','network'):
        for p in sorted((stage/name).rglob('*')):
            if p.is_file():
                assert not p.is_symlink();files[str(p.relative_to(stage))]=p
    for name in ('analysis-final.json','verification-final.json'):
        files[name]=stage/name
    for p in sorted((repo/'experiments/fault_externality').glob('*')):
        if p.is_file():files['source/'+p.name]=p
    for p in sorted(stage.glob('*-monitor/*')):
        # A new monitor writes probe.json only at exit. Do not snapshot our own
        # still-changing stdout/samples while computing and archiving their hash.
        if p.is_file() and (p.parent/'probe.json').is_file():
            files['monitors/'+str(p.relative_to(stage))]=p
    manifest=dict(schema_version='moe-fault-externality-checkpoint/1',
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        files={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()},
        scope='Original four network results and offline analysis replay. No portable binary, clean build or network replay.')
    assert sum(x['bytes'] for x in manifest['files'].values())<64*2**20 and len(files)<2048
    with tarfile.open(a.output,'x:gz',compresslevel=6) as archive:
        for name,p in files.items():
            data=p.read_bytes();entry=manifest['files'][name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
        blob=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(blob);archive.addfile(info,io.BytesIO(blob))
    save(a.output.with_suffix(a.output.suffix+'.receipt.json'),dict(sha256=sha(a.output),bytes=a.output.stat().st_size,
        members=len(files)+1,uncompressed_file_bytes=sum(x['bytes'] for x in manifest['files'].values())+len(blob),
        scope=manifest['scope']))
    print(json.dumps(dict(members=len(files)+1,bytes=a.output.stat().st_size)))


def restore(a):
    assert not a.output.exists()
    with tarfile.open(a.archive,'r:gz') as archive:
        members=archive.getmembers();assert len(members)<=2048
        names=set();total=0
        for m in members:
            p=PurePosixPath(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts and str(p)==m.name
            assert m.name not in names and m.size<=16*2**20
            names.add(m.name);total+=m.size
        assert total<64*2**20 and 'manifest.json' in names
        manifest=json.load(archive.extractfile('manifest.json'))
        assert set(manifest['files'])==names-{'manifest.json'}
        # Check every member before creating the destination; do not trust tar paths or links.
        for m in members:
            if m.name=='manifest.json':continue
            data=archive.extractfile(m).read();entry=manifest['files'][m.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
        a.output.mkdir()
        for m in members:
            p=a.output/m.name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(archive.extractfile(m).read())
    root=a.output.resolve();cwd=root/'empty-cwd';cwd.mkdir();commands=[]
    for label,args in [
        ('analysis',['analyze.py','--observed',root/'inputs/observed','--runs',root/'network','--output',root/'replayed-analysis.json']),
        ('verification',['verify.py','--inputs',root/'inputs','--runs',root/'network',
                         '--analysis',root/'replayed-analysis.json','--output',root/'replayed-verification.json'])]:
        cmd=[sys.executable,'-B',str(root/'source'/args[0]),*map(str,args[1:])]
        with (root/(label+'.stdout.log')).open('w') as stdout,(root/(label+'.stderr.log')).open('w') as stderr:
            process=subprocess.run(cmd,cwd=cwd,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(command=cmd,cwd=str(cwd),exit_code=process.returncode));assert process.returncode==0
    equality={name:(root/original).read_bytes()==(root/replayed).read_bytes() for name,original,replayed in (
        ('analysis','analysis-final.json','replayed-analysis.json'),
        ('verification','verification-final.json','replayed-verification.json'))}
    assert all(equality.values())
    save(root/'replay.json',dict(passed=True,archive_sha256=sha(a.archive),members=len(members),
         byte_identical=equality,commands=commands,network_executions=0,
         scope='Offline replay from a new source root and empty cwd on the same Linux host.'))
    print(json.dumps(dict(passed=True,byte_identical=equality)))


def main():
    p=argparse.ArgumentParser();sp=p.add_subparsers(dest='action',required=True)
    c=sp.add_parser('create');c.add_argument('--stage',type=Path,required=True);c.add_argument('--repository',type=Path,required=True)
    c.add_argument('--output',type=Path,required=True)
    r=sp.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    a=p.parse_args();(create if a.action=='create' else restore)(a)


if __name__=='__main__':main()
