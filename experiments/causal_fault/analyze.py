"""Read observed graphs/public packets only; no raw routes, truth or fixtures."""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')


def case_summary(observed, planned, result):
    w = load(planned/'work.json'); mapping = load(planned/'mapping.json')
    t = result['persistent_transport']; c = result['packet_capture']
    assert result['passed'] and result['drained'] and c['complete'] and not c['omitted_events']
    work = {v['id']: v for v in w['work']}
    times = {v['id']: v for v in [*t['work'], *result['local_work']]}
    streams = {v['id']: v for v in t['streams']}
    def time(ref): return times[ref['work']][{'completed': 'completed_ns', 'accepted': 'accepted_ns', 'acked': 'acked_ns'}[ref['event']]]
    def finish(refs): return max((time(d) for d in refs), default=0)
    nodes = {(v['graph_id'], v['rank'], v['node_id']): v for v in mapping['nodes']}
    labels = {}; phase_records = []; message_meta = {}
    for filename in load(observed/'gbn-plan.json')['waves']:
        wave = load(observed/filename)
        for m in wave['messages']: message_meta[m['id']] = m
        for rank, rows in wave['rank_nodes'].items():
            for node in rows:
                refs = nodes[wave['graph_id'], int(rank), node['id']]['completion']
                if node['kind'] != 'RECV':
                    assert len(refs) == 1
                    labels[refs[0]['work']] = dict(graph_id=wave['graph_id'], rank=int(rank), name=node['name'],
                        kind=node['kind'], node_id=node['id'], message=node.get('message', {}).get('id'))
                if node['kind'] == 'COMP':
                    phase_records.append(dict(graph_id=wave['graph_id'], rank=int(rank), name=node['name'],
                        ready_ns=times[refs[0]['work']]['ready_ns'], start_ns=times[refs[0]['work']]['start_ns'], completed_ns=finish(refs)))
    # Follow ALL tied latest declared prerequisites. Positive-duration local
    # resource blockers are retained; zero-time joins cannot add elapsed delay.
    # Cross-flow network causality is deliberately not inferred from this DAG.
    def frontier(refs):
        stack = list(refs); seen = set(); writes = {}; constraints = []
        while stack:
            ref = stack.pop(); wid, event = ref['work'], ref['event']
            if (wid, event) in seen: continue
            seen.add((wid, event)); item = work[wid]; row = times[wid]
            parents = item['after']; ready = finish(parents)
            assert ready == row['ready_ns']
            if item['kind'] == 'write':
                writes[wid, event] = dict(work=wid, event=event, stream=item['stream'],
                    **labels[wid], ready_ns=ready, published_ns=row['published_ns'],
                    accepted_ns=row['accepted_ns'], acked_ns=row['acked_ns'], elapsed_to_event_ns=time(ref)-ready)
                active = [d for d in parents if time(d) == ready]
            else:
                assert row['completed_ns'] == row['start_ns']+item['duration_ns']
                active = [d for d in parents if time(d) == ready] if row['start_ns'] == ready else []
                blockers = [v for v in result['local_work'] if v['rank'] == row['rank'] and v['duration_ns'] > 0
                    and v['id'] != wid and v['completed_ns'] == row['start_ns']]
                active += [dict(work=v['id'], event='completed') for v in blockers]
                assert row['start_ns'] == ready or blockers, 'unexplained local resource wait'
            constraints.append(dict(work=wid, event=event, latest_predecessors=active))
            stack.extend(active)
        return dict(writes=sorted(writes.values(), key=lambda v: (v['work'], v['event'])),
            constraints=sorted(constraints, key=lambda v: (v['work'], v['event'])),
            scope='declared application and serial local dependencies only; excludes network resource owner dependencies; not avoidable gain')
    windows = []
    for wave in mapping['waves']:
        end = finish(wave['completion'])
        windows.append(dict(graph_id=wave['graph_id'], completion_ns=end,
            last_merge_ns=max(v['completed_ns'] for v in phase_records if v['graph_id'] == wave['graph_id'] and v['name'].endswith('.merge')),
            frontier=frontier(wave['completion'])))
    events = [dict(zip(c['columns'], v)) for v in c['events']]
    ports = {(v['node'], v['port']): v for v in result['ports']}
    queues = defaultdict(deque); path_queue = defaultdict(list); drops = Counter(); payload = Counter(); source_packets = Counter()
    sources = defaultdict(list); source_model = Counter(); physical_tx = physical_rx = physical_drop = 0
    for e in events:
        sid = e['message_id']; proto = e['ip_protocol']; kind = e['event']; node = e['node']
        if kind == 0: physical_tx += 1
        if kind == 1: physical_rx += 1
        if kind == 2: physical_drop += 1
        if kind in (2, 5) and sid in streams: drops[sid, proto] += 1
        if kind == 0 and sid in streams and node == streams[sid]['src'] and proto == 17:
            payload[sid] += e['data_payload_bytes']; source_packets[sid] += 1
            sources[sid, e['packet_uid'], e['sequence']].append(e)
        if kind == 0 and node < result['gpu_count']: source_model[proto] += e['model_bytes']
        if proto != 17: continue
        key = node, e['port'], e['packet_uid'], e['sequence']
        if kind == 3: queues[key].append(e)
        if kind == 4:
            enq = queues[key].popleft()
            path_queue[sid, e['packet_uid'], e['sequence']].append(dict(node=node, port=e['port'],
                peer=ports[node, e['port']]['peer'], enqueue_ns=enq['time_ns'], dequeue_ns=e['time_ns'],
                wait_ns=e['time_ns']-enq['time_ns']))
    assert not any(queues.values())
    all_writes = []
    for row in t['work']:
        sid = row['stream']; label = labels[row['id']]
        witnesses = [e for e in events if e['event'] == 1 and e['ip_protocol'] == 17 and e['message_id'] == sid
            and e['node'] == streams[sid]['dst'] and e['time_ns'] == row['accepted_ns']]
        assert len(witnesses) == 1
        rx = witnesses[0]; txs = sources[sid, rx['packet_uid'], rx['sequence']]
        assert len(txs) == 1, 'ambiguous lineage'
        tx = txs[0]; hops = path_queue[sid, rx['packet_uid'], rx['sequence']]
        hops = [q for q in hops if tx['time_ns'] <= q['enqueue_ns'] <= q['dequeue_ns'] <= rx['time_ns']]
        all_writes.append(dict(work=row['id'], stream=sid, **label, ready_ns=row['ready_ns'],
            published_ns=row['published_ns'], accepted_ns=row['accepted_ns'], acked_ns=row['acked_ns'],
            accepted_after_ready_ns=row['accepted_ns']-row['ready_ns'], ack_after_ready_ns=row['acked_ns']-row['ready_ns'],
            witness=dict(packet_uid=rx['packet_uid'], sequence=rx['sequence'], source_tx_ns=tx['time_ns'],
                destination_rx_ns=rx['time_ns'], queue_wait_ns=sum(q['wait_ns'] for q in hops), queues=hops)))
    sr = t.get('selective_repeat')
    return dict(group_completion_ns=windows[-1]['completion_ns'], physical_finish_ns=result['finish_ns'],
        windows=windows, phases=phase_records, writes=sorted(all_writes, key=lambda v: v['work']),
        last_write_ack_ns=max(v['acked_ns'] for v in t['work']), last_write_receive_ns=max(v['accepted_ns'] for v in t['work']),
        unique_payload_bytes=sum(v['submitted_bytes'] for v in streams.values()), tx_payload_bytes=sum(payload.values()),
        source_model_bytes_by_protocol=dict(sorted(source_model.items())), packet_events=len(events),
        physical_tx=physical_tx, physical_rx=physical_rx, physical_drop=physical_drop,
        timeouts=sum(v['timeout_retransmissions'] for v in t['recovery']['states']),
        peak_reorder_sum_bytes=sum(v['peak_reorder_bytes'] for v in sr['states']) if sr else 0,
        streams=[dict(stream=sid, src=v['src'], dst=v['dst'], unique_bytes=v['submitted_bytes'],
            tx_payload_bytes=payload[sid], source_packets=source_packets[sid],
            dropped_by_protocol={str(p): count for (s, p), count in drops.items() if s == sid}) for sid, v in sorted(streams.items())])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('observed', 'planned', 'network', 'output'): p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists()
    cases = {v['name']: case_summary(a.observed, a.planned/v['name'], load(a.network/v['name']/'result.json'))
             for v in load(a.observed/'index.json')['cases']}
    contrasts = []
    for mode in ('gbn', 'selective'):
        base, fault = cases['healthy-'+mode], cases['failed-'+mode]
        bwork = {v['work']: v for v in base['writes']}; streams = {v['stream']: v for v in fault['streams']}
        final = {(v['work'], v['event']) for v in fault['windows'][-1]['frontier']['writes']}
        contrasts.append(dict(mode=mode, group_delta_ns=fault['group_completion_ns']-base['group_completion_ns'],
            step_delta_ns=[f['completion_ns']-b['completion_ns'] for b, f in zip(base['windows'], fault['windows'])],
            extra_payload_bytes=fault['tx_payload_bytes']-base['tx_payload_bytes'],
            writes=[dict(work=v['work'], stream=v['stream'], name=v['name'], graph_id=v['graph_id'],
                ready_delta_ns=v['ready_ns']-bwork[v['work']]['ready_ns'],
                receive_delta_ns=v['accepted_ns']-bwork[v['work']]['accepted_ns'],
                receive_after_ready_delta_ns=v['accepted_after_ready_ns']-bwork[v['work']]['accepted_after_ready_ns'],
                ack_delta_ns=v['acked_ns']-bwork[v['work']]['acked_ns'],
                ack_after_ready_delta_ns=v['ack_after_ready_ns']-bwork[v['work']]['ack_after_ready_ns'],
                stream_direct_drop=bool(streams[v['stream']]['dropped_by_protocol']),
                final_declared_frontier_events=[e for wid, e in sorted(final) if wid == v['work']]) for v in fault['writes']]))
    save(a.output, dict(schema_version='moe-causal-fault-analysis/1', cases=cases, contrasts=contrasts,
        scope=load(a.observed/'index.json')['scope'],
        warning='realized dependency frontier excludes network cross-flow causality; no counterfactual/optimization/modern fidelity claim'))


if __name__ == '__main__': main()
