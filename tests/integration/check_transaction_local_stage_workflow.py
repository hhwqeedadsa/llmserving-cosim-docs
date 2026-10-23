"""Public mixed-transaction workflow; independent packet, memory and FIFO checks.

Declared local durations and all payloads are artificial functional inputs.
The shared audit decodes actual MRT1 bytes and replays local consumption too.
"""
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


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def endpoint_audit(result, config):
    """Match each carrier frame to endpoint RX and native cumulative ACKs."""
    capture = result['packet_capture']
    assert capture['complete']
    packets = [dict(zip(capture['columns'], p)) for p in capture['events']]
    net = result['transactions']['transport']
    connections = {c['id']: c for c in config['connections']}
    source = net['source_packets']
    reused_prefixes = {}
    for work in net['work']:
        connection = connections[work['stream']//2]
        reverse = work['stream']%2
        src, dst = (connection['dst'], connection['src']) if reverse else (connection['src'], connection['dst'])
        selected = [p for p in source if p[1] == work['stream'] and work['begin'] <= p[5] < work['end']]
        assert selected
        arrivals = []
        for p in selected:
            rx = [q for q in packets if q['event'] == 1 and q['node'] == dst and q['packet_uid'] == p[7]]
            assert len(rx) == 1 and rx[0]['sequence'] == p[5]
            arrivals.append(rx[0]['time_ns'])
        accepted = max(arrivals)
        # This capture lacks the UDP/ACK port. For these non-overlapping tiny
        # frames identify the first endpoint-generated exact-prefix ACK and
        # follow its packet UID to the source. Not a general multiplexed-flow
        # decoder; require an unambiguous earliest candidate in this fixture.
        candidates = []
        for ack in packets:
            if not (ack['event'] == 0 and ack['node'] == dst and ack['ip_protocol'] == 252
                    and ack['sequence'] == work['end'] and ack['time_ns'] >= accepted): continue
            received = [p for p in packets if p['event'] == 1 and p['node'] == src
                        and p['packet_uid'] == ack['packet_uid']]
            if received: candidates.append((ack['time_ns'], received))
        assert candidates
        first = min(t for t, _ in candidates)
        chosen = [rx for t, rx in candidates if t == first]
        assert len(chosen) == len(chosen[0]) == 1
        assert work['accepted_ns'] == accepted and work['acked_ns'] == chosen[0][0]['time_ns']
        reused_prefixes.setdefault((src, dst, work['end']), []).append(
            (min(p[0] for p in selected), chosen[0][0]['time_ns']))
    for observations in reused_prefixes.values():
        ordered = sorted(observations)
        assert all(previous[1] < following[0] for previous, following in zip(ordered, ordered[1:]))
    return len(net['work'])


def local_audit(result, config):
    specs = {op['id']: op for op in config['operations']}
    remote = {r['id']: r for r in result['transactions']['operations']}
    local = {r['id']: r for r in result['local_work']}
    conditions = {r['operation']: r for r in result['transactions']['local_events']}
    assert result['local_model'] == config['local_model']
    assert result['completed_local_work'] == sum(r['completed_ns'] >= 0 for r in local.values())
    assert {r['id'] for r in result['pending_local_work']} == {i for i, r in local.items() if r['completed_ns'] < 0}
    for identity, row in local.items():
        op = specs[identity]
        assert (row['rank'], row['duration_ns']) == (op['rank'], op['duration_ns'])
        if row['ready_ns'] < 0:
            assert row['start_ns'] == row['completed_ns'] == -1
            continue
        parents = []
        for d in op['after']:
            predecessor = local[d['operation']] if specs[d['operation']]['kind'] == 'local' else remote[d['operation']]
            value = predecessor[dict(applied='applied_ns', request_acked='request_acked_ns', completed='completed_ns')[d['event']]]
            assert value >= 0
            parents.append(value)
        ready = max(parents, default=0)
        if 'local_wait' in op:
            assert row['ready_ns'] == conditions[identity]['time_ns'] >= ready
        else:
            assert row['ready_ns'] == ready
        if row['start_ns'] >= 0:
            assert row['start_ns'] >= row['ready_ns']
            if row['completed_ns'] >= 0:
                assert row['completed_ns'] == row['start_ns']+op['duration_ns']
    # Independent lane service: FIFO by admission, not input position or DAG
    # position. Fixtures avoid ambiguous cross-callback ties between admissions.
    position = {op['id']: i for i, op in enumerate(config['operations'])}
    for rank in {r['rank'] for r in local.values()}:
        admitted = sorted((r for r in local.values() if r['rank'] == rank and r['ready_ns'] >= 0),
                          key=lambda r: (r['ready_ns'], position[r['id']]))
        end = 0
        for row in admitted:
            if end < 0:
                assert row['start_ns'] == row['completed_ns'] == -1
            else:
                assert row['start_ns'] == max(end, row['ready_ns'])
            end = row['completed_ns']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--reference-backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    backend, reference, out = (p.resolve() for p in (args.backend, args.reference_backend, args.output))
    assert backend.is_file() and reference.is_file() and not out.exists()
    assert shutil.disk_usage(out.parent).free >= 8*2**30+64*2**20
    out.mkdir(); started = time.monotonic(); commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')

    def invoke(name, argv, expected=0):
        before = time.monotonic(); remaining = 55-(before-started); assert remaining > 0
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            run = subprocess.run(list(map(str, argv)), cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=remaining)
        commands.append(dict(argv=list(map(str, argv)), cwd=str(out), exitcode=run.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands)
        assert run.returncode == expected, name

    def cli(name, argv, expected=0):
        invoke(name, [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *argv], expected)

    def run(name, work, binary=backend, expected=0):
        save(out/(name+'-input.json'), work)
        cli(name, ['rdma-transaction-probe', '--topology', 'topology', '--protocol', 'protocol.json',
                   '--config', name+'-input.json', '--backend', binary, '--output', name], expected)
        if (out/name/'result.json').exists(): return read(out/name/'result.json')

    shutil.copy2(ROOT/'examples/scaleout_rdma/small-clos-100g.json', out/'topology-input.json')
    protocol = read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture'] = dict(nodes=[0, 1, 4], start_ns=0, end_ns=5000000, max_events=10000)
    save(out/'protocol.json', protocol)
    cli('generate', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    config = read(ROOT/'examples/scaleout_rdma_transactions/local-stages.json')
    normal = run('normal', config)
    normal_audit = audit(normal, config, protocol['payload_bytes']); local_audit(normal, config)
    frame_count = endpoint_audit(normal, config)
    tx = {r['id']: r for r in normal['transactions']['operations']}
    loc = {r['id']: r for r in normal['local_work']}
    assert normal['completed_operations'] == 10 and normal['completed_local_work'] == 6
    assert loc[13]['start_ns'] == tx[2]['applied_ns'] < loc[10]['ready_ns'] < loc[13]['completed_ns']
    assert loc[10]['start_ns'] == loc[13]['completed_ns']
    assert loc[12]['start_ns'] == 0 < loc[13]['start_ns'] < loc[12]['completed_ns']
    assert loc[14]['ready_ns'] == loc[14]['start_ns'] == loc[14]['completed_ns'] == loc[10]['completed_ns']
    assert tx[3]['ready_ns'] == loc[14]['completed_ns']
    assert tx[8]['ready_ns'] == loc[11]['completed_ns']
    assert loc[15]['completed_ns'] == normal['finish_ns'] > max(r['completed_ns'] for r in tx.values())
    run('repeat', config)
    assert (out/'normal/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()
    renamed = deepcopy(config)
    for op in renamed['operations']:
        op['id'] += 100
        for dep in op['after']: dep['operation'] += 100
    renamed_result = run('renamed', renamed)
    assert audit(renamed_result, renamed, protocol['payload_bytes']) == normal_audit
    local_audit(renamed_result, renamed); endpoint_audit(renamed_result, renamed)
    for key in ('source_packets', 'source_payload_packets'):
        assert normal['transactions']['transport'][key] == renamed_result['transactions']['transport'][key]

    fifo = deepcopy(config)
    first = deepcopy(config['operations'][0]); last = deepcopy(config['operations'][7])
    last.update(id=4, after=[dict(operation=2, event='completed'), dict(operation=3, event='completed')])
    fifo['operations'] = [first,
        dict(id=1, kind='local', rank=4, duration_ns=2000, after=[dict(operation=0, event='applied')]),
        dict(id=2, kind='local', rank=4, duration_ns=0, after=[dict(operation=1, event='completed')]),
        dict(id=3, kind='local', rank=4, duration_ns=3000, after=[dict(operation=0, event='applied')]), last]
    fifo_result = run('fifo', fifo); audit(fifo_result, fifo, protocol['payload_bytes'])
    local_audit(fifo_result, fifo); endpoint_audit(fifo_result, fifo)
    f = {r['id']: r for r in fifo_result['local_work']}
    assert f[2]['ready_ns'] == f[3]['start_ns'] == f[1]['completed_ns']
    assert f[2]['start_ns'] == f[2]['completed_ns'] == f[3]['completed_ns']

    failures = {}
    for name in ('missing-count', 'wrong-data', 'cutoff'):
        case = deepcopy(config)
        if name == 'missing-count': case['operations'][2]['value'] = 0
        elif name == 'wrong-data': case['operations'][0]['hex'] = (12).to_bytes(8, 'little').hex()
        else: case['operations'][3]['duration_ns'] = 5000000
        partial = run(name, case, expected=1)
        assert not partial['passed'] and not partial['transactions']['transport']['closed']
        summary = audit(partial, case, protocol['payload_bytes'], allow_partial=True)
        local_audit(partial, case); endpoint_audit(partial, case)
        assert partial['completed_operations'] == 3 and len(partial['pending_operations']) == 7
        assert {r['id'] for r in partial['transactions']['operations']} == {0, 1, 2}
        loc = {r['id']: r for r in partial['local_work']}
        pending = {r['id']: r for r in partial['pending_local_work']}
        assert loc[11]['ready_ns'] == loc[14]['ready_ns'] == loc[15]['ready_ns'] == -1
        if name == 'cutoff':
            assert loc[13]['start_ns'] > 0 and loc[13]['completed_ns'] == -1
            assert loc[10]['ready_ns'] > 0 and loc[10]['start_ns'] == loc[10]['completed_ns'] == -1
            assert pending[10]['admitted'] and summary['final_atomic_value'] == 0
            assert {e['operation'] for e in partial['transactions']['local_events']} == {10}
        else:
            assert not pending[10]['admitted'] and not partial['transactions']['local_events']
            assert [w['matched'] for w in pending[10]['local_wait']] == ([False, True] if name == 'missing-count' else [True, False])
            assert summary['final_atomic_value'] == (1 if name == 'missing-count' else 2)
        failures[name] = summary

    legacy = []
    for name in ('work', 'local-waits', 'queue-fences'):
        case = read(ROOT/f'examples/scaleout_rdma_transactions/{name}.json')
        run(name+'-old', case, reference); run(name+'-new', case)
        assert (out/(name+'-old')/'result.json').read_bytes() == (out/(name+'-new')/'result.json').read_bytes()
        legacy.append(name)
    remote_only = read(ROOT/'examples/scaleout_rdma_transactions/work.json')
    remote_only.update(schema_version='moe-rdma-transactions/2', local_model=deepcopy(config['local_model']))
    for op in remote_only['operations']: op['kind'] = 'remote'
    converted = run('remote-only-v2', remote_only)
    for key in ('local_model', 'local_work', 'completed_local_work', 'pending_local_work'): converted.pop(key)
    assert converted == read(out/'work-old/result.json')

    invalid = []
    for name in ('bool-rank', 'switch-rank', 'bool-duration', 'wrong-event', 'extra-time', 'wrong-model', 'wrong-kind', 'foreign-window', 'bool-condition'):
        case = deepcopy(config)
        if name == 'bool-rank': case['operations'][3]['rank'] = True
        elif name == 'switch-rank': case['operations'][3]['rank'] = 8
        elif name == 'bool-duration': case['operations'][3]['duration_ns'] = True
        elif name == 'wrong-event': case['operations'][7]['after'][0]['event'] = 'applied'
        elif name == 'extra-time': case['operations'][3]['ready_ns'] = 0
        elif name == 'wrong-model': case['local_model']['duration_source'] = 'measured'
        elif name == 'wrong-kind': case['operations'][3]['kind'] = 'compute'
        elif name == 'foreign-window': case['operations'][4]['local_wait'][0]['key'] = 8
        else: case['operations'][4]['local_wait'][0]['value'] = True
        assert run(name, case, expected=1) is None and not (out/name).exists()
        invoke(name+'-native', [backend, out/'topology/topology.json', out/'protocol.json',
                               out/(name+'-input.json'), out/(name+'-native.json')], 1)
        assert not (out/(name+'-native.json')).exists()
        invalid.append(name)
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total <= 64*2**20 and time.monotonic()-started < 60
    verified = dict(passed=True, normal=normal_audit, local_work=normal['local_work'], endpoint_frames_checked=frame_count,
        failures=failures, repeat_byte_identical=True, renaming_wire_invariant=True, fifo_old_queue_first=True,
        legacy_byte_identical=legacy, remote_only_v2_equal=True, invalid_cli_and_native=invalid,
        commands=len(commands), wall_seconds=time.monotonic()-started, output_bytes=total,
        backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(),
        reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        scope='functional declared-duration stages; no GPU visibility, modern GIN or hardware calibration')
    save(out/'verification.json', verified); print(json.dumps(verified))


if __name__ == '__main__': main()
