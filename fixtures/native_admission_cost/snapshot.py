"""Checked local evidence/source snapshot; offline recheck never reruns C++."""
import argparse
import json
import os
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha


def create(repo,stage,output):
    assert not output.exists() and load(stage/'verification.json')['passed']
    output.mkdir();files={}
    (repo/'fixtures/native_admission_cost/verification.json').write_bytes((stage/'verification.json').read_bytes())
    (repo/'fixtures/native_admission_cost/identity.json').write_bytes((stage/'identity.json').read_bytes())
    for path in sorted(stage.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(stage)
        if relative.parts[0].endswith('-monitor') and not (stage/relative.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(relative)]=path
    for path in sorted((repo/'fixtures/native_admission_cost').rglob('*')):
        if path.is_file() and path.suffix in ('.py','.cc','.md','.json'):files['source/'+str(path.relative_to(repo))]=path
    identity=load(stage/'identity.json')
    for name in (*identity['native_headers'],'fixtures/native_wakeup/stage.py','deps.lock.json',
                 'docs/tasks/native_admission_cost_spec.md','docs/tasks/current.md',
                 'docs/reviews/四请求超时与原生准入成本_20261024.md'):
        files['source/'+name]=repo/name
    assert all(not path.is_symlink() and path.stat().st_size<16*2**20 for path in files.values())
    assert sum(path.stat().st_size for path in files.values())<32*2**20
    entries={}
    for name,path in files.items():
        data=path.read_bytes();digest=sha(path)
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        assert sha(target)==digest
        entries[name]=dict(bytes=len(data),sha256=digest)
    (output/'empty-cwd').mkdir()
    save(output/'manifest.json',dict(files=entries,
        scope='copied source and diagnostic evidence; offline recheck uses original immutable eight-library paths; no independent native build'))


def recheck(snapshot,output):
    assert Path(__file__).resolve().is_relative_to(snapshot/'source')
    os.chdir(snapshot/'empty-cwd')
    manifest=load(snapshot/'manifest.json')
    assert all((snapshot/name).stat().st_size==row['bytes'] and sha(snapshot/name)==row['sha256']
               for name,row in manifest['files'].items())
    from fixtures.native_admission_cost.verify import verify
    value=verify(snapshot/'evidence')
    raw=(json.dumps(value,indent=2)+'\n').encode()
    assert raw==(snapshot/'evidence/verification.json').read_bytes()
    save(output,dict(passed=True,network_executed=False,native_measurement_reexecuted=False,
        verified_members=len(manifest['files']),verification_byte_identical=True,
        verification_sha256=sha(snapshot/'evidence/verification.json'),
        fresh_source_root=str(snapshot/'source'),cwd=str(Path.cwd()),
        fixed_eight_original_libraries_rechecked=True,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    r=sub.add_parser('verify')
    for name in ('snapshot','output'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:recheck(a.snapshot.resolve(),a.output.resolve())


if __name__=='__main__':main()
