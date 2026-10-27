"""Six predeclared public-CLI networks and copied-source offline recovery."""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import time

from fixtures.static_stream_validation.verify import load, save, sha, verify, compare, corruptions
from moe_ub_cosim.host_probe import disk_usage

LABELS = ('modern-full', 'modern-cached', 'transaction-full', 'transaction-cached',
          'modern-migrated', 'transaction-migrated')
IDENTITY_SHA = 'e0a6015eb54f6cd9f5ca77cc9c2ff22c9fe1389d52e58fc55ca5b58627d43ac6'
SCHEMA = 'static-stream-validation-checkpoint/1'


def identity_check(source, runtime, identity):
    assert len(identity['files']) == 10
    assert all(sha(runtime/n) == h for n, h in identity['files'].items())
    assert all(sha(source/n) == h for n, h in identity['sources'].items())


def source_check(source):
    manifest = load(source/'source-manifest.json')
    assert all(sha(source/n) == h for n, h in manifest.items())
    assert {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()} == set(manifest)|{'source-manifest.json'}


def prepare(repo, stage):
    runtime = repo/'runs/optimized_host_cost_20261027/stage/runtime'
    identity_path = runtime.parent/'runtime-identity.json'
    assert sha(identity_path) == IDENTITY_SHA
    identity = load(identity_path); identity_check(repo, runtime, identity)
    shutil.copytree(runtime, stage/'runtime'); shutil.copyfile(identity_path, stage/'runtime-identity.json')
    source = stage/'source'; source.mkdir()
    paths = {p for directory in ('python/moe_ub_cosim', 'fixtures') for p in (repo/directory).rglob('*.py')}
    paths.update(repo/n for n in identity['sources'])
    paths.update(repo/n for n in ('deps.lock.json', 'AGENTS.md', 'docs/development.md',
        'docs/tasks/static_stream_validation_spec.md', 'tests/unit/static_stream_validation_test.py'))
    for directory in ('examples/modern_deepep_native', 'examples/scaleout_rdma_transactions', 'examples/scaleout_rdma'):
        paths.update(p for p in (repo/directory).iterdir() if p.is_file())
    for path in paths:
        target = source/path.relative_to(repo); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    save(source/'source-manifest.json', {str(p.relative_to(source)): sha(p) for p in sorted(source.rglob('*')) if p.is_file()})
    source_check(source); identity_check(source, stage/'runtime', identity)
    shutil.copytree(stage/'runtime', stage/'migrated-runtime')
    inputs = stage/'inputs'; inputs.mkdir(); (stage/'empty-cwd').mkdir()
    for label, path in (
        ('modern-topology', 'examples/modern_deepep_native/topology.json'),
        ('transaction-topology', 'examples/scaleout_rdma/small-clos-100g.json'),
        ('protocol', 'examples/modern_deepep_native/rdma.json'),
        ('modern', 'examples/modern_deepep_native/input-expert-costs.json'),
        ('transaction', 'examples/scaleout_rdma_transactions/session-program.json')):
        shutil.copyfile(repo/path, inputs/(label+'.json'))
    save(stage/'preparation.json', dict(passed=True, runtime_identity_sha256=IDENTITY_SHA,
         inputs={p.name: sha(p) for p in inputs.iterdir()}, source_files=len(paths),
         gpu_execution=False, hardware_calibrated=False, new_builds=0, new_downloads=0))


def library_check(root, runtime, identity):
    paths = [Path(p) for p in load(root/'result.json')['loaded_libraries'] if Path(p).name.startswith('libns3.')]
    assert len(paths) == 8 and all(p.parent == runtime/'lib' for p in paths)
    assert all(sha(p) == identity['files']['lib/'+p.name] for p in paths)


