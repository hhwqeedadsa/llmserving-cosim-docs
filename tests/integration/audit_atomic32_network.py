"""Independent byte-level audit of the sparse atomic32 public example.

No platform decoders, workflow helpers or historical expected result imports.
Both retransmitted source bytes and newly delivered destination bytes are read.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def frames(data):
    out = []; position = 0
    while position < len(data):
        magic, verb, sequence, address, key, size = struct.unpack_from('>IIQQII', data, position)
        assert magic == 0x4d525431 and verb in (1, 2, 3, 4, 5)
        assert 0 < size <= 32768 and position+32+size <= len(data)
        if verb != 1:
            assert size == (4 if verb in (4, 5) else 8)
        if verb in (3, 5):
            assert (address, key) == (0, 0)
        out.append(dict(verb=verb, sequence=sequence, address=address, key=key,
                        body=data[position+32:position+32+size], begin=position,
                        end=position+32+size))
        position += 32+size
    assert position == len(data)
    return out


def audit(result, work, protocol):
    assert result['gpu_count'] == 128 and result['switch_count'] == 20
    assert result['native_network_executed'] and result['drained'] and net_ready(result)
    assert result['passed'] and not result['pending_operations']
    assert not result['pending_local_work']
    tx = result['transactions']; net = tx['transport']
    assert net['physical_drained'] and net['inflight_packets'] == 0
    assert tx['duplicate_requests'] == 0
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    connections = {c['id']: c for c in work['connections']}
    operations = {op['id']: op for op in work['operations']}
    rows = {row['id']: row for row in tx['operations']}
    local = {row['id']: row for row in result['local_work']}
    assert set(rows) == {i for i, op in operations.items() if op['kind'] == 'remote'}
    assert set(local) == {i for i, op in operations.items() if op['kind'] == 'local'}
    assert result['completed_operations'] == len(rows) == 9
    assert result['completed_local_work'] == len(local) == 2
    stream_desc = {}
    for c in connections.values():
        stream_desc[2*c['id']] = (c['src'], c['dst'], c['source_nic'], c['destination_nic'])
        stream_desc[2*c['id']+1] = (c['dst'], c['src'], c['destination_nic'], c['source_nic'])
    source = defaultdict(dict); source_bytes = 0
    repeated_ranges = []
    assert len(net['source_packets']) == len(net['source_payload_packets'])
    for packet, payload in zip(net['source_packets'], net['source_payload_packets']):
        time, stream, src, dst, nic, sequence, length, uid = packet
        assert (time, stream, sequence) == (payload['time_ns'], payload['stream'], payload['sequence'])
        assert (src, dst, nic) == stream_desc[stream][:3]
        data = bytes.fromhex(payload['hex']); assert len(data) == length <= protocol['payload_bytes']
        source_bytes += length
        repeated = sum(offset in source[stream] for offset in range(sequence, sequence+length))
        if repeated:
            repeated_ranges.append(dict(stream=stream, sequence=sequence, bytes=repeated, time_ns=time))
        for offset, byte in enumerate(data, sequence):
            assert offset not in source[stream] or source[stream][offset] == byte
            source[stream][offset] = byte
    delivery = defaultdict(bytearray); delivery_times = defaultdict(list)
    events = net['selective_repeat']['events']
    for event in events:
        if event['kind'] != 'payload_delivered':
            continue
        stream = event['stream']; data = bytes.fromhex(event['hex'])
        begin = len(delivery[stream]); assert event['sequence'] == begin
        assert event['sack_valid'] and (event['sack_begin'], event['sack_end']) == (begin, begin+len(data))
        delivery[stream].extend(data)
        delivery_times[stream].append((begin, begin+len(data), event['time_ns']))
    assert set(source) == set(delivery)
    decoded = {}; request_frames = {}; reply_frames = {}; logical_bytes = 0
    for stream, byte_map in source.items():
        assert sorted(byte_map) == list(range(len(byte_map)))
        data = bytes(byte_map[i] for i in range(len(byte_map)))
        assert data == bytes(delivery[stream])
        logical_bytes += len(data); decoded[stream] = frames(data)
        for frame in decoded[stream]:
            frame['received_ns'] = next(t for a, b, t in delivery_times[stream] if a < frame['end'] <= b)
            key = stream//2, frame['sequence']
            table = reply_frames if stream % 2 else request_frames
            assert key not in table; table[key] = frame
    assert logical_bytes == 608 and len(request_frames) == 9 and len(reply_frames) == 7
    assert len(tx['target_events']) == len(request_frames)
    assert {(e['connection'], e['sequence']) for e in tx['target_events']} == set(request_frames)
    assert source_bytes >= logical_bytes
    if not protocol.get('faults'):
        assert source_bytes == logical_bytes
    else:
        assert source_bytes > logical_bytes

    boundaries = {(w['stream'], w['begin'], w['end']): w for w in net['work']}
    assert len(boundaries) == len(request_frames)+len(reply_frames)
    previous = {}
    for op_id, row in rows.items():
        op = operations[op_id]; key = row['connection'], row['sequence']
        request = request_frames[key]
        verb = {'put': 1, 'fetch_add': 2, 'fetch_add32': 4}[op['verb']]
        assert request['verb'] == verb and row['verb'] == op['verb']
        assert (request['key'], request['address']) == (op['key'], op['address'])
        if verb != 1:
            assert int.from_bytes(request['body'], 'big') == op['value']
        wire = boundaries[(2*key[0], request['begin'], request['end'])]
        assert wire['published_ns'] == row['published_ns']
        assert wire['accepted_ns'] == request['received_ns'] == row['applied_ns']
        assert wire['acked_ns'] == row['request_acked_ns']
        assert 0 <= row['ready_ns'] <= row['published_ns'] <= row['applied_ns']
        gates = [row['request_acked_ns'], previous.get(key[0], 0)]
        if verb == 1:
            assert row['response_ns'] == -1 and 'returned_value' not in row
            assert key not in reply_frames
        else:
            reply = reply_frames[key]
            assert reply['verb'] == (5 if verb == 4 else 3)
            assert int.from_bytes(reply['body'], 'big') == row['returned_value']
            assert reply['received_ns'] == row['response_ns'] >= row['applied_ns']
            gates.append(row['response_ns'])
            response_wire = boundaries[(2*key[0]+1, reply['begin'], reply['end'])]
            assert response_wire['published_ns'] == row['applied_ns']
            assert response_wire['accepted_ns'] == row['response_ns']
        assert row['completed_ns'] == max(gates)
        previous[key[0]] = row['completed_ns']
    for stream in net['streams']:
        assert stream['qp_creations'] == 1
        assert stream['submitted_bytes'] == stream['published_bytes'] == stream['snd_una']
        assert stream['ack_completed_work'] == stream['accepted_work']
        assert stream['published_bytes'] == len(delivery.get(stream['id'], b''))
    for c in tx['connections']:
        assert c['closed'] and c['reserved'] == c['published'] == c['completed']
    assert net['closed'] and all(w['acked_ns'] <= result['finish_ns'] for w in net['work'])

    windows = {}; bases = {}
    for w in work['windows']:
        key = w['owner'], w['key']; windows[key] = bytearray(w['bytes']); bases[key] = w['address']
        for segment in w['segments']:
            data = bytes.fromhex(segment['hex']); start = segment['offset']
            windows[key][start:start+len(data)] = data
    def read(owner, key, address, size):
        offset = address-bases[(owner, key)]; data = windows[(owner, key)]
        assert 0 <= offset and offset+size <= len(data)
        return bytes(data[offset:offset+size])
    def write(owner, key, address, data):
        read(owner, key, address, len(data))
        offset = address-bases[(owner, key)]
        windows[(owner, key)][offset:offset+len(data)] = data
    events = [(e['order'], group, e) for group in ('target_events', 'local_events', 'source_events', 'local_memory_events')
              for e in tx[group]]
    events.sort(); assert [order for order, _, _ in events] == list(range(len(events)))
    assert [e['time_ns'] for _, _, e in events] == sorted(e['time_ns'] for _, _, e in events)
    source_states = defaultdict(list); copy_states = defaultdict(list); captured = {}
    for _, group, event in events:
        if group == 'target_events':
            key = event['connection'], event['sequence']; frame = request_frames[key]
            assert event['time_ns'] == frame['received_ns'] and not event['duplicate']
            assert (event['verb'], event['key'], event['address'], event['body_hex']) == (
                frame['verb'], frame['key'], frame['address'], frame['body'].hex())
            owner = connections[key[0]]['dst']; data = frame['body']
            if frame['verb'] != 1:
                size = 4 if frame['verb'] == 4 else 8
                old = int.from_bytes(read(owner, frame['key'], frame['address'], size), 'little')
                assert old == event['old_value'] == int.from_bytes(reply_frames[key]['body'], 'big')
                value = (old+int.from_bytes(data, 'big')) % (1 << (8*size))
                data = value.to_bytes(size, 'little')
            write(owner, frame['key'], frame['address'], data)
        elif group == 'local_events':
            op = operations[event['operation']]
            assert event['time_ns'] == local[op['id']]['ready_ns']
            assert len(event['conditions']) == len(op['local_wait'])
            for condition, declared in zip(event['conditions'], op['local_wait']):
                assert all(condition[k] == v for k, v in declared.items())
                size = declared.get('bits', 64)//8
                observed = int.from_bytes(read(event['owner'], declared['key'], declared['address'], size), 'little')
                assert condition['observed_value'] == observed and condition['matched']
                assert observed == declared['value'] if declared['compare'] == 'eq' else observed >= declared['value']
            for condition in op['local_wait']:
                if 'reset_to' in condition:
                    write(event['owner'], condition['key'], condition['address'],
                          condition['reset_to'].to_bytes(condition.get('bits', 64)//8, 'little'))
        elif group == 'source_events':
            op_id = event['operation']; kind = event['kind']; source_states[op_id].append(kind)
            op = operations[op_id]; row = rows[op_id]
            assert event['owner'] == connections[op['connection']]['src']
            assert all(event[k] == v for k, v in op['source'].items())
            if kind == 'captured':
                data = read(event['owner'], event['key'], event['address'], event['bytes'])
                assert data.hex() == event['hex']
                assert data == request_frames[(row['connection'], row['sequence'])]['body']
                assert event['time_ns'] == row['published_ns']
            elif kind == 'released':
                assert event['time_ns'] == row['completed_ns']
            else:
                assert kind == 'acquired' and event['time_ns'] == row['ready_ns']
        else:
            op_id = event['operation']; kind = event['kind']; copy_states[op_id].append(kind)
            op = operations[op_id]; assert event['source']['owner'] == op['rank']
            for side in ('source', 'target'):
                assert all(event[side][k] == v for k, v in op['copy'][side].items())
            s = event['source']; target = event['target']
            if kind == 'captured':
                captured[op_id] = read(s['owner'], s['key'], s['address'], s['bytes'])
                assert captured[op_id].hex() == event['hex']
                assert event['time_ns'] == local[op_id]['start_ns']
            elif kind == 'committed':
                assert event['hex'] == captured[op_id].hex()
                write(target['owner'], target['key'], target['address'], captured[op_id])
                assert event['time_ns'] == local[op_id]['completed_ns']
            else:
                assert kind in ('acquired', 'released')
                assert event['time_ns'] == local[op_id]['ready_ns' if kind == 'acquired' else 'completed_ns']
    assert dict(source_states) == {0: ['acquired', 'captured', 'released'], 7: ['acquired', 'captured', 'released']}
    assert dict(copy_states) == {6: ['acquired', 'captured', 'committed', 'released'],
                                 9: ['acquired', 'captured', 'committed', 'released']}
    final_hashes = {}
    for w in tx['windows']:
        key = w['owner'], w['key']; actual = bytearray(w['bytes'])
        for page in w['pages']:
            data = bytes.fromhex(page['hex']); offset = page['offset']
            assert 0 <= offset and offset+len(data) <= len(actual)
            actual[offset:offset+len(data)] = data
        assert actual == windows[key]
        final_hashes['%s:%s' % key] = hashlib.sha256(actual).hexdigest()
    # Expected application state is independently explicit, beyond replay equality.
    assert [int.from_bytes(read(16, 7, 8192+o, 4), 'little') for o in (0, 4, 8, 12, 16, 20)] == [
        0x11223344, 0, 0x55667788, 0, 5, 0xdeadbeef]
    assert read(16, 7, 8216, 8) == bytes(8)
    assert read(16, 7, 8320, 16) == read(17, 8, 12288, 16) == read(17, 8, 12352, 16) == bytes(range(16))
    assert read(17, 8, 12308, 4) == bytes(4)
    for op_id, op in operations.items():
        row = local[op_id] if op['kind'] == 'local' else rows[op_id]
        for dependency in op['after']:
            parent_id = dependency['operation']; parent = local.get(parent_id, rows.get(parent_id))
            assert row['ready_ns'] >= parent[dependency['event']+'_ns']
        if op['kind'] == 'local':
            assert row['start_ns'] >= row['ready_ns']
            assert row['completed_ns'] == row['start_ns']+op['duration_ns']
    capture = result['packet_capture']
    # A full bounded capture must not hit its declared ceiling.
    assert capture['complete'] and capture['omitted_events'] == 0
    packet_rows = capture['events']; assert len(packet_rows) < protocol['packet_capture']['max_events']
    payload_column = capture['columns'].index('data_payload_bytes')
    by_uid = defaultdict(list)
    for p in packet_rows:
        by_uid[p[4]].append(p)
    for p in net['source_packets']:
        time, stream, src, dst, nic, sequence, length, uid = p
        assert any(e[0] == time and e[1:4] == [src, nic, 0] and e[payload_column] == length for e in by_uid[uid])
    for e in net['selective_repeat']['events']:
        if e['kind'] == 'payload_delivered':
            desc = stream_desc[e['stream']]
            assert any(p[1:4] == [desc[1], desc[3], 1] and p[0] <= e['time_ns'] for p in by_uid[e['packet_uid']])
    if protocol.get('faults'):
        fault = result['fault_injection']
        assert fault['drop_counts'] == [1, 1] and len(fault['events']) == 2
        for e in fault['events']:
            _, rule_index, node, port, uid = e
            rule = protocol['faults'][rule_index]
            assert (node, port) == (rule['node'], rule['port'])
            assert any(p[4] == uid and p[1] == node and p[2] == port for p in packet_rows)
    if 'session' in result:
        session = result['session']; assert session['finished']
        callbacks = {(e['operation'], e['event']): e for e in session['events']}
        assert len(callbacks) == len(session['events'])
        for op_id, row in rows.items():
            for name, field in [('submitted', 'ready_ns'), ('published', 'published_ns'),
                                ('applied', 'applied_ns'), ('request_acked', 'request_acked_ns'),
                                ('completed', 'completed_ns')]:
                assert callbacks[(op_id, name)]['time_ns'] == row[field]
            if row['verb'] != 'put':
                response = callbacks[(op_id, 'response')]
                assert response['time_ns'] == row['response_ns']
                assert response['returned_value'] == row['returned_value']
                assert callbacks[(op_id, 'completed')]['returned_value'] == row['returned_value']
    return dict(passed=True, request_frames=len(request_frames), reply_frames=len(reply_frames),
                logical_transport_bytes=logical_bytes, transmitted_payload_bytes=source_bytes,
                retransmitted_payload_bytes=source_bytes-logical_bytes,
                retransmitted_ranges=repeated_ranges, finish_ns=result['finish_ns'],
                actual_delivery_events=sum(len(v) for v in delivery_times.values()),
                memory_events=len(events), packet_events=len(packet_rows), final_window_sha256=final_hashes,
                gpu_endpoints=128, active_ranks=[0, 1, 16, 17], real_routes=False,
                hardware_calibrated=False, full_moe_execution=False)


def net_ready(result):
    """Explicit complete recovery observation, not merely a success exit code."""
    recovery = result['transactions']['transport']['recovery']
    return recovery['complete'] and not recovery['failed'] and recovery['omitted_events'] == 0


def tamper_checks(result, work, protocol):
    mutations = []
    bad = deepcopy(result); bad['transactions']['operations'][1]['returned_value'] ^= 1
    mutations.append(('wrong-return', bad))
    bad = deepcopy(result); bad['transactions']['operations'][1]['completed_ns'] -= 1
    mutations.append(('early-retirement', bad))
    bad = deepcopy(result); bad['transactions']['local_events'][0]['conditions'][0]['bits'] = 64
    mutations.append(('wrong-wait-width', bad))
    bad = deepcopy(result); page = bad['transactions']['windows'][0]['pages'][0]
    data = bytearray.fromhex(page['hex']); data[0] ^= 1; page['hex'] = data.hex()
    mutations.append(('changed-neighbor', bad))
    bad = deepcopy(result)
    delivery = next(e for e in bad['transactions']['transport']['selective_repeat']['events'] if e['kind'] == 'payload_delivered')
    data = bytearray.fromhex(delivery['hex']); data[-1] ^= 1; delivery['hex'] = data.hex()
    mutations.append(('changed-delivered-byte', bad))
    bad = deepcopy(result); bad['packet_capture']['complete'] = False
    mutations.append(('incomplete-capture', bad))
    rejected = []
    for name, bad in mutations:
        try:
            audit(bad, work, protocol)
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError('Auditor accepted '+name)
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    work = json.loads((args.inputs/'work.json').read_bytes()); results = {}
    for name in ('normal', 'recovery', 'session-recovery'):
        protocol = json.loads((args.inputs/('normal.json' if name == 'normal' else 'recovery.json')).read_bytes())
        result = json.loads((args.cases/name/'run/result.json').read_bytes())
        results[name] = audit(result, work, protocol)
    assert len({json.dumps(r['final_window_sha256'], sort_keys=True) for r in results.values()}) == 1
    normal = json.loads((args.cases/'normal/run/result.json').read_bytes())
    normal_protocol = json.loads((args.inputs/'normal.json').read_bytes())
    summary = dict(passed=True, cases=results, final_windows_equal=True,
                   tamper_rejections=tamper_checks(normal, work, normal_protocol),
                   auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.output/'verification.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
