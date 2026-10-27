"""Offline functional checks; cache counters are not evidence of equivalence."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

from fixtures.modern_expert_costs.verify import verify as modern_verify


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def callbacks(root, result):
    lines = [json.loads(line) for line in (root/'control.jsonl').read_text().splitlines()]
    assert len(lines) % 2 == 0
    known, seen, events, registrations = {}, set(), [], []
    for i in range(0, len(lines), 2):
        incoming, outgoing = lines[i:i+2]
        assert incoming['direction'] == 'native' and outgoing['direction'] == 'frontend'
        event, command = incoming['message'], outgoing['message']
        assert event['type'] == 'DECIDE' and command['type'] == 'COMMIT'
        assert event['decision_id'] == command['decision_id'] == i//2
        assert event['now_ns'] == command['now_ns']
        for row in event['events']:
            key = row['operation'], row['event']
            assert row['operation'] in known and key not in seen
            assert row['time_ns'] <= event['now_ns']
            seen.add(key); events.append(row)
        for node in command['submissions']:
            assert node['id'] not in known; known[node['id']] = node
            registrations.append(dict(operation=node['id'], time_ns=event['now_ns'], decision_id=i//2))
    assert lines[-1]['message']['finish'] is True
    assert events == result['session']['events'] and registrations == result['session']['registrations']
    assert len(lines)//2 == result['session']['decisions'] <= 256
    names = dict(prepared='prepared_ns', submitted='ready_ns', published='published_ns', applied='applied_ns',
                 request_acked='request_acked_ns', response='response_ns', completed='completed_ns',
                 local_admitted='ready_ns', local_started='start_ns')
    rows = {n['id']: n for n in result['transactions']['operations']+result.get('local_work', [])}
    assert set(rows) == set(known)
    for row in events:
        assert row['time_ns'] == rows[row['operation']][names[row['event']]]
        if 'returned_value' in row:
            assert row['returned_value'] == rows[row['operation']]['returned_value']
    expected = set()
    local = {n['id'] for n in result.get('local_work', [])}
    for identity, row in rows.items():
        for event, field in names.items():
            allowed = event in ('local_admitted', 'local_started', 'completed') if identity in local else event not in ('local_admitted', 'local_started')
            if allowed and field in row and row[field] >= 0: expected.add((identity, event))
    assert expected == seen
    return dict(decisions=len(lines)//2, callbacks=len(events), nodes=len(known))


def transaction(root, result, cfg):
    """Independent small-program frame and memory replay; no production decoder."""
    tx = result['transactions']; net = tx['transport']
    assert cfg['schema_version'] == 'moe-rdma-transactions/4'
    assert tx['duplicate_requests'] == 0 and not result.get('local_work', [])
    specs = {n['id']: n for n in cfg['operations']}
    rows = {n['id']: n for n in tx['operations']}
    assert set(specs) == set(rows) == {0, 1}
    streams = {}; packets = net['source_packets']; payload = net['source_payload_packets']
    assert len(packets) == len(payload)
    mtu = load(root/'protocol.json')['payload_bytes']
    connections = {c['id']: c for c in cfg['connections']}
    for packet, captured in zip(packets, payload):
        stamp, stream, src, dst, nic, seq, size, uid = packet
        c = connections[stream//2]
        assert (src, dst, nic) == ((c['dst'], c['src'], c['destination_nic']) if stream%2 else
                                   (c['src'], c['dst'], c['source_nic']))
        raw = bytes.fromhex(captured['hex']); data = streams.setdefault(stream, bytearray())
        assert (stamp, stream, seq, size) == (captured['time_ns'], captured['stream'], captured['sequence'], len(raw))
        assert seq == len(data) and 0 < size <= mtu and uid >= 0
        data.extend(raw)
    frames = {}
    for stream, data in streams.items():
        offset = 0
        while offset < len(data):
            magic, verb, seq, address, key, size = struct.unpack_from('>IIQQII', data, offset)
            assert magic == 0x4d525431 and 0 < size <= 32768
            end = offset+32+size; body = bytes(data[offset+32:end]); assert len(body) == size
            assert (stream, seq) not in frames
            work = [w for w in net['work'] if w['stream'] == stream and w['begin'] == offset and w['end'] == end]
            assert len(work) == 1
            frames[stream, seq] = (verb, address, key, body, work[0]); offset = end
        assert offset == len(data)
    memory = {(w['owner'], w['key']): bytearray.fromhex(w['hex']) for w in cfg['windows']}
    windows = {(w['owner'], w['key']): w for w in cfg['windows']}
    expected_frames = set()
    for identity in (0, 1):
        op, row = specs[identity], rows[identity]
        c = connections[op['connection']]; stream = 2*c['id']; sequence = row['sequence']
        f = frames[stream, sequence]; expected_frames.add((stream, sequence))
        body = bytes.fromhex(op['hex']) if op['verb'] == 'put' else op['value'].to_bytes(8, 'big')
        assert f[:4] == (1 if op['verb'] == 'put' else 2, op['address'], op['key'], body)
        assert row['ready_ns'] <= row['published_ns'] <= row['applied_ns'] < row['request_acked_ns'] <= row['completed_ns']
        assert f[4]['published_ns'] == row['published_ns'] and f[4]['accepted_ns'] == row['applied_ns']
        assert f[4]['acked_ns'] == row['request_acked_ns']
        key = c['dst'], op['key']; begin = op['address']-windows[key]['address']
        if op['verb'] == 'fetch_add':
            old = int.from_bytes(memory[key][begin:begin+8], 'little')
            assert old == row['returned_value'] == 7
            memory[key][begin:begin+8] = ((old+op['value']) % 2**64).to_bytes(8, 'little')
            response = frames[stream+1, sequence]; expected_frames.add((stream+1, sequence))
            assert response[:4] == (3, 0, 0, old.to_bytes(8, 'big'))
            assert response[4]['accepted_ns'] == row['response_ns'] <= row['completed_ns']
        else:
            memory[key][begin:begin+len(body)] = body
            assert row['response_ns'] == -1
        assert row['completed_ns'] == max(row['request_acked_ns'], row['response_ns'])
        for d in op['after']:
            assert row['ready_ns'] == rows[d['operation']][{'completed': 'completed_ns'}[d['event']]]
    assert expected_frames == set(frames)
    assert len(tx['target_events']) == 2
    effects = {(e['connection'], e['sequence']): e for e in tx['target_events']}
    assert set(effects) == {(r['connection'], r['sequence']) for r in rows.values()}
    for row in rows.values():
        effect = effects[row['connection'], row['sequence']]
        assert not effect['duplicate'] and effect['time_ns'] == row['applied_ns']
        frame = frames[2*row['connection'], row['sequence']]
        assert (effect['verb'], effect['address'], effect['key'], effect['body_hex']) == (
            frame[0], frame[1], frame[2], frame[3].hex())
    assert all(bytes.fromhex(w['hex']) == memory[w['owner'], w['key']] for w in tx['windows'])
    assert all(s['qp_creations'] == 1 and s['snd_nxt'] == s['snd_una'] == s['submitted_bytes'] for s in net['streams'])
    return dict(operations=2, frames=3, source_packets=len(packets), source_bytes=sum(map(len, streams.values())),
                actual_returned_value=7, final_atomic_value=10, final_put_value=107)


def verify(root, cached, overrides=None):
    def read(name): return overrides[name] if overrides and name in overrides else load(root/name)
    result = read('result.json'); cfg = read('input.json')
    assert result['passed'] and result['drained'] and result['session']['finished']
    assert result['native_network_executed'] and result['transactions']['transport']['physical_drained']
    assert result['packet_capture']['complete'] and result['packet_capture']['omitted_events'] == 0
    calls = callbacks(root, result)
    if cached:
        counters = read('static-validation.json')
        assert counters == dict(schema_version='moe-static-stream-validation/1', host_only=True,
            calls=calls['decisions']+1, hits=calls['decisions'], validations=1, unkeyable=0, entries=1)
    else:
        assert not (root/'static-validation.json').exists(); counters = None
    app = modern_verify(root, overrides) if cfg['schema_version'].startswith('moe-modern') else transaction(root, result, cfg)
    return dict(passed=True, cached=cached, counters=counters, delivery=calls, application=app)


def compare(left, right, *, migrated=False):
    a, b = load(left/'result.json'), load(right/'result.json')
    if migrated:
        for value in (a, b):
            native = [p for p in value['loaded_libraries'] if Path(p).name.startswith('libns3.')]
            assert len(native) == 8
            value['loaded_libraries'] = dict(native=sorted(Path(p).name for p in native),
                system=[p for p in value['loaded_libraries'] if not Path(p).name.startswith('libns3.')])
    assert a == b, 'complete native result differs'
    assert (left/'control.jsonl').read_bytes() == (right/'control.jsonl').read_bytes(), 'control differs'
    modern = (left/'modern-execution.json').exists()
    for name in ('initial.json', 'input.json', 'protocol.json', 'topology.json')+(
            ('modern-execution.json', 'modern-plan.json') if modern else ()):
        assert (left/name).read_bytes() == (right/name).read_bytes(), name+' differs'
    return dict(passed=True, whole_result_equal=True, control_byte_identical=True,
                modern=modern, migrated_library_roots_and_enumeration_only=migrated)


def corruptions(root):
    checks = []
    bad = load(root/'result.json'); bad['session']['events'][0]['time_ns'] += 1
    checks.append(('actual_callback', {'result.json': bad}))
    bad = load(root/'result.json'); packet = bad['transactions']['transport']['source_payload_packets'][0]
    packet['hex'] = ('00' if packet['hex'][:2] != '00' else 'ff')+packet['hex'][2:]
    checks.append(('actual_payload', {'result.json': bad}))
    bad = load(root/'static-validation.json'); bad['hits'] += 1
    checks.append(('cache_counter', {'static-validation.json': bad}))
    bad = load(root/'result.json'); bad['transactions']['operations'][0]['completed_ns'] += 1
    checks.append(('transaction_completion', {'result.json': bad}))
    for name, changed in checks:
        try: verify(root, True, changed)
        except (AssertionError, ValueError, KeyError, struct.error): continue
        raise AssertionError('corruption accepted: '+name)
    return [name for name, _ in checks]


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cached', action='store_true'); p.add_argument('--corruptions', action='store_true')
    a = p.parse_args(); assert not a.output.exists()
    report = verify(a.run, a.cached)
    if a.corruptions: report['corruptions_rejected'] = corruptions(a.run)
    save(a.output, report)
