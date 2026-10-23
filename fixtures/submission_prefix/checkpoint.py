"""Archive bounded acceptance evidence and replay public commands from a new root."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[2]
def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args(); root = a.root.resolve(); dest = root/'checkpoint'
    assert not dest.exists(); dest.mkdir()
    items = {}
    for folder in ('python', 'backend', 'tests/integration', 'examples/scaleout_rdma',
                   'examples/scaleout_rdma_transactions', 'fixtures/submission_prefix'):
        for source in (REPO/folder).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                items['source/'+str(source.relative_to(REPO))] = source
    for folder in ('edge-accept', 'transaction-regression', 'session-regression', 'sparse-regression'):
        for source in (root/folder).rglob('*.json'):
            items['raw/'+str(source.relative_to(root))] = source
    for flavor in ('probe', 'session'):
        items['runtime/bin/'+flavor] = root/'rebuild/bin'/('ns3.42-moe-rdma-transaction-'+flavor)
    libraries = {Path(p).name: Path(p) for p in read(root/'old-runtime.json') if p.endswith('.so')}
    assert len(libraries) == 8
    for name, source in libraries.items():
        assert sha(source) == read(root/'old-runtime.json')[str(source)]
        items['runtime/lib/'+name] = source
    manifest = {name: dict(bytes=source.stat().st_size, sha256=sha(source)) for name, source in sorted(items.items())}
    assert sum(row['bytes'] for row in manifest.values()) < 96*2**20
    archive = dest/'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz', dereference=True) as tar:
        for name, source in sorted(items.items()): tar.add(source, arcname=name, recursive=False)
    assert archive.stat().st_size < 32*2**20
    save(dest/'members.json', manifest)
    restored = dest/'restored'; restored.mkdir()
    with tarfile.open(archive) as tar:
        assert len(tar.getmembers()) == len(manifest)
        for member in tar:
            path = PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and '..' not in path.parts
            data = tar.extractfile(member).read()
            assert len(data) == manifest[member.name]['bytes'] and hashlib.sha256(data).hexdigest() == manifest[member.name]['sha256']
            # Restore only current public inputs, code, runtime and two expected results.
            if member.name.startswith(('source/', 'runtime/')) or member.name in (
                    'raw/edge-accept/protocol.json', 'raw/edge-accept/prefix/result.json', 'raw/edge-accept/online/result.json'):
                target = restored/member.name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
                if member.name.startswith('runtime/bin/'): target.chmod(0o755)
    source = restored/'source'; empty = dest/'empty-cwd'; empty.mkdir(); fresh = dest/'fresh'; fresh.mkdir()
    environment = dict(os.environ, PYTHONPATH=str(source/'python'), PYTHONDONTWRITEBYTECODE='1', LD_LIBRARY_PATH=str(restored/'runtime/lib'))
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']; commands = []
    jobs = [('topology', cli+['topology', '--config', source/'examples/scaleout_rdma/small-clos-100g.json', '--output', fresh/'topology'])]
    for flavor in ('probe', 'session'):
        jobs.append((flavor, cli+['rdma-transaction-'+flavor, '--topology', fresh/'topology',
            '--protocol', restored/'raw/edge-accept/protocol.json', '--config', source/'examples/scaleout_rdma_transactions/submission-prefix.json',
            '--backend', restored/'runtime/bin'/flavor, '--output', fresh/flavor]))
    for label, command in jobs:
        with (fresh/(label+'.stdout')).open('w') as stdout, (fresh/(label+'.stderr')).open('w') as stderr:
            r = subprocess.run(list(map(str, command)), cwd=empty, env=environment, stdout=stdout, stderr=stderr, timeout=20)
        commands.append(dict(command=list(map(str, command)), cwd=str(empty), exit_code=r.returncode))
        save(dest/'commands.json', commands); assert r.returncode == 0, label
    spec = importlib.util.spec_from_file_location('prefix_oracle', source/'tests/integration/check_remote_transaction_workflow.py')
    oracle = importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
    for flavor, old in (('probe', 'prefix'), ('session', 'online')):
        result = read(fresh/flavor/'result.json'); expected = read(restored/'raw/edge-accept'/old/'result.json')
        oracle.audit(result, read(source/'examples/scaleout_rdma_transactions/submission-prefix.json'), 64)
        native = [Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
        assert len(native) == 8
        assert all(p.parent == restored/'runtime/lib' for p in native)
        assert {Path(p).name for p in result.pop('loaded_libraries')} == {Path(p).name for p in expected.pop('loaded_libraries')}
        assert result == expected, flavor
    assert all(sha(REPO/name.removeprefix('source/')) == row['sha256'] for name, row in manifest.items() if name.startswith('source/'))
    report = dict(passed=True, members_checked=len(manifest), member_bytes=sum(row['bytes'] for row in manifest.values()),
        archive_bytes=archive.stat().st_size, archive_sha256=sha(archive), public_commands=3, new_network_executions=2,
        new_source_root=True, empty_cwd=True, packaged_native_libraries=8, results_except_library_paths_equal=True,
        independent_byte_oracle=True, host_python_and_system_libraries_required=True,
        storage='tmpfs, volatile; no persistent backup', hardware_calibrated=False)
    save(root/'recovery.json', report); save(REPO/'fixtures/submission_prefix/recovery.json', report)
    print(json.dumps(report))

if __name__ == '__main__': main()
