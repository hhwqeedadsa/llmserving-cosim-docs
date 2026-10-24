"""Bounded standalone research source/evidence archive and offline replay."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists()
    assert load(s/'run.json')['new_actual_networks']==4 and load(s/'verification.json')['passed'] and load(s/'mechanism-verification.json')['passed']
    files={}
    for directory in ('inputs','planned','topology','network','archive-safety'):
        for p in sorted((s/directory).rglob('*')):
            if p.is_file():files['evidence/'+str(p.relative_to(s))]=p
    for p in s.iterdir():
        if p.is_file() and p.suffix in ('.py','.json'):files['evidence/'+p.name]=p
    for p in sorted(s.glob('*-monitor/*')):
        if p.is_file() and (p.parent/'probe.json').exists():files['evidence/monitors/'+str(p.relative_to(s))]=p
    for p in (repo/'experiments/local_fault_baselines').glob('*'):
        if p.is_file():files['source/experiments/local_fault_baselines/'+p.name]=p
    report='docs/reviews/经典本地处理后的故障损失与乱序重传_20261024.md'
    if (repo/report).exists():files['source/'+report]=repo/report
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<=2048 and sum(v['bytes'] for v in entries.values())<128*2**20
    manifest=dict(schema_version='local-fault-baselines-checkpoint/1',files=entries,native_runtime_bundled=False)
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);ar.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);ar.addfile(info,io.BytesIO(data))
    assert a.output.stat().st_size<32*2**20
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(v['bytes'] for v in entries.values())+len(data),compressed_bytes=a.output.stat().st_size,
        scope='standalone offline source and six observed conditions, four new networks; no native runtime or clean build'))


def restore(a):
    assert not a.output.exists() and a.archive.stat().st_size<32*2**20
    with tarfile.open(a.archive,'r:gz') as ar:
        members=ar.getmembers();assert len(members)<=2048;names=set();total=0
        for m in members:
            p=PurePosixPath(m.name)
            assert m.isfile() and not p.is_absolute() and '..' not in p.parts and str(p)==m.name and '\\' not in m.name
            assert m.name not in ('','.') and m.name not in names and 0<=m.size<=16*2**20
            names.add(m.name);total+=m.size
        assert total<128*2**20 and 'manifest.json' in names
        assert not any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent)!='.')
        manifest=json.load(ar.extractfile('manifest.json'));assert manifest['schema_version']=='local-fault-baselines-checkpoint/1'
        assert set(manifest['files'])==names-{'manifest.json'}
        for name,entry in manifest['files'].items():
            data=ar.extractfile(name).read();assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
        a.output.mkdir()
        for m in members:
            p=a.output/m.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(ar.extractfile(m).read())
    root=a.output.resolve();s=root/'evidence';source=root/'source';cwd=root/'empty-cwd';cwd.mkdir()
    env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1');commands=[]
    steps=[('analysis','analyze',['--observed',s/'inputs/observed','--planned',s/'planned','--network',s/'network','--baseline',s/'inputs/baseline']),
        ('verification','verify',['--stage',s,'--analysis',root/'analysis.json']),
        ('mechanism-expanded','mechanism',['--stage',s,'--analysis',root/'analysis.json']),
        ('mechanism-verification','verify_mechanism',['--stage',s,'--analysis',root/'analysis.json','--mechanism',root/'mechanism-expanded.json'])]
    for label,module,args in steps:
        cmd=[sys.executable,'-B','-m','experiments.local_fault_baselines.'+module,*map(str,args),'--output',str(root/(label+'.json'))]
        with (root/(label+'.stdout.log')).open('w') as stdout,(root/(label+'.stderr.log')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(command=cmd,cwd=str(cwd),pythonpath=str(source),exit_code=r.returncode));save(root/'commands.json',commands)
        assert r.returncode==0,label+': offline replay failed'
        assert (root/(label+'.json')).read_bytes()==(s/(label+'.json')).read_bytes(),label+': output changed'
    save(root/'replay.json',dict(passed=True,archive_sha256=sha(a.archive),members_verified=len(members),new_source_root=True,
        empty_cwd=True,pythonpath_scope='new research source only, no platform/fixtures/old repository imports',
        all_four_reports_byte_identical=True,network_executions=0,commands=commands,native_runtime_bundled=False))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();(create if a.action=='create' else restore)(a)


if __name__=='__main__':main()
