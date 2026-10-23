"""Public native acceptance for passive, bounded IPv4/QBB wire headers."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def inspect(result, work):
    capture = result['packet_capture']
    table = capture['wire_headers']
    assert table['columns'] == ['packet_uid', 'ip_src', 'ip_dst', 'ip_protocol',
                                'source_port', 'destination_port', 'ports_present']
    headers, indices, events = table['headers'], table['event_header_indices'], capture['events']
    assert len(indices) == len(events)
    assert len(set(map(tuple, headers))) == len(headers)
    first_seen = []
    ack_ports = defaultdict(set)
    bindings = {s['wire_port']: s for s in work['streams']}
    for event, index in zip(events, indices):
        assert type(index) is int and 0 <= index < len(headers)
        if index not in first_seen:
            first_seen.append(index)
        uid, src, dst, proto, sport, dport, present = headers[index]
        assert uid == event[4] and proto == event[6] and present == 1
        assert proto in (17, 252)
        stream = bindings[sport if proto == 17 else dport]
        ip = lambda gpu: 0x0b000001 + gpu * 32 + stream['plane']
        endpoints = ip(stream['src']), ip(stream['dst'])
        if proto == 17:
            assert (src, dst) == endpoints and dport == stream['wire_port']
        else:
            assert (dst, src) == endpoints and sport == stream['wire_port']
            ack_ports[src, dst, event[7]].add(dport)
    assert first_seen == list(range(len(headers)))
    return dict(events=len(events), header_rows=len(headers),
                ambiguous_endpoint_sequence_groups=sum(len(v) > 1 for v in ack_ports.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--reference-backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    assert not out.exists()
    out.mkdir()
    started = time.monotonic()
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'python'), PYTHONDONTWRITEBYTECODE='1')
    commands = []

    def save(name, value):
        (out / name).write_text(json.dumps(value, indent=2) + '\n')

    def invoke(name, argv, expected=0):
        before = time.monotonic()
        with (out / (name + '.stdout')).open('w') as stdout, (out / (name + '.stderr')).open('w') as stderr:
            process = subprocess.run(list(map(str, argv)), env=env, cwd=out,
                                     stdout=stdout, stderr=stderr, timeout=15)
        commands.append(dict(command=list(map(str, argv)), cwd=str(out), exitcode=process.returncode,
                             expected_exitcode=expected, wall_seconds=time.monotonic()-before))
        save('commands.json', commands)
        assert process.returncode == expected, name

    cli = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout']
    topology = json.loads((ROOT / 'examples/scaleout_rdma/small-clos-100g.json').read_text())
    topology['fabric']['planes'] = 2
    topology['fabric'].update(tors_per_segment_per_rail=1, aggs_per_pod_per_group=1,
                              cores_per_group=1, tor_uplinks=1, agg_uplinks=1, core_wiring='full_mesh')
    topology['nic']['total_rate_bps'] = 200000000000
    save('topology-input.json', topology)
    invoke('topology', cli + ['topology', '--config', 'topology-input.json', '--output', 'topology'])
    work = json.loads((ROOT / 'fixtures/wire_headers/work.json').read_text())
    save('work.json', work)
    protocol = json.loads((ROOT / 'examples/scaleout_rdma/rdma.json').read_text())
    protocol['packet_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000, max_events=10000)

    def run(name, capture, binary):
        effective = dict(protocol, packet_capture=capture)
        save(name + '-protocol.json', effective)
        invoke(name, cli + ['rdma-stream-probe', '--topology', 'topology', '--protocol', name+'-protocol.json',
                           '--config', 'work.json', '--backend', binary.resolve(), '--output', name])
        result = json.loads((out / name / 'result.json').read_text())
        assert result['passed'] and result['drained']
        return result

    capture = protocol['packet_capture']
    old = run('old', capture, args.reference_backend)
    current = run('current', capture, args.backend)
    assert (out / 'old/result.json').read_bytes() == (out / 'current/result.json').read_bytes()
    explicit_false = run('false', dict(capture, wire_headers=False), args.backend)
    assert 'wire_headers' not in explicit_false['packet_capture']
    del explicit_false['packet_capture']['selection']['wire_headers']
    assert explicit_false == old
    observed = run('observed', dict(capture, wire_headers=True), args.backend)
    full = inspect(observed, work)
    assert observed['packet_capture']['complete'] and full['ambiguous_endpoint_sequence_groups'] > 0
    stripped = deepcopy(observed)
    del stripped['packet_capture']['wire_headers']
    del stripped['packet_capture']['selection']['wire_headers']
    assert stripped == old
    limited = run('limited', dict(capture, wire_headers=True, max_events=7), args.backend)
    inspect(limited, work)
    assert limited['packet_capture']['events'] == observed['packet_capture']['events'][:7]
    assert not limited['packet_capture']['complete'] and limited['packet_capture']['omitted_events'] > 0
    selected = run('selected', dict(capture, wire_headers=True, nodes=[0], start_ns=1000, end_ns=20000), args.backend)
    selected_audit = inspect(selected, work)
    assert selected['packet_capture']['events'] == [e for e in observed['packet_capture']['events']
                                                   if e[1] == 0 and 1000 <= e[0] < 20000]
    assert selected_audit['events'] > 0
    invalid = dict(protocol, packet_capture=dict(capture, wire_headers=1))
    save('invalid.json', invalid)
    invoke('native-rejection', [args.backend.resolve(), out/'topology/topology.json', out/'invalid.json',
                              out/'work.json', out/'rejected.json'], 1)
    assert 'wire_headers must be boolean' in (out/'native-rejection.stderr').read_text()
    assert not (out/'rejected.json').exists()
    save('verification.json', dict(passed=True, default_result_byte_identical=True,
         enabled_original_fields_identical=True, explicit_false_original_fields_identical=True,
         truncated_saved_prefix_verified=True, selected_events_verified=True, native_type_rejection=True,
         full=full, selected=selected_audit, work=len(work['work']), public_commands=len(commands)-1,
         backend_sha256=hashlib.sha256(args.backend.read_bytes()).hexdigest(),
         reference_backend_sha256=hashlib.sha256(args.reference_backend.read_bytes()).hexdigest(),
         wall_seconds=time.monotonic()-started))
    print((out/'verification.json').read_text())


if __name__ == '__main__':
    main()
