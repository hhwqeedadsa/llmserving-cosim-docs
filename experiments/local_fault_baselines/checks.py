"""Graph/byte oracle derived from causal_fault at 7077530; per-condition schedules."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def refs(values): return {(v['work'], v['event']) for v in values}


def check_case(waves, work, mapping, r, summary, protocol):
    t = r['persistent_transport']; c = r['packet_capture']; sr = t.get('selective_repeat')
    assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
    assert c['complete'] and not c['omitted_events'] and c['selection']['nodes'] == []
    assert c['selection']['start_ns'] == 0 and c['selection']['end_ns'] > r['finish_ns']
    assert t['execution_limits'] == work['limits']
    ops = {v['id']: v for v in work['work']}; remote = {v['id']: v for v in t['work']}
    local = {v['id']: v for v in r['local_work']}; rows = dict(remote)
    rows.update(local)
    assert not set(remote)&set(local) and set(rows) == set(ops) and len(rows) == r['completed_work']
    streams = {v['id']: v for v in work['streams']}; native = {v['id']: v for v in t['streams']}
    totals = {sid: sum(v['bytes'] for v in remote.values() if v['stream'] == sid) for sid in streams}
    assert max(totals.values()) == 444416 < 522289
    assert bool(sr) == (work['transport_recovery']['mode'] == 'selective_repeat/1')
    if sr: assert sr['receive_buffer_bytes'] == 524288 and sr['inflight_bytes'] == 522289
    prefixes = Counter(); confirmed = Counter(); highs = Counter(); used = Counter(); peaks = Counter()
    masks = {sid: bytearray(n) for sid, n in totals.items()}
    known = {sid: bytearray(n) for sid, n in totals.items()}
    progress = defaultdict(list); acks = defaultdict(list); source = []; source_bytes = Counter(); source_model = Counter()
    selective_data = {v['packet_uid']: v for v in sr['events'] if v['kind'] == 'data_received'} if sr else {}
    generated = {v['packet_uid']: v for v in sr['events'] if v['kind'] == 'feedback_generated'} if sr else {}
    received = {v['packet_uid']: v for v in sr['events'] if v['kind'] == 'feedback_received'} if sr else {}
    seen_data = set(); seen_generated = set(); seen_feedback = set()
    ports = {(v['node'], v['port']): v for v in r['ports']}; reverse = {(v['node'], v['peer']): v for v in r['ports']}
    physical = defaultdict(deque); fifo = defaultdict(deque); qbytes = Counter(); area = Counter(); last = Counter(); peak = Counter()
    physical_counts = Counter(); port_bytes = Counter(); data_port_bytes = Counter(); drops = Counter(); source_count = Counter()
    wire = c['wire_headers']; assert len(wire['event_header_indices']) == len(c['events'])
    packet_headers = [dict(zip(wire['columns'], v)) for v in wire['headers']]
    for raw, index in zip(c['events'], wire['event_header_indices']):
        e = dict(zip(c['columns'], raw)); h = packet_headers[index]
        n, port, kind, now = (e[k] for k in ('node', 'port', 'event', 'time_ns'))
        uid, proto, seq, size, sid = (e[k] for k in ('packet_uid', 'ip_protocol', 'sequence', 'model_bytes', 'message_id'))
        assert uid == h['packet_uid'] and proto == h['ip_protocol'] and 0 <= now <= r['finish_ns']
        peer = ports[n, port]['peer']; ident = uid, proto, seq, size
        if kind == 0:
            physical[n, peer, ident].append(now); physical_counts[0] += 1; port_bytes[n, port] += size
            if proto == 17: data_port_bytes[n, port] += size
            if n < r['gpu_count']: source_model[proto] += size
        elif kind in (1, 2):
            assert physical[peer, n, ident], 'unpaired physical arrival'
            sent = physical[peer, n, ident].popleft(); sender = reverse[peer, n]
            assert now == sent+round(size*8*10**9/sender['rate_bps'])+sender['delay_ns'], 'physical flight time'
            physical_counts[kind] += 1
        if kind in (2, 5) and sid in streams: drops[sid, proto] += 1
        if kind in (3, 4):
            key = n, port; area[key] += qbytes[key]*(now-last[key]); last[key] = now
            if kind == 3:
                qbytes[key] += size; assert qbytes[key] == e['egress_queue_bytes']
            else:
                assert qbytes[key] == e['egress_queue_bytes']; qbytes[key] -= size
            assert qbytes[key] >= 0; peak[key] = max(peak[key], qbytes[key])
            if proto == 17:
                if kind == 3: fifo[key].append(ident)
                else: assert fifo[key].popleft() == ident, 'data FIFO'
        if proto not in (17, 252, 253): continue
        s = streams[sid]
        assert h['source_port'] == h['destination_port'] == s['wire_port']
        assert (h['ip_src'], h['ip_dst']) == tuple(0x0b000001+v*32 for v in
            ((s['src'], s['dst']) if proto == 17 else (s['dst'], s['src'])))
        if proto == 17 and kind == 0 and n == s['src']:
            end = seq+e['data_payload_bytes']; assert 0 <= seq < end <= totals[sid]
            if sr:
                assert not any(known[sid][seq:end]), 'selective retransmitted known bytes'
                assert end-confirmed[sid] <= 522289, 'inflight budget'
            highs[sid] = max(highs[sid], end)
            source.append([now, sid, n, s['dst'], port, seq, e['data_payload_bytes'], uid])
            source_bytes[sid] += e['data_payload_bytes']; source_count[sid] += 1
            owners = [v for v in remote.values() if v['stream'] == sid and v['begin'] <= seq < end <= v['end']]
            assert len(owners) == 1 and now >= owners[0]['published_ns'], 'published owner'
        if proto == 17 and kind == 1 and n == s['dst']:
            end = seq+e['data_payload_bytes']; before = prefixes[sid]
            if sr:
                assert uid in selective_data and uid not in seen_data; seen_data.add(uid)
                missing = sum(not b for b in masks[sid][max(seq, before):end])
                assert used[sid]+missing <= 524288
                masks[sid][max(seq, before):end] = b'\1'*max(0, end-max(seq, before)); used[sid] += missing
                while prefixes[sid] < totals[sid] and masks[sid][prefixes[sid]]:
                    masks[sid][prefixes[sid]] = 0; used[sid] -= 1; prefixes[sid] += 1
                peaks[sid] = max(peaks[sid], used[sid]); v = selective_data[uid]
                assert (v['stream'], v['time_ns'], v['sequence'], v['receiver_prefix'], v['reorder_bytes']) == (sid, now, seq, prefixes[sid], used[sid])
                assert (v['sack_begin'], v['sack_end'], v['sack_valid']) == (seq, end, True)
            elif seq == prefixes[sid]: prefixes[sid] += e['data_payload_bytes']
            progress[sid].append((now, prefixes[sid]))
        if proto in (252, 253) and kind == 0 and n == s['dst']:
            assert size == (50 if sr else 48)
            if sr:
                assert uid in generated and uid not in seen_generated; seen_generated.add(uid); v = generated[uid]
                magic, begin, end, valid = struct.unpack('!IIII', bytes.fromhex(v['extension_hex']))
                assert magic == 0x4d535231 and valid == 1 and v['stream'] == sid and v['time_ns'] <= now
                assert (begin, end, seq) == (v['sack_begin'], v['sack_end'], v['cumulative_ack'])
                d = next(x for x in selective_data.values() if x['stream'] == sid and x['time_ns'] == v['time_ns'])
                assert (begin, end, seq) == (d['sack_begin'], d['sack_end'], d['receiver_prefix'])
        if proto in (252, 253) and kind == 1 and n == s['src']:
            assert seq <= highs[sid]; confirmed[sid] = max(confirmed[sid], seq); acks[sid].append((now, confirmed[sid]))
            if sr:
                assert uid in received and uid in generated and uid not in seen_feedback; seen_feedback.add(uid)
                v = received[uid]; magic, begin, end, valid = struct.unpack('!IIII', bytes.fromhex(v['extension_hex']))
                assert magic == 0x4d535231 and valid == 1 and 0 <= begin < end <= highs[sid]
                assert v['extension_hex'] == generated[uid]['extension_hex'] and v['time_ns'] == now and v['cumulative_ack'] == seq
                known[sid][begin:end] = b'\1'*(end-begin); known[sid][:confirmed[sid]] = b'\1'*confirmed[sid]
    assert not any(physical.values()) and not any(fifo.values()) and not any(qbytes.values())
    assert physical_counts[0] == physical_counts[1]+physical_counts[2] == t['link_transmissions']
    assert source == t['source_packets']
    for key, p in ports.items():
        assert port_bytes[key] == p['model_tx_bytes'] and data_port_bytes[key] == p['data_tx_bytes']
        if key[0] >= r['gpu_count']:
            assert p['queue_metrics']['final_bytes'] == 0 and area[key] == p['queue_metrics']['area_byte_ns'] and peak[key] == p['queue_metrics']['peak_bytes']
    for sid, s in streams.items():
        end = 0
        for ticket, v in enumerate(sorted((v for v in remote.values() if v['stream'] == sid), key=lambda v: v['ticket'])):
            assert v['ticket'] == ticket and v['begin'] == end and v['end'] == end+v['bytes']; end = v['end']
            assert v['bytes'] == ops[v['id']]['bytes'] and v['published_ns'] == v['ready_ns']
            assert v['accepted_ns'] == next(now for now, prefix in progress[sid] if prefix >= end)
            assert v['acked_ns'] == next(now for now, prefix in acks[sid] if prefix >= end)
        assert end == prefixes[sid] == confirmed[sid] == totals[sid] == native[sid]['snd_una'] == native[sid]['submitted_bytes']
        assert native[sid]['qp_creations'] == 1 and source_bytes[sid] == native[sid]['source_payload_bytes']
    if sr:
        assert seen_data == set(selective_data) and seen_generated == set(generated) and seen_feedback == set(received)
        for v in sr['states']:
            sid = v['stream']; assert v['receiver_prefix'] == totals[sid] and v['peak_reorder_bytes'] == peaks[sid]
            assert not v['held_ranges'] and not v['source_sacked_ranges'] and not v['rejected_packets'] and not used[sid]
    def time(ref): return rows[ref[0]][ref[1]+'_ns']
    def end(rr): return max((time(x) for x in rr), default=0)
    for wid, op in ops.items():
        assert rows[wid]['ready_ns'] == end(refs(op['after'])), 'dependency readiness'
        if wid in local:
            v = rows[wid]; assert (v['rank'], v['duration_ns']) == (op['rank'], op['duration_ns'])
            assert v['completed_ns'] == v['start_ns']+v['duration_ns'] and v['start_ns'] >= v['ready_ns']
    for rank in (0, 2, 4, 6):
        until = 0
        for v in sorted((v for v in local.values() if v['rank'] == rank), key=lambda v: (v['start_ns'], v['duration_ns'] > 0, v['id'])):
            assert v['start_ns'] == max(until, v['ready_ns']), 'serial local execution'
            until = v['completed_ns']
    mnodes = {(v['graph_id'], v['rank'], v['node_id']): v for v in mapping['nodes']}
    assert len(mnodes) == len(mapping['nodes']) == 216
    retirement = {v['graph_id']: v for v in mapping['waves']}; used_ops = set(); previous = set(); group_ends = []
    for wave in waves:
        node_refs = {(rank, n['id']): refs(mnodes[wave['graph_id'], int(rank), n['id']]['completion'])
                     for rank, ns in wave['rank_nodes'].items() for n in ns}
        sends = {n['message']['id']: node_refs[rank, n['id']] for rank, ns in wave['rank_nodes'].items() for n in ns if n['kind'] == 'SEND'}
        allrefs = set().union(*node_refs.values())
        for rank, ns in wave['rank_nodes'].items():
            for n in ns:
                before = set().union(*(node_refs[rank, d] for d in n['deps'])) if n['deps'] else previous
                rr = node_refs[rank, n['id']]
                if n['kind'] == 'RECV':
                    sender = next(iter(sends[n['message']['id']]))
                    assert rr == before|{(sender[0], 'accepted')}, 'RECV conjunction'
                else:
                    assert len(rr) == 1; wid, event = next(iter(rr)); used_ops.add(wid)
                    assert refs(ops[wid]['after']) == before, 'original local prerequisites'
                    if n['kind'] == 'COMP':
                        assert event == 'completed' and ops[wid]['kind'] == 'local'
                        assert (ops[wid]['rank'], ops[wid]['duration_ns']) == (int(rank), n['duration_ns'])
                    else:
                        m = n['message']; assert event == 'acked' and ops[wid]['kind'] == 'write'
                        assert ops[wid]['bytes'] == m['bytes'] and (streams[ops[wid]['stream']]['src'], streams[ops[wid]['stream']]['dst']) == (m['src'], m['dst'])
        retire = retirement[wave['graph_id']]; joins = set(retire['join_work']); used_ops |= joins
        leaves = set(); pending = list(refs(retire['completion']))
        while pending:
            wid, event = pending.pop()
            if wid in joins:
                assert event == 'completed' and ops[wid]['kind'] == 'local' and ops[wid]['duration_ns'] == 0
                pending.extend(refs(ops[wid]['after']))
            else: leaves.add((wid, event))
        assert leaves == allrefs, 'all nodes count toward retirement'
        assert end(refs(retire['completion'])) == end(allrefs)
        group_ends.append(end(allrefs)); previous = refs(retire['completion'])
    assert used_ops == set(ops) and group_ends == [v['completion_ns'] for v in summary['windows']]
    assert group_ends[-1] == summary['group_completion_ns'] and r['finish_ns'] == summary['physical_finish_ns']
    assert sum(source_bytes.values()) == summary['tx_payload_bytes'] and sum(totals.values()) == summary['unique_payload_bytes'] == 2609152
    assert summary['source_model_bytes_by_protocol'] == {str(k): v for k, v in sorted(source_model.items())}
    for s in summary['streams']:
        sid = s['stream']; assert s['dropped_by_protocol'] == {str(p): v for (i, p), v in drops.items() if i == sid}
    # Independently check reported write witnesses and the entire tied frontier.
    labels = {}
    for wave in waves:
        for rank, ns in wave['rank_nodes'].items():
            for n in ns:
                if n['kind'] != 'RECV':
                    wid, _ = next(iter(refs(mnodes[wave['graph_id'], int(rank), n['id']]['completion'])))
                    labels[wid] = (wave['graph_id'], n['name'], int(rank))
    events = [dict(zip(c['columns'], e)) for e in c['events']]
    for reported in summary['writes']:
        wid = reported['work']; actual = remote[wid]; witness = reported['witness']; sid = actual['stream']
        assert (reported['graph_id'], reported['name'], reported['rank']) == labels[wid]
        for field in ('ready_ns', 'published_ns', 'accepted_ns', 'acked_ns'): assert reported[field] == actual[field]
        assert reported['accepted_after_ready_ns'] == actual['accepted_ns']-actual['ready_ns']
        assert reported['ack_after_ready_ns'] == actual['acked_ns']-actual['ready_ns']
        lineage = [e for e in events if e['ip_protocol'] == 17 and e['message_id'] == sid and
                   e['packet_uid'] == witness['packet_uid'] and e['sequence'] == witness['sequence']]
        flights = [e for e in lineage if e['event'] == 0]
        assert flights[0]['node'] == streams[sid]['src'] and flights[0]['time_ns'] == witness['source_tx_ns']
        destination = next(e for e in lineage if e['event'] == 1 and e['node'] == streams[sid]['dst'])
        assert destination['time_ns'] == actual['accepted_ns'] == witness['destination_rx_ns']
        assert all(ports[e['node'], e['port']]['peer'] == nxt['node'] for e, nxt in zip(flights, flights[1:]))
        assert ports[flights[-1]['node'], flights[-1]['port']]['peer'] == streams[sid]['dst']
        total_queue = 0
        for hop, reported_queue in zip(flights[1:], witness['queues']):
            assert (reported_queue['node'], reported_queue['port']) == (hop['node'], hop['port'])
            enq = next(e for e in lineage if e['event'] == 3 and e['node'] == hop['node'] and e['port'] == hop['port'])
            deq = next(e for e in lineage if e['event'] == 4 and e['node'] == hop['node'] and e['port'] == hop['port'])
            assert (reported_queue['enqueue_ns'], reported_queue['dequeue_ns']) == (enq['time_ns'], deq['time_ns'])
            assert reported_queue['wait_ns'] == deq['time_ns']-enq['time_ns']; total_queue += reported_queue['wait_ns']
        assert len(witness['queues']) == len(flights)-1 and total_queue == witness['queue_wait_ns']
        transit = sum(round(e['model_bytes']*8*10**9/ports[e['node'], e['port']]['rate_bps'])+ports[e['node'], e['port']]['delay_ns'] for e in flights)
        assert destination['time_ns']-flights[0]['time_ns'] == total_queue+transit
    assert {v['work'] for v in summary['writes']} == set(remote)
    for window in summary['windows']:
        frontier = window['frontier']; pending = list(refs(retirement[window['graph_id']]['completion'])); reached = set(); checked_writes = set()
        actual_constraints = {(v['work'], v['event']): refs(v['latest_predecessors']) for v in frontier['constraints']}
        while pending:
            wid, event = pending.pop()
            if (wid, event) in reached: continue
            reached.add((wid, event)); row = rows[wid]; parents = refs(ops[wid]['after']); ready = end(parents)
            active = {d for d in parents if time(d) == ready}
            if wid in local:
                if row['start_ns'] != ready: active = set()
                active |= {(v['id'], 'completed') for v in local.values() if v['rank'] == row['rank'] and v['id'] != wid and
                           v['duration_ns'] > 0 and v['completed_ns'] == row['start_ns']}
            else: checked_writes.add((wid, event))
            assert actual_constraints[wid, event] == active, 'all tied latest constraints'
            pending.extend(active)
        assert reached == set(actual_constraints) and checked_writes == {(v['work'], v['event']) for v in frontier['writes']}
        for v in frontier['writes']:
            row = rows[v['work']]; assert v['elapsed_to_event_ns'] == row[v['event']+'_ns']-row['ready_ns']
    assert summary['timeouts'] == sum(v['timeout_retransmissions'] for v in t['recovery']['states'])
    if 'link_failures' in r:
        lf = r['link_failures']; assert lf['route_epoch'] == 1 and lf['pending_events'] == 0
        declared = protocol['link_failures']['events'][0]
        assert [(v['kind'], v['time_ns']) for v in lf['events']] == [('link_down', declared['time_ns']), ('routes_updated', declared['time_ns']+declared['route_update_delay_ns'])]
    return dict(messages=len(remote), original_nodes=216, local_work=len(local), group_completion_ns=group_ends[-1],
        source_packets=len(source), physical_transmissions=physical_counts[0], physical_drops=physical_counts[2],
        packet_events=len(c['events']), window_never_binding=True, actual_callbacks=True, all_original_dependencies=True)


def verify_sources(repository, inputs):
    truth = load(inputs/'truth/source.json'); obs = inputs/'observed'; originals = {}
    for entry in truth['manifest']['files']:
        p = repository/entry['file']['path']; assert sha(p) == entry['file']['sha256']; originals[entry['file_id']] = load(p)
    waves = []
    for step in (0, 1):
        source = load(inputs/f'truth/g0.w{step}.json'); mapped = load(obs/f'g0.w{step}.json')
        def inverse(o):
            if isinstance(o, list): return [inverse(v) for v in o]
            if not isinstance(o, dict): return o
            d = {k: inverse(v) for k, v in o.items()}
            for key in ('src', 'dst', 'rank'):
                if key in d: assert d[key] % 2 == 0; d[key] //= 2
            if 'rank_ids' in d: d['rank_ids'] = [r//2 for r in d['rank_ids']]
            if 'rank_nodes' in d: d['rank_nodes'] = {str(int(k)//2): v for k, v in d['rank_nodes'].items()}
            return d
        assert inverse(mapped) == source
        for route in source['routes']:
            contributions = Counter(); layer = route['layer']
            for token in route['tokens']:
                assert token['source']['file_id'] == truth['manifest']['files'][token['rank']]['file_id']
                experts = originals[token['source']['file_id']][step+1][str(layer+3)][0]
                assert token['experts'] == experts
                for expert in experts: contributions[token['rank'], expert//64] += 1
            expected = {}
            for (src, dst), count in contributions.items():
                if src != dst:
                    expected['dispatch', src, dst] = count
                    expected['combine', dst, src] = count
            actual = {(m['phase'], m['src'], m['dst']): m['contributions'] for m in source['messages'] if m['layer'] == layer}
            assert actual == expected
            assert all(m['bytes'] == m['contributions']*7168*2 for m in source['messages'] if m['layer'] == layer)
        waves.append(mapped)
    return waves

