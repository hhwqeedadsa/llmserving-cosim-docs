"""Public causal local-stage workflow, checked against endpoint packets and ACKs.

All durations and the slow-uplink ECN fixture are artificial functional inputs.
This script imports no platform execution internals or other test drivers.
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

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def audit(result, config, mtu):
    assert result['passed'] and result['native_network_executed'] and result['drained']
    transport = result['persistent_transport']
    assert transport['closed'] and transport['physical_drained']
    assert result['completed_work'] == len(config['work'])
    writes = {w['id']: w for w in transport['work']}
    locals_ = {w['id']: w for w in result['local_work']}
    assert result['completed_local_work'] == len(locals_) == 5
    assert set(writes) == {0, 4, 6} and set(locals_) == {1, 2, 3, 5, 7}
    capture = result['packet_capture']
    assert capture['complete']
    packets = [dict(zip(capture['columns'], p)) for p in capture['events']]
    source = [dict(zip(transport['source_packet_columns'], p)) for p in transport['source_packets']]
    streams = {s['id']: s for s in config['streams']}
    times = {}
    for node in config['work']:
        if node['kind'] != 'write':
            continue
        row = writes[node['id']]
        stream = streams[node['stream']]
        selected = [p for p in source if p['stream'] == node['stream'] and row['begin'] <= p['sequence'] < row['end']]
        expected = []
        seq = row['begin']
        while seq < row['end']:
            size = min(mtu, row['end'] - seq)
            expected.append((seq, size))
            seq += size
        assert [(p['sequence'], p['payload_bytes']) for p in selected] == expected
        arrivals = []
        for p in selected:
            assert (p['src'], p['dst'], p['nic']) == (stream['src'], stream['dst'], stream['source_nic'])
            rx = [q for q in packets if q['event'] == 1 and q['packet_uid'] == p['packet_uid'] and q['node'] == stream['dst']]
            assert len(rx) == 1 and rx[0]['sequence'] == p['sequence']
            arrivals.append(rx[0]['time_ns'])
        ack = [p['time_ns'] for p in packets if p['event'] == 1 and p['node'] == stream['src']
               and p['ip_protocol'] == 252 and p['sequence'] >= row['end']]
        times[node['id']] = dict(accepted=max(arrivals), acked=min(ack))
        assert row['accepted_ns'] == times[node['id']]['accepted']
        assert row['acked_ns'] == times[node['id']]['acked']
        assert row['bytes'] == node['bytes'] and row['ready_ns'] <= row['published_ns'] <= min(p['time_ns'] for p in selected)
    # Local ready times are independently derived from endpoint observations
    # and earlier declared-duration completions; no future finish is injected.
    lanes = {}
    for node in config['work']:
        if node['kind'] != 'local':
            continue
        row = locals_[node['id']]
        ready = max((times[d['work']][d['event']] for d in node['after']), default=0)
        start = max(ready, lanes.get(node['rank'], 0))
        end = start + node['duration_ns']
        assert (row['ready_ns'], row['start_ns'], row['completed_ns']) == (ready, start, end)
        times[node['id']] = dict(completed=end)
        lanes[node['rank']] = end
    for node in config['work']:
        if node['kind'] == 'write':
            ready = max((times[d['work']][d['event']] for d in node['after']), default=0)
            assert writes[node['id']]['ready_ns'] == ready
    assert locals_[1]['ready_ns'] == locals_[2]['ready_ns']
    assert locals_[2]['start_ns'] == locals_[1]['completed_ns']
    assert locals_[3]['start_ns'] < locals_[1]['start_ns'] < locals_[3]['completed_ns']
    assert locals_[5]['start_ns'] == locals_[5]['completed_ns']
    assert writes[6]['ready_ns'] == locals_[3]['completed_ns']
    assert locals_[7]['completed_ns'] > writes[6]['acked_ns']  # local-only tail must keep QPs alive
    for stream in transport['streams']:
        ordered = sorted((w for w in writes.values() if w['stream'] == stream['id']), key=lambda w: w['ticket'])
        end = 0
        for ticket, row in enumerate(ordered):
            assert row['ticket'] == ticket and row['begin'] == end
            end = row['end']
        assert stream['qp_creations'] == 1
        assert stream['snd_nxt'] == stream['snd_una'] == stream['submitted_bytes'] == stream['published_bytes'] == end
    assert sum(p['payload_bytes'] for p in source) == 15482
    assert len(source) == 18  # local work emits no extra data packets
    assert transport['source_packets'][-1][5] == 14464  # same stream appends, never resets sequence
    libraries = [p for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert libraries and len({str(Path(p).parent) for p in libraries}) == 1
    assert not any('unified-bus' in p for p in libraries)
    return dict(writes=len(writes), local_jobs=len(locals_), source_packets=len(source), payload_bytes=15482,
                local_work=list(locals_.values()), finish_ns=result['finish_ns'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--reference-backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    backend, reference, out = (p.resolve() for p in (args.backend, args.reference_backend, args.output))
    assert backend.is_file() and reference.is_file() and not out.exists()
    assert shutil.disk_usage(out.parent).free >= 8 * 2**30 + 64 * 2**20
    out.mkdir()
    started = time.monotonic()
    commands = []
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')

    def invoke(name, argv, expected=0):
        before = time.monotonic()
        assert before - started < 240
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            run = subprocess.run(list(map(str, argv)), cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=60)
        commands.append(dict(argv=list(map(str, argv)), cwd=str(out), exitcode=run.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands)
        assert run.returncode == expected, name

    def cli(name, argv, expected=0):
        invoke(name, [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *argv], expected)

    def run(name, work, protocol='protocol.json', topology='topology', binary=backend, expected=0):
        save(out/(name+'-input.json'), work)
        cli(name, ['rdma-stream-probe', '--topology', topology, '--protocol', protocol,
                   '--config', name+'-input.json', '--backend', binary, '--output', name], expected)
        if not expected:
            return read(out/name/'result.json')

    topology = read(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    protocol = read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture'] = dict(nodes=[0, 4], start_ns=0, end_ns=5000000, max_events=10000)
    save(out/'topology-input.json', topology)
    save(out/'protocol.json', protocol)
    cli('generate', ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    work = read(ROOT/'examples/scaleout_rdma_streams/local-stages.json')
    result = run('local', work)
    normal = audit(result, work, protocol['payload_bytes'])
    run('repeat', work)
    assert (out/'local/result.json').read_bytes() == (out/'repeat/result.json').read_bytes()

    # A previously queued job (input index 3) must precede a newly unlocked
    # job (index 2), even though the latter has the lower input index.
    fifo = deepcopy(work)
    fifo['work'] = [deepcopy(work['work'][0]),
                    dict(id=1, kind='local', rank=4, duration_ns=2000, after=[dict(work=0, event='accepted')]),
                    dict(id=2, kind='local', rank=4, duration_ns=0, after=[dict(work=1, event='completed')]),
                    dict(id=3, kind='local', rank=4, duration_ns=3000, after=[dict(work=0, event='accepted')]),
                    dict(id=4, kind='write', stream=1, bytes=1,
                         after=[dict(work=2, event='completed'), dict(work=3, event='completed')])]
    fifo_result = run('fifo', fifo)
    fifo_rows = {w['id']: w for w in fifo_result['local_work']}
    first = next(w for w in fifo_result['persistent_transport']['work'] if w['id'] == 0)['accepted_ns']
    assert fifo_rows[1]['start_ns'] == fifo_rows[3]['ready_ns'] == first
    assert fifo_rows[2]['ready_ns'] == fifo_rows[1]['completed_ns'] == fifo_rows[3]['start_ns'] == first + 2000
    assert fifo_rows[2]['start_ns'] == fifo_rows[2]['completed_ns'] == fifo_rows[3]['completed_ns'] == first + 5000
    assert next(w for w in fifo_result['persistent_transport']['work'] if w['id'] == 4)['published_ns'] == first + 5000

    # Fixed functional controller witness: slow only the tor/agg and agg/core
    # links to 25G, mark above 1/2 bytes. Parameters saved before execution.
    slow = deepcopy(topology)
    for tier in ('tor_agg', 'agg_core'):
        slow['links'][tier]['rate_bps'] = 25000000000
    slow_protocol = deepcopy(protocol)
    slow_protocol['ecn_by_rate']['25000000000'] = dict(kmin_bytes=1, kmax_bytes=2, pmax=1.0)
    save(out/'feedback-topology-input.json', slow)
    save(out/'feedback-protocol.json', slow_protocol)
    feedback = deepcopy(work)
    feedback['dcqcn_reference'] = read(ROOT/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
    cli('generate-feedback', ['topology', '--config', 'feedback-topology-input.json', '--output', 'feedback-topology'])
    observed = run('feedback', feedback, 'feedback-protocol.json', 'feedback-topology')
    feedback_audit = audit(observed, feedback, protocol['payload_bytes'])
    local = {w['id']: w for w in observed['local_work']}
    events = observed['persistent_transport']['dcqcn_reference']['events']
    timers = [e for e in events if e['kind'] == 'rp_timer' and e['stream'] == 0
              and local[1]['start_ns'] < e['time_ns'] < local[1]['completed_ns']]
    assert timers and any(e['kind'] == 'cnp_received' and e['stream'] == 0 for e in events)

    differential = []
    for version in (1, 2):
        old = read(ROOT/'examples/scaleout_rdma_streams/work.json')
        old['schema_version'] = 'moe-rdma-stream-work/'+str(version)
        if version == 2:
            old['limits'] = dict(max_streams=2, max_work=6)
        for mode in ('default', 'reference'):
            case = deepcopy(old)
            if mode == 'reference':
                case['dcqcn_reference'] = deepcopy(feedback['dcqcn_reference'])
            prefix = 'v'+str(version)+'-'+mode
            run(prefix+'-old', case, binary=reference)
            run(prefix+'-new', case)
            assert (out/(prefix+'-old')/'result.json').read_bytes() == (out/(prefix+'-new')/'result.json').read_bytes()
            differential.append(prefix)
    write_only = read(ROOT/'examples/scaleout_rdma_streams/work.json')
    write_only.update(schema_version='moe-rdma-stream-work/3', limits=dict(max_streams=2, max_work=6),
                      local_model=deepcopy(work['local_model']))
    for w in write_only['work']:
        w['kind'] = 'write'
    converted = run('write-only-v3', write_only)
    for key in ('local_model', 'local_work', 'completed_local_work'):
        converted.pop(key)
    assert converted == read(out/'v1-default-old/result.json')

    invalid = []
    for name in ('bool-duration', 'bool-rank', 'wrong-rank', 'wrong-event', 'extra-time', 'wrong-model', 'wrong-kind'):
        case = deepcopy(work)
        if name == 'bool-duration': case['work'][1]['duration_ns'] = True
        elif name == 'bool-rank': case['work'][1]['rank'] = True
        elif name == 'wrong-rank': case['work'][1]['rank'] = 8
        elif name == 'wrong-event': case['work'][4]['after'][0]['event'] = 'acked'
        elif name == 'extra-time': case['work'][1]['ready_ns'] = 0
        elif name == 'wrong-model': case['local_model']['duration_source'] = 'calibrated'
        else: case['work'][1]['kind'] = 'compute'
        run(name, case, expected=1)
        assert not (out/name).exists()
        invoke(name+'-native', [backend, out/'topology/topology.json', out/'protocol.json',
                               out/(name+'-input.json'), out/(name+'-native.json')], 1)
        assert not (out/(name+'-native.json')).exists()
        invalid.append(name)

    cutoff = deepcopy(work)
    cutoff['work'][1]['duration_ns'] = 5000000
    run('cutoff', cutoff, expected=1)
    partial = read(out/'cutoff/result.json')
    assert not partial['passed'] and not partial['persistent_transport']['closed']
    rows = {w['id']: w for w in partial['local_work']}
    assert rows[1]['start_ns'] > 0 and rows[1]['completed_ns'] == -1
    assert rows[2]['ready_ns'] > 0 and rows[2]['start_ns'] == rows[2]['completed_ns'] == -1
    assert set(w['id'] for w in partial['persistent_transport']['work']) == {0}
    assert partial['completed_work'] == 2 and partial['completed_local_work'] == 1
    assert all(rows[i]['ready_ns'] == rows[i]['start_ns'] == rows[i]['completed_ns'] == -1 for i in (5, 7))
    assert not any(p[1] == 1 for p in partial['persistent_transport']['source_packets'])
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    assert total < 64 * 2**20 and time.monotonic() - started < 240
    verified = dict(passed=True, normal=normal, feedback=feedback_audit, timer_events_during_local=len(timers),
                    legacy_byte_identical=differential, write_only_v3_equal=True, repeat_byte_identical=True,
                    invalid_cli_and_native=invalid, cutoff_prefix_verified=True, queued_before_newly_ready=True, commands=len(commands),
                    backend_sha256=hashlib.sha256(backend.read_bytes()).hexdigest(),
                    reference_backend_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                    output_bytes=total, wall_seconds=time.monotonic()-started,
                    hardware_calibrated=False, modern_GIN_executed=False)
    save(out/'verification.json', verified)
    print(json.dumps(verified))


if __name__ == '__main__':
    main()
