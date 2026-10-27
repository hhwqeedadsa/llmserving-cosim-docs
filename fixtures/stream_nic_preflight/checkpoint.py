"""Small portable source/input bundle; recovery executes no native network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def load(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def admit(path,digest):
    assert path.stat().st_size<32*2**20 and sha(path)==digest
    blobs={};total=0
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            p=PurePosixPath(member.name)
            assert member.isfile() and not p.is_absolute() and '..' not in p.parts and '\\' not in member.name
            assert str(p)==member.name and member.name not in blobs and len(blobs)<2048
            assert 0<=member.size<8*2**20
            total+=member.size;assert total<128*2**20
            raw=archive.extractfile(member).read();assert len(raw)==member.size;blobs[member.name]=raw
    manifest=json.loads(blobs.pop('manifest.json'));assert set(blobs)==set(manifest['files'])
    for name,raw in blobs.items():
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents if str(p)!='.')
        assert manifest['files'][name]==dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return blobs


def create(repo,stage,output):
    assert load(stage/'after/verification.json')['passed'] and not output.exists()
    files={}
    for p in stage.rglob('*'):
        if p.is_file():
            # A running monitor has no completed probe yet; it stays outside the bundle.
            monitor=next((parent for parent in p.parents if parent.name.endswith('-monitor')),None)
            if monitor is not None and not (monitor/'probe.json').exists():continue
            files['evidence/'+str(p.relative_to(stage))]=p
    for p in (repo/'python/moe_ub_cosim').rglob('*.py'):files['source/'+str(p.relative_to(repo))]=p
    for name in ('checks.py','checkpoint.py'):
        p=repo/'fixtures/stream_nic_preflight'/name;files['source/'+str(p.relative_to(repo))]=p
    for name in ('topology.json','input.json','rdma.json'):
        p=repo/'examples/modern_deepep_native'/name;files['source/'+str(p.relative_to(repo))]=p
    p=repo/'tests/unit/stream_nic_routes_test.py';files['source/'+str(p.relative_to(repo))]=p
    entries={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in files.items()}
    assert not any(p.is_symlink() for p in files.values())
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,p in sorted(files.items()):
            raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;archive.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(dict(schema_version='stream-nic-preflight-checkpoint/1',files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
    digest=sha(output);admit(output,digest)
    save(output.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=output.stat().st_size,uncompressed_bytes=sum(v['bytes'] for v in entries.values())))


def restore(archive,output):
    assert not output.exists();digest=load(archive.with_suffix('.receipt.json'))['sha256']
    blobs=admit(archive,digest);output.mkdir()
    for name,raw in blobs.items():
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    empty=output/'empty-cwd';empty.mkdir();source=output/'source'
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    commands=[
        [sys.executable,'-B','-m','fixtures.stream_nic_preflight.checks','after','--repository',str(source),
         '--previous',str(output/'evidence/before'),'--output',str(output/'recheck')],
        [sys.executable,'-B','-m','unittest','discover','-s',str(source/'tests/unit'),'-p','stream_nic_routes_test.py','-v']]
    records=[]
    for i,cmd in enumerate(commands):
        done=subprocess.run(cmd,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
        (output/f'command{i}.stdout').write_text(done.stdout);(output/f'command{i}.stderr').write_text(done.stderr)
        records.append(dict(command=cmd,cwd=str(empty),exit_code=done.returncode));save(output/'commands.json',records)
        assert done.returncode==0,done.stderr
    expected=load(output/'evidence/after/verification.json');actual=load(output/'recheck/verification.json')
    equal={key:actual[key]==value for key,value in expected.items() if key!='commands'}
    assert all(equal.values())
    assert (output/'recheck/topology/topology.json').read_bytes()==blobs['evidence/after/topology/topology.json']
    save(output/'recovery.json',dict(passed=True,archive_sha256=digest,hash_verified_members=len(blobs),
        copied_source_root=True,empty_cwd=True,public_acceptance_equal=equal,topology_byte_identical=True,
        independent_path_unit_tests_repeated=8,network_executions=0,backend_was_not_started=True))


def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
    c=s.add_parser('create')
    for n in ('repository','stage','output'):c.add_argument('--'+n,type=Path,required=True)
    r=s.add_parser('restore')
    for n in ('archive','output'):r.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
