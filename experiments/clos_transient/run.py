"""Execute one frozen 128-card condition through public CLI, once only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from prepare import ROOT, HERE, load, save, sha


def run(root, backend_receipt, protoc, case):
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise ValueError('commit the registered method before network execution')
    prepared = root / 'prepared'
    frozen = load(prepared / 'preparation.json')
    if frozen['gpu_endpoints'] != 128 or frozen['ep_sizes'] != [64, 64]:
        raise ValueError('registered topology/deployment differs')
    for name, digest in frozen['input_hashes'].items():
        if sha(prepared / name) != digest:
            raise ValueError('frozen input changed: ' + name)
    for name, digest in frozen['method_hashes'].items():
        if sha(HERE / name) != digest:
            raise ValueError('frozen method changed: ' + name)
    binding = load(backend_receipt)
    backend = Path(binding['service'])
    if sha(backend) != binding['service_sha256']:
        raise ValueError('backend differs from declared receipt')
    for name, digest in binding['libraries'].items():
        if sha(Path(name)) != digest:
            raise ValueError('fixed ns-3 library changed: ' + name)
    for name, digest in binding['source_hashes'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('backend source changed: ' + name)
    attempts = root / 'attempts'
    attempts.mkdir(exist_ok=True)
    record = attempts / (case + '.json')
    if record.exists() or (root / case).exists():
        raise ValueError('condition already attempted; no automatic retry')
    if case == 'local' and load(attempts / 'normal.json').get('status') != 'verified':
        raise ValueError('normal condition not successfully verified')
    if shutil.disk_usage(root).free < 8 * 2**30:
        raise ValueError('free disk reserve exhausted')
    if sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) >= 512 * 2**20:
        raise ValueError('cumulative artifact budget exhausted')
    if sum(load(p)['wall_seconds'] for p in (root / 'monitors').glob('*/probe.json')) >= 900:
        raise ValueError('cumulative monitored time exhausted')
    prefix = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    command = prefix + ['rdma-run', '--config', str(prepared / 'service.json'),
        '--protocol', str(prepared / (case + '-rdma.json')), '--topology', str(prepared / 'topology'),
        '--backend', str(backend), '--protoc', str(protoc), '--output', str(root / case)]
    entry = dict(status='started', case=case, command=command, cwd=str(ROOT),
        backend_receipt_sha256=sha(backend_receipt), backend_sha256=sha(backend),
        protoc_sha256=sha(protoc), preparation_sha256=sha(prepared / 'preparation.json'),
        execution_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
    save(record, entry)
    start = time.monotonic()
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'python'), PYTHONDONTWRITEBYTECODE='1')
    try:
        with (attempts / (case + '.stdout')).open('x') as stdout, (attempts / (case + '.stderr')).open('x') as stderr:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr, timeout=90)
        entry['exit_code'] = completed.returncode
        if completed.returncode:
            raise RuntimeError('native condition failed; stop subsequent network')
        graphs = list((root / case / 'graphs').glob('*/wave.json'))
        if len(graphs) != 4:
            raise ValueError('missing actual task graph')
        for path in graphs:
            if load(path) != load(prepared / 'planned' / (path.parent.name + '.json')):
                raise ValueError('actual task graph differs from frozen plan')
        checks = ['rdma-check'] + (['data-loss-check'] if case == 'local' else [])
        for check in checks:
            with (attempts / (case + '-' + check + '.json')).open('x') as out:
                subprocess.run(prefix + [check, '--run', str(root / case)], cwd=ROOT,
                    env=env, stdout=out, check=True, timeout=30)
        entry.update(status='verified', graphs_verified=True, public_checks=checks)
    except BaseException as error:
        entry.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        entry['wall_seconds'] = time.monotonic() - start
        record.write_text(json.dumps(entry, indent=2) + '\n')
    print(json.dumps(entry))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'backend-receipt', 'protoc'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--case', choices=('normal', 'local'), required=True)
    args = parser.parse_args()
    run(args.root.resolve(), args.backend_receipt.resolve(), args.protoc.resolve(), args.case)
