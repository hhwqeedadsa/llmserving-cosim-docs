"""Hash archive and copied-source/empty-cwd offline recovery; no network/build."""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def create(repo,stage,archive):
    assert not archive.exists() and json.loads((stage/'verification.json').read_bytes())['passed']
    files={}
    for folder,pattern in [('python/moe_ub_cosim','*.py'),('backend/rdma','*'),
            ('examples/scaleout_counter_storage','*'),('examples/scaleout_multirail_native','*.json'),
            ('examples/scaleout_rdma','*.json'),('fixtures/switch_counter_storage','*')]:
        for p in (repo/folder).glob(pattern):
            if p.is_file():files['repository/'+p.relative_to(repo).as_posix()]=p
    for name in ('examples/scaleout_rdma_streams/ep_groups.py','fixtures/physical_nic_binding/checks.py',
            'fixtures/persistent_scale/audit.py','tests/unit/switch_counter_storage_test.py',
            'docs/tasks/switch_counter_storage_spec.md','deps.lock.json'):
        files['repository/'+name]=repo/name
    for folder in ('inputs','inputs-initial','small-topology','large-topology','large-topology-initial','dependencies','failed-checker-source',
            'small-old','small-default','small-legacy_dense','small-port_dense',
            'failure-legacy_dense','failure-port_dense','large-legacy_dense','large-port_dense'):
        for p in (stage/folder).rglob('*'):
            assert not p.is_symlink()
            if p.is_file():files['evidence/'+p.relative_to(stage).as_posix()]=p
    for p in stage.rglob('probe.json'):
        value=json.loads(p.read_bytes())
        if value['status'] not in ('completed','child_failed'):continue
        for f in p.parent.iterdir():
            if f.is_file():files['evidence/'+f.relative_to(stage).as_posix()]=f
    for name in ('verification.json','small-verification.json','healthy-gate.json','native-check.json',
            'identity.json','report.html','failure-notes.json'):
        files['evidence/'+name]=stage/name
    for name in ('backend.json','provenance.json','compiler-options.json','ldd.txt','cmake/compile_commands.json'):
        files['evidence/build/'+name]=stage/'build'/name
    for p in (stage/'build/logs').rglob('*'):
        if p.is_file():files['evidence/'+p.relative_to(stage).as_posix()]=p
    manifest={n:dict(sha256=sha(p),bytes=p.stat().st_size) for n,p in sorted(files.items())}
    save(stage/'manifest.json',manifest)
    with tarfile.open(archive,'w:gz') as stream:
        stream.add(stage/'manifest.json',arcname='manifest.json',recursive=False)
        for name,path in sorted(files.items()):stream.add(path,arcname=name,recursive=False)
    receipt=dict(sha256=sha(archive),members=len(manifest),content_bytes=sum(v['bytes'] for v in manifest.values()),
        compressed_bytes=archive.stat().st_size,binary_included=False,dependency_bundles_require_prior_commits=True)
    save(archive.with_suffix('.receipt.json'),receipt)


def restore(archive,expected,out):
    assert sha(archive)==expected and not out.exists();out.mkdir()
    with tarfile.open(archive,'r:gz') as stream:
        members=stream.getmembers();manifest=json.loads(stream.extractfile('manifest.json').read())
        assert len(members)==len(manifest)+1 and len({m.name for m in members})==len(members)
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            data=stream.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name];assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            target=out/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    repo=out/'repository';stage=out/'evidence';empty=out/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(repo/'python'))
    commands=[
        [sys.executable,'-B',repo/'fixtures/switch_counter_storage/verify.py','--run',stage,'--output',out/'verification.json'],
        [sys.executable,'-B',repo/'fixtures/switch_counter_storage/report.py','--verification',out/'verification.json','--output',out/'report.html'],
        [sys.executable,'-B',repo/'examples/scaleout_counter_storage/prepare.py','--repo',repo,'--output',out/'regenerated-inputs'],
        [sys.executable,'-B','-m','unittest','discover','-s',repo/'tests/unit','-p','switch_counter_storage_test.py'],
        [sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe','--help']]
    for i,command in enumerate(commands):
        result=subprocess.run(list(map(str,command)),env=env,cwd=empty,capture_output=True,timeout=30)
        (out/f'command-{i}.stdout').write_bytes(result.stdout);(out/f'command-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr.decode())
    for name in ('verification.json','report.html'):assert sha(out/name)==sha(stage/name)
    count=0
    for path in (stage/'inputs').rglob('*'):
        if path.is_file():assert sha(path)==sha(out/'regenerated-inputs'/path.relative_to(stage/'inputs'));count+=1
    save(out/'recovery.json',dict(passed=True,members_verified=len(manifest),copied_source=True,empty_cwd=True,
        byte_equal_verification=True,byte_equal_html=True,byte_equal_input_files=count,
        input_tests=4,network_runs=0,builds=0,HTTP=0,GPU=0,full_offline_build_reproduction_claimed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('create')
    for key in ('repo','stage','archive'):a.add_argument('--'+key,type=Path,required=True)
    a=s.add_parser('restore');a.add_argument('--archive',type=Path,required=True)
    a.add_argument('--sha256',required=True);a.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repo.resolve(),a.stage.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.sha256,a.output.resolve())
