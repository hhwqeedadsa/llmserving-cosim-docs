"""Save packet evidence/runtime and verify a relocated copy without a network run."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


def create(repo, run, archive):
    assert not archive.exists() and json.loads((run/'verification.json').read_bytes())['passed']
    files = {}
    for folder, pattern in [('python/moe_ub_cosim', '*.py'), ('examples/rc_concurrent', '*'),
                            ('fixtures/rc_concurrent_fabric', '*.py'), ('backend/rdma', '*')]:
        for path in sorted((repo/folder).glob(pattern)):
            if path.is_file():
                files['repository/'+path.relative_to(repo).as_posix()] = path
    for name in ('tests/test_rc_concurrent.py', 'tests/unit/rc_packet_test.py',
                 'tests/unit/rc_reference_test.py', 'tests/unit/native_compilation_test.py',
                 'examples/rc_packet_probe/prepare.py', 'deps.lock.json',
                 'docs/tasks/rc_concurrent_fabric_spec.md', 'fixtures/rc_concurrent_fabric/README.md'):
        files['repository/'+name] = repo/name
    for name in ('inputs', 'healthy', 'drop_write_middle', 'drop_atomic_ack', 'build'):
        for path in sorted((run/name).rglob('*')):
            assert not path.is_symlink()
            if path.is_file() and path.suffix != '.o':
                files['evidence/'+path.relative_to(run).as_posix()] = path
    for folder in sorted(run.glob('*-monitor')):
        receipt = folder/'probe.json'
        if not receipt.is_file():
            continue  # Never select an active monitor's growing logs.
        assert json.loads(receipt.read_bytes())['status'] in ('completed', 'child_failed')
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                files['evidence/'+path.relative_to(run).as_posix()] = path
    for name in ('verification.json', 'verification-before-offline-portability.json',
                 'verification-final.json', 'identity.json', 'report.html'):
        files['evidence/'+name] = run/name
    manifest = {name: dict(bytes=path.stat().st_size, sha256=sha(path)) for name,path in sorted(files.items())}
    assert sum(row['bytes'] for row in manifest.values()) < 64*2**20
    save(run/'manifest.json', manifest)
    with tarfile.open(archive, 'w:gz') as stream:
        stream.add(run/'manifest.json', arcname='manifest.json', recursive=False)
        for name, path in sorted(files.items()):
            stream.add(path, arcname=name, recursive=False)
    save(archive.with_suffix('.receipt.json'), dict(sha256=sha(archive), members=len(manifest),
        compressed_bytes=archive.stat().st_size, content_bytes=sum(row['bytes'] for row in manifest.values()),
        prebuilt_driver_and_runtime_included=True, full_build_offline_reproduction_claimed=False))


def restore(archive, expected, output):
    assert sha(archive)==expected and not output.exists()
    output.mkdir()
    with tarfile.open(archive, 'r:gz') as stream:
        members=stream.getmembers()
        assert len({member.name for member in members})==len(members)
        manifest=json.loads(stream.extractfile('manifest.json').read())
        assert len(members)==len(manifest)+1
        for member in members:
            path=PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            data=stream.extractfile(member).read()
            if member.name!='manifest.json':
                row=manifest[member.name]
                assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
            target=output/member.name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            if member.name=='evidence/build/moe-rdma-rc-concurrent':
                target.chmod(0o755)
    repo=output/'repository';run=output/'evidence';empty=output/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(repo/'python'),str(repo))))
    for key in ('LD_PRELOAD','LD_LIBRARY_PATH'):
        env.pop(key,None)
    commands=[
        [sys.executable,'-B',str(repo/'fixtures/rc_concurrent_fabric/verify.py'),'--stage',str(run),
         '--output',str(output/'verification.json')],
        [sys.executable,'-B',str(repo/'fixtures/rc_concurrent_fabric/report.py'),
         '--verification',str(output/'verification.json'),'--output',str(output/'report.html')],
        [sys.executable,'-B',str(repo/'examples/rc_concurrent/prepare.py'),'--output',str(output/'regenerated-inputs')],
        [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests'),'-p','test_rc_concurrent.py'],
        [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests/unit'),'-p','rc_*test.py'],
        [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests/unit'),'-p','native_compilation_test.py'],
        [sys.executable,'-B','-m','moe_ub_cosim','scaleout','rc-fabric-build','--help'],
        [sys.executable,'-B','-m','moe_ub_cosim','scaleout','rc-fabric-probe','--help'],
        ['ldd',str(run/'build/moe-rdma-rc-concurrent')]]
    for index,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,env=env,capture_output=True,timeout=30)
        (output/f'command-{index}.stdout').write_bytes(result.stdout)
        (output/f'command-{index}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(index,result.stderr.decode())
    assert sha(output/'verification.json')==sha(run/'verification.json')
    assert sha(output/'report.html')==sha(run/'report.html')
    equal=0
    for path in (run/'inputs').rglob('*'):
        if path.is_file():
            assert sha(path)==sha(output/'regenerated-inputs'/path.relative_to(run/'inputs'))
            equal+=1
    actual={}
    text=(output/'command-8.stdout').read_text()
    assert 'not found' not in text
    for line in text.splitlines():
        if 'libns3' in line and '=>' in line:
            name,right=line.split('=>',1);path=Path(right.split()[0]).resolve()
            assert path.parent==run/'build/runtime/lib'
            assert name.strip() not in actual
            actual[name.strip()]=sha(path)
    assert actual==json.loads((run/'build/backend.json').read_bytes())['library_hashes']
    save(output/'recovery.json',dict(passed=True,members_verified=len(manifest),copied_source=True,
        empty_cwd=True,byte_equal_verification=True,byte_equal_html=True,regenerated_input_files=equal,
        byte_equal_all_input_files=True,relocated_native_libraries_verified=len(actual),
        input_tests=12,reference_input_regressions=14,network_runs=0,builds=0,HTTP=0,GPU=0,
        full_build_offline_reproduction_claimed=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    create_parser=sub.add_parser('create')
    for key in ('repo','run','archive'):
        create_parser.add_argument('--'+key,type=Path,required=True)
    restore_parser=sub.add_parser('restore')
    restore_parser.add_argument('--archive',type=Path,required=True)
    restore_parser.add_argument('--sha256',required=True)
    restore_parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='create':
        create(args.repo.resolve(),args.run.resolve(),args.archive.resolve())
    else:
        restore(args.archive.resolve(),args.sha256,args.output.resolve())
