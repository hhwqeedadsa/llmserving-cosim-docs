"""Actual native packet checks for captured, source-owned queue prefixes."""
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
                         ('examples/scaleout_rdma_transactions/queue-fences.json', 'work.json')]:
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
        if expected:
            assert not (out/name).exists(); return None
        usage = json.loads((out/name/'execution/probe.json').read_text())
        assert usage['status'] == 'completed'
        return json.loads((out/name/'result.json').read_text())

    normal = run('normal', config); normal_audit = audit(normal, config, protocol['payload_bytes'])
    rows = {r['id']:r for r in normal['transactions']['operations']}
    fences = {f['operation']:f for f in normal['transactions']['queue_fences']}
    assert set(fences) == {2, 5} and fences[2]['captured_ns'] == 0
    assert [t['covered_operations'] for t in fences[2]['targets']] == [[0], [1]]
    assert [t['covered_operations'] for t in fences[5]['targets']] == [[0, 3], [1, 4]]
    assert rows[3]['ready_ns'] == rows[3]['published_ns'] == 0
    assert rows[2]['published_ns'] == max(rows[0]['completed_ns'], rows[1]['completed_ns']) < rows[3]['completed_ns']
    assert rows[5]['published_ns'] == max(rows[2]['completed_ns'], rows[3]['completed_ns'], rows[4]['completed_ns'])
    assert not normal['pending_operations']

    # Reserve the tail before capturing the first fence, while depth 1 prevents
    # publishing it. Capture must include reserved work, not just transmitted work.
    reserved = deepcopy(config)
    for c in reserved['connections']: c['queue_depth'] = 1
    reserved['operations'] = [reserved['operations'][i] for i in (0, 3, 1, 2, 4, 5)]
    tail = run('reserved-tail', reserved); tail_audit = audit(tail, reserved, protocol['payload_bytes'])
    tail_rows = {r['id']:r for r in tail['transactions']['operations']}
    tail_fences = {f['operation']:f for f in tail['transactions']['queue_fences']}
    assert [t['covered_operations'] for t in tail_fences[2]['targets']] == [[0, 3], [1]]
    assert tail_rows[3]['ready_ns'] == tail_fences[2]['captured_ns'] == 0 < tail_rows[3]['published_ns']
    assert tail_rows[3]['published_ns'] == tail_rows[0]['completed_ns']
    assert tail_rows[2]['published_ns'] == max(tail_rows[i]['completed_ns'] for i in (0, 1, 3))

    # An empty snapshot does not accidentally wait for later submissions. This
    # intentionally lacks a producer join; it is not a valid data-ready barrier.
    empty = deepcopy(config); empty['operations'] = [empty['operations'][i] for i in (2, 0, 1, 3, 4, 5)]
    empty_result = run('empty', empty); empty_audit = audit(empty_result, empty, protocol['payload_bytes'])
    empty_fence = next(f for f in empty_result['transactions']['queue_fences'] if f['operation'] == 2)
    assert empty_fence['captured_ns'] == empty_fence['completed_ns'] == 0
    assert [t['target'] for t in empty_fence['targets']] == [0, 0]
    run('repeat', config)
    assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for d in op['after']: d['operation'] += 100
    renamed_result = run('renamed', renamed)
    assert audit(renamed_result, renamed, protocol['payload_bytes']) == normal_audit
    for key in ('source_packets', 'source_payload_packets'):
        assert normal['transactions']['transport'][key] == renamed_result['transactions']['transport'][key]
    foreign = deepcopy(config); foreign['connections'][1]['src'] = 1
    run('foreign-owner', foreign, 1)
    duplicate = deepcopy(config); duplicate['operations'][2]['queue_fence'] = [0, 0]
    run('duplicate', duplicate, 1)
    paths = ['backend/rdma/remote_transactions.hh', 'backend/rdma/remote_transactions.cc',
             'backend/rdma/transaction_probe.cc', 'backend/rdma/gin_work_queue.hh',
             'python/moe_ub_cosim/scaleout_transactions.py',
             'examples/scaleout_rdma_transactions/queue-fences.json',
             'tests/integration/check_remote_transaction_workflow.py',
             'tests/integration/check_queue_fence_workflow.py']
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total <= 32*2**20 and time.monotonic()-start < 60
    result = dict(passed=True, public_commands=len(commands), expected_rejections=2,
                  normal=normal_audit, reserved_tail=tail_audit, empty_snapshot=empty_audit,
                  snapshot_excludes_future_work=True, snapshot_includes_unpublished_reserved_work=True,
                  empty_snapshot_does_not_wait_for_future_work=True,
                  first_fence_completed_ns=fences[2]['completed_ns'],
                  uncovered_tail_completed_ns=rows[3]['completed_ns'],
                  repeat_byte_identical=True, id_renaming_wire_invariant=True,
                  same_network_clock=True, modern_GIN_flush_validated=False, hardware_calibrated=False,
                  source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
                  output_bytes=total, wall_seconds=time.monotonic()-start)
    (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__': main()
