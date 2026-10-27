"""Public inputs and independent functional audit of one newly built network.

No historical network outputs or test drivers supply the expected result.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(repository, stage):
    destination = stage / 'inputs'
    destination.mkdir()
    for name in ('small-clos-100g.json', 'messages.json', 'rdma.json'):
        value = load(repository / 'examples/scaleout_rdma' / name)
        if name == 'rdma.json':
            value['packet_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000,
                                           max_events=1000, wire_headers=True)
        save(destination / name, value)
    before = subprocess.check_output(
        ['git', 'show', 'b27f008:python/moe_ub_cosim/scaleout_rdma.py'], cwd=repository, text=True)
    old = next(line.split('=')[1].strip().strip("'") for line in before.splitlines()
               if line.startswith('NATIVE_COMMIT ='))
    rows = load(repository / 'deps.lock.json')['dependencies']
    expected = next(row['checkout_commit'] for row in rows if row['name'] == 'ASTRA-original-ns3-backend')
    assert old != expected
    save(stage / 'before.json', dict(old_literal=old, locked_checkout=expected,
        inferred_old_build_rejection_from_source=True, old_build_not_executed=True,
        original_source_sha256=hashlib.sha256(before.encode()).hexdigest(),
        inputs={path.name: sha(path) for path in destination.iterdir()}))


def verify(repository, stage):
    topology = load(stage / 'topology/topology.json')
    result = load(stage / 'network/result.json')
    backend = load(stage / 'build/backend.json')
    provenance = load(stage / 'build/provenance.json')
    messages = load(stage / 'inputs/messages.json')
    inputs = load(stage / 'network/inputs.json')
    expected = load(stage / 'before.json')['locked_checkout']
    assert backend['passed'] and backend['native_commit'] == provenance['native_commit'] == expected
    assert inputs['native_commit'] == expected
    assert inputs['native_commit_role'] == 'expected_source_checkout_not_binary_build_identity'
    assert result['passed'] and result['native_network_executed'] and result['drained']
    assert result['completed_messages'] == len(messages) == 4
    assert sum(message['bytes'] for message in messages) == 6000
    actual = {message['id']: message for message in result['messages']}
    for message in messages:
        observed = actual[message['id']]
        assert all(observed[key] == message[key] for key in ('src', 'dst', 'bytes', 'ready_ns', 'plane'))
        assert observed['receiver_contiguous_bytes'] == observed['source_data_payload_bytes'] == message['bytes']
        assert observed['ack_callbacks'] == 1
        assert observed['ready_ns'] <= observed['first_tx_ns'] < observed['receive_ready_ns'] < observed['ack_ns']
    capture = result['packet_capture']
    assert capture['complete'] and capture['omitted_events'] == 0
    rows = [dict(zip(capture['columns'], row)) for row in capture['events']]
    links = {}
    for link in topology['links']:
        links[link['src'], link['src_port']] = (link['dst'], link['dst_port'])
        links[link['dst'], link['dst_port']] = (link['src'], link['src_port'])
    grouped = defaultdict(list)
    counters = Counter()
    for row in rows:
        assert row['ip_protocol'] in (17, 252)
        assert row['event'] in (0, 1, 3, 4)
        grouped[row['packet_uid']].append(row)
        if row['event'] == 0:
            counters[row['node'], row['port']] += row['model_bytes']
    source_data = [row for row in rows if row['event'] == 0 and row['node'] == 0 and row['ip_protocol'] == 17]
    assert len(source_data) == 7 and sum(row['data_payload_bytes'] for row in source_data) == 6000
    data_offsets = defaultdict(list)
    for row in source_data:
        data_offsets[row['message_id']].append((row['sequence'], row['data_payload_bytes']))
    for message in messages:
        cursor = 0
        for offset, length in sorted(data_offsets[message['id']]):
            assert offset == cursor and 0 < length <= 1000
            cursor += length
        assert cursor == message['bytes']
    hop_count = 0
    for packet_rows in grouped.values():
        tx = [row for row in packet_rows if row['event'] == 0]
        rx = [row for row in packet_rows if row['event'] == 1]
        assert len(tx) == len(rx) == 6
        assert tx[0]['node'] == (0 if tx[0]['ip_protocol'] == 17 else 4)
        assert rx[-1]['node'] == (4 if tx[0]['ip_protocol'] == 17 else 0)
        for index, (sent, received) in enumerate(zip(tx, rx)):
            assert links[sent['node'], sent['port']] == (received['node'], received['port'])
            assert sent['time_ns'] < received['time_ns']
            assert sent['model_bytes'] == received['model_bytes']
            if index:
                assert tx[index]['node'] == rx[index-1]['node']
                assert tx[index]['time_ns'] >= rx[index-1]['time_ns']
            hop_count += 1
    for port in result['ports']:
        assert port['model_tx_bytes'] == counters[port['node'], port['port']]
    assert len(grouped) == 14 and hop_count == 84
    # Retain loader paths in reports; recovered bytes must come only from the
    # copied output, never from a historical absolute location.
    new_output = Path(backend['probe']).parent.parent
    def local_runtime(path):
        return stage / 'build/output' / Path(path).relative_to(new_output)
    native_libs = [Path(path).resolve() for path in result['loaded_libraries'] if Path(path).name.startswith('libns3')]
    assert len(native_libs) == 8 and all(path.is_relative_to(new_output) for path in native_libs)
    assert all('unified-bus' not in path.name for path in native_libs)
    # Source identity alone cannot attest an arbitrary executable passed to a probe.
    binaries = {key: dict(path=backend[key], sha256=sha(local_runtime(backend[key])))
                for key in ('probe', 'stream_probe', 'transaction_probe', 'transaction_session', 'service')}
    summary = dict(passed=True, native_commit=expected, clean_output_build=True,
        independent_messages=4, source_payload_bytes=6000, source_data_packets=7,
        complete_packet_lineages=14, physical_transmissions=84, physical_receives=84,
        all_port_bytes_independently_recounted=True, same_native_libraries=8,
        binaries=binaries, loaded_libraries={str(path): sha(local_runtime(path)) for path in native_libs},
        new_networks=1, source_calibrated_to_hardware=False, same_plane_multi_nic_implemented=False,
        research_innovation_proven=False)
    save(stage / 'verification.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in ('binaries', 'loaded_libraries')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    globals()[args.action](args.repository.resolve(), args.stage.resolve())


if __name__ == '__main__': main()
