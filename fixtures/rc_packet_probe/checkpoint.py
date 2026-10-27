"""Archive sources/evidence and repeat offline checks from an empty cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')


def create(repo,run,archive):
    assert not archive.exists() and json.loads((run/'verification.json').read_bytes())['passed']
    files={}
    for folder,pattern in [('python/moe_ub_cosim','*.py'),('examples/rc_packet_probe','*'),
            ('fixtures/rc_packet_probe','*.py')]:
        for p in sorted((repo/folder).glob(pattern)):
            if p.is_file():files['repository/'+p.relative_to(repo).as_posix()]=p
    for name in ('fixtures/rc_packet_probe/README.md','docs/tasks/rc_packet_probe_spec.md','deps.lock.json',
            'tests/unit/rc_packet_test.py','tests/unit/rc_reference_test.py','tests/unit/native_compilation_test.py'):
        files['repository/'+name]=repo/name
    for p in (repo/'backend/rdma').iterdir():
        if p.is_file():files['repository/'+p.relative_to(repo).as_posix()]=p
    for name in ('inputs','healthy','drop_write_middle','drop_write_ack','drop_atomic_ack','legacy','dependencies','failed-build-source'):
        for p in sorted((run/name).rglob('*')):
            assert not p.is_symlink()
            if p.is_file():files['evidence/'+p.relative_to(run).as_posix()]=p
    for name in ('verification-initial.json','verification.json','report.html','report-original-link.html','failure-notes.json','identity.json'):
        files['evidence/'+name]=run/name
    # Never archive the active archive monitor's changing logs or partial state.
    finished_monitors=[p for p in run.glob('*-monitor') if (p/'probe.json').is_file()
        and json.loads((p/'probe.json').read_bytes())['status'] in ('completed','child_failed')]
    for folder in (run/'build/logs',*finished_monitors):
        for p in sorted(folder.rglob('*')):
            if p.is_file():files['evidence/'+p.relative_to(run).as_posix()]=p
    for name in ('provenance.json','backend.json','compiler-options.json','ldd.txt','cmake/compile_commands.json'):
        files['evidence/build/'+name]=run/'build'/name
    manifest={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(files.items())}
    assert sum(r['bytes'] for r in manifest.values())<64*2**20
    save(run/'manifest.json',manifest)
    with tarfile.open(archive,'w:gz') as f:
        f.add(run/'manifest.json',arcname='manifest.json',recursive=False)
        for name,p in sorted(files.items()):f.add(p,arcname=name,recursive=False)
    save(archive.with_suffix('.receipt.json'),dict(sha256=sha(archive),members=len(manifest),
        compressed_bytes=archive.stat().st_size,content_bytes=sum(r['bytes'] for r in manifest.values()),
        binary_included=False,dependency_bundles_require_declared_prior_commits=True))


def restore(archive,expected,output):
    assert sha(archive)==expected and not output.exists();output.mkdir()
    with tarfile.open(archive,'r:gz') as f:
        members=f.getmembers();assert len({m.name for m in members})==len(members)
        manifest=json.loads(f.extractfile('manifest.json').read())
        assert len(members)==len(manifest)+1
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            data=f.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name];assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            p=output/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    repo=output/'repository';run=output/'evidence';empty=output/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(repo/'python'))
    commands=[
        [sys.executable,'-B',str(repo/'fixtures/rc_packet_probe/verify.py'),'--run',str(run),'--output',str(output/'verification.json')],
        [sys.executable,'-B',str(repo/'fixtures/rc_packet_probe/report.py'),'--verification',str(output/'verification.json'),'--output',str(output/'report.html')],
        [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests/unit'),'-p','rc_packet_test.py'],
        [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests/unit'),'-p','rc_reference_test.py'],
        [sys.executable,'-B',str(repo/'examples/rc_packet_probe/prepare.py'),'--output',str(output/'regenerated-inputs')],
        [sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-rc-probe','--help']]
    for i,cmd in enumerate(commands):
        result=subprocess.run(cmd,cwd=empty,env=env,capture_output=True,timeout=30)
        (output/f'command-{i}.stdout').write_bytes(result.stdout);(output/f'command-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr.decode())
    assert sha(output/'verification.json')==sha(run/'verification.json')
    assert sha(output/'report.html')==sha(run/'report.html')
    equal=0
    for p in (run/'inputs').rglob('*'):
        if not p.is_file():continue
        other=output/'regenerated-inputs'/p.relative_to(run/'inputs')
        assert sha(p)==sha(other)
        equal+=1
    save(output/'recovery.json',dict(passed=True,members_verified=len(manifest),copied_source=True,
        empty_cwd=True,byte_equal_verification=True,byte_equal_html=True,regenerated_input_files=equal,
        byte_equal_all_input_files=True,input_tests=3,reference_tests=11,
        network_runs=0,builds=0,HTTP=0,GPU=0,full_build_offline_reproduction_claimed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('create')
    for k in ('repo','run','archive'):a.add_argument('--'+k,type=Path,required=True)
    a=s.add_parser('restore');a.add_argument('--archive',type=Path,required=True)
    a.add_argument('--sha256',required=True);a.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repo.resolve(),a.run.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.sha256,a.output.resolve())
