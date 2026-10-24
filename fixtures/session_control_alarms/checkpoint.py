"""Bounded, hashed source/runtime/evidence archive and prebuilt public replay.

No dependency installation or third-party rebuild. Run each subcommand under
host_probe with the limits in session_control_alarms_spec.md.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
PLAIN_LIMIT = 64 * 2**20
ARCHIVE_LIMIT = 32 * 2**20
EVIDENCE_DIRS = (
    'functional', 'functional-monitor', 'session-regression',
    'session-regression-monitor', 'prefix-regression', 'prefix-regression-monitor',
    'public-example', 'public-example-monitor', 'public-topology', 'topology-monitor',
    'unit-monitor', 'unit-admission-monitor', 'audit-monitor',
    'audit-revised-monitor', 'build-compile', 'build-link',
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')


def safe_name(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts and
            str(path) == name and path.parts[0] in ('source', 'evidence', 'runtime'),
            'unsafe archive member: ' + name)
    return path


def archive(a):
    stage, out = a.stage.resolve(), a.output.resolve()
    require(not out.exists(), 'checkpoint output already exists')
    entries = {}

    def add(path, name):
        safe_name(name)
        require(path.is_file() and not path.is_symlink() and name not in entries,
                'missing, linked or duplicate input: ' + str(path))
        entries[name] = path

    def tree(folder, prefix):
        require(folder.is_dir(), 'missing source/evidence directory: ' + str(folder))
        for path in sorted(folder.rglob('*')):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            require(not path.is_symlink(), 'linked input: ' + str(path))
            if path.is_file():
                add(path, prefix + '/' + path.relative_to(folder).as_posix())

    for folder in ('python/moe_ub_cosim', 'backend',
                   'examples/scaleout_rdma', 'examples/scaleout_rdma_transactions',
                   'fixtures/session_control_alarms'):
        tree(ROOT / folder, 'source/' + folder)
    for rel in ('AGENTS.md', 'README.md', 'deps.lock.json', 'docs/development.md',
                'docs/architecture.md', 'docs/model_validation.md', 'docs/record_storage.md',
                'docs/tasks/session_control_alarms_spec.md',
                'docs/reviews/控制定时器与GPU队列分离_20261024.md',
                'tests/unit/transaction_session_alarms_test.py',
                'tests/integration/check_session_control_alarms.py',
                'tests/integration/audit_session_control_alarms.py',
                'tests/integration/check_transaction_session_workflow.py',
                'tests/integration/check_submission_prefix_workflow.py'):
        add(ROOT / rel, 'source/' + rel)
    for folder in EVIDENCE_DIRS:
        tree(stage / folder, 'evidence/' + folder)
    for rel in ('backend.json', 'input-identity.json', 'build-records.json',
                'independent-audit.json', 'audit-before-empty-packet-fix.py',
                'audit-failure-note.json'):
        add(stage / rel, 'evidence/' + rel)
    add(stage / 'bin/ns3.42-moe-rdma-transaction-session',
        'runtime/bin/ns3.42-moe-rdma-transaction-session')
    libraries = sorted(a.libraries.resolve().glob('libns3*.so'))
    require(len(libraries) == 8, 'exactly eight pinned ns-3 libraries required')
    protected = json.loads((stage / 'input-identity.json').read_text())['protected']
    for path in libraries:
        require(sha(path) == protected[str(path)], 'library changed since build')
        add(path, 'runtime/lib/' + path.name)
    manifest = {
        name: dict(bytes=path.stat().st_size, sha256=sha(path),
                   mode=path.stat().st_mode & 0o777)
        for name, path in sorted(entries.items())
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    plain = sum(row['bytes'] for row in manifest.values()) + len(raw)
    require(plain < PLAIN_LIMIT, 'uncompressed archive budget exceeded')
    out.mkdir()
    bundle = out / 'checkpoint.tar.gz'
    with tarfile.open(bundle, 'w:gz', compresslevel=6) as tf:
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(raw))
        for name, path in sorted(entries.items()):
            tf.add(path, arcname=name, recursive=False)
    require(bundle.stat().st_size < ARCHIVE_LIMIT, 'compressed archive budget exceeded')
    with tarfile.open(bundle, 'r:gz') as tf:
        names = set()
        for member in tf:
            require(member.name not in names and member.isfile(), 'duplicate/nonfile member')
            names.add(member.name)
            if member.name == 'manifest.json':
                require(tf.extractfile(member).read() == raw, 'manifest changed')
                continue
            row = manifest[member.name]
            require(member.size == row['bytes'] and member.mode == row['mode'],
                    'archive member metadata changed')
            with tf.extractfile(member) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'],
                        'archive member content changed')
        require(names == set(manifest) | {'manifest.json'}, 'missing archive member')
    require(all(sha(path) == manifest[name]['sha256'] for name, path in entries.items()),
            'input changed while archiving')
    (out / 'manifest.json').write_bytes(raw)
    receipt = dict(passed=True, sha256=sha(bundle), archive_bytes=bundle.stat().st_size,
                   plain_bytes=plain, members=len(manifest) + 1,
                   source_members=sum(n.startswith('source/') for n in manifest),
                   ns3_libraries=8, all_members_verified=True,
                   scope='source and prebuilt runtime replay; host loader and system ABI required')
    write_json(out / 'archive.json', receipt)
    print(json.dumps(receipt))


def recover(a):
    bundle, dest = a.archive.resolve(), a.destination.resolve()
    receipt = json.loads(a.receipt.read_text())
    require(bundle.stat().st_size == receipt['archive_bytes'] < ARCHIVE_LIMIT and
            sha(bundle) == receipt['sha256'], 'archive identity/size mismatch')
    require(not dest.exists(), 'recovery destination already exists')
    with tarfile.open(bundle, 'r:gz') as tf:
        members = tf.getmembers()
        require(len(members) == receipt['members'] and
                len({m.name for m in members}) == len(members) and
                all(m.isfile() for m in members), 'duplicate/nonfile/missing member')
        require(sum(m.size for m in members) == receipt['plain_bytes'] < PLAIN_LIMIT,
                'restore size budget exceeded')
        meta = tf.getmember('manifest.json')
        require(meta.size < 4 * 2**20, 'oversize member manifest')
        manifest = json.load(tf.extractfile(meta))
        require(set(manifest) | {'manifest.json'} == {m.name for m in members},
                'member list differs from manifest')
        for name in manifest:
            safe_name(name)
        # Destination is empty; symlink/hardlink archive members are forbidden.
        dest.mkdir()
        for member in members:
            target = dest / member.name
            if member.name != 'manifest.json':
                row = manifest[member.name]
                require(member.size == row['bytes'] and member.mode == row['mode'] and
                        0 <= member.mode <= 0o777, 'member metadata mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(member.mode)
            if member.name != 'manifest.json':
                require(sha(target) == row['sha256'], 'restored content mismatch')
    require(all(sha(dest / name) == row['sha256'] for name, row in manifest.items()),
            'restored member changed')
    write_json(dest / 'restore.json', dict(passed=True, members=len(members),
                                          all_members_sha256_verified=True))
    print(json.dumps(dict(restored=str(dest), members=len(members))))


def replay(a):
    restored, out = a.restored.resolve(), a.output.resolve()
    source, evidence = restored / 'source', restored / 'evidence'
    lib = restored / 'runtime/lib'
    require(not out.exists(), 'replay output already exists')
    out.mkdir()
    cwd = out / 'empty-cwd'
    cwd.mkdir()
    env = dict(os.environ, PYTHONPATH=str(source / 'python'),
               PYTHONDONTWRITEBYTECODE='1', LD_LIBRARY_PATH=str(lib))
    env.pop('LD_PRELOAD', None)
    records = []

    def run(command, label):
        started = time.monotonic()
        with (out / (label + '.stdout')).open('xb') as stdout, \
                (out / (label + '.stderr')).open('xb') as stderr:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stdout,
                                    stderr=stderr, timeout=40)
        records.append(dict(command=command, cwd=str(cwd), exit_code=result.returncode,
                            wall_seconds=time.monotonic()-started,
                            pythonpath=env['PYTHONPATH'], ld_library_path=env['LD_LIBRARY_PATH']))
        write_json(out / (label + '.command.json'), records[-1])
        require(result.returncode == 0, 'recovered command failed: ' + label)

    py = sys.executable
    run([py, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'topology', '--config',
         str(source / 'examples/scaleout_rdma/small-clos-100g.json'), '--output',
         str(out / 'topology')], 'topology')
    require(sha(out / 'topology/topology.json') == sha(evidence / 'public-topology/topology.json'),
            'restored public topology changed')
    run([py, '-B', str(source / 'examples/scaleout_rdma_transactions/control_alarm_driver.py'),
         '--topology', str(out / 'topology'), '--protocol', str(source / 'examples/scaleout_rdma/rdma.json'),
         '--config', str(source / 'examples/scaleout_rdma_transactions/control-alarm.json'),
         '--backend', str(restored / 'runtime/bin/ns3.42-moe-rdma-transaction-session'),
         '--output', str(out / 'public-example')], 'public-driver')
    actual = json.loads((out / 'public-example/result.json').read_text())
    expected = json.loads((evidence / 'public-example/result.json').read_text())
    loaded = [Path(n) for n in actual['loaded_libraries'] if Path(n).name.startswith('libns3')]
    require(len(loaded) == 8 and {n.resolve() for n in loaded} ==
            {n.resolve() for n in lib.glob('libns3*.so')}, 'ns-3 libraries escaped restored runtime')
    for result in (actual, expected):
        result['loaded_libraries'] = [Path(n).name if Path(n).name.startswith('libns3') else n
                                      for n in result['loaded_libraries']]
    require(actual == expected, 'full public result changed beyond ns-3 library directories')
    require((out / 'public-example/observed-summary.json').read_bytes() ==
            (evidence / 'public-example/observed-summary.json').read_bytes(), 'public summary changed')
    run([py, '-B', str(source / 'tests/integration/audit_session_control_alarms.py'),
         '--functional', str(evidence / 'functional'), '--output', str(out / 'independent-audit.json')],
        'offline-audit')
    require((out / 'independent-audit.json').read_bytes() ==
            (evidence / 'independent-audit.json').read_bytes(), 'offline audit bytes changed')
    manifest = json.loads((restored / 'manifest.json').read_text())
    require(all(sha(restored / name) == row['sha256'] for name, row in manifest.items()),
            'archived member changed during replay')
    report = dict(passed=True, network_replays=1, offline_audit_recomputations=1,
                  complete_result_equal_except_ns3_library_directories=True,
                  public_summary_byte_equal=True, offline_audit_byte_equal=True,
                  all_members_hash_unchanged=True, packaged_ns3_libraries=8,
                  source=str(source), cwd=str(cwd),
                  scope='prebuilt runtime replay; host Linux/Python/loader/glibc/libstdc++ still required')
    write_json(out / 'recovery.json', report)
    print(json.dumps(report))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('archive')
    pack.add_argument('--stage', type=Path, required=True)
    pack.add_argument('--libraries', type=Path, required=True)
    pack.add_argument('--output', type=Path, required=True)
    restore = commands.add_parser('recover')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--receipt', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    run = commands.add_parser('replay')
    run.add_argument('--restored', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    dict(archive=archive, recover=recover, replay=replay)[a.command](a)


if __name__ == '__main__':
    main()
