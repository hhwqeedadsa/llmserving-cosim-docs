"""Interactive native acceptance, actual byte/return-value and protocol checks."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from moe_ub_cosim.transaction_session import NativeTransactionSession
from check_remote_transaction_workflow import audit
from check_local_io_workflow import io_audit
from check_transaction_local_stage_workflow import endpoint_audit

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def validate_events(result):
    rows = {op['id']: op for op in result['transactions']['operations']}
    locals_ = {op['id']: op for op in result['local_work']}
    names = dict(submitted='ready_ns', published='published_ns', applied='applied_ns',
                 request_acked='request_acked_ns', response='response_ns', completed='completed_ns',
                 local_admitted='ready_ns', local_started='start_ns')
    found = set()
    for e in result['session']['events']:
        key = e['operation'], e['event']
        assert key not in found
        found.add(key)
        source = (locals_ if e['operation'] in locals_ else rows)[e['operation']]
        assert e['time_ns'] == source[names[e['event']]]
        if 'returned_value' in e:
            assert e['event'] in ('completed', 'response') and e['returned_value'] == source['returned_value']
    expected = {(identity, event) for identity, row in rows.items() for event, field in names.items()
                if event not in ('local_admitted', 'local_started') and row[field] >= 0}
    expected.update((identity, event) for identity, row in locals_.items()
                    for event, field in [('local_admitted', 'ready_ns'), ('local_started', 'start_ns'), ('completed', 'completed_ns')]
                    if row[field] >= 0)
    assert found == expected
    registered = {r['operation']: r['time_ns'] for r in result['session']['registrations']}
    for identity, row in {**rows, **locals_}.items():
        assert registered[identity] <= row['ready_ns']
    return len(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backend', 'reference-backend', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    assert not out.exists()
    out.mkdir()
    start = time.monotonic()
    commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')

    def invoke(name, argv, expected=0):
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            began = time.monotonic()
            p = subprocess.run(list(map(str, argv)), env=env, cwd=out, stdout=stdout, stderr=stderr, timeout=20)
        commands.append(dict(command=list(map(str, argv)), cwd=str(out), exitcode=p.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-began))
        save(out/'commands.json', commands)
        assert p.returncode == expected, name

    cli = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout']
    save(out/'topology-input.json', read(ROOT/'examples/scaleout_rdma/small-clos-100g.json'))
    protocol = read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol.update(payload_bytes=64, chunk_bytes=256)
    protocol['packet_capture'] = dict(nodes=[0, 1, 4], start_ns=0, end_ns=5000000, max_events=10000, wire_headers=True)
    save(out/'protocol.json', protocol)
    invoke('topology', cli+['topology', '--config', 'topology-input.json', '--output', 'topology'])
    topology = out/'topology/topology.json'
    config = read(ROOT/'examples/scaleout_rdma_transactions/local-io.json')
    save(out/'program.json', config)
    invoke('reference', cli+['rdma-transaction-probe', '--topology', 'topology', '--protocol', 'protocol.json',
           '--config', 'program.json', '--backend', args.reference_backend.resolve(), '--output', 'reference'])
    reference = read(out/'reference/result.json')
    initial = dict(config, operations=[])
    # All initial program nodes in one commit: compare the underlying complete
    # execution, including callbacks, packets, bytes and memory, to the old path.
    (out/'all-at-once').mkdir()
    with NativeTransactionSession(topology, out/'protocol.json', initial, args.backend, out/'all-at-once') as s:
        first = True
        while (e := s.receive()) is not None:
            if first:
                time.sleep(.02)  # Deliberate host-only controller delay.
            s.commit(config['operations'] if first else [], finish=(not first and e['drained']))
            first = False
    together = read(out/'all-at-once/result.json')
    plain = deepcopy(together)
    del plain['session']
    assert plain == reference
    together_events = validate_events(together)
    invoke('online', cli+['rdma-transaction-session', '--topology', 'topology', '--protocol', 'protocol.json',
           '--config', 'program.json', '--backend', args.backend.resolve(), '--output', 'online'])
    online = read(out/'online/result.json')
    audit(online, config, 64)
    io_audit(online, config)
    endpoint_frames = endpoint_audit(online, config)
    online_events = validate_events(online)
    transitions = {(e['operation'], e['event']): e['time_ns'] for e in online['session']['events']}
    registered = {r['operation']: r['time_ns'] for r in online['session']['registrations']}
    for node in config['operations']:
        assert registered[node['id']] == max((transitions[d['operation'], d['event']] for d in node['after']), default=0)

    # A real decoded FetchAdd reply chooses a later request body. The second
    # request does not exist in the native startup input or first commit.
    dynamic = []
    for value in (7, 19):
        name = 'dynamic-'+str(value)
        directory = out/name
        directory.mkdir()
        base = dict(config, connections=config['connections'][:1], operations=[], windows=[
            dict(owner=0, key=7, address=4096, allow_atomic=True, hex='00'*128),
            dict(owner=4, key=9, address=8192, allow_atomic=True, hex=(value.to_bytes(8, 'little')+bytes(120)).hex())])
        atom = dict(id=100, kind='remote', connection=0, verb='fetch_add', key=9, address=8192, value=3, after=[])
        program = dict(base, operations=[atom])
        returned = None
        issued_second = False
        local_rejection = False
        with NativeTransactionSession(topology, out/'protocol.json', base, args.backend, directory) as s:
            first = True
            while (e := s.receive()) is not None:
                if first:
                    try:
                        s.commit([dict(atom, value=True)])
                    except ValueError:
                        local_rejection = True
                    else:
                        raise AssertionError('invalid atomic value accepted')
                    s.commit([atom])
                    first = False
                    continue
                done = False
                for row in e['events']:
                    if row['operation'] == 100 and 'returned_value' in row:
                        returned = row['returned_value']
                    done |= row['operation'] == 100 and row['event'] == 'completed'
                if done and not issued_second:
                    assert returned == value
                    put = dict(id=101, kind='remote', connection=0, verb='put', key=9, address=8208,
                               hex=(returned+100).to_bytes(8, 'little').hex(), after=[dict(operation=100, event='completed')])
                    program['operations'].append(put)
                    s.commit([put])
                    issued_second = True
                else:
                    s.commit([], finish=issued_second and e['drained'])
        result = read(directory/'result.json')
        audit(result, program, 64)
        count = validate_events(result)
        window = next(w for w in result['transactions']['windows'] if w['owner'] == 4)
        memory = bytes.fromhex(window['hex'])
        assert int.from_bytes(memory[:8], 'little') == value+3
        assert int.from_bytes(memory[16:24], 'little') == value+100
        request_qp = next(q for q in result['transactions']['transport']['streams'] if q['id'] == 0)
        assert request_qp['qp_creations'] == 1 and request_qp['accepted_work'] == 2
        assert local_rejection and issued_second
        save(directory/'effective-program.json', program)
        dynamic.append(dict(initial_value=value, actual_returned_value=returned, derived_put_value=value+100,
                            events=count, qp_creations=1, finish_ns=result['finish_ns']))

    # Bypass Python admission: native command errors must retain partial state
    # and must not execute the valid prefix of an invalid batch.
    failures = []
    for kind in ('batch-invalid', 'wrong-time', 'wrong-sequence', 'early-finish', 'eof',
                 'bool-value', 'duplicate-id', 'future-dependency', 'over-budget', 'idle-empty', 'oversized'):
        directory = out/kind
        directory.mkdir()
        save(directory/'initial.json', base)
        argv = [str(args.backend.resolve()), str(topology), str(out/'protocol.json'),
                str(directory/'initial.json'), str(directory/'result.json')]
        with (directory/'stderr.log').open('w') as errors:
            p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True)
            first_line = p.stdout.readline()
            assert first_line.startswith('MOE_TX_EVENT ')
            e = json.loads(first_line[len('MOE_TX_EVENT '):])
            commit = dict(type='COMMIT', decision_id=e['decision_id'], now_ns=e['now_ns'], submissions=[atom], finish=False)
            if kind == 'batch-invalid':
                commit['submissions'].append(dict(atom, id=101, unexpected=True))
            elif kind == 'wrong-time':
                commit['now_ns'] += 1
            elif kind == 'wrong-sequence':
                commit['decision_id'] += 1
            elif kind == 'early-finish':
                commit.update(submissions=[], finish=True)
            elif kind == 'bool-value':
                commit['submissions'][0] = dict(atom, value=True)
            elif kind == 'duplicate-id':
                commit['submissions'].append(atom)
            elif kind == 'future-dependency':
                commit['submissions'][0] = dict(atom, after=[dict(operation=999, event='completed')])
            elif kind == 'over-budget':
                commit['submissions'] = [dict(atom, id=identity) for identity in range(33)]
            elif kind == 'idle-empty':
                commit['submissions'] = []
            text = None if kind == 'eof' else ' '*1048578+'\n' if kind == 'oversized' else json.dumps(commit)+'\n'
            remaining, _ = p.communicate(text, timeout=10)
            (directory/'stdout.log').write_text(first_line+remaining)
            assert p.returncode == 1
        result = read(directory/'result.json')
        assert not result['passed'] and result['execution_error']
        assert not result['transactions']['operations'] and not result['session']['registrations']
        assert not result['transactions']['transport']['source_packets']
        failures.append(kind)

    cutoff = out/'cutoff'
    cutoff.mkdir()
    try:
        with NativeTransactionSession(topology, out/'protocol.json', initial, args.backend, cutoff) as s:
            first = True
            while (e := s.receive()) is not None:
                nodes = [dict(id=200, kind='local', rank=0, duration_ns=5000000, after=[],
                              copy=dict(source=dict(key=7, address=4096, bytes=8), target=dict(key=7, address=4128))),
                         dict(atom, id=201, key=8, address=8960, after=[dict(operation=200, event='completed')])] if first else []
                s.commit(nodes)
                first = False
    except ValueError as error:
        assert 'native transaction session failed' in str(error)
    else:
        raise AssertionError('cutoff incorrectly reported success')
    partial = read(cutoff/'result.json')
    assert partial['finish_ns'] == 5000000 and not partial['passed']
    assert partial['local_work'][0]['completed_ns'] == -1 and partial['completed_operations'] == 0
    assert len(partial['transactions']['active_local_memory_leases']) == 1
    assert [e['kind'] for e in partial['transactions']['local_memory_events']] == ['acquired', 'captured']
    save(out/'verification.json', dict(passed=True, all_at_once_original_fields_equal=True,
         all_at_once_events=together_events, online_events=online_events, online_endpoint_frames=endpoint_frames,
         actual_return_driven_conditions=dynamic, native_preflight_failures=failures,
         cutoff_actual_prefix_verified=True, future_work_not_preloaded=True,
         native_shared_clock=True, deliberate_host_delay_did_not_change_original_times=True,
         modern_deepep_connected=False, hardware_calibrated=False,
         public_commands=len(commands), wall_seconds=time.monotonic()-start,
         backend_sha256=hashlib.sha256(args.backend.read_bytes()).hexdigest(),
         reference_sha256=hashlib.sha256(args.reference_backend.read_bytes()).hexdigest()))
    print((out/'verification.json').read_text())


if __name__ == '__main__':
    main()
