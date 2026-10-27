"""Independent bytes, leases and shared-port schedule oracle; no simulator imports."""
import argparse
from copy import deepcopy
import hashlib
import heapq
import json
from pathlib import Path
import struct


def load(p): return json.loads(p.read_bytes())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def schedule(model, transfers):
    """Enumerate source serialization and destination arrival reservations."""
    free, links, arrivals, finishes = {}, {}, [], {}
    serial = 0
    def reserve(gpu, direction, arrival, size):
        key = gpu, direction
        begin = max(arrival, free.get(key, 0))
        duration = (size*8000000000+model['rate_bps']-1)//model['rate_bps']
        free[key] = begin+duration
        row = links.setdefault(key, dict(payload_bytes=0, busy_ns=0, max_wait_ns=0, chunks=0))
        row['payload_bytes'] += size; row['busy_ns'] += duration; row['chunks'] += 1
        row['max_wait_ns'] = max(row['max_wait_ns'], begin-arrival)
        return begin+duration
    for identity, src, dst, size, start in transfers:
        for offset in range(0, size, model['chunk_bytes']):
            chunk = min(model['chunk_bytes'], size-offset)
            end = reserve(src, 'to_switch', start, chunk)
            heapq.heappush(arrivals, (end+model['delay_ns'], serial, identity, dst, chunk))
            serial += 1
    while arrivals:
        arrival, _, identity, dst, chunk = heapq.heappop(arrivals)
        end = reserve(dst, 'from_switch', arrival, chunk)+model['delay_ns']
        finishes[identity] = max(finishes.get(identity, 0), end)
    return finishes, links


