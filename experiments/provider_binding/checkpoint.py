"""Research-source checkpoint; isolated offline replay, zero network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from experiments.reordering_baseline.checkpoint import admit as admit_format,SCHEMA
from experiments.provider_binding.stage import load,save,sha


def admit(path):
    payload,manifest=admit_format(path);assert manifest['experiment']=='provider_binding' and not manifest['runtime_bundled']
    return payload,manifest


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists()
    assert load(s/'verification-complete.json')['passed'];files={}
    for directory in ('inputs','extra','legacy','plan'):
        for p in sorted((s/directory).rglob('*')):
            if p.is_file():files['evidence/'+str(p.relative_to(s))]=p
    for p in sorted(s.iterdir()):
        if p.is_file() and p.suffix in ('.json','.txt'):files['evidence/'+p.name]=p
    for p in sorted(s.glob('*-monitor/*')):
        if p.is_file() and (p.parent/'probe.json').exists():files['evidence/monitors/'+str(p.relative_to(s))]=p
    for p in sorted((repo/'experiments/provider_binding').iterdir()):
        if p.is_file():files['source/experiments/provider_binding/'+p.name]=p
    for name in ('checkpoint.py','stage.py'):
        p=repo/'experiments/reordering_baseline'/name;files['source/'+str(p.relative_to(repo))]=p
    report=repo/'docs/reviews/现代连接共享范围与网络优化对象_20261024.md'
    files['source/'+str(report.relative_to(repo))]=report
    assert all(not p.is_symlink() and p.stat().st_size<16*2**20 for p in files.values())
    entries={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in files.items()}
    assert len(entries)<2048 and sum(e['bytes'] for e in entries.values())<64*2**20
    manifest=dict(schema_version=SCHEMA,experiment='provider_binding',files=entries,runtime_bundled=False,
        scope='fixed original routes/source, public plan and CPU scalar-function observations; no native/CUDA runtime')
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256'];m=tarfile.TarInfo(name);m.size=len(raw);ar.addfile(m,io.BytesIO(raw))
        raw=(json.dumps(manifest,indent=2)+'\n').encode();m=tarfile.TarInfo('manifest.json');m.size=len(raw);ar.addfile(m,io.BytesIO(raw))
    admit(a.output);save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(raw),compressed_bytes=a.output.stat().st_size,runtime_bundled=False))


def restore(a):
    assert not a.output.exists();payload,manifest=admit(a.archive);a.output.mkdir()
    for name,raw in payload.items():
        p=a.output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    out=a.output.resolve();s=out/'evidence';src=out/'source';cwd=out/'empty-cwd';cwd.mkdir()
    env=dict(os.environ,PYTHONPATH=str(src),PYTHONDONTWRITEBYTECODE='1');jobs=[
        ('analysis','audit',['analyze','--stage',s]),
        ('legacy-comparison','legacy',['compare','--stage',s,'--analysis',out/'analysis.json']),
        ('verification-complete','verify',['--stage',s,'--analysis',out/'analysis.json','--comparison',out/'legacy-comparison.json'])]
    commands=[]
    for label,module,args in jobs:
        command=[sys.executable,'-B','-m','experiments.provider_binding.'+module,*map(str,args),'--output',str(out/(label+'.json'))]
        with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
            r=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(command=command,cwd=str(cwd),pythonpath=str(src),exit_code=r.returncode));save(out/'commands.json',commands)
        assert r.returncode==0,label+': preserve replay failure'
        assert (out/(label+'.json')).read_bytes()==(s/(label+'.json')).read_bytes(),label+': differs'
    save(out/'replay.json',dict(passed=True,archive_sha256=sha(a.archive),members_verified=len(payload),
        three_json_byte_identical=True,new_source_root=True,empty_cwd=True,commands=commands,
        new_network_executions=0,provider_or_cuda_executed=False,
        scope='research sources and saved public plan/scalar outputs only; no original repository, platform imports or native runtime'))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();dict(create=create,restore=restore)[a.action](a)


if __name__=='__main__':main()