def summary(stage):
    reports = {label: verify(stage/label, not label.endswith('-full')) for label in LABELS}
    comparisons = {}
    for kind in ('modern', 'transaction'):
        comparisons[kind+'-cached'] = compare(stage/(kind+'-full'), stage/(kind+'-cached'))
        comparisons[kind+'-migrated'] = compare(stage/(kind+'-cached'), stage/(kind+'-migrated'), migrated=True)
    rejected = {kind: corruptions(stage/(kind+'-cached')) for kind in ('modern', 'transaction')}
    source_check(stage/'source')
    identity = load(stage/'runtime-identity.json')
    identity_check(stage/'source', stage/'runtime', identity)
    identity_check(stage/'source', stage/'migrated-runtime', identity)
    return dict(passed=True, networks=6, reports=reports, comparisons=comparisons,
        corruptions_rejected=rejected, copied_source_hashes_verified=True,
        actual_runtime_identity_sha256=IDENTITY_SHA, performance_measured=False,
        hardware_calibrated=False, research_innovation_proven=False)


def admit(archive, digest):
    assert archive.stat().st_size < 64*2**20 and sha(archive) == digest
    blobs = {}; total = 0
    with tarfile.open(archive) as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert str(name) == member.name and '\\' not in member.name and member.name not in blobs
            assert len(blobs) < 4096 and member.size < 32*2**20
            total += member.size; assert total < 128*2**20
            raw = stream.extractfile(member).read(); assert len(raw) == member.size
            blobs[member.name] = raw, stat.S_IMODE(member.mode)
    raw, _ = blobs.pop('manifest.json'); manifest = json.loads(raw)
    assert manifest['schema_version'] == SCHEMA and set(blobs) == set(manifest['files'])
    for name, (raw, mode) in blobs.items():
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents)
        assert mode in (0o644, 0o755)
        import hashlib
        assert manifest['files'][name] == dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), mode=mode)
    return blobs


def archive_create(stage, archive):
    assert not archive.exists(); paths = {}
    for directory in (*LABELS, 'inputs', 'source', 'source-initial', 'source-checker-v1', 'runtime', 'migrated-runtime', 'topology-modern', 'topology-transaction'):
        for path in (stage/directory).rglob('*'):
            if path.is_file():
                assert not path.is_symlink(); paths[str(path.relative_to(stage))] = path
    for name in ('preparation.json', 'runtime-identity.json', 'verification.json', 'execution.json'):
        paths[name] = stage/name
    if (stage/'execution-initial-failure.json').exists():
        paths['execution-initial-failure.json'] = stage/'execution-initial-failure.json'
    if (stage/'execution-field-failure.json').exists():
        paths['execution-field-failure.json'] = stage/'execution-field-failure.json'
    # Include terminal monitors only; the outer driver monitor is still open.
    for probe in (stage/'monitor').glob('*/probe.json'):
        for path in probe.parent.iterdir():
            if path.is_file(): paths[str(path.relative_to(stage))] = path
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p), mode=0o755 if os.access(p, os.X_OK) else 0o644)
               for name, p in paths.items()}
    assert len(entries) < 4095 and sum(e['bytes'] for e in entries.values()) < 128*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(paths.items()):
            raw = path.read_bytes(); info = tarfile.TarInfo(name); info.size = len(raw); info.mode = entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o644; target.addfile(info, io.BytesIO(raw))
    digest = sha(archive); admit(archive, digest)
    save(stage/'checkpoint.json', dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(e['bytes'] for e in entries.values()),
        additional_networks_on_restore=0, same_host_abi_bound=True))


