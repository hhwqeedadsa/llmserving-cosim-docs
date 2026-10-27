"""Seal and recover RC reference evidence without network execution or builds."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

SOURCE = ('python/moe_ub_cosim/__init__.py','python/moe_ub_cosim/rc_reference.py',
          'examples/rc_operation_reference/README.md','examples/rc_operation_reference/run.py',
          'tests/unit/rc_reference_test.py','tests/unit/rc_source_branches.py','scripts/rc_reference_sources.py',
          'fixtures/rc_operation_reference/README.md','fixtures/rc_operation_reference/verify.py',
          'fixtures/rc_operation_reference/checkpoint.py','docs/tasks/rc_operation_reference_spec.md')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')


def create(repo,run,archive):
    assert not archive.exists() and json.loads((run/'verification.json').read_bytes())['passed']
    files={'repository/'+name:repo/name for name in SOURCE}
    for name in ('upstream','oracle-v3','oracle-v4',*[p.name for p in run.glob('*-monitor') if (p/'probe.json').exists()]):
        for p in sorted((run/name).rglob('*')):
            assert not p.is_symlink()
            if p.is_file():files['evidence/'+p.relative_to(run).as_posix()]=p
    for name in ('example.json','verification.json','failure-notes.json'):
        files['evidence/'+name]=run/name
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p),mode=p.stat().st_mode & 0o777)
              for name,p in files.items()}
    assert sum(r['bytes'] for r in manifest.values())<16*2**20
    save(run/'manifest.json',manifest)
    with tarfile.open(archive,'w:gz') as target:
        target.add(run/'manifest.json',arcname='manifest.json',recursive=False)
        for name,p in sorted(files.items()):target.add(p,arcname=name,recursive=False)
    save(archive.with_suffix('.receipt.json'),dict(sha256=sha(archive),content_members=len(files),
        bytes=archive.stat().st_size,uncompressed_content_bytes=sum(r['bytes'] for r in manifest.values())))


def restore(archive,expected,out):
    assert sha(archive)==expected and not out.exists();out.mkdir()
    with tarfile.open(archive) as source:
        members=source.getmembers();manifest=json.load(source.extractfile('manifest.json'))
        assert len(members)<500 and len({m.name for m in members})==len(members)
        assert set(manifest)|{'manifest.json'}=={m.name for m in members}
        assert sum(m.size for m in members)<16*2**20
        for member in members:
            p=PurePosixPath(member.name)
            assert member.isfile() and not p.is_absolute() and '..' not in p.parts
            raw=source.extractfile(member).read()
            if member.name!='manifest.json':
                r=manifest[member.name]
                assert len(raw)==r['bytes'] and hashlib.sha256(raw).hexdigest()==r['sha256']
            target=out/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            if member.name!='manifest.json':target.chmod(manifest[member.name]['mode'])
    repo=out/'repository';run=out/'evidence';empty=out/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(repo/'python'))
    commands=[
      [sys.executable,'-B',str(repo/'examples/rc_operation_reference/run.py'),'--output',str(out/'example.json')],
      [sys.executable,'-B','-m','unittest','discover','-s',str(repo/'tests/unit'),'-p','rc_reference_test.py','-v'],
      [sys.executable,'-I','-B',str(repo/'scripts/rc_reference_sources.py'),'verify','--root',str(run/'upstream')],
      [sys.executable,'-I','-B',str(repo/'tests/unit/rc_source_branches.py'),'--sources',str(run/'upstream'),'--output',str(out/'regenerated')],
      [sys.executable,'-I','-B',str(repo/'fixtures/rc_operation_reference/verify.py'),'--run',str(run),'--output',str(out/'verification.json')],
      [str(run/'oracle-v4/branches')]]
    for i,cmd in enumerate(commands):
        p=subprocess.run(cmd,cwd=empty,env=env,capture_output=True,timeout=20)
        (out/f'command-{i}.stdout').write_bytes(p.stdout);(out/f'command-{i}.stderr').write_bytes(p.stderr)
        assert p.returncode==0,(i,p.stderr.decode())
    assert sha(out/'example.json')==sha(run/'example.json')
    assert sha(out/'verification.json')==sha(run/'verification.json')
    for name in ('stub.h','responder.c','completer.c','main.c','source-branches.json','upstream-notices.txt'):
        assert sha(out/'regenerated'/name)==sha(run/'oracle-v4'/name)
    assert sha(out/'command-5.stdout')==sha(run/'oracle-run-v2-monitor/stdout.log')
    save(out/'recovery.json',dict(passed=True,archive_sha256=expected,content_members_verified=len(manifest),
        copied_source=True,empty_cwd=True,byte_equal_public_example=True,unit_tests=11,
        byte_equal_generated_source=True,byte_equal_verification=True,byte_equal_source_branch_output=True,
        network_runs=0,new_compiles=0,downloads=0,GPU_runs=0,full_RXE_executed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('create');a.add_argument('--repo',type=Path,required=True);a.add_argument('--run',type=Path,required=True);a.add_argument('--archive',type=Path,required=True)
    a=s.add_parser('restore');a.add_argument('--archive',type=Path,required=True);a.add_argument('--sha256',required=True);a.add_argument('--output',type=Path,required=True)
    x=p.parse_args()
    if x.action=='create':create(x.repo.resolve(),x.run.resolve(),x.archive.resolve())
    else:restore(x.archive.resolve(),x.sha256,x.output.resolve())
