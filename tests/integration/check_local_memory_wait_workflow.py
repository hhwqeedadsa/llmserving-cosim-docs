"""Public native workflow: actual received values gate two application rounds."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from check_remote_transaction_workflow import audit

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output.resolve(); backend = args.backend.resolve()
    assert not out.exists() and backend.is_file() and shutil.disk_usage(out.parent).free >= 8*2**30+32*2**20
    out.mkdir(); start = time.monotonic(); commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    for source, name in [('examples/scaleout_rdma/small-clos-100g.json', 'topology-input.json'),
                         ('examples/scaleout_rdma/rdma.json', 'protocol.json'),
                         ('examples/scaleout_rdma_transactions/local-waits.json', 'work.json')]:
        shutil.copy2(ROOT/source, out/name)

    def cli(name, arguments, expected=0):
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *arguments]
        remaining = 55-(time.monotonic()-start); assert remaining > 0
        began = time.monotonic()
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            run = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=remaining)
        commands.append(dict(argv=list(map(str, command)), cwd=str(out), exitcode=run.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-began))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert run.returncode == expected, name

    cli('topology', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    config = json.loads((out/'work.json').read_text()); protocol = json.loads((out/'protocol.json').read_text())

    def run(name, work, expected=0):
        file = name+'-input.json'; (out/file).write_text(json.dumps(work, indent=2)+'\n')
        cli(name, ['rdma-transaction-probe', '--topology', 'topology', '--protocol', 'protocol.json',
                   '--config', file, '--backend', str(backend), '--output', name], expected)
        if (out/name/'result.json').exists():
            usage = json.loads((out/name/'execution/probe.json').read_text())
            assert usage['status'] == ('completed' if expected == 0 else 'child_failed')
            return json.loads((out/name/'result.json').read_text())

    normal = run('normal', config); normal_audit = audit(normal, config, protocol['payload_bytes'])
    tx = normal['transactions']; rows = {r['id']:r for r in tx['operations']}
    local = {e['operation']:e for e in tx['local_events']}
    assert set(local) == {3, 5, 7, 8} and not normal['pending_operations']
    for consume, producers in [(3, (0, 1, 2)), (8, (5, 6, 7))]:
        assert local[consume]['time_ns'] == max(rows[i]['applied_ns'] for i in producers)
        assert local[consume]['conditions'][0]['observed_value'] == 2
        assert local[consume]['conditions'][0]['reset_to'] == 0
    # The consumer can act on actual target values while a producer is still
    # awaiting source completion. It never uses that producer's global applied
    # fact as an input dependency.
    assert rows[3]['published_ns'] < max(rows[i]['completed_ns'] for i in (1, 2))
    assert all(d['event'] != 'applied' for op in config['operations'] for d in op['after'])
    assert all(next(c for c in config['connections'] if c['id'] == config['operations'][d['operation']]['connection'])['src'] ==
               next(c for c in config['connections'] if c['id'] == op['connection'])['src']
               for op in config['operations'] for d in op['after'])
    assert rows[5]['ready_ns'] == rows[3]['applied_ns'] and rows[7]['ready_ns'] == rows[4]['applied_ns']
    assert normal_audit['final_atomic_value'] == 0
    repeated = run('repeat', config)
    assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    ge = deepcopy(config); ge['operations'][1]['value'] = 3
    ge['operations'][3]['local_wait'][0]['compare'] = 'ge'
    greater = run('greater', ge); ge_audit = audit(greater, ge, protocol['payload_bytes'])
    observed = next(e for e in greater['transactions']['local_events'] if e['operation'] == 3)
    assert observed['conditions'][0]['observed_value'] == 4

    failures = {}
    for name, field, value, expected_count, matches in [
            ('missing-increment', 'value', 0, 1, [False, True]),
            ('wrong-payload', 'hex', (22).to_bytes(8, 'little').hex(), 2, [True, False])]:
        bad = deepcopy(config); bad['operations'][2 if field == 'value' else 0][field] = value
        failed = run(name, bad, 1); assert not failed['passed']
        assert failed['completed_operations'] == 3 and len(failed['pending_operations']) == 7
        assert not failed['transactions']['local_events']
        pending = next(r for r in failed['pending_operations'] if r['id'] == 3)
        assert [w['matched'] for w in pending['local_wait']] == matches
        window = next(w for w in failed['transactions']['windows'] if w['owner'] == 4)
        assert int.from_bytes(bytes.fromhex(window['hex'])[:8], 'little') == expected_count
        assert {r['id'] for r in failed['transactions']['operations']} == {0, 1, 2}
        assert all(p[1] in (0, 1, 2, 3) for p in failed['transactions']['transport']['source_packets'])
        failures[name] = dict(completed=3, pending=7, actual_count=expected_count,
                              conjunction=matches, no_partial_reset=True, no_future_packets=True)
    invalid = deepcopy(config); invalid['operations'][3]['local_wait'][0]['key'] = 8
    assert run('foreign-window', invalid, 1) is None and not (out/'foreign-window').exists()
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for d in op['after']: d['operation'] += 100
    renamed_result = run('renamed', renamed); assert audit(renamed_result, renamed, protocol['payload_bytes']) == normal_audit
    for key in ('source_packets', 'source_payload_packets'):
        assert tx['transport'][key] == renamed_result['transactions']['transport'][key]
    paths = ['backend/rdma/remote_memory.hh', 'backend/rdma/remote_transactions.hh',
             'backend/rdma/remote_transactions.cc', 'backend/rdma/transaction_probe.cc',
             'python/moe_ub_cosim/scaleout_transactions.py',
             'examples/scaleout_rdma_transactions/local-waits.json',
             'tests/integration/check_local_memory_wait_workflow.py',
             'tests/integration/check_remote_transaction_workflow.py']
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total <= 32*2**20 and time.monotonic()-start < 60
    result = dict(passed=True, public_commands=len(commands), normal=normal_audit, greater=ge_audit,
                  failure_probes=failures, expected_rejections=3, local_conditions_checked=6,
                  repeat_byte_identical=True, id_renaming_wire_invariant=True, shared_clock=True,
                  modern_deepep_executed=False, hardware_calibrated=False,
                  source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
                  output_bytes=total, wall_seconds=time.monotonic()-start)
    (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__': main()