def restore(archive, digest, output):
    assert not output.exists(); blobs = admit(archive, digest); output.mkdir()
    for name, (raw, mode) in blobs.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(mode)
    del blobs
    empty = output/'empty-cwd'; empty.mkdir(); source = output/'source'
    env = os.environ|{'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    command = [sys.executable, '-P', '-B', '-m', 'fixtures.static_stream_validation.accept', 'verify',
               '--stage', str(output), '--output', str(output/'reverification.json')]
    p = subprocess.run(command, cwd=empty, env=env, capture_output=True, text=True, timeout=45)
    save(output/'restore-command.json', dict(command=command, cwd=str(empty), exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, p.stderr
    assert (output/'reverification.json').read_bytes() == (output/'verification.json').read_bytes()
    save(output/'recovery.json', dict(passed=True, copied_source=True, empty_cwd=True,
        archive_sha256=digest, member_hashes_checked=True, complete_verification_byte_identical=True,
        additional_networks=0, new_builds=0, new_downloads=0))


def run(repo, stage, *, resume_checker=False):
    assert stage.is_dir()
    if resume_checker:
        previous = load(stage/'execution.json')
        assert previous['closed'] and previous['state'] == 'failure_preserved_remaining_networks_closed'
        count = previous['network_attempts']; assert count in (1, 3)
        if count == 1:
            assert previous['records'][-1]['label'] == 'modern-full' and previous['records'][-1]['exit_code'] == 0
            preserved, source_name = 'execution-initial-failure.json', 'source-initial'
        else:
            assert previous['records'][-1]['label'] == 'transaction-full-verify' and previous['records'][-1]['exit_code'] == 1
            preserved, source_name = 'execution-field-failure.json', 'source-checker-v1'
        assert not (stage/preserved).exists()
        shutil.copyfile(stage/'execution.json', stage/preserved)
        # Preserve the original checker snapshot. Only the two fixture
        # checkers change; platform/runtime/input sources remain identical.
        source_check(stage/'source')
        (stage/'source').rename(stage/source_name)
        shutil.copytree(stage/source_name, stage/'source')
        for name in ('accept.py', 'verify.py'):
            shutil.copyfile(repo/'fixtures/static_stream_validation'/name, stage/'source/fixtures/static_stream_validation'/name)
        spec = 'docs/tasks/static_stream_validation_spec.md'
        shutil.copyfile(repo/spec, stage/'source'/spec)
        old = load(stage/'source/source-manifest.json')
        new = {n: sha(stage/'source'/n) for n in old}
        assert {n for n in old if old[n] != new[n]} == {
            'fixtures/static_stream_validation/accept.py', 'fixtures/static_stream_validation/verify.py', spec}
        save(stage/'source/source-manifest.json', new); source_check(stage/'source')
        ledger = dict(state='checker_corrected_no_network_repeated', closed=False,
                      network_attempts=count, records=previous['records'], initial_failure_preserved=True)
    else:
        assert not (stage/'execution.json').exists()
        ledger = dict(state='started', closed=False, network_attempts=0, records=[])
    begin = time.monotonic()
    def command(label, argv, *, network=False, source=None, cwd=None):
        if network:
            assert ledger['network_attempts'] < 6; ledger['network_attempts'] += 1
        probes = [load(p) for p in (stage/'monitor').glob('*/probe.json')]
        assert sum(p['wall_seconds'] for p in probes)+130 < 900
        assert max(disk_usage(stage).values())+2*2**20 < 512*2**20
        assert shutil.disk_usage(stage).free >= 8*2**30
        source = source or repo; cwd = cwd or repo
        env = os.environ|{'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
        row = dict(label=label, command=list(map(str, argv)), cwd=str(cwd), source=str(source), network=network, exit_code=None)
        ledger['records'].append(row); save(stage/'execution.json', ledger)
        wrapped = [sys.executable, '-P', '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', stage/'monitor'/label,
            '--rss-bytes', 2**30, '--address-bytes', 2**31, '--artifact-bytes', 512*2**20,
            '--artifact-root', stage, '--wall-seconds', 60, '--min-free-bytes', 8*2**30, '--', *argv]
        p = subprocess.run(list(map(str, wrapped)), cwd=cwd, env=env, timeout=65)
        row['exit_code'] = p.returncode; save(stage/'execution.json', ledger)
        probe = load(stage/'monitor'/label/'probe.json')
        assert p.returncode == 0 and probe['status'] == 'completed' and probe['child_exit_code'] == 0
        assert not probe['remaining_live_group_pids']
        print(label, 'passed', flush=True)
    cli = [sys.executable, '-P', '-B', '-m', 'moe_ub_cosim', 'scaleout']
    module = [sys.executable, '-P', '-B', '-m', 'fixtures.static_stream_validation.accept']
    try:
        if not resume_checker:
            command('prepare', [*module, 'prepare', '--repository', repo, '--stage', stage])
            # Do not rerun the unchanged successful dedicated unit check.
            initial = load(stage/'monitor/unit-initial/probe.json')
            assert initial['status'] == 'completed' and initial['child_exit_code'] == 0
            for pattern in ('scaleout_transactions_test.py', 'scaleout_streams_test.py',
                        'stream_nic_routes_test.py', 'transaction_session_alarms_test.py', 'modern_native_test.py',
                        'session_host_timing_test.py'):
                command('unit-'+pattern, [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests/unit', '-p', pattern])
            command('dependencies', ['bash', 'scripts/check_deps.sh'])
            for kind, entry in (('modern', 'modern-deepep-native'), ('transaction', 'rdma-transaction-session')):
                command('help-'+kind, [*cli, entry, '--help'])
                command('topology-'+kind, [*cli, 'topology', '--config', stage/'inputs'/(kind+'-topology.json'),
                                     '--output', stage/('topology-'+kind)])
        identity = load(stage/'runtime-identity.json')
        for label in LABELS:
            kind = label.split('-')[0]; migrated = label.endswith('-migrated'); cached = not label.endswith('-full')
            source = stage/'source' if migrated else repo
            runtime = stage/('migrated-runtime' if migrated else 'runtime')
            entry = 'modern-deepep-native' if kind == 'modern' else 'rdma-transaction-session'
            argv = [*cli, entry, '--topology', stage/('topology-'+kind), '--protocol', stage/'inputs/protocol.json',
                    '--config', stage/'inputs'/(kind+'.json'), '--backend', runtime/'run-transaction-session', '--output', stage/label]
            if kind == 'modern': argv.append('--callback-batches')
            if cached: argv.append('--reuse-static-validation')
            existing = {r['label'] for r in ledger['records'] if r['network'] and r['exit_code'] == 0}
            if label not in existing:
                command(label, argv, network=True, source=source, cwd=stage/'empty-cwd')
            library_check(stage/label, runtime, identity)
            verifier = [sys.executable, '-P', '-B', '-m', 'fixtures.static_stream_validation.verify',
                        '--run', stage/label, '--output', stage/(label+'-verification.json')]
            if cached: verifier += ['--cached', '--corruptions']
            if not (stage/(label+'-verification.json')).exists():
                monitor_label = label+'-verify'
                if (stage/'monitor'/monitor_label).exists(): monitor_label += '-corrected'
                command(monitor_label, verifier, source=source, cwd=stage/'empty-cwd')
            if cached:
                comparison = compare(stage/(kind+'-cached' if migrated else kind+'-full'), stage/label, migrated=migrated)
                save(stage/(label+'-comparison.json'), comparison)
        save(stage/'verification.json', summary(stage))
        ledger.update(state='networks_completed', networks_closed=True); save(stage/'execution.json', ledger)
        command('archive', [*module, 'archive', '--stage', stage, '--archive', stage/'checkpoint.tar.gz'])
        command('restore', [*module, 'restore', '--archive', stage/'checkpoint.tar.gz', '--digest', sha(stage/'checkpoint.tar.gz'),
                            '--output', stage/'restored'])
        ledger.update(state='completed', closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_remaining_networks_closed', closed=True, error=repr(error)); raise
    finally:
        ledger['driver_wall_seconds'] = time.monotonic()-begin; save(stage/'execution.json', ledger)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'run', 'continue-checker', 'verify', 'archive', 'restore'))
    for name in ('repository', 'stage', 'output', 'archive'): p.add_argument('--'+name, type=Path)
    p.add_argument('--digest'); a = p.parse_args()
    if a.action in ('prepare', 'run'): globals()[a.action](a.repository.resolve(), a.stage.resolve())
    elif a.action == 'continue-checker': run(a.repository.resolve(), a.stage.resolve(), resume_checker=True)
    elif a.action == 'verify': save(a.output, summary(a.stage.resolve()))
    elif a.action == 'archive': archive_create(a.stage.resolve(), a.archive.resolve())
    else: restore(a.archive.resolve(), a.digest, a.output.resolve())
