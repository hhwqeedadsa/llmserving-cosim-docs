"""Independent audit for the small, lossless, single-frame NIC-alias example.

Uses only the standard library. Actual endpoint packet capture determines target
NIC and application time; input operations never supply received data bytes.
Not a general MRT1/retransmission or hardware validator.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import struct


def need(ok, reason):
    if not ok:
        raise AssertionError(reason)


def audit(result, work):
    t = result['transactions']
    transport = t['transport']
    need(result['passed'] and result['drained'] and transport['physical_drained'], 'not drained')
    need(t['registration_model'] == work['registration_model'] and
         t['registrations'] == work['registrations'], 'registration identity')
    need(not t['active_source_leases'] and not t['active_local_memory_leases'], 'held leases')
    registrations = {(r['owner'], r['nic'], r['key']): r for r in work['registrations']}
    connections = {c['id']: c for c in work['connections']}
    storage = {}
    for w in work['windows']:
        data = bytearray.fromhex(w['hex']) if 'hex' in w else bytearray(w['bytes'])
        for segment in w.get('segments', []):
            raw = bytes.fromhex(segment['hex'])
            data[segment['offset']:segment['offset'] + len(raw)] = raw
        storage[w['owner'], w['key']] = w['address'], data

    def span(owner, key, address, size):
        need((owner, key) in storage, 'unknown physical storage')
        base, data = storage[owner, key]
        offset = address - base
        need(0 <= offset and offset + size <= len(data), 'physical range')
        return data, offset

    streams = {s['id']: s for s in transport['streams']}
    rows = [dict(zip(transport['source_packet_columns'], row)) for row in transport['source_packets']]
    payloads = transport['source_payload_packets']
    need(len(rows) == len(payloads), 'payload coverage')
    packets = {}
    for row, payload in zip(rows, payloads):
        raw = bytes.fromhex(payload['hex'])
        need(len(raw) == row['payload_bytes'] and len(raw) >= 32, 'packet length')
        need(all(payload[k] == row[k] for k in ('time_ns', 'stream', 'sequence')), 'payload association')
        magic, verb, sequence, address, key, size = struct.unpack('!IIQQII', raw[:32])
        need(magic == 0x4d525431 and len(raw) == 32 + size, 'MRT1 frame')
        need(row['packet_uid'] not in packets, 'fixture requires unique packet IDs')
        packets[row['packet_uid']] = row, (verb, sequence, address, key, raw[32:])
    operations = {o['id']: o for o in work['operations'] if o['kind'] == 'remote'}
    reported = {o['id']: o for o in t['operations']}
    expected_sequence = defaultdict(int)
    by_sequence = {}
    for operation in operations.values():
        c = operation['connection']
        by_sequence[c, expected_sequence[c]] = operation
        expected_sequence[c] += 1
    need(set(operations) == set(reported), 'operation coverage')
    capture = result['packet_capture']
    need(capture['complete'] and capture['selection']['nodes'] == [], 'capture coverage')
    wire = capture['wire_headers']
    need(len(wire['event_header_indices']) == len(capture['events']), 'wire coverage')
    transmitted = set()
    received = set()
    applied = []
    response_values = {}
    response_rx = {}
    ack_rx = defaultdict(list)
    decoded = {}
    prefixes = defaultdict(int)
    for index, values in enumerate(capture['events']):
        event = dict(zip(capture['columns'], values))
        header = dict(zip(wire['columns'], wire['headers'][wire['event_header_indices'][index]]))
        if event['event'] == 1 and event['ip_protocol'] == 252:
            for sid, stream in streams.items():
                if event['node'] == stream['src'] and header['ports_present'] and \
                   header['source_port'] == header['destination_port'] == stream['wire_port']:
                    need(event['port'] == stream['source_nic'], 'ACK received on wrong NIC')
                    ack_rx[sid].append((event['time_ns'], event['sequence']))
        if event['ip_protocol'] != 17 or event['packet_uid'] not in packets:
            continue
        uid = event['packet_uid']
        row, frame = packets[uid]
        sid = row['stream']
        stream = streams[sid]
        connection = connections[sid // 2]
        forward = sid % 2 == 0
        need(stream['src'] == (connection['src'] if forward else connection['dst']) and
             stream['dst'] == (connection['dst'] if forward else connection['src']) and
             stream['source_nic'] == (connection['source_nic'] if forward else connection['destination_nic']) and
             stream['destination_nic'] == (connection['destination_nic'] if forward else connection['source_nic']), 'stream binding')
        need(header['source_port'] == header['destination_port'] == stream['wire_port'] and
             event['sequence'] == row['sequence'], 'wire port/sequence')
        verb, seq, address, key, body = frame
        operation = by_sequence[sid // 2, seq]
        op = reported[operation['id']]
        if event['event'] == 0 and event['node'] == stream['src']:
            need(uid not in transmitted and event['port'] == row['nic'] == stream['source_nic'] and
                 row['src'] == stream['src'] and row['dst'] == stream['dst'] and
                 event['time_ns'] == row['time_ns'], 'actual source TX')
            transmitted.add(uid)
            if forward:
                need(verb == (1 if operation['verb'] == 'put' else 2) and
                     key == operation['key'] and address == operation['address'], 'request header')
                need(op['published_ns'] <= event['time_ns'], 'TX before publication')
                if verb == 1:
                    source = operation['source']
                    data, offset = span(stream['src'], source['key'], source['address'], source['bytes'])
                    need(body == data[offset:offset + source['bytes']], 'TX source bytes')
                    decoded[operation['id']] = body.hex()
                else:
                    need(body == struct.pack('!Q', operation['value']), 'atomic operand')
            else:
                need(verb == 3 and address == key == 0 and
                     body == struct.pack('!Q', response_values[sid // 2, seq]), 'actual atomic reply')
        if event['event'] != 1 or event['node'] != stream['dst']:
            continue
        need(uid in transmitted and uid not in received and event['port'] == stream['destination_nic'], 'actual endpoint RX NIC')
        need(row['sequence'] == prefixes[sid], 'contiguous single-frame fixture')
        prefixes[sid] += row['payload_bytes']
        received.add(uid)
        if not forward:
            response_rx[operation['id']] = event['time_ns']
            need(op['returned_value'] == response_values[sid // 2, seq], 'returned old value')
            continue
        reg = registrations.get((event['node'], event['port'], key))
        need(reg is not None, 'RX has no NIC/rkey authority')
        size = len(body) if verb == 1 else 8
        need(reg['address'] <= address and address + size <= reg['address'] + reg['bytes'], 'RX registration range')
        data, offset = span(event['node'], reg['window_key'], address, size)
        old = 0
        if verb == 1:
            data[offset:offset + size] = body
        else:
            need(verb == 2 and reg['allow_atomic'] and address % 8 == 0 and len(body) == 8, 'RX atomic permission')
            old = int.from_bytes(data[offset:offset + 8], 'little')
            value = (old + int.from_bytes(body, 'big')) % 2**64
            data[offset:offset + 8] = value.to_bytes(8, 'little')
            response_values[sid // 2, seq] = old
        applied.append(dict(time_ns=event['time_ns'], connection=sid // 2, sequence=seq,
                            verb=verb, key=key, address=address, body_hex=body.hex(),
                            destination_nic=event['port'], duplicate=False, old_value=old))
        need(op['applied_ns'] == event['time_ns'], 'target time')
    need(transmitted == received == set(packets), 'endpoint packet coverage')
    need(applied == [{k: e[k] for k in applied[0]} for e in t['target_events']], 'target event differs from actual RX')
    works = {(w['stream'], w['ticket']): w for w in transport['work']}
    for op in reported.values():
        sid = op['connection'] * 2
        frame_work = works[sid, op['ticket']]
        times = [time for time, seq in ack_rx[sid] if seq >= frame_work['end']]
        need(times and op['request_acked_ns'] == frame_work['acked_ns'] == min(times), 'request ACK from wire')
        need(op['response_ns'] == response_rx.get(op['id'], -1), 'reply RX time')
        need(op['completed_ns'] == max(op['request_acked_ns'], op['response_ns']), 'completion')
        for parent in operations[op['id']]['after']:
            need(parent['event'] == 'completed' and op['ready_ns'] >= reported[parent['operation']]['completed_ns'], 'dependency')
    source_captures = {e['operation']: e['hex'] for e in t['source_events'] if e['kind'] == 'captured'}
    need(source_captures == decoded, 'source event bytes')
    # This fixture has one final copy, after all network-dependent work. Do not
    # replay arbitrary local events after the network as if they were concurrent.
    locals_ = [o for o in work['operations'] if o['kind'] == 'local']
    need(len(locals_) <= 1 and len(result['local_work']) == len(locals_), 'local fixture shape')
    for operation in locals_:
        need(operation['id'] == work['operations'][-1]['id'], 'only final local copy supported')
        record = result['local_work'][0]
        need(record['id'] == operation['id'] and
             record['completed_ns'] == record['start_ns'] + operation['duration_ns'], 'local duration')
        need(record['start_ns'] >= max(o['completed_ns'] for o in reported.values()), 'copy before network completion')
        copy = operation['copy'];source = copy['source'];target = copy['target'];owner = operation['rank']
        data, offset = span(owner, source['key'], source['address'], source['bytes'])
        snapshot = bytes(data[offset:offset + source['bytes']])
        dst, pos = span(owner, target['key'], target['address'], len(snapshot))
        dst[pos:pos + len(snapshot)] = snapshot
        events = t['local_memory_events']
        need([e['kind'] for e in events] == ['acquired', 'captured', 'committed', 'released'], 'copy lease events')
        for event in events:
            need(event['operation'] == operation['id'] and
                 event['time_ns'] == record['start_ns' if event['kind'] in ('acquired', 'captured') else 'completed_ns'], 'copy event time')
            if 'hex' in event:
                need(event['hex'] == snapshot.hex(), 'copy snapshot/commit bytes')
    need(len(t['windows']) == len(storage), 'physical storage duplicated')
    for w in t['windows']:
        base, expected = storage[w['owner'], w['key']]
        actual = bytearray.fromhex(w['hex']) if 'hex' in w else bytearray(w['bytes'])
        for page in w.get('pages', []):
            raw = bytes.fromhex(page['hex']);actual[page['offset']:page['offset'] + len(raw)] = raw
        need(w['address'] == base and actual == expected, 'final physical bytes')
    return dict(schema_version='nic-registration-independent-audit/1', source_packets=len(rows),
                wire_payload_bytes=sum(r['payload_bytes'] for r in rows),
                target_applications=len(applied), atomic_replies=len(response_rx),
                physical_windows=len(storage), registrations=len(registrations),
                distinct_target_nics=sorted({(r['owner'], r['nic']) for r in work['registrations']}),
                actual_nic_wire_memory_consistent=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('result', 'work', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.result.read_text());work = json.loads(args.work.read_text())
    report = audit(raw, work)
    rejected = []
    tests = ['endpoint_nic', 'registration_mapping', 'target_memory', 'target_event']
    if report['atomic_replies']:
        tests.append('reply_payload')
    for kind in tests:
        value = deepcopy(raw)
        if kind == 'endpoint_nic':
            capture = value['packet_capture'];cols = capture['columns']
            row = next(r for r in capture['events'] if r[cols.index('event')] == 1 and
                       r[cols.index('ip_protocol')] == 17 and r[cols.index('node')] == 4)
            row[cols.index('port')] = 0
        elif kind == 'registration_mapping':
            value['transactions']['registrations'][0]['window_key'] = 7
        elif kind == 'target_memory':
            w = value['transactions']['windows'][-1]
            target = w if 'hex' in w else w['pages'][0]
            target['hex'] = 'ff' + target['hex'][2:]
        elif kind == 'target_event':
            value['transactions']['target_events'][0]['destination_nic'] = 0
        else:
            packet = next(p for p in value['transactions']['transport']['source_payload_packets'] if p['stream'] % 2)
            packet['hex'] = packet['hex'][:-2] + ('ff' if packet['hex'][-2:] != 'ff' else '00')
        try:
            audit(value, work)
        except (AssertionError, KeyError):
            rejected.append(kind)
        else:
            raise AssertionError('tampering accepted: ' + kind)
    report['tampering_rejected'] = rejected
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
