"""Bounded, hash-checked source/runtime archive; recovery executes no networks."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def admit(path, digest):
    assert path.stat().st_size < 128*2**20 and sha(path) == digest
    blobs, total = {}, 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in member.name and str(name) == member.name and member.name not in blobs
            assert len(blobs) < 4096 and 0 <= member.size <= 64*2**20
            total += member.size; assert total < 256*2**20
            raw = archive.extractfile(member).read(); assert len(raw) == member.size
            blobs[member.name] = raw
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == 'physical-nic-binding-checkpoint/1'
    assert set(blobs) == set(manifest['files'])
    for name, raw in blobs.items():
        assert not any(str(parent) in blobs for parent in PurePosixPath(name).parents)
        entry = manifest['files'][name]
        assert entry['bytes'] == len(raw) and entry['sha256'] == hashlib.sha256(raw).hexdigest()
        assert entry['mode'] in (0o644, 0o755)
    return blobs, manifest


def create(repository, stage, archive):
    assert load(stage/'verification.json')['passed'] and not archive.exists()
    files = {}
    selected = list(stage.glob('*.json'))
    for directory in stage.iterdir():
        if not directory.is_dir(): continue
        if directory.name in ('inputs', 'dependency-bundles', 'before-single', 'after-single', 'independent',
                              'shared-congestion', 'transactions', 'modern', 'alternate', 'partition', 'negative') or directory.name.endswith('-topology'):
            selected += [p for p in directory.rglob('*') if p.is_file()]
        elif (directory/'probe.json').exists(): selected += [p for p in directory.iterdir() if p.is_file()]
    selected += [p for p in (stage/'build/logs').rglob('*') if p.is_file()]
    selected += [stage/'build'/name for name in ('backend.json', 'provenance.json', 'ldd.txt')]
    backend = load(stage/'build/backend.json')
    selected += [Path(backend[key]) for key in ('probe','stream_probe','transaction_probe','transaction_session','service')]
    selected += [Path(p) for p in backend['linked_ns3_libraries']]
    selected.append(stage/'rdma-local-address-test')
    for path in selected:
        assert path.is_file() and not path.is_symlink()
        files['evidence/'+str(path.relative_to(stage))] = path
    for directory, pattern in (('python/moe_ub_cosim','*.py'), ('backend','*'), ('fixtures/physical_nic_binding','*.py'),
                               ('examples/scaleout_multirail_native','*')):
        for path in (repository/directory).rglob(pattern):
            if path.is_file() and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repository))] = path
    names = ['deps.lock.json','AGENTS.md','docs/development.md','docs/tasks/physical_nic_binding_spec.md',
             'fixtures/physical_nic_binding/README.md','tests/unit/stream_nic_routes_test.py',
             'tests/native/rdma_local_address_test.cc','fixtures/modern_native/audit.py','fixtures/modern_native/stage.py',
             'fixtures/context_transactions/stage.py','fixtures/native_build_identity/verification.json']
    native = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/'
    names += [native+'rdma-hw.'+ext for ext in ('h','cc')]
    for name in names: files['source/'+name] = repository/name
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path), mode=0o755 if os.access(path, os.X_OK) else 0o644)
               for name, path in files.items()}
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size, info.mode = len(raw), entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version='physical-nic-binding-checkpoint/1', files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); target.addfile(info, io.BytesIO(raw))
    digest = sha(archive); admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1, compressed_bytes=archive.stat().st_size,
         uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = load(archive.with_suffix('.receipt.json'))['sha256']
    blobs, manifest = admit(archive, digest)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(manifest['files'][name]['mode'])
    empty, source = output/'empty-cwd', output/'source'; empty.mkdir()
    env = os.environ | dict(PYTHONPATH=str(source/'python')+':'+str(source), PYTHONDONTWRITEBYTECODE='1')
    commands = [[sys.executable,'-P','-B','-m','fixtures.physical_nic_binding.verify','--stage',str(output/'evidence')],
                [sys.executable,'-P','-B','-m','unittest','discover','-s',str(source/'tests/unit'),'-p','stream_nic_routes_test.py']]
    records = []
    for index, command in enumerate(commands):
        done = subprocess.run(command, cwd=empty, env=env, capture_output=True, text=True, timeout=60)
        (output/f'command{index}.stdout').write_text(done.stdout); (output/f'command{index}.stderr').write_text(done.stderr)
        records.append(dict(command=command, cwd=str(empty), exit_code=done.returncode)); save(output/'commands.json', records)
        assert done.returncode == 0, done.stderr
    for name in ('verification.json','tampering.json'):
        assert (output/'evidence'/name).read_bytes() == blobs['evidence/'+name], name
    save(output/'recovery.json', dict(passed=True, sha256=digest, verified_members=len(blobs), copied_source=True,
        empty_cwd=True, verification_and_tampering_byte_identical=True, repeated_route_methods=10,
        network_executions=0, builds=0, runtime_preserved_not_executed=True,
        dependency_bundles_are_incremental_and_require_recorded_baselines=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create','restore'))
    for name in ('repository','stage','archive','output'): parser.add_argument('--'+name, type=Path)
    args = parser.parse_args()
    if args.action == 'create': create(args.repository.resolve(), args.stage.resolve(), args.archive.resolve())
    else: restore(args.archive.resolve(), args.output.resolve())