def verify(result, config, topology, protocol):
    assert result['passed'] and result['native_network_executed'] and result['drained']
    tx, ops = result['transactions'], {op['id']: op for op in config['operations']}
    net = tx['transport']; connections = {c['id']: c for c in config['connections']}
    assert net['physical_drained'] and net['closed'] and net['inflight_packets'] == 0
    assert not tx['hardware_calibrated'] and not tx['gpu_visibility_modeled'] and not tx['roce_wire_equivalent']
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    assert result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
    assert not result['pending_operations'] and not result['pending_local_work']
    remote = {row['id']: row for row in tx['operations']}
    local = {row['id']: row for row in result['local_work']}
    assert set(remote) == {i for i in ops if ops[i]['kind'] == 'remote'}
    assert set(local) == {i for i in ops if ops[i]['kind'] == 'local'}
    assert result['completed_operations'] == len(remote) and result['completed_local_work'] == len(local)
    for identity, op in ops.items():
        row = local[identity] if identity in local else remote[identity]
        for dependency in op['after']:
            parent = dependency['operation']
            source = local[parent] if parent in local else remote[parent]
            assert row['ready_ns'] >= source[dependency['event']+'_ns'] >= 0
    memory, bases = {}, {}
    for w in config['windows']:
        key = w['owner'], w['key']; bases[key] = w['address']; data = bytearray(w['bytes'])
        for segment in w['segments']:
            value = bytes.fromhex(segment['hex']); start = segment['offset']; data[start:start+len(value)] = value
        memory[key] = data
    def span(r):
        key = r['owner'], r['key']; begin = r['address']-bases[key]
        assert 0 <= begin < begin+r['bytes'] <= len(memory[key])
        return key, begin, begin+r['bytes']
    def overlap(a, b): return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]
    def read(r):
        key, a, b = span(r); return bytes(memory[key][a:b])
    def write(r, body):
        key, a, b = span(r); assert len(body) == b-a; memory[key][a:b] = body
    reads, writes, captured, committed, remote_bodies = {}, {}, {}, set(), {}
    events = [(e['order'], kind, e) for field, kind in (
        ('local_memory_events', 'local'), ('source_events', 'source'), ('target_events', 'target')) for e in tx[field]]
    assert sorted(order for order, _, _ in events) == list(range(len(events)))
    last_time = -1
    bindings = {(r['connection'], r['sequence']): identity for identity, r in remote.items()}
    for _, kind, e in sorted(events):
        assert e['time_ns'] >= last_time; last_time = e['time_ns']
        if kind == 'target':
            identity = bindings[e['connection'], e['sequence']]; op = ops[identity]
            assert e['time_ns'] == remote[identity]['applied_ns'] and not e['duplicate']
            assert e['verb'] == 1 and e['address'] == op['address'] and e['key'] == op['key']
            body = bytes.fromhex(e['body_hex']); assert body == remote_bodies[identity]
            target = dict(owner=connections[e['connection']]['dst'], key=op['key'], address=op['address'], bytes=len(body))
            assert all(not overlap(span(target), r) for r in (*reads.values(), *writes.values()))
            write(target, body); continue
        identity = e['operation']; op = ops[identity]; tag = kind, identity
        if kind == 'local':
            source = dict(owner=op['rank'], **op['copy']['source'])
            target = dict(owner=op['copy']['target'].get('owner', op['rank']), key=op['copy']['target']['key'],
                          address=op['copy']['target']['address'], bytes=source['bytes'])
            assert e['source'] == source and e['target'] == target
        else:
            source = dict(owner=connections[op['connection']]['src'], **op['source'])
            assert all(e[k] == v for k, v in source.items())
        if e['kind'] == 'acquired':
            assert tag not in reads and all(not overlap(span(source), r) for r in writes.values())
            if kind == 'local':
                assert all(not overlap(span(target), r) for r in (*reads.values(), *writes.values()))
                writes[tag] = span(target)
            reads[tag] = span(source)
        elif e['kind'] == 'captured':
            assert tag in reads and tag not in captured
            captured[tag] = bytes.fromhex(e['hex']); assert captured[tag] == read(source)
            assert e['time_ns'] == (local[identity]['start_ns'] if kind == 'local' else remote[identity]['published_ns'])
            if kind == 'source': remote_bodies[identity] = captured[tag]
        elif e['kind'] == 'committed':
            assert kind == 'local' and tag in writes and tag not in committed
            assert bytes.fromhex(e['hex']) == captured[tag] and e['time_ns'] == local[identity]['completed_ns']
            write(target, captured[tag]); committed.add(tag)
        else:
            assert e['kind'] == 'released' and tag in reads
            assert e['time_ns'] == (local[identity]['completed_ns'] if kind == 'local' else remote[identity]['completed_ns'])
            if kind == 'local': assert tag in committed; del writes[tag]
            del reads[tag]
    assert not reads and not writes and len(committed) == len(local)
    for w in tx['windows']:
        key = w['owner'], w['key']; actual = bytearray(w['bytes'])
        for page in w['pages']:
            body = bytes.fromhex(page['hex']); actual[page['offset']:page['offset']+len(body)] = body
        assert actual == memory[key]
    expected_frames = {}
    for identity, row in remote.items():
        op = ops[identity]; body = remote_bodies[identity]
        expected_frames[2*row['connection']] = struct.pack('>IIQQII', 0x4d525431, 1, row['sequence'],
                                                         op['address'], op['key'], len(body))+body
    observed = {}; assert len(net['source_packets']) == len(net['source_payload_packets']) == len(remote)
    for packet, payload in zip(net['source_packets'], net['source_payload_packets']):
        stamp, stream, src, dst, nic, seq, size, uid = packet
        c = connections[stream//2]; body = bytes.fromhex(payload['hex'])
        assert (src, dst, nic) == (c['src'], c['dst'], c['source_nic']) and stream%2 == 0
        assert (payload['time_ns'], payload['stream'], payload['sequence'], len(body)) == (stamp, stream, seq, size)
        assert uid >= 0 and seq == 0 and size <= protocol['payload_bytes'] and stream not in observed
        assert body == expected_frames[stream]; observed[stream] = body
    assert observed == expected_frames
    capture = result['packet_capture']; columns = capture['columns']
    wire = capture['wire_headers']; assert len(wire['event_header_indices']) == len(capture['events'])
    physical = []
    for event, index in zip(capture['events'], wire['event_header_indices']):
        row = dict(zip(columns, event)); header = dict(zip(wire['columns'], wire['headers'][index]))
        assert row['packet_uid'] == header['packet_uid'] and row['ip_protocol'] == header['ip_protocol']
        row.update(header); physical.append(row)
    ports = {}
    for link in topology['links']:
        for src,dst in (('src','dst'),('dst','src')):
            ports[link[src],link[src+'_port']] = (link[dst],link[dst+'_port'],link['rate_bps'],link['delay_ns'])
    transmissions = [p for p in physical if p['event'] == capture['event_codes']['tx']]
    receives = [p for p in physical if p['event'] == capture['event_codes']['rx']]
    assert len(transmissions) == len(receives)
    unmatched = set(range(len(receives)))
    wire_fields = ('packet_uid','sequence','model_bytes','ip_protocol','ip_src','ip_dst','source_port','destination_port')
    for packet in transmissions:
        peer,port,rate,delay = ports[packet['node'],packet['port']]
        end = packet['time_ns']+(packet['model_bytes']*8000000000+rate-1)//rate+delay
        matches = [i for i in unmatched if (receives[i]['node'],receives[i]['port'],receives[i]['time_ns']) == (peer,port,end)
                   and all(receives[i][key] == packet[key] for key in wire_fields)]
        assert len(matches) == 1; unmatched.remove(matches[0])
    assert not unmatched
    for identity,row in remote.items():
        c=connections[row['connection']];stream=2*c['id'];frame=observed[stream]
        work, = [w for w in net['work'] if w['stream']==stream]
        assert (work['begin'],work['end'],work['bytes']) == (0,len(frame),len(frame))
        assert work['ready_ns'] == row['ready_ns'] and work['published_ns'] == row['published_ns']
        assert work['accepted_ns'] == row['applied_ns'] and work['acked_ns'] == row['request_acked_ns'] == row['completed_ns']
        packet, = [p for p in net['source_packets'] if p[1]==stream]
        sent, = [p for p in transmissions if p['node']==c['src'] and p['ip_protocol']==17]
        arrived, = [p for p in receives if p['node']==c['dst'] and p['ip_protocol']==17]
        acked, = [p for p in receives if p['node']==c['src'] and p['ip_protocol']==252]
        assert (sent['time_ns'],sent['port'],sent['packet_uid'],sent['data_payload_bytes']) == (packet[0],c['source_nic'],packet[7],len(frame))
        assert sent['source_port'] == sent['destination_port'] == c['request_port']
        assert arrived['time_ns'] == row['applied_ns'] and arrived['packet_uid'] == packet[7]
        assert acked['time_ns'] == row['request_acked_ns'] and acked['sequence'] == len(frame)
    assert max(p['time_ns'] for p in receives) == result['finish_ns']
    peers = [op for op in ops.values() if 'owner' in op.get('copy', {}).get('target', {})]
    if peers:
        assert tx['peer_copy_model'] == dict(mode='topology_intra_host_crossbar/1', physical_rdma_packets=False,
                                           gpu_coherence_modeled=False, hardware_calibrated=False)
        transfers = [(op['id'], op['rank'], op['copy']['target']['owner'], op['copy']['source']['bytes'],
                      local[op['id']]['start_ns']) for op in peers]
        finishes, links = schedule(topology['intra_host'], transfers)
        messages = {m['id']-2147483648: m for m in result['messages']}
        assert set(messages) == {op['id'] for op in peers}
        for op in peers:
            identity = op['id']; row = local[identity]; message = messages[identity]
            assert row['duration_ns'] == 0 and row['duration_source'] == 'topology_intra_host_crossbar/1'
            assert row['completed_ns'] == finishes[identity] == message['receive_ready_ns']
            assert (message['src'], message['dst'], message['bytes']) == (op['rank'], op['copy']['target']['owner'], op['copy']['source']['bytes'])
            assert message['source_data_packets'] == 0 and message['first_packet_path'] == []
            assert message['transport'] == 'intra_host_chunked_crossbar'
            assert message['completion_kind'] == 'local_data_ready_no_RDMA_ACK'
        assert len(result['intra_host_links']) == len(links)
        for row in result['intra_host_links']:
            assert all(row[k] == v for k, v in links[row['gpu'], row['direction']].items())
        assert (local[0]['completed_ns'], local[1]['completed_ns']) == (276, 340)
        assert local[3]['completed_ns']-local[3]['start_ns'] == 340
        assert local[3]['start_ns'] == remote[2]['applied_ns']
        assert remote[4]['published_ns'] == local[3]['completed_ns']
        assert memory[0, 1][256:512] == bytes([0x31])*128+bytes([0x62])*128
    else:
        assert not result['messages'] and 'peer_copy_model' not in tx
        assert local[0]['completed_ns']-local[0]['start_ns'] == 123
    return dict(passed=True, remote_operations=len(remote), local_copies=len(local), peer_copies=len(peers),
                source_packets=len(observed), mrt1_bytes=sum(map(len, observed.values())),
                peer_payload_bytes=sum(op['copy']['source']['bytes'] for op in peers),
                memory_events=len(events), final_memory_sha256={str(k): hashlib.sha256(v).hexdigest() for k,v in sorted(memory.items())},
                physical_hop_transmissions=len(transmissions), physical_hops_and_receive_ack_callbacks_verified=True,
                shared_port_schedule_verified=bool(peers), byte_continuity_verified=True, hardware_calibrated=False)


def corruptions(result, config, topology, protocol):
    changes = {
        'early-peer-completion': lambda r: r['local_work'][0].update(completed_ns=275),
        'wrong-delivered-byte': lambda r: next(e for e in r['transactions']['local_memory_events'] if e['kind']=='committed' and e['operation']==0).update(hex='00'*128),
        'early-source-release': lambda r: next(e for e in r['transactions']['source_events'] if e['kind']=='released').update(time_ns=0),
        'wire-payload': lambda r: r['transactions']['transport']['source_payload_packets'][0].update(hex='00'*288),
        'hidden-local-packet': lambda r: r['messages'][0].update(source_data_packets=1),
        'missing-port-wait': lambda r: next(e for e in r['intra_host_links'] if e['gpu']==2 and e['direction']=='from_switch').update(max_wait_ns=0),
        'physical-source-port': lambda r: r['packet_capture']['events'][0].__setitem__(2,1024),
    }
    for name, change in changes.items():
        altered = deepcopy(result); change(altered)
        try: verify(altered, config, topology, protocol)
        except (AssertionError, KeyError, ValueError): continue
        raise AssertionError('corruption escaped: '+name)
    return sorted(changes)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('result', 'config', 'topology', 'protocol', 'output'): p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--corruptions', action='store_true'); a = p.parse_args()
    values = [load(getattr(a, name)) for name in ('result','config','topology','protocol')]
    report = verify(*values)
    if a.corruptions: report['corruptions_rejected'] = corruptions(*values)
    assert not a.output.exists(); save(a.output, report)
    print(json.dumps(report, ensure_ascii=False))
