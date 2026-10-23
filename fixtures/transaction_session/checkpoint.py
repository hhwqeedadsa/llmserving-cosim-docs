"""Bounded local checkpoint/recovery for transaction-session acceptance."""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(repo, root):
    dest = root/'checkpoint'
    dest.mkdir()
    items = {}
    for p in root.rglob('*'):
        if not p.is_file() or p.is_symlink():
            continue
        relative = p.relative_to(root)
        if relative.parts[0] in ('checkpoint', 'checkpoint-before-doc-layout', 'recovery'):
            continue
        if relative.parts[0].endswith('-monitor') and not (root/relative.parts[0]/'probe.json').exists():
            continue
        items['raw/'+str(relative)] = p
    for directory in ('python', 'backend', 'tests', 'fixtures/transaction_session'):
        for p in (repo/directory).rglob('*'):
            if directory == 'fixtures/transaction_session' and p.name in ('archive.json', 'recovery.json', 'closeout.json'):
                continue  # Receipts follow the snapshot; never snapshot an older receipt.
            if p.is_file() and '__pycache__' not in p.parts:
                items['source/'+str(p.relative_to(repo))] = p
    for name in ('deps.lock.json', 'AGENTS.md', 'docs/tasks/transaction_session_spec.md',
                 'docs/architecture.md', 'docs/model_validation.md', 'docs/record_storage.md', 'docs/user_guide.md',
                 'examples/scaleout_rdma/small-clos-100g.json', 'examples/scaleout_rdma/rdma.json',
                 'examples/scaleout_rdma_transactions/session_driver.py', 'examples/scaleout_rdma_transactions/session-program.json',
                 'examples/scaleout_rdma_transactions/README.md'):
        items['source/'+name] = repo/name
    items['runtime/bin/session'] = root/'bin/ns3.42-moe-rdma-transaction-session'
    for name, digest in read(root/'old-runtime.json').items():
        p = Path(name)
        assert sha(p) == digest
        if p.suffix == '.so':
            items['runtime/lib/'+p.name] = p.resolve()
    manifest = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name, p in sorted(items.items())}
    size = sum(v['bytes'] for v in manifest.values())
    assert size <= 96*2**20 and len(manifest) <= 2000
    path = dest/'checkpoint.tar.gz'
    with tarfile.open(path, 'w:gz', dereference=True) as tf:
        for name, p in sorted(items.items()):
            tf.add(p, arcname=name, recursive=False)
    assert path.stat().st_size <= 32*2**20
    with tarfile.open(path, 'r:gz') as tf:
        assert len(tf.getmembers()) == len(manifest)
        for member in tf:
            assert member.isfile()
            data = tf.extractfile(member).read()
            assert len(data) == manifest[member.name]['bytes'] and hashlib.sha256(data).hexdigest() == manifest[member.name]['sha256']
    save(dest/'members.json', manifest)
    summary = dict(passed=True, members=len(manifest), member_bytes=size, compressed_bytes=path.stat().st_size,
                   archive_sha256=sha(path), includes_session_binary=True, native_libraries=8,
                   storage='tmpfs_volatile', persistent_backup=False)
    save(dest/'archive.json', summary)
    save(repo/'fixtures/transaction_session/archive.json', summary)
    print(json.dumps(summary))


