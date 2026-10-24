"""Reconstruct persistent byte callbacks and MRT1 effects from complete captures."""
import argparse
from copy import deepcopy
from collections import Counter, defaultdict, deque
import json
from pathlib import Path
import struct


def need(ok, why):
    if not ok:
        raise ValueError(why)


def check(topology, protocol, program, result, partition=False):
    transaction = 'transactions' in result
    transport = result['transactions']['transport'] if transaction else result['persistent_transport']
    streams = {s['id']: s for s in transport['streams']}
    capture = result['packet_capture']
    need(capture['complete'] and not capture['selection']['nodes'] and
         capture['selection']['start_ns'] == 0 and capture['selection']['end_ns'] >= result['finish_ns'],
         'partial capture')
    peers, links = {}, {}
    for link in topology['links']:
        a, b = (link['src'], link['src_port']), (link['dst'], link['dst_port'])
        peers[a], peers[b] = b, a
        links[a] = links[b] = link
    flying, counts = defaultdict(deque), Counter()
    received, acked, source_bytes, source_count = Counter(), Counter(), Counter(), Counter()
    receive_times, ack_times = defaultdict(list), defaultdict(list)
    source_payload = {(p['stream'], p['time_ns'], p['sequence']): bytes.fromhex(p['hex'])
                      for p in transport.get('source_payload_packets', [])}
    payload_by_uid, decoded = {}, []
    source_rows = []
    for event in capture['events']:
        time, node, port, kind, uid, sid, proto, seq, size, _, _, payload = event
        key = uid, proto, seq, size
        endpoint = node, port
        counts[kind] += 1
        if kind == 0:
            flying[peers[endpoint], key].append((time, links[endpoint]))
            if proto == 17 and sid in streams and node == streams[sid]['src']:
                need(port == streams[sid]['source_nic'], 'source NIC mismatch')
                source_count[sid] += 1
                source_bytes[sid] += payload
                source_rows.append([time, sid, node, streams[sid]['dst'], port, seq, payload, uid])
                if transaction:
                    body = source_payload[sid, time, seq]
                    need(len(body) == payload, 'source payload size mismatch')
                    payload_by_uid[uid] = body
        elif kind in (1, 2):
            need(flying[endpoint, key], 'arrival without physical TX')
            sent, link = flying[endpoint, key].popleft()
            need(abs(time-sent-link['delay_ns']-size*8*10**9/link['rate_bps']) <= 1,
                 'physical timing mismatch')
            if kind == 1 and sid in streams:
                stream = streams[sid]
                if proto == 17 and node == stream['dst']:
                    need(port == stream['destination_nic'], 'destination NIC mismatch')
                    if seq == received[sid]:
                        received[sid] += payload
                        receive_times[sid].append((received[sid], time))
                        if transaction:
                            body = payload_by_uid[uid]
                            magic, verb, ordinal, address, remote_key, length = struct.unpack('!IIQQII', body[:32])
                            need(magic == 0x4d525431 and len(body) == 32+length, 'invalid/split example MRT1 frame')
                            decoded.append(dict(stream=sid, time_ns=time, verb=verb, sequence=ordinal,
                                                address=address, key=remote_key, body=body[32:]))
                elif proto in (252, 253) and node == stream['src']:
                    need(seq <= received[sid], 'feedback beyond received prefix')
                    if seq > acked[sid]:
                        acked[sid] = seq
                        ack_times[sid].append((seq, time))
    need(not any(flying.values()) and counts[0] == counts[1]+counts[2], 'physical conservation failure')
    need(source_rows == transport['source_packets'], 'source packet records differ from device capture')
    for sid, stream in streams.items():
        need(stream['qp_creations'] == 1 and stream['source_data_packets'] == source_count[sid] and
             stream['source_payload_bytes'] == source_bytes[sid] and stream['snd_una'] == acked[sid],
             'QP/source summary mismatch')
    for work in transport['work']:
        sid, end = work['stream'], work['end']
        accepted = next((t for count, t in receive_times[sid] if count >= end), -1)
        acknowledged = next((t for count, t in ack_times[sid] if count >= end), -1)
        need(work['accepted_ns'] == accepted and work['acked_ns'] == acknowledged,
             'fabricated work callback')
        if acknowledged >= 0:
            need(work['published_ns'] <= accepted <= acknowledged, 'callback order mismatch')
    latest_update = max(f['time_ns']+f['route_update_delay_ns'] for f in protocol['link_failures']['events'])
    need(result['finish_ns'] >= latest_update and result['link_failures']['pending_events'] == 0,
         'terminated before configured network event')
    if partition:
        need(not result['passed'] and not transport['closed'] and result['completed_work'] == 1,
             'partition converted to global success')
        need(received[0] < 64000 and acked[0] < 64000 and acked[1] == 64000,
             'partition/healthy prefix mismatch')
        need(transport['recovery']['failures'] and transport['source_isolation']['healthy_registered_work_drained'],
             'missing source isolation/healthy drain')
    else:
        need(result['passed'] and transport['closed'] and transport['physical_drained'], 'run not completed')
        for sid, stream in streams.items():
            need(received[sid] == acked[sid] == stream['published_bytes'], 'incomplete stream')
    summary = dict(physical_tx=counts[0], physical_rx=counts[1], physical_drop=counts[2],
                   local_drop=counts[5], streams=len(streams), work=len(transport['work']),
                   source_packets=sum(source_count.values()), finish_ns=result['finish_ns'],
                   partition_preserved=partition)
    if transaction:
        connections = {c['id']: c for c in program['connections']}
        windows = {(w['owner'], w['key']): (w['address'], bytearray.fromhex(w['hex'])) for w in program['windows']}
        input_order = defaultdict(list)
        for op in program['operations']:
            input_order[op['connection']].append(op)
        expected_targets, old_values, replies = [], {}, {}
        for frame in decoded:
            connection = frame['stream']//2
            if frame['verb'] == 3:
                replies[connection, frame['sequence']] = (int.from_bytes(frame['body'], 'big'), frame['time_ns'])
                continue
            op = input_order[connection][frame['sequence']]
            need(frame['stream']%2 == 0 and frame['key'] == op['key'] and frame['address'] == op['address'],
                 'MRT1 target differs from input')
            expected_body = bytes.fromhex(op['hex']) if op['verb'] == 'put' else op['value'].to_bytes(8, 'big')
            need(frame['body'] == expected_body and frame['verb'] == (1 if op['verb'] == 'put' else 2),
                 'actual MRT1 body differs from operation')
            base, memory = windows[connections[connection]['dst'], frame['key']]
            offset, length = frame['address']-base, len(frame['body'])
            need(0 <= offset and offset+length <= len(memory), 'out-of-bounds MRT1')
            old = 0
            if frame['verb'] == 1:
                memory[offset:offset+length] = frame['body']
            else:
                old = int.from_bytes(memory[offset:offset+8], 'little')
                memory[offset:offset+8] = ((old+int.from_bytes(frame['body'], 'big')) % 2**64).to_bytes(8, 'little')
                old_values[connection, frame['sequence']] = old
            expected_targets.append(dict(address=frame['address'], body_hex=frame['body'].hex(), connection=connection,
                                         duplicate=False, key=frame['key'], old_value=old, order=len(expected_targets),
                                         sequence=frame['sequence'], time_ns=frame['time_ns'], verb=frame['verb']))
        actual = result['transactions']
        need(expected_targets == actual['target_events'] and len(expected_targets) == len(program['operations']),
             'target effects differ from delivered MRT1 frames')
        for window in actual['windows']:
            need(bytes(windows[window['owner'], window['key']][1]).hex() == window['hex'], 'wrong final memory')
        for op in actual['operations']:
            key = op['connection'], op['sequence']
            if op['verb'] == 'fetch_add':
                need(replies[key] == (old_values[key], op['response_ns']) and op['returned_value'] == old_values[key],
                     'reply does not match actual atomic old value/arrival')
            need(op['completed_ns'] == max(op['request_acked_ns'], op['response_ns']), 'premature transaction completion')
        summary.update(applied_frames=len(expected_targets), replies=len(replies),
                       final_atomic_value=int.from_bytes(windows[4, 7][1][:8], 'little'))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'examples', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    load = lambda path: json.loads(path.read_text())
    topology = load(a.stage/'queued-topology/topology.json')
    summaries = {}
    inputs = {}
    for name, protocol, program in [('persistent-failover', 'stream-rdma.json', 'stream-work.json'),
                                     ('future-event', 'future-rdma.json', 'future-work.json'),
                                     ('partition', 'partition-rdma.json', 'partition-work.json'),
                                     ('transaction-failover', 'stream-rdma.json', 'transaction-work.json')]:
        inputs[name] = (load(a.examples/protocol), load(a.examples/program), load(a.stage/name/'result.json'))
        summaries[name] = check(topology, *inputs[name], partition=name == 'partition')
    mutations = {}
    for name, case in [('fake_work_ack', 'persistent-failover'), ('early_finish', 'future-event'),
                        ('partition_success', 'partition'), ('changed_MRT1_body', 'transaction-failover'),
                        ('changed_atomic_memory', 'transaction-failover')]:
        protocol, program, result = deepcopy(inputs[case])
        if name == 'fake_work_ack':
            result['persistent_transport']['work'][0]['acked_ns'] -= 1
        elif name == 'early_finish':
            result['finish_ns'] = 12144
        elif name == 'partition_success':
            result['passed'] = True
        elif name == 'changed_MRT1_body':
            packet = result['transactions']['transport']['source_payload_packets'][0]
            packet['hex'] = packet['hex'][:-2]+'00'
        else:
            window = result['transactions']['windows'][0]
            window['hex'] = '0b'+window['hex'][2:]
        try:
            check(topology, protocol, program, result, partition=case == 'partition')
        except ValueError as error:
            mutations[name] = str(error)
        else:
            raise ValueError('auditor accepted '+name)
    a.output.write_text(json.dumps(summaries, indent=2, sort_keys=True)+'\n')
    a.output.with_name(a.output.stem+'-tampering.json').write_text(json.dumps(mutations, indent=2, sort_keys=True)+'\n')
