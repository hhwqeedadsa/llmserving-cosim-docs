"""One preregistered generic dependency-frontend network, public CLI only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p): return json.loads(p.read_bytes())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--name', choices=('baseline', 'bandwidth', 'propagation'), required=True)
    p.add_argument('--repository', type=Path, required=True)
    a = p.parse_args(); stage = a.stage.resolve(); repo = a.repository.resolve()
    identity = load(stage/'runtime-identity.json')
    assert all(hashlib.sha256((stage/'runtime'/n).read_bytes()).hexdigest() == d for n, d in identity['files'].items())
    assert all(hashlib.sha256((repo/n).read_bytes()).hexdigest() == d for n, d in identity['sources'].items())
    assert hashlib.sha256((stage/'program.json').read_bytes()).hexdigest() == load(stage/'source.json')['program_sha256']
    ledger = load(stage/'execution.json'); assert not ledger['closed'] and ledger['network_executions'] < 3
    assert a.name not in {r['name'] for r in ledger['records']}
    if a.name != 'baseline': assert load(stage/'baseline-equivalence.json')['equivalent']
    assert sum(load(p)['wall_seconds'] for p in stage.glob('*-monitor/probe.json'))+65 < 900
    assert sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())+96*2**20 < 512*2**20
    empty = stage/'empty-cwd'; empty.mkdir(exist_ok=True)
    ref = stage/'reference'/a.name
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-transaction-session',
           '--topology', ref, '--protocol', ref/'protocol.json', '--config', stage/'program.json',
           '--backend', stage/'runtime/run-transaction-session', '--output', stage/a.name]
    command = list(map(str, cli))
    row = dict(name=a.name, command=command, cwd=str(empty), state='started')
    ledger['network_executions'] += 1; ledger['records'].append(row); save(stage/'execution.json', ledger)
    env = os.environ | {'PYTHONPATH':str(repo/'python')+':'+str(repo), 'PYTHONDONTWRITEBYTECODE':'1'}
    monitor = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', stage/(a.name+'-monitor'),
               '--rss-bytes', str(2**30), '--address-bytes', str(2**31), '--artifact-bytes', str(192*2**20),
               '--artifact-root', stage, '--wall-seconds', '60', '--min-free-bytes', str(8*2**30), '--', *command]
    done = subprocess.run(list(map(str, monitor)), cwd=empty, env=env, timeout=65)
    row.update(state='terminal', exit_code=done.returncode); save(stage/'execution.json', ledger)
    assert done.returncode == 0, 'preserve failure and stop, without raising limits or rerunning'
    print(a.name, 'network complete')


if __name__ == '__main__': main()