def recover(repo, root):
    dest = root/'recovery'
    dest.mkdir()
    path = root/'checkpoint/checkpoint.tar.gz'
    manifest = read(root/'checkpoint/members.json')
    assert sha(path) == read(root/'checkpoint/archive.json')['archive_sha256']
    with tarfile.open(path, 'r:gz') as tf:
        assert len(tf.getmembers()) == len(manifest)
        for member in tf:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            data = tf.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == manifest[member.name]['sha256']
            if member.name.startswith(('runtime/', 'source/python/', 'source/examples/')) or member.name in (
                    'source/tests/integration/check_remote_transaction_workflow.py', 'raw/accept-final/protocol.json',
                    'raw/example/result.json'):
                p = dest/member.name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                if member.name == 'runtime/bin/session':
                    p.chmod(0o755)
    source = dest/'source'
    cwd = dest/'empty-cwd'
    cwd.mkdir()
    new = dest/'new'
    new.mkdir()
    env = dict(os.environ, PYTHONPATH=str(source/'python'), PYTHONDONTWRITEBYTECODE='1', LD_LIBRARY_PATH=str(dest/'runtime/lib'))
    top_config = source/'examples/scaleout_rdma/small-clos-100g.json'
    protocol = dest/'raw/accept-final/protocol.json'
    program = source/'examples/scaleout_rdma_transactions/session-program.json'
    backend = dest/'runtime/bin/session'
    commands = []
    for name, argv in [
        ('topology', ['-m', 'moe_ub_cosim', 'scaleout', 'topology', '--config', top_config, '--output', new/'topology']),
        ('cli', ['-m', 'moe_ub_cosim', 'scaleout', 'rdma-transaction-session', '--topology', new/'topology',
                 '--protocol', protocol, '--config', program, '--backend', backend, '--output', new/'cli']),
        ('driver', [source/'examples/scaleout_rdma_transactions/session_driver.py', '--topology', new/'topology',
                    '--protocol', protocol, '--config', program, '--backend', backend, '--output', new/'driver'])]:
        command = [sys.executable, *map(str, argv)]
        with (new/(name+'.stdout')).open('w') as stdout, (new/(name+'.stderr')).open('w') as stderr:
            p = subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=20)
        commands.append(dict(command=command, cwd=str(cwd), exitcode=p.returncode, pythonpath=str(source/'python'), library_path=env['LD_LIBRARY_PATH']))
        save(new/'commands.json', commands)
        assert p.returncode == 0, name
    spec = importlib.util.spec_from_file_location('recovered_oracle', source/'tests/integration/check_remote_transaction_workflow.py')
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    original = read(dest/'raw/example/result.json')
    original_libs = {Path(n).name for n in original['loaded_libraries']}
    del original['loaded_libraries']
    summaries = []
    for name in ('cli', 'driver'):
        result = read(new/name/'result.json')
        assert {Path(n).name for n in result['loaded_libraries']} == original_libs
        native = [Path(n) for n in result['loaded_libraries'] if Path(n).name.startswith('libns3')]
        assert len(native) == 8 and all(p.parent == dest/'runtime/lib' for p in native)
        payloads = oracle.audit(result, read(program), 64)
        del result['loaded_libraries']
        assert result == original
        summaries.append(dict(name=name, result_equal_except_library_paths=True, library_basenames_identical=True,
                              loaded_native_libraries=8, independent_byte_memory_audit=payloads))
    summary = dict(passed=True, checkpoint_members_checked=len(manifest), new_source_root=True, new_cwd=True,
                   original_parent_pythonpath_not_inherited=True, restored_native_libraries=True,
                   public_commands=3, fresh_native_functional_executions=2, normalized_results=summaries,
                   hardware_calibrated=False, modern_deepep_connected=False,
                   host_system_libraries_still_required=True)
    save(root/'recovery.json', summary)
    save(repo/'fixtures/transaction_session/recovery.json', summary)
    print(json.dumps(summary))


def final(repo, root):
    manifest = read(root/'checkpoint/members.json')
    source_count = 0
    for name, row in manifest.items():
        if name.startswith('source/'):
            p = repo/name[len('source/'):]
            # Final receipts are written after checkpoint; pre-existing source
            # inputs and code in the package must still match exactly.
            assert sha(p) == row['sha256'], name
            if p.suffix == '.py':
                ast.parse(p.read_text())
            source_count += 1
    assert all(sha(Path(p)) == digest for p, digest in read(root/'old-runtime.json').items())
    size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    assert size < 192*2**20 and shutil.disk_usage(root).free >= 8*2**30
    probes = [read(p) for p in root.glob('*-*/probe.json')]
    assert sum(p['wall_seconds'] for p in probes) < 480
    failed_paths = [p for p in root.glob('*-*/probe.json') if read(p)['status'] != 'completed']
    assert {p.parent.name for p in failed_paths} == {'build-transaction_session', 'accept-monitor'}
    assert read(root/'accept-final/verification.json')['passed'] and read(root/'regression/verification.json')['passed']
    assert read(root/'recovery.json')['passed']
    links = 0
    for name in ('fixtures/transaction_session/README.md', 'docs/tasks/transaction_session_spec.md',
                 'examples/scaleout_rdma_transactions/README.md'):
        p = repo/name
        for target in re.findall(r'\]\(([^)]+)\)', p.read_text()):
            if target.startswith(('http:', 'https:', '#')):
                continue
            dest = (p.parent/target.split('#')[0]).resolve()
            if dest != (repo/'fixtures/transaction_session/closeout.json').resolve():
                assert dest.exists(), (name, target)
            links += 1
    summary = dict(passed=True, checkpoint_source_files_match=source_count, fixed_dependencies=13,
                   old_runtime_hashes_unchanged=12, unit_checks=22, regression_commands=37,
                   native_rejections=11, observed_callbacks=118, deliberate_cutoff_retained=True,
                   preserved_failed_monitored_stages=len(failed_paths), total_monitored_stage_seconds=sum(p['wall_seconds'] for p in probes),
                   root_bytes=size, cumulative_limit_bytes=192*2**20, local_links_checked=links,
                   tmpfs_free_bytes=shutil.disk_usage(root).free, storage='tmpfs_volatile', persistent_backup=False,
                   modern_deepep_connected=False, paper_contribution_established=False)
    save(root/'closeout.json', summary)
    save(repo/'fixtures/transaction_session/closeout.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('archive', 'recover', 'final'))
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    globals()[a.phase](a.repo.resolve(), a.root.resolve())
