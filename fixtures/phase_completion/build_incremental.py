"""Compile only service.cc and link existing fixed objects into a new binary.

The old build/output is never mutated. This is an incremental functional build,
not proof that the project builds from a clean environment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


def build(build_dir,identity,output):
    output.mkdir(parents=True,exist_ok=False);receipt=json.loads(identity.read_bytes());backup=output/'previous';backup.mkdir()
    for field in ('binary_hashes','library_hashes'):
        for name,digest in receipt[field].items():
            path=Path(name)
            if sha(path)!=digest:raise ValueError('old binary/library changed: '+name)
            shutil.copy2(path,backup/path.name)
    rows=json.loads((build_dir/'compile_commands.json').read_bytes())
    row=next(r for r in rows if r['file']==str(ROOT/'backend/rdma/service.cc'))
    command=shlex.split(row['command']);old_object=command[command.index('-o')+1]
    new_object=output/'service.cc.o';command[command.index('-o')+1]=str(new_object)
    new_binary=output/'ns3.42-moe-rdma-service'
    old_binary=next(n for n in receipt['binary_hashes'] if n.endswith('/ns3.42-moe-rdma-service'))
    cache=(build_dir/'CMakeCache.txt').read_text().splitlines()
    ninja=next(line.split('=',1)[1] for line in cache if line.startswith('CMAKE_MAKE_PROGRAM:'))
    if not Path(ninja).is_file():raise ValueError('existing configured ninja is missing')
    commands=subprocess.check_output([ninja,'-C',str(build_dir),'-t','commands',old_binary],text=True).splitlines()
    link=shlex.split(commands[-1])
    if link[:2]!=[':','&&'] or link[-2:]!=['&&',':']:raise ValueError('unexpected native link command')
    link=link[2:-2]
    if any(x in link for x in ('&&',';','|')):raise ValueError('unsupported link shell operators')
    link[link.index('-o')+1]=str(new_binary)
    if link.count(old_object)!=1:raise ValueError('expected exactly one service object')
    link[link.index(old_object)]=str(new_object)
    save(output/'commands.json',dict(compile=command,link=link,cwd=row['directory']))
    for name,argv in (('compile',command),('link',link)):
        with (output/(name+'.stdout')).open('x') as out,(output/(name+'.stderr')).open('x') as err:
            result=subprocess.run(argv,cwd=row['directory'],stdout=out,stderr=err)
        save(output/(name+'.status.json'),dict(exit_code=result.returncode))
        if result.returncode:raise RuntimeError('incremental '+name+' failed; retain logs')
    for field in ('binary_hashes','library_hashes'):
        for name,digest in receipt[field].items():
            if sha(Path(name))!=digest:raise ValueError('old build was mutated')
    save(output/'backend.json',dict(service=str(new_binary),service_sha256=sha(new_binary),
        previous_identity_sha256=sha(identity),old_build_unchanged=True,
        libraries=receipt['library_hashes'],source_hashes={str(p.relative_to(ROOT)):sha(p) for p in (
            ROOT/'backend/rdma/service.cc',ROOT/'backend/rdma/phase_gates.hh')},
        scope='incremental service object and link; fixed existing archives/libraries; no cold build'))
    print(json.dumps(dict(passed=True,service=str(new_binary),old_build_unchanged=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('build-dir','identity','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();build(a.build_dir.resolve(),a.identity.resolve(),a.output.resolve())
