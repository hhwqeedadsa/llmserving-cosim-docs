"""Independent public-wave, actual endpoint packet and completion audit.

Standard library only; no compiler, scheduler or transport implementations.
"""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path


def need(ok, why):
    if not ok:
        raise AssertionError(why)


def refs(rows):
    return {(x['work'], x['event']) for x in rows}


def audit(waves, work, mapping, result):
    r, t = result, result['persistent_transport']
    need(r['passed'] and r['drained'] and t['closed'] and t['physical_drained'], 'drain')
    need(t['execution_limits'] == work['limits'], 'effective limits')
    ops = {x['id']: x for x in work['work']}
    remote = {x['id']: x for x in t['work']}
    local = {x['id']: x for x in r['local_work']}
    need(set(ops) == set(remote)|set(local) and not set(remote)&set(local), 'work coverage')
    need(r['completed_work'] == len(ops), 'all work completed')
    streams = {x['id']: x for x in work['streams']}
    wire = {x['wire_port']: x['id'] for x in work['streams']}
    packets = {x['packet_uid']: x for x in [dict(zip(t['source_packet_columns'], p)) for p in t['source_packets']]}
    cap = r['packet_capture']; headers = cap['wire_headers']
    full = cap['selection']['nodes'] == []
    need(cap['complete'] and not cap['omitted_events'] and (full or set(cap['selection']['nodes']) == set(range(r['gpu_count']))) and
         cap['selection']['start_ns'] == 0 and cap['selection']['end_ns'] > r['finish_ns'], 'full capture')
    need(len(cap['events']) == len(headers['event_header_indices']), 'header coverage')
    tx, rx, ack_tx, ack_rx, prefixes = set(), set(), {}, {}, defaultdict(int)
    held, accepted, confirmed = defaultdict(dict), defaultdict(list), defaultdict(list)
    physical = defaultdict(list); dropped = 0; physical_tx = 0; lost_data = set()
    endpoint_tx, endpoint_rx, cnp_peers, frames = {}, {}, {}, {}
    ports = {(x['node'], x['port']): x for x in r['ports']}
    for i, row in enumerate(cap['events']):
        e = dict(zip(cap['columns'], row)); h = dict(zip(headers['columns'], headers['headers'][headers['event_header_indices'][i]]))
        kind, uid, node = e['event'], e['packet_uid'], e['node']
        if kind not in (0, 1, 2):
            continue
        need(h['packet_uid'] == uid and e['ip_protocol'] == h['ip_protocol'], 'wire identity')
        p = ports[node, e['port']]; ident = uid, e['ip_protocol'], e['sequence'], e['model_bytes']
        if full:
            if kind == 0:
                physical[node, p['peer'], ident].append(e['time_ns']); physical_tx += 1
            else:
                queue = physical[p['peer'], node, ident]
                need(queue, 'physical receive without TX')
                when = queue.pop(0)
                need(e['time_ns'] == when+p['delay_ns']+round(e['model_bytes']*8*10**9/p['rate_bps']), 'physical link service')
        if kind == 2:
            dropped += 1
            if e['ip_protocol'] == 17:
                lost_data.add(uid)
            continue
        if node >= r['gpu_count']:
            continue
        proto = e['ip_protocol']
        if proto == 255:
            need((h['ip_src'], h['ip_dst']) in cnp_peers and not h['ports_present'], 'CNP endpoint addresses')
            sid = cnp_peers[h['ip_src'], h['ip_dst']]
        else:
            need(h['source_port'] in wire and h['destination_port'] == h['source_port'] and h['ports_present'], 'endpoint wire port')
            sid = wire[h['source_port']]
        s = streams[sid]
        if kind == 0:
            need(uid not in endpoint_tx, 'unique endpoint TX'); endpoint_tx[uid] = (e, sid)
        else:
            need(uid not in endpoint_rx, 'unique endpoint RX'); endpoint_rx[uid] = (e, sid)
        frames[uid] = e['model_bytes']
        if e['ip_protocol'] == 17:
            need(uid in packets, 'unrecorded source data')
            data = packets[uid]; start, end = data['sequence'], data['sequence']+data['payload_bytes']
            need(e['sequence'] == start and e['data_payload_bytes'] == data['payload_bytes'] and data['stream'] == sid and
                 (data['src'], data['dst'], data['nic']) == (s['src'], s['dst'], s['source_nic']), 'actual packet range')
            if kind == 0:
                need(uid not in tx and node == s['src'] and e['port'] == s['source_nic'] and e['time_ns'] == data['time_ns'], 'source packet/NIC')
                tx.add(uid)
                cnp_peers[h['ip_dst'], h['ip_src']] = sid
            else:
                need(uid not in rx and node == s['dst'] and e['port'] == s['destination_nic'], 'receive packet/NIC')
                rx.add(uid)
                if start == prefixes[sid]:
                    prefixes[sid] = end
                    while prefixes[sid] in held[sid]:
                        prefixes[sid] = held[sid].pop(prefixes[sid])
                elif start > prefixes[sid] and 'selective_repeat' in t:
                    held[sid][start] = end
                else:
                    need(start > prefixes[sid] or end <= prefixes[sid], 'partial duplicate')
                accepted[sid].append((e['time_ns'], prefixes[sid]))
        elif proto == 255:
            need(node == s['dst' if kind == 0 else 'src'] and e['port'] == s['destination_nic' if kind == 0 else 'source_nic'], 'CNP bound endpoints')
        else:
            need(e['ip_protocol'] in (252, 253), 'unexpected endpoint feedback')
            if kind == 0:
                need(node == s['dst'] and uid not in ack_tx, 'ACK source'); ack_tx[uid] = e
            else:
                need(node == s['src'] and uid not in ack_rx, 'ACK destination'); ack_rx[uid] = e
                confirmed[sid].append((e['time_ns'], e['sequence']))
    need(tx == set(packets) and rx == tx-lost_data and set(ack_tx) == set(ack_rx) and not any(physical.values()), 'packet conservation')
    need(set(endpoint_rx) == set(endpoint_tx)-lost_data, 'all endpoint packets accounted')
    for uid, (e, sid) in endpoint_rx.items():
        sent, ss = endpoint_tx[uid]
        need(ss == sid and sent['time_ns'] <= e['time_ns'] and sent['model_bytes'] == e['model_bytes'] and sent['ip_protocol'] == e['ip_protocol'], 'actual endpoint delivery')
    if not full:
        need(not dropped, 'endpoint-only audit does not reconstruct intermediate losses')
        adjacency = defaultdict(list)
        for port in r['ports']: adjacency[port['node']].append(port['peer'])
        distances = {}
        for dst in {s['dst'] for s in streams.values()}:
            d = {dst: 0}; q = deque([dst])
            while q:
                node = q.popleft()
                for peer in adjacency[node]:
                    if peer not in d:
                        d[peer] = d[node]+1
                        if peer >= r['gpu_count']: q.append(peer)
            distances[dst] = d
        data_bytes = control_bytes = 0
        for uid, (e, sid) in endpoint_tx.items():
            s = streams[sid]; hops = distances[s['dst']][s['src']]
            physical_tx += hops
            if e['ip_protocol'] == 17: data_bytes += hops*frames[uid]
            else: control_bytes += hops*frames[uid]
        need(sum(x['data_tx_bytes'] for x in r['ports']) == data_bytes and
             sum(x['control_tx_bytes'] for x in r['ports']) == control_bytes, 'shortest-hop all-port byte conservation')
    need(t['link_transmissions'] == physical_tx, 'physical transmission total')
    for uid, e in ack_rx.items():
        sent = ack_tx[uid]
        need(e['sequence'] == sent['sequence'] and e['time_ns'] >= sent['time_ns'], 'actual feedback')
    for sid, s in streams.items():
        rows = sorted((x for x in remote.values() if x['stream'] == sid), key=lambda x: x['ticket'])
        end = 0
        for ticket, x in enumerate(rows):
            need(x['ticket'] == ticket and x['begin'] == end and x['end'] == end+x['bytes'] and x['bytes'] == ops[x['id']]['bytes'], 'work byte boundaries')
            end = x['end']
            need(x['accepted_ns'] == min(now for now, p in accepted[sid] if p >= end) and
                 x['acked_ns'] == min(now for now, p in confirmed[sid] if p >= end), 'actual receive/ACK callback')
            need(x['published_ns'] == max(x['ready_ns'], rows[ticket-1]['published_ns'] if ticket else 0,
                 rows[ticket-s['queue_depth']]['acked_ns'] if ticket >= s['queue_depth'] else 0), 'actual queue credit')
        for data in packets.values():
            if data['stream'] == sid:
                owners = [x for x in rows if x['begin'] <= data['sequence'] < data['sequence']+data['payload_bytes'] <= x['end']]
                need(len(owners) == 1 and data['time_ns'] >= owners[0]['published_ns'], 'packet belongs to published frontend SEND')
        need(prefixes[sid] == end, 'final accepted prefix')
    def time(ref):
        wid, event = ref
        need(wid in ops, 'unknown reference')
        if wid in local:
            need(event == 'completed', 'local event'); return local[wid]['completed_ns']
        need(event in ('accepted', 'acked'), 'write event'); return remote[wid][event+'_ns']
    def done(rows):
        return max((time(x) for x in refs(rows)), default=0)
    for wid, op in ops.items():
        row = local[wid] if wid in local else remote[wid]
        need(row['ready_ns'] == done(op['after']), 'work readiness')
        if wid in local:
            need(row['rank'] == op['rank'] and row['duration_ns'] == op['duration_ns'] and
                 row['start_ns'] >= row['ready_ns'] and row['completed_ns'] == row['start_ns']+op['duration_ns'], 'local duration')
    by_rank = defaultdict(list)
    for row in local.values(): by_rank[row['rank']].append(row)
    for rows in by_rank.values():
        end = 0
        for row in sorted(rows, key=lambda x: (x['start_ns'], x['duration_ns'] > 0, x['id'])):
            need(row['start_ns'] == max(end, row['ready_ns']), 'serial local slot without overlap or unexplained idle')
            end = row['completed_ns']
    nodes = {(x['graph_id'], x['rank'], x['node_id']): x for x in mapping['nodes']}
    need(len(nodes) == len(mapping['nodes']) == sum(len(ts) for w in waves for ts in w['rank_nodes'].values()), 'node coverage')
    retired = {(x['group_index'], x['decode_step']): x for x in mapping['waves']}
    need(len(retired) == len(waves), 'wave retirement coverage')
    sends, used, group_finish, recv_gates = {}, set(), {}, []
    for w in waves:
        for rank, ts in w['rank_nodes'].items():
            for n in ts:
                if n['kind'] == 'SEND':
                    row = nodes[w['graph_id'], int(rank), n['id']]
                    rr = refs(row['completion']); need(len(rr) == 1, 'SEND completion')
                    wid, event = next(iter(rr)); need(event == 'acked' and wid in remote, 'SEND actual ACK')
                    need(n['message']['id'] not in sends, 'message uniqueness'); sends[n['message']['id']] = wid
    for w in sorted(waves, key=lambda x: (x['decode_step'], x['group_index'])):
        gi, step = w['group_index'], w['decode_step']; prior = retired.get((gi, step-1))
        latest = 0
        for rank, ts in w['rank_nodes'].items():
            rank = int(rank)
            for n in ts:
                row = nodes[w['graph_id'], rank, n['id']]
                need(row['kind'] == n['kind'] and row['group_index'] == gi and row['decode_step'] == step, 'node mapping identity')
                before = set().union(*(refs(nodes[w['graph_id'], rank, d]['completion']) for d in n['deps'])) if n['deps'] else (refs(prior['completion']) if prior else set())
                if n['kind'] == 'RECV':
                    wid = sends[n['message']['id']]; expected = before|{(wid, 'accepted')}
                    need(refs(row['completion']) == expected, 'RECV conjunction without implicit ACK')
                    recv_gates.append(dict(graph=w['graph_id'], rank=rank, node=n['id'], receive_ns=remote[wid]['accepted_ns'], completed_ns=done(row['completion'])))
                else:
                    rr = refs(row['completion']); need(len(rr) == 1, 'node direct completion')
                    wid, event = next(iter(rr)); need(wid not in used, 'node/work bijection'); used.add(wid)
                    op = ops[wid]; need(refs(op['after']) == before, 'original local prerequisites and next-wave gate')
                    if n['kind'] == 'COMP':
                        need(wid in local and op['rank'] == rank and op['duration_ns'] == n['duration_ns'], 'COMP identity')
                    else:
                        msg = n['message']; s = streams[op['stream']]
                        need(op['bytes'] == msg['bytes'] and (s['src'], s['dst'], s['plane']) == (msg['src'], msg['dst'], msg['plane']), 'SEND intent')
                latest = max(latest, done(row['completion']))
        retirement = retired[gi, step]
        need(done(retirement['completion']) == latest, 'whole-group completion includes dangling SEND ACK')
        group_finish[w['graph_id']] = latest
        for wid in retirement['join_work']:
            need(wid not in used and wid in local and ops[wid]['duration_ns'] == 0 and ops[wid]['rank'] == w['rank_ids'][0], 'retirement join')
            used.add(wid)
    need(used == set(ops) and len(remote) == len(sends), 'no invented/omitted operation')
    need(all(p['queue_metrics']['final_bytes'] == 0 for p in r['ports'] if p['node'] >= r['gpu_count']), 'queue drain')
    report = dict(passed=True, frontend_nodes=len(nodes), writes=len(remote), local_work=len(local), source_packets=len(packets),
                source_payload_bytes=sum(x['payload_bytes'] for x in packets.values()), physical_link_transmissions=physical_tx,
                physical_drop_events=dropped, group_finish_ns=group_finish, finish_ns=r['finish_ns'],
                receives_with_local_gate=[x for x in recv_gates if x['completed_ns'] > x['receive_ns']],
                modern_gin=False, astra_scheduling_equivalence=False, hardware_calibrated=False)
    if not full:
        report['intermediate_capture'] = 'endpoint capture with shortest-hop byte conservation; no exact ECMP paths'
    return report


def main():
    p = argparse.ArgumentParser()
    for name in ('config', 'work', 'mapping', 'result', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); load = lambda p: json.loads(p.read_text())
    options, mapping = load(a.config), load(a.mapping); waves = []
    for name, saved in zip(options['waves'], mapping['inputs']):
        raw = (a.config.parent/name).resolve().read_bytes()
        need(saved['path'] == name and saved['bytes'] == len(raw) and saved['sha256'] == hashlib.sha256(raw).hexdigest(), 'source provenance')
        waves.append(json.loads(raw))
    report = audit(waves, load(a.work), mapping, load(a.result))
    with a.output.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True); stream.write('\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
