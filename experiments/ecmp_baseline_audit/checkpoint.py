"""Bounded hashed source/input recovery; prediction never receives truth input."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

from .model import load, save, sha


def admit(archive, digest):
    assert archive.stat().st_size < 32*2**20 and sha(archive) == digest
    files, total = {}, 0
    with tarfile.open(archive,'r:gz') as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            assert member.isfile() and str(name) == member.name and not name.is_absolute()
            assert '..' not in name.parts and '\\' not in member.name and member.name not in files
            assert 0 <= member.size <= 32*2**20 and len(files) < 2048
            total += member.size
            assert total < 64*2**20
            raw = stream.extractfile(member).read()
            assert len(raw) == member.size
            files[member.name] = raw
    manifest = json.loads(files.pop('manifest.json'))
    assert manifest['schema_version'] == 'ecmp-baseline-audit-checkpoint/1'
    assert set(files) == set(manifest['files'])
    for name,raw in files.items():
        assert not any(str(parent) in files for parent in PurePosixPath(name).parents)
        assert manifest['files'][name] == dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return files


def create(repo, run, output):
    assert not output.exists() and load(run/'verification.json')['passed']
    paths = {}
    for directory in ('observed','truth','predictions','report','report-initial','failed-source','monitor'):
        for path in sorted((run/directory).rglob('*')):
            if not path.is_file() or path.is_symlink(): continue
            if directory == 'monitor' and not (run/'monitor'/path.relative_to(run/'monitor').parts[0]/'probe.json').is_file(): continue
            paths['run/'+str(path.relative_to(run))] = path
    for name in ('preparation.json','execution.json','verification.json'):
        paths['run/'+name] = run/name
    for path in (repo/'experiments/ecmp_baseline_audit').glob('*'):
        if path.suffix in ('.py','.md'): paths['source/'+str(path.relative_to(repo))] = path
    receipt = dict(schema_version='ecmp-baseline-audit-checkpoint/1',files={name:dict(
        bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(paths.items())})
    assert sum(row['bytes'] for row in receipt['files'].values()) < 64*2**20
    with tarfile.open(output,'x:gz',compresslevel=6) as stream:
        for name,path in sorted(paths.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == receipt['files'][name]['sha256']
            info = tarfile.TarInfo(name); info.size=len(raw);info.mode=0o644
            stream.addfile(info,io.BytesIO(raw))
        raw = (json.dumps(receipt,indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json');info.size=len(raw);info.mode=0o644
        stream.addfile(info,io.BytesIO(raw))
    digest = sha(output)
    admit(output,digest)
    save(output.with_suffix('.receipt.json'),dict(sha256=digest,members=len(paths)+1,
        uncompressed_bytes=sum(r['bytes'] for r in receipt['files'].values()),compressed_bytes=output.stat().st_size))


def restore(archive, digest, output):
    files = admit(archive,digest)
    assert not output.exists()
    output.mkdir()
    for name,raw in files.items():
        path = output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    source,run = output/'source',output/'run'
    empty = output/'empty-cwd';empty.mkdir()
    env = os.environ|{'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    commands = []
    def command(module,*args):
        argv = [sys.executable,'-B','-m','experiments.ecmp_baseline_audit.'+module,*map(str,args)]
        result = subprocess.run(argv,cwd=empty,env=env,capture_output=True,text=True,timeout=50)
        commands.append(dict(command=argv,cwd=str(empty),exit_code=result.returncode,
            stdout=result.stdout,stderr=result.stderr))
        assert result.returncode == 0, commands[-1]
    command('model','--observed',run/'observed','--output',run/'recomputed-predictions')
    for path in (run/'predictions').iterdir():
        assert path.read_bytes() == (run/'recomputed-predictions'/path.name).read_bytes()
    command('verify','--run',run,'--output',run/'recomputed-verification.json')
    assert (run/'verification.json').read_bytes() == (run/'recomputed-verification.json').read_bytes()
    command('study','report','--run',run,'--output',run/'recomputed-report')
    for name in ('report.md','report.html'):
        assert (run/'report'/name).read_bytes() == (run/'recomputed-report'/name).read_bytes()
    save(output/'recovery.json',dict(passed=True,archive_sha256=digest,copied_source=True,
        empty_cwd=True,all_predictions_identical=True,verification_identical=True,reports_identical=True,
        new_networks=0,new_builds=0,commands=commands))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('create','restore'))
    p.add_argument('--repository',type=Path)
    p.add_argument('--run',type=Path)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action == 'create': create(a.repository.resolve(),a.run.resolve(),a.archive.resolve())
    else: restore(a.archive.resolve(),a.sha256,a.output.resolve())
